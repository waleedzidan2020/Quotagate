# QuotaGate 3.0 feature matrix

| Capability | Status |
|---|---|
| User quota shared across multiple devices | Implemented |
| Fixed GB quota | Implemented |
| Equal-share of remaining bundle | Implemented |
| Top-up | Implemented |
| Hard internet cut at quota | Implemented (nftables) |
| Per-user/device exempt | Implemented |
| Per-user/device speed limit | Implemented (tc HTB/fq_codel) |
| Mobile dashboard | Implemented |
| Guest mode | Implemented |
| STOP NEW CONNECTIONS | Implemented as onboarding deny |
| Random MAC decline | Implemented |
| MAC whitelist / blacklist | Implemented |
| Bundle reset | Implemented |
| Device discovery | Implemented |
| DNS filtering global/user/device | Implemented through local DNS proxy |
| DNS history + top domains | Implemented |
| Custom firewall rules | Implemented |
| Port forwarding | Implemented |
| DMZ | Implemented |
| VPN share interface | Implemented (route/NAT selection) |
| 2FA | Implemented |
| Login abuse temporary bans | Implemented |
| HTTPS self-signed | Implemented |
| System logs/info | Implemented |
| Auto start on antiX SysVinit | Implemented |
| Dashboard fixed at 192.168.2.1:8080 | Implemented |
| Runs privileged network controller as root | Implemented |
| Persistent config outside /opt | Implemented (/etc/quotagate/config.json) |
| Persistent database outside /opt | Implemented (/var/lib/quotagate/quotagate.db) |
| Persistent log directory outside /opt | Implemented (/var/log/quotagate/) |
| Safe /opt replacement during updates | Implemented |
| Legacy config/database migration | Implemented |
| Strong PPPoE/WAN mode | Safety scaffold + local test helper; auto-apply intentionally disabled |
| Remote self-updater | Not included |
