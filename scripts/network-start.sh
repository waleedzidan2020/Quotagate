#!/bin/sh
set -eu
CFG=/etc/quotagate/config.json
[ -f "$CFG" ] || { echo "QuotaGate: missing $CFG" >&2; exit 1; }
read_cfg(){ python3 - "$1" <<'PY'
import json,sys
c=json.load(open('/etc/quotagate/config.json'))
x=c
for p in sys.argv[1].split('.'): x=x[p]
print(x)
PY
}
WAN=$(read_cfg network.wan_interface)
LAN=$(read_cfg network.lan_interface)
LANIP=$(read_cfg network.lan_ip)
PREFIX=$(read_cfg network.lan_prefix)
install -d -m0700 /run/quotagate
modprobe b44 2>/dev/null || true
modprobe b43 2>/dev/null || true
n=0
while [ ! -e "/sys/class/net/$LAN" ] && [ "$n" -lt 20 ]; do n=$((n+1)); sleep 1; done
[ -e "/sys/class/net/$LAN" ] || { echo "QuotaGate: LAN $LAN not found" >&2; exit 1; }
if [ ! -e "/sys/class/net/$WAN" ]; then echo "QuotaGate warning: WAN $WAN not present yet" >&2; fi
ip link set "$LAN" up
ip addr replace "$LANIP/$PREFIX" dev "$LAN"
if [ -w /proc/sys/net/ipv4/ip_forward ]; then echo 1 >/proc/sys/net/ipv4/ip_forward; else sysctl -w net.ipv4.ip_forward=1 >/dev/null; fi
rm -f /run/quotagate-hostapd.pid /run/quotagate-dnsmasq.pid 2>/dev/null || true
printf 'QuotaGate interface ready: %s -> %s (%s/%s)\n' "$WAN" "$LAN" "$LANIP" "$PREFIX"
