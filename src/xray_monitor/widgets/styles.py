"""CSS стили TUI приложения."""

CSS = """
Screen { background: $surface; }

TabbedContent { height: 1fr; }
TabPane       { height: 1fr; padding: 0; }

/* ── Дашборд ─────────────────────────────────────── */
#dash      { height: 1fr; padding: 0 1; }
#dash-cols { height: 1fr; }
#dash-left { width: 2fr; height: 1fr; }
#dash-right{ width: 1fr; height: 1fr; margin-left: 1; }
#top-row   { height: auto; max-height: 18; }
#ov-box    { height: 1fr; border: round $panel; padding: 0 1; width: 1fr; }
#sys-box   { height: 1fr; width: 36; border: round $panel; padding: 0 1; }
TrafficW   { height: 1fr; border: round $panel; padding: 0 1; overflow-y: auto; }
UsersW     { height: 1fr; border: round $panel; padding: 0 1; overflow-y: auto; }
#filter-bar   { height: 3; padding: 0 1; display: none; }
#filter-input { width: 1fr; }

/* ── Ключи ────────────────────────────────────────── */
#keys-layout  { height: 1fr; padding: 0 1; }
#keys-left    { width: 1fr; height: 1fr; border: round $panel; padding: 0 1; overflow-y: auto; }
#keys-right   { width: 1fr; height: 1fr; border: round $panel; padding: 0 1; overflow-y: auto; margin-left: 1; }
#inp-server   { width: 1fr; }

/* ── Система ──────────────────────────────────────── */
#sys-tab    { height: 1fr; padding: 0 1; }
#sys-top    { height: auto; max-height: 10; }
#sys-cpuram { width: 1fr; border: round $panel; padding: 0 1; }
#sys-disk   { width: 1fr; border: round $panel; padding: 0 1; margin-left: 1; }
#sys-net    { width: 1fr; border: round $panel; padding: 0 1; margin-left: 1; }
#sys-bottom { height: 1fr; }
#sys-procs  { width: 1fr; height: 1fr; border: round $panel; padding: 0 1; overflow-y: auto; }
#sys-ping   { width: 38;  height: 1fr; border: round $panel; padding: 0 1; margin-left: 1; }
SysCpuRam   { height: 1fr; }
SysDisk     { height: 1fr; }
SysNet      { height: 1fr; }
SysProcs    { height: 1fr; }
SysPing     { height: 1fr; }

/* ── Лог / Подключения / Управление ──────────────── */
LogW  { height: auto; padding: 0 1; }
ConnW { height: auto; padding: 0 1; }
MgmtW     { height: auto; padding: 0 1; }
MgmtKeysW { height: auto; padding: 0 1; }

#log-scroll  { height: 1fr; border: round $panel; }
#conn-scroll { height: 1fr; border: round $panel; }
#mgmt-layout { height: 1fr; padding: 0 1; }
#mgmt-scroll { height: 1fr; width: 2fr; border: round $panel; }
#mgmt-keys-scroll { height: 1fr; width: 1fr; border: round $panel; margin-left: 1; }

#log-scroll  > .vertical-scrollbar  { width: 1; }
#conn-scroll > .vertical-scrollbar  { width: 1; }
#mgmt-scroll > .vertical-scrollbar  { width: 1; }
#mgmt-keys-scroll > .vertical-scrollbar { width: 1; }

/* ── IP Радар ─────────────────────────────────────── */
#ip-radar-tab     { height: 1fr; padding: 0 1; }
#ip-sort-bar      { height: 1; padding: 0 1; }
IPSortBar         { height: 1; }
IPTableW          { height: 3fr; border: round $panel; }
#ip-detail-scroll { height: 2fr; border: round $panel; margin-top: 1; }
IPDetailW         { height: auto; padding: 0 1; }

/* ── QR-модальное окно ────────────────────────────── */
QRModal { align: center middle; }
#qr-box { width: auto; height: auto; max-width: 80; max-height: 50;
          border: round $accent; padding: 1 2; background: $surface; }

/* ── Статус-бар ───────────────────────────────────── */
#status { dock: bottom; height: 1; background: $boost; color: $text; padding: 0 1; }
"""
