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
from textual.widgets import Header, Footer, Static, TabbedContent, TabPane, Input, DataTable
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
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
    get_xray_status, start_xray, stop_xray, restart_xray, reload_xray,
    enable_xray, disable_xray, update_xray_async,
)
from .widgets import (
    CSS, OvBox, SysBox, TrafficW, UsersW,
    KeysLeft, KeysRight,
    SysCpuRam, SysDisk, SysNet, SysProcs, SysPing,
    LogW, ConnW, MgmtW, StatusBar, QRModal, DeleteConfirmScreen,
    IPTableW, IPDetailW, IPSortBar,
)
from .panels.dashboard   import render_overview, render_sysmini, render_traffic, render_users
from .panels.system      import render_cpu_ram, render_disk, render_net, render_procs, render_ping
from .panels.logs        import render_log
from .panels.connections import render_connections
from .panels.keys        import render_keys_left, render_keys_right
from .panels.management  import start_management_update
from .panels.ip_radar    import render_ip_detail, build_ip_table_rows


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

    # Какие action-ы показывать на каждой вкладке
    _TAB_ACTIONS: dict[str, frozenset] = {
        "tab-dash":  frozenset({"toggle_pause", "toggle_sort", "toggle_filter", "show_qr"}),
        "tab-keys":  frozenset({"show_qr", "edit_config", "check_config", "rollback_config"}),
        "tab-sys":   frozenset(),
        "tab-log":   frozenset({"reset_stats"}),
        "tab-conn":  frozenset({"toggle_filter"}),
        "tab-mgmt":  frozenset({"start_xray", "stop_xray", "restart_xray", "reload_xray",
                                 "toggle_enable_xray", "update_xray",
                                 "edit_config", "check_config", "rollback_config"}),
        "tab-ip":    frozenset({"ip_sort_time", "ip_sort_name", "ip_sort_dn", "ip_sort_status",
                                 "delete_ip_user"}),
    }
    _ALL_TAB_ACTIONS: frozenset = frozenset().union(*_TAB_ACTIONS.values())

    BINDINGS = [
        # ── Всегда видимые ──────────────────────────────────
        Binding("q", "quit",       "Выход",     show=True),
        Binding("r", "reconnect",  "Реконнект", show=True),
        # ── tab-dash ────────────────────────────────────────
        Binding("p", "toggle_pause",  "Пауза",      show=True),
        Binding("s", "toggle_sort",   "Сортировка", show=True),
        Binding("f", "toggle_filter", "Фильтр",     show=True),
        # ── tab-keys / tab-mgmt ─────────────────────────────
        Binding("Q", "show_qr",           "QR-код",   show=True),
        Binding("e", "edit_config",        "Редактор", show=True),
        Binding("C", "check_config",       "Проверка", show=True),
        Binding("B", "rollback_config",    "Откат",    show=True),
        # ── tab-log ─────────────────────────────────────────
        Binding("z", "reset_stats",        "Сброс",    show=True),
        # ── tab-mgmt ────────────────────────────────────────
        Binding("S", "start_xray",         "Старт",        show=True),
        Binding("X", "stop_xray",          "Стоп",         show=True),
        Binding("R", "restart_xray",       "Рестарт",      show=True),
        Binding("H", "reload_xray",        "Релоад",       show=True),
        Binding("E", "toggle_enable_xray", "Авт.запуск",   show=True),
        Binding("U", "update_xray",        "Обновить",     show=True),
        # ── tab-ip ──────────────────────────────────────────
        Binding("t",      "ip_sort_time",   "↕ Время",    show=True),
        Binding("n",      "ip_sort_name",   "↕ Имя",      show=True),
        Binding("d",      "ip_sort_dn",     "↕ Загрузка", show=True),
        Binding("o",      "ip_sort_status", "↕ Статус",   show=True),
        Binding("delete", "delete_ip_user", "Удалить",     show=True),
        # ── Вкладки 1–7 (скрытые) ───────────────────────────
        Binding("1", "tab_dash",  "", show=False),
        Binding("2", "tab_keys",  "", show=False),
        Binding("3", "tab_sys",   "", show=False),
        Binding("4", "tab_log",   "", show=False),
        Binding("5", "tab_conn",  "", show=False),
        Binding("6", "tab_ip",  "", show=False),
        Binding("7", "tab_mgmt",    "", show=False),
        # ── Прочее (скрытые) ────────────────────────────────
        Binding("escape", "clear_filter", "", show=False),
    ]

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
        self._fetching = False   # предотвращает параллельные тики
        self._ping_hosts: list    = ["1.1.1.1", "8.8.8.8", "google.com"]
        self._update_status       = ""
        self._bak_cache:   list   = []
        self._bak_cache_t: float  = 0
        self._mgmt_last_update:   float = 0
        self._mgmt_update_interval: float = 2.0
        self._qr_url      = ""
        self._paused_at: float = 0
        # IP Радар
        self._ip_sort_col:   str   = "last_active"  # last_active|email|dn|up|status
        self._ip_db_cache:   list  = []              # кэш query_all_ips()
        self._ip_db_cache_t: float = 0
        self._current_ip:    str   = ""              # выбранный IP в таблице
        self._active_tab:    str   = "tab-dash"      # текущая вкладка (для Footer)

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
                with VerticalScroll(id="log-scroll"):
                    yield LogW("...")
            with TabPane(L["tab_connections"], id="tab-conn"):
                with VerticalScroll(id="conn-scroll"):
                    yield ConnW("...")
            with TabPane("IP Радар", id="tab-ip"):
                with Vertical(id="ip-radar-tab"):
                    yield IPSortBar("", id="ip-sort-bar")
                    yield IPTableW(id="ip-table")
                    with VerticalScroll(id="ip-detail-scroll"):
                        yield IPDetailW("  Выберите IP стрелками ↑↓",
                                        id="ip-detail")
            with TabPane(L["tab_mgmt"], id="tab-mgmt"):
                with VerticalScroll(id="mgmt-scroll"):
                    yield MgmtW("...")
                    
        yield StatusBar("...", id="status")
        yield Footer()

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
        # Загружаем накопленные IP-данные из БД (SNI + байты)
        threading.Thread(target=self._load_ip_data_from_db, daemon=True).start()

    def _load_ip_data_from_db(self) -> None:
        """Загружает SNI и байты по IP из SQLite в память (фоновый поток)."""
        try:
            stored_bytes = self.traffic_log.load_ip_bytes()
            self.xray.ip_bytes.update(stored_bytes)
            stored_sni = self.traffic_log.load_ip_sni()
            self.log_tail.load_sni_from_db(stored_sni)
        except Exception:
            pass

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

    def on_data_table_row_highlighted(
        self, event: DataTable.RowHighlighted
    ) -> None:
        """Обновляет детальную панель при перемещении курсора в IP-таблице."""
        if event.row_key is None:
            return
        ip = str(event.row_key.value) if event.row_key.value else ""
        if not ip:
            return
        self._current_ip = ip
        try:
            self.query_one(IPDetailW).update(render_ip_detail(self, ip))
        except Exception:
            pass

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Скрывает из Footer биндинги, нерелевантные текущей вкладке."""
        if action in self._ALL_TAB_ACTIONS:
            allowed = self._TAB_ACTIONS.get(self._active_tab, frozenset())
            return True if action in allowed else False
        return True  # глобальные биндинги (q, r, вкладки) — всегда активны

    def on_tabbed_content_tab_activated(
        self, event: TabbedContent.TabActivated
    ) -> None:
        if not event.tab:
            return
        raw = event.tab.id or ""
        # Обновить активную вкладку и перерисовать Footer
        self._active_tab = next(
            (k for k in self._TAB_ACTIONS if k in raw), "tab-dash"
        )
        self.refresh_bindings()
        # IP Радар: сразу обновить таблицу при переключении на вкладку
        if self._active_tab == "tab-ip":
            self._ip_db_cache_t = 0
            self._draw_ip_table()

    # ── Tick / Draw ──────────────────────────────────────────

    def _tick(self) -> None:
        """Планировщик тика — запускает _tick_worker в отдельном потоке.

        Важно: set_interval() вызывает этот метод в event loop Textual.
        Все блокирующие операции (gRPC, диск) выполняются в _tick_worker(),
        чтобы не замораживать UI.
        """
        if self.paused: return
        if self._fetching: return   # пропускаем тик если предыдущий ещё не завершён
        self._tick_n  += 1
        self._fetching = True
        threading.Thread(target=self._tick_worker, daemon=True).start()

    def _tick_worker(self) -> None:
        """Фоновый поток: gRPC-вызовы + обновление SQLite."""
        try:
            threading.Thread(target=self.log_tail.update_block_stats, daemon=True).start()
            log_snap = {em: dict(ips) for em, ips in self.log_tail.client_ips.items()}
            d = self.xray.fetch(log_ips=log_snap)
            if "error" not in d and d.get("users"):
                self.traffic_log.update(d["users"])   # SQLite с WAL — быстро
            # ── SNI Radar: сохраняем в БД каждые 10 тиков ────
            if self._tick_n % 10 == 0:
                sni_buf = self.log_tail.flush_new_sni()
                if sni_buf:
                    self.traffic_log.save_ip_sni(sni_buf)
                if self.xray.ip_bytes:
                    self.traffic_log.save_ip_bytes(
                        self.xray.ip_bytes, self.xray.ip_email
                    )
                if self.log_tail.client_ips:
                    self.traffic_log.save_ip_connections(self.log_tail.client_ips)
            self.call_from_thread(lambda: self._after_tick(d))
        except Exception as e:
            err_msg = str(e)
            self.call_from_thread(lambda: self._tick_error(err_msg))
        finally:
            self._fetching = False

    def _after_tick(self, d: dict) -> None:
        """Вызывается из event loop через call_from_thread — обновляет UI."""
        self._last_d = d
        self._draw(d)

    def _tick_error(self, msg: str) -> None:
        try: self.query_one(StatusBar).update(Text(f" ✗ {msg}", C["err"]))
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
        self._draw_ip_table()
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

    def _draw_ip_table(self) -> None:
        """Обновляет IP-радар: пересобирает таблицу и сортировочную строку."""
        try:
            if self.query_one(TabbedContent).active != "tab-ip":
                return
        except Exception:
            return

        # Обновляем кэш БД каждые 15 с
        now = time.time()
        if now - self._ip_db_cache_t > 15:
            try:
                self._ip_db_cache   = self.traffic_log.query_all_ips()
                self._ip_db_cache_t = now
            except Exception:
                pass

        try:
            rows = build_ip_table_rows(self)
            self.query_one(IPTableW).rebuild(rows, keep_key=self._current_ip)
        except Exception:
            pass

        # Сортировочная строка
        _SORT_LABELS = {
            "last_active": "t время",
            "email":       "n имя",
            "dn":          "d загрузка",
            "up":          "u отдача",
            "status":      "o статус",
        }
        try:
            label = _SORT_LABELS.get(self._ip_sort_col, self._ip_sort_col)
            total = len(self._ip_db_cache)
            hint  = Text()
            hint.append(f"  Сортировка: [{label}]  ", C["accent3"])
            hint.append("t время  n имя  d загрузка  o статус  ", C["dim"])
            hint.append(f"всего: {total} IP", C["dim"])
            self.query_one(IPSortBar).update(hint)
        except Exception:
            pass

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

    def action_reload_xray(self) -> None:
        """H — горячая перезагрузка конфига xray без обрыва сессий (SIGHUP)."""
        def _do() -> None:
            ok, msg = reload_xray()
            self.call_from_thread(lambda: self.notify(
                msg,
                severity="information" if ok else "error",
            ))
            if ok:
                time.sleep(1)
                self.call_from_thread(self.action_reconnect)
        self.notify("Горячая перезагрузка xray...", severity="warning")
        threading.Thread(target=_do, daemon=True).start()

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

    def action_tab_ip(self) -> None:
        try:
            self.query_one(TabbedContent).active = "tab-ip"
            self._ip_db_cache_t = 0   # сбросить кэш, чтобы сразу загрузить
            self._draw_ip_table()
        except Exception: pass

    # ── Сортировка IP-таблицы ────────────────────────────────

    def _ip_sort(self, col: str) -> None:
        if self.query_one(TabbedContent).active != "tab-ip":
            return
        self._ip_sort_col = col
        self._draw_ip_table()

    def action_ip_sort_time(self)   -> None: self._ip_sort("last_active")
    def action_ip_sort_name(self)   -> None: self._ip_sort("email")
    def action_ip_sort_dn(self)     -> None: self._ip_sort("dn")
    def action_ip_sort_status(self) -> None: self._ip_sort("status")

    def action_delete_ip_user(self) -> None:
        """Del — удалить пользователя выбранного IP с подтверждением."""
        ip = self._current_ip
        if not ip:
            return

        # Ищем email по IP
        email = ""
        for e, ips in self.log_tail.client_ips.items():
            if ip in ips:
                email = e
                break
        if not email:
            row = next((r for r in self._ip_db_cache if r.get("ip") == ip), None)
            email = (row or {}).get("email", "")
        if not email:
            self.query_one(StatusBar).update(f" Нет email для IP {ip}")
            return

        def _on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            ok, msg = self.cfg.delete_client(email)
            self.query_one(StatusBar).update(f" {msg}")
            if ok:
                # Горячий релоад xray
                import threading
                from .modules.xray_manager import reload_xray
                def _reload() -> None:
                    reload_xray()
                    self.call_from_thread(lambda: self._draw_ip_table())
                threading.Thread(target=_reload, daemon=True).start()

        self.push_screen(DeleteConfirmScreen(email), _on_confirm)
