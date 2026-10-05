from __future__ import annotations

import ipaddress
import socket
import threading
import time

from . import db, network

_LOCK=threading.RLock()
_LAST_RESOLVE={}
_SYNC_SIG=None
_TABLE='quotagate_dnsblock'


def enabled(c):
    d=c.get('dns',{})
    return bool(c.get('features',{}).get('dns_proxy',True) and d.get('ip_guard_enabled',True))


def ttl_seconds(c):
    return max(60,min(int(c.get('dns',{}).get('ip_guard_seconds',300) or 300),3600))


def _valid_v4(value):
    try:return str(ipaddress.IPv4Address(str(value)))
    except Exception:return ''


def _resolve_ipv4(domain):
    out=[]
    try:
        for item in socket.getaddrinfo(str(domain),None,socket.AF_INET,socket.SOCK_STREAM):
            ip=_valid_v4(item[4][0])
            if ip and ip not in out:out.append(ip)
    except Exception:return []
    return out[:32]


def _scope_sets(rows,devices):
    global_ips=set();dev_sets={};user_sets={}
    for r in rows:
        ip=_valid_v4(r.get('ip'))
        if not ip:continue
        st=str(r.get('scope_type') or '');sid=int(r.get('scope_id') or 0)
        if st=='global':global_ips.add(ip)
        elif st=='device' and sid>0:dev_sets.setdefault(sid,set()).add(ip)
        elif st=='user' and sid>0:user_sets.setdefault(sid,set()).add(ip)
    current={}
    users={}
    for d in devices:
        ip=_valid_v4(d.get('ip'))
        if not ip:continue
        current[int(d['id'])]=ip
        uid=int(d.get('user_id') or 0)
        if uid>0:users.setdefault(uid,[]).append(ip)
    return global_ips,dev_sets,user_sets,current,users


def _set_line(name,ips):
    vals=', '.join(sorted(ips))
    if vals:return f' set {name} {{ type ipv4_addr; elements = {{ {vals} }} }}'
    return f' set {name} {{ type ipv4_addr; }}'


def sync(c,force=False):
    global _SYNC_SIG
    with _LOCK:
        if not enabled(c):
            network.run(['nft','delete','table','inet',_TABLE])
            _SYNC_SIG=None
            return {'enabled':False,'ok':True,'tracked_ips':0}
        db.prune_dns_block_ips()
        rows=db.dns_block_ips()
        devices=db.devices()
        global_ips,dev_sets,user_sets,current,users=_scope_sets(rows,devices)
        sig=(
            tuple(sorted(global_ips)),
            tuple((i,tuple(sorted(v))) for i,v in sorted(dev_sets.items())),
            tuple((i,tuple(sorted(v))) for i,v in sorted(user_sets.items())),
            tuple(sorted(current.items())),
            tuple((i,tuple(sorted(v))) for i,v in sorted(users.items())),
            str(c.get('network',{}).get('client_net','192.168.2.0/24')),
        )
        if not force and sig==_SYNC_SIG:
            p=network.run(['nft','list','table','inet',_TABLE])
            if p.returncode==0:
                return {'enabled':True,'ok':True,'tracked_ips':len({r['ip'] for r in rows})}
        lines=[f'table inet {_TABLE} {{']
        lines.append(_set_line('global_v4',global_ips))
        for did,ips in sorted(dev_sets.items()):lines.append(_set_line(f'd_{did}_v4',ips))
        for uid,ips in sorted(user_sets.items()):lines.append(_set_line(f'u_{uid}_v4',ips))
        lines.append(' chain forward {')
        lines.append('  type filter hook forward priority -20; policy accept;')
        client_net=str(c.get('network',{}).get('client_net','192.168.2.0/24'))
        if global_ips:lines.append(f'  ip saddr {client_net} ip daddr @global_v4 counter drop')
        for did,ips in sorted(dev_sets.items()):
            src=current.get(did)
            if src and ips:lines.append(f'  ip saddr {src} ip daddr @d_{did}_v4 counter drop')
        for uid,ips in sorted(user_sets.items()):
            if not ips:continue
            for src in sorted(set(users.get(uid,[]))):
                lines.append(f'  ip saddr {src} ip daddr @u_{uid}_v4 counter drop')
        lines.append(' }')
        lines.append('}')
        network.run(['nft','delete','table','inet',_TABLE])
        p=network.run(['nft','-f','-'],input_text='\n'.join(lines)+'\n')
        if p.returncode:
            db.event('DNS IP guard nft apply failed: '+(p.stderr or p.stdout or 'unknown')[:900],'error')
            _SYNC_SIG=None
            return {'enabled':True,'ok':False,'tracked_ips':len({r['ip'] for r in rows}),'error':(p.stderr or p.stdout or '').strip()[:300]}
        _SYNC_SIG=sig
        return {'enabled':True,'ok':True,'tracked_ips':len({r['ip'] for r in rows})}


def status(c):
    if not enabled(c):return {'enabled':False,'ok':True,'tracked_ips':0}
    rows=db.dns_block_ips()
    p=network.run(['nft','list','table','inet',_TABLE])
    return {'enabled':True,'ok':p.returncode==0,'tracked_ips':len({r['ip'] for r in rows})}


def _resolve_and_remember(rule,domain,c,sync_after=True):
    ips=_resolve_ipv4(domain)
    if not ips:return False
    ttl=ttl_seconds(c)
    changed=False
    for ip in ips:
        try:
            db.remember_dns_block_ip(int(rule['id']),domain,ip,ttl);changed=True
        except Exception as e:
            try:db.event('DNS IP guard DB update failed: '+str(e)[:500],'warning')
            except Exception:pass
    if changed and sync_after:sync(c)
    return changed


def observe_block(rule,domain,c):
    if not rule or str(rule.get('action'))!='block' or not enabled(c):return
    key=(int(rule.get('id') or 0),str(domain).lower())
    if key[0]<=0:return
    now=time.time()
    with _LOCK:
        if now-_LAST_RESOLVE.get(key,0)<20:return
        _LAST_RESOLVE[key]=now
    threading.Thread(target=_resolve_and_remember,args=(dict(rule),str(domain),dict(c)),daemon=True,name='qg-dns-ip-guard').start()


def seed_rule(rule,domains,c):
    if not rule or str(rule.get('action'))!='block' or not enabled(c):return
    seen=[]
    for d in domains:
        d=str(d or '').strip().lower().rstrip('.')
        if d and d not in seen:seen.append(d)
        if len(seen)>=40:break
    if not seen:return
    def run_seed():
        changed=False
        for domain in seen:
            if _resolve_and_remember(rule,domain,c,sync_after=False):changed=True
        if changed:sync(c)
    threading.Thread(target=run_seed,daemon=True,name='qg-dns-ip-seed').start()
