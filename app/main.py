from __future__ import annotations
import base64, io, json, os, secrets, ssl, threading, time, subprocess
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from . import auth, config, db, network, diagnostics
from .dnsproxy import DNSProxy

ROOT=Path(__file__).resolve().parent.parent; WEB=ROOT/'web'; LOCK=threading.RLock(); SESSIONS={}; FAILS={}; BANS={}; REQS={}; PREV={}; PREV_GATEWAY={'up':0,'down':0}; DNS=None

def body(h):
    n=int(h.headers.get('Content-Length','0') or 0)
    if n>2_000_000:raise ValueError('body too large')
    return json.loads(h.rfile.read(n) or b'{}')
def client_ip(h):return h.client_address[0]
def session(h):
    ip=client_ip(h)
    if BANS.get(ip,0)>time.time():return False
    c=SimpleCookie(h.headers.get('Cookie',''));x=c.get('qg_session')
    if not x:return False
    with LOCK:
        s=SESSIONS.get(x.value)
        if s and s>time.time():SESSIONS[x.value]=time.time()+28800;return True
    return False
def request_allowed(ip,limit=240,window=60):
    now=time.time();arr=[x for x in REQS.get(ip,[]) if now-x<window]
    if len(arr)>=limit:return False
    arr.append(now);REQS[ip]=arr;return True

def mask_config(c):
    x=json.loads(json.dumps(c));x['admin']['password_hash']='***';x['admin']['totp_secret']='***';x['wan']['pppoe_password']='***';password_set=bool(x['wifi'].pop('passphrase',''));x['wifi']['password_set']=password_set;return x
def wifi_view(c):
    w=c['wifi'];return {'ssid':w.get('ssid',''),'hidden':bool(w.get('hidden',False)),'password_set':bool(w.get('passphrase','')),'channel':int(w.get('channel',1))}
def restart_wifi_after_save(c):
    time.sleep(1.0)
    try:network.restart_wifi(c);db.event('Wi-Fi settings applied and hostapd restarted','info')
    except Exception as e:db.event('Wi-Fi restart failed: '+str(e),'error');db.alert('wifi-restart-failed','Wi-Fi settings were saved but hostapd failed to restart')
def quota_view(c,users):
    out=[]
    for u in users:
        x=dict(u);x['effective_quota_gb']=network.effective_quota(u,users,c);out.append(x)
    return out

def scan(c):
    n=c['network'];net=__import__('ipaddress').ip_network(n['client_net']);guest=c.get('guest',{});rules={r['mac']:r['action'] for r in db.mac_rules()}
    for item in network.discover(n['lan_interface']):
        mac,ip,name=item['mac'],item['ip'],item.get('name','')
        try:
            if __import__('ipaddress').ip_address(ip) not in net:continue
        except Exception:continue
        if n.get('decline_random_macs') and network.is_random_mac(mac) and rules.get(mac)!='whitelist':db.mac_rule(mac,'blacklist','random MAC declined');continue
        did,new=db.upsert_device(mac,ip,name=name,manufacturer=network.manufacturer(mac))
        if new:
            db.event(f'New device {mac} at {ip}','warning');db.alert('new-device',f'New device {mac} joined at {ip}')
            if rules.get(mac)=='whitelist':db.update_device(did,enabled=1)
            elif n.get('stop_new_connections'):db.update_device(did,enabled=0)
            elif guest.get('enabled'):
                guests=[d for d in db.devices() if d.get('is_guest')]
                if len(guests)<int(guest.get('max_devices',10)):
                    uid=db.create_user('Guest-'+mac[-5:].replace(':',''),guest.get('quota_gb',.5),guest.get('speed_down_kbit',1024),guest.get('speed_up_kbit',256),'fixed');db.update_device(did,user_id=uid,enabled=1,is_guest=1)
            elif n.get('default_new_enabled'):db.update_device(did,enabled=1)

def apply(c):
    us=db.users();ds=db.devices();rebuilt=network.sync_rules(c,ds,network.blocked(c,us,ds));network.shaping(c,ds,us);network.sync_dnsmasq(c);return rebuilt

def maybe_reset(c):
    b=c['bundle']
    if not b.get('auto_reset',True):return
    today=time.localtime();p=time.strftime('%Y-%m');day=int(b.get('reset_day',1) or 1);trigger=(today.tm_mday==day)
    if b.get('bundle_type')=='end_of_month':
        import calendar;trigger=today.tm_mday==calendar.monthrange(today.tm_year,today.tm_mon)[1]
    if trigger and b.get('last_reset_period')!=p:db.reset_month();b['last_reset_period']=p;config.save(c);db.alert('bundle-reset',f'Bundle counters reset for {p}')

def update_status(c,persist=False):
    u=c.get('updates',{});repo=str(u.get('repo','https://github.com/waleedzidan2020/Quotagate.git'));branch=str(u.get('branch','main'))
    try:
        p=subprocess.run(['git','ls-remote',repo,f'refs/heads/{branch}'],text=True,capture_output=True,timeout=12)
        if p.returncode:raise RuntimeError((p.stderr or 'git ls-remote failed').strip())
        remote=(p.stdout.split() or [''])[0];local=Path('/var/lib/quotagate/installed_commit').read_text().strip() if Path('/var/lib/quotagate/installed_commit').exists() else '';result={'ok':True,'current_commit':local,'remote_commit':remote,'available':bool(remote and remote!=local)}
        if persist:c['updates']['last_check']=int(time.time());c['updates']['available_commit']=remote if result['available'] else '';config.save(c)
        return result
    except Exception as e:return {'ok':False,'error':str(e)[:300]}

def worker():
    global PREV,PREV_GATEWAY,DNS
    while True:
        try:
            c=config.load();maybe_reset(c);scan(c);cur=network.counters();rows=[]
            for k,val in cur.items():
                old=PREV.get(k,val);delta=max(0,val-old)
                if delta:
                    did,direction=k;rows.append((did,delta if direction=='up' else 0,delta if direction=='down' else 0))
            db.add_usage_batch(rows);gcur=network.gateway_counters();gup=max(0,int(gcur.get('up',0))-int(PREV_GATEWAY.get('up',gcur.get('up',0))));gdown=max(0,int(gcur.get('down',0))-int(PREV_GATEWAY.get('down',gcur.get('down',0))));db.add_gateway_usage(gup,gdown);PREV=cur;PREV_GATEWAY=gcur
            if apply(c):PREV=network.counters();PREV_GATEWAY=network.gateway_counters()
            dcfg=c.get('dns',{});db.prune_dns(dcfg.get('history_days',7),dcfg.get('max_history_rows',100000))
            if DNS:DNS.update_config(c)
            u=c.get('updates',{})
            if u.get('auto_check') and time.time()-int(u.get('last_check',0) or 0)>86400:update_status(c,True)
        except Exception as e:
            try:db.event('worker: '+str(e),'error')
            except Exception:pass
        time.sleep(15)

def system_info(c):
    def cmd(x):
        try:return subprocess.run(x,text=True,capture_output=True,timeout=3).stdout.strip()
        except Exception:return ''
    return {'hostname':cmd(['hostname']),'kernel':cmd(['uname','-r']),'uptime':cmd(['uptime','-p']),'interfaces':cmd(['ip','-br','addr']),'version':c.get('version','3.1.0')}

class Handler(SimpleHTTPRequestHandler):
    server_version='QuotaGate-antiX/3.1'
    def log_message(self,*a):pass
    def hdr(self):self.send_header('cache-control','no-store');self.send_header('pragma','no-cache');self.send_header('x-frame-options','DENY');self.send_header('x-content-type-options','nosniff');self.send_header('referrer-policy','no-referrer')
    def json(self,obj,status=200,cookie=None):
        b=json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode();self.send_response(status);self.send_header('content-type','application/json; charset=utf-8');self.send_header('content-length',str(len(b)));self.hdr();
        if cookie:self.send_header('set-cookie',cookie)
        self.end_headers();self.wfile.write(b)
    def need(self):
        if not session(self):self.json({'error':'unauthorized'},401);return False
        if not request_allowed(client_ip(self)):self.json({'error':'rate limit'},429);return False
        return True
    def do_GET(self):
        u=urlparse(self.path);p=u.path
        if p.startswith('/api/'):return self.api_get(p,parse_qs(u.query))
        if p=='/':p='/index.html'
        f=(WEB/p.lstrip('/')).resolve()
        if not str(f).startswith(str(WEB.resolve())) or not f.is_file():return self.send_error(404)
        b=f.read_bytes();ct='text/plain'
        if f.suffix=='.html':ct='text/html; charset=utf-8'
        elif f.suffix=='.js':ct='application/javascript; charset=utf-8'
        elif f.suffix=='.css':ct='text/css; charset=utf-8'
        self.send_response(200);self.send_header('content-type',ct);self.send_header('content-length',str(len(b)));self.hdr();self.end_headers();self.wfile.write(b)
    def do_POST(self):
        try:d=body(self)
        except Exception as e:return self.json({'error':str(e)},400)
        return self.api_post(urlparse(self.path).path,d)
    def api_get(self,p,q):
        if p=='/api/health':return self.json({'ok':True,'version':'3.1.0'})
        if not self.need():return
        c=config.load()
        if p=='/api/status':
            us=quota_view(c,db.users());ds=db.devices();gu=db.gateway_usage();gateway_bytes=int(gu.get('up_bytes',0))+int(gu.get('down_bytes',0));used=sum(int(u.get('usage_bytes') or 0) for u in us)+gateway_bytes
            return self.json({'users':us,'devices':ds,'events':db.events(80),'alerts':db.alerts(50),'daily':db.daily(),'bundle':{**c['bundle'],'used_bytes':used,'gateway_bytes':gateway_bytes},'network':c['network'],'wifi':wifi_view(c),'web':c['web'],'mac_rules':db.mac_rules(),'system':system_info(c),'dns':c.get('dns',{}),'updates':{k:v for k,v in c.get('updates',{}).items() if k!='repo'}})
        if p=='/api/settings':return self.json(mask_config(c))
        if p=='/api/diagnostics':return self.json(diagnostics.snapshot(c,((q.get('ping') or ['0'])[0]=='1')))
        if p=='/api/update/status':return self.json(update_status(c,False))
        if p=='/api/report':
            us=quota_view(c,db.users());return self.json({'users':us,'devices':db.devices(),'gateway':db.gateway_usage(),'events':db.events(100),'health':{'ok':True,'version':'3.1.0'}})
        if p=='/api/dns/rules':return self.json({'rules':db.dns_rules()})
        if p=='/api/dns/history':
            did=int((q.get('device_id') or ['0'])[0]);hours=int((q.get('hours') or ['24'])[0]);return self.json({'history':db.dns_history(did,hours,500),'top':db.dns_top(did,hours,30)})
        if p=='/api/firewall':return self.json({'rules':db.firewall_rules(),'forwards':db.port_forwards(),'dmz':c['network'].get('dmz_ip','')})
        if p=='/api/2fa/qr':
            s=c['admin'].get('totp_secret','')
            if not s:return self.json({'error':'2fa not initialized'},400)
            try:
                import qrcode;img=qrcode.make(auth.otpauth_uri(s));buf=io.BytesIO();img.save(buf,format='PNG');return self.json({'png':'data:image/png;base64,'+base64.b64encode(buf.getvalue()).decode()})
            except Exception:return self.json({'error':'QR generation unavailable'},500)
        return self.json({'error':'not found'},404)
    def api_post(self,p,d):
        global DNS
        c=config.load();ip=client_ip(self)
        if p=='/api/login':
            if BANS.get(ip,0)>time.time():return self.json({'error':'temporarily banned'},429)
            ok=auth.verify_password(str(d.get('password','')),c['admin'].get('password_hash',''))
            if ok and c['admin'].get('totp_enabled'):ok=auth.verify_totp(c['admin'].get('totp_secret',''),str(d.get('totp','')))
            if not ok:
                now=time.time();arr=[x for x in FAILS.get(ip,[]) if now-x<int(c['security'].get('failed_login_window',300))];arr.append(now);FAILS[ip]=arr;db.alert('failed-login',f'Failed dashboard login from {ip}')
                if c['security'].get('auto_ban') and len(arr)>=int(c['security'].get('failed_login_limit',8)):BANS[ip]=now+int(c['security'].get('ban_seconds',900))
                return self.json({'error':'bad credentials'},401)
            t=secrets.token_urlsafe(32);SESSIONS[t]=time.time()+28800;return self.json({'ok':True},cookie=f'qg_session={t}; Path=/; HttpOnly; SameSite=Strict')
        if not self.need():return
        try:
            if p=='/api/users':return self.json({'ok':True,'id':db.create_user(str(d['name'])[:80],float(d.get('quota_gb',0)),int(d.get('speed_down_kbit',0)),int(d.get('speed_up_kbit',0)),str(d.get('quota_mode','fixed')))})
            if p=='/api/user/update':x=dict(d);i=int(x.pop('id'));db.update_user(i,**x);apply(c);return self.json({'ok':True})
            if p=='/api/user/topup':us={u['id']:u for u in db.users()};i=int(d['id']);u=us[i];db.update_user(i,topup_gb=float(u.get('topup_gb') or 0)+float(d.get('gb',0)));return self.json({'ok':True})
            if p=='/api/user/delete':db.delete_user(int(d['id']));apply(c);return self.json({'ok':True})
            if p=='/api/device/update':x=dict(d);i=int(x.pop('id'));db.update_device(i,**x);apply(c);return self.json({'ok':True})
            if p=='/api/device/delete':
                ds={x['id']:x for x in db.devices()};i=int(d['id']);old=ds.get(i)
                if old:db.mac_rule(old['mac'],'blacklist','deleted device')
                db.delete_device(i);apply(c);return self.json({'ok':True})
            if p=='/api/mac-rule':db.mac_rule(d['mac'],d.get('action'),d.get('note',''));apply(c);return self.json({'ok':True})
            if p=='/api/network/rescan':scan(c);apply(c);return self.json({'ok':True})
            if p=='/api/policy/apply':apply(c);return self.json({'ok':True})
            if p=='/api/reset-month':db.reset_month();return self.json({'ok':True})
            if p=='/api/settings':
                for sec in ('bundle','network','guest','features','security'):
                    if sec in d and isinstance(d[sec],dict):c[sec].update(d[sec])
                if 'bundle' in d:
                    mode=str(c['bundle'].get('bundle_type','renew_day'))
                    if mode not in ('renew_day','end_of_month'):raise ValueError('invalid bundle_type')
                    rd=int(c['bundle'].get('reset_day',1))
                    if mode=='renew_day' and not 1<=rd<=28:raise ValueError('reset_day must be 1..28')
                config.save(c);apply(c);return self.json({'ok':True})
            if p=='/api/dns/settings':
                x=d if isinstance(d,dict) else {}
                if 'history_days' in x:c['dns']['history_days']=max(1,min(int(x['history_days']),90))
                if 'max_history_rows' in x:c['dns']['max_history_rows']=max(1000,min(int(x['max_history_rows']),1000000))
                if 'family_mode' in x:c['dns']['family_mode']=bool(x['family_mode'])
                if isinstance(x.get('presets'),dict):
                    for k in ('ads_tracking','social','streaming','gambling','adult'):
                        if k in x['presets']:c['dns']['presets'][k]=bool(x['presets'][k])
                config.save(c)
                if DNS:DNS.update_config(c)
                return self.json({'ok':True,'dns':c['dns']})
            if p=='/api/wifi':
                w=dict(c['wifi'])
                if 'ssid' in d:w['ssid']=str(d.get('ssid',''))
                if 'hidden' in d:
                    if not isinstance(d['hidden'],bool):raise ValueError('hidden must be true or false')
                    w['hidden']=d['hidden']
                if 'channel' in d:w['channel']=int(d['channel'])
                new_pw=d.get('password',None)
                if new_pw is not None and str(new_pw)!='':w['passphrase']=str(new_pw)
                network.validate_wifi_settings(w);c['wifi']=w;config.save(c);snapshot=json.loads(json.dumps(c));threading.Thread(target=restart_wifi_after_save,args=(snapshot,),daemon=True).start();return self.json({'ok':True,'wifi_restart':True,'wifi':wifi_view(c)})
            if p=='/api/password':
                pw=str(d.get('password',''))
                if len(pw)<8:return self.json({'error':'8+ chars required'},400)
                c['admin']['password_hash']=auth.hash_password(pw);config.save(c);return self.json({'ok':True})
            if p=='/api/2fa/setup':c['admin']['totp_secret']=auth.new_totp_secret();c['admin']['totp_enabled']=False;config.save(c);return self.json({'ok':True})
            if p=='/api/2fa/enable':
                if not auth.verify_totp(c['admin'].get('totp_secret',''),str(d.get('code',''))):return self.json({'error':'invalid code'},400)
                c['admin']['totp_enabled']=True;config.save(c);return self.json({'ok':True})
            if p=='/api/2fa/disable':c['admin']['totp_enabled']=False;c['admin']['totp_secret']='';config.save(c);return self.json({'ok':True})
            if p=='/api/dns/rule/add':db.add_dns_rule(d.get('scope_type','global'),int(d.get('scope_id',0)),d['domain'],d.get('action','block'),d.get('target',''));return self.json({'ok':True})
            if p=='/api/dns/rule/delete':db.del_dns_rule(int(d['id']));return self.json({'ok':True})
            if p=='/api/firewall/rule/add':db.add_firewall_rule(d);apply(c);return self.json({'ok':True})
            if p=='/api/firewall/rule/update':x=dict(d);i=int(x.pop('id'));db.update_firewall_rule(i,x);apply(c);return self.json({'ok':True})
            if p=='/api/firewall/rule/delete':db.del_firewall_rule(int(d['id']));apply(c);return self.json({'ok':True})
            if p=='/api/firewall/forward/add':db.add_port_forward(d);apply(c);return self.json({'ok':True})
            if p=='/api/firewall/forward/update':x=dict(d);i=int(x.pop('id'));db.update_port_forward(i,x);apply(c);return self.json({'ok':True})
            if p=='/api/firewall/forward/delete':db.del_port_forward(int(d['id']));apply(c);return self.json({'ok':True})
            if p=='/api/firewall/dmz':c['network']['dmz_ip']=str(d.get('ip',''));config.save(c);apply(c);return self.json({'ok':True})
            if p=='/api/alerts/seen':db.mark_alerts_seen();return self.json({'ok':True})
            if p=='/api/https/enable':subprocess.run(['/usr/local/sbin/quotagate-make-cert'],check=True);c['web']['https']=True;config.save(c);return self.json({'ok':True,'restart_required':True})
            if p=='/api/https/disable':c['web']['https']=False;config.save(c);return self.json({'ok':True,'restart_required':True})
            if p=='/api/update/check':return self.json(update_status(c,True))
            if p=='/api/update/settings':c['updates']['auto_check']=bool(d.get('auto_check',False));config.save(c);return self.json({'ok':True,'auto_check':c['updates']['auto_check']})
            if p=='/api/update/install':
                exe='/usr/local/sbin/quotagate-update'
                if not Path(exe).exists():return self.json({'error':'quotagate-update is not installed; run install.sh from this release once'},400)
                subprocess.Popen([exe],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True);return self.json({'ok':True,'started':True})
            if p=='/api/wan/test':
                if not c['wan'].get('pppoe_user'):return self.json({'error':'PPPoE credentials not configured'},400)
                return self.json({'ok':True,'message':'PPPoE test remains intentionally manual on antiX; run quotagate-pppoe-test locally.'})
        except Exception as e:db.event(f'API {p}: {e}','error');return self.json({'error':str(e)},400)
        return self.json({'error':'not found'},404)

def main():
    global DNS,PREV_GATEWAY
    if os.geteuid()!=0:raise SystemExit('QuotaGate must run as root')
    db.init();c=config.load()
    try:network.runtime(c);network.start_network_daemons(c)
    except Exception as e:db.event('network startup: '+str(e),'error')
    try:apply(c);PREV_GATEWAY=network.gateway_counters()
    except Exception as e:db.event('policy startup: '+str(e),'error')
    if c.get('features',{}).get('dns_proxy',True):
        try:DNS=DNSProxy(c);DNS.start();db.event('DNS proxy listening on '+c['network']['lan_ip']+':53')
        except Exception as e:db.event('DNS proxy: '+str(e),'error')
    threading.Thread(target=worker,daemon=True).start();host,port=c['web']['host'],int(c['web']['port']);srv=ThreadingHTTPServer((host,port),Handler)
    if c['web'].get('https'):
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(c['web']['cert'],c['web']['key']);srv.socket=ctx.wrap_socket(srv.socket,server_side=True)
    db.event(f'QuotaGate 3.1 started on {host}:{port} as uid 0');srv.serve_forever()

if __name__=='__main__':main()
