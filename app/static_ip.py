from __future__ import annotations

import ipaddress
import re
from pathlib import Path

from . import config, db, network

_INSTALLED = False
_MAC_RE = re.compile(r'^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$', re.I)


def _validate_address_value(value, c):
    """Validate one IPv4 reservation against the configured QuotaGate LAN."""
    value = str(value or '').strip()
    if not value:
        return ''
    try:
        addr = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError('Reserved IP must be a valid IPv4 address') from exc
    if addr.version != 4:
        raise ValueError('Reserved IP must be IPv4')

    n = c['network']
    net = ipaddress.ip_network(str(n['client_net']), strict=False)
    if addr not in net:
        raise ValueError(f'Reserved IP must be inside {net}')
    if addr in (net.network_address, net.broadcast_address):
        raise ValueError('Reserved IP cannot be the network or broadcast address')
    if str(addr) == str(n.get('lan_ip') or ''):
        raise ValueError('Reserved IP cannot be the QuotaGate gateway address')
    return str(addr)


def _reservation_line(mac, ip, known_only=False):
    mac = str(mac or '').lower().strip()
    if not _MAC_RE.fullmatch(mac):
        raise ValueError('Device does not have a valid MAC address')
    if known_only:
        return f'dhcp-host={mac},set:qgknown,{ip}'
    return f'dhcp-host={mac},{ip}'


def _reservation_rows():
    try:
        with db.con() as c:
            return [dict(r) for r in c.execute(
                "SELECT id,name,mac,reserved_ip FROM devices WHERE reserved_ip<>'' ORDER BY id"
            )]
    except Exception:
        # The column does not exist until db.init() performs the migration.
        return []


def _render_dnsmasq_lines(lines, reservations, stop_new_connections=False):
    """Merge reservations into QuotaGate's generated dnsmasq config.

    When STOP NEW CONNECTIONS is enabled, the base generator already emits a
    dhcp-host=<mac>,set:qgknown line. Replace that line with a single directive
    that both tags the device as known and reserves its address.
    """
    reserved_macs = {str(x['mac']).lower() for x in reservations}
    out = []
    for line in lines:
        lower = line.lower()
        if lower.startswith('dhcp-host='):
            mac = lower.split('=', 1)[1].split(',', 1)[0]
            if mac in reserved_macs:
                continue
        out.append(line)
    for row in reservations:
        out.append(_reservation_line(row['mac'], row['reserved_ip'], bool(stop_new_connections)))
    return out


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    old_init = db.init
    old_update_device = db.update_device
    old_dhcp_signature = network._dhcp_signature
    old_write_dnsmasq = network.write_dnsmasq

    def init():
        old_init()
        with db.L, db.con() as c:
            cols = {r['name'] for r in c.execute('PRAGMA table_info(devices)')}
            if 'reserved_ip' not in cols:
                c.execute("ALTER TABLE devices ADD COLUMN reserved_ip TEXT NOT NULL DEFAULT ''")
            c.execute("INSERT INTO settings(key,value) VALUES('schema_version','6') "
                      "ON CONFLICT(key) DO UPDATE SET value='6'")

    def update_device(i, **kw):
        reservation_present = 'reserved_ip' in kw
        raw_reserved = kw.pop('reserved_ip', None)
        reserved = None
        if reservation_present:
            cfg = config.load()
            reserved = _validate_address_value(raw_reserved, cfg)
            with db.con() as c:
                row = c.execute('SELECT id,mac FROM devices WHERE id=?', (int(i),)).fetchone()
                if not row:
                    raise ValueError('device not found')
                mac = str(row['mac'] or '').lower()
                if not _MAC_RE.fullmatch(mac) or mac == '00:00:00:00:00:00':
                    raise ValueError('Static IP reservation requires a valid device MAC')
                if reserved:
                    conflict = c.execute(
                        "SELECT id,name,mac FROM devices WHERE id<>? AND (reserved_ip=? OR ip=?) LIMIT 1",
                        (int(i), reserved, reserved),
                    ).fetchone()
                    if conflict:
                        raise ValueError(f"IP {reserved} is already used by {conflict['name'] or conflict['mac']}")

        # Keep every previously-installed device wrapper (Guest, quota, shaping,
        # priority) in the call chain for all normal fields.
        old_update_device(i, **kw)
        if reservation_present:
            with db.L, db.con() as c:
                c.execute('UPDATE devices SET reserved_ip=? WHERE id=?', (reserved, int(i)))
            db.event(
                f"Static IP reservation {'set to '+reserved if reserved else 'cleared'} for device {int(i)}",
                'info',
            )

    def dhcp_signature(c):
        base = old_dhcp_signature(c)
        reservations = tuple(
            sorted((str(x['mac']).lower(), str(x['reserved_ip'])) for x in _reservation_rows())
        )
        return base + (reservations,)

    def write_dnsmasq(c):
        old_write_dnsmasq(c)
        target = Path('/run/quotagate/dnsmasq.conf')
        reservations = _reservation_rows()
        if not target.exists() or not reservations:
            return
        lines = target.read_text(errors='ignore').splitlines()
        merged = _render_dnsmasq_lines(lines, reservations, bool(c['network'].get('stop_new_connections')))
        target.write_text('\n'.join(merged) + '\n')
        target.chmod(0o600)

    db.init = init
    db.update_device = update_device
    network._dhcp_signature = dhcp_signature
    network.write_dnsmasq = write_dnsmasq
