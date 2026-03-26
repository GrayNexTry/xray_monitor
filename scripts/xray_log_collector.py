#!/usr/bin/env python3
"""Фоновый сборщик данных для xray-monitor.

Следит за access.log Xray и записывает в SQLite:
  • IP-адреса клиентов + временны́е метки (first_seen / last_active)
  • SNI-домены, которые посещал каждый IP

Работает независимо от xray-monitor: данные накапливаются даже когда
мониторинг не запущен и отображаются при следующем запуске.

Использование:
  python xray_log_collector.py [опции]

Переменные окружения (переопределяют опции):
  XRAY_LOG_PATH         — путь к access.log
  XRAY_MONITOR_DATA     — путь к traffic_history.db
  COLLECTOR_INTERVAL    — интервал опроса лога в секундах (по умолчанию 5)
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time

# Добавляем src/ в путь, чтобы найти пакет xray_monitor
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_INSTALL_DIR = os.path.dirname(_SCRIPT_DIR)
for _p in (
    os.path.join(_INSTALL_DIR, "src"),          # при запуске из install-директории
    os.path.join(_INSTALL_DIR, "lib", "src"),
    _INSTALL_DIR,
):
    if os.path.isdir(os.path.join(_p, "xray_monitor")):
        sys.path.insert(0, _p)
        break

try:
    from xray_monitor.modules.log_tail    import LogTail
    from xray_monitor.modules.traffic_log import TrafficLog
except ImportError as e:
    print(f"[xray-log-collector] Ошибка импорта: {e}", file=sys.stderr)
    print(f"  Убедитесь что xray-monitor установлен в {_INSTALL_DIR}", file=sys.stderr)
    sys.exit(1)

# ── Конфигурация ─────────────────────────────────────────────

DEFAULT_LOG  = "/var/log/xray/access.log"
DEFAULT_DB   = "/opt/xray-monitor/traffic_history.db"
DEFAULT_INTERVAL = 5.0

_running = True


def _on_signal(signum, frame):  # type: ignore[type-arg]
    global _running
    _running = False


def main() -> None:
    global _running

    parser = argparse.ArgumentParser(
        description="xray-log-collector: фоновый сборщик SNI/IP данных"
    )
    parser.add_argument(
        "--log", default=os.environ.get("XRAY_LOG_PATH", DEFAULT_LOG),
        help=f"Путь к access.log (по умолч. {DEFAULT_LOG})"
    )
    parser.add_argument(
        "--db", default=os.environ.get("XRAY_MONITOR_DATA", DEFAULT_DB),
        help=f"Путь к SQLite-базе (по умолч. {DEFAULT_DB})"
    )
    parser.add_argument(
        "--interval", type=float,
        default=float(os.environ.get("COLLECTOR_INTERVAL", DEFAULT_INTERVAL)),
        help=f"Интервал опроса лога в секундах (по умолч. {DEFAULT_INTERVAL})"
    )
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT,  _on_signal)

    log_tail = LogTail(args.log)
    tl_db    = TrafficLog(args.db)

    print(f"[xray-log-collector] Слежу за:   {args.log}")
    print(f"[xray-log-collector] База данных: {args.db}")
    print(f"[xray-log-collector] Интервал:   {args.interval}s")
    print("[xray-log-collector] Запущен. Ctrl+C для остановки.")

    flush_every = max(1, int(60 / args.interval))   # ~раз в минуту
    tick = 0

    while _running:
        try:
            log_tail.update_block_stats()

            tick += 1
            if tick % flush_every == 0:
                # Сохраняем SNI
                sni_buf = log_tail.flush_new_sni()
                if sni_buf:
                    tl_db.save_ip_sni(sni_buf)

                # Сохраняем IP-подключения (first_seen + last_active)
                if log_tail.client_ips:
                    tl_db.save_ip_connections(log_tail.client_ips)

        except Exception as exc:
            print(f"[xray-log-collector] Ошибка: {exc}", file=sys.stderr)

        time.sleep(args.interval)

    print("[xray-log-collector] Остановлен.")


if __name__ == "__main__":
    main()
