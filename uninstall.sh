#!/bin/bash
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run: sudo ./uninstall.sh"; exit 1; }
service quotagate stop >/dev/null 2>&1 || true
update-rc.d -f quotagate remove >/dev/null 2>&1 || true
rm -f /etc/init.d/quotagate /usr/local/sbin/quotagate-network-start /usr/local/sbin/quotagate-network-stop /usr/local/sbin/quotagate-diagnose /usr/local/sbin/quotagate-setup-network /usr/local/sbin/quotagate-make-cert /usr/local/sbin/quotagate-pppoe-test /usr/local/sbin/quotagate-update
rm -rf /opt/quotagate
rm -f /etc/sysctl.d/99-quotagate.conf
nft delete table ip quotagate_nat 2>/dev/null || true
nft delete table inet quotagate 2>/dev/null || true
if [ -f /etc/quotagate/backup/connman-main.conf ]; then cp -a /etc/quotagate/backup/connman-main.conf /etc/connman/main.conf; service connman restart || true; fi
if [ -f /etc/quotagate/backup/hostapd.conf ]; then cp -a /etc/quotagate/backup/hostapd.conf /etc/hostapd/hostapd.conf; fi
echo "QuotaGate removed. Persistent config/data/update backups under /etc/quotagate and /var/lib/quotagate were kept intentionally."
echo "Broadcom firmware under /lib/firmware/b43 was NOT modified."
