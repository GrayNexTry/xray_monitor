"""Главное TUI-приложение xray-monitor."""

from __future__ import annotations

import logging
import os
import glob
import shutil
import subprocess
import threading
import ipaddress
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.request import urlopen

log = logging.getLogger(__name__)

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
from .modules.ip_registry  import IPRegistry
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
        Binding("delete", "delete_ip_user", "Удалить",      show=True),
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
        self.ip_registry = IPRegistry(self.traffic_log)
        self._last_d: dict | None = None
        self._tick_n  = 0
        self._fetch_lock  = threading.Lock()  # атомарная защита от параллельных тиков
        self._system_lock = threading.Lock()  # блокирует тики при критических операциях
        self._ping_hosts: list    = ["1.1.1.1", "8.8.8.8", "google.com"]
        self._update_status       = ""
        self._critical_threads: list[threading.Thread] = []  # потоки, которые нельзя убивать
        # Фиксированный пул потоков — предотвращает утечку VIRT-памяти
        # (каждый threading.Thread выделяет ~8 МБ стека)
        self._pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="xm")
        self._bak_cache:   list   = []
        self._bak_cache_t: float  = 0
        self._mgmt_last_update:   float = 0
        self._mgmt_update_interval: float = 2.0
        self._qr_url      = ""
        self._paused_at: float = 0
        # IP Радар
        self._ip_sort_col:   str   = "last_active"  # last_active|email|dn|up|status
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
            # Один фоновый поток для сбора системной статистики
            # вместо создания нового потока каждые 3 секунды
            self._sys_collector_stop = threading.Event()
            def _sys_collector_loop():
                while not self._sys_collector_stop.is_set():
                    self.sys_s.collect()
                    self._sys_collector_stop.wait(3.0)
            threading.Thread(target=_sys_collector_loop, daemon=True).start()
        self.set_interval(self.interval, self._tick)
        self.call_later(self._tick)
        for h in self._ping_hosts:
            self.sys_s.ping(h)
        self.call_later(self._init_keys_from_config)
        # Загружаем накопленные IP-данные из БД (SNI + байты)
        self._pool.submit(self._load_ip_data_from_db)

    def _load_ip_data_from_db(self) -> None:
        """Загружает IP-данные из SQLite в IPRegistry (фоновый поток)."""
        try:
            self.ip_registry.load_from_db()
        except Exception:
            log.warning("failed to load IP data from DB", exc_info=True)

    # ── Lifecycle ──────────────────────────────────────────────

    def on_unmount(self) -> None:
        """Корректное завершение: закрываем ресурсы."""
        # Останавливаем фоновый сборщик системной статистики
        if hasattr(self, '_sys_collector_stop'):
            self._sys_collector_stop.set()
        # Ждём завершения критических потоков (обновление бинарника и т.д.)
        for t in self._critical_threads:
            if t.is_alive():
                log.info("waiting for critical thread %s to finish...", t.name)
                t.join(timeout=30.0)
                if t.is_alive():
                    log.warning("critical thread %s still alive after timeout", t.name)
        self._critical_threads.clear()
        try:
            self.ip_registry.flush_to_db()
        except Exception:
            log.debug("ip_registry flush error", exc_info=True)
        try:
            self.traffic_log.close()
        except Exception:
            log.debug("traffic_log close error", exc_info=True)
        try:
            self.xray.disconnect()
        except Exception:
            pass
        # Завершаем пул потоков
        try:
            self._pool.shutdown(wait=False)
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
            self._draw_ip_table()

    # ── Tick / Draw ──────────────────────────────────────────

    def _tick(self) -> None:
        """Планировщик тика — запускает _tick_worker в отдельном потоке.

        Важно: set_interval() вызывает этот метод в event loop Textual.
        Все блокирующие операции (gRPC, диск) выполняются в _tick_worker(),
        чтобы не замораживать UI.
        """
        if self.paused: return
        # Атомарная проверка: Lock.acquire(blocking=False) — потокобезопасна
        if not self._fetch_lock.acquire(blocking=False):
            return  # предыдущий тик ещё выполняется
        self._tick_n += 1
        self._pool.submit(self._tick_worker)

    def _tick_worker(self) -> None:
        """Фоновый поток: gRPC-вызовы + обновление SQLite."""
        if not self._system_lock.acquire(blocking=False):
            self._fetch_lock.release()
            return  # критическая операция в процессе — пропускаем тик
        try:
            # Обновляем блок-статистику в этом же потоке (не спавним новый)
            self.log_tail.update_block_stats()
            log_snap = {em: dict(ips) for em, ips in self.log_tail.client_ips.items()}
            d = self.xray.fetch(log_ips=log_snap,
                                ip_registry=self.ip_registry)
            if "error" not in d and d.get("users"):
                self.traffic_log.update(d["users"])   # SQLite с WAL — быстро
            # Обновляем connections в registry из лога
            self.ip_registry.update_connections(log_snap)
            # ── SNI + persistence: каждые 10 тиков ────────────
            if self._tick_n % 10 == 0:
                sni_buf = self.log_tail.flush_new_sni()
                if sni_buf:
                    self.ip_registry.update_sni(sni_buf)
                self.ip_registry.flush_to_db()
            self.call_from_thread(lambda: self._after_tick(d))
        except Exception as e:
            log.exception("tick worker error")
            err_msg = str(e)
            self.call_from_thread(lambda: self._tick_error(err_msg))
        finally:
            self._system_lock.release()
            self._fetch_lock.release()

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

    def _safe_update(self, widget_cls: type, content) -> None:
        """Обновляет виджет, логируя ошибки вместо проглатывания."""
        try:
            self.query_one(widget_cls).update(content)
        except Exception:
            log.debug("failed to update %s", widget_cls.__name__, exc_info=True)

    def _draw_overview(self, d: dict) -> None:
        self._safe_update(OvBox, render_overview(self, d))

    def _draw_sysmini(self, d: dict) -> None:
        self._safe_update(SysBox, render_sysmini(self, d))

    def _draw_traffic(self, d: dict) -> None:
        self._safe_update(TrafficW, render_traffic(self, d))

    def _draw_users(self, d: dict) -> None:
        self._safe_update(UsersW, render_users(self, d))

    def _draw_system_tab(self) -> None:
        if not HAS_PSUTIL:
            hint = Text(f"\n  {L['psutil_hint']}\n", C["dim"])
            for w in (SysCpuRam, SysDisk, SysNet, SysProcs, SysPing):
                self._safe_update(w, hint)
            return
        self._safe_update(SysCpuRam, render_cpu_ram(self))
        self._safe_update(SysDisk, render_disk(self))
        self._safe_update(SysNet, render_net(self))
        self._safe_update(SysProcs, render_procs(self))
        self._safe_update(SysPing, render_ping(self))

    def _draw_log(self) -> None:
        self._safe_update(LogW, render_log(self))

    def _draw_conn(self) -> None:
        self._safe_update(ConnW, render_connections(self))

    def _draw_ip_table(self) -> None:
        """Обновляет IP-радар: пересобирает таблицу и сортировочную строку."""
        try:
            if self.query_one(TabbedContent).active != "tab-ip":
                return
        except Exception:
            return

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
            total = self.ip_registry.get_total_count()
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
        self._pool.submit(_detect)

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
        """H — перезагрузка конфига xray (restart, т.к. hot reload не поддерживается)."""
        def _do() -> None:
            with self._system_lock:
                ok, msg = reload_xray()
            if ok:
                self.call_from_thread(lambda: self.notify(
                    "Конфиг применён (restart)", severity="warning"))
                self.call_from_thread(self.action_reconnect)
            else:
                self.call_from_thread(lambda: self.notify(
                    msg, severity="error"))
        self.notify("Перезагрузка конфига xray...", severity="warning")
        self._pool.submit(_do)

    def action_restart_xray(self) -> None:
        def _do() -> None:
            with self._system_lock:
                ok, msg = restart_xray()
            if ok:
                self.call_from_thread(lambda: self.notify(
                    L["xray_restarted"], severity="warning"))
                self.call_from_thread(self.action_reconnect)
            else:
                bak  = self._find_last_backup()
                hint = "  [B] Откат конфига" if bak else ""
                self.call_from_thread(lambda: self.notify(
                    f"{L['xray_restart_fail']}: {msg}{hint}", severity="error"))
        self.notify("Перезапуск xray...", severity="warning")
        self._pool.submit(_do)

    def action_start_xray(self) -> None:
        def _do() -> None:
            with self._system_lock:
                ok, msg = start_xray()
            if ok:
                self.call_from_thread(lambda: self.notify(
                    L["xray_started"], severity="information"))
                self.call_from_thread(self.action_reconnect)
            else:
                self.call_from_thread(lambda: self.notify(
                    f"{L['xray_start_fail']}: {msg}", severity="error"))
        self.notify("Запуск xray...", severity="warning")
        self._pool.submit(_do)

    def action_stop_xray(self) -> None:
        def _do() -> None:
            with self._system_lock:
                ok, msg = stop_xray()
            if ok:
                self.call_from_thread(lambda: self.notify(
                    L["xray_stopped"], severity="warning"))
            else:
                self.call_from_thread(lambda: self.notify(
                    f"{L['xray_stop_fail']}: {msg}", severity="error"))
        self.notify("Остановка xray...", severity="warning")
        self._pool.submit(_do)

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
        self._pool.submit(_do)

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

        t = update_xray_async(callback=_progress, done_callback=_done)
        self._critical_threads.append(t)

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
            # Проверяем синтаксис бэкапа ПЕРЕД применением
            ok, out = self.cfg.check_syntax_file(bak)
            if ok is False:
                lines = out.splitlines()
                msg = next((l for l in lines if "error" in l.lower()), out[:120])
                self.notify(f"Бэкап повреждён: {msg}", severity="error")
                return
            # Сохраняем текущий (возможно сломанный) конфиг перед откатом
            self._backup_config()
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
        self._pool.submit(_do)

    # ── Tab actions ──────────────────────────────────────────

    def _switch_tab(self, tab_id: str) -> None:
        """Переключает вкладку, логируя ошибки."""
        try:
            self.query_one(TabbedContent).active = tab_id
        except Exception:
            log.debug("failed to switch to tab %s", tab_id, exc_info=True)

    def action_tab_dash(self) -> None:
        self._switch_tab("tab-dash")

    def action_tab_keys(self) -> None:
        self._switch_tab("tab-keys")
        self._draw_keys_panel()

    def action_tab_sys(self) -> None:
        self._switch_tab("tab-sys")

    def action_tab_log(self) -> None:
        self._switch_tab("tab-log")

    def action_tab_conn(self) -> None:
        self._switch_tab("tab-conn")

    def action_tab_mgmt(self) -> None:
        self._switch_tab("tab-mgmt")
        self._mgmt_last_update = 0
        self._draw_mgmt_tab()

    def action_tab_ip(self) -> None:
        self._switch_tab("tab-ip")
        self._draw_ip_table()

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
        # Пробуем взять IP из кэша; если пусто — читаем курсор таблицы напрямую
        ip = self._current_ip
        if not ip:
            try:
                tbl = self.query_one(IPTableW)
                rk  = tbl.get_row_at(tbl.cursor_row)
                # get_row_at возвращает список ячеек; IP в колонке 2 (индекс 2)
                ip  = str(rk[2]) if rk and len(rk) > 2 else ""
            except Exception:
                pass
        if not ip:
            self.notify("Выберите строку в таблице (↑↓)", severity="warning")
            return

        # Ищем email по IP
        email = self.ip_registry.get_email_for_ip(ip)
        label = f"{ip}" + (f"  ({email})" if email else "")

        def _on_confirm(confirmed: bool) -> None:
            if not confirmed:
                return
            self.ip_registry.delete_ip(ip)
            self.notify(f"История IP {ip} очищена", severity="information")
            self._draw_ip_table()

        self.push_screen(DeleteConfirmScreen(label), _on_confirm)
