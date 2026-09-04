(function(){
  const $q=id=>document.getElementById(id);
  let LAST_GUEST=null;
  const MIN_MBIT=0.064;

  function kb(v){
    v=Number(v||0);
    if(v<=0)return 'Unlimited';
    return v>=1000?(v/1000).toFixed(v%1000?2:0)+' Mbps':v+' kbit';
  }
  function mbitValueFromKbit(v){
    v=Number(v||0);
    if(v<0)return '0';
    if(v===0)return '';
    const m=v/1000;
    return String(Number(m.toFixed(3)));
  }
  function mbitFromKbit(v){
    v=Number(v||0);
    return v>0?Number((v/1000).toFixed(3)):0;
  }
  function toKbitFromMbps(raw,{allowInherit=false}={}){
    raw=String(raw??'').trim();
    if(raw==='')return allowInherit?0:0;
    const n=Number(raw);
    if(!Number.isFinite(n)||n<0)throw Error('السرعة يجب أن تكون 0 أو أكبر');
    if(n===0)return allowInherit?-1:0;
    if(n<MIN_MBIT)throw Error('أقل سرعة مسموحة هي 0.064 Mbps (64 kbit). استخدم 0 لو عايز Unlimited.');
    return Math.round(n*1000);
  }
  function roleName(r){return r==='guest'?'Guest':r==='managed'?'Managed':r==='existing_at_activation'?'متصل قبل تفعيل Guest':'Unassigned'}
  function roleClass(r){return r==='guest'?'ok':r==='managed'?'':'existing_at_activation'?'warn':''}
  function storedFromInput(id){return toKbitFromMbps($q(id)?.value,{allowInherit:true})}

  function activateGuestTab(){
    document.querySelectorAll('#tabs button').forEach(x=>x.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
    const b=document.querySelector('#tabs button[data-tab="guest"]');if(b)b.classList.add('active');
    const s=$q('guest');if(s)s.classList.add('active');
    loadGuestControlCenter();
  }

  function buildGuestTab(){
    if($q('guest'))return;
    const nav=$q('tabs'),main=document.querySelector('main');if(!nav||!main)return;
    const btn=document.createElement('button');btn.dataset.tab='guest';btn.textContent='Guest Mode';btn.onclick=activateGuestTab;
    const management=nav.querySelector('[data-tab="management"]');
    if(management&&management.nextSibling)nav.insertBefore(btn,management.nextSibling);else nav.appendChild(btn);
    const section=document.createElement('section');section.id='guest';section.className='tab';
    const network=$q('network');main.insertBefore(section,network||main.firstChild);
    const old=$q('guestEnabled')?.closest('.card');if(old)old.remove();
    section.innerHTML=`
      <div class="grid">
        <div class="card">
          <h2>Guest Mode</h2>
          <label><input id="guestEnabled" type="checkbox"> تفعيل Guest Mode</label>
          <label>Guest Quota GB<input id="guestQuota" type="number" min="0" step="0.1"></label>
          <label>Default Download Mbps<input id="guestDown" type="number" min="0" step="0.1" placeholder="مثال: 2 = 2 Mbps"></label>
          <label>Default Upload Mbps<input id="guestUp" type="number" min="0" step="0.1" placeholder="مثال: 0.5 = 500 kbit"></label>
          <label>Max Guest Devices<input id="guestMax" type="number" min="1" max="100" step="1"></label>
          <small>السرعة هنا Mbps وبشكل Per Guest وليست Shared Pool. 0 = Unlimited. أقل Limit موجب هو 0.064 Mbps.</small>
          <div class="toolbar">
            <button onclick="saveGuestModeState()">حفظ حالة Guest</button>
            <button class="warn" onclick="applyGuestSettingsAll()">Apply Guest Settings to ALL Guests</button>
          </div>
          <div id="guestApplyState" class="notice"><span class="muted">جاهز للتطبيق.</span></div>
        </div>
        <div class="card">
          <h3>Guest shaping status</h3>
          <div id="guestSpeedStatus"><span class="muted">جارِ قراءة tc/nftables…</span></div>
        </div>
      </div>
      <div class="row"><h2>Active Guests</h2><button class="ghost" onclick="loadGuestControlCenter()">تحديث</button></div>
      <div id="activeGuests" class="grid"></div>
      <h2>Currently Connected Devices</h2>
      <small>هذه القائمة من iw station dump. سرعة الجهاز بالـMbps: الحقل الفارغ = inherit/default، و0 = Unlimited override.</small>
      <div id="connectedGuestDevices" class="grid"></div>`;
  }

  function renderGuestShaping(s){
    const box=$q('guestSpeedStatus');if(!box)return;
    if(!s){box.innerHTML='<span class="muted">لا توجد بيانات shaping.</span>';return}
    const cfg=s.guest_config||{};
    const state=!s.enabled?'<span class="pill bad">Shaping OFF</span>':s.healthy?'<span class="pill ok">Kernel Applied</span>':'<span class="pill bad">Kernel Failed / Internet restored unshaped</span>';
    let html=`<div class="row">${state}<span class="pill">Fail-open</span><span class="pill">Per Guest</span></div><div>Configured: <b>${kb(cfg.down_kbit)} ↓</b> / <b>${kb(cfg.up_kbit)} ↑</b></div><small>لو tc/nftables فشل، QuotaGate يشيل الـshaping ويرجع الإنترنت بدون Limit بدل ما يقطع الاتصال.</small>`;
    if(s.last_error)html+=`<div class="bad" style="margin-top:8px">${esc(s.last_error)}</div>`;
    box.innerHTML=html;
  }

  function speedInputs(x,prefix){
    const downVal=mbitValueFromKbit(x.stored_down_kbit),upVal=mbitValueFromKbit(x.stored_up_kbit);
    const downDefault=mbitFromKbit(x.effective_down_kbit),upDefault=mbitFromKbit(x.effective_up_kbit);
    return `<label>Download Mbps<input id="${prefix}Down" type="number" min="0" step="0.1" value="${downVal}" placeholder="Default: ${downDefault} Mbps"></label><label>Upload Mbps<input id="${prefix}Up" type="number" min="0" step="0.1" value="${upVal}" placeholder="Default: ${upDefault} Mbps"></label>`;
  }

  function renderActiveGuests(g){
    const box=$q('activeGuests');if(!box)return;
    const guests=g.active_guests||[];
    if(!guests.length){box.innerHTML='<div class="card muted">لا توجد أجهزة Guest متصلة حالياً.</div>';return}
    box.innerHTML=guests.map(x=>{
      const p='g'+x.device_id;
      const kernel=(x.effective_down_kbit>0||x.effective_up_kbit>0)?(x.kernel_applied?'<span class="pill ok">Kernel applied</span>':'<span class="pill bad">Not applied</span>'):'<span class="pill ok">Unlimited / no class required</span>';
      return `<div class="card"><div class="row"><h3>${esc(x.name||('Guest-'+x.device_id))}</h3>${kernel}</div><div>${sensitive(x.ip||'-')} • <span class="sensitive">${sensitive(x.mac)}</span></div><small>Signal: ${x.signal??'-'} dBm • TX ${x.tx_bitrate_mbps??'-'} / RX ${x.rx_bitrate_mbps??'-'} Mbps</small><div>Effective: <b>${kb(x.effective_down_kbit)} ↓ / ${kb(x.effective_up_kbit)} ↑</b></div>${speedInputs(x,p)}<div class="toolbar"><button onclick="applyConnectedDeviceSpeed(${x.device_id},'${p}')">Apply Speed</button><button class="ghost" onclick="resetGuestDefault(${x.device_id})">Reset to Guest Default</button><button class="warn" onclick="removeFromGuest(${x.device_id})">Remove from Guest</button><button class="ghost" onclick="moveGuestToUser(${x.device_id})">Move to User</button><button class="danger" onclick="blockGuest(${x.device_id})">Block</button></div></div>`;
    }).join('');
  }

  function renderConnected(g){
    const box=$q('connectedGuestDevices');if(!box)return;
    const rows=g.connected||[];
    if(!rows.length){box.innerHTML='<div class="card muted">لا توجد Wi-Fi stations متصلة حالياً.</div>';return}
    box.innerHTML=rows.map(x=>{
      const p='c'+(x.device_id||'x');
      const controls=x.device_id?`${speedInputs(x,p)}<button onclick="applyConnectedDeviceSpeed(${x.device_id},'${p}')">Apply Speed</button>`:'<small class="muted">سيظهر التحكم بعد اكتشاف الجهاز في قاعدة البيانات.</small>';
      return `<div class="card"><div class="row"><b>${esc(x.name||x.mac)}</b><span class="pill ${roleClass(x.role)}">${roleName(x.role)}</span></div><div>${sensitive(x.ip||'-')} • <span class="sensitive">${sensitive(x.mac)}</span></div><small>Signal ${x.signal??'-'} dBm • TX ${x.tx_bitrate_mbps??'-'} / RX ${x.rx_bitrate_mbps??'-'} Mbps</small><div>Effective: <b>${kb(x.effective_down_kbit)} ↓ / ${kb(x.effective_up_kbit)} ↑</b></div>${controls}</div>`;
    }).join('');
  }

  function setApplyState(html,cls=''){
    const b=$q('guestApplyState');if(b)b.innerHTML=`<span class="${cls}">${html}</span>`;
  }

  async function loadGuestControlCenter(){
    try{
      const j=await api('/api/diagnostics');const g=j.guest_mode||{};LAST_GUEST=g;
      if($q('guestEnabled'))$q('guestEnabled').checked=!!g.enabled;
      if($q('guestQuota'))$q('guestQuota').value=g.quota_gb??0.5;
      if($q('guestDown'))$q('guestDown').value=mbitFromKbit(g.speed_down_kbit??1024);
      if($q('guestUp'))$q('guestUp').value=mbitFromKbit(g.speed_up_kbit??256);
      if($q('guestMax'))$q('guestMax').value=g.max_devices??10;
      renderGuestShaping(g.shaping||j.shaping||{});renderActiveGuests(g);renderConnected(g);
    }catch(e){setApplyState(esc(e.message),'bad')}
  }

  function guestPayload(withRevision=false){
    const quota=+$q('guestQuota').value,max=+$q('guestMax').value;
    const down=toKbitFromMbps($q('guestDown').value),up=toKbitFromMbps($q('guestUp').value);
    if(!Number.isFinite(quota)||quota<0)throw Error('Quota غير صحيحة');
    if(!Number.isInteger(max)||max<1||max>100)throw Error('Max Guests يجب أن يكون بين 1 و100');
    const g={enabled:$q('guestEnabled').checked,quota_gb:quota,speed_down_kbit:down,speed_up_kbit:up,max_devices:max};
    if(withRevision)g.apply_revision=Date.now();
    return g;
  }

  async function saveGuestModeState(){
    try{
      setApplyState('Saving Guest Mode…');
      await api('/api/settings',{guest:guestPayload(false)});
      await refreshAll();await loadGuestControlCenter();
      setApplyState('Guest Mode saved. الأجهزة المتصلة قبل لحظة التفعيل لم يتم تحويلها إلى Guests.','ok');
    }catch(e){setApplyState('Failed: '+esc(e.message),'bad')}
  }

  async function applyGuestSettingsAll(){
    if(!confirm('سيتم مسح أي Device Override على كل Guest وتطبيق السرعة/Quota العامة فوراً على جميع Guests. متابعة؟'))return;
    try{
      setApplyState('Applying Guest settings to ALL Guests…');
      await api('/api/settings',{guest:guestPayload(true),features:{speed_limits:true}});
      await refreshAll();
      const j=await api('/api/diagnostics'),g=j.guest_mode||{};LAST_GUEST=g;
      renderGuestShaping(g.shaping||j.shaping||{});renderActiveGuests(g);renderConnected(g);
      if(!g.shaping_healthy&&(Number(g.speed_down_kbit)>0||Number(g.speed_up_kbit)>0))throw Error(g.shaping_error||'Kernel shaping verification failed; Internet restored without shaping');
      setApplyState(`Applied ✅ — ${kb(g.speed_down_kbit)} ↓ / ${kb(g.speed_up_kbit)} ↑ لكل Guest`,'ok');
    }catch(e){setApplyState('Kernel Apply Failed — Internet restored unshaped: '+esc(e.message),'bad')}
  }

  async function applyConnectedDeviceSpeed(id,prefix){
    try{
      const down=storedFromInput(prefix+'Down'),up=storedFromInput(prefix+'Up');
      setApplyState(`Applying speed for device ${id}…`);
      await api('/api/device/update',{id,speed_down_kbit:down,speed_up_kbit:up});
      await refreshAll();await loadGuestControlCenter();
      const x=(LAST_GUEST?.connected||[]).find(d=>Number(d.device_id)===Number(id));
      if(LAST_GUEST&&!LAST_GUEST.shaping_healthy&&(x&&(x.effective_down_kbit>0||x.effective_up_kbit>0)))throw Error(LAST_GUEST.shaping_error||'Kernel verification failed; Internet restored without shaping');
      setApplyState(`Device ${id}: Applied ✅`,'ok');
    }catch(e){setApplyState(`Device ${id}: Limit failed — Internet restored unshaped — ${esc(e.message)}`,'bad')}
  }

  async function resetGuestDefault(id){
    try{setApplyState(`Resetting Guest ${id} to defaults…`);await api('/api/device/update',{id,speed_down_kbit:0,speed_up_kbit:0});await refreshAll();await loadGuestControlCenter();setApplyState(`Guest ${id}: Guest defaults applied ✅`,'ok')}catch(e){setApplyState(esc(e.message),'bad')}
  }

  async function removeFromGuest(id){
    if(!confirm('إخراج الجهاز من Guest Mode؟ سيظل متصلاً بالـWi-Fi ولن يتم حذفه أو إضافته للـDeny List.'))return;
    try{await api('/api/device/update',{id,is_guest:0,user_id:null,speed_down_kbit:0,speed_up_kbit:0,enabled:1});await refreshAll();await loadGuestControlCenter();setApplyState(`Device ${id} moved to Unassigned ✅`,'ok')}catch(e){setApplyState(esc(e.message),'bad')}
  }

  function moveGuestToUser(id){
    const users=(S?.users||[]).filter(u=>!String(u.name||'').startsWith('Guest-'));
    if(!users.length)return toast('<h3>لا يوجد مستخدم عادي</h3><p>أنشئ User أولاً.</p>');
    modal(`<h3>Move Guest to User</h3><select id="guestMoveUser">${users.map(u=>`<option value="${u.id}">${esc(u.name)}</option>`).join('')}</select><button onclick="confirmMoveGuest(${id})">نقل</button>`);
  }
  async function confirmMoveGuest(id){
    try{const uid=+$q('guestMoveUser').value;await api('/api/device/update',{id,is_guest:0,user_id:uid,speed_down_kbit:0,speed_up_kbit:0,enabled:1});closeModal();await refreshAll();await loadGuestControlCenter();setApplyState(`Device ${id} moved to User ✅`,'ok')}catch(e){setApplyState(esc(e.message),'bad')}
  }
  async function blockGuest(id){
    if(!confirm('حظر الإنترنت عن هذا الجهاز؟'))return;
    try{await api('/api/device/update',{id,blocked_manual:1});await refreshAll();await loadGuestControlCenter();setApplyState(`Device ${id} blocked ✅`,'ok')}catch(e){setApplyState(esc(e.message),'bad')}
  }

  buildGuestTab();
  window.renderGuestShaping=renderGuestShaping;
  window.loadGuestSpeedStatus=loadGuestControlCenter;
  window.loadGuestControlCenter=loadGuestControlCenter;
  window.saveGuestModeState=saveGuestModeState;
  window.applyGuestSettingsAll=applyGuestSettingsAll;
  window.applyConnectedDeviceSpeed=applyConnectedDeviceSpeed;
  window.resetGuestDefault=resetGuestDefault;
  window.removeFromGuest=removeFromGuest;
  window.moveGuestToUser=moveGuestToUser;
  window.confirmMoveGuest=confirmMoveGuest;
  window.blockGuest=blockGuest;
  setInterval(()=>{if($q('guest')?.classList.contains('active'))loadGuestControlCenter()},5000);
})();
