(()=>{
  const state={overview:null,table:null,offset:0,limit:50,started:false};
  const byId=id=>document.getElementById(id);
  const safe=s=>esc(String(s??''));
  const fmtCount=n=>Number(n||0).toLocaleString();
  const riskLabel=r=>r==='safe'?'SAFE':r==='danger'?'DANGER':'CAUTION';

  const css=document.createElement('style');
  css.textContent=`
    #maintenance .maint-summary{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}
    #maintenance .maint-card{position:relative}.maint-risk{font-weight:700}.maint-risk.safe{color:#2ecc71}.maint-risk.caution{color:#f39c12}.maint-risk.danger{color:#e74c3c}
    #maintenance .maint-sections{display:flex;flex-wrap:wrap;gap:.4rem;margin:12px 0}.maint-table-wrap{overflow:auto;max-height:62vh}.maint-table{width:100%;border-collapse:collapse;direction:ltr;text-align:left}.maint-table th,.maint-table td{padding:7px;border-bottom:1px solid rgba(127,127,127,.25);white-space:nowrap}.maint-table code{font-size:12px}.maint-danger{border:1px solid #e74c3c}.maint-actions{display:flex;flex-wrap:wrap;gap:.35rem}.maint-muted{opacity:.72}
  `;document.head.appendChild(css);

  function installTab(){
    const tabs=byId('tabs'),main=document.querySelector('main');if(!tabs||!main||byId('maintenance'))return false;
    const b=document.createElement('button');b.dataset.tab='maintenance';b.textContent='Maintenance';tabs.insertBefore(b,tabs.querySelector('[data-tab="admin"]'));
    const sec=document.createElement('section');sec.id='maintenance';sec.className='tab';sec.innerHTML=`
      <div class="row"><div><h2>Maintenance Center</h2><small>إدارة SQLite، الاستهلاك، السجلات والبيانات الداخلية بأمان.</small></div><button class="ghost" id="maintRefresh">تحديث</button></div>
      <div id="maintSummary" class="grid stats maint-summary"></div>
      <div class="toolbar"><button id="maintBackup">Backup Database</button><button class="ghost" id="maintVacuum">VACUUM</button></div>
      <h3>Usage & Quota</h3><div id="maintUsage" class="card"></div>
      <h3>Database Tables</h3><div id="maintTables" class="grid"></div>
      <h3>Danger Zone</h3><div id="maintDanger" class="card maint-danger"></div>`;
    main.appendChild(sec);
    b.onclick=()=>{document.querySelectorAll('#tabs button').forEach(x=>x.classList.remove('active'));document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));b.classList.add('active');sec.classList.add('active');loadOverview()};
    byId('maintRefresh').onclick=loadOverview;byId('maintBackup').onclick=backupDb;byId('maintVacuum').onclick=vacuumDb;
    return true;
  }

  function summaryCard(title,value){return `<div class="card"><small>${safe(title)}</small><div><b>${safe(value)}</b></div></div>`}
  function renderOverview(j){
    state.overview=j;byId('maintSummary').innerHTML=summaryCard('Database',j.database_file||'quotagate.db')+summaryCard('DB Size',fmt(j.database_size_bytes||0))+summaryCard('Tables',j.table_count)+summaryCard('Total Records',fmtCount(j.total_records))+summaryCard('Journal',String(j.journal_mode||'').toUpperCase())+summaryCard('Schema',j.schema_version||'-');
    const box=byId('maintTables');box.innerHTML='';for(const t of j.tables||[])box.appendChild(tableCard(t));renderUsage(j);renderDanger();
  }
  function tableCard(t){
    const d=document.createElement('div');d.className='card maint-card';
    d.innerHTML=`<div class="row"><h3>${safe(t.name)}</h3><span class="maint-risk ${safe(t.risk)}">${riskLabel(t.risk)}</span></div><div><b>${fmtCount(t.records)}</b> records</div><small>${safe(t.description)}</small><div class="maint-muted">${safe(t.category)}</div><div class="maint-actions" style="margin-top:8px"></div>`;
    const a=d.querySelector('.maint-actions');const view=document.createElement('button');view.className='ghost';view.textContent='View Records';view.onclick=()=>viewTable(t.name,0);a.appendChild(view);
    if(t.policy==='clear'||t.policy==='refresh'){const clear=document.createElement('button');clear.className=t.risk==='safe'?'ghost':'warn';clear.textContent='Clear Table';clear.onclick=()=>clearTable(t);a.appendChild(clear)}
    if(t.policy==='view'){const x=document.createElement('small');x.textContent=' View only';a.appendChild(x)}
    return d;
  }
  function countFor(name){return (state.overview?.tables||[]).find(x=>x.name===name)?.records||0}
  function actionBtn(label,action,klass='ghost',msg='هل تريد المتابعة؟'){
    const b=document.createElement('button');b.className=klass;b.textContent=label;b.onclick=async()=>{if(!confirm(msg))return;await runReset(action)};return b
  }
  function renderUsage(){
    const b=byId('maintUsage');b.innerHTML='<div class="maint-actions"></div>';const a=b.firstElementChild;
    a.append(actionBtn(`Reset Today Device (${fmtCount(countFor('usage_daily'))})`,'today_device','warn','سيتم تصفير استهلاك الأجهزة لليوم فقط. التاريخ الأقدم سيبقى.'));
    a.append(actionBtn('Reset Current Month Device','month_device','warn'));
    a.append(actionBtn('Reset Today Gateway','today_gateway','warn'));
    a.append(actionBtn('Reset Current Month Gateway','month_gateway','warn'));
    a.append(actionBtn('Reset Live Baseline','live_baseline','ghost','سيبدأ Live Tracker من baseline الحالية بدون إعادة احتساب البيانات القديمة.'));
    a.append(actionBtn('Reset All Current User Quotas','all_current_quotas','warn','سيتم تصفير quota-used لجميع المستخدمين مع الحفاظ على التاريخ وManual Block.'));
    a.append(actionBtn('Reset Quota Thresholds','quota_thresholds','ghost'));
    a.append(actionBtn('Reset Current Quota Cycle','current_quota_cycle','warn','سيتم حذف بيانات دورة الكوتا الحالية وإعادة reconciliation.'));
    a.append(actionBtn('Clear Old Quota Cycles','quota_history','ghost'));
    a.append(actionBtn('Clear Old Quota State','quota_state_history','ghost'));
    a.append(actionBtn('Clear Seen Alerts','clear_seen_alerts','ghost'));
    a.append(actionBtn('Clear DNS History Today','dns_today','ghost'));
    a.append(actionBtn('Clear DNS Older Than 7 Days','dns_old_7','ghost'));
  }
  function renderDanger(){
    const b=byId('maintDanger');b.innerHTML='<p>العمليات التالية تعمل Backup تلقائي قبل التنفيذ وتتطلب كتابة confirmation token.</p><div class="maint-actions"></div><small>Factory Reset غير متاح من الواجهة لحماية schema والإعدادات.</small>';const a=b.querySelector('.maint-actions');
    [['Delete All Devices','delete_all_devices','DELETE DEVICES'],['Delete All Users','delete_all_users','DELETE USERS'],['Reset ALL Usage Data','reset_all_usage','RESET USAGE'],['Clear ALL Custom Network Rules','clear_network_rules','CLEAR NETWORK RULES']].forEach(x=>{const bt=document.createElement('button');bt.className='danger';bt.textContent=x[0];bt.onclick=()=>dangerAction(x[1],x[2]);a.appendChild(bt)});
  }

  async function loadOverview(){try{renderOverview(await api('/api/maintenance/overview'))}catch(e){toast(`<h3>Maintenance Error</h3><p class="bad">${safe(e.message)}</p>`)}}
  async function runReset(action,user_id=null){try{const r=await api('/api/maintenance/reset',{action,user_id});toast(`<h3>تم</h3><p>Affected: ${fmtCount(r.deleted||0)}</p>`);await loadOverview();if(window.refreshLiveUsage)refreshLiveUsage()}catch(e){toast(`<h3>فشل</h3><p class="bad">${safe(e.message)}</p>`)}}
  async function clearTable(t){const msg=`Clear ${t.name}?\nRecords: ${t.records}\n${t.description}`;if(!confirm(msg))return;try{const r=await api('/api/maintenance/clear-table',{table:t.name});toast(`<h3>تم المسح</h3><p>${fmtCount(r.deleted)} records</p>`);await loadOverview();refreshAll()}catch(e){toast(`<h3>فشل</h3><p class="bad">${safe(e.message)}</p>`)}}
  async function backupDb(){try{const r=await api('/api/maintenance/backup',{});toast(`<h3>Backup Created</h3><p>${safe(r.name)} • ${fmt(r.size_bytes)}</p>`);loadOverview()}catch(e){toast(`<h3>Backup Failed</h3><p class="bad">${safe(e.message)}</p>`)}}
  async function vacuumDb(){if(!confirm('تشغيل VACUUM الآن؟ قد تستغرق العملية بعض الوقت حسب حجم قاعدة البيانات.'))return;try{const r=await api('/api/maintenance/vacuum',{});toast(`<h3>VACUUM Completed</h3><p>${fmt(r.before_bytes)} → ${fmt(r.after_bytes)}</p>`);loadOverview()}catch(e){toast(`<h3>VACUUM Failed</h3><p class="bad">${safe(e.message)}</p>`)}}
  async function dangerAction(action,token){const entered=prompt(`عملية خطرة. اكتب بالضبط:\n${token}`,'');if(entered!==token)return;try{const r=await api('/api/maintenance/danger',{action,confirm:entered});toast(`<h3>تم التنفيذ</h3><p>Affected: ${fmtCount(r.deleted)}<br>Backup: ${safe(r.backup?.name||'-')}</p>`);await loadOverview();await refreshAll()}catch(e){toast(`<h3>فشل</h3><p class="bad">${safe(e.message)}</p>`)}}

  async function viewTable(name,offset=0){
    try{const j=await api(`/api/maintenance/table?name=${encodeURIComponent(name)}&limit=${state.limit}&offset=${offset}`);state.table=j;state.offset=offset;renderTableModal(j)}catch(e){toast(`<h3>Table Error</h3><p class="bad">${safe(e.message)}</p>`)}
  }
  function displayValue(col,v){if(v===null)return 'NULL';const n=String(col||'').toLowerCase();if(PRIVACY&&(n==='ip'||n.endsWith('_ip')||n==='mac'||n.endsWith('_mac')))return '••••••';return String(v)}
  function renderTableModal(j){
    const pk=(j.columns||[]).filter(c=>Number(c.pk)>0).sort((a,b)=>Number(a.pk)-Number(b.pk)).map(c=>c.name);const pages=Math.max(1,Math.ceil(Number(j.total||0)/Number(j.limit||50)));const page=Math.floor(Number(j.offset||0)/Number(j.limit||50))+1;
    modal(`<div class="row"><h2>${safe(j.table)}</h2><span>${fmtCount(j.total)} records</span></div><div class="row"><span class="maint-risk ${safe(j.risk)}">${riskLabel(j.risk)}</span><small>${safe(j.description)}</small></div><div id="maintViewer"></div><div class="toolbar" id="maintViewerActions"></div>`);
    const wrap=document.createElement('div');wrap.className='maint-table-wrap';const table=document.createElement('table');table.className='maint-table';const thead=document.createElement('thead'),trh=document.createElement('tr');
    if(pk.length&&j.table!=='settings'){const th=document.createElement('th');th.textContent='✓';trh.appendChild(th)}
    for(const c of j.columns||[]){const th=document.createElement('th');th.textContent=c.name+(Number(c.pk)>0?' 🔑':'');trh.appendChild(th)}thead.appendChild(trh);table.appendChild(thead);const tb=document.createElement('tbody');
    for(const row of j.rows||[]){const tr=document.createElement('tr');if(pk.length&&j.table!=='settings'){const td=document.createElement('td'),cb=document.createElement('input');cb.type='checkbox';cb.className='maint-row-check';const key={};pk.forEach(k=>key[k]=row[k]);cb.value=JSON.stringify(key);td.appendChild(cb);tr.appendChild(td)}for(const c of j.columns||[]){const td=document.createElement('td');td.textContent=displayValue(c.name,row[c.name]);tr.appendChild(td)}tb.appendChild(tr)}table.appendChild(tb);wrap.appendChild(table);byId('maintViewer').appendChild(wrap);
    const a=byId('maintViewerActions');if(j.table!=='settings'&&pk.length){const del=document.createElement('button');del.className=j.risk==='danger'?'danger':'warn';del.textContent='Delete Selected';del.onclick=()=>deleteSelected(j);a.appendChild(del)}const prev=document.createElement('button');prev.className='ghost';prev.textContent='السابق';prev.disabled=page<=1;prev.onclick=()=>viewTable(j.table,Math.max(0,j.offset-j.limit));a.appendChild(prev);const span=document.createElement('span');span.textContent=`Page ${page}/${pages}`;a.appendChild(span);const next=document.createElement('button');next.className='ghost';next.textContent='التالي';next.disabled=page>=pages;next.onclick=()=>viewTable(j.table,j.offset+j.limit);a.appendChild(next);const sel=document.createElement('select');[25,50,100].forEach(n=>{const o=document.createElement('option');o.value=n;o.textContent=n;o.selected=n===state.limit;sel.appendChild(o)});sel.onchange=()=>{state.limit=Number(sel.value);viewTable(j.table,0)};a.appendChild(sel);
  }
  async function deleteSelected(j){const keys=Array.from(document.querySelectorAll('.maint-row-check:checked')).map(x=>JSON.parse(x.value));if(!keys.length)return alert('اختر record واحد على الأقل.');let confirmToken='';if(j.table==='users'||j.table==='devices'){const token='DELETE '+j.table.toUpperCase();confirmToken=prompt(`لحذف السجلات المختارة اكتب:\n${token}`,'');if(confirmToken!==token)return}else if(!confirm(`Delete ${keys.length} selected record(s) from ${j.table}?`))return;try{const r=await api('/api/maintenance/delete-records',{table:j.table,keys,confirm:confirmToken});toast(`<h3>تم الحذف</h3><p>${fmtCount(r.deleted)} records</p>`);await loadOverview();await refreshAll()}catch(e){toast(`<h3>فشل الحذف</h3><p class="bad">${safe(e.message)}</p>`)}}

  function start(){if(state.started)return;state.started=true;if(!installTab())return;setInterval(()=>{if(byId('maintenance')?.classList.contains('active'))loadOverview()},20000)}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
  window.loadMaintenance=loadOverview;
})();
