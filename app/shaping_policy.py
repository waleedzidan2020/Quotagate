from __future__ import annotations
import ipaddress, re
from pathlib import Path
from . import config, db, shaping, guestmode, network

_INSTALLED = False
_ORIG_LIMITS = None
_ORIG_STATUS = None
_ORIG_CONFIG_SAVE = None
_ORIG_CREATE_USER = None
_ORIG_UPDATE_USER = None
_ORIG_UPDATE_DEVICE = None
_ORIG_UPSERT_DEVICE = None
_ORIG_DEVICE_BY_IP = None
_ORIG_BLOCKED = None
_ORIG_SYNC_RULES = None

_MAC_RE = re.compile(r'^(?:[0-9a-f]{2}:){5}[0-9a-f]{2}$', re.I)
_ZERO_MAC = '00:00:00:00:00:00'
_BROADCAST_MAC = 'ff:ff:ff:ff:ff:ff'
_SPEED_KEYS = {'speed_down_kbit', 'speed_up_kbit'}


def _valid_mac(mac):
    mac = str(mac or '').strip().lower()
    return bool(_MAC_RE.fullmatch(mac)) and mac not in {_ZERO_MAC, _BROADCAST_MAC}


def _valid_ipv4(ip):
    try:
        return ipaddress.ip_address(str(ip or '')).version == 4
    except Exception:
        return False


def _neighbor_map():
    """Best-effort current IPv4 -> MAC ownership from the kernel ARP table."""
    out = {}
    try:
        lines = Path('/proc/net/arp').read_text(errors='ignore').splitlines()[1:]
        for line in lines:
            p = line.split()
            if len(p) < 4:
                continue
            ip, mac = p[0], p[3].lower()
            if _valid_ipv4(ip) and _valid_mac(mac):
                out[ip] = mac
    except Exception:
        pass
    return out


def _device_score(d, neighbors=None):
    ip = str(d.get('ip') or '')
    mac = str(d.get('mac') or '').lower()
    owner = 1 if neighbors and neighbors.get(ip) == mac else 0
    # Prefer the MAC the kernel currently associates with the IP. This matters
    # when stale DHCP leases keep refreshing two database rows with one address.
    return (
        owner,
        int(d.get('last_seen') or 0),
        int(bool(d.get('enabled'))),
        int(d.get('id') or 0),
    )


def _sanitize_devices(devices):
    """Return one safe policy owner per IPv4 address.

    Invalid/placeholder MAC rows never reach tc/nftables. If stale DB rows share
    one IP, the live kernel-neighbor MAC wins; otherwise newest valid row wins.
    The database is intentionally left untouched so usage/history are preserved.
    """
    by_ip = {}
    neighbors = _neighbor_map()
    for d in devices:
        ip = str(d.get('ip') or '')
        if not _valid_mac(d.get('mac')) or not _valid_ipv4(ip):
            continue
        prev = by_ip.get(ip)
        if prev is None or _device_score(d, neighbors) > _device_score(prev, neighbors):
            by_ip[ip] = d
    return sorted(by_ip.values(), key=lambda x: int(x.get('id') or 0))


def _limits(c, devices, users):
    return _ORIG_LIMITS(c, _sanitize_devices(devices), users)


def _limits_exist(c):
    down, up = shaping._limits(c, db.devices(), db.users())
    return bool(down or up)


def _persist_speed_feature(c, reason):
    if c.get('features', {}).get('speed_limits', True):
        return False
    c.setdefault('features', {})['speed_limits'] = True
    guestmode._ORIG_SAVE(c)
    try:
        db.event('Traffic shaping auto-enabled: ' + reason, 'info')
    except Exception:
        pass
    return True


def _enable_if_active_limits(reason):
    try:
        c = config.load()
        if not c.get('features', {}).get('speed_limits', True) and _limits_exist(c):
            _persist_speed_feature(c, reason)
    except Exception as e:
        try: db.event('Speed-limit auto-enable check failed: ' + str(e), 'error')
        except Exception: pass


def create_user(name, quota_gb=0, down=0, up=0, quota_mode='fixed'):
    uid = _ORIG_CREATE_USER(name, quota_gb, down, up, quota_mode)
    if int(down or 0) > 0 or int(up or 0) > 0:
        _enable_if_active_limits('a user was created with an active speed limit')
    return uid


def update_user(i, **kw):
    result = _ORIG_UPDATE_USER(i, **kw)
    if _SPEED_KEYS.intersection(kw):
        _enable_if_active_limits('a user speed limit was applied')
    return result


def update_device(i, **kw):
    result = _ORIG_UPDATE_DEVICE(i, **kw)
    if _SPEED_KEYS.intersection(kw):
        _enable_if_active_limits('a per-device speed limit was applied')
    return result


def upsert_device(mac, ip, *args, **kwargs):
    if not _valid_mac(mac):
        try: db.event(f'Ignored invalid/placeholder device MAC {mac!s} at {ip!s}', 'warning')
        except Exception: pass
        return 0, False
    return _ORIG_UPSERT_DEVICE(mac, ip, *args, **kwargs)


def device_by_ip(ip):
    """Resolve an IP to the single current valid device owner."""
    ip = str(ip or '')
    candidates = [d for d in db.devices() if str(d.get('ip') or '') == ip]
    safe = _sanitize_devices(candidates)
    return safe[0] if safe else None


def blocked(c, users, devices):
    """Never let a stale duplicate row block the live device sharing its IP."""
    return _ORIG_BLOCKED(c, users, _sanitize_devices(devices))


def sync_rules(c, devices, blocked_ips):
    """Create firewall counters/rules once per live IPv4 owner only."""
    return _ORIG_SYNC_RULES(c, _sanitize_devices(devices), blocked_ips)


def save(c):
    try:
        old = config.load()
    except Exception:
        old = {}
    old_g = old.get('guest', {})
    new_g = c.get('guest', {})
    guest_speed_changed = any(
        int(old_g.get(k, 0) or 0) != int(new_g.get(k, 0) or 0)
        for k in _SPEED_KEYS
    )
    guest_apply = int(old_g.get('apply_revision', 0) or 0) != int(new_g.get('apply_revision', 0) or 0)
    auto_enabled = False
    if (guest_speed_changed or guest_apply) and (
        int(new_g.get('speed_down_kbit', 0) or 0) > 0 or
        int(new_g.get('speed_up_kbit', 0) or 0) > 0
    ):
        if not c.get('features', {}).get('speed_limits', True):
            c.setdefault('features', {})['speed_limits'] = True
            auto_enabled = True
    result = _ORIG_CONFIG_SAVE(c)
    if auto_enabled:
        try: db.event('Traffic shaping auto-enabled: Guest speed settings were applied', 'info')
        except Exception: pass
    return result


def status(c, devices, users):
    data = _ORIG_STATUS(c, devices, users)
    eligible = {int(d.get('id') or 0) for d in _sanitize_devices(devices)}
    for item in data.get('guest_devices', []):
        did = int(item.get('device_id') or 0)
        if did not in eligible:
            item['applied'] = False
            item['reason'] = 'invalid MAC or duplicate/stale IP; excluded from kernel policy'
    return data


def install():
    global _INSTALLED, _ORIG_LIMITS, _ORIG_STATUS, _ORIG_CONFIG_SAVE
    global _ORIG_CREATE_USER, _ORIG_UPDATE_USER, _ORIG_UPDATE_DEVICE, _ORIG_UPSERT_DEVICE
    global _ORIG_DEVICE_BY_IP, _ORIG_BLOCKED, _ORIG_SYNC_RULES
    if _INSTALLED:
        return

    _ORIG_LIMITS = shaping._limits
    _ORIG_STATUS = shaping.status
    _ORIG_CONFIG_SAVE = config.save
    _ORIG_CREATE_USER = db.create_user
    _ORIG_UPDATE_USER = db.update_user
    _ORIG_UPDATE_DEVICE = db.update_device
    _ORIG_UPSERT_DEVICE = db.upsert_device
    _ORIG_DEVICE_BY_IP = db.device_by_ip
    _ORIG_BLOCKED = network.blocked
    _ORIG_SYNC_RULES = network.sync_rules

    shaping._limits = _limits
    shaping.status = status
    config.save = save
    db.create_user = create_user
    db.update_user = update_user
    db.update_device = update_device
    db.upsert_device = upsert_device
    db.device_by_ip = device_by_ip
    network.blocked = blocked
    network.sync_rules = sync_rules
    _INSTALLED = True
