# QuotaGate

QuotaGate هو Gateway / Quota Manager خفيف مخصص لـ antiX Linux لإدارة الأجهزة المتصلة، السرعات، الحصص، Guest Mode، DNS، Firewall، QoS Priority وGaming Mode من Dashboard ويب محلية.

> **التوبولوجي الافتراضي للمشروع:**
>
> `Internet/Router -> eth0 (WAN) -> QuotaGate -> wlan0 (Access Point)`

المثبت الحالي يفترض افتراضيًا أن:

- كارت الإنترنت السلكي هو `eth0`
- كارت الـ Wi-Fi الذي سيعمل Access Point هو `wlan0`
- شبكة العملاء هي `192.168.2.0/24`
- عنوان QuotaGate هو `192.168.2.1`
- Dashboard تعمل على البورت `8080`

إذا كانت أسماء كروت الشبكة مختلفة، يوجد قسم بالأسفل لتعديلها بعد التثبيت.

---

## 1. المتطلبات

يفضل استخدام antiX Linux على جهاز به:

- اتصال Ethernet بالراوتر/الإنترنت
- كارت Wi-Fi يدعم Access Point mode عبر `hostapd` و`nl80211`
- Python 3.10 أو أحدث
- صلاحية `sudo/root`
- اتصال إنترنت أثناء أول تثبيت لتنزيل الحزم المطلوبة

المثبت يقوم تلقائيًا بتثبيت الحزم الأساسية مثل:

```text
python3
python3-venv
dnsmasq
nftables
iproute2
kmod
hostapd
iw
ca-certificates
openssl
curl
git
ppp
python3-qrcode
python3-pil
```

يمكن فحص إصدار Python قبل التثبيت:

```bash
python3 --version
```

وفحص أسماء كروت الشبكة:

```bash
ip link
```

ولفحص كروت Ethernet وWi-Fi:

```bash
lspci -nn | grep -Ei 'network|ethernet'
```

---

## 2. تنزيل QuotaGate من GitHub

ثبت Git إذا لم يكن موجودًا:

```bash
sudo apt update
sudo apt install -y git
```

ثم نزّل المشروع:

```bash
git clone https://github.com/waleedzidan2020/Quotagate.git
```

ادخل إلى مجلد المشروع:

```bash
cd Quotagate
```

يمكن التأكد أنك على فرع `main`:

```bash
git branch --show-current
```

المفروض يظهر:

```text
main
```

---

## 3. تجهيز ملفات التثبيت

اجعل سكربتات المشروع قابلة للتنفيذ:

```bash
chmod +x install.sh scripts/*.sh init/quotagate
```

---

## 4. تثبيت QuotaGate

شغّل المثبت:

```bash
sudo ./install.sh
```

المثبت سيقوم تلقائيًا بـ:

1. تحديث قائمة الحزم.
2. تثبيت المتطلبات المطلوبة.
3. إنشاء مجلدات الإعدادات والبيانات والسجلات.
4. إنشاء Python virtual environment للتطبيق.
5. تثبيت QuotaGate في `/opt/quotagate`.
6. إنشاء خدمة SysV باسم `quotagate`.
7. تفعيل IPv4 forwarding.
8. تجهيز أدوات الإدارة والتحديث.
9. تشغيل الخدمة بعد انتهاء التثبيت.

---

## 5. أسئلة أول تثبيت

في أول تثبيت فقط سيطلب المثبت بعض البيانات.

### اسم شبكة Wi-Fi

مثال:

```text
Wi-Fi SSID [fox3]: MyWiFi
```

لو ضغطت Enter بدون كتابة شيء سيتم استخدام:

```text
fox3
```

### كلمة مرور Wi-Fi

يجب أن تكون من 8 إلى 63 حرفًا:

```text
Wi-Fi password (8-63 chars):
```

### كلمة مرور Dashboard

يجب أن تكون 8 أحرف أو أكثر:

```text
Dashboard admin password (8+ chars):
```

### حجم الباقة الشهرية

مثال:

```text
Monthly bundle GB [140]: 140
```

### يوم تجديد الباقة

مثال:

```text
ISP reset day [1]: 1
```

---

## 6. أماكن ملفات QuotaGate بعد التثبيت

QuotaGate يفصل كود التطبيق عن البيانات الدائمة:

```text
/opt/quotagate
```

كود التطبيق.

```text
/etc/quotagate/config.json
```

الإعدادات الدائمة.

```text
/var/lib/quotagate/quotagate.db
```

قاعدة بيانات SQLite الدائمة.

```text
/var/log/quotagate/
```

السجلات.

```text
/run/quotagate/
```

ملفات runtime المؤقتة.

التحديثات لا يفترض أن تحذف `config.json` أو قاعدة البيانات أو السجلات.

---

## 7. تشغيل Dashboard

بعد نجاح التثبيت، اتصل بشبكة Wi-Fi التي أنشأها QuotaGate ثم افتح:

```text
http://192.168.2.1:8080
```

وسجل الدخول بكلمة مرور Dashboard التي أدخلتها أثناء التثبيت.

---

## 8. التأكد من أن الخدمة تعمل

اعرض حالة QuotaGate:

```bash
sudo service quotagate status
```

إعادة تشغيل الخدمة:

```bash
sudo service quotagate restart
```

إيقاف الخدمة:

```bash
sudo service quotagate stop
```

تشغيلها:

```bash
sudo service quotagate start
```

---

## 9. فحص الجهاز بعد التثبيت

شغّل أداة التشخيص:

```bash
sudo quotagate-diagnose
```

وفحص الـ interfaces:

```bash
ip addr
```

المتوقع في الإعداد الافتراضي:

```text
eth0  -> يحصل على الإنترنت من الراوتر
wlan0 -> 192.168.2.1
```

افحص IPv4 forwarding:

```bash
sysctl net.ipv4.ip_forward
```

المتوقع:

```text
net.ipv4.ip_forward = 1
```

---

## 10. إذا كانت أسماء كروت الشبكة ليست eth0 وwlan0

اعرف الأسماء الصحيحة أولًا:

```bash
ip link
```

مثال قد يظهر عند بعض الأجهزة:

```text
enp2s0
wlp3s0
```

بعد تثبيت QuotaGate شغّل:

```bash
sudo quotagate-setup-network
```

الأداة ستعرض الإعداد الحالي ثم تطلب:

```text
WAN interface [eth0]:
LAN interface [wlan0]:
LAN IP [192.168.2.1]:
```

مثال:

```text
WAN interface [eth0]: enp2s0
LAN interface [wlan0]: wlp3s0
LAN IP [192.168.2.1]: 192.168.2.1
```

بعد الحفظ ستقوم الأداة بإعادة تشغيل QuotaGate تلقائيًا.

---

## 11. اختبار Wi-Fi وAccess Point

تأكد أن كارت Wi-Fi موجود:

```bash
iw dev
```

ولفحص دعم Access Point mode:

```bash
iw list | grep -A 10 "Supported interface modes"
```

يفضل أن ترى:

```text
* AP
```

إذا كان `hostapd` لا يعمل، افحص السجل:

```bash
sudo tail -n 100 /var/log/quotagate/service.log
```

---

## 12. اختبار الوصول إلى الإنترنت من العميل

من جهاز متصل بشبكة QuotaGate:

اختبر الـ Gateway:

```bash
ping 192.168.2.1
```

ثم الإنترنت:

```bash
ping 1.1.1.1
```

ثم DNS:

```bash
ping google.com
```

إذا نجح الأول وفشل الثاني، راجع WAN/NAT.

إذا نجح الثاني وفشل الثالث، راجع DNS.

---

## 13. فحص nftables وQoS

لعرض قواعد NAT/Firewall الحالية:

```bash
sudo nft list ruleset
```

لعرض HTB/QoS على Wi-Fi:

```bash
sudo tc -s class show dev wlan0
```

وعلى واجهة الإنترنت:

```bash
sudo tc -s class show dev eth0
```

إذا كنت تستخدم أسماء Interfaces مختلفة، استبدل `eth0` و`wlan0` بالأسماء الموجودة عندك.

---

## 14. التحديث إلى أحدث نسخة

QuotaGate يحتوي على updater مثبت في:

```text
/usr/local/sbin/quotagate-update
```

لفحص وجود تحديث:

```bash
sudo quotagate-update --check
```

لتثبيت أحدث تحديث:

```bash
sudo quotagate-update
```

ثم يمكن إعادة تشغيل الخدمة:

```bash
sudo service quotagate restart
```

الإعدادات وقاعدة البيانات والسجلات مخزنة خارج `/opt/quotagate` حتى لا يتم حذفها أثناء تحديث كود التطبيق.

---

## 15. تحديث نسخة Git التي نزلتها يدويًا

لو ما زلت محتفظًا بمجلد Git الأصلي:

```bash
cd Quotagate
```

ثم:

```bash
git pull origin main
```

وبعدها يمكنك إعادة تشغيل المثبت لتحديث ملفات التطبيق مع الحفاظ على البيانات الدائمة:

```bash
sudo ./install.sh
```

لكن للاستخدام اليومي، يفضل استخدام:

```bash
sudo quotagate-update
```

---

## 16. عرض السجلات

آخر 100 سطر:

```bash
sudo tail -n 100 /var/log/quotagate/service.log
```

متابعة السجل Live:

```bash
sudo tail -f /var/log/quotagate/service.log
```

---

## 17. قاعدة البيانات

مسار قاعدة البيانات:

```text
/var/lib/quotagate/quotagate.db
```

عرض حجمها:

```bash
sudo du -h /var/lib/quotagate/quotagate.db
```

عرض حجم مجلد البيانات كله:

```bash
sudo du -sh /var/lib/quotagate
```

> لا تحذف قاعدة البيانات يدويًا أثناء تشغيل الخدمة إلا إذا كنت تعرف بالضبط ما تفعله.

---

## 18. Backup

QuotaGate يحتفظ بالبيانات الدائمة خارج كود التطبيق.

لعمل نسخة يدوية سريعة من الإعدادات وقاعدة البيانات:

```bash
sudo service quotagate stop
sudo cp /etc/quotagate/config.json /etc/quotagate/config.json.backup
sudo cp /var/lib/quotagate/quotagate.db /var/lib/quotagate/quotagate.db.backup
sudo service quotagate start
```

كما يحتوي Dashboard على Maintenance/Backup functionality في الإصدارات الحالية.

---

## 19. مشاكل شائعة

### Dashboard لا تفتح

افحص الخدمة:

```bash
sudo service quotagate status
```

ثم:

```bash
sudo quotagate-diagnose
```

وتأكد من وجود IP على LAN interface:

```bash
ip addr show wlan0
```

### Wi-Fi لا يظهر

افحص:

```bash
iw dev
```

ثم السجل:

```bash
sudo tail -n 100 /var/log/quotagate/service.log
```

### العميل يتصل بالـ Wi-Fi لكن لا يوجد إنترنت

افحص WAN:

```bash
ip addr show eth0
ip route
```

ثم:

```bash
ping -c 4 1.1.1.1
```

ثم افحص قواعد nftables:

```bash
sudo nft list ruleset
```

### الجهاز يستخدم أسماء Interfaces مختلفة

شغّل:

```bash
sudo quotagate-setup-network
```

---

## 20. إزالة QuotaGate

من مجلد المشروع الأصلي:

```bash
chmod +x uninstall.sh
sudo ./uninstall.sh
```

الإزالة تحذف التطبيق والخدمة، لكنها تتعمد إبقاء البيانات الدائمة في:

```text
/etc/quotagate
/var/lib/quotagate
```

حتى لا تفقد إعداداتك وقاعدة البيانات بالخطأ.

---

## 21. Quick Install

لمن يعرف إعداد الشبكة مسبقًا:

```bash
sudo apt update
sudo apt install -y git
git clone https://github.com/waleedzidan2020/Quotagate.git
cd Quotagate
chmod +x install.sh scripts/*.sh init/quotagate
sudo ./install.sh
```

بعد انتهاء التثبيت:

```bash
sudo service quotagate status
sudo quotagate-diagnose
```

ثم افتح:

```text
http://192.168.2.1:8080
```

---

## ملاحظة أمنية

QuotaGate يعمل كـ `root` لأن وظائفه تحتاج التحكم في:

- `nftables`
- `tc`
- DHCP
- DNS
- `hostapd`
- إعدادات الشبكة وIP forwarding

لا يتم حفظ كلمات مرور Wi-Fi أو Dashboard داخل Git repository. يتم إنشاء الإعداد المحلي في `/etc/quotagate/config.json` بصلاحيات مقيدة، وملفات runtime الحساسة تظل خارج كود المشروع.

---

## Repository

```text
https://github.com/waleedzidan2020/Quotagate
```
