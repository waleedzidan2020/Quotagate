from app import static_ip


def main():
    c={
        'network':{
            'client_net':'192.168.2.0/24',
            'lan_ip':'192.168.2.1',
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

    print('static IP reservation semantics: OK')


if __name__=='__main__':
    main()
