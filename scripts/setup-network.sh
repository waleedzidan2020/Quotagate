#!/bin/bash
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo"; exit 1; }
CFG=/etc/quotagate/config.json
[ -f "$CFG" ] || { echo "Install QuotaGate first."; exit 1; }
echo "Current QuotaGate network:"
python3 - <<'PY'
import json
c=json.load(open('/etc/quotagate/config.json'))
print('WAN :',c['network']['wan_interface'],'(router DHCP)')
print('LAN :',c['network']['lan_interface'],c['network']['lan_ip'])
print('SSID:',c['wifi']['ssid'])
print('Web : http://%s:%s'%(c['web']['host'],c['web']['port']))
PY
read -rp "WAN interface [eth0]: " WAN; WAN=${WAN:-eth0}
read -rp "LAN interface [wlan0]: " LAN; LAN=${LAN:-wlan0}
read -rp "LAN IP [192.168.2.1]: " LIP; LIP=${LIP:-192.168.2.1}
python3 - "$WAN" "$LAN" "$LIP" <<'PY'
import ipaddress,json,sys
wan,lan,lip=sys.argv[1:];ipaddress.ip_address(lip)
p='/etc/quotagate/config.json';c=json.load(open(p));n=c['network']
n['wan_interface']=wan;n['lan_interface']=lan;n['lan_ip']=lip;n['client_net']='.'.join(lip.split('.')[:3])+'.0/24';n['pool_start']='.'.join(lip.split('.')[:3])+'.100';n['pool_end']='.'.join(lip.split('.')[:3])+'.200';c['web']['host']=lip
json.dump(c,open(p,'w'),indent=2)
PY
chmod 600 "$CFG"
service quotagate restart
