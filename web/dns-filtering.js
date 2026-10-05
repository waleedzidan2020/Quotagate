(()=>{
  const GROUP_LABELS={
    'youtube.com':'YouTube كامل (youtube.com + youtu.be + googlevideo.com + ytimg.com + YouTube API)'
  };

  function installInfo(){
    const domain=$('dnsDomain');
    if(!domain)return;
    domain.placeholder='youtube.com';
    const card=domain.closest('.card');
    if(!card||document.getElementById('dnsEnforcementInfo'))return;
    const info=document.createElement('div');
    info.id='dnsEnforcementInfo';
    info.className='notice';
    info.innerHTML='<b id="dnsRuntimeStatus" class="muted">DNS Enforcement: checking...</b><br><small>اكتب الدومين فقط مثل youtube.com. يتم حجب الدومين وكل Subdomains، ويتم إجبار DNS العادي TCP/UDP 53 على المرور عبر QuotaGate مع منع DNS-over-TLS على 853. اختيار جهاز يطبق القاعدة على هذا الجهاز فقط.</small><br><small class="muted">يتم أيضًا إنشاء حظر IP قصير المدى للدومينات المحجوبة لقطع جلسات HTTPS/QUIC المفتوحة أو عناوين DNS المخزنة. Secure DNS / DoH غير المعروف عبر HTTPS 443 لا يمكن اكتشافه عالميًا بدون اعتراض HTTPS.</small>';
    card.appendChild(info);
    loadDnsRuntimeStatus();
  }

  async function loadDnsRuntimeStatus(){
    const box=$('dnsRuntimeStatus');
    if(!box)return;
    try{
      const j=await api('/api/dns/status');
      const e=j.enforcement||{};
      const g=j.ip_guard||{};
      if(j.ok){
        box.textContent='DNS Enforcement: ACTIVE ✅ — UDP/TCP proxy + forced DNS' + (e.dot_block?' + DoT blocked':'') + (g.enabled?' + Active-session IP guard ('+(g.tracked_ips||0)+')':'');
        box.className='ok';
      }else{
        const missing=[];
        if(!j.proxy_udp)missing.push('UDP proxy');
        if(!j.proxy_tcp)missing.push('TCP proxy');
        if(e.enabled&&!e.udp53)missing.push('UDP/53 redirect');
        if(e.enabled&&!e.tcp53)missing.push('TCP/53 redirect');
        if(e.enabled&&!e.dot_block)missing.push('DoT block');
        if(g.enabled&&!g.ok)missing.push('active-session IP guard');
        box.textContent='DNS Enforcement: ERROR ❌'+(missing.length?' — '+missing.join(', '):'');
        box.className='bad';
      }
    }catch(e){
      box.textContent='DNS Enforcement: status unavailable';
      box.className='bad';
    }
  }

  function targetOptions(){
    const scope=$('dnsScope'),target=$('dnsScopeId');
    if(!scope||!target)return;
    const value=scope.value;
    const prev=target.value;
    if(value==='global'){
      target.innerHTML='<option value="0">كل الأجهزة</option>';
      target.value='0';
      target.disabled=true;
      return;
    }
    target.disabled=false;
    if(value==='device'){
      const rows=(S&&S.devices)||[];
      target.innerHTML='<option value="">اختر جهازاً...</option>'+rows.map(d=>`<option value="${d.id}">${esc(d.name||('Device '+d.id))} — ${PRIVACY?'hidden':esc(d.ip||'no IP')}</option>`).join('');
    }else{
      const rows=(S&&S.users)||[];
      target.innerHTML='<option value="">اختر مستخدماً...</option>'+rows.map(u=>`<option value="${u.id}">${esc(u.name||('User '+u.id))}</option>`).join('');
    }
    if([...target.options].some(o=>o.value===prev))target.value=prev;
  }

  function installTargetPicker(){
    const scope=$('dnsScope'),old=$('dnsScopeId');
    if(!scope||!old)return false;
    if(old.tagName!=='SELECT'){
      const sel=document.createElement('select');
      sel.id='dnsScopeId';
      sel.setAttribute('aria-label','DNS target');
      old.replaceWith(sel);
    }
    if(!scope.dataset.qgDnsPicker){
      scope.dataset.qgDnsPicker='1';
      scope.addEventListener('change',targetOptions);
    }
    targetOptions();
    installInfo();
    return true;
  }

  function scopeText(r){
    if(r.scope_type==='global')return 'كل الأجهزة';
    if(r.scope_type==='device'){
      const d=((S&&S.devices)||[]).find(x=>Number(x.id)===Number(r.scope_id));
      return d?'جهاز: '+esc(d.name||('Device '+d.id)):'جهاز #'+Number(r.scope_id);
    }
    if(r.scope_type==='user'){
      const u=((S&&S.users)||[]).find(x=>Number(x.id)===Number(r.scope_id));
      return u?'مستخدم: '+esc(u.name||('User '+u.id)):'مستخدم #'+Number(r.scope_id);
    }
    return esc(r.scope_type)+':'+Number(r.scope_id||0);
  }

  async function enhancedLoadDnsRules(){
    try{
      const j=await api('/api/dns/rules');
      $('dnsRules').innerHTML=(j.rules||[]).map(r=>{
        const group=GROUP_LABELS[String(r.domain||'').toLowerCase()];
        return `<div class="card"><div class="row"><b>${esc(r.domain)}</b><span class="pill">${scopeText(r)}</span><span class="pill ${r.action==='block'?'bad':r.action==='allow'?'ok':''}">${esc(r.action)}</span><button class="danger" onclick="delDns(${r.id})">حذف</button></div>${group?`<small>${esc(group)}</small>`:''}</div>`;
      }).join('')||'<div class="muted">لا توجد قواعد DNS.</div>';
    }catch(e){
      $('dnsRules').innerHTML=`<div class="bad">${esc(e.message||'DNS rules failed')}</div>`;
    }
  }

  async function enhancedAddDnsRule(){
    const scope=$('dnsScope').value;
    const raw=$('dnsDomain').value.trim();
    const targetId=scope==='global'?0:Number($('dnsScopeId').value||0);
    if(!raw)return toast('<h3>خطأ</h3><p class="bad">اكتب الدومين، مثال: youtube.com</p>');
    if(scope!=='global'&&!targetId)return toast('<h3>خطأ</h3><p class="bad">اختر الجهاز أو المستخدم أولاً.</p>');
    try{
      await api('/api/dns/rule/add',{
        scope_type:scope,
        scope_id:targetId,
        domain:raw,
        action:$('dnsAction').value,
        target:$('dnsTarget').value.trim()
      });
      $('dnsDomain').value='';
      await enhancedLoadDnsRules();
      setTimeout(loadDnsRuntimeStatus,800);
      setTimeout(loadDnsRuntimeStatus,2500);
      const suffix=scope==='global'?'على كل الأجهزة':scope==='device'?'على الجهاز المحدد':'على المستخدم المحدد';
      toast(`<h3>تم تطبيق قاعدة DNS</h3><p><b>${esc(raw)}</b> — ${esc(suffix)}</p>`);
    }catch(e){
      toast(`<h3>فشل إضافة القاعدة</h3><p class="bad">${esc(e.message||'unknown error')}</p>`);
    }
  }

  window.loadDnsRules=enhancedLoadDnsRules;
  window.addDnsRule=enhancedAddDnsRule;

  const originalRender=window.render;
  if(typeof originalRender==='function'){
    window.render=function(){
      const out=originalRender.apply(this,arguments);
      setTimeout(()=>{installTargetPicker();targetOptions();loadDnsRuntimeStatus();},0);
      return out;
    };
  }

  function boot(){
    if(!installTargetPicker())return setTimeout(boot,60);
    targetOptions();
    loadDnsRuntimeStatus();
    if(S)enhancedLoadDnsRules();
  }
  boot();
})();
