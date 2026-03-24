#!/usr/bin/env python3
"""
xray-monitor v10 — TUI-мониторинг персонального Xray VPN-сервера

Установка: pip install textual grpcio psutil qrcode
Использование: xray-monitor [--server 127.0.0.1:10085] [--config /usr/local/etc/xray/config.json]

Вкладки: 1=Панель  2=Ключи  3=Система  4=Логи  5=Подключения  6=Управление
Клавиши:
  q — выход        r — реконнект    s — сортировка
  z — сброс        p — пауза        Q — QR-код
  e — nano конфиг  R — рестарт      C — проверка конфига  B — откат
  S — старт        X — стоп         U — обновить xray-core
  E — вкл/выкл автозапуск
  1-6 — вкладки    f — фильтр
"""

import sys
import argparse

from . import __version__


def main() -> None:
    try:
        import grpc  # noqa: F401
    except ImportError:
        print("Установите grpcio:  pip install grpcio")
        sys.exit(1)
    try:
        from textual.app import App  # noqa: F401
    except ImportError:
        print("Установите textual:  pip install textual>=0.47")
        sys.exit(1)

    from .App import XrayMonitor

    p = argparse.ArgumentParser(
        description=f"xray-monitor v{__version__}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("-s", "--server",   default="127.0.0.1:10085",
                   help="gRPC-адрес Xray (default: 127.0.0.1:10085)")
    p.add_argument("-i", "--interval", type=float, default=2.0,
                   help="Интервал опроса в секундах (default: 2.0)")
    p.add_argument("-l", "--log",      default="/var/log/xray/access.log",
                   help="Путь к access.log")
    p.add_argument("-c", "--config",   default="/usr/local/etc/xray/config.json",
                   help="Путь к config.json")
    p.add_argument("--no-geo",         action="store_true",
                   help="Отключить GeoIP-поиск")
    p.add_argument("--ping",           nargs="*", default=None,
                   help="Хосты для пинга (заменяет список по умолчанию)")
    p.add_argument("--version",        action="version",
                   version=f"xray-monitor v{__version__}")
    a = p.parse_args()

    app = XrayMonitor(
        server=a.server,
        interval=a.interval,
        log_path=a.log,
        config_path=a.config,
    )
    if a.no_geo:  app.geo_on = False
    if a.ping:    app._ping_hosts = a.ping
    app.run()


if __name__ == "__main__":
    main()
