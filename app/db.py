from __future__ import annotations
import sqlite3, threading, time, json, os
from pathlib import Path

DB = Path('/var/lib/quotagate/quotagate.db')
L = threading.RLock()

SCHEMA = r'''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL UNIQUE,
 quota_mode TEXT NOT NULL DEFAULT 'fixed',
 quota_gb REAL NOT NULL DEFAULT 0,
 topup_gb REAL NOT NULL DEFAULT 0,
 speed_down_kbit INTEGER NOT NULL DEFAULT 0,
 speed_up_kbit INTEGER NOT NULL DEFAULT 0,
 exempt INTEGER NOT NULL DEFAULT 0,
 enabled INTEGER NOT NULL DEFAULT 1,
 history_days INTEGER NOT NULL DEFAULT 7,
 dns_server TEXT NOT NULL DEFAULT '',
 created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS devices(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 mac TEXT NOT NULL UNIQUE,
 ip TEXT NOT NULL DEFAULT '',
 user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
 exempt INTEGER NOT NULL DEFAULT 0,
 blocked_manual INTEGER NOT NULL DEFAULT 0,
 enabled INTEGER NOT NULL DEFAULT 0,
 is_guest INTEGER NOT NULL DEFAULT 0,
 speed_down_kbit INTEGER NOT NULL DEFAULT 0,
 speed_up_kbit INTEGER NOT NULL DEFAULT 0,
 dns_server TEXT NOT NULL DEFAULT '',
 manufacturer TEXT NOT NULL DEFAULT '',
 last_seen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage_monthly(
 device_id INTEGER NOT NULL,
 period TEXT NOT NULL,
 up_bytes INTEGER NOT NULL DEFAULT 0,
 down_bytes INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(device_id, period)
);
CREATE TABLE IF NOT EXISTS usage_daily(
 device_id INTEGER NOT NULL,
 day TEXT NOT NULL,
 up_bytes INTEGER NOT NULL DEFAULT 0,
 down_bytes INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(device_id, day)
);
CREATE TABLE IF NOT EXISTS events(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 ts INTEGER NOT NULL,
 level TEXT NOT NULL,
 message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 ts INTEGER NOT NULL,
 kind TEXT NOT NULL,
 message TEXT NOT NULL,
 seen INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mac_rules(
 mac TEXT PRIMARY KEY,
 action TEXT NOT NULL,
 note TEXT NOT NULL DEFAULT '',
 created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS dns_rules(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 scope_type TEXT NOT NULL,
 scope_id INTEGER NOT NULL DEFAULT 0,
 domain TEXT NOT NULL,
 action TEXT NOT NULL,
 target TEXT NOT NULL DEFAULT '',
 enabled INTEGER NOT NULL DEFAULT 1,
 UNIQUE(scope_type, scope_id, domain, action)
);
CREATE TABLE IF NOT EXISTS dns_history(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 ts INTEGER NOT NULL,
 device_id INTEGER,
 client_ip TEXT NOT NULL,
 domain TEXT NOT NULL,
 qtype TEXT NOT NULL DEFAULT 'A',
 action TEXT NOT NULL DEFAULT 'allow'
);
CREATE INDEX IF NOT EXISTS idx_dns_hist_ts ON dns_history(ts);
CREATE INDEX IF NOT EXISTS idx_dns_hist_dev ON dns_history(device_id, ts);
CREATE TABLE IF NOT EXISTS firewall_rules(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 direction TEXT NOT NULL DEFAULT 'forward',
 src TEXT NOT NULL DEFAULT '',
 dst TEXT NOT NULL DEFAULT '',
 proto TEXT NOT NULL DEFAULT 'any',
 sport TEXT NOT NULL DEFAULT '',
 dport TEXT NOT NULL DEFAULT '',
 action TEXT NOT NULL DEFAULT 'accept',
 enabled INTEGER NOT NULL DEFAULT 1,
 priority INTEGER NOT NULL DEFAULT 100
);
CREATE TABLE IF NOT EXISTS port_forwards(
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 name TEXT NOT NULL,
 proto TEXT NOT NULL DEFAULT 'tcp',
 external_port INTEGER NOT NULL,
 internal_ip TEXT NOT NULL,
 internal_port INTEGER NOT NULL,
 enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS settings(
 key TEXT PRIMARY KEY,
 value TEXT NOT NULL
);
'''

def con():
    DB.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(DB.parent, 0o700)
    c = sqlite3.connect(DB, timeout=10, check_same_thread=False)
    if DB.exists(): os.chmod(DB, 0o600)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=10000')
    return c

def init():
    with L, con() as c:
        c.executescript(SCHEMA)
        cols = {r['name'] for r in c.execute('PRAGMA table_info(users)')}
        for name, ddl in {
            'quota_mode': "ALTER TABLE users ADD COLUMN quota_mode TEXT NOT NULL DEFAULT 'fixed'",
            'topup_gb': "ALTER TABLE users ADD COLUMN topup_gb REAL NOT NULL DEFAULT 0",
            'enabled': "ALTER TABLE users ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1",
            'history_days': "ALTER TABLE users ADD COLUMN history_days INTEGER NOT NULL DEFAULT 7",
            'dns_server': "ALTER TABLE users ADD COLUMN dns_server TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in cols: c.execute(ddl)
        dcols = {r['name'] for r in c.execute('PRAGMA table_info(devices)')}
        for name, ddl in {
            'enabled': "ALTER TABLE devices ADD COLUMN enabled INTEGER NOT NULL DEFAULT 0",
            'is_guest': "ALTER TABLE devices ADD COLUMN is_guest INTEGER NOT NULL DEFAULT 0",
            'dns_server': "ALTER TABLE devices ADD COLUMN dns_server TEXT NOT NULL DEFAULT ''",
            'manufacturer': "ALTER TABLE devices ADD COLUMN manufacturer TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in dcols: c.execute(ddl)
        c.execute("INSERT INTO settings(key,value) VALUES('schema_version','3') ON CONFLICT(key) DO NOTHING")

def period(ts=None): return time.strftime('%Y-%m', time.localtime(ts or time.time()))
def day(ts=None): return time.strftime('%Y-%m-%d', time.localtime(ts or time.time()))

def event(message, level='info'):
    with L, con() as c:
        c.execute('INSERT INTO events(ts,level,message) VALUES(?,?,?)',(int(time.time()), level, str(message)[:2000]))

def alert(kind, message):
    with L, con() as c:
        c.execute('INSERT INTO alerts(ts,kind,message) VALUES(?,?,?)',(int(time.time()), str(kind)[:50], str(message)[:1000]))

def events(n=200):
    with con() as c: return [dict(r) for r in c.execute('SELECT * FROM events ORDER BY id DESC LIMIT ?',(int(n),))]
def alerts(n=100, unseen_only=False):
    q='SELECT * FROM alerts' + (' WHERE seen=0' if unseen_only else '') + ' ORDER BY id DESC LIMIT ?'
    with con() as c: return [dict(r) for r in c.execute(q,(int(n),))]
def mark_alerts_seen():
    with L, con() as c: c.execute('UPDATE alerts SET seen=1')

def users():
    q='''SELECT u.*, COALESCE(SUM(m.up_bytes+m.down_bytes),0) usage_bytes,
                COUNT(DISTINCT d.id) device_count
         FROM users u
         LEFT JOIN devices d ON d.user_id=u.id
         LEFT JOIN usage_monthly m ON m.device_id=d.id AND m.period=?
         GROUP BY u.id ORDER BY u.name'''
    with con() as c: return [dict(r) for r in c.execute(q,(period(),))]

def user_by_id(i):
    with con() as c:
        r=c.execute('SELECT * FROM users WHERE id=?',(int(i),)).fetchone(); return dict(r) if r else None

def devices():
    q='''SELECT d.*,u.name user_name,
                COALESCE(m.up_bytes,0) up_bytes,COALESCE(m.down_bytes,0) down_bytes
         FROM devices d
         LEFT JOIN users u ON u.id=d.user_id
         LEFT JOIN usage_monthly m ON m.device_id=d.id AND m.period=?
         ORDER BY d.last_seen DESC,d.name'''
    with con() as c: return [dict(r) for r in c.execute(q,(period(),))]

def device_by_ip(ip):
    with con() as c:
        r=c.execute('SELECT * FROM devices WHERE ip=? ORDER BY last_seen DESC LIMIT 1',(ip,)).fetchone()
        return dict(r) if r else None

def upsert_device(mac, ip, name=None, manufacturer=''):
    mac=mac.lower(); now=int(time.time())
    with L, con() as c:
        r=c.execute('SELECT id,name FROM devices WHERE mac=?',(mac,)).fetchone()
        if r:
            new_name=(name or '').strip()
            if new_name and (not r['name'] or r['name']==mac):
                c.execute('UPDATE devices SET ip=?,last_seen=?,name=?,manufacturer=CASE WHEN manufacturer="" THEN ? ELSE manufacturer END WHERE id=?',(ip,now,new_name,manufacturer,r['id']))
            else:
                c.execute('UPDATE devices SET ip=?,last_seen=?,manufacturer=CASE WHEN manufacturer="" THEN ? ELSE manufacturer END WHERE id=?',(ip,now,manufacturer,r['id']))
            return r['id'], False
        i=c.execute('INSERT INTO devices(name,mac,ip,manufacturer,last_seen) VALUES(?,?,?,?,?)',(name or mac,mac,ip,manufacturer,now)).lastrowid
        return i, True

def add_usage(i, up, down):
    if up<0 or down<0: return
    if not up and not down:return
    with L, con() as c:
        for table,key,val in (('usage_monthly','period',period()),('usage_daily','day',day())):
            q=f'''INSERT INTO {table}(device_id,{key},up_bytes,down_bytes) VALUES(?,?,?,?)
                  ON CONFLICT(device_id,{key}) DO UPDATE SET
                    up_bytes=up_bytes+excluded.up_bytes, down_bytes=down_bytes+excluded.down_bytes'''
            c.execute(q,(int(i),val,int(up),int(down)))

def create_user(name, quota_gb=0, down=0, up=0, quota_mode='fixed'):
    mode=str(quota_mode).lower()
    if mode=='shared':mode='auto'
    if mode not in ('fixed','auto','disabled'): raise ValueError('quota_mode must be fixed, auto or disabled')
    with L, con() as c:
        return c.execute('''INSERT INTO users(name,quota_mode,quota_gb,speed_down_kbit,speed_up_kbit,created_at)
                            VALUES(?,?,?,?,?,?)''',(name,mode,float(quota_gb),int(down),int(up),int(time.time()))).lastrowid

def _update(table,i,kw,allowed):
    fields=[]; vals=[]
    for k,v in kw.items():
        if k in allowed:
            if table=='users' and k=='quota_mode':
                v=str(v).lower(); v='auto' if v=='shared' else v
                if v not in ('fixed','auto','disabled'): raise ValueError('invalid quota mode')
            fields.append(k+'=?'); vals.append(v)
    if fields:
        vals.append(int(i))
        with L, con() as c: c.execute(f"UPDATE {table} SET {','.join(fields)} WHERE id=?", vals)

def update_device(i, **kw):
    _update('devices',i,kw,{'name','user_id','exempt','blocked_manual','enabled','is_guest','speed_down_kbit','speed_up_kbit','dns_server','ip','manufacturer'})
def update_user(i, **kw):
    _update('users',i,kw,{'name','quota_mode','quota_gb','topup_gb','speed_down_kbit','speed_up_kbit','exempt','enabled','history_days','dns_server'})
def delete_user(i):
    with L, con() as c: c.execute('DELETE FROM users WHERE id=?',(int(i),))
def delete_device(i):
    with L, con() as c:
        c.execute('DELETE FROM usage_monthly WHERE device_id=?',(int(i),)); c.execute('DELETE FROM usage_daily WHERE device_id=?',(int(i),)); c.execute('DELETE FROM devices WHERE id=?',(int(i),))

def reset_month():
    with L, con() as c:
        c.execute('DELETE FROM usage_monthly WHERE period=?',(period(),))
        c.execute('UPDATE users SET topup_gb=0')
    event('Monthly counters reset','warning')

def daily(n=31):
    with con() as c:
        return [dict(r) for r in c.execute('SELECT day,SUM(up_bytes+down_bytes) bytes FROM usage_daily GROUP BY day ORDER BY day DESC LIMIT ?',(int(n),))]

def mac_rule(mac, action=None, note=''):
    mac=mac.lower()
    with L, con() as c:
        if action is None:
            r=c.execute('SELECT * FROM mac_rules WHERE mac=?',(mac,)).fetchone(); return dict(r) if r else None
        if action=='delete': c.execute('DELETE FROM mac_rules WHERE mac=?',(mac,)); return
        if action not in ('whitelist','blacklist'): raise ValueError('invalid MAC rule action')
        c.execute('''INSERT INTO mac_rules(mac,action,note,created_at) VALUES(?,?,?,?)
                     ON CONFLICT(mac) DO UPDATE SET action=excluded.action,note=excluded.note''',(mac,action,note,int(time.time())))
def mac_rules():
    with con() as c: return [dict(r) for r in c.execute('SELECT * FROM mac_rules ORDER BY created_at DESC')]

def dns_rules():
    with con() as c: return [dict(r) for r in c.execute('SELECT * FROM dns_rules ORDER BY id DESC')]
def add_dns_rule(scope_type, scope_id, domain, action, target=''):
    scope_type=str(scope_type).lower(); action=str(action).lower(); domain=domain.strip().lower().rstrip('.')
    if scope_type not in ('global','user','device'): raise ValueError('invalid DNS scope')
    if action not in ('block','allow','redirect'): raise ValueError('invalid DNS action')
    if not domain or len(domain)>253: raise ValueError('invalid domain')
    with L, con() as c:
        return c.execute('INSERT OR REPLACE INTO dns_rules(scope_type,scope_id,domain,action,target,enabled) VALUES(?,?,?,?,?,1)',(scope_type,int(scope_id or 0),domain,action,target)).lastrowid
def del_dns_rule(i):
    with L, con() as c: c.execute('DELETE FROM dns_rules WHERE id=?',(int(i),))
def log_dns(client_ip, domain, qtype='A', action='allow'):
    d=device_by_ip(client_ip); did=d['id'] if d else None
    with L, con() as c:
        c.execute('INSERT INTO dns_history(ts,device_id,client_ip,domain,qtype,action) VALUES(?,?,?,?,?,?)',(int(time.time()),did,client_ip,domain[:253],qtype[:10],action[:20]))
def prune_dns(default_days=7,max_rows=100000):
    now=int(time.time()); days=max(1,min(int(default_days),90)); max_rows=max(1000,int(max_rows))
    with L, con() as c:
        c.execute('DELETE FROM dns_history WHERE ts<?',(now-days*86400,))
        r=c.execute('SELECT COUNT(*) n FROM dns_history').fetchone()
        extra=max(0,int(r['n'])-max_rows)
        if extra:c.execute('DELETE FROM dns_history WHERE id IN (SELECT id FROM dns_history ORDER BY id ASC LIMIT ?)',(extra,))
def dns_history(device_id=0, hours=24, limit=500):
    since=int(time.time())-max(1,min(int(hours),336))*3600; limit=max(1,min(int(limit),1000))
    with con() as c:
        if int(device_id or 0):
            rows=c.execute('''SELECT h.*,d.name device_name,d.user_id,u.name user_name FROM dns_history h
                              LEFT JOIN devices d ON d.id=h.device_id LEFT JOIN users u ON u.id=d.user_id
                              WHERE h.device_id=? AND h.ts>=? ORDER BY h.id DESC LIMIT ?''',(int(device_id),since,limit))
        else:
            rows=c.execute('''SELECT h.*,d.name device_name,d.user_id,u.name user_name FROM dns_history h
                              LEFT JOIN devices d ON d.id=h.device_id LEFT JOIN users u ON u.id=d.user_id
                              WHERE h.ts>=? ORDER BY h.id DESC LIMIT ?''',(since,limit))
        return [dict(r) for r in rows]
def dns_top(device_id=0, hours=24, limit=30):
    since=int(time.time())-max(1,min(int(hours),336))*3600; args=[since]; where='ts>=?'
    if int(device_id or 0): where+=' AND device_id=?'; args.append(int(device_id))
    args.append(max(1,min(int(limit),100)))
    with con() as c:
        return [dict(r) for r in c.execute(f'SELECT domain,COUNT(*) hits FROM dns_history WHERE {where} GROUP BY domain ORDER BY hits DESC LIMIT ?',args)]

def firewall_rules():
    with con() as c: return [dict(r) for r in c.execute('SELECT * FROM firewall_rules ORDER BY priority,id')]
def add_firewall_rule(d):
    with L, con() as c:
        return c.execute('''INSERT INTO firewall_rules(name,direction,src,dst,proto,sport,dport,action,enabled,priority)
        VALUES(?,?,?,?,?,?,?,?,?,?)''',(d.get('name','rule'),d.get('direction','forward'),d.get('src',''),d.get('dst',''),d.get('proto','any'),d.get('sport',''),d.get('dport',''),d.get('action','accept'),1 if d.get('enabled',True) else 0,int(d.get('priority',100)))).lastrowid
def update_firewall_rule(i,d):
    _update('firewall_rules',i,d,{'name','direction','src','dst','proto','sport','dport','action','enabled','priority'})
def del_firewall_rule(i):
    with L, con() as c: c.execute('DELETE FROM firewall_rules WHERE id=?',(int(i),))
def port_forwards():
    with con() as c: return [dict(r) for r in c.execute('SELECT * FROM port_forwards ORDER BY id DESC')]
def add_port_forward(d):
    with L, con() as c:
        return c.execute('INSERT INTO port_forwards(name,proto,external_port,internal_ip,internal_port,enabled) VALUES(?,?,?,?,?,?)',(d.get('name','forward'),d.get('proto','tcp'),int(d['external_port']),d['internal_ip'],int(d.get('internal_port',d['external_port'])),1 if d.get('enabled',True) else 0)).lastrowid
def update_port_forward(i,d):
    _update('port_forwards',i,d,{'name','proto','external_port','internal_ip','internal_port','enabled'})
def del_port_forward(i):
    with L, con() as c: c.execute('DELETE FROM port_forwards WHERE id=?',(int(i),))

def get_setting(key, default=None):
    with con() as c:
        r=c.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        if not r: return default
        try: return json.loads(r['value'])
        except Exception: return r['value']
def set_setting(key, value):
    with L, con() as c:
        c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value)))
