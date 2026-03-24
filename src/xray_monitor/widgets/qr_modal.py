"""QR-модальное окно."""

from textual.screen  import ModalScreen
from textual.binding import Binding
from textual.widgets import Static
from rich.text import Text

from ..constants import C
from ..utils import qr_to_lines


class QRModal(ModalScreen):
    BINDINGS = [
        Binding("escape", "dismiss", "Закрыть"),
        Binding("q",      "dismiss", "Закрыть"),
    ]

    def __init__(self, url: str, title: str = "VLESS URL") -> None:
        super().__init__()
        self.url      = url
        self.qr_title = title

    def compose(self):
        lines = qr_to_lines(self.url, border=2)
        t = Text()
        t.append(f"  {self.qr_title}\n\n", "bold")
        for line in lines:
            t.append("  " + line + "\n", "white on black")
        t.append(
            f"\n  {self.url[:72]}{'...' if len(self.url) > 72 else ''}\n",
            C["dim"],
        )
        t.append("\n  [Esc] закрыть", "dim italic")
        yield Static(t, id="qr-box")
