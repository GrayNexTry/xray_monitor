"""Модальный экран подтверждения удаления пользователя."""

from textual.app import ComposeResult
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

    def __init__(self, label: str) -> None:
        super().__init__()
        self._label = label

    def compose(self) -> ComposeResult:
        t = Text()
        t.append("  УДАЛИТЬ\n\n", C["err"])
        t.append(f"  {self._label}\n\n", "bold")
        t.append("  Действие необратимо.\n\n", C["dim"])
        t.append("  ")
        t.append("[Y]", C["ok"])
        t.append(" Да, удалить     ", "bold")
        t.append("[N]", C["err"])
        t.append(" / Esc — Отмена", "bold")
        yield Static(t, id="del-box")

    def on_key(self, event) -> None:
        if event.key in ("y", "Y"):
            event.stop()
            self.dismiss(True)
        elif event.key in ("n", "N", "escape"):
            event.stop()
            self.dismiss(False)
