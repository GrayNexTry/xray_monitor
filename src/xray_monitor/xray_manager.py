"""Xray-core management: update, restart, stop, start, status."""

import os
import re
import time
import json
import shutil
import platform
import subprocess
import threading
from typing import Optional, Tuple, Callable
from urllib.request import urlopen, Request

_GITHUB_API = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
_XRAY_BINS = ["/usr/local/bin/xray", "/usr/bin/xray"]
_XRAY_SERVICE = "xray"

# Cache for version checks (avoid hammering GitHub API)
_version_cache: dict = {}
_VERSION_TTL = 300  # 5 minutes


def find_xray_binary() -> Optional[str]:
    """Find the xray binary path."""
    for path in _XRAY_BINS:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    # Try PATH
    try:
        r = subprocess.run(["which", "xray"], capture_output=True, text=True, timeout=3)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    return None


def get_installed_version() -> Optional[str]:
    """Get currently installed xray version."""
    xray = find_xray_binary()
    if not xray:
        return None
    try:
        r = subprocess.run([xray, "version"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            # Parse "Xray 1.8.24 (Xray, Penetrates Everything.) ..."
            m = re.search(r"Xray\s+(\d+\.\d+\.\d+)", r.stdout)
            if m:
                return m.group(1)
    except Exception:
        pass
    return None


def get_latest_version() -> Tuple[Optional[str], Optional[str]]:
    """Get latest xray-core version from GitHub. Returns (version, download_url)."""
    now = time.monotonic()
    cached = _version_cache.get("latest")
    if cached and now - cached[0] < _VERSION_TTL:
        return cached[1], cached[2]

    try:
        req = Request(_GITHUB_API, headers={"User-Agent": "xray-monitor"})
        raw = urlopen(req, timeout=10).read()
        data = json.loads(raw)
        tag = data.get("tag_name", "").lstrip("v")

        # Detect architecture
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
    """Get xray service status."""
    result = {
        "running": False,
        "enabled": False,
        "pid": None,
        "uptime": None,
        "memory": None,
        "version": get_installed_version(),
    }

    try:
        r = subprocess.run(
            ["systemctl", "is-active", _XRAY_SERVICE],
            capture_output=True, text=True, timeout=5
        )
        result["running"] = r.stdout.strip() == "active"
    except Exception:
        pass

    try:
        r = subprocess.run(
            ["systemctl", "is-enabled", _XRAY_SERVICE],
            capture_output=True, text=True, timeout=5
        )
        result["enabled"] = r.stdout.strip() == "enabled"
    except Exception:
        pass

    if result["running"]:
        try:
            r = subprocess.run(
                ["systemctl", "show", _XRAY_SERVICE,
                 "--property=MainPID,ActiveEnterTimestamp,MemoryCurrent"],
                capture_output=True, text=True, timeout=5
            )
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
    """Start xray service."""
    try:
        r = subprocess.run(
            ["systemctl", "start", _XRAY_SERVICE],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            return True, "Xray started"
        return False, (r.stderr or r.stdout).strip()[:200]
    except Exception as e:
        return False, str(e)


def stop_xray() -> Tuple[bool, str]:
    """Stop xray service."""
    try:
        r = subprocess.run(
            ["systemctl", "stop", _XRAY_SERVICE],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            return True, "Xray stopped"
        return False, (r.stderr or r.stdout).strip()[:200]
    except Exception as e:
        return False, str(e)


def restart_xray() -> Tuple[bool, str]:
    """Restart xray service."""
    try:
        r = subprocess.run(
            ["systemctl", "restart", _XRAY_SERVICE],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            return True, "Xray restarted"
        return False, (r.stderr or r.stdout).strip()[:200]
    except Exception as e:
        return False, str(e)


def enable_xray() -> Tuple[bool, str]:
    """Enable xray service to start on boot."""
    try:
        r = subprocess.run(
            ["systemctl", "enable", _XRAY_SERVICE],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            return True, "Xray enabled"
        return False, (r.stderr or r.stdout).strip()[:200]
    except Exception as e:
        return False, str(e)


def disable_xray() -> Tuple[bool, str]:
    """Disable xray service from starting on boot."""
    try:
        r = subprocess.run(
            ["systemctl", "disable", _XRAY_SERVICE],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            return True, "Xray disabled"
        return False, (r.stderr or r.stdout).strip()[:200]
    except Exception as e:
        return False, str(e)


def update_xray_core(callback: Optional[Callable] = None) -> Tuple[bool, str]:
    """
    Download and install latest xray-core.
    callback(stage, message) is called with progress updates.
    Returns (success, message).
    """
    def _cb(stage, msg):
        if callback:
            callback(stage, msg)

    _cb("check", "Checking latest version...")
    current = get_installed_version()
    latest, url = get_latest_version()

    if not latest:
        return False, "Failed to fetch latest version from GitHub"

    if not url:
        return False, f"No download URL for architecture: {platform.machine()}"

    if current and current == latest:
        return True, f"Already on latest version: v{latest}"

    _cb("download", f"Downloading v{latest}...")

    xray_bin = find_xray_binary() or "/usr/local/bin/xray"
    tmp_dir = "/tmp/xray-update"

    try:
        # Clean up previous attempts
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)
        os.makedirs(tmp_dir, exist_ok=True)

        zip_path = os.path.join(tmp_dir, "xray.zip")

        # Download
        req = Request(url, headers={"User-Agent": "xray-monitor"})
        data = urlopen(req, timeout=60).read()
        with open(zip_path, "wb") as f:
            f.write(data)

        _cb("extract", "Extracting...")

        # Extract
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp_dir)

        new_bin = os.path.join(tmp_dir, "xray")
        if not os.path.isfile(new_bin):
            return False, "xray binary not found in archive"

        # Verify the new binary works
        os.chmod(new_bin, 0o755)
        r = subprocess.run([new_bin, "version"], capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return False, f"New binary failed verification: {r.stderr}"

        _cb("install", "Installing...")

        # Stop xray before replacing binary
        was_running = get_xray_status()["running"]
        if was_running:
            stop_xray()
            time.sleep(1)

        # Backup old binary
        if os.path.isfile(xray_bin):
            backup = f"{xray_bin}.bak"
            shutil.copy2(xray_bin, backup)

        # Install new binary
        shutil.copy2(new_bin, xray_bin)
        os.chmod(xray_bin, 0o755)

        # Also update geodata if present in archive
        for geofile in ["geoip.dat", "geosite.dat"]:
            src = os.path.join(tmp_dir, geofile)
            if os.path.isfile(src):
                dst_dir = os.path.dirname(xray_bin)
                # Try standard locations
                for dest in [
                    os.path.join(dst_dir, geofile),
                    f"/usr/local/share/xray/{geofile}",
                    f"/usr/share/xray/{geofile}",
                ]:
                    dest_dir = os.path.dirname(dest)
                    if os.path.isdir(dest_dir):
                        shutil.copy2(src, dest)
                        break

        # Restart if was running
        if was_running:
            _cb("restart", "Restarting xray...")
            time.sleep(1)
            start_xray()

        # Cleanup
        shutil.rmtree(tmp_dir, ignore_errors=True)

        # Clear version cache
        _version_cache.clear()

        new_ver = get_installed_version() or latest
        msg = f"Updated: v{current or '?'} -> v{new_ver}"
        _cb("done", msg)
        return True, msg

    except Exception as e:
        # Cleanup on error
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, f"Update failed: {e}"


def update_xray_async(callback: Optional[Callable] = None,
                      done_callback: Optional[Callable] = None):
    """Run xray update in background thread."""
    def _run():
        ok, msg = update_xray_core(callback=callback)
        if done_callback:
            done_callback(ok, msg)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t
