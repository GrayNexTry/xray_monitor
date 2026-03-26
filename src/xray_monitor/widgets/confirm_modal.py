"""Модальный экран подтверждения удаления пользователя."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import Static
from rich.text import Text

from ..constants import C


class DeleteConfirmScreen(ModalScreen[bool]):
    """Y/N подтверждение перед удалением пользователя из конфига."""

    DEFAULT_CSS = """
    DeleteConfirmScreen {
        align: center middle;
    }
    #del-box {
        width: 58;
        height: 12;
        border: double $error;
        background: $surface;
        padding: 1 3;
    }
    """

    BINDINGS = [
        Binding("y",      "confirm",  "", show=False),
        Binding("n",      "cancel",   "", show=False),
        Binding("escape", "cancel",   "", show=False),
    ]

    def __init__(self, email: str) -> None:
        super().__init__()
        self._email = email

    def compose(self) -> ComposeResult:
        t = Text()
        t.append("  УДАЛИТЬ ПОЛЬЗОВАТЕЛЯ\n\n", C["err"])
        t.append(f"  {self._email}\n\n", "bold")
        t.append("  Пользователь будет удалён из config.json.\n", C["dim"])
        t.append("  Xray перезапустится автоматически.\n\n", C["dim"])
        t.append("  ")
        t.append("[Y]", C["ok"])
        t.append(" Удалить     ", "bold")
        t.append("[N]", C["err"])
        t.append(" / Esc — Отмена", "bold")
        yield Static(t, id="del-box")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
