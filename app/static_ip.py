from __future__ import annotations

import ipaddress
import re
import select
import socket
import struct
import threading
import time
from pathlib import Path

from . import config, db, network

_INSTALLED = False
_MAC_RE = re.compile(r'^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$', re.I)
_DISCOVERY_LOCK = threading.RLock()
_PROBE_STATE = {}


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


def _wifi_station_macs(iface):
    """Return MAC addresses currently associated to the QuotaGate AP."""
    p = network.run(['iw', 'dev', str(iface), 'station', 'dump'])
    if p.returncode:
        return []
    out = []
    for line in (p.stdout or '').splitlines():
        m = re.match(r'^Station\s+([0-9a-f:]{17})\b', line.strip(), re.I)
        if m:
            mac = m.group(1).lower()
            if mac not in out:
                out.append(mac)
    return out


def _interface_mac(iface):
    try:
        mac = Path(f'/sys/class/net/{iface}/address').read_text().strip().lower()
        if _MAC_RE.fullmatch(mac):
            return mac
    except Exception:
        pass
    return ''


def _arp_probe(iface, client_net, source_ip, wanted_macs=None, timeout=0.45):
    """Actively discover IPv4 addresses used by associated Wi-Fi stations.

    This does not depend on DHCP, so a client configured with a manual/static
    IPv4 address is still discoverable. The scan is intentionally limited to a
    small LAN and is throttled by _discover_with_static_clients().
    """
    try:
        net = ipaddress.ip_network(str(client_net), strict=False)
        src_ip = ipaddress.ip_address(str(source_ip))
    except Exception:
        return {}
    if net.version != 4 or src_ip.version != 4 or net.num_addresses > 1024:
        return {}
    mac_text = _interface_mac(iface)
    if not mac_text:
        return {}
    wanted = {str(x).lower() for x in (wanted_macs or []) if _MAC_RE.fullmatch(str(x))}
    try:
        src_mac = bytes.fromhex(mac_text.replace(':', ''))
        sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0806))
        sock.bind((str(iface), 0))
        sock.setblocking(False)
    except Exception:
        return {}

    eth = b'\xff' * 6 + src_mac + struct.pack('!H', 0x0806)
    arp_head = struct.pack('!HHBBH', 1, 0x0800, 6, 4, 1)
    zero_mac = b'\x00' * 6
    try:
        for host in net.hosts():
            if host == src_ip:
                continue
            frame = eth + arp_head + src_mac + src_ip.packed + zero_mac + host.packed
            try:
                sock.send(frame)
            except Exception:
                continue

        found = {}
        deadline = time.monotonic() + max(0.05, min(float(timeout), 1.5))
        while time.monotonic() < deadline:
            remain = max(0.0, deadline - time.monotonic())
            try:
                ready, _, _ = select.select([sock], [], [], remain)
            except Exception:
                break
            if not ready:
                break
            try:
                frame = sock.recv(2048)
            except Exception:
                continue
            if len(frame) < 42 or frame[12:14] != b'\x08\x06':
                continue
            try:
                op = struct.unpack('!H', frame[20:22])[0]
            except Exception:
                continue
            if op != 2:
                continue
            smac = ':'.join(f'{b:02x}' for b in frame[22:28])
            if wanted and smac not in wanted:
                continue
            try:
                sip = str(ipaddress.ip_address(frame[28:32]))
            except Exception:
                continue
            try:
                if ipaddress.ip_address(sip) not in net:
                    continue
            except Exception:
                continue
            found[smac] = sip
        return found
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _merge_discovery_rows(base_rows, station_macs, probed):
    """Prefer live ARP evidence over possibly stale DHCP lease addresses."""
    by_mac = {}
    for row in base_rows or []:
        mac = str(row.get('mac') or '').lower()
        if not _MAC_RE.fullmatch(mac):
            continue
        by_mac[mac] = dict(row, mac=mac)
    stations = {str(x).lower() for x in (station_macs or []) if _MAC_RE.fullmatch(str(x))}
    for mac, ip in (probed or {}).items():
        mac = str(mac).lower()
        if stations and mac not in stations:
            continue
        try:
            ip = str(ipaddress.IPv4Address(str(ip)))
        except Exception:
            continue
        row = by_mac.get(mac, {'mac': mac, 'name': ''})
        row['ip'] = ip
        row['source'] = 'arp-probe'
        by_mac[mac] = row
    return list(by_mac.values())


def _discover_with_static_clients(old_discover, iface):
    """Discover DHCP and manual-static Wi-Fi clients without flooding the LAN."""
    base = old_discover(iface)
    try:
        c = config.load()
        n = c.get('network', {})
        if str(iface) != str(n.get('lan_interface', 'wlan0')):
            return base
        stations = _wifi_station_macs(iface)
        if not stations:
            return base
        now = time.monotonic()
        with _DISCOVERY_LOCK:
            state = _PROBE_STATE.setdefault(str(iface), {'at': 0.0, 'rows': {}})
            if now - float(state.get('at', 0.0)) >= 30.0:
                probed = _arp_probe(
                    iface,
                    n.get('client_net', '192.168.2.0/24'),
                    n.get('lan_ip', '192.168.2.1'),
                    wanted_macs=stations,
                )
                if probed:
                    state['rows'] = probed
                else:
                    # Never keep stale active-probe mappings forever.
                    state['rows'] = {}
                state['at'] = now
            probed = dict(state.get('rows') or {})
        return _merge_discovery_rows(base, stations, probed)
    except Exception as e:
        try:
            db.event('Static IP active discovery failed: ' + str(e)[:500], 'warning')
        except Exception:
            pass
        return base


def _lease_for_mac(mac):
    mac = str(mac or '').lower()
    for row in network.leases():
        if str(row.get('mac') or '').lower() == mac:
            return str(row.get('ip') or '')
    return ''


def _neighbor_for_mac(iface, mac):
    mac = str(mac or '').lower()
    for seen_mac, ip in network.neighbors(iface):
        if str(seen_mac).lower() == mac:
            return str(ip)
    return ''


def _drop_lease_file(mac, paths=None):
    """Remove a stale dynamic lease before dnsmasq is restarted."""
    mac = str(mac or '').lower()
    if not _MAC_RE.fullmatch(mac):
        return 0
    paths = paths or (
        '/run/quotagate/dnsmasq.leases',
        '/var/lib/quotagate/dnsmasq.leases',
    )
    removed = 0
    for name in paths:
        p = Path(name)
        if not p.exists():
            continue
        try:
            lines = p.read_text(errors='ignore').splitlines()
            keep = []
            changed = False
            for line in lines:
                parts = line.split()
                if len(parts) >= 2 and parts[1].lower() == mac:
                    removed += 1
                    changed = True
                    continue
                keep.append(line)
            if changed:
                p.write_text(('\n'.join(keep) + '\n') if keep else '')
        except Exception:
            continue
    return removed


def _station_disconnect_command(iface, mac):
    mac = str(mac or '').lower()
    if not _MAC_RE.fullmatch(mac):
        raise ValueError('invalid Wi-Fi station MAC')
    return ['iw', 'dev', str(iface), 'station', 'del', mac]


def device_status(device_id, c=None):
    c = c or config.load()
    with db.con() as cx:
        row = cx.execute(
            "SELECT id,name,mac,ip,reserved_ip FROM devices WHERE id=?",
            (int(device_id),),
        ).fetchone()
    if not row:
        raise ValueError('device not found')
    row = dict(row)
    mac = str(row.get('mac') or '').lower()
    iface = str(c.get('network', {}).get('lan_interface', 'wlan0'))
    stations = set(_wifi_station_macs(iface))
    neighbour_ip = _neighbor_for_mac(iface, mac)
    lease_ip = _lease_for_mac(mac)
    current_ip = neighbour_ip or str(row.get('ip') or '')
    reserved = str(row.get('reserved_ip') or '')
    connected = mac in stations
    matches = bool(reserved and (current_ip == reserved or lease_ip == reserved))
    dhcp_seen = bool(lease_ip)
    return {
        'device_id': int(row['id']),
        'name': row.get('name') or '',
        'mac': mac,
        'current_ip': current_ip,
        'database_ip': str(row.get('ip') or ''),
        'neighbor_ip': neighbour_ip,
        'lease_ip': lease_ip,
        'reserved_ip': reserved,
        'connected': connected,
        'dhcp_seen': dhcp_seen,
        'manual_static_hint': bool(connected and current_ip and not dhcp_seen),
        'matches_reservation': matches,
        'reconnect_supported': connected and bool(reserved),
    }


def apply_reservation_now(device_id, c=None, reload_dnsmasq=True):
    """Reload the reservation and reconnect only the selected Wi-Fi station.

    A DHCP client should request/validate its address after reconnecting. A
    truly manual/static client keeps its locally configured address; QuotaGate
    can discover and manage it but cannot rewrite the client OS network config.
    """
    c = c or config.load()
    before = device_status(device_id, c)
    if not before['reserved_ip']:
        return dict(before, reconnect_sent=False, message='No reservation is configured')
    _drop_lease_file(before['mac'])
    if reload_dnsmasq:
        network.restart_dnsmasq(c)
    sent = False
    error = ''
    if before['connected']:
        p = network.run(_station_disconnect_command(c['network']['lan_interface'], before['mac']))
        if p.returncode == 0:
            sent = True
            db.event(
                f"Static IP reconnect requested for device {int(device_id)} ({before['mac']}) "
                f"to apply {before['reserved_ip']}",
                'info',
            )
        else:
            error = (p.stderr or p.stdout or 'iw station del failed').strip()[:500]
            db.event('Static IP reconnect failed: ' + error, 'warning')
    result = dict(before)
    result['reconnect_sent'] = sent
    result['error'] = error
    result['message'] = (
        'Wi-Fi reconnect sent; DHCP clients should obtain the reserved IP'
        if sent else
        'Reservation is active; it will apply on the next DHCP reconnect'
    )
    return result


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    old_init = db.init
    old_update_device = db.update_device
    old_dhcp_signature = network._dhcp_signature
    old_write_dnsmasq = network.write_dnsmasq
    old_discover = network.discover

    def init():
        old_init()
        with db.L, db.con() as c:
            cols = {r['name'] for r in c.execute('PRAGMA table_info(devices)')}
            if 'reserved_ip' not in cols:
                c.execute("ALTER TABLE devices ADD COLUMN reserved_ip TEXT NOT NULL DEFAULT ''")
            c.execute("INSERT INTO settings(key,value) VALUES('schema_version','7') "
                      "ON CONFLICT(key) DO UPDATE SET value='7'")

    def update_device(i, **kw):
        reservation_present = 'reserved_ip' in kw
        raw_reserved = kw.pop('reserved_ip', None)
        reserved = None
        mac = ''
        old_reserved = ''
        if reservation_present:
            cfg = config.load()
            reserved = _validate_address_value(raw_reserved, cfg)
            with db.con() as c:
                row = c.execute('SELECT id,mac,reserved_ip FROM devices WHERE id=?', (int(i),)).fetchone()
                if not row:
                    raise ValueError('device not found')
                mac = str(row['mac'] or '').lower()
                old_reserved = str(row['reserved_ip'] or '')
                if not _MAC_RE.fullmatch(mac) or mac == '00:00:00:00:00:00':
                    raise ValueError('Static IP reservation requires a valid device MAC')
                if reserved:
                    conflict = c.execute(
                        "SELECT id,name,mac FROM devices WHERE id<>? AND reserved_ip=? LIMIT 1",
                        (int(i), reserved),
                    ).fetchone()
                    if conflict:
                        raise ValueError(f"IP {reserved} is already reserved by {conflict['name'] or conflict['mac']}")
                    # Reject an address that is actively owned by a different LAN neighbour.
                    for seen_mac, seen_ip in network.neighbors(cfg['network']['lan_interface']):
                        if str(seen_ip) == reserved and str(seen_mac).lower() != mac:
                            raise ValueError(f'IP {reserved} is currently active on another device')

        # Keep every previously-installed device wrapper (Guest, quota, shaping,
        # priority) in the call chain for all normal fields.
        old_update_device(i, **kw)
        if reservation_present:
            with db.L, db.con() as c:
                c.execute('UPDATE devices SET reserved_ip=? WHERE id=?', (reserved, int(i)))
            if old_reserved != reserved and mac:
                _drop_lease_file(mac)
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

    def discover(iface):
        return _discover_with_static_clients(old_discover, iface)

    db.init = init
    db.update_device = update_device
    network._dhcp_signature = dhcp_signature
    network.write_dnsmasq = write_dnsmasq
    network.discover = discover
