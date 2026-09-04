#!/bin/sh
echo '=== QuotaGate 3.0 diagnose ==='
id
echo '-- service --'; service quotagate status 2>&1 || true
echo '-- links --'; ip -br addr 2>&1 || true
echo '-- route --'; ip route 2>&1 || true
echo '-- forwarding --'; cat /proc/sys/net/ipv4/ip_forward 2>&1 || true
echo '-- hostapd --'; [ -f /run/quotagate-hostapd.pid ] && ps -p "$(cat /run/quotagate-hostapd.pid)" -o pid,cmd || true
echo '-- dedicated dnsmasq --'; [ -f /run/quotagate-dnsmasq.pid ] && ps -p "$(cat /run/quotagate-dnsmasq.pid)" -o pid,cmd || true
echo '-- ports --'; ss -lntup 2>&1 | grep -E ':(53|8080)\b' || true
echo '-- dashboard --'; curl -s --max-time 2 http://192.168.2.1:8080/api/health 2>/dev/null || true; echo
echo '-- nft --'; nft list table ip quotagate_nat 2>&1 || true; nft list table inet quotagate 2>&1 || true
echo '-- tc --'; tc qdisc show dev wlan0 2>&1 || true; tc qdisc show dev eth0 2>&1 || true
echo '-- b43 firmware --'; dmesg 2>/dev/null | grep -i 'firmware version' | tail -3 || true
echo '-- log --'; tail -100 /var/log/quotagate/service.log 2>&1 || true
