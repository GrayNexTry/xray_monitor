"""Панели дашборда: обзор, мини-система, трафик, пользователи."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text

from ..constants import C, L
from ..utils import fmt_b, fmt_s, fmt_up, fmt_ts, spark, gauge, pct_col, H

if TYPE_CHECKING:
    from ..App import XrayMonitor


def render_overview(app: "XrayMonitor", d: dict) -> Text:
    t = Text()
    onl = d.get("online_users", [])
    sy  = d.get("sys", {})
    su  = d["speed_up"]
    sd  = d["speed_down"]

    t.append(f" {L['overview']}", C["accent"])
    t.append(f"  {len(onl)} {L['online']}", C["dim"])
    if sy.get("uptime"): t.append(f"  {fmt_up(sy['uptime'])}", C["dim"])
    t.append("\n\n")

    t.append(" UP  ", C["up"]);  t.append(f"{fmt_b(d['total_up']):>9}", C["up"])
    t.append(f"  {fmt_s(su):>10}", C["up"]); t.append("  ")
    t.append(spark(app.xray.up_hist, 24), C["spark_u"]); t.append("\n")
    t.append(" DN  ", C["dn"]); t.append(f"{fmt_b(d['total_down']):>9}", C["dn"])
    t.append(f"  {fmt_s(sd):>10}", C["dn"]); t.append("  ")
    t.append(spark(app.xray.dn_hist, 24), C["spark_d"]); t.append("\n")
    tot = d["total_up"] + d["total_down"]
    t.append(f" TOT ", C["total"]); t.append(f"{fmt_b(tot):>9}", C["total"])
    t.append(f"  pk-up {fmt_s(app.xray.peak_up):>9}", C["dim"])
    t.append(f"  pk-dn {fmt_s(app.xray.peak_dn):>9}", C["dim"]); t.append("\n")
    t.append(f"  {L['session_up']:>10}: {fmt_b(app.xray.sess_up)}", C["dim"])
    t.append(f"   {L['session_dn']:>10}: {fmt_b(app.xray.sess_dn)}\n", C["dim"])
    t.append("\n")

    peak = max(app.xray.peak_up, app.xray.peak_dn, 1)
    t.append(" UP  [", C["dim"]); t.append(gauge(su, peak, 28), C["up"])
    t.append(f"]  {fmt_s(su)}\n", C["up"])
    t.append(" DN  [", C["dim"]); t.append(gauge(sd, peak, 28), C["dn"])
    t.append(f"]  {fmt_s(sd)}\n", C["dn"])

    blk_tot  = app.log_tail._block_session
    blk_rate = app.log_tail.block_per_min()
    if blk_tot > 0 or app.log_tail._last_pos > 0:
        t.append("\n BLK ", C["err"])
        t.append(f"{blk_tot:>7} blk", C["err"])
        if blk_rate >= 0.1:
            t.append(f"  {blk_rate:5.1f}/min", C["warn"])
        top = app.log_tail.top_blocked(3)
        if top:
            t.append("\n", "")
            for domain, cnt in top:
                short = (domain[:28]+"...") if len(domain) > 29 else domain
                t.append(f"      {short:<30}", C["dim"])
                t.append(f" {cnt:>5}\n", C["err"])
        else:
            t.append("\n", "")
    return t


def render_sysmini(app: "XrayMonitor", d: dict) -> Text:
    t = Text()
    sy = d.get("sys", {})
    t.append(f" {L['system']}\n\n", C["accent2"])
    if sy:
        rows = [
            ("UP ",  L["uptime"],     fmt_up(sy.get("uptime", 0)),    C["total"]),
            ("THR",  L["goroutines"], str(sy.get("goroutines", "?")), C["accent"]),
            ("MEM",  L["alloc"],      fmt_b(sy.get("alloc", 0)),       C["up"]),
            ("SYS",  L["mem"],        fmt_b(sy.get("sys", 0)),         C["dn"]),
            ("GC ",  L["gc"],         f"x{sy.get('gc_runs', '?')}",    C["dim"]),
        ]
        lo = sy.get("live_objects", 0)
        if lo: rows.append(("OBJ", L["objects"], f"{lo:,}", C["dim"]))
        for pref, label, val, col in rows:
            t.append(f" {pref} ", C["accent2"])
            t.append(f"{label:<10}", C["dim"])
            t.append(f"  {val}\n", col)
    else:
        t.append(f"  {L['waiting']}", C["dim"])

    sd = app.sys_s.get()
    if sd.get("xray_pid"):
        t.append(f"\n PID  xray  {sd['xray_pid']}\n", C["dim"])
        if sd.get("xray_mem"): t.append(f" MEM  xray  {fmt_b(sd['xray_mem'])}\n", C["dim"])
    return t


def render_traffic(app: "XrayMonitor", d: dict) -> Text:
    t = Text()

    def hdr() -> None:
        t.append(f"  {'':23}", "")
        t.append(f"{'UP':>12}", C["up"])
        t.append(f"{'DOWN':>12}", C["dn"])
        t.append(f"{'TOTAL':>12}\n", C["total"])

    def sep() -> None:
        t.append("  " + H * 57 + "\n", C["dim"])

    inbounds = d.get("inbounds", {})
    if inbounds:
        t.append(f" {L['inbound']}\n", C["accent"])
        sep(); hdr(); sep()
        for tag, v in sorted(inbounds.items(),
                             key=lambda x: x[1].get(app.sort_by, 0), reverse=True):
            up   = v.get("uplink", 0); dn = v.get("downlink", 0)
            name = (tag[:21]+"...") if len(tag) > 22 else tag
            t.append(f"  {name:<23}", "bold")
            t.append(f"{fmt_b(up):>12}", C["up"])
            t.append(f"{fmt_b(dn):>12}", C["dn"])
            t.append(f"{fmt_b(up+dn):>12}\n", C["total"])

    outbounds = d.get("outbounds", {})
    if outbounds:
        t.append(f"\n {L['outbound']} / ROUTING\n", C["accent"])
        sep(); hdr(); sep()
        total_out = sum(v.get("uplink", 0)+v.get("downlink", 0) for v in outbounds.values())
        DIRECT = {"direct", "freedom", "bypass"}
        BLOCK  = {"block", "blackhole", "banned", "ads", "adblock"}
        PROXY  = {"warp", "cloudflare", "proxy", "relay", "socks", "vmess", "vless", "trojan"}

        def classify(tag: str) -> str:
            tl = tag.lower()
            if any(x in tl for x in BLOCK):  return "block"
            if any(x in tl for x in DIRECT): return "direct"
            if any(x in tl for x in PROXY):  return "proxy"
            return "other"

        for tag, v in sorted(outbounds.items(),
                             key=lambda x: x[1].get("downlink", 0)+x[1].get("uplink", 0),
                             reverse=True):
            up = v.get("uplink", 0); dn = v.get("downlink", 0); tot = up + dn
            name = (tag[:21]+"...") if len(tag) > 22 else tag
            kind = classify(tag)
            nc   = {"block": C["err"], "direct": C["ok"], "proxy": C["accent"]}.get(kind, C["dim"])
            icon = {"block": "[X]", "direct": "[->]", "proxy": "[~]"}.get(kind, "[?]")
            t.append(f"  {icon} ", nc)
            t.append(f"{name:<20}", "bold")
            t.append(f"{fmt_b(up):>12}", C["up"])
            t.append(f"{fmt_b(dn):>12}", C["dn"])
            t.append(f"{fmt_b(tot):>12}", C["total"])
            if total_out > 0:
                t.append(f"  {gauge(tot, total_out, 8)} {tot/total_out*100:4.1f}%\n", nc)
            else:
                t.append("\n", "")

        if total_out > 0:
            t.append(f"\n  {L['summary']}: ", C["dim"])
            groups: dict = {}
            for tag, v in outbounds.items():
                k   = classify(tag)
                tot = v.get("uplink", 0) + v.get("downlink", 0)
                groups[k] = groups.get(k, 0) + tot
            for kind, tot in sorted(groups.items(), key=lambda x: x[1], reverse=True):
                col = {"block": C["err"], "direct": C["ok"], "proxy": C["accent"]}.get(kind, C["dim"])
                t.append(f"  {kind} {tot/total_out*100:.0f}%", col)
            t.append("\n", "")

    if not inbounds and not outbounds:
        t.append(f"\n  {L['no_traffic']}\n", C["dim"])
    return t


def render_users(app: "XrayMonitor", d: dict) -> Text:
    t = Text()
    users      = d.get("users", {})
    online_set = set(d.get("online_users", []))
    user_ips   = d.get("user_ips", {})
    filt       = app.filter_txt
    n_on       = sum(1 for e in users if e in online_set)

    t.append(f" {L['users']}", C["accent"])
    if filt: t.append(f"  /{filt}", C["accent2"])
    else:    t.append(f"  {n_on}/{len(users)} {L['online']}", C["dim"])
    t.append("\n  " + H*36 + "\n", C["dim"])

    filtered = {e: v for e, v in users.items() if not filt or filt in e.lower()}
    sfn = {
        "downlink": lambda x: x[1].get("downlink", 0),
        "uplink":   lambda x: x[1].get("uplink", 0),
        "total":    lambda x: x[1].get("uplink", 0) + x[1].get("downlink", 0),
    }.get(app.sort_by, lambda x: x[1].get("downlink", 0))
    su_list = sorted(filtered.items(), key=sfn, reverse=True)

    if not su_list and filt:
        t.append(f"\n  {L['no_matches_for']} '{filt}'\n", C["dim"])
        return t

    for idx, (email, v) in enumerate(su_list):
        up    = v.get("uplink", 0); dn = v.get("downlink", 0)
        is_on = email in online_set
        sp    = app.xray.u_speed.get(email, {}); su = sp.get("su", 0); sd = sp.get("sd", 0)
        ips   = user_ips.get(email, {}); hist = app.xray.u_hist.get(email)
        dc    = C["online"] if is_on else C["offline"]
        dot   = "*" if is_on else "o"
        name  = (email[:24]+"...") if len(email) > 24 else email

        t.append(f"  {dot} ", dc)
        t.append(name, "bold" if is_on else C["dim"])
        if is_on and ips: t.append(f"  {len(ips)} {L['conn']}", C["dim"])
        t.append("\n")

        t.append("   UP ", C["up"]); t.append(f"{fmt_b(up):>9}", C["up"])
        if su > 10: t.append(f" {fmt_s(su):>9}", C["up"])
        else: t.append(f"{'':>10}", "")
        t.append("  DN ", C["dn"]); t.append(f"{fmt_b(dn):>9}", C["dn"])
        if sd > 10: t.append(f" {fmt_s(sd):>9}", C["dn"])
        t.append("\n")

        if hist and hist.n >= 3 and is_on:
            t.append("    ", ""); t.append(spark(hist.up, 16), C["spark_u"])
            t.append("  ",  ""); t.append(spark(hist.dn, 16), C["spark_d"])
            t.append(f"  pk{fmt_s(hist.p_up)}", C["dim"]); t.append("\n")

        if ips and is_on:
            ip_list = sorted(ips.items(), key=lambda x: x[1], reverse=True)
            for i, (ip, ts) in enumerate(ip_list):
                pfx = "|" if i < len(ip_list)-1 else "L"
                t.append(f"   {pfx} ", C["dim"])
                t.append(f"{ip:<18}", C["dim"])
                if app.geo_on:
                    geo_str, asn_str, is_hosting = app.geo.fmt_full(ip)
                    t.append(f"{geo_str:<22}", C["accent2"])
                    if asn_str:
                        asn_col = C["warn"] if is_hosting else C["dim"]
                        warn    = " [!datacenter]" if is_hosting else ""
                        t.append(f"{asn_str:<28}{warn}", asn_col)
                t.append(f" {fmt_ts(ts)}\n", C["dim"])

        if idx < len(su_list)-1:
            t.append("  " + "."*30 + "\n", C["dim"])

    if not users and not online_set:
        t.append(f"\n  {L['no_users']}\n", C["dim"])
        t.append(f"  {L['enable_hint']}\n", C["dim"])
    return t
