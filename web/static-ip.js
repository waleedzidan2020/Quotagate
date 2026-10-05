(()=>{
  const id=x=>document.getElementById(x);

  function statusText(s){
    if(!s)return 'الحالة غير متاحة.';
    if(s.matches_reservation)return 'Reservation Applied ✅ — الجهاز يستخدم العنوان المحجوز.';
    if(s.connected&&s.manual_static_hint)return 'Manual Static detected ✅ — QuotaGate شايف الجهاز، لكن DHCP Reservation لا يغيّر إعداد IP اليدوي داخل الجهاز.';
    if(s.connected&&s.reserved_ip)return 'الجهاز متصل لكن لم يأخذ العنوان المحجوز بعد.';
    if(!s.connected&&s.reserved_ip)return 'الحجز محفوظ. سيتم تطبيقه عند اتصال الجهاز عبر DHCP.';
    if(s.connected)return 'الجهاز متصل وQuotaGate قادر على اكتشاف عنوانه الحالي.';
    return 'الجهاز غير متصل حالياً.';
  }

  function renderStaticStatus(s){
    const box=id('dStaticStatus');if(!box)return;
    const cls=s?.matches_reservation?'ok':s?.connected?'warn':'muted';
    box.innerHTML=`
      <div class="${cls}"><b>${esc(statusText(s))}</b></div>
      <small>
        Current: <b class="sensitive">${sensitive(s?.current_ip||'—')}</b>
        • DHCP lease: <b class="sensitive">${sensitive(s?.lease_ip||'—')}</b>
        • Reserved: <b class="sensitive">${sensitive(s?.reserved_ip||'—')}</b>
      </small>`;
    const btn=id('dStaticApplyNow');
    if(btn)btn.disabled=!s?.reserved_ip;
  }

  async function loadStaticIpStatus(deviceId){
    const box=id('dStaticStatus');if(box)box.textContent='Checking static IP status...';
    try{
      const s=await api(`/api/device/static-ip/status?device_id=${deviceId}`);
      renderStaticStatus(s);
      return s;
    }catch(e){
      if(box)box.innerHTML=`<span class="bad">${esc(e.message||'Status failed')}</span>`;
      return null;
    }
  }

  function patchDeviceEditor(){
    if(typeof editDevice!=='function'||editDevice.__staticIpPatched)return;
    const old=editDevice;
    const wrapped=function(deviceId){
      old(deviceId);
      const d=(typeof S!=='undefined'&&S?S.devices:[]).find(x=>x.id===deviceId);
      const body=id('modalBody');
      if(!d||!body||id('dReservedIp'))return;

      const dns=id('dDns');
      const anchor=dns&&dns.closest('label');
      const block=document.createElement('div');
      block.className='notice';
      block.innerHTML=`
        <label>Static IP / DHCP Reservation
          <input id="dReservedIp" inputmode="decimal" autocomplete="off" placeholder="مثال: 192.168.2.50" value="${esc(d.reserved_ip||'')}">
        </label>
        <div id="dStaticStatus" class="muted">Checking static IP status...</div>
        <div class="row">
          <button id="dStaticApplyNow" type="button" class="ghost" onclick="applyStaticIpNow(${deviceId})" ${d.reserved_ip?'':'disabled'}>Apply / Reconnect Now</button>
        </div>
        <small>
          QuotaGate يكتشف الأجهزة التي تستخدم Manual Static IP عن طريق ARP حتى بدون DHCP.
          أما DHCP Reservation فيتحكم فقط في الأجهزة المضبوطة على Obtain IP automatically.
          عند تغيير الحجز سيحاول QuotaGate فصل الجهاز المحدد من Wi-Fi فقط ليعيد الاتصال ويطلب العنوان الجديد.
        </small>`;
      if(anchor)anchor.insertAdjacentElement('afterend',block);
      else body.insertBefore(block,body.querySelector('button')||null);
      loadStaticIpStatus(deviceId);
    };
    wrapped.__staticIpPatched=true;
    window.editDevice=wrapped;
  }

  async function applyStaticIpNow(deviceId){
    const btn=id('dStaticApplyNow');if(btn)btn.disabled=true;
    try{
      const j=await api('/api/device/static-ip/apply',{device_id:deviceId});
      const s=j.static_ip||{};
      renderStaticStatus(s);
      if(s.reconnect_sent){
        toast('<h3>تم إرسال Reconnect</h3><p>تم فصل الجهاز المحدد من Wi-Fi فقط. إذا كان على DHCP سيعيد الاتصال ويطلب الـ IP المحجوز.</p>');
      }else if(s.manual_static_hint){
        toast('<h3>Manual Static</h3><p>QuotaGate شايف الجهاز، لكن لا يمكن تغيير IP اليدوي داخل نظام الجهاز من السيرفر. غيّر الجهاز إلى DHCP إذا أردت تطبيق الـ Reservation.</p>');
      }else{
        toast('<h3>الحجز جاهز</h3><p>سيتم تطبيقه عند اتصال الجهاز القادم عبر DHCP.</p>');
      }
      setTimeout(()=>loadStaticIpStatus(deviceId),2500);
      setTimeout(()=>{if(typeof refreshAll==='function')refreshAll()},3500);
    }catch(e){
      toast(`<h3>فشل تطبيق Static IP</h3><p class="bad">${esc(e.message||'unknown error')}</p>`);
    }finally{
      if(btn)btn.disabled=false;
    }
  }

  function patchSaveDevice(){
    if(typeof saveDevice!=='function'||saveDevice.__staticIpPatched)return;
    const wrapped=async function(deviceId){
      const reserved=(id('dReservedIp')?.value||'').trim();
      try{
        const j=await api('/api/device/update',{
          id:deviceId,
          name:id('dName').value,
          user_id:id('dUser').value?+id('dUser').value:null,
          enabled:id('dEn').checked?1:0,
          blocked_manual:id('dBlock').checked?1:0,
          exempt:id('dEx').checked?1:0,
          speed_down_kbit:+id('dDown').value,
          speed_up_kbit:+id('dUp').value,
          dns_server:id('dDns').value,
          reserved_ip:reserved
        });
        const s=j.static_ip||null;
        closeModal();
        await refreshAll();
        let note=reserved?`تم حجز <b>${esc(reserved)}</b> لهذا الجهاز عبر DHCP.`:'تم إلغاء حجز الـ IP.';
        if(s?.reconnect_sent)note+=' تم إرسال Reconnect للجهاز المحدد ليطلب العنوان الجديد.';
        else if(s?.manual_static_hint)note+=' الجهاز ظاهر كـ Manual Static؛ QuotaGate سيكتشفه ويديره، لكن الـ Reservation لن يغيّر IP اليدوي داخل الجهاز.';
        else if(reserved&&!s?.matches_reservation)note+=' الحجز جاهز وسيطبق عند اتصال الجهاز عبر DHCP.';
        toast(`<h3>تم حفظ الجهاز</h3><p>${note}</p>`);
      }catch(e){
        toast(`<h3>فشل حفظ Static IP</h3><p class="bad">${esc(e.message)}</p>`);
      }
    };
    wrapped.__staticIpPatched=true;
    window.saveDevice=wrapped;
  }

  function start(){patchDeviceEditor();patchSaveDevice()}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
  window.applyStaticIpNow=applyStaticIpNow;
  window.loadStaticIpStatus=loadStaticIpStatus;
})();
