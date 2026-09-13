(()=>{
  const CARD_ID='shutdownServerCard';

  function installStyle(){
    if(document.getElementById('shutdownFeatureStyle'))return;
    const s=document.createElement('style');
    s.id='shutdownFeatureStyle';
    s.textContent=`
      #${CARD_ID}{border-color:#6d3340;background:linear-gradient(180deg,#21151add,#120f13dd)}
      #shutdownServerBtn{width:100%;margin-top:8px}
      #shutdownConfirmText{width:100%;direction:ltr;text-align:left;text-transform:uppercase;letter-spacing:.08em}
      #shutdownConfirmBtn:disabled,#shutdownServerBtn:disabled{opacity:.45;cursor:not-allowed}
      #shutdownModalState{min-height:1.4em;margin-top:8px}
    `;
    document.head.appendChild(s);
  }

  function installCard(){
    if(typeof $!=='function')return false;
    const admin=$('admin');
    if(!admin)return false;
    if($(CARD_ID))return true;
    installStyle();
    const card=document.createElement('div');
    card.id=CARD_ID;
    card.className='card';
    card.innerHTML=`
      <h3>إيقاف الخادم</h3>
      <p class="muted">إيقاف لابتوب QuotaGate بالكامل. سيتوقف الإنترنت ولوحة التحكم حتى يتم تشغيل الجهاز مرة أخرى.</p>
      <button id="shutdownServerBtn" class="danger" type="button" onclick="openShutdownDialog()">Shutdown Server</button>
      <div id="shutdownServerStatus" class="muted" style="margin-top:8px">يتطلب تأكيداً إضافياً قبل الإيقاف.</div>
    `;
    const grid=admin.querySelector('.grid');
    if(grid)grid.appendChild(card);else admin.prepend(card);
    return true;
  }

  function openShutdownDialog(){
    modal(`
      <h2>إيقاف جهاز QuotaGate بالكامل</h2>
      <div class="notice warnbox">
        <b>تحذير:</b> سيتم إيقاف اللابتوب بالكامل، وسيتوقف الإنترنت المار عبر QuotaGate.
      </div>
      <p>للتأكيد اكتب <code>SHUTDOWN</code> بالضبط:</p>
      <input id="shutdownConfirmText" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="SHUTDOWN">
      <div id="shutdownModalState" class="muted"></div>
      <div class="toolbar">
        <button class="ghost" type="button" onclick="closeModal()">إلغاء</button>
        <button id="shutdownConfirmBtn" class="danger" type="button" disabled onclick="confirmServerShutdown()">Confirm Shutdown</button>
      </div>
    `);
    const input=$('shutdownConfirmText');
    if(input){
      input.addEventListener('input',syncShutdownConfirm);
      input.focus();
    }
  }

  function syncShutdownConfirm(){
    const input=$('shutdownConfirmText'),btn=$('shutdownConfirmBtn');
    if(btn)btn.disabled=!input||input.value!=='SHUTDOWN';
  }

  async function confirmServerShutdown(){
    const input=$('shutdownConfirmText'),btn=$('shutdownConfirmBtn'),state=$('shutdownModalState');
    if(!input||input.value!=='SHUTDOWN'){
      if(state){state.textContent='اكتب SHUTDOWN بالضبط للمتابعة.';state.className='bad'}
      return;
    }
    if(btn)btn.disabled=true;
    input.disabled=true;
    if(state){state.textContent='جاري إرسال أمر الإيقاف...';state.className='muted'}
    try{
      const result=await api('/api/admin/shutdown',{confirm:'SHUTDOWN'});
      const mainBtn=$('shutdownServerBtn'),cardState=$('shutdownServerStatus');
      if(mainBtn)mainBtn.disabled=true;
      if(cardState){cardState.textContent='Server is shutting down...';cardState.className='ok'}
      $('modalBody').innerHTML=`<h2>Server is shutting down...</h2><p>${esc(result.message||'Shutdown command sent. Server is powering off.')}</p><p class="muted">قد تنقطع لوحة التحكم خلال ثوانٍ.</p>`;
    }catch(e){
      input.disabled=false;
      if(btn)btn.disabled=input.value!=='SHUTDOWN';
      if(state){state.textContent=e.message||'Shutdown failed';state.className='bad'}
    }
  }

  window.openShutdownDialog=openShutdownDialog;
  window.syncShutdownConfirm=syncShutdownConfirm;
  window.confirmServerShutdown=confirmServerShutdown;

  function boot(){if(!installCard())setTimeout(boot,50)}
  boot();
})();
