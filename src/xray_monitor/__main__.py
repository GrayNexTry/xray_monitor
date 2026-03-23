#!/usr/bin/env python3
"""
xray-monitor v10 — TUI for personal Xray VPN server
pip install textual grpcio protobuf psutil qrcode
Usage: xray-monitor [--server 127.0.0.1:10085] [--config /usr/local/etc/xray/config.json] [--interval 2] [--lang ru]
Tabs: 1=Dashboard  2=Keys  3=System  4=Log  5=Connections
"""

import sys
import argparse


def main():
    try:
        import grpc  # noqa: F401
    except ImportError:
        print("pip install grpcio protobuf"); sys.exit(1)
    try:
        from textual.app import App  # noqa: F401
    except ImportError:
        print("pip install textual>=0.47"); sys.exit(1)

    from .app import XrayMonitor

    p = argparse.ArgumentParser(
        description="xray-monitor v10",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Keys:
  q  — quit            r — reconnect       s — sort
  z  — reset counters  p — pause           l — language
  Q  — QR code         e — nano config     R — restart xray
  C  — check config    B — rollback config
  1-5 — tabs           f — filter users

pip install textual grpcio protobuf psutil qrcode
""")
    p.add_argument("-s", "--server",   default="127.0.0.1:10085")
    p.add_argument("-i", "--interval", type=float, default=2.0)
    p.add_argument("-l", "--log",      default="/var/log/xray/access.log")
    p.add_argument("-c", "--config",   default="/usr/local/etc/xray/config.json")
    p.add_argument("--lang",          choices=["en", "ru"], default="ru")
    p.add_argument("--no-geo",        action="store_true")
    p.add_argument("--ping",          nargs="*", default=None)
    a = p.parse_args()

    app = XrayMonitor(server=a.server, interval=a.interval,
                      log_path=a.log, config_path=a.config, lang=a.lang)
    if a.no_geo:  app.geo_on = False
    if a.ping:    app._ping_hosts = a.ping
    app.run()


if __name__ == "__main__":
    main()
