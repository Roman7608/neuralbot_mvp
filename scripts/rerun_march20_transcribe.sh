#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
#  Удаление транскрипций за 20 марта и повторный прогон: транскрипция
#  (GigaAM + 0.5 сек warmup), классификация, оценка.
#  Файлы и записи calls сохраняются — только перезаписываем текст и оценки.
#
#  Использование: ./scripts/rerun_march20_transcribe.sh
# ═══════════════════════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")/.."

DATE="2026-03-20"
export POSTGRESQL_ANALYTICS_PASSWORD="${POSTGRESQL_ANALYTICS_PASSWORD:-TOP}"
[ -f .env ] && set -a && . .env && set +a

echo "════════════════════════════════════════════════════════════════"
echo "  1. Удаление транскрипций и оценок за $DATE"
echo "════════════════════════════════════════════════════════════════"
PGPASSWORD="$POSTGRESQL_ANALYTICS_PASSWORD" psql -h localhost -p 5433 -U analytics_user -d vikingi_analytics -v ON_ERROR_STOP=1 -c "
  DELETE FROM call_quality_scores WHERE call_id IN (SELECT id FROM calls WHERE call_date = '$DATE');
  DELETE FROM call_transcriptions WHERE call_id IN (SELECT id FROM calls WHERE call_date = '$DATE');
  UPDATE calls SET status = 'pending', department = 'OTHER', call_type = 'OTHER' WHERE call_date = '$DATE';
"
echo "  ✓ Готово"

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  2. Транскрипция (GigaAM + 0.5 сек warmup) + классификация + оценка"
echo "════════════════════════════════════════════════════════════════"
USE_GIGAAM=1 ./run_local.sh python -m call_analytics.retranscribe_reclassify \
  --date-from "$DATE" --date-to "$DATE"

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  ✓ Готово"
echo "════════════════════════════════════════════════════════════════"
