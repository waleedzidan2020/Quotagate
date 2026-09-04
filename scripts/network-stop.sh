#!/bin/sh
set +e
[ -f /run/quotagate-hostapd.pid ] && kill "$(cat /run/quotagate-hostapd.pid)" 2>/dev/null
[ -f /run/quotagate-dnsmasq.pid ] && kill "$(cat /run/quotagate-dnsmasq.pid)" 2>/dev/null
rm -f /run/quotagate-hostapd.pid /run/quotagate-dnsmasq.pid
nft delete table ip quotagate_nat 2>/dev/null
nft delete table inet quotagate 2>/dev/null
exit 0
