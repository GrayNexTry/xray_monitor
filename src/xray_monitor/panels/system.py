"""Панели вкладки «Система»: CPU/RAM, диск, сеть, процессы, пинг."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text

from ..constants import C, L
from ..utils import fmt_b, fmt_s, gauge, pct_bar, pct_col, H

if TYPE_CHECKING:
    from ..App import XrayMonitor


def render_cpu_ram(app: "XrayMonitor") -> Text:
    t = Text()
    sd = app.sys_s.get()
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
    t.append(f"  {L['used_label']} {fmt_b(sd.get('ram_used',0))} / {fmt_b(sd.get('ram_total',0))}\n", C["dim"])
    t.append(f"  {L['free_label']} {fmt_b(sd.get('ram_free',0))}\n", C["dim"])

    load = sd.get("load", (0, 0, 0))
    t.append(f"\n  {L['sys_load']}  ", C["dim"])
    t.append(f"{load[0]:.2f}  {load[1]:.2f}  {load[2]:.2f}\n",
             C["warn"] if load[0] > 2 else C["ok"])
    if sd.get("temp"):
        tc = C["err"] if sd["temp"] > 80 else C["warn"] if sd["temp"] > 65 else C["ok"]
        t.append(f"  {L['sys_temp']}  ", C["dim"]); t.append(f"{sd['temp']:.0f}°C\n", tc)
    return t


def render_disk(app: "XrayMonitor") -> Text:
    t = Text()
    sd = app.sys_s.get()
    t.append(f" {L['sys_disk']}\n\n", C["accent"])

    dp = sd.get("disk_pct", 0.0)
    t.append("  /    ", C["dim"]); t.append(pct_bar(dp, 22), pct_col(dp))
    t.append(f"  {dp:5.1f}%\n", pct_col(dp))
    t.append(f"  {fmt_b(sd.get('disk_used',0))} / {fmt_b(sd.get('disk_tot',0))}\n", C["dim"])

    t.append(f"\n  {L['tcp_connections']}\n", C["accent2"])
    t.append(f"  {L['established_label']}  {sd.get('tcp_est', 0)}\n", C["ok"])
    t.append(f"  {L['listen_label']}       {sd.get('tcp_listen', 0)}\n", C["dim"])
    t.append(f"  {L['processes_label']}    {sd.get('procs', 0)}\n", C["dim"])

    if sd.get("xray_pid"):
        t.append(f"\n  {L['xray_pid_label']}  {sd['xray_pid']}\n", C["accent2"])
        if sd.get("xray_mem"): t.append(f"  {L['xray_ram_label']}  {fmt_b(sd['xray_mem'])}\n", C["accent2"])
        if sd.get("xray_cpu"): t.append(f"  {L['xray_cpu_label']}  {sd['xray_cpu']:.1f}%\n", C["accent2"])
    return t


def render_net(app: "XrayMonitor") -> Text:
    t = Text()
    sd = app.sys_s.get()
    t.append(f" {L['sys_load']} / Net\n\n", C["accent"])
    rx = sd.get("rx_s", 0); tx = sd.get("tx_s", 0)
    t.append(f"  {L['rx_label']}  ", C["dn"]); t.append(f"{fmt_s(rx)}\n", C["dn"])
    t.append(f"  {L['tx_label']}  ", C["up"]); t.append(f"{fmt_s(tx)}\n", C["up"])
    t.append(f"\n  {L['total_rx_label']}   {fmt_b(sd.get('rx_tot',0))}\n", C["dim"])
    t.append(f"  {L['total_tx_label']}   {fmt_b(sd.get('tx_tot',0))}\n", C["dim"])
    return t


def render_procs(app: "XrayMonitor") -> Text:
    t = Text()
    sd = app.sys_s.get()
    t.append(f" {L['top_procs_ram']}\n\n", C["accent"])
    t.append(f"  {'PID':>7}  {'NAME':<20}  {'CPU':>6}  {'RAM':>10}\n", C["dim"])
    t.append("  " + H*50 + "\n", C["dim"])
    for pid, name, cpu_p, mem in sd.get("top_procs", []):
        ns  = (name[:18]+"...") if len(name) > 19 else name
        cc  = C["warn"] if cpu_p > 50 else C["ok"] if cpu_p > 10 else C["dim"]
        mc  = C["warn"] if mem > 500_000_000 else C["dim"]
        t.append(f"  {pid:>7}  {ns:<20}  ", C["dim"])
        t.append(f"{cpu_p:>5.1f}%", cc)
        t.append(f"  {fmt_b(mem):>10}\n", mc)
    return t


def render_ping(app: "XrayMonitor") -> Text:
    t = Text()
    t.append(f" {L['sys_latency']}\n\n", C["accent"])
    for host in app._ping_hosts:
        ms = app.sys_s.ping(host)
        if ms is None:
            t.append(f"  {host:<22} ", C["dim"]); t.append("...\n", C["dim"])
        elif ms < 0:
            t.append(f"  {host:<22} ", C["dim"]); t.append(f"✗ {L['ping_fail']}\n", C["err"])
        else:
            col = C["ok"] if ms < 50 else C["warn"] if ms < 150 else C["err"]
            t.append(f"  {host:<22} ", C["dim"])
            t.append(gauge(min(ms, 300), 300, 10), col)
            t.append(f"  {ms:5.0f} ms\n", col)

    t.append(f"\n  {L['dns_check']}\n", C["accent2"])
    for dns in ["1.1.1.1", "8.8.8.8"]:
        ms = app.sys_s.ping(dns)
        t.append(f"  {dns} (DNS)  ", C["dim"])
        t.append(f"{ms:.0f} ms  OK\n" if ms and ms > 0 else "...\n",
                 C["ok"] if ms and ms > 0 else C["dim"])
    return t
