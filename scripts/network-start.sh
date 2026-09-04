#!/bin/sh
set -eu
CFG=/etc/quotagate/config.json
[ -f "$CFG" ] || exit 1
WAN=$(python3 -c 'import json;print(json.load(open("/etc/quotagate/config.json"))["network"]["wan_interface"])')
LAN=$(python3 -c 'import json;print(json.load(open("/etc/quotagate/config.json"))["network"]["lan_interface"])')
LANIP=$(python3 -c 'import json;print(json.load(open("/etc/quotagate/config.json"))["network"]["lan_ip"])')
PREFIX=$(python3 -c 'import json;print(json.load(open("/etc/quotagate/config.json"))["network"]["lan_prefix"])')
modprobe b43 2>/dev/null || true
n=0
while [ ! -e "/sys/class/net/$LAN" ] && [ "$n" -lt 20 ]; do n=$((n+1)); sleep 1; done
[ -e "/sys/class/net/$LAN" ] || { echo "QuotaGate: $LAN not found" >&2; exit 1; }
ip link set "$LAN" up
ip addr replace "$LANIP/$PREFIX" dev "$LAN"
sysctl -w net.ipv4.ip_forward=1 >/dev/null
# Main QuotaGate process starts hostapd, dedicated DHCP dnsmasq, DNS proxy and nftables.
echo "QuotaGate interface ready: $WAN -> $LAN ($LANIP)"
