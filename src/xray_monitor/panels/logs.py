"""Панель лога доступа Xray."""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text

from ..constants import C, L
from ..utils import gauge, H

if TYPE_CHECKING:
    from ..App import XrayMonitor


def render_log(app: "XrayMonitor") -> Text:
    t = Text()
    blk_s    = app.log_tail._block_session
    blk_rate = app.log_tail.block_per_min()
    top      = app.log_tail.top_blocked(10)

    t.append(f" {L['log_title']}", C["accent"])
    if blk_s > 0:
        t.append(f"   {L['log_blocked']} ", C["dim"])
        t.append(f"{blk_s}", C["err"])
        t.append(f" {L['session']}", C["dim"])
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
            col   = C["dim"] if target.startswith("[udp]") or target.startswith("[ip]") else C["err"]
            t.append(f"  {short:<45}", col)
            t.append(f" {cnt:>8}", C["err"])
            t.append(f" {pct:>5.1f}%", C["warn"])
            t.append(f"  {bar}\n", col)
        t.append("\n")

    t.append("  " + H*120 + "\n", C["dim"])
    lines = app.log_tail.read()
    if not lines:
        t.append(f"  {L['log_empty']}: {app.log_tail.path}\n", C["dim"])
    else:
        for line in lines:
            ll  = line.lower()
            col = (C["err"] if "-> block" in ll or "->block" in ll else
                   C["up"]  if "accepted" in ll else
                   C["dim"])
            t.append(f"  {line[:130]}\n", col)
    return t
