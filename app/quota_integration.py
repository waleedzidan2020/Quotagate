from __future__ import annotations
from . import db, quota

_INSTALLED=False
_ORIG_UPDATE_USER=None
_ORIG_USAGE_SNAPSHOT=None


def install():
    global _INSTALLED,_ORIG_UPDATE_USER,_ORIG_USAGE_SNAPSHOT
    if _INSTALLED:return

    # Reuse the existing authenticated /api/user/update route for per-user
    # quota resets. main.py forwards unknown fields to db.update_user, so this
    # narrow wrapper consumes quota_reset without changing the public router.
    _ORIG_UPDATE_USER=db.update_user
    def update_user(i,**kw):
        reset=bool(kw.pop('quota_reset',False))
        result=_ORIG_UPDATE_USER(i,**kw)
        if reset:quota.reset_user(int(i))
        return result
    db.update_user=update_user

    # Extend the existing authenticated /api/usage/live response instead of
    # adding another 2-second HTTP poll. The quota snapshot is memory-backed
    # after reconciliation and never launches an nft subprocess itself.
    from . import usage_tracker
    _ORIG_USAGE_SNAPSHOT=usage_tracker.snapshot
    def usage_snapshot():
        data=_ORIG_USAGE_SNAPSHOT()
        q=quota.snapshot()
        data['users']=q.get('users',[])
        data['quota_cycle_key']=q.get('cycle_key','')
        return data
    usage_tracker.snapshot=usage_snapshot

    _INSTALLED=True
