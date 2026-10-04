#!/usr/bin/env bash
# Остановка стека Docker Compose перед выключением сервера (ярлык на рабочем столе).
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "═══════════════════════════════════════════════════════════"
echo "  Vikingi — остановка стека перед выключением сервера"
echo "═══════════════════════════════════════════════════════════"
echo ""

if ! docker info >/dev/null 2>&1; then
  echo "Ошибка: Docker недоступен (демон не запущен или нет прав)."
  echo "Пользователь должен входить в группу docker или использовать sudo systemctl start docker"
  exit 1
fi

/usr/bin/docker compose down

echo ""
echo "Готово: контейнеры остановлены. Можно выключать сервер."
if command -v notify-send >/dev/null 2>&1; then
  notify-send -a Vikingi "Vikingi" "Docker Compose остановлен. Можно выключать сервер." 2>/dev/null || true
fi

if [ -t 0 ]; then
  echo ""
  read -r -p "Нажмите Enter для закрытия окна... " _
fi
