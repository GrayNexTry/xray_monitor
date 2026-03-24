"""TUI widgets, CSS, and QR modal."""

from textual.widgets import Static
from textual.screen import ModalScreen
from textual.binding import Binding
from rich.text import Text

from .constants import C
from .utils import qr_to_lines

# ── CSS ─────────────────────────────────────────────────────

CSS = """
Screen { background: $surface; }

/* Dashboard */
#dash      { height: 1fr; padding: 0 1; }
#dash-cols { height: 1fr; }
#dash-left { width: 2fr; height: 1fr; }
#dash-right{ width: 1fr; height: 1fr; margin-left: 1; }
#top-row   { height: auto; max-height: 14; }
#ov-box    { height: auto; border: tall $accent; padding: 0 1; width: 1fr; }
#sys-box   { width: 30; border: tall $secondary; padding: 0 1; }
TrafficW   { height: 1fr; border: tall $primary; padding: 0 1; overflow-y: auto; }
UsersW     { height: 1fr; border: tall $primary; padding: 0 1; overflow-y: auto; }
#filter-bar   { height: 3; padding: 0 1; display: none; }
#filter-input { width: 1fr; }

/* Keys tab */
#keys-layout  { height: 1fr; padding: 0 1; }
#keys-left    { width: 1fr; height: 1fr; border: tall $accent; padding: 0 1; overflow-y: auto; }
#keys-right   { width: 1fr; height: 1fr; border: tall $primary; padding: 0 1; overflow-y: auto; margin-left: 1; }
#inp-server   { width: 1fr; }

/* System tab */
#sys-tab    { height: 1fr; padding: 0 1; }
#sys-top    { height: auto; max-height: 10; }
#sys-cpuram { width: 1fr; border: tall $accent;     padding: 0 1; }
#sys-disk   { width: 1fr; border: tall $primary;    padding: 0 1; margin-left: 1; }
#sys-net    { width: 1fr; border: tall $secondary;  padding: 0 1; margin-left: 1; }
#sys-bottom { height: 1fr; }
#sys-procs  { width: 1fr; border: tall $primary;    padding: 0 1; overflow-y: auto; }
#sys-ping   { width: 38;  border: tall $accent;     padding: 0 1; margin-left: 1; }
SysCpuRam  { height: 1fr; }
SysDisk    { height: 1fr; }
SysNet     { height: 1fr; }
SysProcs   { height: 1fr; }
SysPing    { height: 1fr; }

/* Log / Conn / Mgmt */
LogW       { height: 1fr; padding: 0 1; border: tall $primary; overflow-y: auto; }
ConnW      { height: 1fr; padding: 0 1; border: tall $primary; overflow-y: auto; }
MgmtW      { height: 1fr; padding: 0 1; border: tall $accent; overflow-y: auto; }
#log-wrap  { height: 1fr; }
#conn-wrap { height: 1fr; }
#mgmt-wrap { height: 1fr; }

/* QR modal */
QRModal    { align: center middle; }
#qr-box    { width: auto; height: auto; max-width: 80; max-height: 50;
             border: double $accent; padding: 1 2; background: $surface; }

/* Status bar */
#status    { dock: bottom; height: 1; background: $boost; color: $text; padding: 0 1; }

TabbedContent { height: 1fr; }
TabPane       { height: 1fr; padding: 0; }
"""


# ── Widget stubs ────────────────────────────────────────────

class OvBox(Static):     pass
class SysBox(Static):    pass
class TrafficW(Static):  pass
class UsersW(Static):    pass
class KeysLeft(Static):  pass
class KeysRight(Static): pass
class SysCpuRam(Static): pass
class SysDisk(Static):   pass
class SysNet(Static):    pass
class SysProcs(Static):  pass
class SysPing(Static):   pass
class LogW(Static):      pass
class ConnW(Static):     pass
class MgmtW(Static):     pass
class StatusBar(Static): pass


# ── QR Modal ────────────────────────────────────────────────

class QRModal(ModalScreen):
    BINDINGS = [Binding("escape", "dismiss", "Close"),
                Binding("q", "dismiss", "Close")]

    def __init__(self, url: str, title: str = "VLESS URL"):
        super().__init__()
        self.url = url
        self.qr_title = title

    def compose(self):
        lines = qr_to_lines(self.url, border=2)
        t = Text()
        t.append(f"  {self.qr_title}\n\n", "bold")
        for line in lines:
            t.append("  " + line + "\n", "white on black")
        t.append(f"\n  {self.url[:72]}{'...' if len(self.url) > 72 else ''}\n", C["dim"])
        t.append("\n  [Esc] close", "dim italic")
        yield Static(t, id="qr-box")
