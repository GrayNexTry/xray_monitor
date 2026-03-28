#!/usr/bin/env bash
set -euo pipefail

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# xray-monitor (Go) — установщик для Linux
# Использование: bash install.sh [--uninstall] [--update] [--build]
#
#  --build     принудительно собрать из исходников (нужен Go ≥ 1.22)
#  --update    обновить бинарник / базы GeoLite2 / юнит
#  --uninstall полностью удалить
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

APP_NAME="xray-monitor"
INSTALL_DIR="/opt/xray-monitor"
BIN_TARGET="/usr/local/bin/$APP_NAME"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
DATA_DIR="$INSTALL_DIR"
VERSION_FILE="$INSTALL_DIR/.version"
LOG_FILE="/tmp/xray-monitor-install.log"

# Репозиторий — замените на свой при публикации
GITHUB_REPO="GrayNexTry/xray_monitor"   # пока публикации нет — сборка из исходников

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()   { echo -e "${GREEN}[+]${NC} $*"; }
warn()   { echo -e "${YELLOW}[!]${NC} $*"; }
error()  { echo -e "${RED}[x]${NC} $*" >&2; exit 1; }
header() { echo -e "\n${CYAN}${BOLD}━━━ $* ━━━${NC}\n"; }
step()   { echo -e "    ${CYAN}→${NC} $*"; }

# ── Спиннер ──────────────────────────────────────────────────────────

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
    [[ -n "$msg" ]] && info "$msg"
}

# ── Аргументы ────────────────────────────────────────────────────────

MODE="install"
FORCE_BUILD=false

for arg in "$@"; do
    case "$arg" in
        --uninstall) MODE="uninstall" ;;
        --update)    MODE="update" ;;
        --build)     FORCE_BUILD=true ;;
    esac
done

# ── Проверка root ────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
    error "Запустите от root: sudo bash install.sh"
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Удаление ─────────────────────────────────────────────────────────

if [[ "$MODE" == "uninstall" ]]; then
    header "Удаление $APP_NAME"
    spinner_start "Останавливаем сервис..."
    systemctl stop  "$APP_NAME" 2>/dev/null || true
    systemctl disable "$APP_NAME" 2>/dev/null || true
    spinner_stop

    spinner_start "Удаляем файлы..."
    rm -f  "$SERVICE_FILE"
    rm -f  "$BIN_TARGET"
    rm -rf "$INSTALL_DIR"
    systemctl daemon-reload 2>/dev/null || true
    spinner_stop "Удалено успешно"
    exit 0
fi

# ── Определяем arch ──────────────────────────────────────────────────

ARCH="$(uname -m)"
case "$ARCH" in
    x86_64)  GO_ARCH="amd64"  ;;
    aarch64) GO_ARCH="arm64"  ;;
    armv7l)  GO_ARCH="arm"    ;;
    i686)    GO_ARCH="386"    ;;
    *)       GO_ARCH="amd64"  ;;   # лучше amd64, чем ничего
esac

BINARY_NAME="${APP_NAME}-linux-${GO_ARCH}"

# ── Режим обновления / первый запуск ─────────────────────────────────

IS_UPDATE=false
[[ "$MODE" == "update" || -f "$BIN_TARGET" ]] && IS_UPDATE=true

if $IS_UPDATE; then
    OLD_VER="unknown"
    [[ -f "$VERSION_FILE" ]] && OLD_VER="$(cat "$VERSION_FILE")"
    header "Обновление $APP_NAME  (текущая: $OLD_VER)"
else
    header "Установка $APP_NAME  (Go-версия)"
fi

: > "$LOG_FILE"

mkdir -p "$INSTALL_DIR"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# БЛОК 1 — Получаем бинарник
# Приоритет:
#   1. Уже есть готовый бинарник рядом со скриптом
#   2. Скачать с GitHub Releases (когда будет публикация)
#   3. Собрать из исходников (нужен Go ≥ 1.22)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BUILT_BIN=""

try_local_binary() {
    # Ищем готовый бинарник рядом со скриптом
    for candidate in \
        "$SCRIPT_DIR/$BINARY_NAME" \
        "$SCRIPT_DIR/${APP_NAME}" \
        "$SCRIPT_DIR/bin/$BINARY_NAME" \
        "$SCRIPT_DIR/bin/${APP_NAME}"; do
        if [[ -f "$candidate" && -x "$candidate" ]]; then
            BUILT_BIN="$candidate"
            return 0
        fi
    done
    return 1
}

try_github_release() {
    # Скачиваем последний релиз с GitHub
    if ! command -v curl &>/dev/null; then return 1; fi

    local api_url="https://api.github.com/repos/${GITHUB_REPO}/releases/latest"
    local rel_info
    rel_info=$(curl -fsSL --max-time 10 "$api_url" 2>>"$LOG_FILE") || return 1

    # Ищем asset нашей архитектуры
    local dl_url
    dl_url=$(echo "$rel_info" \
        | grep -oP '"browser_download_url":\s*"\K[^"]+' \
        | grep -i "linux" \
        | grep -i "$GO_ARCH" \
        | head -1) || return 1

    [[ -z "$dl_url" ]] && return 1

    local tag
    tag=$(echo "$rel_info" | grep -oP '"tag_name":\s*"\K[^"]+' | head -1)
    info "Скачиваем релиз $tag для linux/$GO_ARCH..."

    local tmp_bin
    tmp_bin=$(mktemp)
    if curl -fsSL --progress-bar "$dl_url" -o "$tmp_bin" 2>>"$LOG_FILE"; then
        chmod +x "$tmp_bin"
        BUILT_BIN="$tmp_bin"
        return 0
    fi
    rm -f "$tmp_bin"
    return 1
}

build_from_source() {
    # Собираем Go-исходники
    local go_bin=""
    for candidate in /usr/local/go/bin/go /usr/bin/go "$(command -v go 2>/dev/null)"; do
        [[ -x "$candidate" ]] && go_bin="$candidate" && break
    done

    if [[ -z "$go_bin" ]]; then
        # Предлагаем установить Go
        echo ""
        echo -e " ${YELLOW}Go не найден.${NC} Установить Go 1.22 автоматически? [y/N]"
        read -rp "   > " ANS
        if [[ "${ANS,,}" == "y" ]]; then
            install_go
            go_bin="/usr/local/go/bin/go"
        else
            return 1
        fi
    fi

    local go_ver
    go_ver=$("$go_bin" version 2>/dev/null | grep -oP 'go\K[\d.]+' | head -1)
    info "Go $go_ver — собираем из исходников..."

    # Ищем исходники: сначала $SCRIPT_DIR (go/ папка проекта), потом $SCRIPT_DIR/..
    local src_dir=""
    for candidate in "$SCRIPT_DIR" "$SCRIPT_DIR/.." "$SCRIPT_DIR/../go"; do
        if [[ -f "$candidate/go.mod" ]]; then
            src_dir="$candidate"
            break
        fi
    done

    if [[ -z "$src_dir" ]]; then
        warn "go.mod не найден. Положите исходники рядом с install.sh"
        return 1
    fi

    local tmp_bin
    tmp_bin=$(mktemp)
    spinner_start "go build -ldflags='-s -w' ..."
    if GOOS=linux GOARCH=$GO_ARCH \
        "$go_bin" build -ldflags="-s -w" \
            -o "$tmp_bin" \
            "$src_dir/cmd/xray-monitor" >> "$LOG_FILE" 2>&1; then
        spinner_stop "Сборка завершена"
        chmod +x "$tmp_bin"
        BUILT_BIN="$tmp_bin"
        return 0
    else
        spinner_stop
        warn "Ошибка сборки. Лог:"
        tail -20 "$LOG_FILE"
        rm -f "$tmp_bin"
        return 1
    fi
}

install_go() {
    header "Установка Go 1.22"
    local GO_VER="1.22.5"
    local GO_TAR="go${GO_VER}.linux-${GO_ARCH}.tar.gz"
    local GO_URL="https://go.dev/dl/$GO_TAR"

    spinner_start "Скачиваем Go ${GO_VER}..."
    curl -fsSL "$GO_URL" -o "/tmp/$GO_TAR" >> "$LOG_FILE" 2>&1 || {
        spinner_stop
        error "Не удалось скачать Go. Установите вручную: https://go.dev/dl/"
    }
    spinner_stop "Go ${GO_VER} скачан"

    spinner_start "Устанавливаем в /usr/local/go..."
    rm -rf /usr/local/go
    tar -C /usr/local -xzf "/tmp/$GO_TAR" >> "$LOG_FILE" 2>&1
    rm -f "/tmp/$GO_TAR"
    spinner_stop "Go установлен: $(/usr/local/go/bin/go version)"

    # Добавляем в PATH для текущего сеанса
    export PATH="$PATH:/usr/local/go/bin"

    # Добавляем в /etc/profile.d постоянно
    if [[ ! -f /etc/profile.d/go.sh ]]; then
        echo 'export PATH="$PATH:/usr/local/go/bin"' > /etc/profile.d/go.sh
        info "Добавлено в /etc/profile.d/go.sh"
    fi
}

# ── Выбор стратегии получения бинарника ──────────────────────────────

if $FORCE_BUILD; then
    build_from_source || error "Сборка из исходников не удалась"
elif try_local_binary; then
    info "Найден готовый бинарник: $BUILT_BIN"
elif try_github_release; then
    : # success — переменная BUILT_BIN установлена
else
    warn "Готовый бинарник не найден, GitHub недоступен — собираем из исходников"
    build_from_source || error "Не удалось получить бинарник. Запустите: bash install.sh --build"
fi

# ── Получаем версию из бинарника ─────────────────────────────────────

NEW_VER="$("$BUILT_BIN" --version 2>/dev/null | awk '{print $NF}' || echo "1.0.0")"

# ── Устанавливаем бинарник ───────────────────────────────────────────

spinner_start "Устанавливаем $BIN_TARGET..."
if [[ -f "$BIN_TARGET" ]]; then
    cp -f "$BIN_TARGET" "${BIN_TARGET}.bak" 2>/dev/null || true
fi
install -m 755 "$BUILT_BIN" "$BIN_TARGET"
# Если это был временный файл — удаляем
[[ "$BUILT_BIN" == /tmp/* ]] && rm -f "$BUILT_BIN"
spinner_stop "Бинарник установлен: $BIN_TARGET"

echo "$NEW_VER" > "$VERSION_FILE"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# БЛОК 2 — GeoLite2 MMDB (офлайн геолокация)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CITY_DB="$INSTALL_DIR/GeoLite2-City.mmdb"
ASN_DB="$INSTALL_DIR/GeoLite2-ASN.mmdb"
MMDB_PRESENT=false
[[ -f "$CITY_DB" ]] && MMDB_PRESENT=true

download_mmdb() {
    local name="$1" dest="$2" url="$3"
    spinner_start "Скачиваем $name..."
    if curl -fsSL --max-time 120 "$url" -o "${dest}.tmp" 2>>"$LOG_FILE" \
       && mv "${dest}.tmp" "$dest"; then
        spinner_stop "${name} загружен ($(du -sh "$dest" | cut -f1))"
        return 0
    else
        spinner_stop
        rm -f "${dest}.tmp"
        warn "Не удалось скачать $name"
        return 1
    fi
}

# Источник без ключа MaxMind (зеркало, обновляется автоматически)
MMDB_CITY_URL="https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-City.mmdb"
MMDB_ASN_URL="https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-ASN.mmdb"

if $IS_UPDATE && $MMDB_PRESENT; then
    # При обновлении — молча обновляем базы
    download_mmdb "GeoLite2-City" "$CITY_DB" "$MMDB_CITY_URL" || true
    download_mmdb "GeoLite2-ASN"  "$ASN_DB"  "$MMDB_ASN_URL"  || true
else
    echo ""
    echo -e "${CYAN}━━━ Геолокация ━━━${NC}"
    echo ""
    echo "   1) MaxMind GeoLite2 — офлайн, без лимитов  (~60 MB)"
    echo "   2) ip-api.com       — онлайн, 45 req/min  (без скачивания)"
    echo ""
    read -rp "   Выбор [1/2, Enter=1]: " GEO_CHOICE
    GEO_CHOICE="${GEO_CHOICE:-1}"

    if [[ "$GEO_CHOICE" == "1" ]]; then
        download_mmdb "GeoLite2-City" "$CITY_DB" "$MMDB_CITY_URL" \
            || warn "Скачайте вручную: $MMDB_CITY_URL → $CITY_DB"
        download_mmdb "GeoLite2-ASN"  "$ASN_DB"  "$MMDB_ASN_URL"  || true
    else
        info "Используется ip-api.com. Базы можно добавить позже в $INSTALL_DIR/"
    fi
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# БЛОК 3 — Директория данных
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

mkdir -p "$DATA_DIR"
chmod 755 "$DATA_DIR"

# Права на директорию логов xray
if [[ -d "/var/log/xray" ]]; then
    chmod 755 /var/log/xray 2>/dev/null || true
    find /var/log/xray -name "*.log" -exec chmod 644 {} \; 2>/dev/null || true
    info "Права на /var/log/xray/ настроены"
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# БЛОК 4 — Systemd-сервис
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Определяем пути из реального окружения
XRAY_CFG="/usr/local/etc/xray/config.json"
[[ -f "/etc/xray/config.json" ]] && XRAY_CFG="/etc/xray/config.json"

XRAY_LOG="/var/log/xray/access.log"
[[ -f "/var/log/xray/access.log"  ]] || XRAY_LOG="/tmp/xray_access.log"

if ! $IS_UPDATE || [[ ! -f "$SERVICE_FILE" ]]; then
    info "Создаём systemd-сервис $APP_NAME..."
    cat > "$SERVICE_FILE" << UNIT
[Unit]
Description=Xray Monitor TUI (Go)
Documentation=https://github.com/GrayNexTry/xray_monitor
After=network.target xray.service

[Service]
Type=simple
ExecStart=$BIN_TARGET \\
    --server 127.0.0.1:10085 \\
    --config $XRAY_CFG \\
    --log    $XRAY_LOG \\
    --data   $DATA_DIR/traffic_history.json \\
    --interval 2
Environment=TERM=xterm-256color
Restart=on-failure
RestartSec=5
StandardInput=tty
StandardOutput=tty
TTYPath=/dev/tty7

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    info "Юнит создан: $SERVICE_FILE"
    step "Запустить сервис:  systemctl start $APP_NAME"
    step "В автозапуск:      systemctl enable $APP_NAME"
else
    # При обновлении перечитываем конфигурацию юнита
    systemctl daemon-reload
    if systemctl is-active --quiet "$APP_NAME" 2>/dev/null; then
        spinner_start "Перезапускаем сервис..."
        systemctl restart "$APP_NAME" 2>/dev/null || true
        spinner_stop "Сервис перезапущен"
    fi
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# БЛОК 5 — Быстрая проверка
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

spinner_start "Проверяем бинарник..."
if "$BIN_TARGET" --version &>/dev/null; then
    spinner_stop "Бинарник работает: $($BIN_TARGET --version 2>/dev/null || echo ok)"
else
    spinner_stop
    warn "Проверка --version не прошла (это нормально если флаг не реализован)"
fi

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ИТОГ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

GEO_BACKEND="ip-api.com (онлайн)"
[[ -f "$CITY_DB" ]] && GEO_BACKEND="MaxMind GeoLite2 (офлайн, $(du -sh "$CITY_DB" | cut -f1))"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if $IS_UPDATE; then
    echo -e " ${GREEN}xray-monitor обновлён!${NC}"
    [[ -n "${OLD_VER:-}" ]] && echo -e " ${OLD_VER} → ${NEW_VER}"
else
    echo -e " ${GREEN}xray-monitor ${NEW_VER} установлен!${NC}  (linux/${GO_ARCH})"
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo " Запуск:"
echo "   xray-monitor"
echo "   xray-monitor --server 127.0.0.1:10085 --config $XRAY_CFG"
echo ""
echo " Клавиши:"
echo "   q=выход    Tab/1-6=вкладки   p=пауза   s=сортировка"
echo "   Q=QR-код   r=рестарт Xray    U=обновить Xray"
echo ""
echo " Как сервис:"
echo "   systemctl start  $APP_NAME"
echo "   systemctl enable $APP_NAME"
echo "   journalctl -u $APP_NAME -f"
echo ""
echo " Геолокация: $GEO_BACKEND"
echo " Данные:     $DATA_DIR/traffic_history.json"
echo ""
echo " Обновление:"
echo "   sudo bash $0"
echo "   sudo bash $0 --build   # пересобрать из исходников"
echo ""
echo " Удаление:"
echo "   sudo bash $0 --uninstall"
echo ""
