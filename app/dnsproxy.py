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

# A single product can use several hostnames/CDNs.  Keep these groups narrow:
# entering youtube.com blocks the YouTube service without broadly blocking Google.
SERVICE_GROUPS={
 'youtube.com':(
   'youtube.com',
   'youtu.be',
   'youtube-nocookie.com',
   'googlevideo.com',
   'ytimg.com',
   'youtubei.googleapis.com',
   'youtube.googleapis.com',
   'yt3.ggpht.com',
 )
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
        try:label=data[off:off+ln].decode('ascii')
        except UnicodeDecodeError:return None
        labels.append(label); off+=ln
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
    p=str(pattern or '').lstrip('*.').lower().rstrip('.')
    d=str(domain or '').lower().rstrip('.')
    return bool(p) and (d==p or d.endswith('.'+p))


def rule_patterns(pattern):
    """Expand well-known service roots while keeping ordinary domains exact+subdomains."""
    p=str(pattern or '').lstrip('*.').lower().rstrip('.')
    return SERVICE_GROUPS.get(p,(p,))


def rule_matches(domain,pattern):
    return any(_hit(domain,p) for p in rule_patterns(pattern))


def explicit_rule(domain, client_ip):
    dev=db.device_by_ip(client_ip); did=dev['id'] if dev else 0; uid=dev.get('user_id') if dev else 0
    candidates=[]
    for r in db.dns_rules():
        if not r['enabled'] or not rule_matches(domain,r['domain']):continue
        st=r['scope_type']; sid=int(r['scope_id'] or 0)
        if st=='global': candidates.append((1,int(r.get('id') or 0),r))
        elif st=='user' and uid and sid==int(uid): candidates.append((2,int(r.get('id') or 0),r))
        elif st=='device' and did and sid==int(did): candidates.append((3,int(r.get('id') or 0),r))
    # Most-specific scope wins.  For duplicate historical rows, newest rule wins.
    return sorted(candidates,key=lambda x:(x[0],x[1]),reverse=True)[0][2] if candidates else None


def preset_action(domain,c):
    enabled=c.get('dns',{}).get('presets',{})
    for name,domains in PRESETS.items():
        if enabled.get(name) and any(_hit(domain,x) for x in domains):return {'action':'block','preset':name}
    return None


def _uplink_gateway(c):
    """Return explicit DNS fallback or infer the first host of uplink_net."""
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


def forward_tcp(data,c,client_ip=''):
    errors=[]
    for host in upstreams(c,client_ip):
        s=None
        try:
            s=socket.create_connection((host,53),timeout=1.5)
            s.settimeout(1.5)
            s.sendall(struct.pack('!H',len(data))+data)
            raw_len=_recv_exact(s,2)
            if len(raw_len)!=2:raise RuntimeError('short DNS TCP length')
            size=struct.unpack('!H',raw_len)[0]
            if size<12 or size>65535:raise RuntimeError('invalid DNS TCP response length')
            ans=_recv_exact(s,size)
            if len(ans)==size:return ans
            raise RuntimeError('short DNS TCP response')
        except Exception as e:
            errors.append(f'{host}: {e}')
        finally:
            try:
                if s:s.close()
            except Exception:pass
    if errors:
        _log_limited('dns-upstream-tcp','DNS TCP upstream failure; tried fallbacks: '+' | '.join(errors)[:1200])
    return error_reply(data,2)


def process_query(data,c,client_ip,transport='udp'):
    """Apply global/user/device policy and return (reply, domain, qtype, action)."""
    q=parse_query(data)
    if not q:return error_reply(data,1),'','','error'
    domain,qt,_=q
    rule=explicit_rule(domain,client_ip)
    preset=None if rule else preset_action(domain,c)
    chosen=rule or preset
    if chosen and chosen.get('action')=='block':
        return blocked_reply(data),domain,qt,'block'
    if chosen and chosen.get('action')=='redirect':
        return redirect_reply(data,chosen.get('target','')),domain,qt,'redirect'
    forward=forward_tcp if transport=='tcp' else forward_udp
    return forward(data,c,client_ip),domain,qt,'allow'


def _log_query(c,client_ip,domain,qt,action):
    if domain and c.get('features',{}).get('dns_history',True):
        try:db.log_dns(client_ip,domain,qt,action)
        except Exception as e:_log_limited('dns-history','DNS history write failed: '+str(e)[:500],'warning')


class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data,sock=self.request; ip=self.client_address[0]
        try:ans,domain,qt,action=process_query(data,self.server.cfg,ip)
        except Exception as e:
            _log_limited('dns-handler','DNS proxy handler exception; returning SERVFAIL: '+str(e)[:900])
            ans=error_reply(data,2);domain='';qt='';action='error'
        try:sock.sendto(ans,self.client_address)
        except Exception as e:_log_limited('dns-send','DNS proxy response send failed: '+str(e)[:500])
        _log_query(self.server.cfg,ip,domain,qt,action)


def _recv_exact(sock,size):
    chunks=[];remaining=int(size)
    while remaining>0:
        part=sock.recv(remaining)
        if not part:return b''
        chunks.append(part);remaining-=len(part)
    return b''.join(chunks)


class TCPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        ip=self.client_address[0];self.request.settimeout(4.0)
        try:
            raw_len=_recv_exact(self.request,2)
            if len(raw_len)!=2:return
            size=struct.unpack('!H',raw_len)[0]
            if size<12 or size>65535:return
            data=_recv_exact(self.request,size)
            if len(data)!=size:return
            ans,domain,qt,action=process_query(data,self.server.cfg,ip,transport='tcp')
            self.request.sendall(struct.pack('!H',len(ans))+ans)
            _log_query(self.server.cfg,ip,domain,qt,action)
        except Exception as e:_log_limited('dns-tcp','DNS TCP handler failed: '+str(e)[:700])


class ThreadingUDP(socketserver.ThreadingMixIn,socketserver.UDPServer):
    daemon_threads=True
    allow_reuse_address=True


class ThreadingTCP(socketserver.ThreadingMixIn,socketserver.TCPServer):
    daemon_threads=True
    allow_reuse_address=True


class DNSProxy:
    def __init__(self,c):
        self.cfg=c;self.srv=None;self.udp_srv=None;self.tcp_srv=None;self.threads=[]
    def update_config(self,c):
        self.cfg=c
        if self.udp_srv:self.udp_srv.cfg=c
        if self.tcp_srv:self.tcp_srv.cfg=c
    def start(self):
        host=self.cfg['network']['lan_ip']
        udp=None;tcp=None
        try:
            udp=ThreadingUDP((host,53),UDPHandler);udp.cfg=self.cfg
            tcp=ThreadingTCP((host,53),TCPHandler);tcp.cfg=self.cfg
            self.udp_srv=udp;self.tcp_srv=tcp;self.srv=udp
            for name,srv in (('udp',udp),('tcp',tcp)):
                t=threading.Thread(target=srv.serve_forever,daemon=True,name='quotagate-dns-'+name)
                t.start();self.threads.append(t)
        except Exception:
            for srv in (udp,tcp):
                try:
                    if srv:srv.server_close()
                except Exception:pass
            self.udp_srv=self.tcp_srv=self.srv=None
            raise
        _log_limited('dns-start',f'DNS proxy active on {host}:53 UDP+TCP; upstreams={upstreams(self.cfg)}','info',1)
    def stop(self):
        for srv in (self.udp_srv,self.tcp_srv):
            if srv:
                try:srv.shutdown();srv.server_close()
                except Exception:pass
        self.udp_srv=self.tcp_srv=self.srv=None
