(()=>{
  const $$=(s,r=document)=>Array.from(r.querySelectorAll(s));
  const byId=id=>document.getElementById(id);

  const css=document.createElement('style');
  css.textContent=`
    .quota-panel{margin:.7rem 0;padding:.7rem;border:1px solid var(--border,#30363d);border-radius:10px}
    .quota-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:.5rem;margin:.45rem 0}
    .quota-grid small{display:block;opacity:.7}.quota-grid b{display:block;margin-top:.15rem}
    .quota-progress{height:9px;overflow:hidden;border-radius:999px;background:rgba(127,127,127,.2);margin:.5rem 0}
    .quota-progress i{display:block;height:100%;width:0;background:currentColor;transition:width .25s ease}
    .quota-panel.warning{color:#d6a700}.quota-panel.critical{color:#e67e22}.quota-panel.exceeded{color:#e74c3c}
    .quota-status{font-weight:700}.quota-actions{display:flex;flex-wrap:wrap;gap:.35rem;margin-top:.55rem}
    .quota-actions button{padding:.35rem .55rem}
  `;
  document.head.appendChild(css);

  function bytes(v){try{return fmt(Number(v||0))}catch{return '0 B'}}
  function modeLabel(u){
    const m=String(u.quota_mode||'fixed').toLowerCase();
    if(m==='unlimited'||u.exempt)return 'Unlimited';
    if(m==='auto'||m==='shared')return 'Auto';
    if(m==='disabled')return 'Disabled';
    return 'Fixed';
  }
  function statusLabel(s){return ({active:'✓ Active',warning:'⚠ Warning',critical:'⚠ Critical',exceeded:'⛔ Quota Exceeded',unlimited:'∞ Unlimited',disabled:'Disabled'})[s]||s||'Active'}

  function panelHtml(u){
    const unlimited=u.quota_mode==='unlimited'||u.exempt;
    const quota=Number(u.effective_quota_gb||0);
    const topup=Number(u.topup_gb||0);
    const used=Number(u.quota_used_bytes??u.usage_bytes??0);
    const rem=u.quota_remaining_bytes;
    const pct=Math.max(0,Number(u.quota_percent||0));
    const width=Math.min(100,pct);
    return `<div class="quota-panel ${esc(u.quota_status||'active')}" data-quota-user="${u.id}">
      <div class="row"><b>${esc(modeLabel(u))}</b><span class="quota-status" data-q="status">${esc(statusLabel(u.quota_status))}</span></div>
      <div class="quota-grid">
        <div><small>Quota</small><b data-q="quota">${unlimited?'Unlimited':quota.toFixed(2)+' GB'}</b></div>
        <div><small>Used</small><b data-q="used">${bytes(used)}</b></div>
        <div><small>Remaining</small><b data-q="remaining">${unlimited?'Unlimited':bytes(rem||0)}</b></div>
        <div><small>Usage</small><b data-q="percent">${unlimited?'—':pct.toFixed(1)+'%'}</b></div>
      </div>
      ${topup>0&&!unlimited?`<small data-q="topup">Base ${Number(u.quota_gb||0).toFixed(2)} GB + ${topup.toFixed(2)} GB Top Up</small>`:''}
      <div class="quota-progress" ${unlimited?'hidden':''}><i data-q="bar" style="width:${width}%"></i></div>
      <div class="quota-actions">
        <button class="ghost" onclick="quotaQuickTopup(${u.id},1)">+1 GB</button>
        <button class="ghost" onclick="quotaQuickTopup(${u.id},5)">+5 GB</button>
        <button class="ghost" onclick="topup(${u.id})">Custom Top Up</button>
        <button class="ghost" onclick="editUser(${u.id})">Change Quota</button>
        <button class="danger" onclick="quotaResetUsage(${u.id})">Reset Usage</button>
      </div>
    </div>`;
  }

  function enhanceUserCards(){
    if(!window.S||!Array.isArray(S.users))return;
    const cards=$$('#users .card.user');
    cards.forEach((card,idx)=>{
      const u=S.users[idx];if(!u)return;
      card.dataset.userId=String(u.id);
      const old=card.querySelector('.quota-panel');if(old)old.remove();
      const firstRow=card.querySelector(':scope > .row');
      if(firstRow)firstRow.insertAdjacentHTML('afterend',panelHtml(u));
      const children=Array.from(card.children);
      const panel=card.querySelector('.quota-panel');
      for(const el of children){
        if(el===panel||el===firstRow)continue;
        if(el.classList.contains('progress'))el.style.display='none';
      }
      if(children[1]&&children[1]!==panel)children[1].style.display='none';
      const pill=firstRow?.querySelector('.pill');if(pill)pill.textContent=modeLabel(u);
    });
  }

  const originalRenderUsers=window.renderUsers;
  if(typeof originalRenderUsers==='function'){
    window.renderUsers=function(){originalRenderUsers();enhanceUserCards()};
  }

  window.quotaQuickTopup=async function(id,gb){
    try{await api('/api/user/topup',{id,gb});await refreshAll()}catch(e){toast(`<h3>خطأ</h3><p class="bad">${esc(e.message)}</p>`)}
  };
  window.quotaResetUsage=async function(id){
    if(!confirm('تصفير استهلاك الحصة لهذا المستخدم فقط؟ سيظل سجل الاستهلاك التاريخي محفوظاً.'))return;
    try{await api('/api/user/update',{id,quota_reset:true});await refreshAll()}catch(e){toast(`<h3>خطأ</h3><p class="bad">${esc(e.message)}</p>`)}
  };

  window.showAddUser=function(){modal(`<h2>مستخدم جديد</h2><label>الاسم<input id="mName"></label><label>نوع الحصة<select id="mMode"><option value="fixed">Fixed</option><option value="auto">Auto</option><option value="unlimited">Unlimited</option></select></label><label>Quota GB<input id="mQuota" type="number" min="0" step="0.1" value="5"></label><label>Download kbit<input id="mDown" type="number" value="0"></label><label>Upload kbit<input id="mUp" type="number" value="0"></label><button onclick="addUser()">إنشاء</button>`)};
  window.editUser=function(id){
    const u=S.users.find(x=>x.id===id),m=u.quota_mode==='shared'?'auto':u.quota_mode;
    const legacy=m==='disabled'?'<option value="disabled" selected>Disabled (Legacy)</option>':'';
    modal(`<h2>تعديل ${esc(u.name)}</h2><label>الاسم<input id="mName" value="${esc(u.name)}"></label><label>الحصة<select id="mMode"><option value="fixed" ${m==='fixed'?'selected':''}>Fixed</option><option value="auto" ${m==='auto'?'selected':''}>Auto</option><option value="unlimited" ${m==='unlimited'?'selected':''}>Unlimited</option>${legacy}</select></label><label>Quota<input id="mQuota" type="number" min="0" step="0.1" value="${u.quota_gb}"></label><label>Down kbit<input id="mDown" type="number" value="${u.speed_down_kbit}"></label><label>Up kbit<input id="mUp" type="number" value="${u.speed_up_kbit}"></label><label>Custom DNS<input id="mDns" value="${esc(u.dns_server||'')}"></label><label>History days<input id="mHist" type="number" min="1" max="90" value="${u.history_days||7}"></label><label><input id="mEx" type="checkbox" ${u.exempt?'checked':''}> Quota Exempt</label><label><input id="mEn" type="checkbox" ${u.enabled?'checked':''}> Enabled</label><button onclick="saveUser(${id})">حفظ</button>`)
  };

  window.updateLiveQuota=function(users){
    for(const q of users||[]){
      const panel=document.querySelector(`[data-quota-user="${Number(q.user_id)}"]`);if(!panel)continue;
      panel.className='quota-panel '+String(q.status||'active');
      const set=(name,val)=>{const el=panel.querySelector(`[data-q="${name}"]`);if(el)el.textContent=val};
      const unlimited=q.status==='unlimited'||q.quota_mode==='unlimited'||q.exempt;
      set('quota',unlimited?'Unlimited':Number(q.effective_quota_gb||0).toFixed(2)+' GB');
      set('used',bytes(q.used_bytes));
      set('remaining',unlimited?'Unlimited':bytes(q.remaining_bytes||0));
      set('percent',unlimited?'—':Number(q.percent||0).toFixed(1)+'%');
      set('status',statusLabel(q.status));
      const bar=panel.querySelector('[data-q="bar"]');if(bar)bar.style.width=Math.min(100,Math.max(0,Number(q.percent||0)))+'%';
      const prog=panel.querySelector('.quota-progress');if(prog)prog.hidden=unlimited;
    }
  };

  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',enhanceUserCards);else enhanceUserCards();
})();
