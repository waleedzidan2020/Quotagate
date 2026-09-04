from __future__ import annotations
import base64,hashlib,hmac,secrets,struct,time
from urllib.parse import quote
def hash_password(p):
    s=secrets.token_bytes(16);r=240000;d=hashlib.pbkdf2_hmac("sha256",p.encode(),s,r)
    return f"pbkdf2_sha256${r}${base64.b64encode(s).decode()}${base64.b64encode(d).decode()}"
def verify_password(p,e):
    try:
        a,r,s,x=e.split("$",3);d=hashlib.pbkdf2_hmac("sha256",p.encode(),base64.b64decode(s),int(r))
        return a=="pbkdf2_sha256" and hmac.compare_digest(d,base64.b64decode(x))
    except:return False
def new_totp_secret():return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
def _totp(s,c):
    s+= "="*((8-len(s)%8)%8);k=base64.b32decode(s,casefold=True);d=hmac.new(k,struct.pack(">Q",c),hashlib.sha1).digest();o=d[-1]&15
    return f"{(struct.unpack('>I',d[o:o+4])[0]&0x7fffffff)%1000000:06d}"
def verify_totp(s,code,window=1):
    if not s or not str(code).isdigit() or len(str(code))!=6:return False
    n=int(time.time())//30;return any(hmac.compare_digest(_totp(s,n+i),str(code)) for i in range(-window,window+1))
def otpauth_uri(s,account="admin",issuer="QuotaGate antiX"):
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={s}&issuer={quote(issuer)}&digits=6&period=30"
