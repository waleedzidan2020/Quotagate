#!/bin/bash
set -euo pipefail
umask 077
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo/root."; exit 1; }

REPO="${QUOTAGATE_REPO:-https://github.com/waleedzidan2020/Quotagate.git}"
BRANCH="${QUOTAGATE_BRANCH:-main}"
APP=/opt/quotagate
STATE=/var/lib/quotagate/installed_commit
BACKUPS=/var/lib/quotagate/update-backups
TMP=$(mktemp -d /tmp/quotagate-update.XXXXXX)
MODE="${1:-update}"
HEALTH="http://192.168.2.1:8080/api/health"
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$BACKUPS/$STAMP"
trap 'rm -rf "$TMP"' EXIT

command -v git >/dev/null || { echo "git is required"; exit 1; }
command -v curl >/dev/null || { echo "curl is required"; exit 1; }

REMOTE=$(git ls-remote "$REPO" "refs/heads/$BRANCH" | awk 'NR==1{print $1}')
[ -n "$REMOTE" ] || { echo "Could not resolve remote commit."; exit 1; }
LOCAL=$(cat "$STATE" 2>/dev/null || true)

echo "Installed: ${LOCAL:-unknown}"
echo "Remote:    $REMOTE"
if [ "$REMOTE" = "$LOCAL" ]; then echo "QuotaGate is up to date."; exit 0; fi
if [ "$MODE" = "--check" ] || [ "$MODE" = "check" ]; then echo "Update available."; exit 10; fi
[ "$MODE" = "update" ] || { echo "Usage: quotagate-update [--check]"; exit 2; }

git clone --depth 1 --branch "$BRANCH" "$REPO" "$TMP/repo" >/dev/null 2>&1
SRC="$TMP/repo"
for p in app web init/quotagate scripts/network-start.sh scripts/network-stop.sh scripts/diagnose.sh scripts/setup-network.sh scripts/make-cert.sh scripts/pppoe-test.sh scripts/update.sh; do
  [ -e "$SRC/$p" ] || { echo "Missing required update file: $p"; exit 1; }
done
python3 -m compileall -q "$SRC/app"

install -d -m0700 "$BACKUPS" "$BACKUP"
[ -d "$APP" ] && cp -a "$APP" "$BACKUP/app"
[ -f /etc/init.d/quotagate ] && cp -a /etc/init.d/quotagate "$BACKUP/quotagate.init"
for f in /usr/local/sbin/quotagate-network-start /usr/local/sbin/quotagate-network-stop /usr/local/sbin/quotagate-diagnose /usr/local/sbin/quotagate-setup-network /usr/local/sbin/quotagate-make-cert /usr/local/sbin/quotagate-pppoe-test /usr/local/sbin/quotagate-update; do
  [ -f "$f" ] && cp -a "$f" "$BACKUP/$(basename "$f")"
done

service quotagate stop >/dev/null 2>&1 || true
NEW=/opt/.quotagate-update-new
rm -rf "$NEW"
install -d -m0755 "$NEW"
cp -a "$SRC/app" "$SRC/web" "$NEW/"
if [ -d "$APP/venv" ]; then cp -a "$APP/venv" "$NEW/venv"; else python3 -m venv --system-site-packages "$NEW/venv"; fi
chown -R root:root "$NEW"
find "$NEW" -type d -exec chmod go-w {} +
find "$NEW" -type f -exec chmod go-w {} +
rm -rf "$APP"
mv "$NEW" "$APP"

install -m755 "$SRC/init/quotagate" /etc/init.d/quotagate
install -m755 "$SRC/scripts/network-start.sh" /usr/local/sbin/quotagate-network-start
install -m755 "$SRC/scripts/network-stop.sh" /usr/local/sbin/quotagate-network-stop
install -m755 "$SRC/scripts/diagnose.sh" /usr/local/sbin/quotagate-diagnose
install -m755 "$SRC/scripts/setup-network.sh" /usr/local/sbin/quotagate-setup-network
install -m755 "$SRC/scripts/make-cert.sh" /usr/local/sbin/quotagate-make-cert
install -m755 "$SRC/scripts/pppoe-test.sh" /usr/local/sbin/quotagate-pppoe-test
install -m755 "$SRC/scripts/update.sh" /usr/local/sbin/quotagate-update
update-rc.d quotagate defaults >/dev/null 2>&1 || true

service quotagate start
OK=0
for _ in 1 2 3 4 5 6 7 8; do
  sleep 1
  if curl -fsS --max-time 2 "$HEALTH" 2>/dev/null | grep -Eq '"ok"[[:space:]]*:[[:space:]]*true'; then OK=1; break; fi
done
if [ "$OK" -eq 1 ]; then
  printf '%s\n' "$REMOTE" >"$STATE"; chmod 600 "$STATE"
  echo "Update successful: $REMOTE"
  exit 0
fi

echo "Health check failed; rolling back."
service quotagate stop >/dev/null 2>&1 || true
rm -rf "$APP"
[ -d "$BACKUP/app" ] && cp -a "$BACKUP/app" "$APP"
[ -f "$BACKUP/quotagate.init" ] && cp -a "$BACKUP/quotagate.init" /etc/init.d/quotagate
for f in quotagate-network-start quotagate-network-stop quotagate-diagnose quotagate-setup-network quotagate-make-cert quotagate-pppoe-test quotagate-update; do
  [ -f "$BACKUP/$f" ] && cp -a "$BACKUP/$f" "/usr/local/sbin/$f"
done
service quotagate start >/dev/null 2>&1 || true
exit 1
