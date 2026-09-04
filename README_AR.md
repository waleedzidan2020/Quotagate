# QuotaGate antiX 3.0

QuotaGate هو Gateway/Quota Manager مخصص لـ antiX وتوبولوجي `eth0 -> wlan0`.

## توزيع الملفات

تم فصل كود التطبيق نهائياً عن البيانات الدائمة:

- كود التطبيق: `/opt/quotagate`
- الإعدادات الدائمة: `/etc/quotagate/config.json`
- قاعدة البيانات الدائمة: `/var/lib/quotagate/quotagate.db`
- السجلات: `/var/log/quotagate/`
- ملفات runtime المؤقتة: `/run/quotagate/`

يمكن تحديث أو استبدال `/opt/quotagate` بالكامل بدون حذف الإعدادات أو قاعدة البيانات أو السجلات.

## الأمان

لا يتم تخزين كلمات المرور أو Password Hashes أو TOTP secrets أو Wi-Fi passwords أو PPPoE credentials أو API keys أو قواعد بيانات runtime داخل Git repository.

الملف `config.example.json` يحتوي فقط على قيم آمنة وحقول secrets فارغة. يقوم المثبت في أول تثبيت بنسخه إلى `/etc/quotagate/config.json` ثم يطلب كلمات المرور محلياً ويخزنها بصلاحية `600` خارج `/opt`.

ملف hostapd الذي يحتوي Wi-Fi passphrase يتم توليده مؤقتاً في `/run/quotagate/hostapd.conf` بصلاحية `600` ولا يدخل Git ولا يبقى جزءاً من كود التطبيق.

## التثبيت والتحديث

```bash
chmod +x install.sh scripts/*.sh init/quotagate
sudo ./install.sh
```

المثبت:

1. ينشئ `/etc/quotagate`, `/var/lib/quotagate`, `/var/log/quotagate` بالصلاحيات المطلوبة.
2. يوقف الخدمة قبل Migration أو تحديث التطبيق.
3. يبحث عن `config.json` أو `quotagate.db` القديمة الموجودة داخل `/opt/quotagate` وينقلها إلى المسارات الدائمة الجديدة.
4. إذا كانت البيانات الجديدة موجودة بالفعل، لا يقوم بالكتابة فوقها ويضع النسخة القديمة في مجلد backup دائم.
5. يبني نسخة جديدة من التطبيق ثم يستبدل `/opt/quotagate` بالكامل.
6. يحافظ على `/etc/quotagate`, `/var/lib/quotagate`, `/var/log/quotagate` كما هي أثناء التحديث.
7. يعيد تشغيل الخدمة.

## الخدمة والصلاحيات

QuotaGate يستمر في العمل كـ root لأن إدارة الشبكة تتطلب صلاحيات لـ:

- `nftables`
- `tc`
- `hostapd`
- `dnsmasq`
- إعداد Interfaces وIPv4 forwarding

## Dashboard

الافتراضي:

```text
http://192.168.2.1:8080
```

## أوامر الإدارة

```bash
sudo service quotagate status
sudo service quotagate restart
sudo quotagate-diagnose
sudo quotagate-setup-network
```

## إزالة التطبيق

```bash
sudo ./uninstall.sh
```

الإزالة تحذف `/opt/quotagate` والخدمة، لكنها تترك البيانات الدائمة في `/etc/quotagate` و`/var/lib/quotagate` عمداً.

## تنبيه

إعدادات Firewall/NAT/DHCP تغيّر مسار الشبكة. احتفظ بوصول محلي للجهاز أثناء أول تثبيت أو تحديث كبير.
