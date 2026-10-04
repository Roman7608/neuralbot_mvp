#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  Vikingi — Управление проектом
# ═══════════════════════════════════════════════════════════════

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

show_status() {
    echo ""
    echo -e "${CYAN}═══ Статус контейнеров ═══${NC}"
    echo ""
    docker compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}" 2>/dev/null
    RUNNING=$(docker compose ps --format '{{.Status}}' 2>/dev/null | grep -c "Up")
    TOTAL=$(docker compose ps --format '{{.Name}}' 2>/dev/null | wc -l)
    echo ""
    echo -e "  Запущено: ${GREEN}${RUNNING}${NC} из ${TOTAL}"
    echo ""
}

do_start() {
    echo ""
    echo -e "${CYAN}═══ Запуск проекта ═══${NC}"
    echo ""
    docker compose up -d 2>&1
    sleep 3
    show_status
}

do_stop() {
    echo ""
    echo -e "${YELLOW}═══ Остановка проекта ═══${NC}"
    echo ""
    docker compose down 2>&1
    echo ""
    echo -e "${GREEN}✓${NC} Проект остановлен"
    echo ""
}

do_restart() {
    echo ""
    echo -e "${CYAN}═══ Перезапуск проекта ═══${NC}"
    echo ""
    docker compose restart 2>&1
    sleep 3
    show_status
}

# Перезапуск отдельных сервисов: ./vikingi_manage.sh restart-one admin-panel voice-bot ...
do_restart_one() {
    shift
    if [ $# -eq 0 ]; then
        echo ""
        echo -e "${YELLOW}Укажите сервисы:${NC} ./vikingi_manage.sh restart-one admin-panel"
        echo "  admin-panel  voice-bot  telegram-bot  max-bot  asterisk  stt-service  tts-service  postgres  redis"
        echo "  (telegram-bot: docker compose --profile telegram …)"
        echo ""
        return 1
    fi
    echo ""
    echo -e "${CYAN}═══ Перезапуск: $* ═══${NC}"
    echo ""
    for svc in "$@"; do
        if [ "$svc" = "telegram-bot" ]; then
            docker compose --profile telegram restart telegram-bot 2>&1
        else
            docker compose restart "$svc" 2>&1
        fi
    done
    sleep 2
    show_status
}

do_logs() {
    echo ""
    echo -e "${CYAN}═══ Выберите сервис для просмотра логов ═══${NC}"
    echo ""
    echo "  1) admin-panel    — Админ-панель"
    echo "  2) telegram-bot   — Telegram-бот"
    echo "  3) voice-bot      — Голосовой бот"
    echo "  4) max-bot        — MAX-бот (long polling)"
    echo "  5) asterisk       — SIP-телефония"
    echo "  6) stt-service    — Распознавание речи"
    echo "  7) tts-service    — Синтез речи"
    echo "  8) postgres       — База данных"
    echo "  9) redis          — Очереди"
    echo " 10) ВСЕ сервисы"
    echo "  0) Назад"
    echo ""
    read -p "  Номер: " LOG_CHOICE

    local service=""
    case "$LOG_CHOICE" in
        1) service="admin-panel" ;;
        2) service="telegram-bot" ;;
        3) service="voice-bot" ;;
        4) service="max-bot" ;;
        5) service="asterisk" ;;
        6) service="stt-service" ;;
        7) service="tts-service" ;;
        8) service="postgres" ;;
        9) service="redis" ;;
        10) service="" ;;
        0) return ;;
        *) echo "  Неверный выбор"; return ;;
    esac

    echo ""
    echo -e "  ${YELLOW}Последние 50 строк (Ctrl+C — выход):${NC}"
    echo ""

    if [ -z "$service" ]; then
        docker compose logs --tail=50 -f 2>&1
    elif [ "$service" = "telegram-bot" ]; then
        docker compose --profile telegram logs --tail=50 -f telegram-bot 2>&1
    else
        docker compose logs --tail=50 -f "$service" 2>&1
    fi
}

do_check() {
    echo ""
    echo -e "${CYAN}═══ Проверка сервисов ═══${NC}"
    echo ""

    if curl -sf http://localhost:8000 > /dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} Админ-панель (порт 8000)"
    else
        echo -e "  ${RED}✗${NC} Админ-панель — не отвечает"
    fi

    if curl -sf http://localhost:8000/api/license > /dev/null 2>&1; then
        LICENSE=$(curl -sf http://localhost:8000/api/license 2>/dev/null)
        echo -e "  ${GREEN}✓${NC} Лицензия: $LICENSE"
    else
        echo -e "  ${YELLOW}!${NC} Лицензия — не удалось проверить"
    fi

    BOT_STATUS=$(docker compose --profile telegram ps telegram-bot --format '{{.Status}}' 2>/dev/null)
    if echo "$BOT_STATUS" | grep -q "Up"; then
        echo -e "  ${GREEN}✓${NC} Telegram-бот"
    else
        echo -e "  ${YELLOW}!${NC} Telegram-бот — выключен по умолчанию (compose profile \`telegram\`)"
    fi

    AST_STATUS=$(docker compose ps asterisk --format '{{.Status}}' 2>/dev/null)
    if echo "$AST_STATUS" | grep -q "Up"; then
        echo -e "  ${GREEN}✓${NC} Asterisk — $AST_STATUS"
        AST_CID=$(docker compose ps -q asterisk 2>/dev/null | head -1)
        SIP_REG=""
        [ -n "$AST_CID" ] && SIP_REG=$(docker exec "$AST_CID" asterisk -rx "pjsip show registrations" 2>/dev/null | grep -i "Registered" || true)
        if [ -n "$SIP_REG" ]; then
            echo -e "  ${GREEN}✓${NC} SIP у Инфолады: Registered"
        else
            echo -e "  ${YELLOW}!${NC} SIP не Registered — ./vikingi_manage.sh sip-fix"
        fi
    else
        echo -e "  ${RED}✗${NC} Asterisk — не запущен"
    fi

    if ping -c1 -W2 8.8.8.8 > /dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} Интернет"
    else
        echo -e "  ${RED}✗${NC} Интернет — нет доступа"
    fi

    echo ""
}

run_predeploy_python_check() {
    echo ""
    echo -e "${CYAN}═══ Преддеплойная проверка Python-синтаксиса ═══${NC}"
    echo ""
    python3 - <<'PY'
import py_compile
from pathlib import Path
import sys

root = Path(".")
skip_parts = {
    ".git",
    ".venv",
    ".venv-wav-gen",
    "__pycache__",
    "node_modules",
}
files = []
for p in root.rglob("*.py"):
    if any(part in skip_parts for part in p.parts):
        continue
    files.append(p)

errors = []
for p in sorted(files):
    try:
        py_compile.compile(str(p), doraise=True)
    except py_compile.PyCompileError as exc:
        errors.append((str(p), str(exc).strip()))

if errors:
    print(f"Найдено синтаксических ошибок: {len(errors)}")
    for path, err in errors[:20]:
        print(f"- {path}")
        print(f"  {err}")
    if len(errors) > 20:
        print(f"... и еще {len(errors) - 20}")
    sys.exit(1)

print(f"Проверено Python-файлов: {len(files)}")
print("Синтаксических ошибок не найдено.")
PY
    local rc=$?
    if [ $rc -ne 0 ]; then
        echo ""
        echo -e "  ${RED}✗${NC} Преддеплойная проверка не пройдена. Сборка отменена."
        echo ""
        return 1
    fi
    echo ""
    echo -e "  ${GREEN}✓${NC} Преддеплойная проверка пройдена"
    echo ""
    return 0
}

do_build() {
    echo ""
    echo -e "${CYAN}═══ Пересборка контейнеров ═══${NC}"
    echo ""
    echo "  Это может занять 5-15 минут..."
    echo ""
    if ! run_predeploy_python_check; then
        return 1
    fi
    docker compose build 2>&1
    echo ""
    echo -e "${GREEN}✓${NC} Сборка завершена"
    echo ""
    read -p "  Запустить обновлённые контейнеры? (y/N): " DO_UP
    if [[ "$DO_UP" =~ ^[Yy]$ ]]; then
        docker compose up -d 2>&1
        sleep 3
        show_status
    fi
}

do_run_local() {
    echo ""
    echo -e "${CYAN}═══ Запуск скриптов на хосте (БД Docker: localhost:5433) ═══${NC}"
    echo ""
    echo "  Используется ./run_local.sh для подключения к той же БД, что и админка."
    echo ""
    echo "  1) Транскрибация (retranscribe) — указать даты"
    echo "  2) Создать пользователя админки"
    echo "  3) Показать пример команды"
    echo "  0) Назад"
    echo ""
    read -p "  Выбор: " RL_CHOICE
    case "$RL_CHOICE" in
        1)
            read -p "  Дата от (YYYY-MM-DD): " DF
            read -p "  Дата до (YYYY-MM-DD): " DT
            [ -z "$DF" ] && DF="2026-03-06"
            [ -z "$DT" ] && DT="$DF"
            read -p "  GigaAM? (y/N): " GIGA
            export_cmd=""
            [[ "$GIGA" =~ ^[Yy]$ ]] && export_cmd="USE_GIGAAM=1 "
            echo ""
            echo "  Запуск: ${export_cmd}./run_local.sh python -m call_analytics.retranscribe_reclassify --date-from $DF --date-to $DT"
            echo ""
            ${export_cmd}./run_local.sh python -m call_analytics.retranscribe_reclassify --date-from "$DF" --date-to "$DT"
            ;;
        2)
            echo ""
            echo "  Создание пользователя admin с паролем secret, роль 6 (полный доступ)"
            docker compose exec admin-panel python -m admin_panel.create_admin_user admin secret 6
            ;;
        3)
            echo ""
            echo "  Примеры:"
            echo "    ./run_local.sh python -m call_analytics.retranscribe_reclassify --date-from 2026-03-07 --date-to 2026-03-07"
            echo "    USE_GIGAAM=1 ./run_local.sh python -m call_analytics.retranscribe_reclassify --date-from 2026-03-07"
            echo ""
            read -p "  Нажмите Enter..."
            ;;
        0) return ;;
        *) echo -e "  ${RED}Неверный выбор${NC}" ;;
    esac
}

do_sip_status() {
    echo ""
    echo -e "${CYAN}═══ SIP Asterisk → Инфолада ═══${NC}"
    echo ""
    AST_CID=$(docker compose ps -q asterisk 2>/dev/null | head -1)
    if [ -z "$AST_CID" ]; then
        echo -e "  ${RED}✗${NC} Контейнер asterisk не запущен"
        echo ""
        return 1
    fi
    docker compose ps asterisk --format "  Контейнер: {{.Name}}  {{.Status}}" 2>/dev/null
    echo ""
    docker exec "$AST_CID" asterisk -rx "pjsip show registrations" 2>/dev/null
    echo ""
    if docker exec "$AST_CID" asterisk -rx "pjsip show registrations" 2>/dev/null | grep -q Registered; then
        echo -e "  ${GREEN}✓${NC} Registered — входящие на бота должны проходить (если маршрут DID в Инфоладе верный)"
    else
        echo -e "  ${YELLOW}!${NC} Не Registered — ./vikingi_manage.sh sip-fix"
    fi
    echo ""
}

do_sip_fix() {
    echo ""
    echo -e "${CYAN}═══ Восстановление SIP ═══${NC}"
    echo ""
    bash "$PROJECT_DIR/scripts/ensure_asterisk_sip_registration.sh"
    echo ""
    do_sip_status
}

# ═══════════════════════════════════════════════════════════════

# Быстрый запуск: ./vikingi_manage.sh start
# Перезапуск выбранных сервисов: ./vikingi_manage.sh restart-one admin-panel voice-bot
# SIP: ./vikingi_manage.sh sip-status | sip-fix
case "${1:-}" in
    start) do_start; exit 0 ;;
    stop)  do_stop; exit 0 ;;
    restart) do_restart; exit 0 ;;
    restart-one) do_restart_one "$@"; exit 0 ;;
    check) do_check; exit 0 ;;
    sip-status) do_sip_status; exit 0 ;;
    sip-fix) do_sip_fix; exit 0 ;;
esac

while true; do
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo -e "  ${CYAN}Vikingi — Управление проектом${NC}"
    echo "═══════════════════════════════════════════════════════════"
    echo ""
    echo "  1) Запустить проект"
    echo "  2) Остановить проект"
    echo "  3) Перезапустить проект (все сервисы)"
    echo "  3r) Перезапуск отдельных сервисов (ввести: 3r)"
    echo "  4) Статус контейнеров"
    echo "  5) Проверка сервисов"
    echo "  6) Логи сервисов"
    echo "  7) Пересборка (после обновлений)"
    echo "  8) Запуск скриптов на хосте (retranscribe, create_admin_user)"
    echo ""
    echo "  0) Выход"
    echo ""
    read -p "  Выберите действие: " CHOICE

    case "$CHOICE" in
        1) do_start ;;
        2) do_stop ;;
        3) do_restart ;;
        3r)
            echo ""
            echo "  Сервисы через пробел, например: admin-panel voice-bot max-bot"
            read -p "  Имена сервисов: " RSVC
            [ -n "$RSVC" ] && do_restart_one restart-one $RSVC
            ;;
        4) show_status ;;
        5) do_check ;;
        6) do_logs ;;
        7) do_build ;;
        8) do_run_local ;;
        0) echo ""; exit 0 ;;
        *) echo -e "  ${RED}Неверный выбор${NC}" ;;
    esac
done
