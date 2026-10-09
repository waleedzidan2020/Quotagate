// Separate VLAN dashboard. No password is returned from the server.
let VLAN_ITEMS = [];
let VLAN_ACTIVE = [];

async function vlanLoad() {
  const state = document.getElementById('vlanState');
  try {
    const data = await api('/api/vlans');
    VLAN_ITEMS = data.profiles || [];
    VLAN_ACTIVE = data.active_vlan_ids || [];
    state.textContent = 'VLANs running: ' + (VLAN_ACTIVE.join(', ') || 'none') +
      ' — enabled is not the same as applied.';
    const box = document.getElementById('vlanProfiles');
    box.innerHTML = VLAN_ITEMS.map(function(p) {
      const active = VLAN_ACTIVE.includes(p.id);
      return '<div class="card"><div class="row"><h3>' + esc(p.name) +
        ' (VLAN ' + p.id + ')</h3><span class="pill ' + (active ? 'ok' : 'warn') +
        '">' + (active ? 'Running' : 'Not running') +
        '</span></div><div>Trunk: <code>' + esc(p.parent) +
        '</code> → <code>' + esc(p.parent + '.' + p.id) +
        '</code> | Gateway: ' + esc(p.gateway) + '</div>' +
        '<small>DHCP: ' + esc(p.dhcp_start) + '–' + esc(p.dhcp_end) +
        ' | Wi-Fi: ' + (p.wifi_interface ? esc(p.ssid) + ' on ' + esc(p.wifi_interface) : 'external VLAN-aware AP / wired only') +
        ' | Enabled: ' + (p.enabled ? 'Yes' : 'No') + '</small>' +
        '<div class="toolbar"><button onclick="vlanEdit(' + p.id + ')">تعديل</button>' +
        '<button class="danger" onclick="vlanRemove(' + p.id + ')">حذف من الإعدادات</button></div></div>';
    }).join('') || '<p class="muted">لا توجد VLANs محفوظة. أضف شبكة جديدة، واحفظها، ثم طبّقها بعد التأكد من التوصيلات.</p>';
  } catch (e) {
    state.textContent = 'تعذّر قراءة VLANs: ' + e.message;
  }
}

function vlanAdd() { vlanForm(null); }
function vlanEdit(id) { vlanForm(VLAN_ITEMS.find(function(x) { return x.id === id; }) || null); }

function vlanForm(p) {
  p = p || {};
  const v = function(k, fallback) { return esc(p[k] == null ? (fallback || '') : p[k]); };
  const isEdit = !!p.id;
  modal(
    '<h2>' + (isEdit ? 'تعديل VLAN ' + p.id : 'إنشاء VLAN جديدة') + '</h2>' +
    '<label>VLAN ID (2–4094)<input id="vId" type="number" min="2" max="4094" ' +
    (isEdit ? 'readonly ' : '') + 'value="' + v('id',20) + '"></label>' +
    '<label>اسم الشبكة<input id="vName" maxlength="60" value="' + v('name','Guests') + '"></label>' +
    '<label>Dedicated Ethernet trunk (ليس eth0)<input id="vParent" placeholder="eth1 / enx..." value="' + v('parent') + '"></label>' +
    '<label>Gateway /24<input id="vGateway" value="' + v('gateway','192.168.20.1/24') + '"></label>' +
    '<label>DHCP start<input id="vStart" value="' + v('dhcp_start','192.168.20.100') + '"></label>' +
    '<label>DHCP end<input id="vEnd" value="' + v('dhcp_end','192.168.20.200') + '"></label>' +
    '<div class="notice">اختياري: Wi-Fi إضافي مستقل لكل VLAN. لو عندك AP خارجي يدعم VLAN، سيب Wi-Fi Interface فارغاً واضبط SSID من الجهاز الخارجي.</div>' +
    '<label>Dedicated Wi-Fi Interface<input id="vWifi" placeholder="wlan1" value="' + v('wifi_interface') + '"></label>' +
    '<label>SSID على الكارت الإضافي<input id="vSsid" maxlength="32" value="' + v('ssid') + '"></label>' +
    '<label>Wi-Fi password<input id="vPassword" type="password" autocomplete="new-password" ' +
    'placeholder="' + (p.wifi_password_set ? 'اتركها فارغة للاحتفاظ بالحالية' : '8-63 bytes') + '"></label>' +
    '<label>Channel<select id="vChannel"><option value="1">1</option><option value="6">6</option><option value="11">11</option></select></label>' +
    '<label><input id="vEnabled" type="checkbox" ' + (p.enabled ? 'checked' : '') +
    '> Enabled (يبدأ بعد الضغط على تطبيق)</label>' +
    '<div class="toolbar"><button onclick="vlanSave()">حفظ فقط</button>' +
    '<button class="ghost" onclick="closeModal()">إلغاء</button></div>');
  document.getElementById('vChannel').value = String(p.channel || 6);
}

async function vlanSave() {
  const val = function(id) { return document.getElementById(id).value.trim(); };
  const profile = {
    id: Number(val('vId')), name: val('vName'), parent: val('vParent'),
    gateway: val('vGateway'), dhcp_start: val('vStart'), dhcp_end: val('vEnd'),
    wifi_interface: val('vWifi'), ssid: val('vSsid'),
    wifi_password: document.getElementById('vPassword').value,
    channel: Number(val('vChannel')),
    enabled: document.getElementById('vEnabled').checked
  };
  const profiles = VLAN_ITEMS.filter(function(x) { return x.id !== profile.id; }).map(function(x) {
    return {
      id: x.id, name: x.name, parent: x.parent, gateway: x.gateway,
      dhcp_start: x.dhcp_start, dhcp_end: x.dhcp_end, wifi_interface: x.wifi_interface,
      ssid: x.ssid, channel: x.channel, enabled: x.enabled
    };
  });
  profiles.push(profile);
  try {
    await api('/api/vlans/save', {profiles: profiles});
    closeModal();
    await vlanLoad();
    toast('<h3>تم حفظ إعدادات VLAN</h3><p>لم يتم تطبيقها على الشبكة. اضغط تطبيق بعد تجهيز الهاردوير.</p>');
  } catch(e) { alert('فشل حفظ VLAN: ' + e.message); }
}

async function vlanRemove(id) {
  if (!confirm('إزالة VLAN ' + id + ' من الإعدادات؟ ثم يلزم الضغط على تطبيق لإيقافها.')) return;
  const profiles = VLAN_ITEMS.filter(function(x) { return x.id !== id; });
  try {
    await api('/api/vlans/save', {profiles: profiles});
    await vlanLoad();
  } catch(e) { alert(e.message); }
}

async function vlanApply() {
  if (!confirm('تطبيق إعدادات VLAN سيفصل عملاء VLANs الحالية مؤقتًا. تأكد من وجود واجهة Ethernet إضافية. متابعة؟')) return;
  try {
    const result = await api('/api/vlans/apply', {});
    await vlanLoad();
    toast('<h3>تم تطبيق VLANs</h3><p>Running: ' + esc(result.active_vlan_ids.join(', ') || 'none') +
      '. اختبر DHCP والإنترنت والعزل على الأجهزة الفعلية.</p>');
  } catch(e) {
    await vlanLoad();
    toast('<h3>فشل تطبيق VLAN</h3><p class="bad">' + esc(e.message) +
      '</p><p>راجع الأجهزة الموصولة وLogs. شبكة QuotaGate الأساسية لم يُطلب تغييرها.</p>');
  }
}
