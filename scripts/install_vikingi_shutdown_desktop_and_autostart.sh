#!/usr/bin/env bash
# Копирует ярлык на рабочий стол и (с sudo) ставит systemd-автозапуск стека Vikingi.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_SRC="${PROJECT_ROOT}/deploy/systemd/vikingi-compose.service"
UNIT_DST="/etc/systemd/system/vikingi-compose.service"

# Реальный каталог рабочего стола (часто ~/Рабочий стол, а не ~/Desktop)
DESKTOP_REAL=""
if command -v xdg-user-dir >/dev/null 2>&1; then
  DESKTOP_REAL="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
fi
[ -z "${DESKTOP_REAL}" ] && DESKTOP_REAL="${HOME}/Desktop"

echo "═══ Vikingi: ярлык + автозапуск ═══"
echo ""

install_desktop_files_to() {
  local d="$1"
  mkdir -p "$d"
  chmod +x "${PROJECT_ROOT}/scripts/vikingi_stop_before_shutdown.sh"
  chmod +x "${PROJECT_ROOT}/scripts/vikingi_open_home.sh"
  cp -f "${PROJECT_ROOT}/desktop/vikingi-stop-before-shutdown.desktop" "${d}/"
  chmod +x "${d}/vikingi-stop-before-shutdown.desktop"
  cp -f "${PROJECT_ROOT}/desktop/vikingi-open-home.desktop" "${d}/"
  chmod +x "${d}/vikingi-open-home.desktop"
}

install_desktop_files_to "${DESKTOP_REAL}"
echo "Ярлыки скопированы в: ${DESKTOP_REAL}"
# Если Xfce смотрит на другой каталог — дублируем (типичная путаница Desktop vs «Рабочий стол»)
for alt in "${HOME}/Desktop" "${HOME}/Рабочий стол"; do
  case "$alt" in
    "${DESKTOP_REAL}") continue ;;
  esac
  if [ -d "$alt" ] || [ "$alt" = "${HOME}/Desktop" ]; then
    install_desktop_files_to "$alt"
    echo "Дубликат ярлыков: $alt"
  fi
done
echo "  Остановка перед выключением: vikingi-stop-before-shutdown.desktop"
echo "  Домашняя папка (Thunar): vikingi-open-home.desktop"
echo "  (Xfce: при первом запуске может понадобиться «Разрешить запуск» / Mark Executable.)"
echo ""

if [ "${1:-}" = "--desktop-only" ]; then
  echo "Пропуск systemd (--desktop-only)."
  exit 0
fi

if [ "$(id -u)" -eq 0 ]; then
  echo "Запустите без root для копирования на рабочий стол; для systemd используйте sudo внутри скрипта."
  exit 1
fi

echo "Установка systemd-юнита (нужен sudo)..."
sudo cp -f "$UNIT_SRC" "$UNIT_DST"
sudo systemctl daemon-reload
sudo systemctl enable vikingi-compose.service
sudo systemctl start vikingi-compose.service
echo ""
echo "Сервис vikingi-compose включён и запущен."
systemctl --no-pager status vikingi-compose.service || true
echo ""
echo "Проверка контейнеров: cd ${PROJECT_ROOT} && docker compose ps"
