"""Форматирование, буфер обмена, QR-код."""

from __future__ import annotations

import os
import stat
import subprocess
from datetime import datetime
from typing import Any, TYPE_CHECKING

from .constants import C

if TYPE_CHECKING:
    import qrcode as _qrcode_type
    import psutil as _psutil_type

try:
    import qrcode as _qrcode  # type: ignore[import-untyped]
    HAS_QR = True
except ImportError:
    _qrcode: Any = None
    HAS_QR = False

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    psutil: Any = None
    HAS_PSUTIL = False


# ── Форматирование байт / скорости / времени ─────────────────

def fmt_b(n: int | float) -> str:
    n = int(n)
    if n < 0: return f"-{fmt_b(-n)}"
    if n == 0: return "0 B"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024: return f"{n:.1f} {u}" if u != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PB"


def fmt_s(b: float) -> str:
    b = max(0.0, float(b))
    for u in ("B/s", "KB/s", "MB/s", "GB/s"):
        if b < 1024: return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB/s"


def fmt_up(s: int | float) -> str:
    s = int(s); d, s = divmod(s, 86400); h, s = divmod(s, 3600); m, s = divmod(s, 60)
    p = []
    if d: p.append(f"{d}d")
    if h: p.append(f"{h}h")
    if m: p.append(f"{m}m")
    p.append(f"{s}s")
    return " ".join(p)


def fmt_ts(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts > 0 else "—"


# ── Sparkline / gauge / bar ──────────────────────────────────

SP = "▁▂▃▄▅▆▇█"


def spark(vals, w: int = 30) -> str:
    if not vals: return "▁" * w
    v = list(vals)[-w:]
    mn, mx = min(v), max(v)
    rng = mx - mn or 1
    return "".join(SP[min(int((x - mn) / rng * 7), 7)] for x in v)


def gauge(val: float, mx: float, w: int = 20) -> str:
    if mx <= 0: return "░" * w
    f = int(min(1.0, val / mx) * w)
    return "█" * f + "░" * (w - f)


def pct_bar(pct: float, w: int = 20) -> str:
    f = int(pct / 100 * w)
    return "".join("█" if i < f else "░" for i in range(w))


def pct_col(pct: float) -> str:
    if pct > 85: return C["err"]
    if pct > 60: return C["warn"]
    return C["ok"]


H = "─"
V = "│"


# ── QR-код ───────────────────────────────────────────────────

def qr_to_lines(data: str, border: int = 1) -> list:
    if not HAS_QR or _qrcode is None:
        return ["[pip install qrcode]"]
    import qrcode.constants  # type: ignore[import-untyped]
    qr = _qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=border,
    )
    qr.add_data(data); qr.make(fit=True)
    matrix = qr.get_matrix()
    lines = []
    for row in range(0, len(matrix), 2):
        row2 = matrix[row + 1] if row + 1 < len(matrix) else [False] * len(matrix[row])
        line = ""
        for top, bot in zip(matrix[row], row2):
            if top and bot:   line += "█"
            elif top:         line += "▀"
            elif bot:         line += "▄"
            else:             line += " "
        lines.append(line)
    return lines


# ── Буфер обмена ─────────────────────────────────────────────

def copy_to_clipboard(text: str) -> bool:
    for cmd in [["xclip", "-selection", "clipboard"],
                ["xsel", "--clipboard", "--input"],
                ["wl-copy"], ["pbcopy"]]:
        try:
            r = subprocess.run(cmd, input=text.encode(),
                               capture_output=True, timeout=3)
            if r.returncode == 0:
                return True
        except Exception:
            pass
    try:
        path = "/tmp/xray-clipboard.txt"
        with open(path, "w") as f:
            f.write(text)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return True
    except Exception:
        return False
