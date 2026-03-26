"""Панели дашборда: обзор, мини-система, трафик, пользователи."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text

from ..constants import C, L
from ..utils import fmt_b, fmt_s, fmt_up, fmt_ts, spark, gauge, pct_col, H
from ..modules.sni_radar import classify as _sni_classify

if TYPE_CHECKING:
    from ..app import XrayMonitor


def render_overview(app: "XrayMonitor", d: dict) -> Text:
    t = Text()
    onl = d.get("online_users", [])
    sy  = d.get("sys", {})
    su  = d["speed_up"]
    sd  = d["speed_down"]
    tot = d["total_up"] + d["total_down"]

    # ── Заголовок ─────────────────────────────────────────────
    t.append(f" {L['overview']}", C["accent"])
    t.append(f"  {len(onl)} {L['online']}", C["online"] if onl else C["dim"])
    if sy.get("uptime"):
        t.append(f"  ·  {fmt_up(sy['uptime'])}", C["dim"])
    t.append("\n")
    t.append("  " + H * 50 + "\n", C["dim"])

    # ── UP / DN / TOT ─────────────────────────────────────────
    t.append(" UP  ", C["up"])
    t.append(f"{fmt_b(d['total_up']):>10}", C["up"])
    t.append(f"   {fmt_s(su):>11}", C["up"])
    t.append("   ")
    t.append(spark(app.xray.up_hist, 26), C["spark_u"])
    
    t.append("\n")
    t.append("\n")

    t.append(" DN  ", C["dn"])
    t.append(f"{fmt_b(d['total_down']):>10}", C["dn"])
    t.append(f"   {fmt_s(sd):>11}", C["dn"])
    t.append("   ")
    t.append(spark(app.xray.dn_hist, 26), C["spark_d"])

    t.append("\n")
    t.append(" " + H * 3 + "\n", C["dim"])
    
    t.append(" TOT ", C["total"])
    t.append(f"{fmt_b(tot):>10}", C["total"])
    t.append(f"   pk↑ {fmt_s(app.xray.peak_up)}", C["dim"])
    t.append(f"   pk↓ {fmt_s(app.xray.peak_dn)}\n", C["dim"])

    # ── Gauges ────────────────────────────────────────────────
    t.append("\n")
    peak = max(app.xray.peak_up, app.xray.peak_dn, 1)
    t.append(" UP  ", C["dim"])
    t.append(gauge(su, peak, 32), C["up"])
    t.append(f"  {fmt_s(su)}\n", C["up"])
    
    t.append(" DN  ", C["dim"])
    t.append(gauge(sd, peak, 32), C["dn"])
    t.append(f"  {fmt_s(sd)}\n", C["dn"])

    # ── Блокировки ────────────────────────────────────────────
    blk_tot  = app.log_tail._block_session
    blk_rate = app.log_tail.block_per_min()
    if blk_tot > 0 or app.log_tail._last_pos > 0:
        t.append("\n BLK ", C["err"])
        t.append(f"{blk_tot:>7}", C["err"])
        if blk_rate >= 0.1:
            t.append(f"   {blk_rate:.0f}/min", C["warn"])
        top = app.log_tail.top_blocked(3)
        if top:
            t.append("\n", "")
            for domain, cnt in top:
                short = (domain[:30] + "…") if len(domain) > 31 else domain
                t.append(f"       {short:<32}", C["dim"])
                t.append(f" {cnt:>5}\n", C["err"])
        else:
            t.append("\n", "")
    return t


def _short_up(s: int) -> str:
    """Компактный аптайм без секунд при > 1 ч: '8h 25m', '2d 3h', '45m'."""
    s = int(s)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:   return f"{d}d {h}h"
    if h:   return f"{h}h {m}m"
    return f"{m}m"


def render_sysmini(app: "XrayMonitor", d: dict) -> Text:
    t = Text()
    sy = d.get("sys", {})
    t.append(f" {L['system']}\n", C["accent2"])
    t.append("  " + H * 30 + "\n", C["dim"])

    if sy:
        up_val  = _short_up(sy.get("uptime", 0))
        gor_val = str(sy.get("goroutines", "?"))
        mem_val = fmt_b(sy.get("alloc", 0))
        sys_val = fmt_b(sy.get("sys", 0))
        gc_val  = f"×{sy.get('gc_runs', '?')}"
        lo      = sy.get("live_objects", 0)
        obj_val = f"{lo:,}" if lo else ""

        # Две колонки: метка+значение | метка+значение
        def row2(l1: str, v1: str, c1: str, l2: str, v2: str, c2: str) -> None:
            t.append(f" {l1:<4}", C["accent2"])
            t.append(f"{v1:<10}", c1)
            t.append(f"  {l2:<4}", C["accent2"])
            t.append(f"{v2}\n", c2)

        def row1(l1: str, v1: str, c1: str) -> None:
            t.append(f" {l1:<4}", C["accent2"])
            t.append(f"{v1}\n", c1)

        row2("UP",  up_val,  C["total"],  "GOR", gor_val, C["accent"])
        row2("MEM", mem_val, C["up"],     "SYS", sys_val, C["dn"])
        row2("GC",  gc_val,  C["dim"],    "OBJ", obj_val, C["dim"])
    else:
        t.append(f"  {L['waiting']}\n", C["dim"])

    sd = app.sys_s.get()
    if sd.get("xray_pid"):
        t.append("\n", "")
        t.append(f" PID  xray   {sd['xray_pid']}\n", C["dim"])
        if sd.get("xray_mem"):
            t.append(f" MEM  xray   {fmt_b(sd['xray_mem'])}\n", C["dim"])
        if sd.get("xray_cpu") is not None:
            cpu_v = sd["xray_cpu"]
            if cpu_v > 0:
                t.append(f" CPU  xray   {cpu_v:.1f}%\n", C["dim"])
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
        DIRECT = {"direct", "freedom", "bypass"}
        BLOCK  = {"block", "blackhole", "banned", "ads", "adblock"}
        PROXY  = {"warp", "cloudflare", "proxy", "relay", "socks", "vmess", "vless", "trojan"}

        def classify(tag: str) -> str:
            tl = tag.lower()
            if any(x in tl for x in BLOCK):  return "block"
            if any(x in tl for x in DIRECT): return "direct"
            if any(x in tl for x in PROXY):  return "proxy"
            return "other"

        # Исключаем block-записи (blackhole): они всегда 0 и только засоряют вывод
        visible = {tag: v for tag, v in outbounds.items() if classify(tag) != "block"}

        if visible:
            t.append(f"\n {L['outbound']} / ROUTING\n", C["accent"])
            sep(); hdr(); sep()
            total_out = sum(v.get("uplink", 0)+v.get("downlink", 0) for v in visible.values())

            for tag, v in sorted(visible.items(),
                                 key=lambda x: x[1].get("downlink", 0)+x[1].get("uplink", 0),
                                 reverse=True):
                up = v.get("uplink", 0); dn = v.get("downlink", 0); tot = up + dn
                name = (tag[:21]+"...") if len(tag) > 22 else tag
                kind = classify(tag)
                nc   = {"direct": C["ok"], "proxy": C["accent"]}.get(kind, C["dim"])
                icon = {"direct": "[->]", "proxy": "[~]"}.get(kind, "[?]")
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
                for tag, v in visible.items():
                    k   = classify(tag)
                    tot = v.get("uplink", 0) + v.get("downlink", 0)
                    groups[k] = groups.get(k, 0) + tot
                for kind, tot in sorted(groups.items(), key=lambda x: x[1], reverse=True):
                    col = {"direct": C["ok"], "proxy": C["accent"]}.get(kind, C["dim"])
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
    else:    t.append(f"  {n_on}/{len(users)} {L['online']}", C["online"] if n_on else C["dim"])
    t.append("\n  " + H * 38 + "\n", C["dim"])

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

    # Подгружаем историю один раз для всего рендера
    tl         = app.traffic_log
    today_hist = tl.get_today()
    week_hist  = tl.get_weekly()   if tl.available_days() >= 2 else {}
    month_hist = tl.get_monthly()  if tl.available_days() >= 2 else {}

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
        t.append("   DN ", C["dn"]); t.append(f"{fmt_b(dn):>9}", C["dn"])
        t.append("\n")

        # ── История трафика ──────────────────────────────────
        td = today_hist.get(email)
        wk = week_hist.get(email)
        mo = month_hist.get(email)
        if td or wk or mo:
            t.append("   ", "")
            if td:
                t.append("сег ", C["dim"])
                t.append(fmt_b(td.get("up", 0) + td.get("dn", 0)), C["accent3"])
            if wk:
                t.append("  7д ", C["dim"])
                t.append(fmt_b(wk.get("up", 0) + wk.get("dn", 0)), C["accent"])
            if mo:
                t.append("  30д ", C["dim"])
                t.append(fmt_b(mo.get("up", 0) + mo.get("dn", 0)), C["total"])
            t.append("\n")

        if ips and is_on:
            ip_list = sorted(ips.items(), key=lambda x: x[1], reverse=True)
            for i, (ip, ts) in enumerate(ip_list):
                pfx = "|" if i < len(ip_list) - 1 else "∟"
                t.append(f"   {pfx} ", C["dim"])
                t.append(f"{ip:<18}", C["dim"])

                # ── Per-IP байты ↓ ────────────────────────────
                ip_b = app.xray.ip_bytes.get(ip)
                if ip_b and ip_b[1] > 1024:
                    t.append(f"  {fmt_b(int(ip_b[1])):>9} ↓", C["dn"])
                else:
                    t.append(f"  {'':>11}", "")

                # ── SNI Radar: до 4 тегов сервисов ───────────
                sni_buf = app.log_tail.ip_sni.get(ip)
                if sni_buf:
                    seen_tags: set = set()
                    for domain, _ts in reversed(list(sni_buf)):
                        cls = _sni_classify(domain)
                        if cls and cls[0] not in seen_tags:
                            _, label, col = cls
                            t.append(f" [{label}]", C[col])
                            seen_tags.add(cls[0])
                            if len(seen_tags) >= 4:
                                break

                t.append(f"  {fmt_ts(ts)}\n", C["dim"])

        if idx < len(su_list) - 1:
            t.append("  " + H * 30 + "\n", C["dim"])

    if not users and not online_set:
        t.append(f"\n  {L['no_users']}\n", C["dim"])
        t.append(f"  {L['enable_hint']}\n", C["dim"])
    return t
