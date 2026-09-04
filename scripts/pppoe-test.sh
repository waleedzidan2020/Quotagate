#!/bin/sh
set -eu
CFG=/etc/quotagate/config.json
USER=$(python3 -c 'import json;print(json.load(open("/etc/quotagate/config.json"))["wan"].get("pppoe_user",""))')
[ -n "$USER" ] || { echo 'PPPoE username is not configured.'; exit 1; }
command -v pppd >/dev/null || { echo 'ppp is not installed. Install with: sudo apt install ppp'; exit 1; }
echo 'PPPoE strong mode is intentionally not auto-applied. Test credentials only from a local terminal before bridging the router.'
echo "Configured PPPoE user: $USER"
