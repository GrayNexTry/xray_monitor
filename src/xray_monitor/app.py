"""Main TUI application."""

import os
import json
import time
import glob
import shutil
import subprocess
import threading
import ipaddress
import socket
from datetime import datetime
from urllib.request import urlopen

from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, TabbedContent, TabPane, Input
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.binding import Binding
from rich.text import Text

from .constants import C, LANG
from .utils import (
    fmt_b, fmt_s, fmt_up, fmt_ts, spark, gauge, pct_bar, pct_col,
    H, V, copy_to_clipboard, HAS_QR, HAS_PSUTIL,
)
from .geoip import GeoIP
from .config import XrayConfig
from .stats import XrayStats, SysStats, LogTail
from .xray_manager import (
    get_xray_status, get_installed_version, get_latest_version,
    start_xray, stop_xray, restart_xray, enable_xray, disable_xray,
    update_xray_async, find_xray_binary,
)
from .widgets import (
    CSS, OvBox, SysBox, TrafficW, UsersW, KeysLeft, KeysRight,
    SysCpuRam, SysDisk, SysNet, SysProcs, SysPing,
    LogW, ConnW, StatusBar, QRModal, MgmtW,
)


def detect_public_ip() -> str:
    for url in ["https://api.ipify.org",
                "https://ifconfig.me/ip",
                "https://icanhazip.com"]:
        try:
            ip = urlopen(url, timeout=4).read().decode().strip()
            ipaddress.ip_address(ip)
            return ip
        except Exception:
            pass
    try:
        return socket.gethostbyname(socket.gethostname())
    except Exception:
        return ""


class XrayMonitor(App):
    TITLE = "xray-monitor v10"
    CSS   = CSS

    # Bindings with labels (using primary language - English shown, but configurable)
    BINDINGS = [
        Binding("q", "quit",         "Выход / Quit"),
        Binding("r", "reconnect",    "Реконнект / Reconnect"),
        Binding("s", "toggle_sort",  "Сортировка / Sort"),
        Binding("z", "reset_stats",  "Сброс / Reset"),
        Binding("p", "toggle_pause", "Пауза / Pause"),
        Binding("l", "toggle_lang",  "Язык / Language"),
        Binding("Q", "show_qr",      "QR"),
        Binding("R", "restart_xray", "Рестарт / Restart"),
        Binding("e", "edit_config",  "Редактировать / Edit"),
        Binding("C", "check_config", "Проверка / Check"),
        Binding("B", "rollback_config", "Откат / Rollback", show=True),
        # Xray management
        Binding("S", "start_xray",   "Старт / Start",   show=False),
        Binding("X", "stop_xray",    "Стоп / Stop",    show=False),
        Binding("U", "update_xray",  "Обновить / Update", show=False),
        Binding("E", "toggle_enable_xray", "Вкл/Выкл / Toggle", show=False),
        # Tabs and filter
        Binding("1", "tab_dash",  "", show=False),
        Binding("2", "tab_keys",  "", show=False),
        Binding("3", "tab_sys",   "", show=False),
        Binding("4", "tab_log",   "", show=False),
        Binding("5", "tab_conn",  "", show=False),
        Binding("6", "tab_mgmt",  "", show=False),
        Binding("f", "toggle_filter", "", show=False),
        Binding("escape", "clear_filter", "", show=False),
    ]

    sort_by     = reactive("downlink")
    geo_on      = reactive(True)
    lang_key    = reactive("ru")
    paused      = reactive(False)
    filter_txt  = reactive("")
    show_filter = reactive(False)

    def __init__(self, server, interval, log_path, config_path, lang="ru"):
        super().__init__()
        self.xray     = XrayStats(server)
        self.interval = interval
        self.log_tail = LogTail(log_path)
        self.geo      = GeoIP()
        self.cfg      = XrayConfig(config_path)
        self.sys_s    = SysStats()
        self.lang_key = lang
        self._last_d  = None
        self._tick_n  = 0
        self._ping_hosts = ["1.1.1.1", "8.8.8.8", "google.com"]
        self._update_status = ""  # xray update progress
        self._bak_cache: list = []  # cached backup list
        self._bak_cache_t: float = 0  # backup cache timestamp
        self._mgmt_last_update: float = 0  # Last time mgmt tab was updated
        self._mgmt_update_interval: float = 2.0  # Update management tab every 2 seconds

    @property
    def L(self): return LANG.get(self.lang_key, LANG["ru"])

    def _refresh_language(self):
        # global text state
        self.sub_title = self.L["title"]

        # tab labels (border_title works for TabPane)
        tab_map = {
            "tab-dash": "tab_dashboard",
            "tab-keys": "tab_keys",
            "tab-sys": "tab_system",
            "tab-log": "tab_logs",
            "tab-conn": "tab_connections",
            "tab-mgmt": "tab_mgmt",
        }
        for tid, lkey in tab_map.items():
            try:
                pane = self.query_one(f"#{tid}")
                if hasattr(pane, "border_title"):
                    pane.border_title = self.L[lkey]
            except Exception:
                pass

        # placeholders
        try:
            self.query_one("#filter-input", Input).placeholder = self.L.get("filter_placeholder", "")
        except Exception:
            pass
        try:
            self.query_one("#inp-server", Input).placeholder = self.L.get("server_ip_placeholder", "")
        except Exception:
            pass

        # redraw the visible content
        if self._last_d is not None:
            self._draw(self._last_d)
        self._draw_keys_panel()
        self._draw_system_tab()
        self._draw_log()
        self._draw_conn()
        self._draw_mgmt_tab()

    def watch_lang_key(self, new_lang: str):
        """Refresh content when language changes."""
        try:
            self._mgmt_last_update = 0
            self._refresh_language()
        except Exception:
            pass

    # ── COMPOSE ──────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="tabs"):
            with TabPane(self.L["tab_dashboard"], id="tab-dash"):
                with Container(id="dash"):
                    with Horizontal(id="dash-cols"):
                        with Vertical(id="dash-left"):
                            with Horizontal(id="top-row"):
                                yield OvBox("...",  id="ov-box")
                                yield SysBox("...", id="sys-box")
                            yield TrafficW("...")
                        with Vertical(id="dash-right"):
                            with Container(id="filter-bar"):
                                yield Input(placeholder=self.L['filter_placeholder'], id="filter-input")
                            yield UsersW("...")
            with TabPane(self.L["tab_keys"], id="tab-keys"):
                with Horizontal(id="keys-layout"):
                    with Vertical(id="keys-left"):
                        yield KeysLeft("...")
                    with Vertical(id="keys-right"):
                        yield KeysRight("...")
                        with Horizontal(id="keys-srv-row"):
                            yield Input(placeholder=self.L['server_ip_placeholder'], id="inp-server")
            with TabPane(self.L["tab_system"], id="tab-sys"):
                with Container(id="sys-tab"):
                    with Horizontal(id="sys-top"):
                        yield SysCpuRam("...", id="sys-cpuram")
                        yield SysDisk("...",   id="sys-disk")
                        yield SysNet("...",    id="sys-net")
                    with Horizontal(id="sys-bottom"):
                        yield SysProcs("...",  id="sys-procs")
                        yield SysPing("...",   id="sys-ping")
            with TabPane(self.L["tab_logs"], id="tab-log"):
                with Container(id="log-wrap"):
                    yield LogW("...")
            with TabPane(self.L["tab_connections"], id="tab-conn"):
                with Container(id="conn-wrap"):
                    yield ConnW("...")
            with TabPane(self.L["tab_mgmt"], id="tab-mgmt"):
                with Container(id="mgmt-wrap"):
                    yield MgmtW("...")
        yield StatusBar("...", id="status")
        yield Footer()

    # ── MOUNT ────────────────────────────────────────────────

    def on_mount(self):
        self.sub_title = self.L["title"]
        self.xray.connect()
        if HAS_PSUTIL:
            self.set_interval(3.0, lambda: threading.Thread(
                target=self.sys_s.collect, daemon=True).start())
            threading.Thread(target=self.sys_s.collect, daemon=True).start()
        self.set_interval(self.interval, self._tick)
        self.call_later(self._tick)
        for h in self._ping_hosts:
            self.sys_s.ping(h)
        self.call_later(self._init_keys_from_config)

    def on_input_changed(self, event):
        if event.input.id == "filter-input":
            self.filter_txt = event.value.lower()
            if self._last_d: self._draw_users(self._last_d)
        elif event.input.id == "inp-server":
            self._draw_keys_panel()

    def on_input_submitted(self, event):
        if event.input.id == "filter-input":
            event.input.blur()

    # ── TICK ─────────────────────────────────────────────────

    def _tick(self):
        if self.paused: return
        self._tick_n += 1
        threading.Thread(target=self.log_tail.update_block_stats, daemon=True).start()
        try:
            d = self.xray.fetch(geo=self.geo if self.geo_on else None)
            self._last_d = d
            self._draw(d)
        except Exception as e:
            try: self.query_one(StatusBar).update(Text(f" X {e}", C["err"]))
            except Exception: pass

    def _draw(self, d):
        err = "error" in d
        t = Text()
        t.append(" * ", C["err"] if err else C["online"])
        t.append(self.L["disconnected"] if err else self.L["connected"], "bold")
        if err: t.append(f"  -- {d['error']}", C["dim"])
        t.append(f"  {V}  {self.xray.server}", C["dim"])
        t.append(f"  {V}  r{self.interval}s", C["dim"])
        if self.paused:
            age = int(time.time() - self._paused_at) if hasattr(self, '_paused_at') else 0
            t.append(f"  [{self.L['paused']} {age}s]", C["warn"])
        if self.filter_txt: t.append(f"  /{self.filter_txt}", C["accent"])
        t.append(f"  #{self._tick_n}  {datetime.now():%H:%M:%S}", C["dim"])
        self.query_one(StatusBar).update(t)
        if err: return

        self._draw_overview(d)
        self._draw_sysmini(d)
        self._draw_traffic(d)
        self._draw_users(d)
        self._draw_log()
        self._draw_conn()
        self._draw_system_tab()
        
        # Update management tab less frequently (every 2 seconds)
        now = time.time()
        if now - self._mgmt_last_update > self._mgmt_update_interval:
            self._mgmt_last_update = now
            self._draw_mgmt_tab()

    # ── OVERVIEW ─────────────────────────────────────────────

    def _draw_overview(self, d):
        t = Text(); L = self.L
        onl = d.get("online_users", []); tot = d["total_up"] + d["total_down"]
        su = d["speed_up"]; sd = d["speed_down"]
        sy = d.get("sys", {})
        t.append(f" {L['overview']}", C["accent"])
        t.append(f"  {len(onl)} {L['online']}", C["dim"])
        if sy.get("uptime"): t.append(f"  {fmt_up(sy['uptime'])}", C["dim"])
        t.append("\n\n")
        t.append(" UP  ", C["up"]);  t.append(f"{fmt_b(d['total_up']):>9}", C["up"])
        t.append(f"  {fmt_s(su):>10}", C["up"]); t.append("  ")
        t.append(spark(self.xray.up_hist, 24), C["spark_u"]); t.append("\n")
        t.append(" DN  ", C["dn"]); t.append(f"{fmt_b(d['total_down']):>9}", C["dn"])
        t.append(f"  {fmt_s(sd):>10}", C["dn"]); t.append("  ")
        t.append(spark(self.xray.dn_hist, 24), C["spark_d"]); t.append("\n")
        t.append(f" TOT ", C["total"]); t.append(f"{fmt_b(tot):>9}", C["total"])
        t.append(f"  pk-up {fmt_s(self.xray.peak_up):>9}", C["dim"])
        t.append(f"  pk-dn {fmt_s(self.xray.peak_dn):>9}", C["dim"]); t.append("\n")
        t.append(f"  {L['session_up']:>10}: {fmt_b(self.xray.sess_up)}", C["dim"])
        t.append(f"   {L['session_dn']:>10}: {fmt_b(self.xray.sess_dn)}\n", C["dim"])
        t.append("\n")
        peak = max(self.xray.peak_up, self.xray.peak_dn, 1)
        t.append(" UP  [", C["dim"]); t.append(gauge(su, peak, 28), C["up"])
        t.append(f"]  {fmt_s(su)}\n", C["up"])
        t.append(" DN  [", C["dim"]); t.append(gauge(sd, peak, 28), C["dn"])
        t.append(f"]  {fmt_s(sd)}\n", C["dn"])
        blk_tot  = self.log_tail._block_session
        blk_rate = self.log_tail.block_per_min()
        if blk_tot > 0 or self.log_tail._last_pos > 0:
            t.append("\n BLK ", C["err"])
            t.append(f"{blk_tot:>7} blk", C["err"])
            if blk_rate >= 0.1:
                t.append(f"  {blk_rate:5.1f}/min", C["warn"])
            top = self.log_tail.top_blocked(3)
            if top:
                t.append("\n", "")
                for domain, cnt in top:
                    short = (domain[:28]+"...") if len(domain) > 29 else domain
                    t.append(f"      {short:<30}", C["dim"])
                    t.append(f" {cnt:>5}\n", C["err"])
            else:
                t.append("\n", "")
        self.query_one(OvBox).update(t)

    # ── SYS MINI ─────────────────────────────────────────────

    def _draw_sysmini(self, d):
        t = Text(); L = self.L
        sy = d.get("sys", {})
        t.append(f" {L['system']}\n\n", C["accent2"])
        if sy:
            rows = [
                ("UP ",  L["uptime"],     fmt_up(sy.get("uptime", 0)),     C["total"]),
                ("THR",  L["goroutines"], str(sy.get("goroutines", "?")),   C["accent"]),
                ("MEM",  L["alloc"],      fmt_b(sy.get("alloc", 0)),        C["up"]),
                ("SYS",  L["mem"],        fmt_b(sy.get("sys", 0)),          C["dn"]),
                ("GC ",  L["gc"],         f"x{sy.get('gc_runs', '?')}",     C["dim"]),
            ]
            lo = sy.get("live_objects", 0)
            if lo: rows.append(("OBJ", L["objects"], f"{lo:,}", C["dim"]))
            for pref, label, val, col in rows:
                t.append(f" {pref} ", C["accent2"])
                t.append(f"{label:<10}", C["dim"])
                t.append(f"  {val}\n", col)
        else:
            t.append(f"  {L['waiting']}", C["dim"])
        sd = self.sys_s.get()
        if sd.get("xray_pid"):
            t.append(f"\n PID  xray  {sd['xray_pid']}\n", C["dim"])
            if sd.get("xray_mem"): t.append(f" MEM  xray  {fmt_b(sd['xray_mem'])}\n", C["dim"])
        self.query_one(SysBox).update(t)

    # ── TRAFFIC ──────────────────────────────────────────────

    def _draw_traffic(self, d):
        t = Text(); L = self.L

        def hdr():
            t.append(f"  {'':23}", "")
            t.append(f"{'UP':>12}", C["up"])
            t.append(f"{'DOWN':>12}", C["dn"])
            t.append(f"{'TOTAL':>12}\n", C["total"])

        def sep():
            t.append("  " + H * 57 + "\n", C["dim"])

        inbounds = d.get("inbounds", {})
        if inbounds:
            t.append(f" {L['inbound']}\n", C["accent"])
            sep(); hdr(); sep()
            for tag, v in sorted(inbounds.items(),
                                 key=lambda x: x[1].get(self.sort_by, 0), reverse=True):
                up = v.get("uplink", 0); dn = v.get("downlink", 0)
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
            DIRECT_TAGS = {"direct", "freedom", "bypass"}
            BLOCK_TAGS  = {"block", "blackhole", "banned", "ads", "adblock"}
            PROXY_TAGS  = {"warp", "cloudflare", "proxy", "relay", "socks", "vmess", "vless", "trojan"}

            def classify(tag):
                tl = tag.lower()
                if any(x in tl for x in BLOCK_TAGS):  return "block"
                if any(x in tl for x in DIRECT_TAGS): return "direct"
                if any(x in tl for x in PROXY_TAGS):  return "proxy"
                return "other"

            for tag, v in sorted(outbounds.items(),
                                 key=lambda x: x[1].get("downlink", 0)+x[1].get("uplink", 0),
                                 reverse=True):
                up = v.get("uplink", 0); dn = v.get("downlink", 0); tot = up + dn
                name = (tag[:21]+"...") if len(tag) > 22 else tag
                kind = classify(tag)
                nc = {"block": C["err"], "direct": C["ok"], "proxy": C["accent"]}.get(kind, C["dim"])
                icon = {"block": "[X]", "direct": "[->]", "proxy": "[~]"}.get(kind, "[?]")
                t.append(f"  {icon} ", nc)
                t.append(f"{name:<20}", "bold")
                t.append(f"{fmt_b(up):>12}", C["up"])
                t.append(f"{fmt_b(dn):>12}", C["dn"])
                t.append(f"{fmt_b(tot):>12}", C["total"])
                if total_out > 0:
                    pct = tot / total_out * 100
                    bar = gauge(tot, total_out, 8)
                    t.append(f"  {bar} {pct:4.1f}%\n", nc)
                else:
                    t.append("\n", "")

            if total_out > 0:
                t.append(f"\n  {L['summary']}: ", C["dim"])
                groups = {}
                for tag, v in outbounds.items():
                    k = classify(tag)
                    tot = v.get("uplink", 0) + v.get("downlink", 0)
                    groups[k] = groups.get(k, 0) + tot
                for kind, tot in sorted(groups.items(), key=lambda x: x[1], reverse=True):
                    pct = tot / total_out * 100
                    col = {"block": C["err"], "direct": C["ok"], "proxy": C["accent"]}.get(kind, C["dim"])
                    t.append(f"  {kind} {pct:.0f}%", col)
                t.append("\n", "")

        if not inbounds and not outbounds:
            t.append(f"\n  {L['no_traffic']}\n", C["dim"])
        self.query_one(TrafficW).update(t)

    # ── USERS ────────────────────────────────────────────────

    def _draw_users(self, d):
        t = Text(); L = self.L
        users      = d.get("users", {})
        online_set = set(d.get("online_users", []))
        user_ips   = d.get("user_ips", {})
        filt       = self.filter_txt
        n_on       = sum(1 for e in users if e in online_set)

        t.append(f" {L['users']}", C["accent"])
        if filt: t.append(f"  /{filt}", C["accent2"])
        else:    t.append(f"  {n_on}/{len(users)} {L['online']}", C["dim"])
        t.append("\n  " + H*36 + "\n", C["dim"])

        filtered = {e: v for e, v in users.items()
                    if not filt or filt in e.lower()}
        sfn = {"downlink": lambda x: x[1].get("downlink", 0),
               "uplink":   lambda x: x[1].get("uplink", 0),
               "total":    lambda x: x[1].get("uplink", 0)+x[1].get("downlink", 0),
               }.get(self.sort_by, lambda x: x[1].get("downlink", 0))
        su_list = sorted(filtered.items(), key=sfn, reverse=True)

        if not su_list and filt:
            t.append(f"\n  {L['no_matches_for']} '{filt}'\n", C["dim"])
            self.query_one(UsersW).update(t); return

        for idx, (email, v) in enumerate(su_list):
            up  = v.get("uplink", 0); dn = v.get("downlink", 0)
            is_on = email in online_set
            sp  = self.xray.u_speed.get(email, {}); su = sp.get("su", 0); sd = sp.get("sd", 0)
            ips = user_ips.get(email, {}); hist = self.xray.u_hist.get(email)
            dc  = C["online"] if is_on else C["offline"]
            dot = "*" if is_on else "o"
            name = (email[:24]+"...") if len(email) > 24 else email

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
                    if self.geo_on:
                        geo_str, asn_str, is_hosting = self.geo.fmt_full(ip)
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
        self.query_one(UsersW).update(t)

    # ── SYSTEM TAB ───────────────────────────────────────────

    def _draw_system_tab(self):
        L = self.L; sd = self.sys_s.get()
        na = Text(f"\n  {L['psutil_hint']}\n", C["dim"])
        if not sd or not HAS_PSUTIL:
            for w in (SysCpuRam, SysDisk, SysNet, SysProcs, SysPing):
                try: self.query_one(w).update(na)
                except Exception: pass
            return

        # CPU + RAM
        t = Text()
        t.append(f" {L['sys_cpu']} / {L['sys_ram']}\n\n", C["accent"])
        cpu = sd.get("cpu", 0.0)
        t.append(f"  {L['cpu_label']}  ", C["dim"]); t.append(pct_bar(cpu, 22), pct_col(cpu))
        t.append(f"  {cpu:5.1f}%\n", pct_col(cpu))
        for i, c in enumerate(sd.get("cpu_cores", [])[:8]):
            bar = "|"*int(c/100*10) + " "*(10-int(c/100*10))
            t.append(f"  c{i:<2} ", C["dim"]); t.append(bar, pct_col(c))
            t.append(f" {c:5.1f}%\n", C["dim"])
        t.append("\n")
        rp = sd.get("ram_pct", 0.0)
        t.append(f"  {L['ram_label']}  ", C["dim"]); t.append(pct_bar(rp, 22), pct_col(rp))
        t.append(f"  {rp:5.1f}%\n", pct_col(rp))
        t.append(f"  {L['used_label']} {fmt_b(sd.get('ram_used', 0))} / {fmt_b(sd.get('ram_total', 0))}\n", C["dim"])
        t.append(f"  {L['free_label']} {fmt_b(sd.get('ram_free', 0))}\n", C["dim"])
        load = sd.get("load", (0, 0, 0))
        t.append(f"\n  {L['sys_load']}  ", C["dim"])
        t.append(f"{load[0]:.2f}  {load[1]:.2f}  {load[2]:.2f}\n",
                 C["warn"] if load[0] > 2 else C["ok"])
        if sd.get("temp"):
            tc = C["err"] if sd["temp"] > 80 else C["warn"] if sd["temp"] > 65 else C["ok"]
            t.append(f"  {L['sys_temp']}  ", C["dim"]); t.append(f"{sd['temp']:.0f}C\n", tc)
        try: self.query_one(SysCpuRam).update(t)
        except Exception: pass

        # Disk
        t = Text()
        t.append(f" {L['sys_disk']}\n\n", C["accent"])
        dp = sd.get("disk_pct", 0.0)
        t.append("  /    ", C["dim"]); t.append(pct_bar(dp, 22), pct_col(dp))
        t.append(f"  {dp:5.1f}%\n", pct_col(dp))
        t.append(f"  {fmt_b(sd.get('disk_used', 0))} / {fmt_b(sd.get('disk_tot', 0))}\n", C["dim"])
        t.append(f"\n  {L['tcp_connections']}\n", C["accent2"])
        t.append(f"  {L['established_label']}  {sd.get('tcp_est', 0)}\n", C["ok"])
        t.append(f"  {L['listen_label']}       {sd.get('tcp_listen', 0)}\n", C["dim"])
        t.append(f"  {L['processes_label']}    {sd.get('procs', 0)}\n", C["dim"])
        if sd.get("xray_pid"):
            t.append(f"\n  {L['xray_pid_label']}  {sd['xray_pid']}\n", C["accent2"])
            if sd.get("xray_mem"): t.append(f"  {L['xray_ram_label']}  {fmt_b(sd['xray_mem'])}\n", C["accent2"])
            if sd.get("xray_cpu"): t.append(f"  {L['xray_cpu_label']}  {sd['xray_cpu']:.1f}%\n", C["accent2"])
        try: self.query_one(SysDisk).update(t)
        except Exception: pass

        # Net
        t = Text()
        t.append(f" {L['sys_load']} / Net\n\n", C["accent"])
        rx = sd.get("rx_s", 0); tx = sd.get("tx_s", 0)
        t.append(f"  {L['rx_label']}  ", C["dn"]); t.append(f"{fmt_s(rx)}\n", C["dn"])
        t.append(f"  {L['tx_label']}  ", C["up"]); t.append(f"{fmt_s(tx)}\n", C["up"])
        t.append(f"\n  {L['total_rx_label']}   {fmt_b(sd.get('rx_tot', 0))}\n", C["dim"])
        t.append(f"  {L['total_tx_label']}   {fmt_b(sd.get('tx_tot', 0))}\n", C["dim"])
        try: self.query_one(SysNet).update(t)
        except Exception: pass

        # Processes
        t = Text()
        t.append(f" {L['top_procs_ram']}\n\n", C["accent"])
        t.append(f"  {'PID':>7}  {'NAME':<20}  {'CPU':>6}  {'RAM':>10}\n", C["dim"])
        t.append("  " + H*50 + "\n", C["dim"])
        for pid, name, cpu_p, mem in sd.get("top_procs", []):
            ns = (name[:18]+"...") if len(name) > 19 else name
            cc = C["warn"] if cpu_p > 50 else C["ok"] if cpu_p > 10 else C["dim"]
            mc = C["warn"] if mem > 500_000_000 else C["dim"]
            t.append(f"  {pid:>7}  {ns:<20}  ", C["dim"])
            t.append(f"{cpu_p:>5.1f}%", cc)
            t.append(f"  {fmt_b(mem):>10}\n", mc)
        try: self.query_one(SysProcs).update(t)
        except Exception: pass

        # Ping
        t = Text()
        t.append(f" {L['sys_latency']}\n\n", C["accent"])
        for host in self._ping_hosts:
            ms = self.sys_s.ping(host)
            if ms is None:
                t.append(f"  {host:<22} ", C["dim"]); t.append("...\n", C["dim"])
            elif ms < 0:
                t.append(f"  {host:<22} ", C["dim"]); t.append(f"X {L['ping_fail']}\n", C["err"])
            else:
                col = C["ok"] if ms < 50 else C["warn"] if ms < 150 else C["err"]
                t.append(f"  {host:<22} ", C["dim"])
                t.append(gauge(min(ms, 300), 300, 10), col)
                t.append(f"  {ms:5.0f} ms\n", col)
        t.append(f"\n  {L['dns_check']}\n", C["accent2"])
        for dns in ["1.1.1.1", "8.8.8.8"]:
            ms = self.sys_s.ping(dns)
            t.append(f"  {dns} (DNS)  ", C["dim"])
            t.append(f"{ms:.0f} ms  OK\n" if ms and ms > 0 else "...\n",
                     C["ok"] if ms and ms > 0 else C["dim"])
        try: self.query_one(SysPing).update(t)
        except Exception: pass

    # ── LOG ──────────────────────────────────────────────────

    def _draw_log(self):
        t = Text(); L = self.L
        blk_s    = self.log_tail._block_session
        blk_rate = self.log_tail.block_per_min()
        top      = self.log_tail.top_blocked(10)

        t.append(f" {L['log_title']}", C["accent"])
        if blk_s > 0:
            t.append(f"   {L['log_blocked']} ", C["dim"])
            t.append(f"{blk_s}", C["err"])
            t.append(f" {L['session']}" if 'session' in L else " session", C["dim"])
            if blk_rate >= 0.1:
                t.append(f"   {blk_rate:.1f}/min", C["warn"])
        t.append("\n")

        if top:
            t.append(f"\n {L['top_blocked']}\n", C["err"])
            t.append("  " + H*72 + "\n", C["dim"])
            t.append(f"  {L['target_header']:<45} {L['block_header']:>8} {L['percent_symbol']:>6}\n", C["dim"])
            t.append("  " + H*72 + "\n", C["dim"])
            max_cnt = top[0][1] if top else 1
            for target, cnt in top:
                short = (target[:43]+"...") if len(target) > 44 else target
                pct   = cnt / max(blk_s, 1) * 100
                bar   = gauge(cnt, max_cnt, 8)
                col = C["dim"] if target.startswith("[udp]") or target.startswith("[ip]") else C["err"]
                t.append(f"  {short:<45}", col)
                t.append(f" {cnt:>8}", C["err"])
                t.append(f" {pct:>5.1f}%", C["warn"])
                t.append(f"  {bar}\n", col)
            t.append("\n")

        t.append("  " + H*120 + "\n", C["dim"])
        lines = self.log_tail.read()
        if not lines:
            t.append(f"  {L['log_empty']}: {self.log_tail.path}\n", C["dim"])
        else:
            for line in lines:
                ll = line.lower()
                col = (C["err"] if "-> block" in ll or "->block" in ll else
                       C["up"]  if "accepted" in ll else
                       C["dim"])
                t.append(f"  {line[:130]}\n", col)
        try: self.query_one(LogW).update(t)
        except Exception: pass

    # ── CONN LOG ─────────────────────────────────────────────

    def _draw_conn(self):
        t = Text(); L = self.L
        t.append(f" {L['conn_log']}\n", C["accent"])
        t.append("  " + H*78 + "\n", C["dim"])
        evs = list(self.xray.conn_events)
        if not evs:
            t.append(f"\n  {L['no_conn_log']}\n", C["dim"])
        else:
            today = [e for e in evs if e.kind == "connect" and time.time()-e.ts < 86400]
            t.append(f"  Total: {len(evs)}   Today: {len(today)}\n\n", C["dim"])
            for ev in reversed(evs[-80:]):
                col  = C["online"] if ev.kind == "connect" else C["offline"]
                icon = "->" if ev.kind == "connect" else "<-"
                name = (ev.email[:28]+"...") if len(ev.email) > 28 else ev.email
                t.append(f"  {fmt_ts(ev.ts)}  ", C["dim"])
                t.append(f"{icon} ", col)
                t.append(f"{name:<30}", "bold" if ev.kind == "connect" else C["dim"])
                if ev.ip:
                    t.append(f"  {ev.ip:<18}", C["dim"])
                    if ev.geo: t.append(f" {ev.geo}", C["accent2"])
                t.append("\n")
        try: self.query_one(ConnW).update(t)
        except Exception: pass

    # ── ACTIONS ──────────────────────────────────────────────

    def action_reconnect(self):
        self.xray.disconnect(); self.xray.connect()
        self.notify(self.L["reconnecting"])

    def action_toggle_sort(self):
        order = ["downlink", "uplink", "total"]
        self.sort_by = order[(order.index(self.sort_by)+1) % len(order)]
        self.notify({"downlink": self.L["sort_down"],
                     "uplink":   self.L["sort_up"],
                     "total":    self.L["sort_total"]}[self.sort_by])

    def action_toggle_lang(self):
        self.lang_key = "ru" if self.lang_key == "en" else "en"
        self._refresh_language()
        self.notify(self.L["lang_switched"])

    def action_toggle_pause(self):
        self.paused = not self.paused
        if self.paused:
            self._paused_at = time.time()
        self.notify(self.L["paused"] if self.paused else self.L["resumed"])

    def action_toggle_filter(self):
        self.show_filter = not self.show_filter
        bar = self.query_one("#filter-bar")
        inp = self.query_one("#filter-input", Input)
        if self.show_filter:
            bar.styles.display = "block"; inp.focus()
        else:
            bar.styles.display = "none"; self.filter_txt = ""; inp.value = ""

    def action_clear_filter(self):
        if self.show_filter: self.action_toggle_filter()

    def action_reset_stats(self):
        if not self.xray.stub: return
        try:
            self.xray.reset()
            self.notify(self.L["stats_reset"], severity="warning")
        except Exception as e:
            self.notify(f"{self.L['reset_fail']}: {e}", severity="error")

    def action_show_qr(self):
        url = getattr(self, "_qr_url", "")
        if not url:
            ip = self._get_server_ip()
            clients = self.cfg.build_client_urls(ip)
            url = clients[0]["url"] if clients else ""
        if not url:
            self.notify(self.L["enter_server_ip_first"], severity="warning")
            return
        if not HAS_QR:
            copy_to_clipboard(url)
            self.notify(self.L['url_saved_qr'])
            return
        self.push_screen(QRModal(url, self.L['vless_url']))

    def action_restart_xray(self):
        def _do():
            ok, msg = restart_xray()
            if ok:
                self.call_from_thread(lambda: self.notify(
                    self.L["xray_restarted"], severity="warning"))
                time.sleep(2)
                self.call_from_thread(self.action_reconnect)
            else:
                bak = self._find_last_backup()
                hint = "  [B] Rollback config" if bak else ""
                self.call_from_thread(lambda: self.notify(
                    f"{self.L['xray_restart_fail']}: {msg}{hint}", severity="error"))
        self.notify("Restarting xray...", severity="warning")
        threading.Thread(target=_do, daemon=True).start()

    def action_start_xray(self):
        def _do():
            ok, msg = start_xray()
            if ok:
                self.call_from_thread(lambda: self.notify(
                    self.L["xray_started"], severity="information"))
                time.sleep(2)
                self.call_from_thread(self.action_reconnect)
            else:
                self.call_from_thread(lambda: self.notify(
                    f"{self.L['xray_start_fail']}: {msg}", severity="error"))
        self.notify("Starting xray...", severity="warning")
        threading.Thread(target=_do, daemon=True).start()

    def action_stop_xray(self):
        def _do():
            ok, msg = stop_xray()
            if ok:
                self.call_from_thread(lambda: self.notify(
                    self.L["xray_stopped"], severity="warning"))
            else:
                self.call_from_thread(lambda: self.notify(
                    f"{self.L['xray_stop_fail']}: {msg}", severity="error"))
        self.notify("Stopping xray...", severity="warning")
        threading.Thread(target=_do, daemon=True).start()

    def action_toggle_enable_xray(self):
        def _do():
            status = get_xray_status()
            if status["enabled"]:
                ok, msg = disable_xray()
                if ok:
                    self.call_from_thread(lambda: self.notify(
                        self.L["xray_disabled"], severity="warning"))
                else:
                    self.call_from_thread(lambda: self.notify(msg, severity="error"))
            else:
                ok, msg = enable_xray()
                if ok:
                    self.call_from_thread(lambda: self.notify(
                        self.L["xray_enabled"], severity="information"))
                else:
                    self.call_from_thread(lambda: self.notify(msg, severity="error"))
        threading.Thread(target=_do, daemon=True).start()

    def action_update_xray(self):
        self._update_status = self.L["xray_update_checking"]
        self.notify(self.L["xray_update_checking"], severity="information")

        def _progress(stage, msg):
            self._update_status = msg
            self.call_from_thread(lambda: self._draw_mgmt_tab())

        def _done(ok, msg):
            self._update_status = msg
            severity = "information" if ok else "error"
            self.call_from_thread(lambda: self.notify(msg, severity=severity))
            self.call_from_thread(lambda: self._draw_mgmt_tab())
            if ok:
                time.sleep(2)
                self.call_from_thread(self.action_reconnect)

        update_xray_async(callback=_progress, done_callback=_done)

    def _backup_config(self) -> str:
        path = self.cfg.path
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak  = f"{path}.bak_{ts}"
        try:
            shutil.copy2(path, bak)
            self._bak_cache_t = 0  # Invalidate cache
            baks = self._get_backups()
            for old in baks[:-10]:
                try: os.remove(old)
                except Exception: pass
            self._bak_cache_t = 0  # Invalidate again after cleanup
            return bak
        except Exception as e:
            self.notify(f"Backup failed: {e}", severity="warning")
            return ""

    def _find_last_backup(self) -> str:
        baks = self._get_backups()
        return baks[-1] if baks else ""

    def _get_backups(self) -> list:
        """Get backups with 30s cache."""
        now = time.time()
        if now - self._bak_cache_t > 30:
            self._bak_cache = sorted(glob.glob(f"{self.cfg.path}.bak_*"))
            self._bak_cache_t = now
        return self._bak_cache

    def action_edit_config(self):
        path = self.cfg.path
        if not os.path.exists(path):
            self.notify(self.L["config_not_found"].format(path=path), severity="error")
            return
        bak = self._backup_config()
        if bak:
            self.notify(self.L["backup_done"].format(bak=os.path.basename(bak)), severity="information")
        try:
            with self.suspend():
                subprocess.run(["nano", path])
        except Exception as e:
            self.notify(self.L["error_path_nano"].format(err=e, path=path), severity="warning")
            return
        self.cfg._mtime = 0
        self._draw_keys_panel()
        self.notify(self.L["config_reloaded_hint"], severity="information")

    def action_rollback_config(self):
        bak = self._find_last_backup()
        if not bak:
            self.notify(self.L["no_backups_found"], severity="warning")
            return
        try:
            shutil.copy2(bak, self.cfg.path)
            self.cfg._mtime = 0
            self._draw_keys_panel()
            self.notify(self.L["rolled_back_to"].format(bak=os.path.basename(bak)), severity="warning")
        except Exception as e:
            self.notify(self.L["rollback_error"].format(err=e), severity="error")
            return
        self.action_restart_xray()

    def action_check_config(self):
        def _do():
            ok, out = self.cfg.check_syntax()
            if ok is None:
                self.call_from_thread(lambda: self.notify(self.L['xray_not_found'], severity="warning"))
            elif ok:
                self.call_from_thread(lambda: self.notify(f"OK  {self.L['config_ok']}"))
            else:
                lines = out.splitlines()
                msg = next((l for l in lines if "error" in l.lower()), out[:120])
                self.call_from_thread(lambda: self.notify(self.L['error_msg'].format(msg=msg), severity="error"))
        threading.Thread(target=_do, daemon=True).start()

    def action_tab_dash(self):
        try: self.query_one(TabbedContent).active = "tab-dash"
        except Exception: pass

    def action_tab_keys(self):
        try:
            self.query_one(TabbedContent).active = "tab-keys"
            self._draw_keys_panel()
        except Exception: pass

    def action_tab_sys(self):
        try: self.query_one(TabbedContent).active = "tab-sys"
        except Exception: pass

    def action_tab_log(self):
        try: self.query_one(TabbedContent).active = "tab-log"
        except Exception: pass

    def action_tab_conn(self):
        try: self.query_one(TabbedContent).active = "tab-conn"
        except Exception: pass

    def action_tab_mgmt(self):
        try:
            self.query_one(TabbedContent).active = "tab-mgmt"
            self._draw_mgmt_tab()
        except Exception: pass

    # ── XRAY MANAGEMENT TAB ──────────────────────────────────

    def _draw_mgmt_tab(self):
        """Draw management tab. Only updates periodically to reduce flickering."""
        L = self.L

        # Get status in background and update when ready
        def _update_status():
            try:
                status = get_xray_status()
                t = Text()
                t.append(f" {L['xray_mgmt']}\n\n", C["accent"])

                # Status
                t.append(f"  {L['status_label']}  ", C["accent2"])
                if status["running"]:
                    t.append(f"  {L['xray_running']}", C["ok"])
                    if status["pid"]:
                        t.append(f"  {L['pid_label']}: {status['pid']}", C["dim"])
                else:
                    t.append(f"  {L['xray_not_running']}", C["err"])
                t.append("\n")

                # Version
                t.append(f"  {L['ver_label']}     ", C["accent2"])
                ver = status.get("version") or "?"
                t.append(f"  v{ver}", C["accent3"])
                t.append("\n")

                # Autostart
                t.append(f"  {L['boot_label']}    ", C["accent2"])
                if status["enabled"]:
                    t.append(f"  {L['enabled_label']}", C["ok"])
                else:
                    t.append(f"  {L['disabled_label']}", C["dim"])
                t.append("\n")

                # Binary path
                xray_bin = find_xray_binary()
                if xray_bin:
                    t.append(f"  {L['path_label']}    ", C["accent2"])
                    t.append(f"  {xray_bin}", C["dim"])
                    t.append("\n")

                # Memory if available
                if status.get("memory"):
                    from .utils import fmt_b as _fb
                    t.append(f"  {L['mem_label']}     ", C["accent2"])
                    t.append(f"  {_fb(status['memory'])}", C["dim"])
                    t.append("\n")

                # Latest version check
                t.append("\n")
                t.append("  " + H * 50 + "\n", C["dim"])
                t.append("\n  LATEST VERSION CHECK\n\n", C["accent"])
                latest, url = get_latest_version()
                if latest:
                    t.append(f"  {L['github_label']}  ", C["accent2"])
                    t.append(f"  v{latest}", C["total"])
                    if ver and latest != ver and ver != "?":
                        t.append(f"  {L['update_available']}", C["warn"])
                    elif ver == latest:
                        t.append(f"  ({L['xray_update_latest']})", C["ok"])
                    t.append("\n")
                else:
                    t.append("  GitHub   ...\n", C["dim"])

                # Update progress
                if self._update_status:
                    t.append(f"\n  >> {self._update_status}\n", C["warn"])

                # Hotkeys help
                t.append("\n")
                t.append("  " + H * 50 + "\n", C["dim"])
                t.append(f"\n  {L['hotkeys_title']}\n\n", C["accent"])
                hotkeys = [
                    ("S", L["hotkey_start_xray"],            L["xray_started"]),
                    ("X", L["hotkey_stop_xray"],             L["xray_stopped"]),
                    ("R", L["hotkey_restart_xray"],          L["xray_restarted"]),
                    ("U", L["hotkey_update_xray"],      L["xray_update_done"]),
                    ("E", L["hotkey_toggle_autostart"],       L["xray_enabled"] + "/" + L["xray_disabled"]),
                    ("C", L["hotkey_check_config"],   L["config_ok"]),
                    ("e", L["hotkey_edit_config"],    L["auto_backup"]),
                    ("B", L["hotkey_rollback_config"],       L["restore_backup"]),
                ]
                for key, desc, hint in hotkeys:
                    t.append(f"  [{key}]  ", C["accent3"])
                    t.append(f"{desc:<25}", "bold")
                    t.append(f" {hint}\n", C["dim"])

                self.call_from_thread(lambda: self._set_mgmt(t))
            except Exception as e:
                t_err = Text()
                t_err.append(f" {L['xray_mgmt']}\n\n", C["accent"])
                t_err.append(f"  Error: {e}\n", C["err"])
                self.call_from_thread(lambda: self._set_mgmt(t_err))

        # Run in background thread
        threading.Thread(target=_update_status, daemon=True).start()

    def _set_mgmt(self, t):
        try: self.query_one(MgmtW).update(t)
        except Exception: pass

    # ── KEYS ─────────────────────────────────────────────────

    def _init_keys_from_config(self):
        def _detect():
            try:
                ip_inp = self.query_one("#inp-server", Input)
                if not ip_inp.value.strip():
                    ip = detect_public_ip()
                    if ip:
                        self.call_from_thread(
                            lambda: setattr(self.query_one("#inp-server", Input), "value", ip))
            except Exception:
                pass
            self.call_from_thread(self._draw_keys_panel)
        threading.Thread(target=_detect, daemon=True).start()

    def _get_server_ip(self) -> str:
        try:
            return self.query_one("#inp-server", Input).value.strip()
        except Exception:
            return ""

    def _draw_keys_panel(self):
        L = self.L
        server_ip = self._get_server_ip()
        clients   = self.cfg.build_client_urls(server_ip)

        t = Text()
        t.append(f" {L['access_keys_title']}\n\n", C["accent"])

        if not clients:
            t.append(f"  {L['no_clients']}\n", C["dim"])
            t.append(L['path_label'].format(path=self.cfg.path) + "\n", C["accent2"])
        else:
            for idx, cl in enumerate(clients):
                is_first = idx == 0
                email    = cl["email"] or L["no_email"]
                uid      = cl["uuid"]
                tag      = cl["tag"]
                port     = cl["port"]
                net      = cl["network"]
                sec      = cl["security"]
                flow     = cl["flow"]
                sns      = cl["server_names"]
                sids     = cl["short_ids"]
                url      = cl["url"]

                t.append(f"  [{idx+1}]  ", C["accent2"])
                t.append(f"{email}\n", "bold")
                t.append(f"       UUID     ", C["dim"])
                t.append(f"{uid}\n", C["accent3"])
                t.append(f"       tag      ", C["dim"])
                t.append(f"{tag}  :{port}", C["dim"])
                t.append(f"  {net}+{sec}", C["dim"])
                if flow: t.append(f"  {flow}", C["dim"])
                t.append("\n")
                if sns:
                    t.append(f"       SNI      ", C["dim"])
                    t.append(f"{', '.join(sns[:3])}\n", C["accent"])
                if sids:
                    t.append(f"       shortIDs ", C["dim"])
                    t.append(f"{', '.join(sids[:3])}\n", C["accent"])

                if url and server_ip:
                    t.append(f"\nURL\n", C["dim"])
                    for i in range(0, len(url), 64):
                        t.append(f"       {url[i:i+64]}\n", C["accent3"])
                    if is_first:
                        self._qr_url = url
                elif not server_ip:
                    t.append(f"\n{L['enter_server_ip_url']}\n", C["warn"])

                if idx < len(clients) - 1:
                    t.append("\n" + H*50 + "\n\n", C["dim"])

        if server_ip:
            t.append(f"\n  {L['qr_first_client']}\n", C["dim"])
        t.append(f"  {L['edit_config_hint']}\n", C["dim"])
        t.append(f"  {L['check_rollback']}\n", C["dim"])
        baks = self._get_backups()
        if baks:
            last = os.path.basename(baks[-1])
            t.append(f"  Last backup: {last}  ({len(baks)} total)\n", C["dim"])

        try: self.query_one(KeysLeft).update(t)
        except Exception: pass

        self._draw_keys_right_raw()

    def _draw_keys_right_raw(self):
        t = Text()
        L = self.L
        t.append(f" {L['inbound_config']}\n\n", C["accent"])
        try:
            for ib in self.cfg.get_inbounds():
                if ib.get("protocol") not in ("vless", "vmess"): continue
                ss   = ib.get("streamSettings", {})
                port = ib.get("port", "?")
                tag  = ib.get("tag", "")
                t.append(f"  {tag}  :{port}\n\n", C["accent2"])
                snippet = json.dumps(ss, indent=2, ensure_ascii=False)
                KEY_HI  = ("privateKey", "shortIds", "serverNames", "network", "security")
                for line in snippet.splitlines():
                    hi = any(f'"{k}"' in line for k in KEY_HI)
                    t.append(line + "\n", C["warn"] if hi else C["dim"])
                break
        except Exception as e:
            t.append(self.L["error_lower"].format(err=e) + "\n", C["dim"])
        try: self.query_one(KeysRight).update(t)
        except Exception: pass
