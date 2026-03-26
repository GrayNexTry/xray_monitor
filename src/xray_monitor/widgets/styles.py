"""CSS стили TUI приложения."""

CSS = """
Screen { background: $surface; }

/* Дашборд */
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

/* Вкладка ключей */
#keys-layout  { height: 1fr; padding: 0 1; }
#keys-left    { width: 1fr; height: 1fr; border: tall $accent;   padding: 0 1; overflow-y: auto; }
#keys-right   { width: 1fr; height: 1fr; border: tall $primary;  padding: 0 1; overflow-y: auto; margin-left: 1; }
#inp-server   { width: 1fr; }

/* Вкладка системы */
#sys-tab    { height: 1fr; padding: 0 1; }
#sys-top    { height: auto; max-height: 10; }
#sys-cpuram { width: 1fr; border: tall $accent;    padding: 0 1; }
#sys-disk   { width: 1fr; border: tall $primary;   padding: 0 1; margin-left: 1; }
#sys-net    { width: 1fr; border: tall $secondary; padding: 0 1; margin-left: 1; }
#sys-bottom { height: 1fr; }
#sys-procs  { width: 1fr; border: tall $primary;   padding: 0 1; overflow-y: auto; }
#sys-ping   { width: 38;  border: tall $accent;    padding: 0 1; margin-left: 1; }
SysCpuRam  { height: 1fr; }
SysDisk    { height: 1fr; }
SysNet     { height: 1fr; }
SysProcs   { height: 1fr; }
SysPing    { height: 1fr; }

/* Лог / Подключения / Управление */
LogW  { height: auto; padding: 0 1; }
ConnW { height: auto; padding: 0 1; }
MgmtW { height: auto; padding: 0 1; }

#log-scroll  { height: 1fr; border: tall $primary; }
#conn-scroll { height: 1fr; border: tall $primary; }
#mgmt-scroll { height: 1fr; border: tall $accent;  }

#log-scroll  > .vertical-scrollbar  { width: 1; }
#conn-scroll > .vertical-scrollbar  { width: 1; }
#mgmt-scroll > .vertical-scrollbar  { width: 1; }

/* IP Радар (вкладка 7) */
#ip-radar-tab   { height: 1fr; padding: 0 1; }
#ip-sort-bar    { height: 1; padding: 0 1; }
IPSortBar       { height: 1; }
IPTableW        { height: 3fr; border: tall $accent; }
#ip-detail-scroll { height: 2fr; border: tall $primary; margin-top: 1; }
IPDetailW       { height: auto; padding: 0 1; }

/* QR-модальное окно */
QRModal    { align: center middle; }
#qr-box    { width: auto; height: auto; max-width: 80; max-height: 50;
             border: double $accent; padding: 1 2; background: $surface; }

/* Строки состояния и подсказок */
#status    { dock: bottom; height: 1; background: $boost;    color: $text;    padding: 0 1; }
HintsBar   { dock: bottom; height: 1; background: $surface-darken-1; color: $text-muted; padding: 0 1; }

TabbedContent { height: 1fr; }
TabPane       { height: 1fr; padding: 0; }
"""
