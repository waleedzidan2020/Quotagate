(function(){
  const $q=id=>document.getElementById(id);
  function kb(v){v=Number(v||0);return v>=1000?(v/1000).toFixed(v%1000?2:0)+' Mbps':v+' kbit'}
  function renderGuestShaping(s){
    const box=$q('guestSpeedStatus');if(!box)return;
    if(!s){box.innerHTML='<span class="muted">لا توجد بيانات shaping.</span>';return}
    const cfg=s.guest_config||{}, guests=s.guest_devices||[];
    const state=!s.enabled?'<span class="pill bad">Shaping OFF</span>':s.healthy?'<span class="pill ok">Applied</span>':'<span class="pill bad">Not applied</span>';
    let html=`<div class="row">${state}<span class="pill">Per Guest</span></div><small>الحد لكل Guest بشكل مستقل، وليس Shared Pool.</small><div style="margin-top:8px">Configured: <b>${kb(cfg.down_kbit)} ↓</b> / <b>${kb(cfg.up_kbit)} ↑</b></div>`;
    if(s.last_error)html+=`<div class="bad" style="margin-top:8px">${esc(s.last_error)}</div>`;
    if(!guests.length)html+='<div class="muted" style="margin-top:8px">لا توجد أجهزة Guest مسجلة حالياً.</div>';
    else html+=guests.map(g=>`<div class="device-line row"><b>${esc(g.name)}</b><span class="sensitive">${sensitive(g.ip||'-')}</span><span>${kb(g.down_kbit)} ↓ / ${kb(g.up_kbit)} ↑</span><span class="pill ${g.applied?'ok':'bad'}">${g.applied?'Kernel applied':'Not applied'}</span>${g.reason?`<small>${esc(g.reason)}</small>`:''}</div>`).join('');
    box.innerHTML=html;
  }
  async function loadGuestSpeedStatus(){
    const box=$q('guestSpeedStatus');if(box)box.innerHTML='<span class="muted">جارِ فحص tc/nftables…</span>';
    try{const j=await api('/api/diagnostics');renderGuestShaping(j.shaping||{});}catch(e){if(box)box.innerHTML=`<span class="bad">${esc(e.message)}</span>`}
  }
  window.renderGuestShaping=renderGuestShaping;
  window.loadGuestSpeedStatus=loadGuestSpeedStatus;
  document.querySelectorAll('#tabs button').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.tab==='network')loadGuestSpeedStatus()}));
  document.addEventListener('DOMContentLoaded',()=>setTimeout(loadGuestSpeedStatus,500));
})();
