#!/bin/sh
echo '=== QuotaGate 3.1 diagnose ==='
id
echo '-- service --'; service quotagate status 2>&1 || true
echo '-- links --'; ip -br addr 2>&1 || true
echo '-- route --'; ip route 2>&1 || true
echo '-- forwarding --'; cat /proc/sys/net/ipv4/ip_forward 2>&1 || true
echo '-- wifi info --'; iw dev wlan0 info 2>&1 || true
echo '-- wifi stations --'; iw dev wlan0 station dump 2>&1 || true
echo '-- hostapd --'; [ -f /run/quotagate-hostapd.pid ] && ps -p "$(cat /run/quotagate-hostapd.pid)" -o pid,cmd || true
echo '-- dedicated dnsmasq --'; [ -f /run/quotagate-dnsmasq.pid ] && ps -p "$(cat /run/quotagate-dnsmasq.pid)" -o pid,cmd || true
echo '-- ports --'; ss -lntup 2>&1 | grep -E ':(53|8080)\b' || true
echo '-- dashboard --'; curl -s --max-time 2 http://192.168.2.1:8080/api/health 2>/dev/null || true; echo
echo '-- gateway ping --'; GW=$(ip route show default 2>/dev/null | awk '/default via/{print $3;exit}'); [ -n "$GW" ] && ping -c 5 "$GW" 2>&1 || true
echo '-- internet ping --'; ping -c 5 1.1.1.1 2>&1 || true
echo '-- nft --'; nft list table ip quotagate_nat 2>&1 || true; nft list table inet quotagate 2>&1 || true
echo '-- tc --'; tc -s qdisc show dev wlan0 2>&1 || true; tc -s qdisc show dev eth0 2>&1 || true; tc class show dev wlan0 2>&1 || true; tc class show dev eth0 2>&1 || true
echo '-- runtime --'; ls -la /run/quotagate 2>&1 || true
echo '-- b43 firmware --'; dmesg 2>/dev/null | grep -i 'firmware version' | tail -3 || true
echo '-- update state --'; cat /var/lib/quotagate/installed_commit 2>/dev/null || echo unknown
echo '-- log --'; tail -100 /var/log/quotagate/service.log 2>&1 || true
