(()=>{
let timer=null;
const $g=id=>document.getElementById(id);
function escg(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
function roleLabel(r){return r==='guest'?'Guest':r==='existing_at_activation'?'متصل قبل تفعيل Guest':r==='managed'?'Managed':'Unassigned'}
function roleClass(r){return r==='guest'?'ok':r==='unassigned'?'warn':''}
function ensureUi(){
 const en=$g('guestEnabled'); if(!en)return;
 const card=en.closest('.card'); if(!card||$g('guestConnectedList'))return;
 const note=document.createElement('div');
 note.innerHTML=`<div class="notice" style="margin-top:10px"><b>سياسة Guest Mode</b><br><small>عند التفعيل يتم تثبيت قائمة الأجهزة المتصلة حالياً. هذه الأجهزة لا تتحول إلى Guest. أي جهاز غير مُدار يتصل بعد التفعيل يدخل Guest تلقائياً ويأخذ السرعة المحددة لكل جهاز.</small></div><div id="guestApplyState" class="row" style="margin-top:10px"></div><h4>الأجهزة المتصلة حالياً</h4><div id="guestConnectedList"><span class="muted">جاري التحميل...</span></div>`;
 card.appendChild(note);
 const down=$g('guestDown'),up=$g('guestUp');
 if(down&&down.previousElementSibling==null){}
 const labels=card.querySelectorAll('label');
 labels.forEach(l=>{const i=l.querySelector('input');if(!i)return;if(i.id==='guestDown')l.childNodes[0].textContent='Download limit (kbit) ';if(i.id==='guestUp')l.childNodes[0].textContent='Upload limit (kbit) '});
 const small=document.createElement('small');small.id='guestSpeedHint';small.className='muted';small.textContent='مثال: 2000 kbit ≈ 2 Mbps لكل Guest.';card.insertBefore(small,card.querySelector('button'));
}
async function loadGuestStatus(){
 ensureUi(); if(!$g('guestConnectedList'))return;
 try{
   const r=await fetch('/api/diagnostics'); if(!r.ok)throw Error('HTTP '+r.status);const j=await r.json(),g=j.guest_mode||{};
   const state=$g('guestApplyState');
   if(state)state.innerHTML=`<span class="pill ${g.enabled?'ok':'bad'}">${g.enabled?'Guest ON':'Guest OFF'}</span><span class="pill ${g.shaping_healthy?'ok':'bad'}">Speed shaping: ${g.shaping_healthy?'OK':'NOT APPLIED'}</span><span class="pill">${Number(g.speed_down_kbit||0)}↓ / ${Number(g.speed_up_kbit||0)}↑ kbit لكل Guest</span>${g.shaping_error?`<span class="bad">${escg(g.shaping_error)}</span>`:''}`;
   const rows=(g.connected||[]).map(x=>`<div class="row device-line"><b>${escg(x.name||'Device')}</b><span class="muted">${escg(x.ip||'')}</span><code>${escg(x.mac||'')}</code><span class="pill ${roleClass(x.role)}">${roleLabel(x.role)}</span>${x.is_guest?`<span class="pill ok">${Number(g.speed_down_kbit||0)}↓/${Number(g.speed_up_kbit||0)}↑ kbit</span>`:''}</div>`).join('');
   $g('guestConnectedList').innerHTML=rows||'<div class="muted">لا توجد أجهزة Wi-Fi متصلة حالياً.</div>';
 }catch(e){$g('guestConnectedList').innerHTML=`<span class="bad">${escg(e.message)}</span>`}
}
function activeNetworkTab(){const b=document.querySelector('#tabs button.active');return b&&b.dataset.tab==='network'}
function start(){ensureUi();loadGuestStatus();timer=setInterval(()=>{if(activeNetworkTab())loadGuestStatus()},5000)}
const oldSave=window.saveNetwork;
if(typeof oldSave==='function')window.saveNetwork=async function(){const r=await oldSave.apply(this,arguments);setTimeout(loadGuestStatus,700);return r};
const oldRender=window.renderSettings;
if(typeof oldRender==='function')window.renderSettings=function(){const r=oldRender.apply(this,arguments);setTimeout(()=>{ensureUi();loadGuestStatus()},0);return r};
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
