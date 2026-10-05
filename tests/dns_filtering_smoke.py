from __future__ import annotations

import struct
import tempfile
from pathlib import Path

from app import db, dnsproxy, network, domainblock

ROOT = Path(__file__).resolve().parent.parent


def query(name: str, qtype: int = 1) -> bytes:
    labels = b''.join(bytes([len(x)]) + x.encode('ascii') for x in name.split('.'))
    return b'\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00' + labels + b'\x00' + struct.pack('!HH', qtype, 1)


def rcode(packet: bytes) -> int:
    return struct.unpack('!H', packet[2:4])[0] & 0xF


def check_normalization_and_groups() -> None:
    assert db.normalize_dns_domain('youtube.com') == 'youtube.com'
    assert db.normalize_dns_domain('https://www.youtube.com/watch?v=1') == 'youtube.com'
    assert db.normalize_dns_domain('*.Example.COM.') == 'example.com'
    assert dnsproxy.rule_matches('www.youtube.com', 'youtube.com')
    assert dnsproxy.rule_matches('r5---sn.example.googlevideo.com', 'youtube.com')
    assert dnsproxy.rule_matches('i.ytimg.com', 'youtube.com')
    assert not dnsproxy.rule_matches('google.com', 'youtube.com')


def check_scoped_policy() -> None:
    original_device = dnsproxy.db.device_by_ip
    original_rules = dnsproxy.db.dns_rules
    original_forward = dnsproxy.forward_udp
    original_forward_tcp = dnsproxy.forward_tcp
    original_observe = dnsproxy.domainblock.observe_block
    try:
        dnsproxy.db.device_by_ip = lambda ip: (
            {'id': 7, 'user_id': 3} if ip == '192.168.2.50'
            else {'id': 8, 'user_id': 4}
        )
        dnsproxy.forward_udp = lambda data, c, client_ip='': b'FORWARDED'
        dnsproxy.forward_tcp = lambda data, c, client_ip='': b'TCP_FORWARDED'
        dnsproxy.domainblock.observe_block = lambda rule, domain, cfg: None

        # A device-only block must affect exactly that device.
        dnsproxy.db.dns_rules = lambda: [
            {'id': 10, 'scope_type': 'device', 'scope_id': 7, 'domain': 'example.com', 'action': 'block', 'target': '', 'enabled': 1}
        ]
        ans, domain, qt, action = dnsproxy.process_query(query('www.example.com'), {}, '192.168.2.50')
        assert domain == 'www.example.com' and action == 'block' and rcode(ans) == 3
        ans, _, _, action = dnsproxy.process_query(query('www.example.com'), {}, '192.168.2.51')
        assert ans == b'FORWARDED' and action == 'allow'

        ans, _, _, action = dnsproxy.process_query(query('www.example.com'), {}, '192.168.2.51', transport='tcp')
        assert ans == b'TCP_FORWARDED' and action == 'allow'

        # A global youtube.com block expands to YouTube media/CDN hostnames.
        dnsproxy.db.dns_rules = lambda: [
            {'id': 11, 'scope_type': 'global', 'scope_id': 0, 'domain': 'youtube.com', 'action': 'block', 'target': '', 'enabled': 1}
        ]
        ans, domain, _, action = dnsproxy.process_query(query('r3---sn-abcd.googlevideo.com'), {}, '192.168.2.51')
        assert domain.endswith('googlevideo.com') and action == 'block' and rcode(ans) == 3

        # More-specific device allow overrides a global block for that device only.
        dnsproxy.db.dns_rules = lambda: [
            {'id': 20, 'scope_type': 'device', 'scope_id': 7, 'domain': 'youtube.com', 'action': 'allow', 'target': '', 'enabled': 1},
            {'id': 19, 'scope_type': 'global', 'scope_id': 0, 'domain': 'youtube.com', 'action': 'block', 'target': '', 'enabled': 1},
        ]
        ans, _, _, action = dnsproxy.process_query(query('www.youtube.com'), {}, '192.168.2.50')
        assert ans == b'FORWARDED' and action == 'allow'
        ans, _, _, action = dnsproxy.process_query(query('www.youtube.com'), {}, '192.168.2.51')
        assert action == 'block' and rcode(ans) == 3
    finally:
        dnsproxy.db.device_by_ip = original_device
        dnsproxy.db.dns_rules = original_rules
        dnsproxy.forward_udp = original_forward
        dnsproxy.forward_tcp = original_forward_tcp
        dnsproxy.domainblock.observe_block = original_observe


def check_network_enforcement() -> None:
    c = {
        'features': {'dns_proxy': True},
        'dns': {'enforce_local': True, 'block_dot': True},
        'network': {
            'lan_interface': 'wlan0',
            'lan_ip': '192.168.2.1',
            'client_net': '192.168.2.0/24',
        },
    }
    redirect = network.dns_redirect_commands(c)
    assert len(redirect) == 2
    assert all('53' in cmd and 'dnat' in cmd and '192.168.2.1:53' in cmd for cmd in redirect)
    assert any('udp' in cmd for cmd in redirect)
    assert any('tcp' in cmd for cmd in redirect)

    dot = network.dns_dot_block_commands(c)
    assert len(dot) == 2
    assert all('853' in cmd and cmd[-1] == 'drop' for cmd in dot)

    original_run = network.run
    class P:
        def __init__(self, out, code=0):
            self.stdout = out
            self.stderr = ''
            self.returncode = code
    try:
        def fake_run(cmd, check=False, input_text=None):
            text = ' '.join(cmd)
            if 'quotagate_nat prerouting' in text:
                return P('udp dport 53 dnat to 192.168.2.1:53\ntcp dport 53 dnat to 192.168.2.1:53\n')
            if 'inet quotagate forward' in text:
                return P('tcp dport 853 drop\nudp dport 853 drop\n')
            return P('', 1)
        network.run = fake_run
        status = network.dns_enforcement_status(c)
        assert status['ok'] and status['udp53'] and status['tcp53'] and status['dot_block']
    finally:
        network.run = original_run

    c['dns']['enforce_local'] = False
    assert network.dns_redirect_commands(c) == []
    assert network.dns_dot_block_commands(c) == []



def check_ip_guard_db() -> None:
    original_db = db.DB
    try:
        with tempfile.TemporaryDirectory() as td:
            db.DB = Path(td) / 'qg.db'
            db.init()
            rid = db.add_dns_rule('global', 0, 'youtube.com', 'block')
            db.remember_dns_block_ip(rid, 'www.youtube.com', '142.250.1.10', 300)
            rows = db.dns_block_ips()
            assert len(rows) == 1 and rows[0]['rule_id'] == rid
            assert rows[0]['scope_type'] == 'global' and rows[0]['action'] == 'block'
            db.del_dns_rule(rid)
            assert db.dns_block_ips() == []
    finally:
        db.DB = original_db


def check_ip_guard_nft() -> None:
    cfg = {
        'features': {'dns_proxy': True},
        'dns': {'ip_guard_enabled': True, 'ip_guard_seconds': 300},
        'network': {'client_net': '192.168.2.0/24'},
    }
    rows = [
        {'rule_id': 1, 'domain': 'www.youtube.com', 'ip': '142.250.1.10', 'scope_type': 'global', 'scope_id': 0},
        {'rule_id': 2, 'domain': 'example.com', 'ip': '203.0.113.20', 'scope_type': 'device', 'scope_id': 7},
        {'rule_id': 3, 'domain': 'example.net', 'ip': '198.51.100.30', 'scope_type': 'user', 'scope_id': 3},
    ]
    devices = [
        {'id': 7, 'ip': '192.168.2.50', 'user_id': 3},
        {'id': 8, 'ip': '192.168.2.51', 'user_id': 3},
    ]
    original_run = domainblock.network.run
    original_prune = domainblock.db.prune_dns_block_ips
    original_rows = domainblock.db.dns_block_ips
    original_devices = domainblock.db.devices
    original_sig = domainblock._SYNC_SIG
    calls = []
    class P:
        def __init__(self, code=0, out=''):
            self.returncode = code
            self.stdout = out
            self.stderr = ''
    try:
        domainblock.db.prune_dns_block_ips = lambda: None
        domainblock.db.dns_block_ips = lambda: list(rows)
        domainblock.db.devices = lambda: list(devices)
        def fake_run(cmd, check=False, input_text=None):
            calls.append((list(cmd), input_text))
            if cmd[:3] == ['nft', 'delete', 'table']:
                return P(1)
            return P(0, 'table inet quotagate_dnsblock {}')
        domainblock.network.run = fake_run
        result = domainblock.sync(cfg, force=True)
        assert result['ok'] and result['tracked_ips'] == 3
        scripts = [text for cmd, text in calls if cmd == ['nft', '-f', '-']]
        assert len(scripts) == 1
        script = scripts[0]
        assert 'table inet quotagate_dnsblock' in script
        assert 'ip saddr 192.168.2.0/24 ip daddr @global_v4 counter drop' in script
        assert 'ip saddr 192.168.2.50 ip daddr @d_7_v4 counter drop' in script
        assert 'ip saddr 192.168.2.50 ip daddr @u_3_v4 counter drop' in script
        assert 'ip saddr 192.168.2.51 ip daddr @u_3_v4 counter drop' in script
        assert 'priority -20' in script
        st = domainblock.status(cfg)
        assert st['ok'] and st['tracked_ips'] == 3
    finally:
        domainblock.network.run = original_run
        domainblock.db.prune_dns_block_ips = original_prune
        domainblock.db.dns_block_ips = original_rows
        domainblock.db.devices = original_devices
        domainblock._SYNC_SIG = original_sig


def check_frontend() -> None:
    js = (ROOT / 'web' / 'dns-filtering.js').read_text(encoding='utf-8')
    loader = (ROOT / 'web' / 'app.js').read_text(encoding='utf-8')
    assert "load('dns-filtering.js')" in loader
    assert "scope==='device'" in js
    assert "اختر جهازاً" in js
    assert "youtube.com" in js
    assert "/api/dns/rule/add" in js
    assert "/api/dns/status" in js
    assert "Active-session IP guard" in js


if __name__ == '__main__':
    check_normalization_and_groups()
    check_scoped_policy()
    check_network_enforcement()
    check_ip_guard_db()
    check_ip_guard_nft()
    check_frontend()
    print('DNS filtering smoke checks: OK')
