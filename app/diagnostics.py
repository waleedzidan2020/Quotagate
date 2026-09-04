from __future__ import annotations
import re, statistics, subprocess, time
from pathlib import Path
from . import db, shaping


def run(args, timeout=5):
    try:
        return subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    except Exception as e:
        class R:
            returncode=1; stdout=''; stderr=str(e)
        return R()


def _float(v):
    try: return float(v)
    except Exception: return None


def wifi_stations(iface='wlan0'):
    p=run(['iw','dev',iface,'station','dump'],4)
    if p.returncode:
        return {'ok':False,'error':(p.stderr or p.stdout).strip(),'stations':[]}
    out=[]; cur=None
    for raw in p.stdout.splitlines():
        line=raw.strip()
        if line.startswith('Station '):
            if cur: out.append(cur)
            mac=line.split()[1].lower(); cur={'mac':mac}
            continue
        if not cur or ':' not in line: continue
        k,v=line.split(':',1); k=k.strip().lower(); v=v.strip()
        if k in ('inactive time','connected time'):
            m=re.search(r'(-?\d+)',v); cur[k.replace(' ','_')]=int(m.group(1)) if m else None
        elif k in ('tx bytes','rx bytes','tx packets','rx packets','tx retries','tx failed','rx drop misc'):
            m=re.search(r'(-?\d+)',v); cur[k.replace(' ','_')]=int(m.group(1)) if m else 0
        elif k in ('signal','signal avg'):
            m=re.search(r'(-?\d+(?:\.\d+)?)',v); cur[k.replace(' ','_')]=_float(m.group(1)) if m else None
        elif k in ('tx bitrate','rx bitrate','expected throughput'):
            m=re.search(r'([0-9.]+)\s*MBit/s',v,re.I)
            cur[k.replace(' ','_')+'_mbps']=_float(m.group(1)) if m else None
    if cur: out.append(cur)
    for s in out:
        tx=max(1,int(s.get('tx_packets') or 0))
        s['retry_ratio']=round(float(s.get('tx_retries') or 0)/tx,4)
        sig=s.get('signal')
        s['signal_quality']='unknown' if sig is None else ('excellent' if sig>=-55 else 'good' if sig>=-67 else 'weak' if sig>=-75 else 'poor')
    return {'ok':True,'stations':out,'count':len(out)}


def default_gateway():
    p=run(['ip','route','show','default'],3)
    if p.returncode:return ''
    for line in p.stdout.splitlines():
        m=re.search(r'\bvia\s+(\d+\.\d+\.\d+\.\d+)',line)
        if m:return m.group(1)
    return ''


def ping_stats(host, count=5, timeout=8):
    if not re.fullmatch(r'[A-Za-z0-9.:-]{1,253}',str(host or '')):
        return {'ok':False,'error':'invalid target'}
    count=max(1,min(int(count),10))
    p=run(['ping','-c',str(count),'-W','2',str(host)],timeout)
    txt=(p.stdout or '')+'\n'+(p.stderr or '')
    times=[float(x) for x in re.findall(r'time[=<]([0-9.]+)\s*ms',txt)]
    loss=None
    m=re.search(r'([0-9.]+)%\s*packet loss',txt)
    if m: loss=float(m.group(1))
    if times:
        avg=sum(times)/len(times); jitter=statistics.pstdev(times) if len(times)>1 else 0.0
        return {'ok':True,'target':host,'sent':count,'received':len(times),'loss_percent':loss if loss is not None else round((count-len(times))*100/count,2),'min_ms':round(min(times),3),'avg_ms':round(avg,3),'max_ms':round(max(times),3),'jitter_ms':round(jitter,3)}
    return {'ok':False,'target':host,'sent':count,'received':0,'loss_percent':100.0 if loss is None else loss,'error':(p.stderr or 'no replies').strip()[:300]}


def interface_status(iface):
    base=Path('/sys/class/net')/iface
    if not base.exists(): return {'exists':False,'up':False}
    try: state=(base/'operstate').read_text().strip()
    except Exception: state='unknown'
    p=run(['ip','-br','addr','show','dev',iface],3)
    return {'exists':True,'up':state=='up','state':state,'summary':p.stdout.strip()}


def vpn_interfaces():
    p=run(['ip','-o','link','show'],3)
    found=[]
    if p.returncode:return found
    for line in p.stdout.splitlines():
        m=re.match(r'\d+:\s+([^:@]+)',line)
        if not m:continue
        name=m.group(1)
        if re.match(r'^(tun|tap|wg|ppp)\d*$|^wg\w+$',name): found.append(name)
    return sorted(set(found))


def snapshot(c, include_ping=False):
    n=c['network']; lan=n.get('lan_interface','wlan0'); wan=n.get('wan_interface','eth0')
    data={
        'wan':interface_status(wan),
        'lan':interface_status(lan),
        'wifi':wifi_stations(lan),
        'vpn_interfaces':vpn_interfaces(),
        'default_gateway':default_gateway(),
        'shaping':shaping.status(c, db.devices(), db.users()),
        'timestamp':int(time.time())
    }
    if include_ping:
        gw=data['default_gateway']
        target=str(c.get('diagnostics',{}).get('internet_target','1.1.1.1'))
        data['gateway_ping']=ping_stats(gw,5) if gw else {'ok':False,'error':'default gateway not found'}
        data['internet_ping']=ping_stats(target,5)
    return data
