from pathlib import Path
from tempfile import TemporaryDirectory

from app import static_ip


def main():
    c={
        'network':{
            'client_net':'192.168.2.0/24',
            'lan_ip':'192.168.2.1',
            'lan_interface':'wlan0',
        }
    }
    assert static_ip._validate_address_value('',c)==''
    assert static_ip._validate_address_value('192.168.2.50',c)=='192.168.2.50'

    for bad in ('192.168.3.50','192.168.2.0','192.168.2.255','192.168.2.1','not-an-ip'):
        try:
            static_ip._validate_address_value(bad,c)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{bad} must be rejected')

    mac='aa:bb:cc:dd:ee:ff'
    assert static_ip._reservation_line(mac,'192.168.2.50')=='dhcp-host=aa:bb:cc:dd:ee:ff,192.168.2.50'
    assert static_ip._reservation_line(mac,'192.168.2.50',True)=='dhcp-host=aa:bb:cc:dd:ee:ff,set:qgknown,192.168.2.50'

    base=[
        '# QuotaGate dedicated DHCP only',
        'dhcp-range=192.168.2.100,192.168.2.200,255.255.255.0,12h',
        'dhcp-host=aa:bb:cc:dd:ee:ff,set:qgknown',
        'dhcp-ignore=tag:!qgknown',
    ]
    rows=[{'mac':mac,'reserved_ip':'192.168.2.50'}]
    merged=static_ip._render_dnsmasq_lines(base,rows,True)
    assert 'dhcp-host=aa:bb:cc:dd:ee:ff,set:qgknown' not in merged
    assert 'dhcp-host=aa:bb:cc:dd:ee:ff,set:qgknown,192.168.2.50' in merged
    assert 'dhcp-ignore=tag:!qgknown' in merged

    # A live ARP result must override a stale DHCP lease address for the same
    # associated Wi-Fi station, and manual-static stations must be addable even
    # when they have no DHCP lease at all.
    base_rows=[
        {'mac':'aa:bb:cc:dd:ee:01','ip':'192.168.2.150','name':'dhcp-client'},
    ]
    probed={
        'aa:bb:cc:dd:ee:01':'192.168.2.23',
        'aa:bb:cc:dd:ee:02':'192.168.2.44',
        'aa:bb:cc:dd:ee:99':'192.168.2.99',
    }
    discovered=static_ip._merge_discovery_rows(
        base_rows,
        ['aa:bb:cc:dd:ee:01','aa:bb:cc:dd:ee:02'],
        probed,
    )
    by_mac={x['mac']:x for x in discovered}
    assert by_mac['aa:bb:cc:dd:ee:01']['ip']=='192.168.2.23'
    assert by_mac['aa:bb:cc:dd:ee:02']['ip']=='192.168.2.44'
    assert 'aa:bb:cc:dd:ee:99' not in by_mac
    assert by_mac['aa:bb:cc:dd:ee:02']['source']=='arp-probe'

    assert static_ip._station_disconnect_command('wlan0',mac)==[
        'iw','dev','wlan0','station','del',mac
    ]

    # Changing a reservation must be able to remove the client's stale lease
    # before the dedicated dnsmasq instance reloads it.
    with TemporaryDirectory() as td:
        lease=Path(td)/'dnsmasq.leases'
        lease.write_text(
            '111 aa:bb:cc:dd:ee:ff 192.168.2.150 client *\n'
            '222 aa:bb:cc:dd:ee:10 192.168.2.151 other *\n'
        )
        removed=static_ip._drop_lease_file(mac,[str(lease)])
        assert removed==1
        text=lease.read_text()
        assert 'aa:bb:cc:dd:ee:ff' not in text
        assert 'aa:bb:cc:dd:ee:10' in text

    # The station parser must only return associated MACs.
    old_run=static_ip.network.run
    class P:
        returncode=0
        stderr=''
        stdout='''Station aa:bb:cc:dd:ee:01 (on wlan0)
        signal: -50 dBm
Station AA:BB:CC:DD:EE:02 (on wlan0)
'''
    try:
        static_ip.network.run=lambda *a,**k:P()
        assert static_ip._wifi_station_macs('wlan0')==[
            'aa:bb:cc:dd:ee:01','aa:bb:cc:dd:ee:02'
        ]
    finally:
        static_ip.network.run=old_run

    js=Path('web/static-ip.js').read_text(encoding='utf-8')
    assert '/api/device/static-ip/status' in js
    assert '/api/device/static-ip/apply' in js
    assert 'Apply / Reconnect Now' in js
    assert 'Manual Static' in js

    print('static IP reservation/discovery semantics: OK')


if __name__=='__main__':
    main()
