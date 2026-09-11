(()=>{
  const id=x=>document.getElementById(x);

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
        <small>Current detected IP: <b class="sensitive">${sensitive(d.ip||'—')}</b></small><br>
        <small>${d.reserved_ip?`Reserved IP: <b class="sensitive">${sensitive(d.reserved_ip)}</b>. `:''}اترك الحقل فارغاً لإلغاء الحجز. بعد تغيير الحجز قد يحتاج الجهاز إلى فصل/إعادة اتصال أو DHCP renew ليأخذ العنوان الجديد.</small>`;
      if(anchor)anchor.insertAdjacentElement('afterend',block);
      else body.insertBefore(block,body.querySelector('button')||null);
    };
    wrapped.__staticIpPatched=true;
    window.editDevice=wrapped;
  }

  function patchSaveDevice(){
    if(typeof saveDevice!=='function'||saveDevice.__staticIpPatched)return;
    const wrapped=async function(deviceId){
      const reserved=(id('dReservedIp')?.value||'').trim();
      try{
        await api('/api/device/update',{
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
        closeModal();
        await refreshAll();
        toast(`<h3>تم حفظ الجهاز</h3><p>${reserved?`تم حجز <b>${esc(reserved)}</b> لهذا الجهاز عبر DHCP.`:'تم إلغاء حجز الـ IP الثابت.'}</p><small>إذا كان الجهاز متصلاً بالفعل، افصل وأعد الاتصال بالشبكة أو نفّذ DHCP renew لتطبيق العنوان الجديد.</small>`);
      }catch(e){
        toast(`<h3>فشل حفظ Static IP</h3><p class="bad">${esc(e.message)}</p>`);
      }
    };
    wrapped.__staticIpPatched=true;
    window.saveDevice=wrapped;
  }

  function start(){patchDeviceEditor();patchSaveDevice()}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start);else start();
})();
