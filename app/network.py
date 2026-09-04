from __future__ import annotations
import ipaddress, json, re, shlex, subprocess, time, socket, struct, threading
from pathlib import Path
from . import db

_RULE_SIG=None; _SHAPE_SIG=None

def run(cmd, check=False, input_text=None):
    p=subprocess.run(cmd if isinstance(cmd,list) else shlex.split(cmd),text=True,input=input_text,capture_output=True)
    if check and p.returncode: raise RuntimeError((p.stderr or p.stdout).strip())
    return p

def is_random_mac(mac):
    try: return bool(int(mac.split(':')[0],16)&2)
    except Exception: return False

def manufacturer(mac):
    oui=mac.upper().replace(':','')[:6]
    tiny={'001302':'Intel','0016CE':'Dell','0019D2':'Intel','001E4F':'Dell','00216A':'Intel','001A92':'ASUSTek','F0DCE2':'Apple','3C5A37':'Samsung','A4C361':'Apple','B827EB':'Raspberry Pi'}
    return tiny.get(oui,'')

def neighbors(iface):
    out=[]
    txt=run(['ip','neigh','show','dev',iface]).stdout
    for line in txt.splitlines():
        m=re.match(r'(\d+\.\d+\.\d+\.\d+)\s+.*lladdr\s+([0-9a-fA-F:]{17})',line)
        if m and 'FAILED' not in line: out.append((m.group(2).lower(),m.group(1)))
    try:
        for line in Path('/proc/net/arp').read_text().splitlines()[1:]:
            p=line.split()
            if len(p)>=6 and p[5]==iface and re.fullmatch(r'[0-9a-fA-F:]{17}',p[3]):
                x=(p[3].lower(),p[0]);
                if x not in out: out.append(x)
    except Exception: pass
    return out

def wait_interface(iface,seconds=10):
    for _ in range(seconds*2):
        if Path(f'/sys/class/net/{iface}').exists(): return True
        time.sleep(.5)
    return False

def ensure_lan(c):
    n=c['network']; lan=n['lan_interface']
    if not wait_interface(lan,2): run(['modprobe','b43'])
    if not wait_interface(lan,10): raise RuntimeError(f'LAN interface {lan} not found')
    run(['ip','link','set',lan,'up'],True)
    run(['ip','addr','replace',f"{n['lan_ip']}/{n['lan_prefix']}",'dev',lan],True)
    try: Path('/proc/sys/net/ipv4/ip_forward').write_text('1')
    except Exception: pass

def write_hostapd(c):
    w=c['wifi']; n=c['network']
    pw=str(w['passphrase'])
    if not 8<=len(pw)<=63: raise RuntimeError('Wi-Fi password must be 8..63 chars')
    text=f'''interface={n['lan_interface']}\ndriver=nl80211\nssid={str(w['ssid'])[:32]}\nhw_mode={w.get('hw_mode','g')}\nchannel={int(w.get('channel',1))}\nauth_algs=1\nwpa=2\nwpa_key_mgmt=WPA-PSK\nwpa_pairwise=CCMP\nwpa_passphrase={pw}\n'''
    runtime=Path('/run/quotagate'); runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=runtime/'hostapd.conf'; target.write_text(text); target.chmod(0o600)

def write_dnsmasq(c):
    n=c['network']
    text=f'''# QuotaGate dedicated DHCP only\nport=0\ninterface={n['lan_interface']}\nbind-interfaces\ndhcp-range={n['pool_start']},{n['pool_end']},255.255.255.0,12h\ndhcp-option=3,{n['lan_ip']}\ndhcp-option=6,{n['lan_ip']}\nlog-dhcp\n'''
    runtime=Path('/run/quotagate'); runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=runtime/'dnsmasq.conf'; target.write_text(text); target.chmod(0o600)

def start_network_daemons(c):
    run(['pkill','-f','hostapd.*quotagate|hostapd.*hostapd.conf'])
    run(['pkill','-F','/run/quotagate-dnsmasq.pid'])
    write_hostapd(c); write_dnsmasq(c)
    hp=subprocess.Popen(['hostapd','-B','-P','/run/quotagate-hostapd.pid','/run/quotagate/hostapd.conf'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    time.sleep(.7)
    dm=subprocess.Popen(['dnsmasq','--conf-file=/run/quotagate/dnsmasq.conf','--pid-file=/run/quotagate-dnsmasq.pid'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    time.sleep(.4)
    return hp.returncode, dm.returncode

def stop_network_daemons():
    run(['pkill','-F','/run/quotagate-hostapd.pid']); run(['pkill','-F','/run/quotagate-dnsmasq.pid'])

def _nft(cmd): return run(['nft']+cmd)
def ensure_firewall(c):
    n=c['network']; lan=n['lan_interface']; wan=n['vpn_interface'] if n.get('vpn_share') else n['wan_interface']; net=n['client_net']
    run(['nft','delete','table','ip','quotagate_nat'])
    nat=f'''table ip quotagate_nat {{
 chain prerouting {{ type nat hook prerouting priority dstnat; policy accept; }}
 chain postrouting {{ type nat hook postrouting priority srcnat; policy accept; ip saddr {net} oifname "{wan}" masquerade; }}
}}'''
    p=run(['nft','-f','-'],input_text=nat)
    if p.returncode: raise RuntimeError(p.stderr.strip())
    run(['nft','delete','table','inet','quotagate'])
    filt='''table inet quotagate {
 set blocked_v4 { type ipv4_addr; flags interval; }
 set allowed_v4 { type ipv4_addr; flags interval; }
 chain input { type filter hook input priority filter; policy accept; }
 chain forward { type filter hook forward priority filter; policy drop; }
}'''
    p=run(['nft','-f','-'],input_text=filt)
    if p.returncode: raise RuntimeError(p.stderr.strip())
    rebuild_rules(c,db.devices())

def rebuild_rules(c,devices):
    n=c['network']; lan=n['lan_interface']; wan=n['vpn_interface'] if n.get('vpn_share') else n['wan_interface']; net=n['client_net']
    run(['nft','flush','chain','inet','quotagate','forward'])
    for d in devices:
        ip=d.get('ip','')
        try: ipaddress.ip_address(ip)
        except Exception: continue
        uplink=n.get('uplink_net','192.168.1.0/24')
        run(['nft','add','rule','inet','quotagate','forward','ip','saddr',ip,'ip','daddr','!=',net,'ip','daddr','!=',uplink,'counter','comment',f"qg:{d['id']}:up"])
        run(['nft','add','rule','inet','quotagate','forward','ip','daddr',ip,'ip','saddr','!=',net,'ip','saddr','!=',uplink,'counter','comment',f"qg:{d['id']}:down"])
    run(['nft','add','rule','inet','quotagate','forward','ip','saddr','@blocked_v4','drop'])
    run(['nft','add','rule','inet','quotagate','forward','ip','daddr','@blocked_v4','drop'])
    for r in db.firewall_rules():
        if not r['enabled'] or r['direction']!='forward': continue
        args=['nft','add','rule','inet','quotagate','forward']
        if r['src']: args += ['ip','saddr',r['src']]
        if r['dst']: args += ['ip','daddr',r['dst']]
        if r['proto'] in ('tcp','udp'):
            args += ['ip','protocol',r['proto']]
            if r['sport']: args += [r['proto'],'sport',str(r['sport'])]
            if r['dport']: args += [r['proto'],'dport',str(r['dport'])]
        args += [r['action'] if r['action'] in ('accept','drop','reject') else 'drop']
        run(args)
    run(['nft','add','rule','inet','quotagate','forward','ct','state','established,related','accept'])
    run(['nft','add','rule','inet','quotagate','forward','iifname',lan,'oifname',wan,'ip','saddr',net,'accept'])
    run(['nft','flush','chain','ip','quotagate_nat','prerouting'])
    for r in db.port_forwards():
        if not r['enabled']: continue
        run(['nft','add','rule','ip','quotagate_nat','prerouting',r['proto'],'dport',str(r['external_port']),'dnat','to',f"{r['internal_ip']}:{r['internal_port']}"])
    if n.get('dmz_ip'):
        try:
            ipaddress.ip_address(n['dmz_ip'])
            run(['nft','add','rule','ip','quotagate_nat','prerouting','dnat','to',n['dmz_ip']])
        except Exception: pass

def sync_rules(c,devices,blocked_ips):
    global _RULE_SIG
    sig=(tuple(sorted((d['id'],d.get('ip','')) for d in devices)),tuple((r['id'],r['enabled']) for r in db.firewall_rules()),tuple((r['id'],r['enabled']) for r in db.port_forwards()),c['network'].get('dmz_ip',''),c['network'].get('vpn_share',False))
    rebuilt=False
    if sig!=_RULE_SIG or run(['nft','list','table','inet','quotagate']).returncode:
        ensure_firewall(c); _RULE_SIG=sig; rebuilt=True
    run(['nft','flush','set','inet','quotagate','blocked_v4'])
    for ip in sorted(set(blocked_ips)):
        try: ipaddress.ip_address(ip); run(['nft','add','element','inet','quotagate','blocked_v4',f'{{ {ip} }}'])
        except Exception: pass
    return rebuilt

def counters():
    p=run(['nft','-j','list','chain','inet','quotagate','forward'])
    if p.returncode:return {}
    try:j=json.loads(p.stdout)
    except Exception:return {}
    out={}
    for x in j.get('nftables',[]):
        r=x.get('rule');
        if not r:continue
        m=re.fullmatch(r'qg:(\d+):(up|down)',r.get('comment',''))
        if not m:continue
        b=0
        for e in r.get('expr',[]):
            if 'counter' in e:b=int(e['counter'].get('bytes',0))
        out[(int(m.group(1)),m.group(2))]=b
    return out

def effective_quota(user,users,c):
    if not user:return 0.0
    if user.get('quota_mode')=='shared':
        fixed=sum(float(x.get('quota_gb') or 0)+float(x.get('topup_gb') or 0) for x in users if x.get('quota_mode')!='shared' and x.get('enabled'))
        shared=[x for x in users if x.get('quota_mode')=='shared' and x.get('enabled')]
        base=max(0.0,float(c['bundle']['total_gb'])-fixed)/max(1,len(shared))
        return base+float(user.get('topup_gb') or 0)
    return float(user.get('quota_gb') or 0)+float(user.get('topup_gb') or 0)

def blocked(c,users,devices):
    um={u['id']:u for u in users}; out=[]; rules={r['mac']:r['action'] for r in db.mac_rules()}
    for d in devices:
        ip=d.get('ip',''); mac=d.get('mac','').lower(); u=um.get(d.get('user_id'))
        action=rules.get(mac)
        b=bool(d.get('blocked_manual')) or not bool(d.get('enabled'))
        if u and not d.get('exempt') and not u.get('exempt'):
            q=effective_quota(u,users,c)
            if q>0 and int(u.get('usage_bytes') or 0)>=int(q*1024**3): b=True
            if not u.get('enabled'): b=True
        if action=='whitelist': b=False
        if action=='blacklist': b=True
        if b and ip: out.append(ip)
    return out

def _shape_one(iface,direction,devices,users,c):
    run(['tc','qdisc','del','dev',iface,'root'])
    um={u['id']:u for u in users}; key='speed_down_kbit' if direction=='down' else 'speed_up_kbit'; limited=[]
    for d in devices:
        u=um.get(d.get('user_id')) or {}; rate=int(d.get(key) or u.get(key) or 0)
        if rate>0 and d.get('ip'): limited.append((d['ip'],rate))
    if not limited:return
    total=int(float(c['network']['line_down_mbit' if direction=='down' else 'line_up_mbit'])*1000)
    total=max(total,max(r for _,r in limited))
    if run(['tc','qdisc','add','dev',iface,'root','handle','1:','htb','default','999']).returncode:return
    run(['tc','class','add','dev',iface,'parent','1:','classid','1:999','htb','rate',f'{total}kbit','ceil',f'{total}kbit'])
    run(['tc','qdisc','add','dev',iface,'parent','1:999','fq_codel'])
    cls=10
    for ip,rate in limited:
        cid=f'1:{cls}'; run(['tc','class','add','dev',iface,'parent','1:','classid',cid,'htb','rate',f'{rate}kbit','ceil',f'{rate}kbit'])
        field='dst' if direction=='down' else 'src'
        run(['tc','filter','add','dev',iface,'protocol','ip','parent','1:','prio','1','u32','match','ip',field,ip,'flowid',cid])
        run(['tc','qdisc','add','dev',iface,'parent',cid,'fq_codel']); cls+=1

def shaping(c,devices,users):
    global _SHAPE_SIG
    n=c['network']; sig=(tuple((d['id'],d.get('ip'),d.get('speed_down_kbit'),d.get('speed_up_kbit')) for d in devices),tuple((u['id'],u.get('speed_down_kbit'),u.get('speed_up_kbit')) for u in users),n.get('line_down_mbit'),n.get('line_up_mbit'))
    if sig==_SHAPE_SIG:return False
    _shape_one(n['lan_interface'],'down',devices,users,c); _shape_one(n['wan_interface'],'up',devices,users,c); _SHAPE_SIG=sig; return True

def runtime(c):
    ensure_lan(c); write_hostapd(c); write_dnsmasq(c); ensure_firewall(c)
