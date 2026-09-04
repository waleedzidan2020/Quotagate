from __future__ import annotations
import socket, socketserver, struct, threading, time, ipaddress
from . import db

QTYPE={1:'A',28:'AAAA',5:'CNAME',15:'MX',16:'TXT',2:'NS'}

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

def blocked_reply(data, ipv6=False):
    if len(data)<12:return data
    flags=struct.unpack('!H',data[2:4])[0]
    flags=(flags|0x8000|0x0080) & ~0x000F
    flags|=3
    return data[:2]+struct.pack('!H',flags)+data[4:6]+b'\x00\x00\x00\x00\x00\x00'+data[12:]

def match_rule(domain, client_ip):
    dev=db.device_by_ip(client_ip); did=dev['id'] if dev else 0; uid=dev.get('user_id') if dev else 0
    rules=[r for r in db.dns_rules() if r['enabled']]
    def hit(rule):
        d=rule['domain'].lstrip('*.')
        return domain==d or domain.endswith('.'+d)
    candidates=[]
    for r in rules:
        if not hit(r): continue
        st=r['scope_type']; sid=int(r['scope_id'] or 0)
        if st=='global': candidates.append((1,r))
        elif st=='user' and uid and sid==int(uid): candidates.append((2,r))
        elif st=='device' and did and sid==int(did): candidates.append((3,r))
    if not candidates:return None
    return sorted(candidates,key=lambda x:x[0],reverse=True)[0][1]

def upstreams(c):
    vals=c['network'].get('upstream_dns') or ['1.1.1.1','8.8.8.8']
    return [x for x in vals if x]

def forward_udp(data, c):
    for host in upstreams(c):
        try:
            s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.settimeout(2.5); s.sendto(data,(host,53)); ans,_=s.recvfrom(65535); s.close(); return ans
        except Exception: continue
    return blocked_reply(data)

class UDPHandler(socketserver.BaseRequestHandler):
    def handle(self):
        data,sock=self.request; ip=self.client_address[0]; q=parse_query(data)
        if not q:return
        domain,qt,_=q; rule=match_rule(domain,ip); action='allow'
        if rule and rule['action']=='block': ans=blocked_reply(data); action='block'
        elif rule and rule['action']=='redirect': ans=blocked_reply(data); action='redirect'
        else: ans=forward_udp(data,self.server.cfg)
        try:sock.sendto(ans,self.client_address)
        except Exception:pass
        try: db.log_dns(ip,domain,qt,action)
        except Exception:pass

class ThreadingUDP(socketserver.ThreadingMixIn,socketserver.UDPServer): daemon_threads=True; allow_reuse_address=True

class DNSProxy:
    def __init__(self,c): self.cfg=c; self.srv=None; self.thread=None
    def start(self):
        host=self.cfg['network']['lan_ip']; self.srv=ThreadingUDP((host,53),UDPHandler); self.srv.cfg=self.cfg
        self.thread=threading.Thread(target=self.srv.serve_forever,daemon=True); self.thread.start()
    def stop(self):
        if self.srv:self.srv.shutdown();self.srv.server_close()
