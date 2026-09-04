(()=>{
  const checkbox=document.getElementById('speedLimits');
  if(!checkbox)return;

  const originalLabel=checkbox.closest('label');
  if(originalLabel)originalLabel.hidden=true;

  const card=checkbox.closest('.card');
  if(!card)return;

  const panel=document.createElement('div');
  panel.className='notice';
  panel.innerHTML=`
    <div class="row" style="justify-content:space-between">
      <b>Speed Limits</b>
      <span id="speedLimitsState" class="pill">جاري قراءة الحالة...</span>
    </div>
    <p class="muted" style="margin:8px 0">يشغّل أو يوقف Traffic Shaping بالكامل. عند الإيقاف تتم إزالة حدود tc/nft ويظل الإنترنت مفتوحًا بدون تحديد سرعة.</p>
    <button id="speedLimitsToggle" type="button" class="ghost">...</button>
  `;
  const heading=card.querySelector('h3');
  if(heading)heading.insertAdjacentElement('afterend',panel);else card.prepend(panel);

  const state=document.getElementById('speedLimitsState');
  const button=document.getElementById('speedLimitsToggle');

  function paint(enabled){
    checkbox.checked=!!enabled;
    state.textContent=enabled?'ON — شغال':'OFF — متوقف';
    state.className='pill '+(enabled?'ok':'bad');
    button.textContent=enabled?'إيقاف Speed Limits':'تشغيل Speed Limits';
    button.className=enabled?'danger':'ghost';
    button.setAttribute('aria-pressed',enabled?'true':'false');
  }

  async function readState(){
    try{
      const r=await fetch('/api/settings',{cache:'no-store'});
      if(!r.ok)return;
      const c=await r.json();
      paint(!!(c.features&&c.features.speed_limits));
    }catch(e){
      state.textContent='تعذر قراءة الحالة';
      state.className='pill bad';
    }
  }

  async function toggle(){
    const next=!checkbox.checked;
    button.disabled=true;
    try{
      await api('/api/settings',{features:{speed_limits:next}});
      paint(next);
      if(typeof refreshAll==='function')await refreshAll();
      await readState();
      toast(`<h3>Speed Limits ${next?'ON':'OFF'}</h3><p>${next?'تم تشغيل تحديد السرعة وتطبيق القواعد الحالية.':'تم إيقاف تحديد السرعة وإزالة Traffic Shaping بدون فصل Wi-Fi.'}</p>`);
    }catch(e){
      toast(`<h3>خطأ</h3><p class="bad">${esc(e.message)}</p>`);
      await readState();
    }finally{
      button.disabled=false;
    }
  }

  button.addEventListener('click',toggle);

  // Keep the visible state synchronized after any dashboard action. This is
  // especially important because applying a new device/Guest speed can
  // automatically re-enable speed_limits on the backend.
  const coreRefresh=window.refreshAll;
  if(typeof coreRefresh==='function'){
    window.refreshAll=async function(){
      const result=await coreRefresh.apply(this,arguments);
      await readState();
      return result;
    };
  }

  readState();
})();
