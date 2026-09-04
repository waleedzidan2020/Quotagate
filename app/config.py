from __future__ import annotations
import json, os
from pathlib import Path
CFG=Path('/etc/quotagate/config.json')
DEFAULT={
 'version':'3.0.0',
 'network':{
   'wan_interface':'eth0','lan_interface':'wlan0','lan_ip':'192.168.2.1','lan_prefix':24,'client_net':'192.168.2.0/24','uplink_net':'192.168.1.0/24',
   'pool_start':'192.168.2.100','pool_end':'192.168.2.200','upstream_dns':['1.1.1.1','8.8.8.8'],
   'line_down_mbit':12.0,'line_up_mbit':1.5,'vpn_share':False,'vpn_interface':'tun0','dmz_ip':'',
   'stop_new_connections':False,'decline_random_macs':False,'default_new_enabled':False
 },
 'wifi':{'enabled':True,'ssid':'QuotaGate','passphrase':'','channel':1,'hw_mode':'g','hidden':False},
 'web':{'host':'192.168.2.1','port':8080,'https':False,'cert':'/etc/quotagate/tls.crt','key':'/etc/quotagate/tls.key'},
 'bundle':{'total_gb':140.0,'reset_day':1,'bundle_type':'renew_day','last_reset_period':''},
 'guest':{'enabled':False,'quota_gb':0.5,'speed_down_kbit':1024,'speed_up_kbit':256,'max_devices':10},
 'security':{'auto_ban':True,'failed_login_limit':8,'failed_login_window':300,'ban_seconds':900},
 'features':{'speed_limits':True,'dns_proxy':True,'dns_history':True,'firewall':True,'pppoe_wan':False},
 'wan':{'mode':'lan','pppoe_user':'','pppoe_password':'','interface':'ppp0','auto_renew_minutes':0},
 'admin':{'password_hash':'','totp_enabled':False,'totp_secret':''}
}
def merge(a,b):
    out=dict(a)
    for k,v in b.items():
        if isinstance(v,dict) and isinstance(out.get(k),dict): out[k]=merge(out[k],v)
        else: out[k]=v
    return out

def load():
    if not CFG.exists():
        CFG.parent.mkdir(parents=True,exist_ok=True,mode=0o700); os.chmod(CFG.parent,0o700); save(DEFAULT); return json.loads(json.dumps(DEFAULT))
    try: data=json.loads(CFG.read_text())
    except Exception: data={}
    return merge(DEFAULT,data)

def save(c):
    CFG.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.chmod(CFG.parent,0o700)
    tmp=CFG.with_suffix('.tmp'); tmp.write_text(json.dumps(c,indent=2,ensure_ascii=False)+'\n'); os.chmod(tmp,0o600); tmp.replace(CFG)
