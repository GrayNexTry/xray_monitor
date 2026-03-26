"""Управление xray-core: обновление, перезапуск, старт, стоп, горячая перезагрузка."""

from __future__ import annotations

import logging
import os
import re
import signal
import tempfile
import time
import json
import shutil
import platform
import subprocess
import threading
from typing import Optional, Tuple, Callable
from urllib.request import urlopen, Request

log = logging.getLogger(__name__)

_GITHUB_API  = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
_XRAY_BINS   = ["/usr/local/bin/xray", "/usr/bin/xray"]
_XRAY_SERVICE = "xray"

_version_cache: dict = {}
_VERSION_TTL = 300   # 5 минут


def find_xray_binary() -> Optional[str]:
    for path in _XRAY_BINS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    # which (Linux/macOS) или where (Windows)
    which_cmd = "where" if os.name == "nt" else "which"
    try:
        r = subprocess.run([which_cmd, "xray"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().splitlines()[0]
    except Exception:
        pass
    return None


def get_installed_version() -> Optional[str]:
    xray = find_xray_binary()
    if not xray:
        return None
    try:
        r = subprocess.run([xray, "version"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            m = re.search(r"Xray\s+(\d+\.\d+\.\d+)", r.stdout)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def get_latest_version() -> Tuple[Optional[str], Optional[str]]:
    """Возвращает (version, download_url) из GitHub."""
    now = time.monotonic()
    cached = _version_cache.get("latest")
    if cached and now - cached[0] < _VERSION_TTL:
        return cached[1], cached[2]

    try:
        req  = Request(_GITHUB_API, headers={"User-Agent": "xray-monitor"})
        raw  = urlopen(req, timeout=10).read()
        data = json.loads(raw)
        tag  = data.get("tag_name", "").lstrip("v")

        arch = platform.machine().lower()
        arch_map = {
            "x86_64": "64", "amd64": "64",
            "aarch64": "arm64-v8a", "arm64": "arm64-v8a",
            "armv7l": "arm32-v7a",
            "i686": "32", "i386": "32",
        }
        xray_arch = arch_map.get(arch, "64")

        download_url = None
        for asset in data.get("assets", []):
            name = asset.get("name", "").lower()
            if f"linux-{xray_arch}" in name and name.endswith(".zip"):
                download_url = asset.get("browser_download_url")
                break

        _version_cache["latest"] = (now, tag, download_url)
        return tag, download_url
    except Exception:
        return None, None


def get_xray_status() -> dict:
    result: dict = {
        "running": False,
        "enabled": False,
        "pid":     None,
        "uptime":  None,
        "memory":  None,
        "version": get_installed_version(),
    }

    try:
        r = subprocess.run(["systemctl", "is-active", _XRAY_SERVICE],
                           capture_output=True, text=True, timeout=5)
        result["running"] = r.stdout.strip() == "active"
    except Exception:
        pass

    try:
        r = subprocess.run(["systemctl", "is-enabled", _XRAY_SERVICE],
                           capture_output=True, text=True, timeout=5)
        result["enabled"] = r.stdout.strip() == "enabled"
    except Exception:
        pass

    if result["running"]:
        try:
            r = subprocess.run(
                ["systemctl", "show", _XRAY_SERVICE,
                 "--property=MainPID,ActiveEnterTimestamp,MemoryCurrent"],
                capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                if line.startswith("MainPID="):
                    pid = line.split("=", 1)[1].strip()
                    if pid and pid != "0":
                        result["pid"] = int(pid)
                elif line.startswith("MemoryCurrent="):
                    mem = line.split("=", 1)[1].strip()
                    if mem.isdigit():
                        result["memory"] = int(mem)
        except Exception:
            pass

    return result


def start_xray() -> Tuple[bool, str]:
    try:
        r = subprocess.run(["systemctl", "start", _XRAY_SERVICE],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return True, "Xray запущен"
        return False, (r.stderr or r.stdout).strip()[:200]
    except Exception as e:
        return False, str(e)


def stop_xray() -> Tuple[bool, str]:
    try:
        r = subprocess.run(["systemctl", "stop", _XRAY_SERVICE],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return True, "Xray остановлен"
        return False, (r.stderr or r.stdout).strip()[:200]
    except Exception as e:
        return False, str(e)


def restart_xray() -> Tuple[bool, str]:
    try:
        r = subprocess.run(["systemctl", "restart", _XRAY_SERVICE],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return True, "Xray перезапущен"
        return False, (r.stderr or r.stdout).strip()[:200]
    except Exception as e:
        return False, str(e)


def reload_xray() -> Tuple[bool, str]:
    """Горячая перезагрузка конфига xray без обрыва текущих сессий.

    Порядок попыток:
      1. systemctl reload xray  (если ExecReload задан в unit-файле)
      2. kill -SIGHUP <pid>     (xray перечитывает конфиг, не закрывая порты)
    """
    # Попытка 1: systemctl reload
    try:
        r = subprocess.run(
            ["systemctl", "reload", _XRAY_SERVICE],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return True, "Горячая перезагрузка (systemctl reload)"
    except Exception:
        pass

    # Попытка 2: прямой SIGHUP к процессу
    status = get_xray_status()
    pid = status.get("pid")
    if not pid:
        return False, "Xray не запущен (PID не найден)"
    try:
        os.kill(pid, signal.SIGHUP)
        return True, f"SIGHUP → PID {pid} (сессии не прерваны)"
    except PermissionError:
        return False, f"Нет прав на kill PID {pid} — нужен sudo"
    except Exception as e:
        return False, str(e)


def enable_xray() -> Tuple[bool, str]:
    try:
        r = subprocess.run(["systemctl", "enable", _XRAY_SERVICE],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return True, "Автозапуск Xray включён"
        return False, (r.stderr or r.stdout).strip()[:200]
    except Exception as e:
        return False, str(e)


def disable_xray() -> Tuple[bool, str]:
    try:
        r = subprocess.run(["systemctl", "disable", _XRAY_SERVICE],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            return True, "Автозапуск Xray выключен"
        return False, (r.stderr or r.stdout).strip()[:200]
    except Exception as e:
        return False, str(e)


def update_xray_core(callback: Optional[Callable] = None) -> Tuple[bool, str]:
    """
    Скачивает и устанавливает последнюю версию xray-core.
    callback(stage, message) вызывается с прогрессом.
    """
    def _cb(stage: str, msg: str) -> None:
        if callback:
            callback(stage, msg)

    _cb("check", "Проверка последней версии...")
    current = get_installed_version()
    latest, url = get_latest_version()

    if not latest:
        return False, "Не удалось получить последнюю версию с GitHub"
    if not url:
        return False, f"Нет URL для архитектуры: {platform.machine()}"
    if current and current == latest:
        return True, f"Уже установлена последняя версия: v{latest}"

    _cb("download", f"Скачивание v{latest}...")

    xray_bin = find_xray_binary() or "/usr/local/bin/xray"
    # Кроссплатформенный temp dir вместо захардкоженного /tmp
    tmp_dir  = os.path.join(tempfile.gettempdir(), "xray-update")

    try:
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
        os.makedirs(tmp_dir, exist_ok=True)

        zip_path = os.path.join(tmp_dir, "xray.zip")
        req  = Request(url, headers={"User-Agent": "xray-monitor"})
        data = urlopen(req, timeout=60).read()
        with open(zip_path, "wb") as f:
            f.write(data)

        _cb("extract", "Распаковка...")
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_dir)

        new_bin = os.path.join(tmp_dir, "xray")
        if not os.path.isfile(new_bin):
            return False, "xray binary не найден в архиве"

        os.chmod(new_bin, 0o755)
        r = subprocess.run([new_bin, "version"], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return False, f"Новый binary не прошёл проверку: {r.stderr}"

        _cb("install", "Установка...")

        was_running = get_xray_status()["running"]
        if was_running:
            stop_xray()
            time.sleep(1)

        if os.path.isfile(xray_bin):
            shutil.copy2(xray_bin, f"{xray_bin}.bak")

        shutil.copy2(new_bin, xray_bin)
        os.chmod(xray_bin, 0o755)

        for geofile in ["geoip.dat", "geosite.dat"]:
            src = os.path.join(tmp_dir, geofile)
            if os.path.isfile(src):
                dst_dir = os.path.dirname(xray_bin)
                for dest in [
                    os.path.join(dst_dir, geofile),
                    f"/usr/local/share/xray/{geofile}",
                    f"/usr/share/xray/{geofile}",
                ]:
                    if os.path.isdir(os.path.dirname(dest)):
                        shutil.copy2(src, dest)
                        break

        if was_running:
            _cb("restart", "Перезапуск xray...")
            time.sleep(1)
            start_xray()

        shutil.rmtree(tmp_dir, ignore_errors=True)
        _version_cache.clear()

        new_ver = get_installed_version() or latest
        msg = f"Обновлено: v{current or '?'} -> v{new_ver}"
        _cb("done", msg)
        return True, msg

    except Exception as e:
        log.exception("xray update failed")
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, f"Ошибка обновления: {e}"


def update_xray_async(callback: Optional[Callable] = None,
                      done_callback: Optional[Callable] = None) -> threading.Thread:
    """Запускает обновление в фоновом потоке."""
    def _run() -> None:
        ok, msg = update_xray_core(callback=callback)
        if done_callback:
            done_callback(ok, msg)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
