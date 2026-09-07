(()=>{
  const state={cards:new Map(),started:false};
  const $u=id=>document.getElementById(id);

  function formatBytes(bytes){
    let n=Math.max(0,Number(bytes||0));
    const units=['B','KB','MB','GB','TB'];let i=0;
    while(n>=1024&&i<units.length-1){n/=1024;i++}
    const digits=i===0?0:2;
    return n.toFixed(digits)+' '+units[i];
  }
  function formatSpeed(v){return Math.max(0,Number(v||0)).toFixed(2)+' Mbps'}
  function privateValue(v){
    try{return PRIVACY?'••••••':String(v||'-')}catch{return String(v||'-')}
  }

  function ensureLayout(){
    const management=$u('management');if(!management)return false;
    if($u('liveUsageSection'))return true;
    const sec=document.createElement('div');sec.id='liveUsageSection';
    sec.innerHTML=`
      <div class="row"><h2>استهلاك الإنترنت Live</h2><span id="usageTrackerState" class="pill">Starting…</span></div>
      <div class="card">
        <h3>استهلاك الخط اليوم</h3>
        <div class="grid stats">
          <div><small>Total</small><div id="usageGatewayTotal"><b>0 B</b></div></div>
          <div><small>Download ↓</small><div id="usageGatewayDown"><b>0 B</b></div></div>
          <div><small>Upload ↑</small><div id="usageGatewayUp"><b>0 B</b></div></div>
          <div><small>Live</small><div><b id="usageGatewayLiveDown">0.00 Mbps</b> ↓ / <b id="usageGatewayLiveUp">0.00 Mbps</b> ↑</div></div>
        </div>
        <small>يتم حساب Internet traffic المار عبر QuotaGate فقط. قد يختلف رقم شركة الإنترنت بسبب overhead وطريقة المحاسبة.</small>
      </div>
      <h3>استهلاك الأجهزة اليوم</h3>
      <div id="liveUsageDevices" class="grid"></div>`;
    const stats=management.querySelector('.grid.stats');
    if(stats&&stats.nextSibling)management.insertBefore(sec,stats.nextSibling);else management.appendChild(sec);
    return true;
  }

  function makeCard(d){
    const card=document.createElement('div');card.className='card';card.dataset.deviceId=String(d.device_id);
    const title=document.createElement('div');title.className='row';
    const name=document.createElement('b');name.dataset.field='name';
    const ip=document.createElement('span');ip.className='muted';ip.dataset.field='ip';
    title.append(name,ip);
    const total=document.createElement('div');total.innerHTML='<small>Today</small> <b data-field="total">0 B</b>';
    const down=document.createElement('div');down.innerHTML='<small>Download ↓</small> <b data-field="down">0 B</b>';
    const up=document.createElement('div');up.innerHTML='<small>Upload ↑</small> <b data-field="up">0 B</b>';
    const live=document.createElement('div');live.innerHTML='<small>Live</small> <b data-field="liveDown">0.00 Mbps</b> ↓ / <b data-field="liveUp">0.00 Mbps</b> ↑';
    card.append(title,total,down,up,live);
    return card;
  }

  function setField(card,name,value){const el=card.querySelector(`[data-field="${name}"]`);if(el)el.textContent=value}

  function updateDevices(devices){
    const box=$u('liveUsageDevices');if(!box)return;
    const seen=new Set();
    for(const d of devices||[]){
      const id=Number(d.device_id);seen.add(id);
      let card=state.cards.get(id);
      if(!card){card=makeCard(d);state.cards.set(id,card);box.appendChild(card)}
      setField(card,'name',d.name||('Device '+id));
      setField(card,'ip',privateValue(d.ip));
      setField(card,'total',formatBytes(d.today_total_bytes));
      setField(card,'down',formatBytes(d.today_down_bytes));
      setField(card,'up',formatBytes(d.today_up_bytes));
      setField(card,'liveDown',formatSpeed(d.live_down_mbps));
      setField(card,'liveUp',formatSpeed(d.live_up_mbps));
    }
    for(const [id,card] of state.cards){if(!seen.has(id)){card.remove();state.cards.delete(id)}}
    if(!state.cards.size){
      if(!$u('usageEmpty')){const x=document.createElement('div');x.id='usageEmpty';x.className='card muted';x.textContent='لا توجد أجهزة مسجلة حالياً.';box.appendChild(x)}
    }else{$u('usageEmpty')?.remove()}
  }

  function updateView(j){
    const g=j.gateway||{},t=j.tracker||{};
    $u('usageGatewayTotal').textContent=formatBytes(g.today_total_bytes);
    $u('usageGatewayDown').textContent=formatBytes(g.today_down_bytes);
    $u('usageGatewayUp').textContent=formatBytes(g.today_up_bytes);
    $u('usageGatewayLiveDown').textContent=formatSpeed(g.live_down_mbps);
    $u('usageGatewayLiveUp').textContent=formatSpeed(g.live_up_mbps);
    const st=$u('usageTrackerState');if(st){st.textContent=t.running?`Live • ${t.poll_seconds}s`:'Stopped';st.className='pill '+(t.running?'ok':'bad')}
    updateDevices(j.devices||[]);
    if(typeof window.updateLiveQuota==='function')window.updateLiveQuota(j.users||[]);
  }

  async function refreshUsage(){
    if(!ensureLayout())return;
    const app=$u('app'),management=$u('management');
    if(!app||app.hidden||!management?.classList.contains('active'))return;
    try{
      const j=await api('/api/usage/live');updateView(j);
    }catch(e){const st=$u('usageTrackerState');if(st){st.textContent='Usage error';st.className='pill bad'}}
  }

  function start(){if(state.started)return;state.started=true;ensureLayout();refreshUsage();setInterval(refreshUsage,2000)}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
  window.refreshLiveUsage=refreshUsage;
})();
