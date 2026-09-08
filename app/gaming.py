from __future__ import annotations
import json, os, threading, time
from pathlib import Path
from . import db

STATE = Path('/var/lib/quotagate/gaming-mode-state.json')
LOCK = threading.RLock()
_ALLOWED_PRIORITIES = {'high', 'normal', 'low'}
_ALLOWED_DURATIONS = {0, 15, 30, 60, 120}


def _safe_priority(value, default='normal'):
    value = str(value or default).lower().strip()
    return value if value in _ALLOWED_PRIORITIES else default


def _read():
    with LOCK:
        try:
            data = json.loads(STATE.read_text())
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}


def _write(data):
    with LOCK:
        STATE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(STATE.parent, 0o700)
        except Exception:
            pass
        tmp = STATE.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')))
        os.chmod(tmp, 0o600)
        tmp.replace(STATE)


def _clear():
    with LOCK:
        try:
            STATE.unlink()
        except FileNotFoundError:
            pass


def _expired(state, now=None):
    if not state or not state.get('active'):
        return False
    exp = int(state.get('expires_at') or 0)
    return bool(exp and exp <= int(now or time.time()))


def tick(c=None):
    """Expire runtime policy without a busy loop. The main worker calls this."""
    state = _read()
    if _expired(state):
        _clear()
        db.event('Smart Gaming Mode expired; original traffic policy is active again', 'info')
        return True
    return False


def current():
    state = _read()
    if _expired(state):
        tick()
        return {}
    return state


def signature():
    s = current()
    if not s:
        return ()
    p = s.get('policy', {})
    return (
        int(s.get('device_id') or 0), int(s.get('expires_at') or 0),
        str(p.get('other_priority') or 'low'), int(bool(p.get('limit_others'))),
        int(p.get('other_down_kbit') or 0), int(p.get('other_up_kbit') or 0),
        int(p.get('guest_down_kbit') or 0),
    )


def snapshot_devices(devices, guest_cfg):
    return {
        'devices': [
            {
                'id': int(d['id']),
                'priority': _safe_priority(d.get('priority'), 'low' if d.get('is_guest') else 'normal'),
                'speed_down_kbit': int(d.get('speed_down_kbit') or 0),
                'speed_up_kbit': int(d.get('speed_up_kbit') or 0),
                'is_guest': bool(d.get('is_guest')),
            }
            for d in devices
        ],
        'guest': {
            'speed_down_kbit': int((guest_cfg or {}).get('speed_down_kbit') or 0),
            'speed_up_kbit': int((guest_cfg or {}).get('speed_up_kbit') or 0),
        },
    }


def start(c, device_id, duration_minutes=30, other_priority='low', limit_others=False,
          other_down_kbit=0, other_up_kbit=0, guest_down_kbit=0):
    device_id = int(device_id)
    duration_minutes = int(duration_minutes)
    if duration_minutes not in _ALLOWED_DURATIONS:
        raise ValueError('duration must be 0, 15, 30, 60 or 120 minutes')
    other_priority = _safe_priority(other_priority, 'low')
    devices = db.devices()
    selected = next((d for d in devices if int(d['id']) == device_id), None)
    if not selected:
        raise ValueError('device not found')
    if not selected.get('ip'):
        raise ValueError('gaming device has no current IP address')
    for name, value in {
        'other_down_kbit': other_down_kbit,
        'other_up_kbit': other_up_kbit,
        'guest_down_kbit': guest_down_kbit,
    }.items():
        if int(value or 0) < 0:
            raise ValueError(name + ' cannot be negative')
    now = int(time.time())
    state = {
        'active': True,
        'started_at': now,
        'expires_at': now + duration_minutes * 60 if duration_minutes else 0,
        'device_id': device_id,
        'device_name': str(selected.get('name') or selected.get('ip') or device_id)[:120],
        'snapshot': snapshot_devices(devices, c.get('guest', {})),
        'policy': {
            'other_priority': other_priority,
            'limit_others': bool(limit_others),
            'other_down_kbit': int(other_down_kbit or 0),
            'other_up_kbit': int(other_up_kbit or 0),
            'guest_down_kbit': int(guest_down_kbit or 0),
        },
    }
    _write(state)
    db.event(f'Smart Gaming Mode started for device {device_id}', 'info')
    return status(devices)


def stop(reason='manual'):
    state = current()
    if state:
        _clear()
        db.event('Smart Gaming Mode stopped; original traffic policy restored' +
                 (f' ({reason})' if reason else ''), 'info')
    return {'active': False}


def status(devices=None):
    s = current()
    if not s:
        return {'active': False}
    now = int(time.time())
    exp = int(s.get('expires_at') or 0)
    out = {
        'active': True,
        'started_at': int(s.get('started_at') or 0),
        'expires_at': exp,
        'remaining_seconds': max(0, exp - now) if exp else None,
        'device_id': int(s.get('device_id') or 0),
        'device_name': s.get('device_name') or '',
        'policy': dict(s.get('policy') or {}),
    }
    return out


def effective_policy(device, direction, base_rate, base_priority):
    """Return temporary (rate, priority) overlay without mutating persistent DB policy."""
    state = current()
    priority = _safe_priority(base_priority, 'low' if device.get('is_guest') else 'normal')
    rate = max(0, int(base_rate or 0))
    if not state:
        return rate, priority
    if int(device.get('id') or 0) == int(state.get('device_id') or 0):
        return rate, 'high'
    policy = state.get('policy') or {}
    priority = _safe_priority(policy.get('other_priority'), 'low')
    if not policy.get('limit_others'):
        return rate, priority
    cap = int(policy.get('other_down_kbit' if direction == 'down' else 'other_up_kbit') or 0)
    if direction == 'down' and device.get('is_guest'):
        guest_cap = int(policy.get('guest_down_kbit') or 0)
        if guest_cap > 0:
            cap = guest_cap if cap <= 0 else min(cap, guest_cap)
    if cap > 0:
        rate = min(rate, cap) if rate > 0 else cap
    return rate, priority
