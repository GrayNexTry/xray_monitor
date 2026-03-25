#!/usr/bin/env bash
set -euo pipefail

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# xray-monitor — установщик для Linux (Debian/Ubuntu/CentOS/etc)
# Использование: bash install.sh [--uninstall] [--update]
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

APP_NAME="xray-monitor"
INSTALL_DIR="/opt/xray-monitor"
VENV_DIR="$INSTALL_DIR/venv"
BIN_LINK="/usr/local/bin/$APP_NAME"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
REQUIRED_PY_VERSION="3.9"
VERSION_FILE="$INSTALL_DIR/.version"
PIP_LOG="/tmp/xray-monitor-install.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()   { echo -e "${GREEN}[+]${NC} $*"; }
warn()   { echo -e "${YELLOW}[!]${NC} $*"; }
error()  { echo -e "${RED}[x]${NC} $*"; exit 1; }
header() { echo -e "\n${CYAN}${BOLD}━━━ $* ━━━${NC}\n"; }

# ── Спиннер ─────────────────────────────────────────────────

_SPIN_PID=""

spinner_start() {
    local msg="$1"
    local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
    (
        local i=0
        while true; do
            printf "\r${CYAN}${frames[$i]}${NC}  %s" "$msg"
            i=$(( (i + 1) % ${#frames[@]} ))
            sleep 0.1
        done
    ) &
    _SPIN_PID=$!
    disown "$_SPIN_PID" 2>/dev/null || true
}

spinner_stop() {
    local msg="${1:-}"
    if [[ -n "$_SPIN_PID" ]]; then
        kill "$_SPIN_PID" 2>/dev/null || true
        wait "$_SPIN_PID" 2>/dev/null || true
        _SPIN_PID=""
    fi
    printf "\r\033[K"
    if [[ -n "$msg" ]]; then
        info "$msg"
    fi
}

# ── Проверка root ────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    error "Запустите от root: sudo bash install.sh"
fi

# ── Режим удаления ───────────────────────────────────────────

if [[ "${1:-}" == "--uninstall" ]]; then
    header "Удаление $APP_NAME"
    spinner_start "Останавливаем сервис..."
    systemctl stop "$APP_NAME" 2>/dev/null || true
    systemctl disable "$APP_NAME" 2>/dev/null || true
    spinner_stop
    spinner_start "Удаляем файлы..."
    rm -f "$SERVICE_FILE"
    rm -f "$BIN_LINK"
    rm -rf "$INSTALL_DIR"
    systemctl daemon-reload 2>/dev/null || true
    spinner_stop "Удалено. Готово."
    exit 0
fi

# ── Определяем режим (установка / обновление) ───────────────

IS_UPDATE=false
if [[ -d "$INSTALL_DIR/src" ]] && [[ -f "$BIN_LINK" ]]; then
    IS_UPDATE=true
fi

if [[ "${1:-}" == "--update" ]]; then
    IS_UPDATE=true
fi

if $IS_UPDATE; then
    header "Обновление $APP_NAME"
    OLD_VERSION="unknown"
    if [[ -f "$VERSION_FILE" ]]; then
        OLD_VERSION=$(cat "$VERSION_FILE")
    elif [[ -f "$INSTALL_DIR/src/xray_monitor/__init__.py" ]]; then
        OLD_VERSION=$(grep -oP '__version__\s*=\s*"\K[^"]+' "$INSTALL_DIR/src/xray_monitor/__init__.py" 2>/dev/null || echo "unknown")
    fi
    info "Текущая версия: $OLD_VERSION"
else
    header "Установка $APP_NAME"
fi

# ── Поиск Python ≥ 3.9 ──────────────────────────────────────

PYTHON=""
for py in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
    if command -v "$py" &>/dev/null; then
        major=$("$py" -c "import sys; print(sys.version_info.major)")
        minor=$("$py" -c "import sys; print(sys.version_info.minor)")
        if [[ "$major" -ge 3 && "$minor" -ge 9 ]]; then
            PYTHON="$py"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    warn "Python >= $REQUIRED_PY_VERSION не найден. Устанавливаем..."
    if command -v apt-get &>/dev/null; then
        spinner_start "apt-get install python3..."
        apt-get update -qq
        apt-get install -y -qq python3 python3-venv python3-pip
        spinner_stop "Python установлен"
    elif command -v dnf &>/dev/null; then
        spinner_start "dnf install python3..."
        dnf install -y python3 python3-pip &>/dev/null
        spinner_stop "Python установлен"
    elif command -v yum &>/dev/null; then
        spinner_start "yum install python3..."
        yum install -y python3 python3-pip &>/dev/null
        spinner_stop "Python установлен"
    else
        error "Не удалось установить Python. Установите Python >= $REQUIRED_PY_VERSION вручную."
    fi
    PYTHON="python3"
fi

info "Используем $PYTHON ($($PYTHON --version 2>&1))"

# ── Создаём директорию ───────────────────────────────────────

mkdir -p "$INSTALL_DIR"

# ── Копируем исходники ───────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ -d "$SCRIPT_DIR/src/xray_monitor" ]]; then
    if $IS_UPDATE; then
        spinner_start "Обновляем файлы из $SCRIPT_DIR..."
        rm -rf "$INSTALL_DIR/src"
        rm -f "$INSTALL_DIR/pyproject.toml"
    else
        spinner_start "Копируем исходники..."
    fi
    cp -r "$SCRIPT_DIR/src" "$INSTALL_DIR/"
    cp -f "$SCRIPT_DIR/pyproject.toml" "$INSTALL_DIR/"
    cp -f "$SCRIPT_DIR/install.sh" "$INSTALL_DIR/install.sh"
    chmod +x "$INSTALL_DIR/install.sh"
    spinner_stop "Исходники скопированы"
else
    error "Исходники не найдены. Запустите install.sh из директории проекта."
fi

# ── Создаём / обновляем venv и устанавливаем зависимости ────

pip_run() {
    # Запускаем pip с выводом в лог; при ошибке показываем лог и падаем
    if ! "$@" >> "$PIP_LOG" 2>&1; then
        spinner_stop
        warn "pip завершился с ошибкой. Лог:"
        tail -30 "$PIP_LOG"
        exit 1
    fi
}

: > "$PIP_LOG"   # очищаем лог

if $IS_UPDATE && [[ -d "$VENV_DIR" ]]; then
    spinner_start "Обновляем pip..."
    pip_run "$VENV_DIR/bin/pip" install --upgrade pip
    spinner_stop

    spinner_start "Обновляем зависимости (может занять пару минут)..."
    pip_run "$VENV_DIR/bin/pip" install --prefer-binary --upgrade "$INSTALL_DIR"
    spinner_stop "Зависимости обновлены"
else
    spinner_start "Создаём виртуальное окружение..."
    $PYTHON -m venv "$VENV_DIR" --clear
    spinner_stop "Виртуальное окружение создано"

    spinner_start "Устанавливаем зависимости (может занять пару минут)..."
    pip_run "$VENV_DIR/bin/pip" install --upgrade pip
    pip_run "$VENV_DIR/bin/pip" install --prefer-binary "$INSTALL_DIR"
    spinner_stop "Зависимости установлены"
fi

# ── MaxMind GeoLite2 (офлайн геолокация) ─────────────────────

MMDB_DIR="$INSTALL_DIR"
CITY_DB="$MMDB_DIR/GeoLite2-City.mmdb"
ASN_DB="$MMDB_DIR/GeoLite2-ASN.mmdb"
MMDB_ALREADY=false

if [[ -f "$CITY_DB" ]]; then
    MMDB_ALREADY=true
fi

# На обновлении не переспрашиваем если уже установлено
if $IS_UPDATE && $MMDB_ALREADY; then
    spinner_start "Обновляем GeoLite2 базы..."
    if curl -fsSL "https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-City.mmdb" \
            -o "$CITY_DB.tmp" 2>>"$PIP_LOG" && mv "$CITY_DB.tmp" "$CITY_DB"; then
        curl -fsSL "https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-ASN.mmdb" \
            -o "$ASN_DB.tmp" 2>>"$PIP_LOG" && mv "$ASN_DB.tmp" "$ASN_DB" || true
        spinner_stop "GeoLite2 базы обновлены"
    else
        spinner_stop
        warn "Не удалось обновить GeoLite2 — оставляем старые файлы"
    fi
elif ! $IS_UPDATE; then
    echo ""
    echo -e "${CYAN}━━━ Геолокация ━━━${NC}"
    echo ""
    echo " Выберите источник геолокации:"
    echo "   1) MaxMind GeoLite2 — офлайн, без лимитов (скачать ~60 MB)"
    echo "   2) ip-api.com       — онлайн, 45 req/min (работает без настройки)"
    echo ""
    read -rp "   Ваш выбор [1/2, Enter=2]: " GEO_CHOICE
    GEO_CHOICE="${GEO_CHOICE:-2}"

    if [[ "$GEO_CHOICE" == "1" ]]; then
        echo ""
        spinner_start "Устанавливаем maxminddb..."
        pip_run "$VENV_DIR/bin/pip" install --prefer-binary "maxminddb>=1.5"
        spinner_stop "maxminddb установлен"

        spinner_start "Скачиваем GeoLite2-City.mmdb (~50 MB)..."
        if curl -fsSL "https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-City.mmdb" \
                -o "$CITY_DB.tmp" 2>>"$PIP_LOG" && mv "$CITY_DB.tmp" "$CITY_DB"; then
            spinner_stop "GeoLite2-City.mmdb загружен"
        else
            spinner_stop
            warn "Не удалось скачать GeoLite2-City.mmdb"
            warn "Скачайте вручную и положите в $CITY_DB"
            warn "Источник: https://github.com/P3TERX/GeoLite.mmdb"
        fi

        spinner_start "Скачиваем GeoLite2-ASN.mmdb (~8 MB)..."
        if curl -fsSL "https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-ASN.mmdb" \
                -o "$ASN_DB.tmp" 2>>"$PIP_LOG" && mv "$ASN_DB.tmp" "$ASN_DB"; then
            spinner_stop "GeoLite2-ASN.mmdb загружен"
        else
            spinner_stop
            warn "GeoLite2-ASN.mmdb не загружен (будет работать без ASN-данных)"
        fi
    else
        info "Используется ip-api.com (можно переключиться позже, положив .mmdb в $MMDB_DIR)"
    fi
fi

# ── Сохраняем версию ─────────────────────────────────────────

NEW_VERSION=$("$VENV_DIR/bin/python" -c "from xray_monitor import __version__; print(__version__)" 2>/dev/null || echo "unknown")
echo "$NEW_VERSION" > "$VERSION_FILE"

# ── Создаём launcher ─────────────────────────────────────────

info "Создаём команду $BIN_LINK..."
cat > "$BIN_LINK" << 'LAUNCHER'
#!/usr/bin/env bash
# Автоматически запрашиваем sudo если не root
if [ "$EUID" -ne 0 ]; then
    exec sudo -E /opt/xray-monitor/venv/bin/python -m xray_monitor "$@"
fi
exec /opt/xray-monitor/venv/bin/python -m xray_monitor "$@"
LAUNCHER
chmod +x "$BIN_LINK"

# ── Права на логи и sudoers ──────────────────────────────────

# Делаем директорию логов xray читаемой без root
if [[ -d "/var/log/xray" ]]; then
    chmod 755 /var/log/xray 2>/dev/null || true
    find /var/log/xray -name "*.log" -exec chmod 644 {} \; 2>/dev/null || true
    info "Права на /var/log/xray/ настроены"
fi

# sudoers: запуск xray-monitor без пароля для группы sudo
SUDOERS_FILE="/etc/sudoers.d/xray-monitor"
cat > "$SUDOERS_FILE" << 'SUDOERS'
# xray-monitor: запуск без пароля для администраторов
%sudo ALL=(ALL) NOPASSWD: /opt/xray-monitor/venv/bin/python -m xray_monitor *
SUDOERS
chmod 440 "$SUDOERS_FILE"
# Проверяем синтаксис sudoers
if visudo -cf "$SUDOERS_FILE" &>/dev/null; then
    info "Sudoers настроен: xray-monitor без пароля"
else
    warn "Ошибка в sudoers, удаляем файл"
    rm -f "$SUDOERS_FILE"
fi

# ── Создаём systemd-сервис ───────────────────────────────────

if ! $IS_UPDATE || [[ ! -f "$SERVICE_FILE" ]]; then
    info "Создаём systemd-сервис..."
    cat > "$SERVICE_FILE" << 'UNIT'
[Unit]
Description=Xray Monitor TUI
After=network.target xray.service

[Service]
Type=simple
ExecStart=/usr/local/bin/xray-monitor --server 127.0.0.1:10085 --config /usr/local/etc/xray/config.json
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
    info "Systemd-сервис уже существует, пропускаем..."
fi

# ── Проверяем установку ──────────────────────────────────────

spinner_start "Проверяем установку..."
if "$BIN_LINK" --help &>/dev/null; then
    spinner_stop "Проверка пройдена"
else
    spinner_stop
    warn "Команда создана, но --help завершился с ошибкой. Проверьте зависимости."
fi

# ── Итог ─────────────────────────────────────────────────────

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if $IS_UPDATE; then
    echo -e " ${GREEN}xray-monitor обновлён!${NC}  ${OLD_VERSION} → ${NEW_VERSION}"
else
    echo -e " ${GREEN}xray-monitor v${NEW_VERSION} установлен!${NC}"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo " Запуск:"
echo "   xray-monitor"
echo "   xray-monitor -s 127.0.0.1:10085"
echo "   xray-monitor -c /path/to/config.json"
echo ""
echo " Клавиши:"
echo "   q=выход  r=реконнект  s=сортировка  z=сброс  p=пауза"
echo "   Q=QR     e=редактор   R=рестарт     H=горячий-релоад"
echo "   S=старт  X=стоп       U=обновить    E=автозапуск вкл/выкл"
echo "   C=проверка  B=откат   1-6=вкладки   f=фильтр"
echo ""
if $IS_UPDATE; then
    echo " Обновление:"
    echo "   sudo bash install.sh"
    echo "   sudo bash $INSTALL_DIR/install.sh"
else
    echo " Обновление:"
    echo "   1) git pull (или скопируй новые файлы)"
    echo "   2) sudo bash install.sh"
fi
echo ""
if [[ -f "$CITY_DB" ]]; then
    echo " Геолокация: MaxMind GeoLite2 (офлайн)"
    echo "   Обновить базы: sudo bash install.sh --update"
else
    echo " Геолокация: ip-api.com (онлайн)"
    echo "   Переключить на MaxMind: sudo bash install.sh --update"
fi
echo ""
echo " Удаление:"
echo "   sudo bash install.sh --uninstall"
echo ""
