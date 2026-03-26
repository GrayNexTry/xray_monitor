"""Экспорт виджетов."""

from .styles import CSS
from .components import (
    OvBox, SysBox, TrafficW, UsersW,
    KeysLeft, KeysRight,
    SysCpuRam, SysDisk, SysNet, SysProcs, SysPing,
    LogW, ConnW, MgmtW, MgmtKeysW, StatusBar,
    IPTableW, IPDetailW, IPSortBar,
)
from .qr_modal import QRModal
from .confirm_modal import DeleteConfirmScreen

__all__ = [
    "CSS",
    "OvBox", "SysBox", "TrafficW", "UsersW",
    "KeysLeft", "KeysRight",
    "SysCpuRam", "SysDisk", "SysNet", "SysProcs", "SysPing",
    "LogW", "ConnW", "MgmtW", "MgmtKeysW", "StatusBar",
    "IPTableW", "IPDetailW", "IPSortBar",
    "QRModal",
    "DeleteConfirmScreen",
]
