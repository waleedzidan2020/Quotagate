from __future__ import annotations
import socket, socketserver, struct, threading, ipaddress, time
from . import db

QTYPE={1:'A',28:'AAAA',5:'CNAME',15:'MX',16:'TXT',2:'NS'}
PRESETS={
 'ads_tracking':('doubleclick.net','googlesyndication.com','googleadservices.com','ads-twitter.com','app-measurement.com'),
 'social':('facebook.com','fbcdn.net','instagram.com','tiktok.com','twitter.com','x.com','snapchat.com'),
 'streaming':('netflix.com','nflxvideo.net','youtube.com','googlevideo.com','twitch.tv','spotify.com'),
 'gambling':('bet365.com','1xbet.com','betway.com','pokerstars.com'),
 'adult':('pornhub.com','xvideos.com','xnxx.com','redtube.com','youporn.com')
}
_LOG_LAST={}


def _log_limited(key,message,level='error',seconds=60):
    now=time.time()
    if now-_LOG_LAST.get(key,0)<seconds:return
    _LOG_LAST[key]=now
    try:db.event(message,level)
    except Exception:pass


def parse_query(data):
    if len(data)<12:return None
    off=12; labels=[]
    while off<len(data):
        ln=data[off]; off+=1
        if ln==0: break
        if off+ln>len(data): return None
        labels.append(data[off:off+ln].decode('idna','ignore')); off+=ln
    if off+4>len(data): return None
    qt=struct.unpack('!H',data[off:off+2])[0]
    return '.'.join(labels).lower().rstrip('.'), QTYPE.get(qt,str(qt)), off+4


def error_reply(data,rcode=2):
    """Return a valid DNS error response instead of letting clients time out."""
    if len(data)<12:return data
    flags=struct.unpack('!H',data[2:4])[0]
    flags=(flags|0x8000|0x0080)&~0x000F
    flags|=(int(rcode)&0xF)
    return data[:2]+struct.pack('!H',flags)+data[4:6]+b'\x00\x00\x00\x00\x00\x00'+data[12:]


def blocked_reply(data):return error_reply(data,3)


def redirect_reply(data,target):
    try: packed=ipaddress.IPv4Address(target).packed
    except Exception:return blocked_reply(data)
    q=parse_query(data)
    if not q or q[1]!='A':return blocked_reply(data)
    qend=q[2]; flags=0x8180
    header=data[:2]+struct.pack('!HHHHH',flags,1,1,0,0)
    question=data[12:qend]
    answer=b'\xc0\x0c'+struct.pack('!HHIH',1,1,60,4)+packed
    return header+question+answer


def _hit(domain,pattern):
    p=pattern.lstrip('*.').lower().rstrip('.')
    return domain==p or domain.endswith('.'+p)


def explicit_rule(domain, client_ip):
    dev=db.device_by_ip(client_ip); did=dev['id'] if dev else 0; uid=dev.get('user_id') if dev else 0
    candidates=[]
    for r in db.dns_rules():
        if not r['enabled'] or not _hit(domain,r['domain']):continue
        st=r['scope_type']; sid=int(r['scope_id'] or 0)
        if st=='global': candidates.append((1,r))
        elif st=='user' and uid and sid==int(uid): candidates.append((2,r))
        elif st=='device' and did and sid==int(did): candidates.append((3,r))
    return sorted(candidates,key=lambda x:x[0],reverse=True)[0][1] if candidates else None


def preset_action(domain,c):
    enabled=c.get('dns',{}).get('presets',{})
    for name,domains in PRESETS.items():
        if enabled.get(name) and any(_hit(domain,x) for x in domains):return {'action':'block','preset':name}
    return None


def _uplink_gateway(c):
    """Return explicit DNS fallback or infer the first host of uplink_net.

    For the default QuotaGate topology 192.168.1.0/24 this is 192.168.1.1.
    """
    n=c.get('network',{})
    explicit=str(n.get('dns_fallback') or '').strip()
    try:
        if explicit and ipaddress.ip_address(explicit).version==4:return explicit
    except Exception:pass
    try:
        net=ipaddress.ip_network(str(n.get('uplink_net') or ''),strict=False)
        if net.version==4:
            return str(next(net.hosts()))
    except Exception:pass
    return ''


def upstreams(c,client_ip=''):
    dev=db.device_by_ip(client_ip) if client_ip else None
    vals=[]
    if dev and dev.get('dns_server'):
        vals.append(dev['dns_server'])
    elif dev and dev.get('user_id'):
        u=db.user_by_id(dev['user_id'])
        if u and u.get('dns_server'):vals.append(u['dns_server'])
    if not vals:
        if c.get('dns',{}).get('family_mode'):
            vals.extend(['1.1.1.3','1.0.0.3'])
        else:
            vals.extend(c.get('network',{}).get('upstream_dns') or ['1.1.1.1','8.8.8.8'])
    fallback=_uplink_gateway(c)
    if fallback:vals.append(fallback)
    out=[]
    for x in vals:
        x=str(x or '').strip()
        try:
            if ipaddress.ip_address(x).version==4 and x not in out:out.append(x)
        except Exception:continue
    return out


def forward_udp(data,c,client_ip=''):
    errors=[]
    for host in upstreams(c,client_ip):
        s=None
        try:
            s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            # Keep failure recovery faster than Android/Windows DNS retry timers.
            s.settimeout(1.0)
            s.sendto(data,(host,53))
            ans,_=s.recvfrom(65535)
            if ans:return ans
        except Exception as e:
            errors.append(f'{host}: {e}')
        finally:
            try:
                if s:s.close()
            except Exception:pass
    if errors:
        _log_limited('dns-upstream','DNS upstream failure; tried fallbacks: '+' | '.join(errors)[:1200])
    return error_reply(data,2)


class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data,sock=self.request; ip=self.client_address[0]
        action='allow'; domain=''; qt=''
        try:
            q=parse_query(data)
            if not q:
                try:sock.sendto(error_reply(data,1),self.client_address)
                except Exception:pass
                return
            domain,qt,_=q
            rule=explicit_rule(domain,ip)
            preset=None if rule else preset_action(domain,self.server.cfg)
            chosen=rule or preset
            if chosen and chosen.get('action')=='block': ans=blocked_reply(data); action='block'
            elif chosen and chosen.get('action')=='redirect': ans=redirect_reply(data,chosen.get('target','')); action='redirect'
            else: ans=forward_udp(data,self.server.cfg,ip)
        except Exception as e:
            # A database/filter/upstream bug must never make the LAN lose DNS.
            _log_limited('dns-handler','DNS proxy handler exception; returning SERVFAIL: '+str(e)[:900])
            ans=error_reply(data,2)
        try:sock.sendto(ans,self.client_address)
        except Exception as e:_log_limited('dns-send','DNS proxy response send failed: '+str(e)[:500])
        if domain and self.server.cfg.get('features',{}).get('dns_history',True):
            try:db.log_dns(ip,domain,qt,action)
            except Exception as e:_log_limited('dns-history','DNS history write failed: '+str(e)[:500],'warning')


class ThreadingUDP(socketserver.ThreadingMixIn,socketserver.UDPServer):
    daemon_threads=True
    allow_reuse_address=True


class DNSProxy:
    def __init__(self,c): self.cfg=c; self.srv=None; self.thread=None
    def update_config(self,c):
        self.cfg=c
        if self.srv:self.srv.cfg=c
    def start(self):
        host=self.cfg['network']['lan_ip']
        self.srv=ThreadingUDP((host,53),UDPHandler); self.srv.cfg=self.cfg
        self.thread=threading.Thread(target=self.srv.serve_forever,daemon=True); self.thread.start()
        _log_limited('dns-start',f'DNS proxy active on {host}:53; upstreams={upstreams(self.cfg)}','info',1)
    def stop(self):
        if self.srv:self.srv.shutdown();self.srv.server_close()
