"""Виджеты TUI (Static-подклассы + DataTable для IP Радара)."""

from textual.widgets import Static, DataTable


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
class IPSortBar(Static): pass
class IPDetailW(Static): pass


class IPTableW(DataTable):
    """Интерактивная таблица IP-адресов с сортировкой.

    Колонки: статус, пользователь, IP, последний раз,
             загружено, отдано, топ-сервис, страна.
    """

    def on_mount(self) -> None:
        self.cursor_type  = "row"
        self.zebra_stripes = True
        self.add_column("●",              width=2,  key="status")
        self.add_column("Пользователь",   width=21, key="email")
        self.add_column("IP",             width=17, key="ip")
        self.add_column("Последний раз",  width=15, key="last_seen")
        self.add_column("↓ Загрузка",     width=11, key="dn")
        self.add_column("↑ Отдача",       width=10, key="up")
        self.add_column("Сервис",         width=14, key="service")
        self.add_column("Страна",         width=18, key="country")

    def rebuild(self, rows: list, keep_key: str = "") -> None:
        """Пересобирает таблицу, сохраняя позицию курсора по ключу (IP)."""
        self.clear()
        for r in rows:
            self.add_row(*r["cells"], key=r["key"])
        if keep_key and self.row_count > 0:
            try:
                idx = self.get_row_index(keep_key)
                self.move_cursor(row=idx, animate=False)
            except Exception:
                pass
