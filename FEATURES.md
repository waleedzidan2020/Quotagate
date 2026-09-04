# QuotaGate 3.1 feature matrix

QuotaGate 3.1 keeps the production antiX architecture: `eth0` WAN, `wlan0` AP/LAN, SysV init, nftables/tc in the kernel path, SQLite persistence, and a lightweight Python + vanilla JS control plane.

| Capability | Status |
|---|---|
| User quota shared across multiple devices | Implemented |
| Fixed GB quota | Implemented |
| Auto/equal-share of remaining bundle | Implemented |
| Disabled quota mode | Implemented |
| Top-up | Implemented |
| Unlimited / exempt user | Implemented |
| Device quota bypass | Implemented |
| Hard Internet cut | Implemented with nftables |
| Manual user/device control | Implemented |
| Internet-only client accounting | Implemented with nftables counters excluding configured local/uplink subnets |
| Gateway-host Internet accounting | Implemented with dedicated nftables input/output counters |
| Restart-safe SQLite usage | Implemented; kernel counter baselines are re-established after rebuild/restart |
| Monthly renewal day | Implemented |
| End-of-month renewal | Implemented |
| Disable automatic renewal | Implemented |
| Per-user/device speed limit | Implemented with tc HTB/fq_codel |
| Master traffic shaping switch | Implemented |
| Local LAN excluded from quota counters | Implemented |
| Guest mode | Implemented |
| New devices disabled by default | Implemented |
| STOP NEW CONNECTIONS | Implemented with DHCP unknown-client refusal plus nftables onboarding deny |
| Random/private MAC decline | Implemented |
| MAC Allow / Deny lists | Implemented |
| Deleted device stays denied | Implemented |
| Device discovery | dnsmasq leases + ip neigh + ARP |
| Hostname discovery | Implemented when dnsmasq lease supplies a hostname |
| Vendor detection | Local OUI cache when available + small built-in fallback; no per-device Internet lookup |
| Rogue/static-IP client detection | Partial: ARP/neighbour discovery registers and blocks unapproved devices; dedicated rogue reporting is limited |
| DNS filtering global/user/device | Implemented through local DNS proxy |
| Wildcard/subdomain DNS rules | Implemented |
| DNS redirect to IPv4 | Implemented for A queries |
| DNS history + top domains | Implemented |
| 1h/24h/7d/14d history | Implemented |
| User name in DNS history | Implemented |
| Global/per-user DNS retention | Implemented |
| Bounded DNS history rows | Implemented |
| Per-device DNS upstream | Implemented |
| Per-user DNS upstream | Implemented |
| Cloudflare Family DNS mode | Implemented |
| Lightweight DNS presets | Ads/tracking, social, streaming, gambling, adult |
| Quick Block/Allow from history | Implemented |
| Mandatory SafeSearch rewriting | Not claimed; Family DNS + local rules are provided without pretending to be perfect filtering |
| Custom firewall rules | Add/Edit/Delete/Enable/Disable implemented |
| Port forwarding | Add/Edit/Delete/Enable/Disable implemented |
| DMZ | Implemented |
| VPN share interface | Implemented at existing NAT/forwarding level |
| VPN interface auto-detection | Implemented for tun/tap/wg interfaces |
| VPN reconnect detection | Partial: periodic policy sync notices interface changes and rebuilds QuotaGate rules |
| Per-user/device VPN exclusion | Not enabled by default; policy-routing implementation is intentionally deferred until hardware/VPN-specific validation |
| Userspace v2ray/tun2socks helper | Optional scaffold only; no arbitrary binary downloads are performed |
| Strong PPPoE/WAN mode | Safety scaffold + local test helper; automatic PPPoE takeover remains intentionally disabled |
| Admin login | Implemented |
| Login abuse temporary bans | Implemented |
| Authenticated API rate limiting | Implemented |
| 2FA/TOTP | Implemented with QR setup; secret is not returned in normal API responses |
| HTTPS self-signed | Enable/disable supported; service restart required |
| Privacy Eye | Implemented in browser UI |
| No-store sensitive APIs/pages | Implemented |
| System logs/info | Implemented |
| Internal authenticated report API | Implemented |
| Wi-Fi SSID/password/hidden state | Implemented |
| Wi-Fi channel selection | Implemented for 2.4 GHz b43 profile |
| Wi-Fi password secrecy in GET APIs | Implemented |
| Hostapd-only restart for Wi-Fi setting changes | Implemented |
| Wi-Fi station diagnostics | Signal, TX/RX bitrate, retries, failures, expected throughput |
| Gateway/Internet ping diagnostics | Implemented on demand |
| WAN/LAN/VPN interface diagnostics | Implemented |
| Mobile dashboard | Implemented |
| Auto start on antiX SysVinit | Implemented |
| Boot-time LAN self-healing | Implemented for b43/interface/LAN IP/IP-forwarding/runtime directory |
| Persistent config outside /opt | `/etc/quotagate/config.json` |
| Persistent database outside /opt | `/var/lib/quotagate/quotagate.db` |
| Persistent logs outside /opt | `/var/log/quotagate/` |
| Atomic config writes | temp + fsync + replace, mode 0600 |
| Forward SQLite migrations | Implemented and idempotent |
| Safe update-only deployment | Implemented with staging, backup, health check and rollback |
| Dashboard update check/install | Implemented using the SysV-compatible updater |
| Automatic daily update check | Optional; auto-install is not enabled by default |
| Static CI | Python compile, JS syntax, shell syntax, secret smoke test |
| Docker/systemd dependency | Not required and not introduced |
| APT repository / DEB-first deployment | Deliberately not made the primary path until real antiX hardware validation is complete |

## Hardware verification boundary

Static CI can verify syntax and repository hygiene, but it cannot prove b43/hostapd radio behavior, real nftables/tc performance, DHCP transitions, VPN routing, or PPPoE behavior on the target Dell/antiX machine. Those items must be validated on the actual gateway before being marked hardware-verified.
