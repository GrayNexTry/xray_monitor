"""Панель управления Xray: статус, версия, логи ошибок, горячие клавиши."""

from __future__ import annotations

import subprocess
import threading
from typing import Callable, TYPE_CHECKING

from rich.text import Text

from ..constants import C, L
from ..utils import fmt_b, H
from ..modules.xray_manager import (
    get_xray_status, get_latest_version, find_xray_binary,
)

if TYPE_CHECKING:
    from ..app import XrayMonitor


def _get_xray_journal(lines: int = 40) -> list:
    """Возвращает последние N строк из journalctl для сервиса xray."""
    try:
        r = subprocess.run(
            ["journalctl", "-u", "xray", "-n", str(lines), "--no-pager",
             "--output=short-iso"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()
    except Exception:
        pass
    return []


def build_management_text(app: "XrayMonitor") -> Text:
    """Строит Text для панели управления (запускается в фоновом потоке)."""
    t = Text()
    t.append(f" {L['xray_mgmt']}\n\n", C["accent"])

    try:
        status = get_xray_status()

        # ── Статус ────────────────────────────────────────────
        t.append(f"  {L['status_label']}   ", C["accent2"])
        if status["running"]:
            t.append(f"  ● {L['xray_running']}", C["ok"])
            if status["pid"]:
                t.append(f"   PID: {status['pid']}", C["dim"])
        else:
            t.append(f"  ○ {L['xray_not_running']}", C["err"])
        t.append("\n")

        # ── Версия ────────────────────────────────────────────
        ver = status.get("version") or "?"
        t.append(f"  {L['ver_label']}       ", C["accent2"])
        t.append(f"  v{ver}", C["accent3"])
        t.append("\n")

        # ── Автозапуск ────────────────────────────────────────
        t.append(f"  {L['boot_label']}     ", C["accent2"])
        if status["enabled"]:
            t.append(f"  {L['enabled_label']}", C["ok"])
        else:
            t.append(f"  {L['disabled_label']}", C["dim"])
        t.append("\n")

        # ── Путь к binary ─────────────────────────────────────
        xray_bin = find_xray_binary()
        if xray_bin:
            t.append(f"  {L['path_label']}      ", C["accent2"])
            t.append(f"  {xray_bin}", C["dim"])
            t.append("\n")

        # ── Память ────────────────────────────────────────────
        if status.get("memory"):
            t.append(f"  {L['mem_label']}       ", C["accent2"])
            t.append(f"  {fmt_b(status['memory'])}", C["dim"])
            t.append("\n")

        # ── Последняя версия на GitHub ────────────────────────
        t.append("\n")
        t.append("  " + H * 50 + "\n", C["dim"])
        t.append(f"\n  {L['latest_version_check']}\n\n", C["accent"])
        latest, _url = get_latest_version()
        if latest:
            t.append(f"  {L['github_label']}   ", C["accent2"])
            t.append(f"  v{latest}", C["total"])
            if ver and ver != "?" and latest != ver:
                t.append(f"  {L['update_available']}", C["warn"])
            elif ver == latest:
                t.append(f"  ({L['xray_update_latest']})", C["ok"])
            t.append("\n")
        else:
            t.append("  GitHub   ...\n", C["dim"])

        # ── Прогресс обновления ───────────────────────────────
        if app._update_status:
            t.append(f"\n  >> {app._update_status}\n", C["warn"])

        # ── Логи xray (journal) ───────────────────────────────
        t.append("\n")
        t.append("  " + H * 50 + "\n", C["dim"])
        journal_lines = _get_xray_journal(30)
        if journal_lines:
            running = status["running"]
            t.append(f"\n  ЖУРНАЛ XRAY {'(работает)' if running else '(остановлен — последние ошибки):'}\n\n",
                     C["ok"] if running else C["err"])
            for line in journal_lines:
                ll = line.lower()
                col = (C["err"]  if any(w in ll for w in ("error", "fatal", "panic", "failed")) else
                       C["warn"] if any(w in ll for w in ("warn", "warning")) else
                       C["dim"])
                t.append(f"  {line[:120]}\n", col)
        else:
            t.append(f"\n  journalctl недоступен или записей нет\n", C["dim"])

    except Exception as e:
        t.append(f"  Ошибка: {e}\n", C["err"])

    return t


def build_hotkeys_text() -> Text:
    """Статичный блок горячих клавиш для правой колонки панели управления."""
    t = Text()
    t.append(f" {L['hotkeys_title']}\n\n", C["accent"])
    hotkeys = [
        ("S", L["hotkey_start_xray"],       L["xray_started"]),
        ("X", L["hotkey_stop_xray"],         L["xray_stopped"]),
        ("R", L["hotkey_restart_xray"],      L["xray_restarted"]),
        ("H", "Перезагрузка конфига",         "restart — применить новый конфиг"),
        ("U", L["hotkey_update_xray"],       L["xray_update_done"]),
        ("E", L["hotkey_toggle_autostart"],  L["xray_enabled"] + "/" + L["xray_disabled"]),
        ("C", L["hotkey_check_config"],      L["config_ok"]),
        ("e", L["hotkey_edit_config"],       L["auto_backup"]),
        ("B", L["hotkey_rollback_config"],   L["restore_backup"]),
    ]
    for key, desc, hint in hotkeys:
        t.append(f"  [{key}]  ", C["accent3"])
        t.append(f"{desc:<32}", "bold")
        t.append(f" {hint}\n", C["dim"])
    return t


def start_management_update(app: "XrayMonitor",
                             on_ready: Callable[[Text], None]) -> None:
    """Запускает сборку панели управления в фоновом потоке."""
    def _run() -> None:
        t = build_management_text(app)
        app.call_from_thread(lambda: on_ready(t))

    threading.Thread(target=_run, daemon=True).start()
