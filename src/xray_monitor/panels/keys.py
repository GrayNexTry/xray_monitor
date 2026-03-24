"""Панель управления ключами клиентов."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

from rich.text import Text

from ..constants import C, L
from ..utils import H

if TYPE_CHECKING:
    from ..app import XrayMonitor


def render_keys_left(app: "XrayMonitor") -> Text:
    """Возвращает Text для левой панели ключей. Побочный эффект: устанавливает app._qr_url."""
    server_ip = app._get_server_ip()
    clients   = app.cfg.build_client_urls(server_ip)

    t = Text()
    t.append(f" {L['access_keys_title']}\n\n", C["accent"])

    if not clients:
        t.append(f"  {L['no_clients']}\n", C["dim"])
        t.append(L["path_label"] + f" {app.cfg.path}\n", C["accent2"])
    else:
        for idx, cl in enumerate(clients):
            is_first = idx == 0
            email    = cl["email"] or L["no_email"]
            uid      = cl["uuid"]
            tag      = cl["tag"]
            port     = cl["port"]
            net      = cl["network"]
            sec      = cl["security"]
            flow     = cl["flow"]
            sns      = cl["server_names"]
            sids     = cl["short_ids"]
            url      = cl["url"]

            t.append(f"  [{idx+1}]  ", C["accent2"])
            t.append(f"{email}\n", "bold")
            t.append(f"       UUID     ", C["dim"])
            t.append(f"{uid}\n", C["accent3"])
            t.append(f"       tag      ", C["dim"])
            t.append(f"{tag}  :{port}", C["dim"])
            t.append(f"  {net}+{sec}", C["dim"])
            if flow: t.append(f"  {flow}", C["dim"])
            t.append("\n")
            if sns:
                t.append(f"       SNI      ", C["dim"])
                t.append(f"{', '.join(sns[:3])}\n", C["accent"])
            if sids:
                t.append(f"       shortIDs ", C["dim"])
                t.append(f"{', '.join(sids[:3])}\n", C["accent"])

            if url and server_ip:
                t.append("\nURL\n", C["dim"])
                for i in range(0, len(url), 64):
                    t.append(f"       {url[i:i+64]}\n", C["accent3"])
                if is_first:
                    app._qr_url = url
            elif not server_ip:
                t.append(f"\n{L['enter_server_ip_url']}\n", C["warn"])

            if idx < len(clients) - 1:
                t.append("\n" + H*50 + "\n\n", C["dim"])

    if server_ip:
        t.append(f"\n  {L['qr_first_client']}\n", C["dim"])
    t.append(f"  {L['edit_config_hint']}\n", C["dim"])
    t.append(f"  {L['check_rollback']}\n", C["dim"])

    baks = app._get_backups()
    if baks:
        last = os.path.basename(baks[-1])
        t.append(f"  Last backup: {last}  ({len(baks)} total)\n", C["dim"])
    return t


def render_keys_right(app: "XrayMonitor") -> Text:
    """Возвращает Text для правой панели ключей (конфигурация inbound)."""
    t = Text()
    t.append(f" {L['inbound_config']}\n\n", C["accent"])
    try:
        for ib in app.cfg.get_inbounds():
            if ib.get("protocol") not in ("vless", "vmess"): continue
            ss   = ib.get("streamSettings", {})
            port = ib.get("port", "?")
            tag  = ib.get("tag", "")
            t.append(f"  {tag}  :{port}\n\n", C["accent2"])
            snippet = json.dumps(ss, indent=2, ensure_ascii=False)
            KEY_HI  = ("privateKey", "shortIds", "serverNames", "network", "security")
            for line in snippet.splitlines():
                hi = any(f'"{k}"' in line for k in KEY_HI)
                t.append(line + "\n", C["warn"] if hi else C["dim"])
            break
    except Exception as e:
        t.append(L["error_lower"].format(err=e) + "\n", C["dim"])
    return t
