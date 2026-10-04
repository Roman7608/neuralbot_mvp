#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
#  Пайплайн за 21 марта: скачать на HDD аудио > 60 сек → транскрибировать →
#  классифицировать → проставить оценки.
#
#  Использование: ./scripts/pipeline_march21.sh
#
#  Требования:
#    - SpRecord примонтирован на /mnt/sprecord
#    - HDD: AUDIO_CALLS_ROOT (по умолчанию /mnt/audio_calls/calls)
#    - PostgreSQL аналитики на localhost:5433
# ═══════════════════════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")/.."

DATE="2026-03-21"
MIN_DURATION=60

export POSTGRESQL_ANALYTICS_PASSWORD="${POSTGRESQL_ANALYTICS_PASSWORD:-TOP}"
[ -f .env ] && set -a && . .env && set +a

echo "════════════════════════════════════════════════════════════════"
echo "  Пайплайн за $DATE: аудио > ${MIN_DURATION} сек → транскрипция → классификация → оценки"
echo "════════════════════════════════════════════════════════════════"

echo ""
echo "  1. Автозабор с SpRecord (server5), файлы > ${MIN_DURATION} сек на HDD"
echo "────────────────────────────────────────────────────────────────"
python3 call_analytics/autofetch_sprecord.py --date "$DATE"
if [ $? -ne 0 ]; then
    echo "  ❌ Ошибка автозабора. Проверьте монтирование /mnt/sprecord"
    exit 1
fi

echo ""
echo "  2. Транскрипция (GigaAM) + классификация + оценка"
echo "────────────────────────────────────────────────────────────────"
USE_GIGAAM=1 ./run_local.sh python -m call_analytics.retranscribe_reclassify \
  --date-from "$DATE" --date-to "$DATE"

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  ✓ Пайплайн завершён"
echo "════════════════════════════════════════════════════════════════"
