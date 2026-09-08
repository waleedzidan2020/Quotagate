(()=>{
  let gameState={active:false},nextStatusPoll=0;
  const id=x=>document.getElementById(x);
  const mbpsToKbit=v=>Math.max(0,Math.round(Number(v||0)*1000));
  const kbitToMbps=v=>Number(v||0)/1000;

  function ensureCard(){
    const grid=document.querySelector('#network .grid');if(!grid||id('qosGamingCard'))return;
    const card=document.createElement('div');card.className='card';card.id='qosGamingCard';card.innerHTML=`
      <h3>🎮 Priority & Smart Gaming</h3>
      <small>Low Latency يدير الـ queue، والـ Priority يحدد من يُخدم أولاً وقت الزحمة. لا يتم Restart للـ Wi-Fi.</small>
      <div class="notice" id="gamingStateBox"><span class="muted">Gaming Mode غير مفعّل.</span></div>
      <label>Gaming Device<select id="gamingDevice"></select></label>
      <label>Duration<select id="gamingDuration"><option value="15">15 minutes</option><option value="30" selected>30 minutes</option><option value="60">60 minutes</option><option value="120">120 minutes</option><option value="0">Until manually stopped</option></select></label>
      <label>Other Devices Priority<select id="gamingOtherPriority"><option value="low" selected>Low</option><option value="normal">Normal</option><option value="high">High</option></select></label>
      <label><input id="gamingLimitOthers" type="checkbox"> Limit other devices while gaming</label>
      <div id="gamingLimitFields" hidden>
        <label>Other Download Max Mbps<input id="gamingOtherDown" type="number" min="0" step="0.1" value="3"></label>
        <label>Other Upload Max Mbps<input id="gamingOtherUp" type="number" min="0" step="0.05" value="0.7"></label>
        <label>Guest Download Max Mbps<input id="gamingGuestDown" type="number" min="0" step="0.1" value="1"></label>
      </div>
      <div class="toolbar"><button id="gamingStart">START GAMING MODE</button><button class="ghost" id="gamingStop" disabled>STOP & RESTORE</button></div>`;
    grid.appendChild(card);
    id('gamingLimitOthers').onchange=()=>id('gamingLimitFields').hidden=!id('gamingLimitOthers').checked;
    id('gamingStart').onclick=startGaming;id('gamingStop').onclick=stopGaming;
  }

  function refreshDevices(){
    ensureCard();const sel=id('gamingDevice');if(!sel||typeof S==='undefined'||!S)return;
    const old=sel.value;sel.innerHTML=S.devices.filter(d=>d.ip).map(d=>`<option value="${d.id}">${esc(d.name)} — ${sensitive(d.ip)}</option>`).join('');
    if([...sel.options].some(o=>o.value===old))sel.value=old;
  }

  function fmtRemain(sec){
    if(sec===null||sec===undefined)return 'Until manually stopped';sec=Math.max(0,Number(sec)||0);const h=Math.floor(sec/3600),m=Math.floor((sec%3600)/60),s=Math.floor(sec%60);return `${h?String(h).padStart(2,'0')+':':''}${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
  }
  function renderGameState(){
    const box=id('gamingStateBox');if(!box)return;
    if(!gameState.active){box.innerHTML='<span class="muted">Gaming Mode غير مفعّل.</span>';id('gamingStart').disabled=false;id('gamingStop').disabled=true;return}
    let rem=gameState.remaining_seconds;if(gameState.expires_at)rem=Math.max(0,Number(gameState.expires_at)-Date.now()/1000);
    box.innerHTML=`<b class="ok">GAMING MODE ACTIVE</b><br>${esc(gameState.device_name||('Device '+gameState.device_id))}<br><small>Priority: Highest • Remaining: ${fmtRemain(rem)}</small>`;
    id('gamingStart').disabled=true;id('gamingStop').disabled=false;
  }
  async function loadGamingStatus(){try{gameState=await api('/api/gaming/status');renderGameState()}catch(e){console.warn('Gaming status:',e.message)}}
  async function startGaming(){
    const did=Number(id('gamingDevice').value||0);if(!did)return toast('<h3>Gaming Mode</h3><p class="bad">اختر جهازاً له IP أولاً.</p>');
    const limit=id('gamingLimitOthers').checked;
    try{gameState=await api('/api/gaming/start',{device_id:did,duration_minutes:Number(id('gamingDuration').value),other_priority:id('gamingOtherPriority').value,limit_others:limit,other_down_kbit:limit?mbpsToKbit(id('gamingOtherDown').value):0,other_up_kbit:limit?mbpsToKbit(id('gamingOtherUp').value):0,guest_down_kbit:limit?mbpsToKbit(id('gamingGuestDown').value):0});renderGameState();toast('<h3>🎮 Gaming Mode</h3><p class="ok">تم تطبيق أولوية اللعب Live بدون فصل Wi-Fi.</p>')}catch(e){toast(`<h3>Gaming Mode Failed</h3><p class="bad">${esc(e.message)}</p><small>تم الحفاظ على Fail-open؛ لا يجب أن يبقى الإنترنت مقطوعاً.</small>`)}
  }
  async function stopGaming(){try{await api('/api/gaming/stop',{});gameState={active:false};renderGameState();toast('<h3>تم الإيقاف</h3><p>رجعت سياسة السرعة والأولوية الأصلية.</p>')}catch(e){toast(`<h3>Restore Failed</h3><p class="bad">${esc(e.message)}</p>`)}}

  async function applyDevicePriority(deviceId){
    const sel=id('devicePrioritySelect');if(!sel)return;
    try{await api('/api/device/priority',{id:Number(deviceId),priority:sel.value});if(typeof refreshAll==='function')await refreshAll();toast(`<h3>Priority Applied</h3><p>${esc(sel.value.toUpperCase())} تم تطبيقها Live بدون Restart.</p>`)}catch(e){toast(`<h3>Priority Failed</h3><p class="bad">${esc(e.message)}</p>`)}}

  function patchDeviceEditor(){
    if(typeof editDevice!=='function'||editDevice.__qosPatched)return;
    const old=editDevice;
    const wrapped=function(deviceId){
      old(deviceId);const d=(typeof S!=='undefined'&&S?S.devices:[]).find(x=>x.id===deviceId);const body=id('modalBody');if(!d||!body||id('devicePrioritySelect'))return;
      const wrap=document.createElement('div');wrap.className='notice';wrap.innerHTML=`<label>Bandwidth Priority<select id="devicePrioritySelect"><option value="high" ${d.priority==='high'?'selected':''}>High</option><option value="normal" ${!d.priority||d.priority==='normal'?'selected':''}>Normal</option><option value="low" ${d.priority==='low'?'selected':''}>Low</option></select></label><small>Priority مستقلة عن Speed Limit وتظهر عند ازدحام الخط.</small><div class="toolbar"><button id="devicePriorityApply">Apply Priority</button></div>`;
      const firstButton=body.querySelector('button');body.insertBefore(wrap,firstButton||null);id('devicePriorityApply').onclick=()=>applyDevicePriority(deviceId);
    };wrapped.__qosPatched=true;window.editDevice=wrapped;
  }

  function patchRender(){
    if(typeof render==='function'&&!render.__qosPatched){const old=render;const wrapped=function(){old();refreshDevices();};wrapped.__qosPatched=true;window.render=wrapped}
  }

  function start(){ensureCard();patchDeviceEditor();patchRender();refreshDevices();loadGamingStatus();setInterval(()=>{renderGameState();const now=Date.now();if(gameState.active&&now>nextStatusPoll){nextStatusPoll=now+30000;loadGamingStatus()}},1000)}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
  window.loadGamingStatus=loadGamingStatus;
})();
