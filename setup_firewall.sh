#!/bin/bash
# Скрипт подготовки firewall (ufw) для подключения сервера в сеть компании.
# Запускать от root / sudo.
#
# Перед запуском установить переменные:
#   ICS_SERVER_IP   — IP сервера 1С (для PostgreSQL)
#   INFOLADA_IP     — IP Инфолады (для SIP/RTP)
#   LAN_SUBNET      — подсеть компании (для admin-panel, Samba)
#
# Пример: sudo ICS_SERVER_IP=192.168.1.10 INFOLADA_IP=81.23.191.220 LAN_SUBNET=192.168.1.0/24 bash setup_firewall.sh

set -e

ICS_SERVER_IP="${ICS_SERVER_IP:-}"
INFOLADA_IP="${INFOLADA_IP:-81.23.191.220}"
LAN_SUBNET="${LAN_SUBNET:-192.168.1.0/24}"

echo "================================================"
echo " Vikingi — настройка UFW (firewall)"
echo "================================================"
echo ""
echo " ICS_SERVER_IP = ${ICS_SERVER_IP:-НЕ ЗАДАН}"
echo " INFOLADA_IP   = ${INFOLADA_IP}"
echo " LAN_SUBNET    = ${LAN_SUBNET}"
echo ""

if ! command -v ufw &> /dev/null; then
    echo "ufw не найден. Установите: apt install ufw"
    exit 1
fi

read -p "Применить правила? (y/N) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Отмена."
    exit 0
fi

ufw --force reset
ufw default deny incoming
ufw default allow outgoing

# SSH — всегда открыт
ufw allow 22/tcp comment "SSH"

# PostgreSQL (порт 5433) — только для сервера 1С
if [ -n "$ICS_SERVER_IP" ]; then
    ufw allow from "$ICS_SERVER_IP" to any port 5433 proto tcp comment "PostgreSQL для 1С"
fi
# PostgreSQL из LAN (на случай прямого подключения аналитиков)
ufw allow from "$LAN_SUBNET" to any port 5433 proto tcp comment "PostgreSQL LAN"

# Admin-panel (порт 8000) — из LAN
ufw allow from "$LAN_SUBNET" to any port 8000 proto tcp comment "Admin-panel"

# SIP — Инфолада
ufw allow from "$INFOLADA_IP" to any port 5060 proto udp comment "SIP UDP Infolada"
ufw allow from "$INFOLADA_IP" to any port 5060 proto tcp comment "SIP TCP Infolada"

# RTP — Инфолада
ufw allow from "$INFOLADA_IP" to any port 10000:10100 proto udp comment "RTP Infolada"

# Telegram Bot — исходящий (уже разрешён default allow outgoing)

ufw --force enable
ufw status verbose

echo ""
echo "================================================"
echo " Firewall настроен."
echo "================================================"
echo ""
echo "Открытые порты:"
echo "  22/tcp    — SSH (отовсюду)"
echo "  5433/tcp  — PostgreSQL (1С + LAN)"
echo "  8000/tcp  — Admin-panel (LAN)"
echo "  5060      — SIP (Инфолада)"
echo "  10000-10100/udp — RTP (Инфолада)"
echo ""
