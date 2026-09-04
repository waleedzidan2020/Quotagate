from __future__ import annotations
import json, os, time
from pathlib import Path
from . import config, db, diagnostics, shaping

STATE = Path('/var/lib/quotagate/guest-mode-state.json')
_ORIG_SAVE = config.save
_ORIG_UPSERT = db.upsert_device
_ORIG_UPDATE_DEVICE = db.update_device
_ORIG_SNAPSHOT = diagnostics.snapshot
_ORIG_EFFECTIVE_RATE = shaping._effective_rate
_INSTALLED = False


def _write_state(data):
    STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = STATE.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
        f.flush()
        try: os.fsync(f.fileno())
        except OSError: pass
    os.chmod(tmp, 0o600)
    os.replace(tmp, STATE)


def _read_state():
    try:
        x = json.loads(STATE.read_text())
        if isinstance(x, dict): return x
    except Exception:
        pass
    return {'enabled': False, 'activated_at': 0, 'baseline_macs': []}


def _station_rows(c):
    iface = c.get('network', {}).get('lan_interface', 'wlan0')
    j = diagnostics.wifi_stations(iface)
    if not j.get('ok'): return []
    rows = []
    seen = set()
    for x in j.get('stations', []):
        mac = str(x.get('mac', '')).lower()
        if not mac or mac in seen: continue
        seen.add(mac)
        rows.append(dict(x, mac=mac))
    return rows


def _station_macs(c):
    return sorted(x['mac'] for x in _station_rows(c))


def _ensure_state(c):
    state = _read_state()
    enabled = bool(c.get('guest', {}).get('enabled'))
    if enabled and not state.get('enabled'):
        state = {'enabled': True, 'activated_at': int(time.time()), 'baseline_macs': _station_macs(c)}
        _write_state(state)
        db.event(f'Guest Mode activated; protected {len(state["baseline_macs"])} currently connected device(s) from Guest conversion', 'info')
    elif not enabled and state.get('enabled'):
        state = {'enabled': False, 'activated_at': 0, 'baseline_macs': []}
        _write_state(state)
        db.event('Guest Mode deactivated', 'info')
    return state


def _guest_values(c):
    g = c.get('guest', {})
    return {
        'quota_gb': float(g.get('quota_gb', 0.5) or 0),
        'speed_down_kbit': max(0, int(g.get('speed_down_kbit', 0) or 0)),
        'speed_up_kbit': max(0, int(g.get('speed_up_kbit', 0) or 0)),
        'max_devices': max(1, int(g.get('max_devices', 10) or 10)),
        'apply_revision': int(g.get('apply_revision', 0) or 0),
    }


def _sync_all_guest_defaults(c, reset_device_overrides=False):
    want = _guest_values(c)
    users = {u['id']: u for u in db.users()}
    changed_users = 0
    reset_devices = 0
    for d in db.devices():
        if not d.get('is_guest'): continue
        uid = d.get('user_id')
        u = users.get(uid) if uid else None
        if u:
            updates = {}
            if float(u.get('quota_gb') or 0) != want['quota_gb']: updates['quota_gb'] = want['quota_gb']
            if int(u.get('speed_down_kbit') or 0) != want['speed_down_kbit']: updates['speed_down_kbit'] = want['speed_down_kbit']
            if int(u.get('speed_up_kbit') or 0) != want['speed_up_kbit']: updates['speed_up_kbit'] = want['speed_up_kbit']
            if updates:
                db.update_user(uid, **updates)
                changed_users += 1
        if reset_device_overrides and (int(d.get('speed_down_kbit') or 0) != 0 or int(d.get('speed_up_kbit') or 0) != 0):
            _ORIG_UPDATE_DEVICE(d['id'], speed_down_kbit=0, speed_up_kbit=0)
            reset_devices += 1
    if changed_users or reset_devices:
        db.event(f'Guest defaults synchronized: {changed_users} user profile(s), {reset_devices} device override(s) reset', 'info')


def save(c):
    try:
        old = config.load()
    except Exception:
        old = {}
    old_enabled = bool(old.get('guest', {}).get('enabled'))
    new_enabled = bool(c.get('guest', {}).get('enabled'))
    old_rev = int(old.get('guest', {}).get('apply_revision', 0) or 0)
    new_rev = int(c.get('guest', {}).get('apply_revision', 0) or 0)
    if new_enabled and not old_enabled:
        _write_state({'enabled': True, 'activated_at': int(time.time()), 'baseline_macs': _station_macs(c)})
    elif not new_enabled and old_enabled:
        _write_state({'enabled': False, 'activated_at': 0, 'baseline_macs': []})
    # A changed revision is the explicit "Apply to ALL Guests" signal.
    # Reset device overrides before the normal /api/settings apply() runs so a
    # single synchronous tc/nftables rebuild enforces the new defaults.
    if new_rev != old_rev:
        _sync_all_guest_defaults(c, reset_device_overrides=True)
    return _ORIG_SAVE(c)


def _device_by_id(did):
    for d in db.devices():
        if int(d.get('id') or 0) == int(did): return d
    return None


def _active_guest_count(current_macs):
    current = set(current_macs)
    return sum(1 for d in db.devices() if d.get('is_guest') and str(d.get('mac', '')).lower() in current)


def _cleanup_orphan_guest_user(uid):
    if not uid: return
    if any(int(d.get('user_id') or 0) == int(uid) for d in db.devices()): return
    u = db.user_by_id(uid)
    if u and str(u.get('name', '')).startswith('Guest-'):
        db.delete_user(uid)
        db.event(f'Removed orphan Guest profile {uid}', 'info')


def update_device(i, **kw):
    old = _device_by_id(i) or {}
    old_guest = bool(old.get('is_guest'))
    old_uid = old.get('user_id')
    result = _ORIG_UPDATE_DEVICE(i, **kw)
    if old_guest and 'is_guest' in kw and not bool(kw.get('is_guest')):
        _cleanup_orphan_guest_user(old_uid)
    return result


def _effective_rate(device, user, key, c):
    # Backward compatible storage semantics:
    #   positive value = explicit per-device limit
    #   0              = inherit Guest/User default
    #   -1             = explicit unlimited override
    # The dashboard maps user-entered 0 to -1, so "0 = unlimited" remains
    # true at the UI/API policy layer while reset-to-default stores 0.
    raw = int(device.get(key) or 0)
    if raw < 0: return 0
    if raw > 0: return raw
    return _ORIG_EFFECTIVE_RATE(device, user, key, c)


def upsert_device(mac, ip, *args, **kwargs):
    did, is_new = _ORIG_UPSERT(mac, ip, *args, **kwargs)
    try:
        c = config.load()
        state = _ensure_state(c)
        if not state.get('enabled'):
            return did, is_new
        mac = str(mac).lower()
        connected = _station_macs(c)
        if mac not in connected:
            return did, is_new
        if mac in {str(x).lower() for x in state.get('baseline_macs', [])}:
            return did, is_new
        rules = {str(r.get('mac', '')).lower(): r.get('action') for r in db.mac_rules()}
        if rules.get(mac) in ('blacklist', 'whitelist'):
            return did, is_new
        d = _device_by_id(did) or {}
        if d.get('is_guest'):
            return did, False if is_new else is_new
        if d.get('user_id'):
            return did, is_new
        guest = c.get('guest', {})
        if _active_guest_count(connected) >= int(guest.get('max_devices', 10) or 10):
            _ORIG_UPDATE_DEVICE(did, enabled=0)
            db.event(f'Guest admission denied for {mac}: max guest devices reached', 'warning')
            db.alert('guest-limit', f'Guest device {mac} was denied because Guest max_devices was reached')
            return did, False
        uid = db.create_user(
            'Guest-' + mac[-5:].replace(':', ''),
            float(guest.get('quota_gb', 0.5) or 0),
            int(guest.get('speed_down_kbit', 1024) or 0),
            int(guest.get('speed_up_kbit', 256) or 0),
            'fixed'
        )
        _ORIG_UPDATE_DEVICE(did, user_id=uid, enabled=1, is_guest=1, speed_down_kbit=0, speed_up_kbit=0)
        db.event(f'Guest device admitted: {mac} at {ip} with {guest.get("speed_down_kbit",0)}kbit down / {guest.get("speed_up_kbit",0)}kbit up', 'info')
        db.alert('guest-joined', f'New Guest device {mac} joined and Guest speed limits were applied')
        try:
            shaping.shaping(c, db.devices(), db.users(), force=True)
        except Exception as e:
            db.event('Immediate Guest shaping failed: ' + str(e), 'error')
            db.alert('guest-shaping-failed', 'Guest joined but traffic shaping failed to apply immediately')
        # Prevent legacy app.main.scan from creating a second Guest profile.
        return did, False
    except Exception as e:
        try: db.event('guest mode admission: ' + str(e), 'error')
        except Exception: pass
        return did, is_new


def _role(d, mac, baseline):
    if d.get('is_guest'): return 'guest'
    if mac in baseline: return 'existing_at_activation'
    if d.get('user_id'): return 'managed'
    return 'unassigned'


def status(c):
    state = _ensure_state(c)
    stations = _station_rows(c)
    baseline = {str(x).lower() for x in state.get('baseline_macs', [])}
    devices = db.devices()
    users = db.users()
    by_mac = {str(d.get('mac', '')).lower(): d for d in devices}
    um = {u['id']: u for u in users}
    sh = shaping.status(c, devices, users)
    sh_guest = {int(x['device_id']): x for x in sh.get('guest_devices', [])}
    items = []
    active_guests = []
    for st in stations:
        mac = st['mac']
        d = by_mac.get(mac) or {}
        u = um.get(d.get('user_id')) or {}
        role = _role(d, mac, baseline)
        down = _effective_rate(d, u, 'speed_down_kbit', c) if d else 0
        up = _effective_rate(d, u, 'speed_up_kbit', c) if d else 0
        item = {
            'mac': mac,
            'device_id': d.get('id'),
            'name': d.get('name') or '',
            'ip': d.get('ip') or '',
            'role': role,
            'is_guest': bool(d.get('is_guest')),
            'user_id': d.get('user_id'),
            'user_name': d.get('user_name') or '',
            'signal': st.get('signal'),
            'signal_avg': st.get('signal_avg'),
            'tx_bitrate_mbps': st.get('tx_bitrate_mbps'),
            'rx_bitrate_mbps': st.get('rx_bitrate_mbps'),
            'stored_down_kbit': int(d.get('speed_down_kbit') or 0) if d else 0,
            'stored_up_kbit': int(d.get('speed_up_kbit') or 0) if d else 0,
            'effective_down_kbit': down,
            'effective_up_kbit': up,
        }
        if d.get('is_guest'):
            ks = sh_guest.get(int(d.get('id') or 0), {})
            item['kernel_applied'] = bool(ks.get('applied')) if (down > 0 or up > 0) else bool(sh.get('healthy'))
            active_guests.append(item)
        items.append(item)
    return {
        'enabled': bool(c.get('guest', {}).get('enabled')),
        'activated_at': int(state.get('activated_at') or 0),
        'baseline_macs': sorted(baseline),
        'connected_count': len(stations),
        'connected': items,
        'active_guests': active_guests,
        'active_guest_count': len(active_guests),
        'speed_down_kbit': int(c.get('guest', {}).get('speed_down_kbit', 0) or 0),
        'speed_up_kbit': int(c.get('guest', {}).get('speed_up_kbit', 0) or 0),
        'quota_gb': float(c.get('guest', {}).get('quota_gb', 0) or 0),
        'max_devices': int(c.get('guest', {}).get('max_devices', 10) or 10),
        'apply_revision': int(c.get('guest', {}).get('apply_revision', 0) or 0),
        'shaping_healthy': bool(sh.get('healthy')),
        'shaping_error': sh.get('last_error', ''),
        'shaping': sh,
    }


def snapshot(c, include_ping=False):
    data = _ORIG_SNAPSHOT(c, include_ping)
    try: data['guest_mode'] = status(c)
    except Exception as e: data['guest_mode'] = {'enabled': bool(c.get('guest', {}).get('enabled')), 'error': str(e)[:300]}
    return data


def install():
    global _INSTALLED
    if _INSTALLED: return
    config.save = save
    db.upsert_device = upsert_device
    db.update_device = update_device
    shaping._effective_rate = _effective_rate
    diagnostics.snapshot = snapshot
    _INSTALLED = True
