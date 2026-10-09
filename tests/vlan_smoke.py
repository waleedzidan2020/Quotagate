#!/usr/bin/env python3
"""Pure smoke tests; no root privileges or network changes needed."""
from unittest.mock import patch
from app import vlans

C = {"network": {
    "wan_interface": "eth0", "lan_interface": "wlan0", "vpn_interface": "tun0",
    "client_net": "192.168.2.0/24", "uplink_net": "192.168.1.0/24",
}}
BASE = {
    "id": 20, "name": "Guests", "parent": "eth1",
    "gateway": "192.168.20.1/24", "dhcp_start": "192.168.20.100",
    "dhcp_end": "192.168.20.200", "enabled": True,
}

def rejects(profiles, message):
    try:
        vlans.validate(C, profiles)
    except ValueError as e:
        assert message in str(e), (message, str(e))
    else:
        raise AssertionError("Expected validation error: " + message)

assert vlans.validate(C, [BASE])[0]["id"] == 20
assert len(vlans.validate(C, [BASE, {**BASE, "id": 30, "gateway": "192.168.30.1/24",
                                  "dhcp_start": "192.168.30.100",
                                  "dhcp_end": "192.168.30.200"}])) == 2
rejects([{**BASE, "parent": "eth0"}], "dedicated")
rejects([{**BASE, "parent": "wlan0"}], "dedicated")
rejects([{**BASE, "gateway": "192.168.1.1/24"}], "overlap")
rejects([{**BASE, "gateway": "192.168.2.1/24"}], "overlap")
rejects([{**BASE, "dhcp_start": "192.168.20.1"}], "gateway")
rejects([{**BASE, "wifi_interface": "wlan0", "ssid": "Guests", "wifi_password": "abcdefgh"}], "dedicated")
rejects([{**BASE, "wifi_interface": "wlan1", "ssid": "Guests", "wifi_password": "123"}], "password")
rejects([BASE, BASE], "unique")
valid = vlans.validate(C, [{**BASE, "wifi_interface": "wlan1",
                            "ssid": "Guests", "wifi_password": "verysecurepass"}])
assert "wifi_password" not in vlans.public_profiles({"vlans": {"profiles": valid}})[0]
with patch.object(vlans, "_interfaces", return_value={"eth0", "eth1", "wlan0", "wlan1"}), \
     patch.object(vlans, "_radio_map", return_value={"wlan0": "phy#0", "wlan1": "phy#0"}):
    try:
        vlans.preflight(C, valid)
    except RuntimeError as exc:
        assert "multiple SSIDs" in str(exc)
    else:
        raise AssertionError("same radio should be refused")

rules = []
running = [{"id": 20, "iface": "qgv20", "wifi": "", "gateway": "192.168.20.1/24"}]
with patch.object(vlans, "_state", return_value=running):
    vlans.forward_rules(lambda cmd: rules.append(cmd), C)
text = [" ".join(x) for x in rules]
assert any("ip daddr 192.168.1.0/24 drop" in x for x in text)
assert any("ip daddr 192.168.2.0/24 drop" in x for x in text)
assert any("input iifname qgv20 drop" in x for x in text)
assert any("oifname eth0 ip saddr 192.168.20.0/24 accept" in x for x in text)
assert not any("wifi_password" in x for x in text)
with patch.object(vlans, "_state", return_value=[]):
    rules.clear()
    vlans.forward_rules(lambda cmd: rules.append(cmd), C)
    assert not rules
print("VLAN validation, secret masking, radio safety, firewall isolation: OK")
