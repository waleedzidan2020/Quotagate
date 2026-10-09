from __future__ import annotations
"""Optional, isolated VLAN trunks and dedicated Wi-Fi radios for QuotaGate.

Never modifies the existing WAN, primary WLAN or primary DHCP process. A tagged
uplink requires a SECOND wired interface connected to a VLAN-aware switch/AP.
"""
import ipaddress
import json
import re
import subprocess
import threading
from pathlib import Path

ROOT = Path("/run/quotagate/vlans")
STATE = ROOT / "state.json"
LOCK = threading.RLock()
IFACE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,14}$")


def _run(args, *, input_text=None, check=True):
    p = subprocess.run(args, input=input_text, capture_output=True, text=True, timeout=12)
    if check and p.returncode:
        raise RuntimeError((p.stderr or p.stdout or "command failed").strip()[:350])
    return p


def _interfaces():
    return {p.name for p in Path("/sys/class/net").iterdir()}


def _ip4(value, field):
    try:
        addr = ipaddress.ip_address(value)
    except ValueError:
        raise ValueError("invalid " + field)
    if not isinstance(addr, ipaddress.IPv4Address):
        raise ValueError(field + " must be IPv4")
    return addr


def validate(c, profiles):
    """Validate *all* profiles before writing config or touching networking."""
    if not isinstance(profiles, list) or len(profiles) > 12:
        raise ValueError("profiles must be a list of at most 12 VLANs")
    n = c["network"]
    primary = {n["wan_interface"], n["lan_interface"], n.get("vpn_interface", "")}
    existing_nets = [ipaddress.ip_network(n["client_net"]), ipaddress.ip_network(n["uplink_net"])]
    ids, wifi_ifaces, networks = set(), set(), []
    out = []
    for raw in profiles:
        if not isinstance(raw, dict):
            raise ValueError("invalid VLAN profile")
        try:
            vid = int(raw["id"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("VLAN ID must be an integer")
        if not 2 <= vid <= 4094 or vid in ids:
            raise ValueError("VLAN ID must be unique and between 2 and 4094")
        ids.add(vid)
        parent = str(raw.get("parent", "")).strip()
        if not IFACE.fullmatch(parent) or parent in primary or parent.startswith("qgbr"):
            raise ValueError("use a dedicated VLAN trunk interface, NOT WAN/primary Wi-Fi/VPN")
        # Linux interface names max out at 15 chars; USB NIC names (enx...)
        # can already be 15 characters, so use our own short VLAN name.
        vlan_iface = "qgv" + str(vid)
        try:
            ip = ipaddress.ip_interface(str(raw.get("gateway", "")))
        except ValueError:
            raise ValueError("invalid VLAN gateway (example: 192.168.20.1/24)")
        if ip.version != 4 or ip.network.prefixlen != 24 or ip.ip.is_multicast or ip.ip.is_unspecified:
            raise ValueError("VLAN gateway must be a usable IPv4 /24 address")
        if ip.ip == ip.network.network_address or ip.ip == ip.network.broadcast_address:
            raise ValueError("VLAN gateway must be a host address")
        if any(ip.network.overlaps(net) for net in existing_nets + networks):
            raise ValueError("VLAN subnets must not overlap WAN, primary LAN or other VLANs")
        networks.append(ip.network)
        start = _ip4(str(raw.get("dhcp_start", "")), "DHCP start")
        end = _ip4(str(raw.get("dhcp_end", "")), "DHCP end")
        if start not in ip.network or end not in ip.network or start > end:
            raise ValueError("DHCP range must be inside VLAN subnet")
        if not ip.network.network_address < start <= end < ip.network.broadcast_address or start <= ip.ip <= end:
            raise ValueError("DHCP range cannot include gateway/network/broadcast")
        wifi = str(raw.get("wifi_interface", "")).strip()
        ssid = str(raw.get("ssid", "")).strip()
        password = str(raw.get("wifi_password", ""))
        if wifi:
            if (not IFACE.fullmatch(wifi) or wifi in primary or wifi == parent
                    or wifi in wifi_ifaces or wifi.startswith("qgbr")):
                raise ValueError("Wi-Fi needs its own unused, dedicated wireless interface")
            if not ssid or len(ssid.encode("utf-8")) > 32:
                raise ValueError("Wi-Fi SSID must be 1-32 bytes")
            if not 8 <= len(password.encode("utf-8")) <= 63:
                raise ValueError("Wi-Fi password must be 8-63 bytes")
            wifi_ifaces.add(wifi)
        elif ssid or password:
            raise ValueError("select a dedicated Wi-Fi interface for the SSID")
        channel = int(raw.get("channel", 6))
        if wifi and channel not in (1, 6, 11):
            raise ValueError("2.4 GHz Wi-Fi channel must be 1, 6 or 11")
        out.append({
            "id": vid, "name": str(raw.get("name", "VLAN " + str(vid)))[:60],
            "parent": parent, "gateway": str(ip), "dhcp_start": str(start),
            "dhcp_end": str(end), "wifi_interface": wifi, "ssid": ssid,
            "wifi_password": password, "channel": channel,
            "enabled": raw.get("enabled") is True,
        })
    if any(p["parent"] in wifi_ifaces for p in out):
        raise ValueError("trunk and Wi-Fi interface must be different")
    return out


def public_profiles(c):
    return [{k: v for k, v in p.items() if k != "wifi_password"} |
            {"wifi_password_set": bool(p.get("wifi_password"))}
            for p in c.get("vlans", {}).get("profiles", [])]


def _radio_map():
    mapping, phy = {}, ""
    p = _run(["iw", "dev"], check=False)
    for line in p.stdout.splitlines():
        line = line.strip()
        if line.startswith("phy#"):
            phy = line
        elif line.startswith("Interface "):
            mapping[line.split(" ", 1)[1].strip()] = phy
    return mapping


def preflight(c, profiles, owned=()):
    available = _interfaces()
    radios = _radio_map()
    primary_radio = radios.get(c["network"]["lan_interface"])
    vlan_radios = set()
    for p in profiles:
        if not p["enabled"]:
            continue
        parent, wifi = p["parent"], p["wifi_interface"]
        if parent not in available:
            raise RuntimeError("VLAN trunk not found: " + parent)
        iface = "qgv" + str(p["id"])
        bridge = "qgbr" + str(p["id"])
        if (iface in available and iface not in owned) or (wifi and bridge in available and bridge not in owned):
            raise RuntimeError("VLAN interface already exists outside QuotaGate: " + iface)
        if wifi:
            if wifi not in available or wifi not in radios:
                raise RuntimeError("dedicated Wi-Fi AP not found: " + wifi)
            if radios[wifi] == primary_radio or radios[wifi] in vlan_radios:
                raise RuntimeError("Wi-Fi interface shares another AP radio; multiple SSIDs not supported safely")
            vlan_radios.add(radios[wifi])
            phy = "phy" + radios[wifi].split("#")[-1]
            info = _run(["iw", "phy", phy, "info"], check=False)
            if info.returncode or not re.search(r"^\s*\* AP\s*$", info.stdout, re.MULTILINE):
                raise RuntimeError("extra Wi-Fi radio lacks supported AP mode: " + wifi)
            a = _run(["ip", "-4", "-o", "addr", "show", "dev", wifi], check=False)
            if a.stdout.strip():
                raise RuntimeError("Wi-Fi interface has an IP; dedicate it to QuotaGate first")
    return True


def _state():
    try:
        return json.loads(STATE.read_text())
    except (ValueError, OSError):
        return []


def _save_state(state):
    ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE.write_text(json.dumps(state))
    STATE.chmod(0o600)


def _delete_nat():
    _run(["nft", "delete", "table", "ip", "qg_vlan_nat"], check=False)


def _stop_owned():
    for p in reversed(_state()):
        vid = p["id"]
        for svc in ("hostapd", "dnsmasq"):
            pidfile = ROOT / (svc + str(vid) + ".pid")
            try:
                pid = int(pidfile.read_text().strip())
                # Verify the process is the expected service before stopping it.
                cmdline = Path("/proc/" + str(pid) + "/cmdline").read_bytes().replace(b"\x00", b" ").decode(errors="replace")
                if svc in cmdline and str(ROOT) in cmdline:
                    _run(["kill", str(pid)], check=False)
            except (OSError, ValueError):
                pass
            pidfile.unlink(missing_ok=True)
        if p.get("wifi"):
            _run(["ip", "link", "set", "dev", p["wifi"], "nomaster"], check=False)
            _run(["ip", "link", "delete", "dev", "qgbr" + str(vid)], check=False)
        _run(["ip", "link", "delete", "dev", p["iface"]], check=False)
    _delete_nat()
    _save_state([])


def _start_one(p, state):
    vid = p["id"]
    iface = "qgv" + str(vid)
    wifi = p["wifi_interface"]
    l3 = "qgbr" + str(vid) if wifi else iface
    _run(["modprobe", "8021q"])
    _run(["ip", "link", "add", "link", p["parent"], "name", iface, "type", "vlan", "id", str(vid)])
    state.append({"id": vid, "iface": iface, "wifi": wifi, "gateway": p["gateway"]})
    _save_state(state)
    _run(["ip", "link", "set", "dev", iface, "up"])
    if wifi:
        _run(["ip", "link", "add", "name", l3, "type", "bridge"])
        _run(["ip", "link", "set", "dev", iface, "master", l3])
        _run(["ip", "link", "set", "dev", l3, "up"])
    _run(["ip", "addr", "add", p["gateway"], "dev", l3])
    if wifi:
        conf = ROOT / ("hostapd" + str(vid) + ".conf")
        conf.write_text("\n".join([
            "interface=" + wifi, "bridge=" + l3, "driver=nl80211",
            "ssid=" + p["ssid"], "hw_mode=g", "channel=" + str(p["channel"]),
            "auth_algs=1", "wpa=2", "wpa_key_mgmt=WPA-PSK",
            "rsn_pairwise=CCMP", "wpa_passphrase=" + p["wifi_password"],
            "ap_isolate=1", "",
        ]))
        conf.chmod(0o600)
        _run(["hostapd", "-B", "-P", str(ROOT / ("hostapd" + str(vid) + ".pid")), str(conf)])
    gateway = str(ipaddress.ip_interface(p["gateway"]).ip)
    dhcp = ROOT / ("dnsmasq" + str(vid) + ".conf")
    dhcp.write_text("\n".join([
        "port=0", "interface=" + l3, "bind-interfaces",
        "dhcp-range=" + p["dhcp_start"] + "," + p["dhcp_end"] + ",255.255.255.0,12h",
        "dhcp-option=3," + gateway, "dhcp-option=6,1.1.1.1,8.8.8.8",
        "dhcp-leasefile=" + str(ROOT / ("leases" + str(vid))),
        "log-dhcp", "",
    ]))
    dhcp.chmod(0o600)
    _run(["dnsmasq", "--conf-file=" + str(dhcp),
          "--pid-file=" + str(ROOT / ("dnsmasq" + str(vid) + ".pid"))])


def _nat(c, enabled):
    _delete_nat()
    if not enabled:
        return
    wan = c["network"]["wan_interface"]
    content = "table ip qg_vlan_nat {\n chain postrouting { type nat hook postrouting priority srcnat; policy accept;\n"
    for p in enabled:
        net = str(ipaddress.ip_interface(p["gateway"]).network)
        content += ' ip saddr ' + net + ' oifname "' + wan + '" masquerade;\n'
    content += " }\n}\n"
    _run(["nft", "-f", "-"], input_text=content)


def apply(c):
    """Explicitly apply profiles. Never runs when saving/editing profiles."""
    with LOCK:
        profiles = validate(c, c.get("vlans", {}).get("profiles", []))
        enabled = [p for p in profiles if p["enabled"]]
        # Preflight before stopping existing VLAN services; ignore only our own interfaces.
        owned = {x["iface"] for x in _state()}
        owned.update("qgbr" + str(x["id"]) for x in _state() if x.get("wifi"))
        preflight(c, profiles, owned)
        _stop_owned()
        state = []
        try:
            for p in enabled:
                _start_one(p, state)
            _nat(c, enabled)
        except Exception:
            _stop_owned()
            raise
        return {"ok": True, "active_vlan_ids": [p["id"] for p in enabled]}


def stop_runtime():
    """Remove only VLAN interfaces and daemons recorded as owned by QuotaGate."""
    with LOCK:
        _stop_owned()


def forward_rules(run, c):
    """Add before generic policy/established accepts in QuotaGate's forward chain."""
    n = c["network"]
    wan, uplink, primary = n["wan_interface"], n["uplink_net"], n["client_net"]
    # Use RUNNING state, not un-applied drafts; saving a profile must never
    # weaken firewall isolation of an already active VLAN.
    profiles = _state()
    for p in profiles:
        iface = "qgbr" + str(p["id"]) if p.get("wifi") else p["iface"]
        # Deny access to private/internal destinations even when the WAN router
        # can route beyond its directly connected subnet.
        for subnet in (uplink, primary, "10.0.0.0/8", "172.16.0.0/12",
                       "192.168.0.0/16", "100.64.0.0/10", "169.254.0.0/16",
                       "127.0.0.0/8"):
            run(["nft", "add", "rule", "inet", "quotagate", "forward", "iifname", iface,
                 "ip", "daddr", subnet, "drop"])
        for other in profiles:
            if p["id"] != other["id"]:
                target = str(ipaddress.ip_interface(other["gateway"]).network)
                run(["nft", "add", "rule", "inet", "quotagate", "forward", "iifname", iface,
                     "ip", "daddr", target, "drop"])
        # Permit DHCP to this VLAN gateway, but deny access to the server itself.
        run(["nft", "add", "rule", "inet", "quotagate", "input", "iifname", iface,
             "udp", "dport", "67", "accept"])
        run(["nft", "add", "rule", "inet", "quotagate", "input", "iifname", iface,
             "drop"])
        # No IPv6 routing to other networks.
        run(["nft", "add", "rule", "inet", "quotagate", "forward", "iifname", iface,
             "meta", "nfproto", "ipv6", "drop"])
        subnet = str(ipaddress.ip_interface(p["gateway"]).network)
        run(["nft", "add", "rule", "inet", "quotagate", "forward", "iifname", iface,
             "oifname", wan, "ip", "saddr", subnet, "accept"])


def status(c):
    ids = {x["id"] for x in _state()}
    return {"profiles": public_profiles(c),
            "active_vlan_ids": sorted(x["id"] for x in _state() if x["iface"] in _interfaces()),
            "interfaces": sorted(_interfaces())}
