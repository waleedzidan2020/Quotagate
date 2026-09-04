from __future__ import annotations
import json, os, time
from pathlib import Path
from . import config, db, diagnostics, shaping

STATE = Path('/var/lib/quotagate/guest-mode-state.json')
_ORIG_SAVE = config.save
_ORIG_UPSERT = db.upsert_device
_ORIG_SNAPSHOT = diagnostics.snapshot
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


def _station_macs(c):
    iface = c.get('network', {}).get('lan_interface', 'wlan0')
    j = diagnostics.wifi_stations(iface)
    if not j.get('ok'): return []
    return sorted({str(x.get('mac', '')).lower() for x in j.get('stations', []) if x.get('mac')})


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


def save(c):
    try:
        old = config.load()
        old_enabled = bool(old.get('guest', {}).get('enabled'))
    except Exception:
        old_enabled = False
    new_enabled = bool(c.get('guest', {}).get('enabled'))
    if new_enabled and not old_enabled:
        _write_state({'enabled': True, 'activated_at': int(time.time()), 'baseline_macs': _station_macs(c)})
    elif not new_enabled and old_enabled:
        _write_state({'enabled': False, 'activated_at': 0, 'baseline_macs': []})
    return _ORIG_SAVE(c)


def _device_by_id(did):
    for d in db.devices():
        if int(d.get('id') or 0) == int(did): return d
    return None


def _active_guest_count(current_macs):
    current = set(current_macs)
    return sum(1 for d in db.devices() if d.get('is_guest') and str(d.get('mac', '')).lower() in current)


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
            # Never steal an explicitly managed/assigned device into Guest Mode.
            return did, is_new
        guest = c.get('guest', {})
        if _active_guest_count(connected) >= int(guest.get('max_devices', 10) or 10):
            db.update_device(did, enabled=0)
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
        db.update_device(did, user_id=uid, enabled=1, is_guest=1)
        db.event(f'Guest device admitted: {mac} at {ip} with {guest.get("speed_down_kbit",0)}kbit down / {guest.get("speed_up_kbit",0)}kbit up', 'info')
        db.alert('guest-joined', f'New Guest device {mac} joined and Guest speed limits were applied')
        try:
            shaping.shaping(c, db.devices(), db.users(), force=True)
        except Exception as e:
            db.event('Immediate Guest shaping failed: ' + str(e), 'error')
            db.alert('guest-shaping-failed', 'Guest joined but traffic shaping failed to apply immediately')
        # Prevent the legacy new-device block in app.main.scan from creating a second Guest user.
        return did, False
    except Exception as e:
        try: db.event('guest mode admission: ' + str(e), 'error')
        except Exception: pass
        return did, is_new


def status(c):
    state = _ensure_state(c)
    connected = _station_macs(c)
    baseline = {str(x).lower() for x in state.get('baseline_macs', [])}
    by_mac = {str(d.get('mac', '')).lower(): d for d in db.devices()}
    items = []
    for mac in connected:
        d = by_mac.get(mac) or {}
        if d.get('is_guest'):
            role = 'guest'
        elif mac in baseline:
            role = 'existing_at_activation'
        elif d.get('user_id'):
            role = 'managed'
        else:
            role = 'unassigned'
        items.append({
            'mac': mac,
            'device_id': d.get('id'),
            'name': d.get('name') or '',
            'ip': d.get('ip') or '',
            'role': role,
            'is_guest': bool(d.get('is_guest')),
        })
    sh = shaping.status(c, db.devices(), db.users())
    return {
        'enabled': bool(c.get('guest', {}).get('enabled')),
        'activated_at': int(state.get('activated_at') or 0),
        'baseline_macs': sorted(baseline),
        'connected_count': len(connected),
        'connected': items,
        'speed_down_kbit': int(c.get('guest', {}).get('speed_down_kbit', 0) or 0),
        'speed_up_kbit': int(c.get('guest', {}).get('speed_up_kbit', 0) or 0),
        'quota_gb': float(c.get('guest', {}).get('quota_gb', 0) or 0),
        'max_devices': int(c.get('guest', {}).get('max_devices', 10) or 10),
        'shaping_healthy': bool(sh.get('healthy')),
        'shaping_error': sh.get('last_error', ''),
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
    diagnostics.snapshot = snapshot
    _INSTALLED = True
