#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  Vikingi — Настройка сервера в сети компании
#  Запуск: sudo bash setup_network.sh
# ═══════════════════════════════════════════════════════════════

set -e

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$PROJECT_DIR/.env"
IFACE=""

# ——— Цвета ———
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "  ${GREEN}✓${NC} $1"; }
fail() { echo -e "  ${RED}✗${NC} $1"; }
warn() { echo -e "  ${YELLOW}!${NC} $1"; }

# ——— Определить сетевой интерфейс ———
detect_iface() {
    IFACE=$(ip -o link show | awk -F': ' '{print $2}' | grep -v -E '^(lo|docker|br-|veth)' | head -1)
    if [ -z "$IFACE" ]; then
        echo "Ошибка: не найден сетевой интерфейс"
        exit 1
    fi
}

# ═══════════════════════════════════════════════════════════════
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Vikingi — Настройка сервера в сети компании"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  Проект: $PROJECT_DIR"

detect_iface
CURRENT_IP=$(ip -4 addr show "$IFACE" 2>/dev/null | grep -oP 'inet \K[\d.]+' | head -1)
echo "  Интерфейс: $IFACE"
echo "  Текущий IP: ${CURRENT_IP:-не назначен}"
echo ""

# ═══════════════════════════════════════════════════════════════
echo "─── СЕТЬ ─────────────────────────────────────────────────"
echo ""

read -p "  Использовать DHCP? (y — авто, n — статический IP) [y]: " USE_DHCP
USE_DHCP="${USE_DHCP:-y}"

SERVER_IP=""
SUBNET_MASK=""
GATEWAY=""
DNS=""

if [[ "$USE_DHCP" =~ ^[Nn]$ ]]; then
    read -p "  IP сервера (например 192.168.1.50): " SERVER_IP
    if [ -z "$SERVER_IP" ]; then
        echo "Ошибка: IP обязателен при статической настройке"
        exit 1
    fi
    read -p "  Маска подсети [24]: " SUBNET_MASK
    SUBNET_MASK="${SUBNET_MASK:-24}"
    read -p "  Шлюз (например 192.168.1.1): " GATEWAY
    if [ -z "$GATEWAY" ]; then
        echo "Ошибка: шлюз обязателен"
        exit 1
    fi
    read -p "  DNS-сервер [8.8.8.8]: " DNS
    DNS="${DNS:-8.8.8.8}"
else
    echo "  → IP будет получен автоматически по DHCP"
fi

# ——— Подсеть LAN (для firewall) ———
echo ""
if [ -n "$SERVER_IP" ]; then
    LAN_GUESS=$(echo "$SERVER_IP" | sed 's/\.[0-9]*$/.0\/24/')
else
    LAN_GUESS=$(echo "$CURRENT_IP" | sed 's/\.[0-9]*$/.0\/24/')
fi
read -p "  Подсеть LAN [$LAN_GUESS]: " LAN_SUBNET
LAN_SUBNET="${LAN_SUBNET:-$LAN_GUESS}"

# ——— IP сервера 1С ———
echo ""
read -p "  IP сервера 1С Альфа 6.0 (Enter — пропустить): " ICS_IP

# ═══════════════════════════════════════════════════════════════
echo ""
echo "─── SIP-ТЕЛЕФОНИЯ (данные от Потемкина Арсения, Инфолада) ─"
echo ""

read -p "  SIP-логин (Enter — пропустить): " SIP_USER
SIP_PASS=""
if [ -n "$SIP_USER" ]; then
    read -sp "  SIP-пароль: " SIP_PASS
    echo ""
fi

# ═══════════════════════════════════════════════════════════════
echo ""
echo "─── ПРОВЕРКА ВВЕДЁННЫХ ДАННЫХ ────────────────────────────"
echo ""
if [[ "$USE_DHCP" =~ ^[Nn]$ ]]; then
    echo "  Сеть:          $SERVER_IP/$SUBNET_MASK, шлюз $GATEWAY, DNS $DNS"
else
    echo "  Сеть:          DHCP (автоматически)"
fi
echo "  Подсеть LAN:   $LAN_SUBNET"
echo "  IP 1С:         ${ICS_IP:-(пропущен)}"
echo "  SIP-логин:     ${SIP_USER:-(пропущен)}"
echo "  SIP-пароль:    ${SIP_PASS:+****}"
echo ""

read -p "  Применить? (y/N): " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "  Отмена."
    exit 0
fi

echo ""

# ═══════════════════════════════════════════════════════════════
# 1. НАСТРОЙКА СЕТИ
# ═══════════════════════════════════════════════════════════════

echo "  [1/5] Настройка сети..."

if [[ "$USE_DHCP" =~ ^[Nn]$ ]]; then
    CON_NAME=$(nmcli -t -f NAME,DEVICE con show --active | grep "$IFACE" | head -1 | cut -d: -f1)
    if [ -z "$CON_NAME" ]; then
        CON_NAME=$(nmcli -t -f NAME con show | head -1)
    fi

    if [ -n "$CON_NAME" ]; then
        nmcli con mod "$CON_NAME" \
            ipv4.method manual \
            ipv4.addresses "$SERVER_IP/$SUBNET_MASK" \
            ipv4.gateway "$GATEWAY" \
            ipv4.dns "$DNS" 2>/dev/null

        nmcli con down "$CON_NAME" 2>/dev/null || true
        sleep 1
        nmcli con up "$CON_NAME" 2>/dev/null
        sleep 2
        ok "Статический IP $SERVER_IP назначен через NetworkManager"
    else
        cat > /etc/netplan/99-vikingi-static.yaml <<NETEOF
network:
  version: 2
  ethernets:
    $IFACE:
      addresses:
        - $SERVER_IP/$SUBNET_MASK
      routes:
        - to: default
          via: $GATEWAY
      nameservers:
        addresses:
          - $DNS
NETEOF
        netplan apply 2>/dev/null
        sleep 2
        ok "Статический IP $SERVER_IP назначен через netplan"
    fi
else
    ok "DHCP — сеть настроена автоматически"
fi

FINAL_IP=$(ip -4 addr show "$IFACE" 2>/dev/null | grep -oP 'inet \K[\d.]+' | head -1)

# ═══════════════════════════════════════════════════════════════
# 2. FIREWALL
# ═══════════════════════════════════════════════════════════════

echo "  [2/5] Настройка firewall..."

if command -v ufw &> /dev/null; then
    ufw --force reset > /dev/null 2>&1
    ufw default deny incoming > /dev/null 2>&1
    ufw default allow outgoing > /dev/null 2>&1

    ufw allow 22/tcp > /dev/null 2>&1

    ufw allow from "$LAN_SUBNET" to any port 5433 proto tcp > /dev/null 2>&1
    if [ -n "$ICS_IP" ]; then
        ufw allow from "$ICS_IP" to any port 5433 proto tcp > /dev/null 2>&1
    fi

    ufw allow from "$LAN_SUBNET" to any port 8000 proto tcp > /dev/null 2>&1

    ufw allow from 81.23.191.220 to any port 5060 proto udp > /dev/null 2>&1
    ufw allow from 81.23.191.220 to any port 5060 proto tcp > /dev/null 2>&1
    ufw allow from 81.23.191.220 to any port 10000:10100 proto udp > /dev/null 2>&1

    ufw --force enable > /dev/null 2>&1
    ok "Firewall настроен (SSH, SIP, PostgreSQL, админка)"
else
    warn "ufw не найден — firewall не настроен"
fi

# ═══════════════════════════════════════════════════════════════
# 3. ОБНОВЛЕНИЕ .env
# ═══════════════════════════════════════════════════════════════

echo "  [3/5] Обновление .env..."

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$PROJECT_DIR/.env.example" ]; then
        cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
    else
        echo "Ошибка: нет .env и .env.example"
        exit 1
    fi
fi

update_env() {
    local key="$1"
    local val="$2"
    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        sed -i "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
    else
        echo "${key}=${val}" >> "$ENV_FILE"
    fi
}

if [ -n "$SIP_USER" ]; then
    update_env "INFOLADA_SIP_USER" "$SIP_USER"
fi
if [ -n "$SIP_PASS" ]; then
    update_env "INFOLADA_SIP_PASSWORD" "$SIP_PASS"
fi
if [ -n "$ICS_IP" ]; then
    update_env "ICS_SERVER_IP" "$ICS_IP"
fi

ok ".env обновлён"

# ═══════════════════════════════════════════════════════════════
# 4. ЗАПУСК DOCKER
# ═══════════════════════════════════════════════════════════════

echo "  [4/5] Запуск Docker-контейнеров (может занять 2-3 минуты)..."

cd "$PROJECT_DIR"

if ! docker compose ps --quiet 2>/dev/null | grep -q .; then
    docker compose up -d 2>/dev/null
else
    docker compose restart 2>/dev/null
fi

sleep 5
RUNNING=$(docker compose ps --format '{{.Name}} {{.Status}}' 2>/dev/null | grep -c "Up" || echo "0")
ok "Запущено контейнеров: $RUNNING"

# ═══════════════════════════════════════════════════════════════
# 5. ПРОВЕРКА
# ═══════════════════════════════════════════════════════════════

echo "  [5/5] Проверка..."

if ping -c1 -W2 8.8.8.8 > /dev/null 2>&1; then
    ok "Интернет"
else
    fail "Интернет — нет доступа (проверьте шлюз и DNS)"
fi

sleep 2

if curl -sf http://localhost:8000/api/license > /dev/null 2>&1; then
    LICENSE_MSG=$(curl -sf http://localhost:8000/api/license 2>/dev/null | grep -oP '"message":"[^"]*"' | head -1)
    ok "Админка (порт 8000): $LICENSE_MSG"
else
    ADMIN_STATUS=$(docker compose ps admin-panel --format '{{.Status}}' 2>/dev/null)
    if echo "$ADMIN_STATUS" | grep -q "Up"; then
        warn "Админка запущена, но ещё стартует (подождите 30 сек и попробуйте http://${FINAL_IP}:8000)"
    else
        fail "Админка не запущена. Логи: docker compose logs admin-panel"
    fi
fi

BOT_STATUS=$(docker compose ps telegram-bot --format '{{.Status}}' 2>/dev/null)
if echo "$BOT_STATUS" | grep -q "Up"; then
    ok "Telegram-бот запущен"
else
    fail "Telegram-бот не запущен. Логи: docker compose logs telegram-bot"
fi

if [ -n "$SIP_USER" ]; then
    sleep 3
    SIP_REG=$(docker exec asterisk asterisk -rx "pjsip show registrations" 2>/dev/null | grep -i "registered" || true)
    if [ -n "$SIP_REG" ]; then
        ok "SIP-регистрация (Инфолада): зарегистрирован"
    else
        fail "SIP не зарегистрирован. Проверьте логин/пароль и белый список IP у Инфолады"
        warn "Логи: docker compose logs asterisk"
    fi
else
    warn "SIP-данные не введены — телефония не настроена"
fi

if [ -n "$ICS_IP" ]; then
    if ping -c1 -W2 "$ICS_IP" > /dev/null 2>&1; then
        ok "Сервер 1С ($ICS_IP) доступен"
    else
        warn "Сервер 1С ($ICS_IP) не пингуется (возможно, ICMP заблокирован)"
    fi
fi

# ═══════════════════════════════════════════════════════════════

echo ""
echo "═══════════════════════════════════════════════════════════"
if [ -n "$FINAL_IP" ]; then
    echo "  ГОТОВО. Сервер подключён в сеть."
    echo ""
    echo "  IP сервера:  $FINAL_IP"
    echo "  Админка:     http://$FINAL_IP:8000"
    echo "  Telegram:    @Neuro_auto_bot"
else
    echo "  ВНИМАНИЕ: IP не определён. Проверьте сетевые настройки."
fi
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "  При проблемах: Козенков Р.В., +7(902)373-08-08"
echo ""
