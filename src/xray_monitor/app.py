"""Главное TUI-приложение xray-monitor."""

from __future__ import annotations

import os
import glob
import shutil
import subprocess
import threading
import ipaddress
import socket
import time
from datetime import datetime
from urllib.request import urlopen

from textual.app import App, ComposeResult
from textual.widgets import Header, Static, TabbedContent, TabPane, Input
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.binding import Binding
from rich.text import Text

from .constants import C, L
from .utils import (
    H, V,
    copy_to_clipboard, HAS_QR, HAS_PSUTIL,
)
from .modules.geoip        import GeoIP
from .modules.config       import XrayConfig
from .modules.stats        import XrayStats
from .modules.log_tail     import LogTail
from .modules.sys_stats    import SysStats
from .modules.traffic_log  import TrafficLog
from .modules.xray_manager import (
    get_xray_status, start_xray, stop_xray, restart_xray,
    enable_xray, disable_xray, update_xray_async,
)
from .widgets import (
    CSS, OvBox, SysBox, TrafficW, UsersW,
    KeysLeft, KeysRight,
    SysCpuRam, SysDisk, SysNet, SysProcs, SysPing,
    LogW, ConnW, MgmtW, StatusBar, HintsBar, QRModal,
)
from .panels.dashboard   import render_overview, render_sysmini, render_traffic, render_users
from .panels.system      import render_cpu_ram, render_disk, render_net, render_procs, render_ping
from .panels.logs        import render_log
from .panels.connections import render_connections
from .panels.keys        import render_keys_left, render_keys_right
from .panels.management  import start_management_update


def detect_public_ip() -> str:
    for url in ["https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"]:
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

    BINDINGS = [
        Binding("q",      "quit",               "", show=False),
        Binding("r",      "reconnect",           "", show=False),
        Binding("s",      "toggle_sort",         "", show=False),
        Binding("z",      "reset_stats",         "", show=False),
        Binding("p",      "toggle_pause",        "", show=False),
        Binding("Q",      "show_qr",             "", show=False),
        Binding("R",      "restart_xray",        "", show=False),
        Binding("e",      "edit_config",         "", show=False),
        Binding("C",      "check_config",        "", show=False),
        Binding("B",      "rollback_config",     "", show=False),
        Binding("S",      "start_xray",          "", show=False),
        Binding("X",      "stop_xray",           "", show=False),
        Binding("U",      "update_xray",         "", show=False),
        Binding("E",      "toggle_enable_xray",  "", show=False),
        Binding("1", "tab_dash",  "", show=False),
        Binding("2", "tab_keys",  "", show=False),
        Binding("3", "tab_sys",   "", show=False),
        Binding("4", "tab_log",   "", show=False),
        Binding("5", "tab_conn",  "", show=False),
        Binding("6", "tab_mgmt",  "", show=False),
        Binding("f",      "toggle_filter",  "", show=False),
        Binding("escape", "clear_filter",   "", show=False),
    ]

    # ── Подсказки по вкладкам ────────────────────────────────
    _TAB_HINTS: dict = {
        "tab-dash":  "q выход  r реконнект  s сортировка  z сброс  p пауза  Q QR  f фильтр  1-6 вкладки",
        "tab-keys":  "q выход  e редактор  C проверка  B откат  Q QR  r реконнект  1-6 вкладки",
        "tab-sys":   "q выход  r реконнект  p пауза  1-6 вкладки",
        "tab-log":   "q выход  r реконнект  z сброс блокировок  1-6 вкладки",
        "tab-conn":  "q выход  r реконнект  f фильтр  1-6 вкладки",
        "tab-mgmt":  "q выход  S старт  X стоп  R рестарт  E авт.запуск  U обновить  e редактор  C проверка  B откат",
    }

    sort_by     = reactive("downlink")
    geo_on      = reactive(True)
    paused      = reactive(False)
    filter_txt  = reactive("")
    show_filter = reactive(False)

    def __init__(self, server: str, interval: float,
                 log_path: str, config_path: str) -> None:
        super().__init__()
        self.xray        = XrayStats(server)
        self.interval    = interval
        self.log_tail    = LogTail(log_path)
        self.geo         = GeoIP()
        self.cfg         = XrayConfig(config_path)
        self.sys_s       = SysStats()
        self.traffic_log = TrafficLog()
        self._last_d: dict | None = None
        self._tick_n  = 0
        self._ping_hosts: list    = ["1.1.1.1", "8.8.8.8", "google.com"]
        self._update_status       = ""
        self._bak_cache:   list   = []
        self._bak_cache_t: float  = 0
        self._mgmt_last_update:   float = 0
        self._mgmt_update_interval: float = 2.0
        self._qr_url      = ""
        self._paused_at: float = 0

    # ── Compose ──────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(id="tabs"):
            with TabPane(L["tab_dashboard"], id="tab-dash"):
                with Container(id="dash"):
                    with Horizontal(id="dash-cols"):
                        with Vertical(id="dash-left"):
                            with Horizontal(id="top-row"):
                                yield OvBox("...",  id="ov-box")
                                yield SysBox("...", id="sys-box")
                            yield TrafficW("...")
                        with Vertical(id="dash-right"):
                            with Container(id="filter-bar"):
                                yield Input(
                                    placeholder=L["filter_placeholder"],
                                    id="filter-input",
                                )
                            yield UsersW("...")
            with TabPane(L["tab_keys"], id="tab-keys"):
                with Horizontal(id="keys-layout"):
                    with Vertical(id="keys-left"):
                        yield KeysLeft("...")
                    with Vertical(id="keys-right"):
                        yield KeysRight("...")
                        with Horizontal(id="keys-srv-row"):
                            yield Input(
                                placeholder=L["server_ip_placeholder"],
                                id="inp-server",
                            )
            with TabPane(L["tab_system"], id="tab-sys"):
                with Container(id="sys-tab"):
                    with Horizontal(id="sys-top"):
                        yield SysCpuRam("...", id="sys-cpuram")
                        yield SysDisk("...",   id="sys-disk")
                        yield SysNet("...",    id="sys-net")
                    with Horizontal(id="sys-bottom"):
                        yield SysProcs("...",  id="sys-procs")
                        yield SysPing("...",   id="sys-ping")
            with TabPane(L["tab_logs"], id="tab-log"):
                with Container(id="log-wrap"):
                    yield LogW("...")
            with TabPane(L["tab_connections"], id="tab-conn"):
                with Container(id="conn-wrap"):
                    yield ConnW("...")
            with TabPane(L["tab_mgmt"], id="tab-mgmt"):
                with Container(id="mgmt-wrap"):
                    yield MgmtW("...")
        yield StatusBar("...", id="status")
        yield HintsBar(self._TAB_HINTS["tab-dash"], id="hints")

    # ── Mount ─────────────────────────────────────────────────

    def on_mount(self) -> None:
        self.sub_title = L["title"]
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

    # ── Events ───────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter-input":
            self.filter_txt = event.value.lower()
            if self._last_d:
                self._draw_users(self._last_d)
        elif event.input.id == "inp-server":
            self._draw_keys_panel()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "filter-input":
            event.input.blur()

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        if not event.tab:
            return
        # Textual формирует id как "--content-tab-tab-dash" → берём часть после последнего "tab-"
        raw = event.tab.id or ""
        # Ищем наш tab-id в строке напрямую
        tab_id = "tab-dash"
        for key in self._TAB_HINTS:
            if key in raw:
                tab_id = key
                break
        hint = self._TAB_HINTS[tab_id]
        try:
            self.query_one("#hints", HintsBar).update(hint)
        except Exception:
            pass

    # ── Tick / Draw ──────────────────────────────────────────

    def _tick(self) -> None:
        if self.paused: return
        self._tick_n += 1
        threading.Thread(target=self.log_tail.update_block_stats, daemon=True).start()
        try:
            d = self.xray.fetch(geo=self.geo if self.geo_on else None)
            self._last_d = d
            if "error" not in d and d.get("users"):
                threading.Thread(
                    target=self.traffic_log.update,
                    args=(d["users"],),
                    daemon=True,
                ).start()
            self._draw(d)
        except Exception as e:
            try: self.query_one(StatusBar).update(Text(f" ✗ {e}", C["err"]))
            except Exception: pass

    def _draw(self, d: dict) -> None:
        err = "error" in d
        t = Text()
        t.append(" ● ", C["err"] if err else C["online"])
        t.append(L["disconnected"] if err else L["connected"], "bold")
        if err:
            t.append(f"  — {d['error']}", C["dim"])
            t.append(f"  │  [6] Управление → S старт  R рестарт", C["warn"])
        t.append(f"  {V}  {self.xray.server}", C["dim"])
        t.append(f"  {V}  r{self.interval}s", C["dim"])
        if self.paused:
            age = int(time.time() - self._paused_at)
            t.append(f"  [{L['paused']} {age}s]", C["warn"])
        if self.filter_txt: t.append(f"  /{self.filter_txt}", C["accent"])
        t.append(f"  #{self._tick_n}  {datetime.now():%H:%M:%S}", C["dim"])
        self.query_one(StatusBar).update(t)

        # Управление всегда обновляется — не зависит от gRPC
        now = time.time()
        if now - self._mgmt_last_update > self._mgmt_update_interval:
            self._mgmt_last_update = now
            self._draw_mgmt_tab()

        if err: return

        self._draw_overview(d)
        self._draw_sysmini(d)
        self._draw_traffic(d)
        self._draw_users(d)
        self._draw_log()
        self._draw_conn()
        self._draw_system_tab()

    # ── Draw-делегаторы ───────────────────────────────────────

    def _draw_overview(self, d: dict) -> None:
        try: self.query_one(OvBox).update(render_overview(self, d))
        except Exception: pass

    def _draw_sysmini(self, d: dict) -> None:
        try: self.query_one(SysBox).update(render_sysmini(self, d))
        except Exception: pass

    def _draw_traffic(self, d: dict) -> None:
        try: self.query_one(TrafficW).update(render_traffic(self, d))
        except Exception: pass

    def _draw_users(self, d: dict) -> None:
        try: self.query_one(UsersW).update(render_users(self, d))
        except Exception: pass

    def _draw_system_tab(self) -> None:
        if not HAS_PSUTIL:
            hint = Text(f"\n  {L['psutil_hint']}\n", C["dim"])
            for w in (SysCpuRam, SysDisk, SysNet, SysProcs, SysPing):
                try: self.query_one(w).update(hint)
                except Exception: pass
            return
        try: self.query_one(SysCpuRam).update(render_cpu_ram(self))
        except Exception: pass
        try: self.query_one(SysDisk).update(render_disk(self))
        except Exception: pass
        try: self.query_one(SysNet).update(render_net(self))
        except Exception: pass
        try: self.query_one(SysProcs).update(render_procs(self))
        except Exception: pass
        try: self.query_one(SysPing).update(render_ping(self))
        except Exception: pass

    def _draw_log(self) -> None:
        try: self.query_one(LogW).update(render_log(self))
        except Exception: pass

    def _draw_conn(self) -> None:
        try: self.query_one(ConnW).update(render_connections(self))
        except Exception: pass

    def _draw_keys_panel(self) -> None:
        try: self.query_one(KeysLeft).update(render_keys_left(self))
        except Exception: pass
        try: self.query_one(KeysRight).update(render_keys_right(self))
        except Exception: pass

    def _draw_mgmt_tab(self) -> None:
        def _set(t: Text) -> None:
            try: self.query_one(MgmtW).update(t)
            except Exception: pass
        start_management_update(self, _set)

    # ── Вспомогательные методы ────────────────────────────────

    def _get_server_ip(self) -> str:
        try:
            return self.query_one("#inp-server", Input).value.strip()
        except Exception:
            return ""

    def _backup_config(self) -> str:
        path = self.cfg.path
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak  = f"{path}.bak_{ts}"
        try:
            shutil.copy2(path, bak)
            self._bak_cache_t = 0
            baks = self._get_backups()
            for old in baks[:-10]:
                try: os.remove(old)
                except Exception: pass
            self._bak_cache_t = 0
            return bak
        except Exception as e:
            self.notify(f"Ошибка бэкапа: {e}", severity="warning")
            return ""

    def _find_last_backup(self) -> str:
        baks = self._get_backups()
        return baks[-1] if baks else ""

    def _get_backups(self) -> list:
        now = time.time()
        if now - self._bak_cache_t > 30:
            self._bak_cache   = sorted(glob.glob(f"{self.cfg.path}.bak_*"))
            self._bak_cache_t = now
        return self._bak_cache

    def _init_keys_from_config(self) -> None:
        def _detect() -> None:
            try:
                ip_inp = self.query_one("#inp-server", Input)
                if not ip_inp.value.strip():
                    ip = detect_public_ip()
                    if ip:
                        self.call_from_thread(
                            lambda: setattr(
                                self.query_one("#inp-server", Input), "value", ip))
            except Exception:
                pass
            self.call_from_thread(self._draw_keys_panel)
        threading.Thread(target=_detect, daemon=True).start()

    # ── Actions ──────────────────────────────────────────────

    def action_reconnect(self) -> None:
        self.xray.disconnect(); self.xray.connect()
        self.notify(L["reconnecting"])

    def action_toggle_sort(self) -> None:
        order = ["downlink", "uplink", "total"]
        self.sort_by = order[(order.index(self.sort_by)+1) % len(order)]
        self.notify({
            "downlink": L["sort_down"],
            "uplink":   L["sort_up"],
            "total":    L["sort_total"],
        }[self.sort_by])

    def action_toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.paused:
            self._paused_at = time.time()
        self.notify(L["paused"] if self.paused else L["resumed"])

    def action_toggle_filter(self) -> None:
        self.show_filter = not self.show_filter
        bar = self.query_one("#filter-bar")
        inp = self.query_one("#filter-input", Input)
        if self.show_filter:
            bar.styles.display = "block"; inp.focus()
        else:
            bar.styles.display = "none"; self.filter_txt = ""; inp.value = ""

    def action_clear_filter(self) -> None:
        if self.show_filter:
            self.action_toggle_filter()

    def action_reset_stats(self) -> None:
        if not self.xray.stub: return
        try:
            self.xray.reset()
            self.notify(L["stats_reset"], severity="warning")
        except Exception as e:
            self.notify(f"{L['reset_fail']}: {e}", severity="error")

    def action_show_qr(self) -> None:
        url = self._qr_url
        if not url:
            ip      = self._get_server_ip()
            clients = self.cfg.build_client_urls(ip)
            url     = clients[0]["url"] if clients else ""
        if not url:
            self.notify(L["enter_server_ip_first"], severity="warning"); return
        if not HAS_QR:
            copy_to_clipboard(url)
            self.notify(L["url_saved_qr"]); return
        self.push_screen(QRModal(url, L["vless_url"]))

    def action_restart_xray(self) -> None:
        def _do() -> None:
            ok, msg = restart_xray()
            if ok:
                self.call_from_thread(lambda: self.notify(
                    L["xray_restarted"], severity="warning"))
                time.sleep(2)
                self.call_from_thread(self.action_reconnect)
            else:
                bak  = self._find_last_backup()
                hint = "  [B] Откат конфига" if bak else ""
                self.call_from_thread(lambda: self.notify(
                    f"{L['xray_restart_fail']}: {msg}{hint}", severity="error"))
        self.notify("Перезапуск xray...", severity="warning")
        threading.Thread(target=_do, daemon=True).start()

    def action_start_xray(self) -> None:
        def _do() -> None:
            ok, msg = start_xray()
            if ok:
                self.call_from_thread(lambda: self.notify(
                    L["xray_started"], severity="information"))
                time.sleep(2)
                self.call_from_thread(self.action_reconnect)
            else:
                self.call_from_thread(lambda: self.notify(
                    f"{L['xray_start_fail']}: {msg}", severity="error"))
        self.notify("Запуск xray...", severity="warning")
        threading.Thread(target=_do, daemon=True).start()

    def action_stop_xray(self) -> None:
        def _do() -> None:
            ok, msg = stop_xray()
            if ok:
                self.call_from_thread(lambda: self.notify(
                    L["xray_stopped"], severity="warning"))
            else:
                self.call_from_thread(lambda: self.notify(
                    f"{L['xray_stop_fail']}: {msg}", severity="error"))
        self.notify("Остановка xray...", severity="warning")
        threading.Thread(target=_do, daemon=True).start()

    def action_toggle_enable_xray(self) -> None:
        def _do() -> None:
            status = get_xray_status()
            if status["enabled"]:
                ok, msg = disable_xray()
                self.call_from_thread(lambda: self.notify(
                    L["xray_disabled"] if ok else msg,
                    severity="warning" if ok else "error"))
            else:
                ok, msg = enable_xray()
                self.call_from_thread(lambda: self.notify(
                    L["xray_enabled"] if ok else msg,
                    severity="information" if ok else "error"))
        threading.Thread(target=_do, daemon=True).start()

    def action_update_xray(self) -> None:
        self._update_status = L["xray_update_checking"]
        self.notify(L["xray_update_checking"], severity="information")

        def _progress(stage: str, msg: str) -> None:
            self._update_status = msg
            self.call_from_thread(self._draw_mgmt_tab)

        def _done(ok: bool, msg: str) -> None:
            self._update_status = msg
            self.call_from_thread(lambda: self.notify(
                msg, severity="information" if ok else "error"))
            self.call_from_thread(self._draw_mgmt_tab)
            if ok:
                time.sleep(2)
                self.call_from_thread(self.action_reconnect)

        update_xray_async(callback=_progress, done_callback=_done)

    def action_edit_config(self) -> None:
        path = self.cfg.path
        if not os.path.exists(path):
            self.notify(L["config_not_found"].format(path=path), severity="error"); return
        bak = self._backup_config()
        if bak:
            self.notify(L["backup_done"].format(bak=os.path.basename(bak)),
                        severity="information")
        try:
            with self.suspend():
                subprocess.run(["nano", path])
        except Exception as e:
            self.notify(L["error_path_nano"].format(err=e, path=path), severity="warning")
            return
        self.cfg._mtime = 0
        self._draw_keys_panel()
        self.notify(L["config_reloaded_hint"], severity="information")

    def action_rollback_config(self) -> None:
        bak = self._find_last_backup()
        if not bak:
            self.notify(L["no_backups_found"], severity="warning"); return
        try:
            shutil.copy2(bak, self.cfg.path)
            self.cfg._mtime = 0
            self._draw_keys_panel()
            self.notify(L["rolled_back_to"].format(bak=os.path.basename(bak)),
                        severity="warning")
        except Exception as e:
            self.notify(L["rollback_error"].format(err=e), severity="error"); return
        self.action_restart_xray()

    def action_check_config(self) -> None:
        def _do() -> None:
            ok, out = self.cfg.check_syntax()
            if ok is None:
                self.call_from_thread(lambda: self.notify(
                    L["xray_not_found"], severity="warning"))
            elif ok:
                self.call_from_thread(lambda: self.notify(f"OK  {L['config_ok']}"))
            else:
                lines = out.splitlines()
                msg   = next((line for line in lines if "error" in line.lower()), out[:120])
                self.call_from_thread(lambda: self.notify(
                    L["error_msg"].format(msg=msg), severity="error"))
        threading.Thread(target=_do, daemon=True).start()

    # ── Tab actions ──────────────────────────────────────────

    def action_tab_dash(self) -> None:
        try: self.query_one(TabbedContent).active = "tab-dash"
        except Exception: pass

    def action_tab_keys(self) -> None:
        try:
            self.query_one(TabbedContent).active = "tab-keys"
            self._draw_keys_panel()
        except Exception: pass

    def action_tab_sys(self) -> None:
        try: self.query_one(TabbedContent).active = "tab-sys"
        except Exception: pass

    def action_tab_log(self) -> None:
        try: self.query_one(TabbedContent).active = "tab-log"
        except Exception: pass

    def action_tab_conn(self) -> None:
        try: self.query_one(TabbedContent).active = "tab-conn"
        except Exception: pass

    def action_tab_mgmt(self) -> None:
        try:
            self.query_one(TabbedContent).active = "tab-mgmt"
            self._mgmt_last_update = 0
            self._draw_mgmt_tab()
        except Exception: pass
