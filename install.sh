#!/usr/bin/env bash
set -euo pipefail

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# xray-monitor installer for Linux (Debian/Ubuntu/CentOS/etc)
# Usage: curl -sL <url>/install.sh | bash
#    or: bash install.sh [--uninstall] [--update]
#
# Features:
#   - Auto-detects if already installed → runs update
#   - Preserves config on update
#   - Creates systemd service
#   - Supports --uninstall flag
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

APP_NAME="xray-monitor"
INSTALL_DIR="/opt/xray-monitor"
VENV_DIR="$INSTALL_DIR/venv"
BIN_LINK="/usr/local/bin/$APP_NAME"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
REQUIRED_PY_VERSION="3.9"
VERSION_FILE="$INSTALL_DIR/.version"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[x]${NC} $*"; exit 1; }
header(){ echo -e "\n${CYAN}${BOLD}━━━ $* ━━━${NC}\n"; }

# ── Check root ──────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    error "Run as root: sudo bash install.sh"
fi

# ── Uninstall mode ──────────────────────────────────────────

if [[ "${1:-}" == "--uninstall" ]]; then
    header "Uninstalling $APP_NAME"
    systemctl stop "$APP_NAME" 2>/dev/null || true
    systemctl disable "$APP_NAME" 2>/dev/null || true
    rm -f "$SERVICE_FILE"
    rm -f "$BIN_LINK"
    rm -rf "$INSTALL_DIR"
    systemctl daemon-reload 2>/dev/null || true
    info "Removed. Done."
    exit 0
fi

# ── Detect install mode (fresh/update) ──────────────────────

IS_UPDATE=false
if [[ -d "$INSTALL_DIR/src" ]] && [[ -f "$BIN_LINK" ]]; then
    IS_UPDATE=true
fi

if [[ "${1:-}" == "--update" ]]; then
    IS_UPDATE=true
fi

if $IS_UPDATE; then
    header "Updating $APP_NAME"
    # Save old version
    OLD_VERSION="unknown"
    if [[ -f "$VERSION_FILE" ]]; then
        OLD_VERSION=$(cat "$VERSION_FILE")
    elif [[ -f "$INSTALL_DIR/src/xray_monitor/__init__.py" ]]; then
        OLD_VERSION=$(grep -oP '__version__\s*=\s*"\K[^"]+' "$INSTALL_DIR/src/xray_monitor/__init__.py" 2>/dev/null || echo "unknown")
    fi
    info "Current version: $OLD_VERSION"
else
    header "Installing $APP_NAME"
fi

# ── Find Python ≥ 3.9 ──────────────────────────────────────

PYTHON=""
for py in python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$py" &>/dev/null; then
        ver=$("$py" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
        major=$("$py" -c "import sys; print(sys.version_info.major)")
        minor=$("$py" -c "import sys; print(sys.version_info.minor)")
        if [[ "$major" -ge 3 && "$minor" -ge 9 ]]; then
            PYTHON="$py"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    warn "Python >= $REQUIRED_PY_VERSION not found. Installing..."
    if command -v apt-get &>/dev/null; then
        apt-get update -qq
        apt-get install -y -qq python3 python3-venv python3-pip
    elif command -v dnf &>/dev/null; then
        dnf install -y python3 python3-pip
    elif command -v yum &>/dev/null; then
        yum install -y python3 python3-pip
    else
        error "Cannot install Python. Install Python >= $REQUIRED_PY_VERSION manually."
    fi
    PYTHON="python3"
fi

info "Using $PYTHON ($($PYTHON --version 2>&1))"

# ── Create install directory ────────────────────────────────

mkdir -p "$INSTALL_DIR"

# ── Copy source ─────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -d "$SCRIPT_DIR/src/xray_monitor" ]]; then
    if $IS_UPDATE; then
        info "Updating source files from $SCRIPT_DIR..."
        # Remove old source but preserve venv and config
        rm -rf "$INSTALL_DIR/src"
        rm -f "$INSTALL_DIR/pyproject.toml"
    else
        info "Copying source from $SCRIPT_DIR..."
    fi
    cp -r "$SCRIPT_DIR/src" "$INSTALL_DIR/"
    cp -f "$SCRIPT_DIR/pyproject.toml" "$INSTALL_DIR/"
    # Copy install script itself for future updates
    cp -f "$SCRIPT_DIR/install.sh" "$INSTALL_DIR/install.sh"
    chmod +x "$INSTALL_DIR/install.sh"
else
    error "Source not found. Run install.sh from the project directory."
fi

# ── Create/update venv and install ──────────────────────────

if $IS_UPDATE && [[ -d "$VENV_DIR" ]]; then
    info "Updating dependencies in existing venv..."
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip 2>/dev/null || true
    "$VENV_DIR/bin/pip" install --quiet --upgrade "$INSTALL_DIR" 2>/dev/null || \
        "$VENV_DIR/bin/pip" install --quiet --force-reinstall "$INSTALL_DIR"
else
    info "Creating virtual environment..."
    $PYTHON -m venv "$VENV_DIR" --clear
    info "Installing dependencies (this may take a minute)..."
    "$VENV_DIR/bin/pip" install --quiet --upgrade pip
    "$VENV_DIR/bin/pip" install --quiet "$INSTALL_DIR"
fi

# ── Save version ────────────────────────────────────────────

NEW_VERSION=$("$VENV_DIR/bin/python" -c "from xray_monitor import __version__; print(__version__)" 2>/dev/null || echo "unknown")
echo "$NEW_VERSION" > "$VERSION_FILE"

# ── Create launcher script ──────────────────────────────────

info "Creating launcher at $BIN_LINK..."
cat > "$BIN_LINK" << 'LAUNCHER'
#!/usr/bin/env bash
exec /opt/xray-monitor/venv/bin/python -m xray_monitor "$@"
LAUNCHER
chmod +x "$BIN_LINK"

# ── Create systemd service (optional, for headless monitoring) ──

if ! $IS_UPDATE || [[ ! -f "$SERVICE_FILE" ]]; then
    info "Creating systemd service (optional)..."
    cat > "$SERVICE_FILE" << 'UNIT'
[Unit]
Description=Xray Monitor TUI
After=network.target xray.service

[Service]
Type=simple
ExecStart=/usr/local/bin/xray-monitor --server 127.0.0.1:10085 --config /usr/local/etc/xray/config.json --lang ru
Restart=on-failure
RestartSec=5
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty7

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
else
    info "Systemd service already exists, skipping..."
fi

# ── Verify installation ─────────────────────────────────────

info "Verifying..."
if "$BIN_LINK" --help &>/dev/null; then
    info "Installation successful!"
else
    warn "Binary created but --help failed. Check dependencies."
fi

# ── Print summary ───────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if $IS_UPDATE; then
    echo -e " ${GREEN}xray-monitor updated!${NC}  ${OLD_VERSION} -> ${NEW_VERSION}"
else
    echo -e " ${GREEN}xray-monitor v${NEW_VERSION} installed!${NC}"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo " Usage:"
echo "   xray-monitor                          # default settings"
echo "   xray-monitor -s 127.0.0.1:10085       # custom gRPC address"
echo "   xray-monitor -c /path/to/config.json  # custom config"
echo "   xray-monitor --lang en                # English UI"
echo ""
echo " Keys:"
echo "   q=quit  r=reconnect  s=sort  z=reset  p=pause  l=lang"
echo "   Q=QR    e=nano       R=restart xray   C=check  B=rollback"
echo "   S=start X=stop       U=update xray    E=enable/disable"
echo "   1-6=tabs  f=filter"
echo ""
if $IS_UPDATE; then
    echo " Update:"
    echo "   sudo bash install.sh              # run again to update"
    echo "   sudo bash $INSTALL_DIR/install.sh # or from install dir"
else
    echo " Update:"
    echo "   1) git pull (or copy new files)"
    echo "   2) sudo bash install.sh           # auto-detects update mode"
fi
echo ""
echo " Uninstall:"
echo "   sudo bash install.sh --uninstall"
echo ""
