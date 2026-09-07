from __future__ import annotations
import json, re
from . import network

_INSTALLED=False
_ORIG_RUN=None
_COMMENT=re.compile(r'^qg:(?:gateway|\d+):(up|down)$')


def _as_nft_text(cmd):
    if not isinstance(cmd,list) or cmd[:3]!=['nft','add','rule']:
        return None
    if len(cmd)<2 or cmd[-2]!='comment' or not _COMMENT.fullmatch(str(cmd[-1])):
        return None
    return ' '.join(str(x) for x in cmd[1:-1])+' '+json.dumps(str(cmd[-1]))+'\n'


def run(cmd,check=False,input_text=None):
    text=_as_nft_text(cmd)
    if text is None:
        return _ORIG_RUN(cmd,check=check,input_text=input_text)
    return _ORIG_RUN(['nft','-f','-'],check=check,input_text=text)


def install():
    global _INSTALLED,_ORIG_RUN
    if _INSTALLED:return
    _ORIG_RUN=network.run
    network.run=run
    _INSTALLED=True
