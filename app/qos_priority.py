from __future__ import annotations
import ipaddress
from . import db, shaping, gaming

_ALLOWED = {'high', 'normal', 'low'}
_PRIO = {'high': 0, 'normal': 1, 'low': 2}
_INSTALLED = False


def normalize(value, default='normal'):
    value = str(value or default).lower().strip()
    if value not in _ALLOWED:
        raise ValueError('priority must be high, normal or low')
    return value


def effective_priority(device):
    default = 'low' if device.get('is_guest') else 'normal'
    try:
        return normalize(device.get('priority') or default, default)
    except ValueError:
        return default


def priority_status(devices):
    gs = gaming.status(devices)
    selected = int(gs.get('device_id') or 0) if gs.get('active') else 0
    other = (gs.get('policy') or {}).get('other_priority', 'low')
    out = []
    for d in devices:
        base = effective_priority(d)
        eff = 'high' if int(d['id']) == selected else (other if selected else base)
        out.append({'id': int(d['id']), 'priority': base, 'effective_priority': eff})
    return {'devices': out, 'gaming': gs}


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    old_init = db.init
    old_update_device = db.update_device
    old_limits = shaping._limits
    old_shape_iface = shaping._shape_iface
    old_signature = shaping._signature

    def init():
        old_init()
        with db.L, db.con() as c:
            cols = {r['name'] for r in c.execute('PRAGMA table_info(devices)')}
            added = 'priority' not in cols
            if added:
                c.execute("ALTER TABLE devices ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'")
                c.execute("UPDATE devices SET priority='low' WHERE is_guest=1")
            c.execute("UPDATE devices SET priority='normal' WHERE priority NOT IN ('high','normal','low') OR priority IS NULL")
            c.execute("INSERT INTO settings(key,value) VALUES('schema_version','5') ON CONFLICT(key) DO UPDATE SET value='5'")

    def update_device(i, **kw):
        priority_present = 'priority' in kw
        priority = kw.pop('priority', None)
        if priority_present:
            priority = normalize(priority)
        # Preserve explicit High/Low when a device becomes Guest; otherwise use the
        # safe Guest default Low without changing unrelated device fields.
        guestify = int(kw.get('is_guest') or 0) == 1
        old_update_device(i, **kw)
        if priority_present:
            with db.L, db.con() as c:
                c.execute('UPDATE devices SET priority=? WHERE id=?', (priority, int(i)))
        elif guestify:
            with db.L, db.con() as c:
                row = c.execute('SELECT priority FROM devices WHERE id=?', (int(i),)).fetchone()
                if row and str(row['priority'] or 'normal') == 'normal':
                    c.execute("UPDATE devices SET priority='low' WHERE id=?", (int(i),))

    def limits(c, devices, users):
        um = {u['id']: u for u in users}
        rows = []
        game_active = bool(gaming.current())
        for d in sorted(devices, key=lambda x: int(x.get('id') or 0)):
            ip = str(d.get('ip') or '')
            try:
                addr = ipaddress.ip_address(ip)
                if addr.version != 4:
                    continue
            except Exception:
                continue
            user = um.get(d.get('user_id')) or {}
            base_prio = effective_priority(d)
            dr = shaping._effective_rate(d, user, 'speed_down_kbit', c)
            ur = shaping._effective_rate(d, user, 'speed_up_kbit', c)
            dr, dp = gaming.effective_policy(d, 'down', dr, base_prio)
            ur, up = gaming.effective_policy(d, 'up', ur, base_prio)
            rows.append((d, ip, dr, ur, dp, up))

        # Do not alter legacy behavior until priority is actually used. Once any
        # device is High/Low (or Gaming Mode is active), classify all known devices
        # so Normal traffic cannot bypass the same line-rate scheduler.
        priority_active = game_active or any(dp != 'normal' or up != 'normal' for _, _, _, _, dp, up in rows)
        down_total = shaping._line_rate(c, 'down') if priority_active and rows else 0
        up_total = shaping._line_rate(c, 'up') if priority_active and rows else 0
        down, up_items = [], []
        for d, ip, dr, ur, dp, up in rows:
            mark = shaping._device_mark(d['id'])
            item = {
                'id': int(d['id']), 'ip': ip, 'mark': mark,
                'classid': shaping._device_classid(d['id']),
                'name': d.get('name') or ip, 'is_guest': bool(d.get('is_guest')),
            }
            if dr > 0 or priority_active:
                down.append({**item, 'rate': int(dr or down_total), 'priority': dp})
            if ur > 0 or priority_active:
                up_items.append({**item, 'rate': int(ur or up_total), 'priority': up})
        return down, up_items

    def shape_iface(iface, direction, items, c):
        iface = shaping._iface(iface)
        from pathlib import Path
        if not Path('/sys/class/net').joinpath(iface).exists():
            raise RuntimeError(f'tc interface {iface} does not exist')
        prepared, total = shaping._prepared_items(c, direction, items)
        if not prepared:
            shaping._delete_root(iface)
            return []

        total = int(total or 0)
        pass_rate = max(shaping._PASS_THROUGH_KBIT, total)
        shaping._ensure_root(iface, pass_rate)
        # 1:1 is the shared Internet line. Device children borrow unused bandwidth
        # up to their own ceil; HTB prio decides who borrows first under congestion.
        shaping._run(['tc', 'class', 'replace', 'dev', iface, 'parent', '1:', 'classid', '1:1', 'htb',
                      'rate', f'{total}kbit', 'ceil', f'{total}kbit'], check=True)
        shaping._run(['tc', 'filter', 'del', 'dev', iface, 'parent', '1:', 'protocol', 'ip', 'prio', '10'])

        # Small guarantees keep the sum safely below the parent while leaving most
        # capacity borrowable according to High/Normal/Low priority.
        guarantee = max(1, total // max(100, len(prepared) * 20))
        applied = []
        for x in prepared:
            cid = x['classid']
            ceiling = int(x['rate'])
            rate = max(1, min(ceiling, guarantee))
            p = normalize(x.get('priority') or 'normal')
            shaping._run(['tc', 'class', 'replace', 'dev', iface, 'parent', '1:1', 'classid', cid, 'htb',
                          'rate', f'{rate}kbit', 'ceil', f'{ceiling}kbit', 'prio', str(_PRIO[p])], check=True)
            shaping._run(['tc', 'qdisc', 'replace', 'dev', iface, 'parent', cid, 'fq_codel'], check=True)
            shaping._run(['tc', 'filter', 'add', 'dev', iface, 'parent', '1:', 'protocol', 'ip',
                          'prio', '10', 'handle', hex(x['mark']), 'fw', 'flowid', cid], check=True)
            applied.append({**x, 'applied_rate': ceiling, 'priority': p})
        return applied

    def signature(c, devices, users):
        base = old_signature(c, devices, users)
        priorities = tuple((int(d['id']), effective_priority(d)) for d in sorted(devices, key=lambda x: int(x['id'])))
        return base + (priorities, gaming.signature())

    db.init = init
    db.update_device = update_device
    shaping._limits = limits
    shaping._shape_iface = shape_iface
    shaping._signature = signature
