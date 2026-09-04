from __future__ import annotations
import ipaddress, re, subprocess, time
from pathlib import Path
from . import db

_SHAPE_SIG = None
_LAST_VERIFY = 0.0
_LAST_ERROR = ''
_LAST_ERROR_SIG = None
_VERIFY_INTERVAL = 60.0
_MARK_BASE = 0x510000


def _run(args, check=False, timeout=5, input_text=None):
    try:
        p = subprocess.run(args, text=True, input=input_text, capture_output=True, timeout=timeout)
    except Exception as e:
        if check:
            raise RuntimeError(str(e))
        class R:
            returncode = 1
            stdout = ''
            stderr = str(e)
        return R()
    if check and p.returncode:
        raise RuntimeError((p.stderr or p.stdout or 'command failed').strip())
    return p


def _iface(name):
    name = str(name or '')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,32}', name):
        raise ValueError(f'invalid interface name: {name!r}')
    return name


def _device_mark(device_id):
    return _MARK_BASE + (int(device_id) & 0xFFFF)


def _effective_rate(device, user, key, c):
    device_rate = int(device.get(key) or 0)
    if device_rate > 0:
        return device_rate
    if device.get('is_guest'):
        return max(0, int(c.get('guest', {}).get(key, 0) or 0))
    return max(0, int((user or {}).get(key) or 0))


def _limits(c, devices, users):
    um = {u['id']: u for u in users}
    down, up = [], []
    for d in sorted(devices, key=lambda x: int(x.get('id') or 0)):
        ip = str(d.get('ip') or '')
        try:
            ipaddress.ip_address(ip)
        except Exception:
            continue
        u = um.get(d.get('user_id')) or {}
        dr = _effective_rate(d, u, 'speed_down_kbit', c)
        ur = _effective_rate(d, u, 'speed_up_kbit', c)
        mark = _device_mark(d['id'])
        item = {'id': int(d['id']), 'ip': ip, 'mark': mark, 'name': d.get('name') or ip, 'is_guest': bool(d.get('is_guest'))}
        if dr > 0:
            down.append({**item, 'rate': dr})
        if ur > 0:
            up.append({**item, 'rate': ur})
    return down, up


def _line_rate(c, direction):
    n = c['network']
    raw = n.get('line_down_mbit' if direction == 'down' else 'line_up_mbit', 0)
    total = int(float(raw) * 1000)
    if total <= 0:
        raise ValueError(f'{direction} line speed must be greater than 0 Mbps when shaping is enabled')
    return max(64, total)


def _sync_guest_profiles(c, devices, users):
    guest = c.get('guest', {})
    um = {u['id']: u for u in users}
    changed = False
    for d in devices:
        if not d.get('is_guest') or not d.get('user_id'):
            continue
        u = um.get(d.get('user_id'))
        if not u:
            continue
        want = {
            'quota_gb': float(guest.get('quota_gb', 0.5) or 0),
            'speed_down_kbit': int(guest.get('speed_down_kbit', 0) or 0),
            'speed_up_kbit': int(guest.get('speed_up_kbit', 0) or 0),
        }
        if any(float(u.get(k) or 0) != float(v) for k, v in want.items()):
            db.update_user(u['id'], **want)
            changed = True
    if changed:
        db.event('Guest profiles synchronized with current Guest Mode quota/speed settings', 'info')
    return changed


def _delete_mark_table():
    _run(['nft', 'delete', 'table', 'inet', 'quotagate_shape'])


def _wan_interface(c):
    n = c['network']
    if not n.get('vpn_share'):
        return n.get('wan_interface', 'eth0')
    wanted = str(n.get('vpn_interface', 'tun0'))
    if Path('/sys/class/net').joinpath(wanted).exists():
        return wanted
    if n.get('vpn_auto_detect', True):
        p = _run(['ip', '-o', 'link', 'show'])
        for line in p.stdout.splitlines():
            m = re.match(r'\d+:\s+([^:@]+)', line)
            if m and re.match(r'^(tun|tap|wg)\w*$', m.group(1)):
                return m.group(1)
    return wanted


def _install_marks(c, down, up):
    _delete_mark_table()
    if not down and not up:
        return
    n = c['network']
    lan = _iface(n.get('lan_interface', 'wlan0'))
    wan = _iface(_wan_interface(c))
    rules = []
    seen = set()
    for x in up:
        key = ('up', x['id'])
        if key in seen:
            continue
        seen.add(key)
        rules.append(
            f'iifname "{lan}" oifname "{wan}" ip saddr {x["ip"]} '
            f'meta mark set {hex(x["mark"])}'
        )
    for x in down:
        key = ('down', x['id'])
        if key in seen:
            continue
        seen.add(key)
        rules.append(
            f'iifname "{wan}" oifname "{lan}" ip daddr {x["ip"]} '
            f'meta mark set {hex(x["mark"])}'
        )
    body = [
        'table inet quotagate_shape {',
        ' chain forward { type filter hook forward priority mangle; policy accept;',
    ]
    body.extend(f'  {r}' for r in rules)
    body.extend([' }', '}'])
    _run(['nft', '-f', '-'], check=True, input_text='\n'.join(body) + '\n')


def _delete_root(iface):
    _run(['tc', 'qdisc', 'del', 'dev', iface, 'root'])


def _shape_iface(iface, direction, items, c):
    iface = _iface(iface)
    if not Path('/sys/class/net').joinpath(iface).exists():
        raise RuntimeError(f'tc interface {iface} does not exist')
    _delete_root(iface)
    if not items:
        return []
    total = _line_rate(c, direction)
    _run(['tc', 'qdisc', 'add', 'dev', iface, 'root', 'handle', '1:', 'htb', 'default', '999'], check=True)
    _run(['tc', 'class', 'add', 'dev', iface, 'parent', '1:', 'classid', '1:999', 'htb',
          'rate', f'{total}kbit', 'ceil', f'{total}kbit'], check=True)
    _run(['tc', 'qdisc', 'add', 'dev', iface, 'parent', '1:999', 'fq_codel'], check=True)
    applied = []
    cls = 10
    for x in items:
        rate = max(8, min(int(x['rate']), total))
        cid = f'1:{cls}'
        _run(['tc', 'class', 'add', 'dev', iface, 'parent', '1:', 'classid', cid, 'htb',
              'rate', f'{rate}kbit', 'ceil', f'{rate}kbit'], check=True)
        _run(['tc', 'qdisc', 'add', 'dev', iface, 'parent', cid, 'fq_codel'], check=True)
        _run(['tc', 'filter', 'add', 'dev', iface, 'parent', '1:', 'protocol', 'ip',
              'prio', '10', 'handle', hex(x['mark']), 'fw', 'flowid', cid], check=True)
        applied.append({**x, 'applied_rate': rate, 'classid': cid})
        cls += 1
    return applied


def _tc_iface_ok(iface, expected_count):
    if expected_count <= 0:
        return True
    q = _run(['tc', 'qdisc', 'show', 'dev', iface])
    if q.returncode or not re.search(r'\bhtb\s+1:\s+root\b', q.stdout):
        return False
    classes = _run(['tc', 'class', 'show', 'dev', iface])
    if classes.returncode or classes.stdout.count('class htb 1:') < expected_count + 1:
        return False
    filters = _run(['tc', 'filter', 'show', 'dev', iface, 'parent', '1:'])
    if filters.returncode:
        return False
    return filters.stdout.count('fw') >= expected_count or filters.stdout.count('flowid 1:') >= expected_count


def _kernel_ok(c, down, up):
    if not down and not up:
        return True
    if _run(['nft', 'list', 'table', 'inet', 'quotagate_shape']).returncode:
        return False
    lan = _iface(c['network'].get('lan_interface', 'wlan0'))
    wan = _iface(_wan_interface(c))
    return _tc_iface_ok(lan, len(down)) and _tc_iface_ok(wan, len(up))


def clear(c):
    global _SHAPE_SIG, _LAST_VERIFY
    n = c['network']
    for iface in {n.get('lan_interface', 'wlan0'), n.get('wan_interface', 'eth0'), _wan_interface(c)}:
        if iface:
            _delete_root(_iface(iface))
    _delete_mark_table()
    _SHAPE_SIG = None
    _LAST_VERIFY = time.time()


def _signature(c, devices, users):
    n = c['network']; g = c.get('guest', {})
    return (
        tuple((int(d['id']), d.get('ip', ''), int(d.get('speed_down_kbit') or 0),
               int(d.get('speed_up_kbit') or 0), int(bool(d.get('is_guest'))),
               int(d.get('user_id') or 0)) for d in sorted(devices, key=lambda x: int(x['id']))),
        tuple((int(u['id']), int(u.get('speed_down_kbit') or 0), int(u.get('speed_up_kbit') or 0))
              for u in sorted(users, key=lambda x: int(x['id']))),
        float(n.get('line_down_mbit') or 0), float(n.get('line_up_mbit') or 0),
        _wan_interface(c),
        int(g.get('speed_down_kbit') or 0), int(g.get('speed_up_kbit') or 0),
        int(bool(c.get('features', {}).get('speed_limits', True))),
    )


def shaping(c, devices, users, force=False):
    global _SHAPE_SIG, _LAST_VERIFY, _LAST_ERROR, _LAST_ERROR_SIG
    if not c.get('features', {}).get('speed_limits', True):
        clear(c)
        _LAST_ERROR = ''
        return True
    _sync_guest_profiles(c, devices, users)
    down, up = _limits(c, devices, users)
    sig = _signature(c, devices, users)
    now = time.time()
    if not force and sig == _SHAPE_SIG and now - _LAST_VERIFY < _VERIFY_INTERVAL:
        return False
    if not force and sig == _SHAPE_SIG and _kernel_ok(c, down, up):
        _LAST_VERIFY = now
        return False
    healing = sig == _SHAPE_SIG
    try:
        _install_marks(c, down, up)
        lan = c['network'].get('lan_interface', 'wlan0')
        wan = _wan_interface(c)
        _shape_iface(lan, 'down', down, c)
        _shape_iface(wan, 'up', up, c)
        if not _kernel_ok(c, down, up):
            raise RuntimeError('tc/nft verification failed after shaping apply')
        _SHAPE_SIG = sig
        _LAST_VERIFY = time.time()
        _LAST_ERROR = ''
        _LAST_ERROR_SIG = None
        if healing:
            db.event('Traffic shaping kernel state was missing and was rebuilt automatically', 'warning')
            db.alert('shaping-self-heal', 'Traffic shaping disappeared from kernel state and was rebuilt automatically')
        elif down or up:
            db.event(f'Traffic shaping applied: {len(down)} download / {len(up)} upload device limits', 'info')
        return True
    except Exception as e:
        _SHAPE_SIG = None
        _LAST_VERIFY = time.time()
        msg = str(e)[:500]
        _LAST_ERROR = msg
        err_sig = (msg, sig)
        if err_sig != _LAST_ERROR_SIG:
            db.event('Traffic shaping apply failed: ' + msg, 'error')
            db.alert('shaping-failed', 'Traffic shaping could not be applied: ' + msg)
            _LAST_ERROR_SIG = err_sig
        raise


def status(c, devices, users):
    enabled = bool(c.get('features', {}).get('speed_limits', True))
    down, up = _limits(c, devices, users)
    healthy = enabled and _kernel_ok(c, down, up) if (down or up) else enabled
    guest_cfg = c.get('guest', {})
    um = {u['id']: u for u in users}
    guests = []
    for d in devices:
        if not d.get('is_guest'):
            continue
        u = um.get(d.get('user_id')) or {}
        ip = str(d.get('ip') or '')
        dr = _effective_rate(d, u, 'speed_down_kbit', c)
        ur = _effective_rate(d, u, 'speed_up_kbit', c)
        has_ip = False
        try:
            ipaddress.ip_address(ip); has_ip = True
        except Exception:
            pass
        guests.append({
            'device_id': int(d['id']),
            'name': d.get('name') or f'Guest {d["id"]}',
            'ip': ip,
            'down_kbit': dr,
            'up_kbit': ur,
            'applied': bool(enabled and healthy and has_ip and (dr > 0 or ur > 0)),
            'reason': '' if has_ip else 'device has no current IP',
        })
    return {
        'enabled': enabled,
        'healthy': bool(healthy),
        'mode': 'per_guest',
        'guest_config': {
            'enabled': bool(guest_cfg.get('enabled')),
            'down_kbit': int(guest_cfg.get('speed_down_kbit', 0) or 0),
            'up_kbit': int(guest_cfg.get('speed_up_kbit', 0) or 0),
        },
        'guest_devices': guests,
        'limited_download_devices': len(down),
        'limited_upload_devices': len(up),
        'last_error': _LAST_ERROR,
        'verification_interval_seconds': int(_VERIFY_INTERVAL),
    }
