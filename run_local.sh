#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  Запуск скриптов на хосте с подключением к Docker PostgreSQL
#  Использование: ./run_local.sh python -m call_analytics.retranscribe_reclassify --date-from 2026-03-07
# ═══════════════════════════════════════════════════════════════

cd "$(dirname "$0")"

# Подключение к postgres в Docker (порт 5433 на хосте).
# Пароль по умолчанию TOP — как в postgresql_config и docker-compose (POSTGRES_PASSWORD).
[ -f .env ] && set -a && . .env && set +a
# GigaAM longform (>25 с): чанки и дедуп стыков — дефолты пайплайна (перекрываются .env / export)
export GIGAAM_COMBO_ENABLED="${GIGAAM_COMBO_ENABLED:-0}"
export GIGAAM_SPLIT_ON_TRANSFER_TONES="${GIGAAM_SPLIT_ON_TRANSFER_TONES:-0}"
export GIGAAM_CHUNK_FIRST_SEC="${GIGAAM_CHUNK_FIRST_SEC:-8}"
export GIGAAM_CHUNK_OVERLAP_SEC="${GIGAAM_CHUNK_OVERLAP_SEC:-2}"
export GIGAAM_CHUNK_DEDUP_WORDS="${GIGAAM_CHUNK_DEDUP_WORDS:-12}"
export POSTGRESQL_HOST="${POSTGRESQL_HOST:-localhost}"
export POSTGRESQL_PORT="${POSTGRESQL_PORT:-5433}"
export POSTGRESQL_ANALYTICS_PASSWORD="${POSTGRESQL_ANALYTICS_PASSWORD:-${POSTGRES_PASSWORD:-TOP}}"

# Активация venv при наличии
[ -d "venv" ] && . venv/bin/activate
[ -d ".venv" ] && . .venv/bin/activate

exec "$@"
