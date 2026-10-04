#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
#  Ежедневный пайплайн: автозабор из SPRecord + транскрибация + классификация +
#  оценка + очистка старых записей. Запуск по cron в 23:55.
#
#  Требования:
#    - SpRecord примонтирован на /mnt/sprecord
#    - HDD: AUDIO_CALLS_ROOT (/mnt/audio_calls/calls)
#    - PostgreSQL аналитики (localhost:5433 или через .env)
#    - GPU и venv для транскрибации (GigaAM)
# ═══════════════════════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")/.."

export POSTGRESQL_ANALYTICS_PASSWORD="${POSTGRESQL_ANALYTICS_PASSWORD:-TOP}"
[ -f .env ] && set -a && . .env && set +a

mkdir -p logs
LOG_FILE="${VIKINGI_AUTOFETCH_LOG:-$(pwd)/logs/vikingi_autofetch.log}"
DATE=$(date +%Y-%m-%d)

echo "════════════════════════════════════════════════════════════════" | tee -a "$LOG_FILE"
echo "  $(date -Iseconds) — Пайплайн SPRecord" | tee -a "$LOG_FILE"
echo "════════════════════════════════════════════════════════════════" | tee -a "$LOG_FILE"

echo "" | tee -a "$LOG_FILE"
echo "  1. Автозабор с SpRecord за $DATE" | tee -a "$LOG_FILE"
echo "────────────────────────────────────────────────────────────────" | tee -a "$LOG_FILE"
if python3 call_analytics/autofetch_sprecord.py --date "$DATE" 2>&1 | tee -a "$LOG_FILE"; then
    echo "  ✅ Автозабор завершён" | tee -a "$LOG_FILE"
else
    echo "  ❌ Ошибка автозабора. Проверьте /mnt/sprecord" | tee -a "$LOG_FILE"
fi

echo "" | tee -a "$LOG_FILE"
echo "  2. Транскрибация, классификация и оценка за $DATE" | tee -a "$LOG_FILE"
echo "────────────────────────────────────────────────────────────────" | tee -a "$LOG_FILE"
if USE_GIGAAM=1 ./run_local.sh python -m call_analytics.retranscribe_reclassify --date-from "$DATE" --date-to "$DATE" 2>&1 | tee -a "$LOG_FILE"; then
    echo "  ✅ Транскрибация и классификация завершены" | tee -a "$LOG_FILE"
else
    echo "  ⚠️ Ошибка транскрибации/классификации" | tee -a "$LOG_FILE"
fi

echo "" | tee -a "$LOG_FILE"
echo "  3. Очистка записей старше 61 дня" | tee -a "$LOG_FILE"
echo "────────────────────────────────────────────────────────────────" | tee -a "$LOG_FILE"
if python3 scripts/cleanup_old_calls.py --days 61 2>&1 | tee -a "$LOG_FILE"; then
    echo "  ✅ Очистка завершена" | tee -a "$LOG_FILE"
else
    echo "  ⚠️ Ошибка очистки" | tee -a "$LOG_FILE"
fi

echo "" | tee -a "$LOG_FILE"
echo "  ✓ Пайплайн завершён" | tee -a "$LOG_FILE"
echo "" | tee -a "$LOG_FILE"
