# VLAN Management (optional, safe-by-default)

QuotaGate's primary topology remains **eth0 WAN → wlan0 primary AP**, unchanged.
The VLAN feature is opt-in and uses a **separate dedicated Ethernet trunk** (for example eth1).
It will reject eth0, wlan0, VPN interfaces and overlapping IPv4 /24 subnets.

## Required hardware

* A second Ethernet interface on antiX, connected to a VLAN-aware switch or AP that accepts 802.1Q tags.
* To broadcast a *new Wi-Fi SSID from this laptop*, an additional, dedicated Wi-Fi adapter with Linux nl80211 AP support. Set its interface as wlan1 (example) and specify its SSID/password. This design uses **one dedicated radio per secondary SSID**. The built-in Broadcom BCM4318 / b43 reports "interface combinations are not supported": **it cannot be relied on for multiple simultaneous SSIDs**.
* Alternative: use a VLAN-capable external AP. Leave the VLAN Wi-Fi interface/SSID/password empty in QuotaGate, and map SSID to VLAN 20 on the external AP. This is recommended for 2+ SSIDs.

## Network design example

* eth0 = untagged WAN from Huawei (192.168.1.x), **do not select as VLAN trunk**.
* wlan0 = existing QuotaGate SSID/subnet (192.168.2.1/24).
* eth1 = new physical port to a managed switch or VLAN-aware AP (tagged trunk).
* qgv20 = VLAN 20; 192.168.20.1/24; DHCP 192.168.20.100–200.
* Optional wlan1 = independent Wi-Fi adapter. QuotaGate runs its own hostapd on wlan1, connects the BSS to qgbr20 and bridges qgv20 into qgbr20. The gateway and VLAN DHCP server bind to qgbr20.

**Important:** if using a switch/AP, configure its trunk ports and SSID VLAN tagging independently. Creating qgv20 on Linux alone does not configure the physical switch, and the WAN Huawei HG630V2 is not assumed to support it.

## Dashboard

1. Update the app from the PR branch after checking code, and open the independent **VLANs / الشبكات** tab.
2. Click **+ شبكة VLAN**; enter ID 20, trunk eth1, gateway 192.168.20.1/24, DHCP pool, and optionally a *dedicated extra* WLAN interface, SSID and 8–63 byte WPA2 password.
3. Set Enabled. **Save only** validates and persists settings without changing networking.
4. Use the separate **Apply VLANs** action to start the VLAN interface, DHCP, NAT, and optional hostapd; the primary AP should stay untouched.
5. On reboot QuotaGate tries to apply saved enabled VLAN profiles. If hardware is missing, it logs the error and keeps starting its primary network.

## Commands on antiX (run locally, not by GitHub)

Read-only checks:

    ip -br link
    iw dev
    ip -d link show qgv20
    sudo nft list chain inet quotagate forward
    sudo nft list chain inet quotagate input
    sudo nft list table ip qg_vlan_nat
    sudo ss -lunp | grep ':67'

Connectivity from a device on VLAN 20:

* Receive 192.168.20.x with gateway 192.168.20.1
* Internet works (public IP and DNS)
* Cannot reach 192.168.1.0/24, 192.168.2.0/24, other VLANs, or QuotaGate dashboard.
* Test IPv6 separately; IPv6 forwarding from VLAN is blocked by this feature.
* Verify from a **non-VLAN** client that the original QuotaGate Wi-Fi and Internet still work.

VLAN forward allow + deny rules are maintained in QuotaGate's existing nftables firewall; NAT rules use a separate qg_vlan_nat table. Only explicitly applied running profiles are used for firewall rules, not unsaved draft changes.

## Limitations / production caution

* Existing QuotaGate device discovery, accounting, quotas and per-device shaping currently operate on **the primary wlan0 subnet only**. VLAN clients have isolated DHCP/NAT/firewall but are **not** integrated with the regular quota dashboard yet.
* VLAN applies restart VLAN clients (not the main wlan0); preflight rejects missing adapters or reusing the primary radio.
* The feature requires a real antiX host for hardware tests; GitHub CI covers validation and syntax, not radio/driver support, tagging on the switch, or actual DHCP/NAT.
* VLAN clients are prevented from connecting to the QuotaGate host except for DHCP. WPA2-PSK is used for the dedicated extra AP. Use strong passwords and trusted equipment.
* Keep physical/console access during the first test. Back up /etc/quotagate/config.json before trying networking changes.
