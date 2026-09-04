let S=null;
const $=id=>document.getElementById(id);
async function api(path,data=null){
  const o=data===null?{}:{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(data)};
  const r=await fetch(path,o); let j={}; try{j=await r.json()}catch{}
  if(r.status===401){$('app').hidden=true;$('login').hidden=false;throw Error('unauthorized')}
  if(!r.ok) throw Error(j.error||('HTTP '+r.status)); return j;
}
function fmt(b){b=Number(b||0);for(const u of ['B','KB','MB','GB','TB']){if(b<1024)return b.toFixed(u==='B'?0:2)+' '+u;b/=1024}return b.toFixed(2)+' PB'}
function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot',"'":'&#39;'}[m]))}
async function login(){try{await api('/api/login',{password:$('pw').value,totp:$('totp').value});$('login').hidden=true;$('app').hidden=false;await refreshAll()}catch(e){$('loginErr').textContent=e.message}}
async function refreshAll(){S=await api('/api/status');render();}
function render(){
 const b=S.bundle,total=Number(b.total_gb||0)*1024**3,used=Number(b.used_bytes||0),pct=total?Math.min(100,used/total*100):0;
 $('bundle').innerHTML=`<b>${fmt(used)} / ${Number(b.total_gb||0).toFixed(1)} GB</b><div class="progress"><i style="width:${pct}%"></i></div><small>${pct.toFixed(1)}% مستخدم</small>`;
 $('devCount').innerHTML=`<b>${S.devices.length}</b> جهاز<br><small>${S.devices.filter(d=>Date.now()/1000-d.last_seen<120).length} نشط حديثاً</small>`;
 $('networkMini').innerHTML=`WAN: <b>${esc(S.network.wan_interface)}</b><br>LAN: <b>${esc(S.network.lan_interface)}</b><br>Gateway: <b>${esc(S.network.lan_ip)}</b>`;
 $('alertBadge').textContent=S.alerts.filter(a=>!a.seen).length;
 renderUsers();renderDevices();renderSettings();renderMac();renderAdmin();renderHistSelect();loadDnsRules();loadFirewall();
}
function renderUsers(){
 const box=$('users');box.innerHTML='<h2>المستخدمون</h2>';
 for(const u of S.users){
   const q=Number(u.effective_quota_gb||0),used=Number(u.usage_bytes||0),pct=q?Math.min(100,used/(q*1024**3)*100):0;
   const devs=S.devices.filter(d=>d.user_id===u.id);
   box.innerHTML+=`<div class="card user"><div class="row"><h3>${esc(u.name)}</h3><span class="pill">ID ${u.id}</span><span class="pill">${esc(u.quota_mode)}</span>${u.exempt?'<span class="pill ok">Exempt</span>':''}</div>
   <div>${fmt(used)} / ${q.toFixed(2)} GB</div><div class="progress"><i style="width:${pct}%"></i></div>
   <small>${devs.length} أجهزة • ${u.speed_down_kbit||0}↓ / ${u.speed_up_kbit||0}↑ kbit</small>
   <div class="toolbar"><button onclick='editUser(${u.id})'>تعديل</button><button class="ghost" onclick='topup(${u.id})'>Top-up</button><button class="danger" onclick='delUser(${u.id})'>حذف</button></div>
   <div>${devs.map(deviceMini).join('')}</div></div>`;
 }
}
function deviceMini(d){return `<div class="row" style="padding:7px 0;border-top:1px solid var(--line)"><b>${esc(d.name)}</b><span class="muted">${esc(d.ip)}</span><span class="pill">${esc(d.mac)}</span><button class="ghost" onclick='editDevice(${d.id})'>إدارة</button></div>`}
function renderDevices(){
 const un=S.devices.filter(d=>!d.user_id);$('devices').innerHTML=un.map(d=>`<div class="card device"><h3>${esc(d.name)}</h3><div>${esc(d.ip)}</div><small>${esc(d.mac)} ${esc(d.manufacturer||'')}</small><div class="row"><span class="pill ${d.enabled?'ok':'bad'}">${d.enabled?'Enabled':'Disabled'}</span>${d.is_guest?'<span class="pill">Guest</span>':''}</div><button onclick='editDevice(${d.id})'>إدارة</button></div>`).join('')||'<div class="muted">لا توجد أجهزة غير مخصصة.</div>';
}
function renderSettings(){const n=S.network,b=S.bundle,w=S.wifi||{};
 $('bundleGb').value=b.total_gb;$('resetDay').value=b.reset_day;$('lineDown').value=n.line_down_mbit;$('lineUp').value=n.line_up_mbit;$('stopNew').checked=!!n.stop_new_connections;$('declineRandom').checked=!!n.decline_random_macs;$('vpnShare').checked=!!n.vpn_share;$('vpnIface').value=n.vpn_interface||'tun0';
 $('wifiSsid').value=w.ssid||'';$('wifiPassword').value='';$('wifiHidden').checked=!!w.hidden;$('wifiState').textContent=w.hidden?'الشبكة مخفية':'الشبكة ظاهرة';$('wifiPasswordState').textContent=w.password_set?'كلمة المرور محفوظة':'كلمة المرور غير مضبوطة';
 fetch('/api/settings').then(r=>r.json()).then(c=>{if(c.guest){$('guestEnabled').checked=!!c.guest.enabled;$('guestQuota').value=c.guest.quota_gb;$('guestDown').value=c.guest.speed_down_kbit;$('guestUp').value=c.guest.speed_up_kbit}})
}
async function saveNetwork(){try{await api('/api/settings',{bundle:{total_gb:+$('bundleGb').value,reset_day:+$('resetDay').value},network:{line_down_mbit:+$('lineDown').value,line_up_mbit:+$('lineUp').value,stop_new_connections:$('stopNew').checked,decline_random_macs:$('declineRandom').checked,vpn_share:$('vpnShare').checked,vpn_interface:$('vpnIface').value},guest:{enabled:$('guestEnabled').checked,quota_gb:+$('guestQuota').value,speed_down_kbit:+$('guestDown').value,speed_up_kbit:+$('guestUp').value}});await refreshAll();alert('تم الحفظ')}catch(e){alert(e.message)}}
async function saveWifi(){
 const ssid=$('wifiSsid').value.trim(),password=$('wifiPassword').value,hidden=$('wifiHidden').checked;
 if(!ssid){modal('<h3>خطأ</h3><p class="bad">اسم شبكة Wi-Fi لا يمكن أن يكون فارغاً.</p>');return}
 if(password && (new TextEncoder().encode(password).length<8 || new TextEncoder().encode(password).length>63)){modal('<h3>خطأ</h3><p class="bad">كلمة مرور Wi-Fi يجب أن تكون بين 8 و63 بايت.</p>');return}
 if(!confirm('تغيير إعدادات Wi-Fi قد يفصل الأجهزة المتصلة مؤقتاً. هل تريد المتابعة؟'))return;
 try{
   const j=await api('/api/wifi',{ssid,password,hidden});
   $('wifiPassword').value='';
   if(S&&j.wifi)S.wifi=j.wifi;
   modal(`<h3>تم حفظ إعدادات Wi-Fi</h3><p>سيتم إعادة تشغيل نقطة الوصول فقط. قد ينقطع اتصال هذا الجهاز لثوانٍ.</p><div class="row"><span class="pill">SSID: ${esc(ssid)}</span><span class="pill">${hidden?'مخفية':'ظاهرة'}</span></div>`);
 }catch(e){modal(`<h3>تعذر حفظ إعدادات Wi-Fi</h3><p class="bad">${esc(e.message)}</p>`)}
}
function showAddUser(){modal(`<h2>مستخدم جديد</h2><label>الاسم<input id="mName"></label><label>نوع الحصة<select id="mMode"><option value="fixed">Fixed</option><option value="shared">Equal share</option></select></label><label>Quota GB<input id="mQuota" type="number" step="0.1" value="5"></label><label>Download kbit<input id="mDown" type="number" value="0"></label><label>Upload kbit<input id="mUp" type="number" value="0"></label><button onclick="addUser()">إنشاء</button>`)}
async function addUser(){await api('/api/users',{name:$('mName').value,quota_mode:$('mMode').value,quota_gb:+$('mQuota').value,speed_down_kbit:+$('mDown').value,speed_up_kbit:+$('mUp').value});closeModal();refreshAll()}
function editUser(id){const u=S.users.find(x=>x.id===id);modal(`<h2>تعديل ${esc(u.name)}</h2><label>الاسم<input id="mName" value="${esc(u.name)}"></label><label>الحصة<select id="mMode"><option value="fixed" ${u.quota_mode==='fixed'?'selected':''}>Fixed</option><option value="shared" ${u.quota_mode==='shared'?'selected':''}>Equal share</option></select></label><label>Quota<input id="mQuota" type="number" step="0.1" value="${u.quota_gb}"></label><label>Down kbit<input id="mDown" type="number" value="${u.speed_down_kbit}"></label><label>Up kbit<input id="mUp" type="number" value="${u.speed_up_kbit}"></label><label><input id="mEx" type="checkbox" ${u.exempt?'checked':''}> Exempt</label><label><input id="mEn" type="checkbox" ${u.enabled?'checked':''}> Enabled</label><button onclick="saveUser(${id})">حفظ</button>`)}
async function saveUser(id){await api('/api/user/update',{id,name:$('mName').value,quota_mode:$('mMode').value,quota_gb:+$('mQuota').value,speed_down_kbit:+$('mDown').value,speed_up_kbit:+$('mUp').value,exempt:$('mEx').checked?1:0,enabled:$('mEn').checked?1:0});closeModal();refreshAll()}
async function topup(id){const gb=prompt('GB إضافية:','1');if(gb!==null){await api('/api/user/topup',{id,gb:+gb});refreshAll()}}
async function delUser(id){if(confirm('حذف المستخدم؟')){await api('/api/user/delete',{id});refreshAll()}}
function editDevice(id){const d=S.devices.find(x=>x.id===id);const opts='<option value="">بدون مستخدم</option>'+S.users.map(u=>`<option value="${u.id}" ${u.id===d.user_id?'selected':''}>${esc(u.name)}</option>`).join('');modal(`<h2>الجهاز</h2><label>الاسم<input id="dName" value="${esc(d.name)}"></label><div>${esc(d.ip)} • ${esc(d.mac)}</div><label>المستخدم<select id="dUser">${opts}</select></label><label><input id="dEn" type="checkbox" ${d.enabled?'checked':''}> Enabled</label><label><input id="dBlock" type="checkbox" ${d.blocked_manual?'checked':''}> Block manually</label><label><input id="dEx" type="checkbox" ${d.exempt?'checked':''}> Exempt</label><label>Down kbit<input id="dDown" type="number" value="${d.speed_down_kbit}"></label><label>Up kbit<input id="dUp" type="number" value="${d.speed_up_kbit}"></label><label>Custom DNS<input id="dDns" value="${esc(d.dns_server||'')}"></label><button onclick="saveDevice(${id})">حفظ</button> <button class="danger" onclick="delDevice(${id})">حذف + Blacklist</button>`)}
async function saveDevice(id){await api('/api/device/update',{id,name:$('dName').value,user_id:$('dUser').value?+$('dUser').value:null,enabled:$('dEn').checked?1:0,blocked_manual:$('dBlock').checked?1:0,exempt:$('dEx').checked?1:0,speed_down_kbit:+$('dDown').value,speed_up_kbit:+$('dUp').value,dns_server:$('dDns').value});closeModal();refreshAll()}
async function delDevice(id){if(confirm('سيتم حذف الجهاز وإضافته للـ Blacklist.')){await api('/api/device/delete',{id});closeModal();refreshAll()}}
async function resetMonth(){if(confirm('تصفير استهلاك الشهر الحالي؟')){await api('/api/reset-month',{});refreshAll()}}
function renderMac(){ $('macRules').innerHTML=S.mac_rules.map(r=>`<div class="row"><code>${esc(r.mac)}</code><span class="pill">${esc(r.action)}</span><span>${esc(r.note)}</span></div>`).join('') }
async function saveMacRule(){await api('/api/mac-rule',{mac:$('macRuleMac').value,action:$('macRuleAction').value});refreshAll()}
async function loadDnsRules(){try{const j=await api('/api/dns/rules');$('dnsRules').innerHTML=j.rules.map(r=>`<div class="card row"><b>${esc(r.domain)}</b><span class="pill">${esc(r.scope_type)}:${r.scope_id}</span><span class="pill">${esc(r.action)}</span><button class="danger" onclick="delDns(${r.id})">حذف</button></div>`).join('')}catch{}}
async function addDnsRule(){await api('/api/dns/rule/add',{scope_type:$('dnsScope').value,scope_id:+$('dnsScopeId').value||0,domain:$('dnsDomain').value,action:$('dnsAction').value});loadDnsRules()}
async function delDns(id){await api('/api/dns/rule/delete',{id});loadDnsRules()}
function renderHistSelect(){const v=$('histDevice').value;$('histDevice').innerHTML='<option value="0">كل الأجهزة</option>'+S.devices.map(d=>`<option value="${d.id}">${esc(d.name)} - ${esc(d.ip)}</option>`).join('');$('histDevice').value=v||'0'}
async function loadHistory(){const j=await api(`/api/dns/history?device_id=${+$('histDevice').value}&hours=${+$('histHours').value}`);$('topDomains').innerHTML=j.top.map(x=>`<div class="row"><b>${esc(x.domain)}</b><span class="pill">${x.hits}</span></div>`).join('');$('dnsHistory').innerHTML=j.history.slice(0,120).map(x=>`<div><small>${new Date(x.ts*1000).toLocaleString()}</small> ${esc(x.client_ip)} → <b>${esc(x.domain)}</b> <span class="pill">${esc(x.action)}</span></div>`).join('')}
async function loadFirewall(){try{const j=await api('/api/firewall');$('dmz').value=j.dmz||'';$('firewallLists').innerHTML='<h3>Rules</h3>'+j.rules.map(r=>`<div class="card row"><b>${esc(r.name)}</b><span>${esc(r.src)} → ${esc(r.dst)}</span><span class="pill">${esc(r.proto)}:${esc(r.dport)}</span><span class="pill">${esc(r.action)}</span><button class="danger" onclick="delFw(${r.id})">حذف</button></div>`).join('')+'<h3>Port Forwards</h3>'+j.forwards.map(r=>`<div class="card row"><b>${esc(r.name)}</b><span>${esc(r.proto)} ${r.external_port} → ${esc(r.internal_ip)}:${r.internal_port}</span><button class="danger" onclick="delPf(${r.id})">حذف</button></div>`).join('')}catch{}}
async function addFw(){await api('/api/firewall/rule/add',{name:$('fwName').value,src:$('fwSrc').value,dst:$('fwDst').value,proto:$('fwProto').value,dport:$('fwPort').value,action:$('fwAction').value});loadFirewall()}
async function delFw(id){await api('/api/firewall/rule/delete',{id});loadFirewall()}
async function addPf(){await api('/api/firewall/forward/add',{name:$('pfName').value,proto:$('pfProto').value,external_port:+$('pfExt').value,internal_ip:$('pfIP').value,internal_port:+$('pfInt').value});loadFirewall()}
async function delPf(id){await api('/api/firewall/forward/delete',{id});loadFirewall()}
async function setDmz(){await api('/api/firewall/dmz',{ip:$('dmz').value});loadFirewall()}
function renderAdmin(){$('sysInfo').textContent=JSON.stringify(S.system,null,2);$('alerts').innerHTML=S.alerts.map(a=>`<div><small>${new Date(a.ts*1000).toLocaleString()}</small> <b>${esc(a.kind)}</b> ${esc(a.message)}</div>`).join('');$('logs').innerHTML=S.events.map(e=>`<div><small>${new Date(e.ts*1000).toLocaleString()}</small> <span class="${e.level==='error'?'bad':''}">${esc(e.level)}</span> ${esc(e.message)}</div>`).join('')}
async function changePw(){await api('/api/password',{password:$('newPw').value});alert('تم التغيير')}
async function setup2fa(){const j=await api('/api/2fa/setup',{});$('twoInfo').textContent='Secret: '+j.secret}
async function enable2fa(){await api('/api/2fa/enable',{code:$('twoCode').value});alert('تم تفعيل 2FA')}
async function enableHttps(){const j=await api('/api/https/enable',{});alert('تم إنشاء الشهادة. أعد تشغيل الخدمة ثم استخدم https://192.168.2.1:8080')}
function modal(html){$('modalBody').innerHTML=html;$('modal').hidden=false}function closeModal(){$('modal').hidden=true}
document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>{document.querySelectorAll('#tabs button').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));b.classList.add('active');$(b.dataset.tab).classList.add('active');if(b.dataset.tab==='history')loadHistory()});
fetch('/api/status').then(r=>{if(r.ok){$('login').hidden=true;$('app').hidden=false;return r.json()}throw 0}).then(j=>{S=j;render()}).catch(()=>{});
