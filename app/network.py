from __future__ import annotations
import ipaddress, json, re, shlex, subprocess, time
from pathlib import Path
from . import db

_RULE_SIG=None; _SHAPE_SIG=None; _BLOCK_SIG=None

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

def leases():
    out=[]
    for path in ('/var/lib/misc/dnsmasq.leases','/var/lib/quotagate/dnsmasq.leases','/run/quotagate/dnsmasq.leases'):
        p=Path(path)
        if not p.exists(): continue
        try:
            for line in p.read_text(errors='ignore').splitlines():
                x=line.split()
                if len(x)>=4 and re.fullmatch(r'[0-9a-fA-F:]{17}',x[1]):
                    out.append({'mac':x[1].lower(),'ip':x[2],'name':'' if x[3]=='*' else x[3]})
        except Exception: pass
    uniq={}
    for x in out: uniq[x['mac']]=x
    return list(uniq.values())

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
                x=(p[3].lower(),p[0])
                if x not in out: out.append(x)
    except Exception: pass
    return out

def discover(iface):
    by_mac={x['mac']:dict(x) for x in leases()}
    for mac,ip in neighbors(iface):
        by_mac.setdefault(mac,{'mac':mac,'ip':ip,'name':''})['ip']=ip
    return list(by_mac.values())

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

def validate_wifi_settings(w):
    ssid=str(w.get('ssid',''))
    if not ssid: raise ValueError('Wi-Fi SSID must not be empty')
    if any(ord(ch)<32 or ord(ch)==127 for ch in ssid): raise ValueError('Wi-Fi SSID contains unsupported control characters')
    if len(ssid.encode('utf-8'))>32: raise ValueError('Wi-Fi SSID must be at most 32 bytes')
    pw=str(w.get('passphrase',''))
    if not 8<=len(pw.encode('utf-8'))<=63: raise ValueError('Wi-Fi password must be 8..63 bytes')
    if any(ch in '\r\n' for ch in pw): raise ValueError('Wi-Fi password contains unsupported characters')
    channel=int(w.get('channel',1))
    if channel<1 or channel>13: raise ValueError('Wi-Fi channel must be 1..13')
    mode=str(w.get('hw_mode','g'))
    if mode not in ('b','g'): raise ValueError('This antiX/b43 profile supports hw_mode b or g')
    return ssid,pw,bool(w.get('hidden',False))

def write_hostapd(c):
    w=c['wifi']; n=c['network']; ssid,pw,hidden=validate_wifi_settings(w)
    text=(f"interface={n['lan_interface']}\n" "driver=nl80211\n" f"ssid={ssid}\n" f"hw_mode={w.get('hw_mode','g')}\n" f"channel={int(w.get('channel',1))}\n"
          "auth_algs=1\n" "wpa=2\n" "wpa_key_mgmt=WPA-PSK\n" "wpa_pairwise=CCMP\n" f"wpa_passphrase={pw}\n" f"ignore_broadcast_ssid={1 if hidden else 0}\n")
    runtime=Path('/run/quotagate'); runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=runtime/'hostapd.conf'; target.write_text(text); target.chmod(0o600)

def write_dnsmasq(c):
    n=c['network']
    text=f'''# QuotaGate dedicated DHCP only\nport=0\ninterface={n['lan_interface']}\nbind-interfaces\ndhcp-range={n['pool_start']},{n['pool_end']},255.255.255.0,12h\ndhcp-option=3,{n['lan_ip']}\ndhcp-option=6,{n['lan_ip']}\ndhcp-leasefile=/run/quotagate/dnsmasq.leases\nlog-dhcp\n'''
    runtime=Path('/run/quotagate'); runtime.mkdir(parents=True,exist_ok=True,mode=0o700)
    target=runtime/'dnsmasq.conf'; target.write_text(text); target.chmod(0o600)

def start_network_daemons(c):
    run(['pkill','-f','hostapd.*quotagate|hostapd.*hostapd.conf']); run(['pkill','-F','/run/quotagate-dnsmasq.pid'])
    write_hostapd(c); write_dnsmasq(c)
    hp=run(['hostapd','-B','-P','/run/quotagate-hostapd.pid','/run/quotagate/hostapd.conf'])
    if hp.returncode: raise RuntimeError((hp.stderr or hp.stdout or 'hostapd failed').strip())
    dm=run(['dnsmasq','--conf-file=/run/quotagate/dnsmasq.conf','--pid-file=/run/quotagate-dnsmasq.pid'])
    if dm.returncode: raise RuntimeError((dm.stderr or dm.stdout or 'dnsmasq failed').strip())
    return 0,0

def restart_wifi(c):
    ensure_lan(c); write_hostapd(c)
    run(['pkill','-F','/run/quotagate-hostapd.pid']); run(['pkill','-f','hostapd.*quotagate|hostapd.*hostapd.conf'])
    p=run(['hostapd','-B','-P','/run/quotagate-hostapd.pid','/run/quotagate/hostapd.conf'])
    if p.returncode: raise RuntimeError((p.stderr or p.stdout or 'hostapd failed to restart').strip())
    time.sleep(.6)
    if not Path('/run/quotagate-hostapd.pid').exists(): raise RuntimeError('hostapd restart did not create its pid file')
    return True

def stop_network_daemons():
    run(['pkill','-F','/run/quotagate-hostapd.pid']); run(['pkill','-F','/run/quotagate-dnsmasq.pid'])

def detect_vpn_interface():
    p=run(['ip','-o','link','show'])
    if p.returncode:return ''
    names=[]
    for line in p.stdout.splitlines():
        m=re.match(r'\d+:\s+([^:@]+)',line)
        if m and re.match(r'^(tun|tap|wg)\w*$',m.group(1)): names.append(m.group(1))
    return sorted(names)[0] if names else ''

def wan_interface(c):
    n=c['network']
    if not n.get('vpn_share'): return n['wan_interface']
    wanted=str(n.get('vpn_interface','tun0'))
    if Path('/sys/class/net') .joinpath(wanted).exists(): return wanted
    if n.get('vpn_auto_detect',True):
        found=detect_vpn_interface()
        if found:return found
    return wanted

def ensure_firewall(c):
    n=c['network']; lan=n['lan_interface']; wan=wan_interface(c); net=n['client_net']
    if not Path('/sys/class/net').joinpath(wan).exists(): raise RuntimeError(f'WAN/VPN interface {wan} not found')
    run(['nft','delete','table','ip','quotagate_nat'])
    nat=f'''table ip quotagate_nat {{\n chain prerouting {{ type nat hook prerouting priority dstnat; policy accept; }}\n chain postrouting {{ type nat hook postrouting priority srcnat; policy accept; ip saddr {net} oifname "{wan}" masquerade; }}\n}}'''
    p=run(['nft','-f','-'],input_text=nat)
    if p.returncode: raise RuntimeError(p.stderr.strip())
    run(['nft','delete','table','inet','quotagate'])
    filt='''table inet quotagate {\n set blocked_v4 { type ipv4_addr; flags interval; }\n chain input { type filter hook input priority filter; policy accept; }\n chain forward { type filter hook forward priority filter; policy drop; }\n}'''
    p=run(['nft','-f','-'],input_text=filt)
    if p.returncode: raise RuntimeError(p.stderr.strip())
    rebuild_rules(c,db.devices())

def _valid_ip_or_net(v):
    if not v:return True
    try: ipaddress.ip_network(v,strict=False); return True
    except Exception:return False

def _valid_port(v):
    if v in ('',None):return True
    try:return 1<=int(v)<=65535
    except Exception:return False

def rebuild_rules(c,devices):
    n=c['network']; lan=n['lan_interface']; wan=wan_interface(c); net=n['client_net']; uplink=n.get('uplink_net','192.168.1.0/24')
    run(['nft','flush','chain','inet','quotagate','forward'])
    for d in devices:
        ip=d.get('ip','')
        try: ipaddress.ip_address(ip)
        except Exception: continue
        run(['nft','add','rule','inet','quotagate','forward','ip','saddr',ip,'ip','daddr','!=',net,'ip','daddr','!=',uplink,'counter','comment',f"qg:{d['id']}:up"])
        run(['nft','add','rule','inet','quotagate','forward','ip','daddr',ip,'ip','saddr','!=',net,'ip','saddr','!=',uplink,'counter','comment',f"qg:{d['id']}:down"])
    run(['nft','add','rule','inet','quotagate','forward','ip','saddr','@blocked_v4','drop']); run(['nft','add','rule','inet','quotagate','forward','ip','daddr','@blocked_v4','drop'])
    for r in db.firewall_rules():
        if not r['enabled'] or r['direction']!='forward': continue
        if not _valid_ip_or_net(r['src']) or not _valid_ip_or_net(r['dst']) or not _valid_port(r['sport']) or not _valid_port(r['dport']): continue
        args=['nft','add','rule','inet','quotagate','forward']
        if r['src']: args += ['ip','saddr',r['src']]
        if r['dst']: args += ['ip','daddr',r['dst']]
        if r['proto'] in ('tcp','udp'):
            args += ['ip','protocol',r['proto']]
            if r['sport']: args += [r['proto'],'sport',str(r['sport'])]
            if r['dport']: args += [r['proto'],'dport',str(r['dport'])]
        args += [r['action'] if r['action'] in ('accept','drop','reject') else 'drop']; run(args)
    run(['nft','add','rule','inet','quotagate','forward','ct','state','established,related','accept'])
    run(['nft','add','rule','inet','quotagate','forward','iifname',lan,'oifname',wan,'ip','saddr',net,'accept'])
    run(['nft','flush','chain','ip','quotagate_nat','prerouting'])
    for r in db.port_forwards():
        if not r['enabled']: continue
        try:
            ipaddress.ip_address(r['internal_ip']); ep=int(r['external_port']); ipt=int(r['internal_port'])
            if not(1<=ep<=65535 and 1<=ipt<=65535):continue
        except Exception:continue
        if r['proto'] not in ('tcp','udp'):continue
        run(['nft','add','rule','ip','quotagate_nat','prerouting',r['proto'],'dport',str(ep),'dnat','to',f"{r['internal_ip']}:{ipt}"])
    if n.get('dmz_ip'):
        try: ipaddress.ip_address(n['dmz_ip']); run(['nft','add','rule','ip','quotagate_nat','prerouting','dnat','to',n['dmz_ip']])
        except Exception: pass

def sync_rules(c,devices,blocked_ips):
    global _RULE_SIG,_BLOCK_SIG
    fw=tuple(tuple(r.get(k) for k in ('id','name','direction','src','dst','proto','sport','dport','action','enabled','priority')) for r in db.firewall_rules())
    pf=tuple(tuple(r.get(k) for k in ('id','name','proto','external_port','internal_ip','internal_port','enabled')) for r in db.port_forwards())
    sig=(tuple(sorted((d['id'],d.get('ip','')) for d in devices)),fw,pf,c['network'].get('dmz_ip',''),c['network'].get('vpn_share',False),wan_interface(c))
    rebuilt=False
    if sig!=_RULE_SIG or run(['nft','list','table','inet','quotagate']).returncode:
        ensure_firewall(c); _RULE_SIG=sig; _BLOCK_SIG=None; rebuilt=True
    bsig=tuple(sorted(set(blocked_ips)))
    if bsig!=_BLOCK_SIG:
        run(['nft','flush','set','inet','quotagate','blocked_v4'])
        for ip in bsig:
            try: ipaddress.ip_address(ip); run(['nft','add','element','inet','quotagate','blocked_v4',f'{{ {ip} }}'])
            except Exception: pass
        _BLOCK_SIG=bsig
    return rebuilt

def counters():
    p=run(['nft','-j','list','chain','inet','quotagate','forward'])
    if p.returncode:return {}
    try:j=json.loads(p.stdout)
    except Exception:return {}
    out={}
    for x in j.get('nftables',[]):
        r=x.get('rule')
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
    mode=str(user.get('quota_mode','fixed')).lower(); mode='auto' if mode=='shared' else mode
    if mode=='disabled': return 0.0
    if mode=='auto':
        fixed=sum(float(x.get('quota_gb') or 0)+float(x.get('topup_gb') or 0) for x in users if str(x.get('quota_mode','fixed')).lower() not in ('shared','auto','disabled') and x.get('enabled'))
        auto=[x for x in users if str(x.get('quota_mode','')).lower() in ('shared','auto') and x.get('enabled')]
        base=max(0.0,float(c['bundle']['total_gb'])-fixed)/max(1,len(auto))
        return base+float(user.get('topup_gb') or 0)
    return max(0.0,float(user.get('quota_gb') or 0)+float(user.get('topup_gb') or 0))

def blocked(c,users,devices):
    um={u['id']:u for u in users}; out=[]; rules={r['mac']:r['action'] for r in db.mac_rules()}
    for d in devices:
        ip=d.get('ip',''); mac=d.get('mac','').lower(); u=um.get(d.get('user_id')); action=rules.get(mac)
        b=bool(d.get('blocked_manual')) or not bool(d.get('enabled'))
        if u:
            mode=str(u.get('quota_mode','fixed')).lower()
            if mode=='disabled' or not u.get('enabled'): b=True
            if not d.get('exempt') and not u.get('exempt') and mode!='disabled':
                q=effective_quota(u,users,c)
                if q>0 and int(u.get('usage_bytes') or 0)>=int(q*1024**3): b=True
        if action=='whitelist': b=False
        if action=='blacklist': b=True
        if bool(d.get('blocked_manual')): b=True
        if b and ip: out.append(ip)
    return out

def clear_shaping(c):
    global _SHAPE_SIG
    n=c['network']
    for iface in {n.get('lan_interface','wlan0'),n.get('wan_interface','eth0'),wan_interface(c)}:
        if iface: run(['tc','qdisc','del','dev',iface,'root'])
    _SHAPE_SIG=None

def _shape_one(iface,direction,devices,users,c):
    run(['tc','qdisc','del','dev',iface,'root'])
    um={u['id']:u for u in users}; key='speed_down_kbit' if direction=='down' else 'speed_up_kbit'; limited=[]
    for d in devices:
        u=um.get(d.get('user_id')) or {}; rate=int(d.get(key) or u.get(key) or 0)
        if rate>0 and d.get('ip'): limited.append((d['ip'],rate))
    if not limited:return
    total=max(64,int(float(c['network']['line_down_mbit' if direction=='down' else 'line_up_mbit'])*1000))
    if run(['tc','qdisc','add','dev',iface,'root','handle','1:','htb','default','999']).returncode:return
    run(['tc','class','add','dev',iface,'parent','1:','classid','1:999','htb','rate',f'{total}kbit','ceil',f'{total}kbit'])
    run(['tc','qdisc','add','dev',iface,'parent','1:999','fq_codel'])
    cls=10
    for ip,rate in limited:
        rate=max(8,min(rate,total)); cid=f'1:{cls}'
        run(['tc','class','add','dev',iface,'parent','1:','classid',cid,'htb','rate',f'{rate}kbit','ceil',f'{rate}kbit'])
        field='dst' if direction=='down' else 'src'; run(['tc','filter','add','dev',iface,'protocol','ip','parent','1:','prio','1','u32','match','ip',field,ip,'flowid',cid]); run(['tc','qdisc','add','dev',iface,'parent',cid,'fq_codel']); cls+=1

def shaping(c,devices,users):
    global _SHAPE_SIG
    if not c.get('features',{}).get('speed_limits',True):
        clear_shaping(c); return True
    n=c['network']; w=wan_interface(c)
    sig=(tuple((d['id'],d.get('ip'),d.get('speed_down_kbit'),d.get('speed_up_kbit')) for d in devices),tuple((u['id'],u.get('speed_down_kbit'),u.get('speed_up_kbit')) for u in users),n.get('line_down_mbit'),n.get('line_up_mbit'),w)
    if sig==_SHAPE_SIG:return False
    _shape_one(n['lan_interface'],'down',devices,users,c); _shape_one(w,'up',devices,users,c); _SHAPE_SIG=sig; return True

def runtime(c):
    ensure_lan(c); write_hostapd(c); write_dnsmasq(c); ensure_firewall(c)
