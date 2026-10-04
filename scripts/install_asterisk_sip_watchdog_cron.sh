#!/usr/bin/env bash
# Установка cron: проверка SIP каждые 3 минуты (server7).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WATCH_SCRIPT="${PROJECT_DIR}/scripts/ensure_asterisk_sip_registration.sh"
LOG_FILE="${VIKINGI_SIP_WATCH_LOG:-/var/log/vikingi-sip-watch.log}"
chmod +x "${WATCH_SCRIPT}"

if ! touch "${LOG_FILE}" 2>/dev/null; then
  LOG_FILE="${HOME}/vikingi-sip-watch.log"
  touch "${LOG_FILE}"
  echo "Лог без root: ${LOG_FILE}"
fi
CRON_LINE="*/3 * * * * ${WATCH_SCRIPT} >> ${LOG_FILE} 2>&1"

EXISTING="$(crontab -l 2>/dev/null | grep -F 'ensure_asterisk_sip_registration.sh' || true)"
if [[ -n "$EXISTING" ]]; then
  echo "Cron уже установлен:"
  echo "  $EXISTING"
  exit 0
fi

( crontab -l 2>/dev/null; echo "$CRON_LINE" ) | crontab -
echo "Добавлено в crontab пользователя $(whoami):"
echo "  $CRON_LINE"
echo "Лог: ${LOG_FILE}"
