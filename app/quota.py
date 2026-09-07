from __future__ import annotations
import calendar, datetime as dt, math, threading, time
from . import config, db, network

_INSTALLED=False
_ORIG_INIT=None
_ORIG_USERS=None
_ORIG_CREATE_USER=None
_ORIG_UPDATE_USER=None
_ORIG_ADD_USAGE_BATCH=None
_ORIG_RESET_MONTH=None
_ORIG_EFFECTIVE_QUOTA=None
_LOCK=threading.RLock()
_CACHE={}
_MAX_QUOTA_GB=100000.0

_SCHEMA=r'''
CREATE TABLE IF NOT EXISTS user_usage_cycle(
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 cycle_key TEXT NOT NULL,
 up_bytes INTEGER NOT NULL DEFAULT 0,
 down_bytes INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(user_id, cycle_key)
);
CREATE TABLE IF NOT EXISTS user_quota_state(
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 cycle_key TEXT NOT NULL,
 baseline_bytes INTEGER NOT NULL DEFAULT 0,
 last_threshold INTEGER NOT NULL DEFAULT 0,
 quota_blocked INTEGER NOT NULL DEFAULT 0,
 reset_at INTEGER NOT NULL DEFAULT 0,
 updated_at INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(user_id, cycle_key)
);
CREATE INDEX IF NOT EXISTS idx_user_usage_cycle_key ON user_usage_cycle(cycle_key);
'''


def cycle_key(c, ts=None):
    now=dt.datetime.fromtimestamp(ts or time.time())
    b=c.get('bundle',{})
    if str(b.get('bundle_type','renew_day'))=='end_of_month':
        return now.replace(day=1,hour=0,minute=0,second=0,microsecond=0).strftime('%Y-%m-%d')
    day=max(1,min(int(b.get('reset_day',1) or 1),28))
    if now.day>=day:
        start=now.replace(day=day,hour=0,minute=0,second=0,microsecond=0)
    else:
        y=now.year; m=now.month-1
        if m==0:y-=1;m=12
        start=now.replace(year=y,month=m,day=day,hour=0,minute=0,second=0,microsecond=0)
    return start.strftime('%Y-%m-%d')


def _finite_gb(v,name):
    x=float(v)
    if not math.isfinite(x) or x<0 or x>_MAX_QUOTA_GB:raise ValueError(f'{name} must be between 0 and {_MAX_QUOTA_GB:g} GB')
    return x


def effective_quota(user, users, c):
    if not user:return 0.0
    mode=str(user.get('quota_mode','fixed')).lower();mode='auto' if mode=='shared' else mode
    if mode in ('disabled','unlimited'):return 0.0
    if mode=='auto':
        fixed=sum(float(x.get('quota_gb') or 0)+float(x.get('topup_gb') or 0) for x in users if str(x.get('quota_mode','fixed')).lower() not in ('shared','auto','disabled','unlimited') and x.get('enabled'))
        auto=[x for x in users if str(x.get('quota_mode','')).lower() in ('shared','auto') and x.get('enabled')]
        base=max(0.0,float(c['bundle']['total_gb'])-fixed)/max(1,len(auto))
        return base+float(user.get('topup_gb') or 0)
    return max(0.0,float(user.get('quota_gb') or 0)+float(user.get('topup_gb') or 0))


def _create_schema():
    with db.L,db.con() as c:c.executescript(_SCHEMA)


def _bootstrap_cycle(c):
    key=cycle_key(c);today=db.day()
    with db.L,db.con() as cx:
        existing=cx.execute('SELECT 1 FROM user_usage_cycle WHERE cycle_key=? LIMIT 1',(key,)).fetchone()
        if existing:return False
        rows=cx.execute('''SELECT d.user_id,SUM(u.up_bytes) up_bytes,SUM(u.down_bytes) down_bytes
                           FROM usage_daily u JOIN devices d ON d.id=u.device_id
                           WHERE d.user_id IS NOT NULL AND u.day>=? AND u.day<=?
                           GROUP BY d.user_id''',(key,today)).fetchall()
        for r in rows:
            cx.execute('INSERT OR IGNORE INTO user_usage_cycle(user_id,cycle_key,up_bytes,down_bytes) VALUES(?,?,?,?)',(int(r['user_id']),key,int(r['up_bytes'] or 0),int(r['down_bytes'] or 0)))
    if rows:
        try:db.event(f'User quota accounting bootstrapped for cycle {key} from existing device history','info')
        except Exception:pass
    return bool(rows)


def _patched_init():
    _ORIG_INIT();_create_schema()
    try:
        c=config.load();_bootstrap_cycle(c);reconcile_all(c,refresh=False)
    except Exception as e:
        try:db.event('Quota initialization: '+str(e),'error')
        except Exception:pass


def _raw_totals(key):
    with db.con() as c:return {int(r['user_id']):int(r['up_bytes'])+int(r['down_bytes']) for r in c.execute('SELECT user_id,up_bytes,down_bytes FROM user_usage_cycle WHERE cycle_key=?',(key,))}


def _states(key):
    with db.con() as c:return {int(r['user_id']):dict(r) for r in c.execute('SELECT * FROM user_quota_state WHERE cycle_key=?',(key,))}


def decorate_users(c, users):
    key=cycle_key(c);totals=_raw_totals(key);states=_states(key);out=[]
    for base in users:
        u=dict(base);uid=int(u['id']);mode=str(u.get('quota_mode','fixed')).lower();mode='auto' if mode=='shared' else mode
        st=states.get(uid,{})
        raw=int(totals.get(uid,0));baseline=max(0,int(st.get('baseline_bytes',0) or 0));used=max(0,raw-baseline)
        q=float(effective_quota(u,users,c));qb=max(0,int(q*1024**3));exempt=bool(u.get('exempt'))
        exceeded=bool(u.get('enabled')) and not exempt and mode not in ('disabled','unlimited') and qb>0 and used>=qb
        pct=(used/qb*100.0) if qb>0 else 0.0
        if not u.get('enabled') or mode=='disabled':status='disabled'
        elif exempt or mode=='unlimited':status='unlimited'
        elif exceeded:status='exceeded'
        elif pct>=90:status='critical'
        elif pct>=80:status='warning'
        else:status='active'
        u.update({
            'usage_bytes':used,
            'quota_used_bytes':used,
            'quota_raw_cycle_bytes':raw,
            'quota_baseline_bytes':baseline,
            'effective_quota_gb':q,
            'quota_bytes':qb,
            'quota_remaining_bytes':None if mode=='unlimited' or exempt else max(0,qb-used),
            'quota_percent':round(pct,2),
            'quota_exceeded':bool(exceeded),
            'quota_status':status,
            'quota_cycle_key':key,
        });out.append(u)
    with _LOCK:
        global _CACHE
        _CACHE={int(u['id']):dict(u) for u in out}
    return out


def users():
    return decorate_users(config.load(),_ORIG_USERS())


def _threshold(u):
    if u.get('quota_status') in ('disabled','unlimited') or not int(u.get('quota_bytes') or 0):return 0
    p=float(u.get('quota_percent') or 0)
    if p>=100:return 100
    if p>=90:return 90
    if p>=80:return 80
    return 0


def _refresh_policy(c):
    us=users();ds=db.devices();network.sync_rules(c,ds,network.blocked(c,us,ds))


def reconcile_users(c, user_ids, refresh=True):
    ids={int(x) for x in user_ids if x is not None}
    if not ids:return False
    with _LOCK:
        decorated={int(u['id']):u for u in decorate_users(c,_ORIG_USERS()) if int(u['id']) in ids}
        key=cycle_key(c);now=int(time.time());changed=False;alerts=[];events=[]
        with db.L,db.con() as cx:
            for uid,u in decorated.items():
                r=cx.execute('SELECT * FROM user_quota_state WHERE user_id=? AND cycle_key=?',(uid,key)).fetchone()
                old_block=bool(r['quota_blocked']) if r else False;old_thr=int(r['last_threshold']) if r else 0
                new_block=bool(u.get('quota_exceeded'));new_thr=_threshold(u)
                cx.execute('''INSERT INTO user_quota_state(user_id,cycle_key,baseline_bytes,last_threshold,quota_blocked,updated_at)
                              VALUES(?,?,?,?,?,?)
                              ON CONFLICT(user_id,cycle_key) DO UPDATE SET last_threshold=excluded.last_threshold,quota_blocked=excluded.quota_blocked,updated_at=excluded.updated_at''',(uid,key,int(r['baseline_bytes']) if r else 0,new_thr,1 if new_block else 0,now))
                for t in (80,90,100):
                    if old_thr<t<=new_thr:
                        if t==80:alerts.append(('quota-warning',f"User {u['name']} crossed 80% quota"))
                        elif t==90:alerts.append(('quota-critical',f"User {u['name']} crossed 90% quota"))
                        else:alerts.append(('quota-exceeded',f"User {u['name']} exceeded quota: {float(u.get('effective_quota_gb') or 0):.2f} GB"))
                if old_block!=new_block:
                    changed=True
                    if new_block:events.append((f"User {u['name']} exceeded quota; Internet blocked",'warning'))
                    else:events.append((f"User {u['name']} Internet restored after quota update",'info'))
        for kind,msg in alerts:
            try:db.alert(kind,msg)
            except Exception:pass
        for msg,level in events:
            try:db.event(msg,level)
            except Exception:pass
        if changed and refresh:
            try:_refresh_policy(c)
            except Exception as e:
                try:db.event('Quota policy refresh failed: '+str(e),'error')
                except Exception:pass
        return changed


def reconcile_all(c=None,refresh=True):
    c=c or config.load();return reconcile_users(c,[u['id'] for u in _ORIG_USERS()],refresh)


def _patched_add_usage_batch(rows):
    clean=[(int(i),max(0,int(up)),max(0,int(down))) for i,up,down in rows if int(up)>0 or int(down)>0]
    if not clean:return
    c=config.load();key=cycle_key(c);ids=sorted({i for i,_,_ in clean});ph=','.join('?' for _ in ids);affected=set()
    with db.L,db.con() as cx:
        owner={int(r['id']):r['user_id'] for r in cx.execute(f'SELECT id,user_id FROM devices WHERE id IN ({ph})',ids)} if ids else {}
        agg={}
        for did,up,down in clean:
            db._usage_upsert(cx,'usage_monthly','period',did,up,down);db._usage_upsert(cx,'usage_daily','day',did,up,down)
            uid=owner.get(did)
            if uid is not None:
                uid=int(uid);a=agg.setdefault(uid,[0,0]);a[0]+=up;a[1]+=down;affected.add(uid)
        for uid,(up,down) in agg.items():
            cx.execute('''INSERT INTO user_usage_cycle(user_id,cycle_key,up_bytes,down_bytes) VALUES(?,?,?,?)
                          ON CONFLICT(user_id,cycle_key) DO UPDATE SET up_bytes=up_bytes+excluded.up_bytes,down_bytes=down_bytes+excluded.down_bytes''',(uid,key,int(up),int(down)))
    if affected:reconcile_users(c,affected,refresh=True)


def _patched_create_user(name,quota_gb=0,down=0,up=0,quota_mode='fixed'):
    q=_finite_gb(quota_gb,'quota_gb');mode=str(quota_mode).lower();mode='auto' if mode=='shared' else mode
    if mode not in ('fixed','auto','unlimited','disabled'):raise ValueError('quota_mode must be fixed, auto, unlimited or disabled')
    if mode=='unlimited':
        uid=_ORIG_CREATE_USER(name,q,down,up,'fixed')
        with db.L,db.con() as c:c.execute("UPDATE users SET quota_mode='unlimited' WHERE id=?",(int(uid),))
        return uid
    return _ORIG_CREATE_USER(name,q,down,up,mode)


def _patched_update_user(i,**kw):
    uid=int(i);x=dict(kw);cur=db.user_by_id(uid)
    if not cur:raise ValueError('user not found')
    if 'quota_gb' in x:x['quota_gb']=_finite_gb(x['quota_gb'],'quota_gb')
    if 'topup_gb' in x:x['topup_gb']=_finite_gb(x['topup_gb'],'topup_gb')
    mode=x.get('quota_mode',None)
    if mode is not None:
        mode=str(mode).lower();mode='auto' if mode=='shared' else mode
        if mode not in ('fixed','auto','unlimited','disabled'):raise ValueError('invalid quota mode')
        x['quota_mode']=mode
    if x.get('quota_mode')=='unlimited':
        x.pop('quota_mode');_ORIG_UPDATE_USER(uid,**x)
        with db.L,db.con() as c:c.execute("UPDATE users SET quota_mode='unlimited' WHERE id=?",(uid,))
    else:_ORIG_UPDATE_USER(uid,**x)
    if {'quota_mode','quota_gb','topup_gb','enabled','exempt'}.intersection(kw):
        reconcile_users(config.load(),[uid],refresh=True)


def reset_user(user_id):
    uid=int(user_id);u=db.user_by_id(uid)
    if not u:raise ValueError('user not found')
    c=config.load();key=cycle_key(c);now=int(time.time())
    with db.L,db.con() as cx:
        r=cx.execute('SELECT up_bytes,down_bytes FROM user_usage_cycle WHERE user_id=? AND cycle_key=?',(uid,key)).fetchone();total=int(r['up_bytes']+r['down_bytes']) if r else 0
        s=cx.execute('SELECT quota_blocked FROM user_quota_state WHERE user_id=? AND cycle_key=?',(uid,key)).fetchone();old=1 if s and s['quota_blocked'] else 0
        cx.execute('''INSERT INTO user_quota_state(user_id,cycle_key,baseline_bytes,last_threshold,quota_blocked,reset_at,updated_at)
                      VALUES(?,?,?,?,?,?,?)
                      ON CONFLICT(user_id,cycle_key) DO UPDATE SET baseline_bytes=excluded.baseline_bytes,last_threshold=0,reset_at=excluded.reset_at,updated_at=excluded.updated_at''',(uid,key,total,0,old,now,now))
    try:db.event(f"User {u['name']} quota usage reset",'warning')
    except Exception:pass
    reconcile_users(c,[uid],refresh=True)
    return snapshot_user(uid)


def _patched_reset_month():
    _ORIG_RESET_MONTH();c=config.load();key=cycle_key(c);now=int(time.time());raw=_raw_totals(key)
    with db.L,db.con() as cx:
        for u in _ORIG_USERS():
            uid=int(u['id']);total=int(raw.get(uid,0));r=cx.execute('SELECT quota_blocked FROM user_quota_state WHERE user_id=? AND cycle_key=?',(uid,key)).fetchone();old=1 if r and r['quota_blocked'] else 0
            cx.execute('''INSERT INTO user_quota_state(user_id,cycle_key,baseline_bytes,last_threshold,quota_blocked,reset_at,updated_at)
                          VALUES(?,?,?,?,?,?,?)
                          ON CONFLICT(user_id,cycle_key) DO UPDATE SET baseline_bytes=excluded.baseline_bytes,last_threshold=0,reset_at=excluded.reset_at,updated_at=excluded.updated_at''',(uid,key,total,0,old,now,now))
    reconcile_all(c,refresh=True)


def snapshot_user(uid):
    with _LOCK:
        u=_CACHE.get(int(uid))
    if u is None:
        allu=users();u=next((x for x in allu if int(x['id'])==int(uid)),None)
    return dict(u) if u else None


def snapshot():
    with _LOCK:
        vals=[dict(v) for _,v in sorted(_CACHE.items())]
    if not vals:vals=users()
    return {'timestamp':int(time.time()),'cycle_key':cycle_key(config.load()),'users':[{
        'user_id':int(u['id']),'name':u.get('name',''),'quota_mode':u.get('quota_mode','fixed'),'quota_gb':float(u.get('quota_gb') or 0),'topup_gb':float(u.get('topup_gb') or 0),'effective_quota_gb':float(u.get('effective_quota_gb') or 0),'used_bytes':int(u.get('quota_used_bytes') or 0),'quota_bytes':int(u.get('quota_bytes') or 0),'remaining_bytes':u.get('quota_remaining_bytes'),'percent':float(u.get('quota_percent') or 0),'quota_exceeded':bool(u.get('quota_exceeded')),'status':u.get('quota_status','active'),'device_count':int(u.get('device_count') or 0),'enabled':bool(u.get('enabled')),'exempt':bool(u.get('exempt'))
    } for u in vals]}


def install():
    global _INSTALLED,_ORIG_INIT,_ORIG_USERS,_ORIG_CREATE_USER,_ORIG_UPDATE_USER,_ORIG_ADD_USAGE_BATCH,_ORIG_RESET_MONTH,_ORIG_EFFECTIVE_QUOTA
    if _INSTALLED:return
    _ORIG_INIT=db.init;_ORIG_USERS=db.users;_ORIG_CREATE_USER=db.create_user;_ORIG_UPDATE_USER=db.update_user;_ORIG_ADD_USAGE_BATCH=db.add_usage_batch;_ORIG_RESET_MONTH=db.reset_month;_ORIG_EFFECTIVE_QUOTA=network.effective_quota
    db.init=_patched_init;db.users=users;db.create_user=_patched_create_user;db.update_user=_patched_update_user;db.add_usage_batch=_patched_add_usage_batch;db.reset_month=_patched_reset_month;network.effective_quota=effective_quota
    _INSTALLED=True
