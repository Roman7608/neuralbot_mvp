#!/usr/bin/env bash
# Восстановление SIP-регистрации Asterisk → Инфолада после обрыва сети.
# Cron: */3 * * * * (см. scripts/install_asterisk_sip_watchdog_cron.sh)
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

LOG_TAG="vikingi-sip"
REGISTER_WAIT_SEC="${REGISTER_WAIT_SEC:-8}"
REGISTRATION_NAME="${REGISTRATION_NAME:-infolada-reg}"

log() {
  local msg="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
  echo "$msg"
  logger -t "$LOG_TAG" -- "$*" 2>/dev/null || true
}

asterisk_container_id() {
  docker compose ps -q asterisk 2>/dev/null | head -1
}

asterisk_exec() {
  local cid
  cid="$(asterisk_container_id)"
  if [[ -z "$cid" ]]; then
    return 1
  fi
  docker exec "$cid" asterisk -rx "$1" 2>/dev/null
}

sip_is_registered() {
  local out
  out="$(asterisk_exec "pjsip show registrations" || true)"
  echo "$out" | grep -qE 'infolada-reg/.*Registered|Registered[[:space:]]+\(exp\.'
}

main() {
  local cid
  cid="$(asterisk_container_id)"
  if [[ -z "$cid" ]]; then
    log "asterisk: контейнер не запущен, пропуск"
    exit 1
  fi

  if sip_is_registered; then
    exit 0
  fi

  log "SIP не Registered — pjsip send register ${REGISTRATION_NAME}"
  asterisk_exec "pjsip send register ${REGISTRATION_NAME}" || true
  sleep "$REGISTER_WAIT_SEC"

  if sip_is_registered; then
    log "SIP восстановлен через send register"
    exit 0
  fi

  log "SIP всё ещё не Registered — docker compose restart asterisk"
  docker compose restart asterisk 2>&1 | while read -r line; do log "$line"; done
  sleep 15

  if sip_is_registered; then
    log "SIP восстановлен после restart asterisk"
    exit 0
  fi

  log "ОШИБКА: SIP не Registered после register и restart — проверьте сеть и учётку Инфолады"
  exit 2
}

main "$@"
