"""Парсер config.json Xray и построитель URL клиентов."""

from __future__ import annotations

import os
import json
import subprocess

from .crypto import derive_public_key


class XrayConfig:
    def __init__(self, path: str = "/usr/local/etc/xray/config.json") -> None:
        self.path   = path
        self._data:  dict  = {}
        self._mtime: float = 0

    def reload(self) -> dict:
        try:
            mtime = os.path.getmtime(self.path)
            if mtime != self._mtime:
                with open(self.path) as f:
                    self._data = json.load(f)
                self._mtime = mtime
        except Exception as e:
            return {"error": str(e)}
        return self._data

    def get_inbounds(self) -> list:
        return self.reload().get("inbounds", [])

    def build_client_urls(self, server_ip: str = "") -> list:
        results = []
        for ib in self.get_inbounds():
            proto = ib.get("protocol", "").lower()
            if proto not in ("vless", "vmess", "trojan", "shadowsocks"):
                continue

            port = ib.get("port", 443)
            tag  = ib.get("tag", "")
            ss   = ib.get("streamSettings", {})
            network  = ss.get("network", "tcp")
            if network == "raw": network = "tcp"
            security = ss.get("security", "none")

            transport_params = self._build_transport_params(ss, network)
            security_params, sns, sids, sni = self._build_security_params(ss, security)

            clients = ib.get("settings", {}).get("clients", [])
            if proto == "shadowsocks" and not clients:
                clients = [{}]

            for client in clients:
                url = self._build_client_url(
                    proto, client, ib, ss, server_ip, port, tag,
                    network, security, transport_params, security_params)
                if url is None:
                    continue
                results.append({
                    "email":        client.get("email", ""),
                    "uuid":         client.get("id", ""),
                    "tag":          tag,
                    "port":         port,
                    "protocol":     proto,
                    "network":      network,
                    "security":     security,
                    "flow":         client.get("flow", ""),
                    "short_ids":    sids,
                    "server_names": sns,
                    "sni":          sni,
                    "url":          url,
                })
        return results

    def _build_transport_params(self, ss: dict, network: str) -> dict:
        transport_params: dict = {"type": network}

        if network == "tcp":
            tcp = ss.get("tcpSettings", {})
            hdr = tcp.get("header", {})
            if hdr.get("type") == "http":
                req = hdr.get("request", {})
                paths = req.get("path", [])
                transport_params["path"] = paths[0] if paths else "/"
                hdrs = req.get("headers", {})
                host = (hdrs.get("Host", hdrs.get("host", [""]))[0]
                        if isinstance(hdrs.get("Host", hdrs.get("host", "")), list)
                        else hdrs.get("Host", hdrs.get("host", "")))
                if host: transport_params["host"] = host
                transport_params["headerType"] = "http"

        elif network == "ws":
            ws = ss.get("wsSettings", {})
            transport_params["path"] = ws.get("path", "/")
            hdrs = ws.get("headers", {})
            host = hdrs.get("Host", hdrs.get("host", ""))
            if host: transport_params["host"] = host

        elif network == "grpc":
            grpc_s = ss.get("grpcSettings", {})
            svc = grpc_s.get("serviceName", "")
            if svc: transport_params["serviceName"] = svc
            auth = grpc_s.get("authority", "")
            if auth: transport_params["authority"] = auth
            if grpc_s.get("multiMode"): transport_params["mode"] = "multi"

        elif network == "httpupgrade":
            hu = ss.get("httpupgradeSettings", {})
            transport_params["path"] = hu.get("path", "/")
            host = hu.get("host", "")
            if host: transport_params["host"] = host

        elif network == "xhttp":
            xh = ss.get("xhttpSettings", ss.get("splithttpSettings", {}))
            transport_params["path"] = xh.get("path", "/")
            host = xh.get("host", "")
            if host: transport_params["host"] = host
            mode = xh.get("mode", "auto")
            if mode: transport_params["mode"] = mode

        return transport_params

    def _build_security_params(self, ss: dict, security: str) -> tuple:
        security_params: dict = {}
        rs  = ss.get("realitySettings", {})
        tls = ss.get("tlsSettings", {})
        sns: list = []; sids: list = []; sni = ""

        if security == "reality":
            priv    = rs.get("privateKey", "")
            pub_key = derive_public_key(priv) if priv else ""
            fp      = (rs.get("fingerprint", "") or
                       rs.get("settings", {}).get("fingerprint", "") or "chrome")
            _sns    = rs.get("serverNames", [])
            sns     = _sns if isinstance(_sns, list) else [s.strip() for s in _sns.split(",") if s.strip()]
            _sids   = rs.get("shortIds", [])
            sids    = _sids if isinstance(_sids, list) else [s.strip() for s in _sids.split(",") if s.strip()]
            spx     = rs.get("settings", {}).get("spiderX", "") or ""
            pqv     = rs.get("settings", {}).get("mldsa65Verify", "") or ""
            sni     = sns[0] if sns else ""

            security_params["security"] = "reality"
            if pub_key:  security_params["pbk"] = pub_key
            security_params["fp"] = fp
            if sni:      security_params["sni"] = sni
            if sids:     security_params["sid"] = sids[0]
            if spx:      security_params["spx"] = spx
            if pqv:      security_params["pqv"] = pqv

        elif security in ("tls", "xtls"):
            tls_s = tls.get("settings", {})
            fp    = tls_s.get("fingerprint", "")
            alpn  = tls.get("alpn", [])
            sni   = tls.get("serverName", "")
            ech   = tls_s.get("echConfigList", [])

            security_params["security"] = "tls"
            if fp:   security_params["fp"]   = fp
            if alpn: security_params["alpn"] = ",".join(alpn) if isinstance(alpn, list) else alpn
            if sni:  security_params["sni"]  = sni
            if ech:  security_params["ech"]  = ",".join(ech) if isinstance(ech, list) else ech
        else:
            security_params["security"] = "none"

        return security_params, sns, sids, sni

    def _build_client_url(self, proto, client, ib, ss, server_ip, port, tag,
                          network, security, transport_params, security_params):
        from urllib.parse import quote

        uid      = client.get("id", "")
        email    = client.get("email", "")
        flow     = client.get("flow", "")
        password = client.get("password", "")
        ip       = server_ip or "SERVER_IP"
        remark   = email or tag or "vpn"

        if proto == "vless":
            if not uid: return None
            p = {"encryption": "none"}
            p.update(transport_params)
            p.update(security_params)
            if flow and network == "tcp":
                p["flow"] = flow
            qs = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in p.items())
            return f"vless://{uid}@{ip}:{port}?{qs}#{quote(remark, safe='')}"

        elif proto == "vmess":
            if not uid: return None
            import json as _json, base64 as _b64
            security_c = client.get("security", "auto")
            obj: dict = {
                "v": "2", "ps": remark, "add": ip, "port": str(port),
                "id": uid, "scy": security_c, "net": network,
                "tls": security if security == "tls" else "",
            }
            if network == "tcp":
                tcp = ss.get("tcpSettings", {})
                hdr = tcp.get("header", {})
                obj["type"] = hdr.get("type", "none")
                if hdr.get("type") == "http":
                    req = hdr.get("request", {})
                    paths = req.get("path", [])
                    obj["path"] = paths[0] if paths else "/"
                    hdrs = req.get("headers", {})
                    host = (hdrs.get("Host", hdrs.get("host", [""]))[0]
                            if isinstance(hdrs.get("Host", hdrs.get("host", "")), list) else "")
                    if host: obj["host"] = host
            elif network == "ws":
                ws = ss.get("wsSettings", {})
                obj["path"] = ws.get("path", "/")
                hdrs = ws.get("headers", {})
                obj["host"] = hdrs.get("Host", hdrs.get("host", ""))
            elif network == "grpc":
                obj["path"] = ss.get("grpcSettings", {}).get("serviceName", "")
                obj["authority"] = ss.get("grpcSettings", {}).get("authority", "")
                if ss.get("grpcSettings", {}).get("multiMode"): obj["type"] = "multi"
            elif network == "xhttp":
                xh = ss.get("xhttpSettings", {})
                obj["path"] = xh.get("path", "/")
                obj["host"] = xh.get("host", "")
                obj["type"] = xh.get("mode", "auto")
            if security == "tls":
                tls_cfg = ss.get("tlsSettings", {})
                sni_v = tls_cfg.get("serverName", "")
                fp_v  = tls_cfg.get("settings", {}).get("fingerprint", "")
                alpn  = tls_cfg.get("alpn", [])
                if sni_v: obj["sni"] = sni_v
                if fp_v:  obj["fp"]  = fp_v
                if alpn:  obj["alpn"] = ",".join(alpn) if isinstance(alpn, list) else alpn
            return "vmess://" + _b64.b64encode(
                _json.dumps(obj, ensure_ascii=False).encode()).decode()

        elif proto == "trojan":
            if not password: return None
            p = {}
            p.update(transport_params)
            p.update(security_params)
            qs = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in p.items())
            return f"trojan://{password}@{ip}:{port}?{qs}#{quote(remark, safe='')}"

        elif proto == "shadowsocks":
            import base64 as _b64
            cfg_s   = ib.get("settings", {})
            method  = cfg_s.get("method", "")
            ss_pass = cfg_s.get("password", "")
            if client.get("password"): ss_pass = client["password"]
            if not (method and ss_pass): return None
            userinfo = _b64.b64encode(f"{method}:{ss_pass}".encode()).decode()
            p = {}
            p.update(transport_params)
            if security == "tls":
                p.update(security_params)
            qs = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in p.items())
            url = f"ss://{userinfo}@{ip}:{port}"
            if qs: url += f"?{qs}"
            url += f"#{quote(remark, safe='')}"
            return url

        return None

    def delete_client(self, email: str) -> tuple[bool, str]:
        """Удаляет клиента по email из всех inbound-ов. Создаёт бэкап."""
        import shutil
        from datetime import datetime as _dt
        try:
            data = self.reload()
            if "error" in data:
                return False, f"Ошибка чтения конфига: {data['error']}"

            # xray хранит email как "user", stats показывает "user@tag" —
            # матчим оба варианта
            short = email.split("@")[0] if "@" in email else email

            found = False
            for ib in data.get("inbounds", []):
                settings = ib.get("settings") or {}
                clients = settings.get("clients") or []
                before = len(clients)
                settings["clients"] = [
                    c for c in clients
                    if c.get("email") not in (email, short)
                ]
                if len(settings["clients"]) < before:
                    found = True

            if not found:
                return False, f"Пользователь '{email}' не найден в конфиге"

            # Формат бэкапа .bak_YYYYMMDD_HHMMSS — совпадает с app._backup_config()
            ts = _dt.now().strftime("%Y%m%d_%H%M%S")
            bak = self.path + f".bak_{ts}"
            shutil.copy2(self.path, bak)

            with open(self.path, "w") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._mtime = 0  # сброс кэша

            return True, f"Удалён '{email}'. Бэкап: {bak}"
        except Exception as e:
            return False, str(e)

    def check_syntax(self) -> tuple:
        from .xray_manager import find_xray_binary
        xray_bin = find_xray_binary()
        if not xray_bin:
            return None, "xray binary not found"
        try:
            r = subprocess.run([xray_bin, "run", "-test", "-c", self.path],
                               capture_output=True, text=True, timeout=10)
            return r.returncode == 0, (r.stdout + r.stderr).strip()
        except Exception as e:
            return None, str(e)
