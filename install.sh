#!/bin/bash
set -euo pipefail
umask 077
[ "$(id -u)" -eq 0 ] || { echo "Run: sudo ./install.sh"; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
APP_DIR=/opt/quotagate
CFG_DIR=/etc/quotagate
CFG=$CFG_DIR/config.json
DATA_DIR=/var/lib/quotagate
DB=$DATA_DIR/quotagate.db
LOG_DIR=/var/log/quotagate
STAGE=/opt/.quotagate.new

echo "=== QuotaGate antiX 3.0 ==="
echo "Code:   $APP_DIR"
echo "Config: $CFG"
echo "Data:   $DB"
echo "Logs:   $LOG_DIR/"
echo "The QuotaGate service intentionally runs as root because it controls nftables, tc, DHCP, DNS, hostapd and network configuration."
echo

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv dnsmasq nftables iproute2 kmod hostapd iw ca-certificates openssl curl ppp python3-qrcode python3-pil
python3 - <<'PY'
import sys
assert sys.version_info >= (3,10), 'Python 3.10+ required'
PY

service quotagate stop >/dev/null 2>&1 || true

install -d -o root -g root -m 0700 "$CFG_DIR" "$CFG_DIR/backup" "$DATA_DIR" "$DATA_DIR/backup"
install -d -o root -g root -m 0750 "$LOG_DIR"
install -d -o root -g root -m 0755 /etc/hostapd

migrate_file() {
  local src="$1" dst="$2" backup_dir="$3" label="$4"
  [ -f "$src" ] || return 0
  if [ ! -e "$dst" ]; then
    echo "Migrating $label: $src -> $dst"
    mv "$src" "$dst"
  else
    local stamp
    stamp=$(date +%Y%m%d-%H%M%S)
    echo "Persistent $label already exists; preserving legacy copy in $backup_dir"
    mv "$src" "$backup_dir/legacy-${label}-${stamp}"
  fi
}

for old_cfg in \
  "$APP_DIR/config.json" \
  "$APP_DIR/etc/config.json" \
  "$APP_DIR/etc/quotagate/config.json" \
  "$APP_DIR/data/config.json"; do
  migrate_file "$old_cfg" "$CFG" "$CFG_DIR/backup" "config.json"
done

for old_db in \
  "$APP_DIR/quotagate.db" \
  "$APP_DIR/data/quotagate.db" \
  "$APP_DIR/var/lib/quotagate/quotagate.db"; do
  if [ -f "$old_db" ]; then
    migrate_file "$old_db" "$DB" "$DATA_DIR/backup" "quotagate.db"
    for suffix in -wal -shm -journal; do
      [ -f "${old_db}${suffix}" ] || continue
      if [ ! -e "${DB}${suffix}" ]; then
        mv "${old_db}${suffix}" "${DB}${suffix}"
      else
        mv "${old_db}${suffix}" "$DATA_DIR/backup/legacy-quotagate.db${suffix}-$(date +%Y%m%d-%H%M%S)"
      fi
    done
  fi
done

FRESH_CONFIG=0
if [ ! -f "$CFG" ]; then
  cp "$HERE/config.example.json" "$CFG"
  FRESH_CONFIG=1
fi
chmod 600 "$CFG"
[ ! -f "$DB" ] || chmod 600 "$DB"

[ -f /etc/hostapd/hostapd.conf ] && [ ! -f "$CFG_DIR/backup/hostapd.conf" ] && cp -a /etc/hostapd/hostapd.conf "$CFG_DIR/backup/hostapd.conf" || true
[ -f /etc/connman/main.conf ] && [ ! -f "$CFG_DIR/backup/connman-main.conf" ] && cp -a /etc/connman/main.conf "$CFG_DIR/backup/connman-main.conf" || true

rm -rf "$STAGE"
install -d -o root -g root -m 0755 "$STAGE"
cp -a "$HERE/app" "$STAGE/"
cp -a "$HERE/web" "$STAGE/"
python3 -m venv --system-site-packages "$STAGE/venv"
chown -R root:root "$STAGE"
find "$STAGE" -type d -exec chmod go-w {} +
find "$STAGE" -type f -exec chmod go-w {} +
rm -rf "$APP_DIR"
mv "$STAGE" "$APP_DIR"

if [ "$FRESH_CONFIG" -eq 1 ]; then
  read -rp "Wi-Fi SSID [fox3]: " SSID; SSID=${SSID:-fox3}
  while true; do
    read -rsp "Wi-Fi password (8-63 chars): " WIFI_PW; echo
    [ ${#WIFI_PW} -ge 8 ] && [ ${#WIFI_PW} -le 63 ] && break
    echo "Password must be 8..63 characters."
  done
  while true; do
    read -rsp "Dashboard admin password (8+ chars): " ADMIN_PW; echo
    [ ${#ADMIN_PW} -ge 8 ] && break
    echo "Password must be at least 8 characters."
  done
  read -rp "Monthly bundle GB [140]: " BUNDLE; BUNDLE=${BUNDLE:-140}
  read -rp "ISP reset day [1]: " RESETDAY; RESETDAY=${RESETDAY:-1}

  PYTHONPATH="$APP_DIR" "$APP_DIR/venv/bin/python" - "$SSID" "$WIFI_PW" "$ADMIN_PW" "$BUNDLE" "$RESETDAY" <<'PY'
import sys
from app import auth, config
ssid,wifi,admin,bundle,reset=sys.argv[1:]
c=config.load(); c['version']='3.0.0'
c['network'].update(wan_interface='eth0',lan_interface='wlan0',lan_ip='192.168.2.1',lan_prefix=24,client_net='192.168.2.0/24',pool_start='192.168.2.100',pool_end='192.168.2.200',upstream_dns=['1.1.1.1','8.8.8.8'])
c['wifi'].update(enabled=True,ssid=ssid,passphrase=wifi,channel=1,hw_mode='g')
c['web'].update(host='192.168.2.1',port=8080)
c['bundle'].update(total_gb=float(bundle),reset_day=int(reset))
c['admin']['password_hash']=auth.hash_password(admin)
config.save(c)
PY
  unset WIFI_PW ADMIN_PW
else
  echo "Existing configuration preserved: $CFG"
fi
chmod 600 "$CFG"

if [ -f /etc/connman/main.conf ]; then
python3 - <<'PY'
p='/etc/connman/main.conf'; lines=open(p).read().splitlines(); out=[]; done=False
for line in lines:
    raw=line.strip(); norm=raw.lstrip('#').strip()
    if norm.startswith('NetworkInterfaceBlacklist') and not done:
        key,val=norm.split('=',1); vals=[x.strip() for x in val.split(',') if x.strip()]
        if 'wlan0' not in vals: vals.append('wlan0')
        out.append('NetworkInterfaceBlacklist = '+','.join(vals)); done=True
    else: out.append(line)
if not done: out.append('NetworkInterfaceBlacklist = wlan0')
open(p,'w').write('\n'.join(out)+'\n')
PY
service connman restart || true
fi

grep -qxF b43 /etc/modules 2>/dev/null || echo b43 >> /etc/modules
cat >/etc/sysctl.d/99-quotagate.conf <<'SYSCTL'
net.ipv4.ip_forward=1
SYSCTL
sysctl -p /etc/sysctl.d/99-quotagate.conf >/dev/null || true

service dnsmasq stop >/dev/null 2>&1 || true
service hostapd stop >/dev/null 2>&1 || true
update-rc.d dnsmasq disable >/dev/null 2>&1 || true
update-rc.d hostapd disable >/dev/null 2>&1 || true

install -m755 "$HERE/init/quotagate" /etc/init.d/quotagate
install -m755 "$HERE/scripts/network-start.sh" /usr/local/sbin/quotagate-network-start
install -m755 "$HERE/scripts/network-stop.sh" /usr/local/sbin/quotagate-network-stop
install -m755 "$HERE/scripts/diagnose.sh" /usr/local/sbin/quotagate-diagnose
install -m755 "$HERE/scripts/setup-network.sh" /usr/local/sbin/quotagate-setup-network
install -m755 "$HERE/scripts/make-cert.sh" /usr/local/sbin/quotagate-make-cert
install -m755 "$HERE/scripts/pppoe-test.sh" /usr/local/sbin/quotagate-pppoe-test
cat >/etc/logrotate.d/quotagate <<'LOGROTATE'
/var/log/quotagate/*.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
    copytruncate
    create 0640 root root
}
LOGROTATE
chmod 644 /etc/logrotate.d/quotagate
update-rc.d quotagate defaults >/dev/null 2>&1 || true

service quotagate restart
sleep 3
service quotagate status || true

echo
echo "Installed/updated QuotaGate antiX 3.0."
echo "Application code: $APP_DIR"
echo "Persistent config: $CFG"
echo "Persistent database: $DB"
echo "Logs: $LOG_DIR/service.log"
echo "A future deploy may replace $APP_DIR completely without deleting config, database, or logs."
echo "Diagnostic command: sudo quotagate-diagnose"
