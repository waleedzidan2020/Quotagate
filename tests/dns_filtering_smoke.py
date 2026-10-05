from __future__ import annotations

import struct
from pathlib import Path

from app import db, dnsproxy, network

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
    try:
        dnsproxy.db.device_by_ip = lambda ip: (
            {'id': 7, 'user_id': 3} if ip == '192.168.2.50'
            else {'id': 8, 'user_id': 4}
        )
        dnsproxy.forward_udp = lambda data, c, client_ip='': b'FORWARDED'

        # A device-only block must affect exactly that device.
        dnsproxy.db.dns_rules = lambda: [
            {'id': 10, 'scope_type': 'device', 'scope_id': 7, 'domain': 'example.com', 'action': 'block', 'target': '', 'enabled': 1}
        ]
        ans, domain, qt, action = dnsproxy.process_query(query('www.example.com'), {}, '192.168.2.50')
        assert domain == 'www.example.com' and action == 'block' and rcode(ans) == 3
        ans, _, _, action = dnsproxy.process_query(query('www.example.com'), {}, '192.168.2.51')
        assert ans == b'FORWARDED' and action == 'allow'

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

    c['dns']['enforce_local'] = False
    assert network.dns_redirect_commands(c) == []
    assert network.dns_dot_block_commands(c) == []


def check_frontend() -> None:
    js = (ROOT / 'web' / 'dns-filtering.js').read_text(encoding='utf-8')
    loader = (ROOT / 'web' / 'app.js').read_text(encoding='utf-8')
    assert "load('dns-filtering.js')" in loader
    assert "scope==='device'" in js
    assert "اختر جهازاً" in js
    assert "youtube.com" in js
    assert "/api/dns/rule/add" in js


if __name__ == '__main__':
    check_normalization_and_groups()
    check_scoped_policy()
    check_network_enforcement()
    check_frontend()
    print('DNS filtering smoke checks: OK')
