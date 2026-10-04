#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
#  Полный пайплайн: удаление аудио → автозабор 20 марта → транскрипция →
#  классификация → оценка (по новым правилам нормализации).
#  Использование:
#    ./scripts/refetch_march20_pipeline.sh           — только 20 марта
#    ./scripts/refetch_march20_pipeline.sh --all     — удалить ВСЕ аудио с HDD
# ═══════════════════════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")/.."

# Переменные
DATE="2026-03-20"
AUDIO_ROOT="${AUDIO_CALLS_ROOT:-/mnt/audio_calls/calls}"
MIN_DURATION=60
DELETE_ALL=false
[ "$1" = "--all" ] && DELETE_ALL=true

# Пароль БД (TOP)
export POSTGRESQL_ANALYTICS_PASSWORD="${POSTGRESQL_ANALYTICS_PASSWORD:-TOP}"
[ -f .env ] && set -a && . .env && set +a

echo "════════════════════════════════════════════════════════════════"
if $DELETE_ALL; then
    echo "  1. Удаление ВСЕХ аудиофайлов с HDD ($AUDIO_ROOT)"
else
    echo "  1. Удаление аудиофайлов за $DATE с HDD"
fi
echo "════════════════════════════════════════════════════════════════"
if $DELETE_ALL; then
    for d in "$AUDIO_ROOT/auto" "$AUDIO_ROOT/manual" "storage/calls"; do
        if [ -d "$d" ]; then
            rm -rf "$d"/*
            echo "  ✓ Очищено: $d"
        fi
    done
else
    AUTO_DIR="$AUDIO_ROOT/auto/$DATE"
    STORAGE_DIR="storage/calls/auto/$DATE"
    if [ -d "$AUTO_DIR" ]; then
        rm -rf "$AUTO_DIR"
        echo "  ✓ Удалено: $AUTO_DIR"
    fi
    if [ -d "$STORAGE_DIR" ]; then
        rm -rf "$STORAGE_DIR"
        echo "  ✓ Удалено: $STORAGE_DIR"
    fi
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  2. Удаление записей за $DATE из БД"
echo "════════════════════════════════════════════════════════════════"
if $DELETE_ALL; then
    PGPASSWORD="$POSTGRESQL_ANALYTICS_PASSWORD" psql -h localhost -p 5433 -U analytics_user -d vikingi_analytics -v ON_ERROR_STOP=1 -c \
      "DELETE FROM calls;" 2>/dev/null || true
    echo "  ✓ Удалены все записи calls"
else
    PGPASSWORD="$POSTGRESQL_ANALYTICS_PASSWORD" psql -h localhost -p 5433 -U analytics_user -d vikingi_analytics -v ON_ERROR_STOP=1 -c \
      "DELETE FROM calls WHERE call_date = '$DATE';" 2>/dev/null || true
    echo "  ✓ Готово"
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  3. Автозабор с server5 (SpRecord), файлы > ${MIN_DURATION} сек"
echo "════════════════════════════════════════════════════════════════"
python3 call_analytics/autofetch_sprecord.py --date "$DATE"
if [ $? -ne 0 ]; then
    echo "  ❌ Ошибка автозабора. Проверьте монтирование /mnt/sprecord"
    exit 1
fi

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  4. Транскрипция (GigaAM) + классификация + оценка"
echo "════════════════════════════════════════════════════════════════"
USE_GIGAAM=1 ./run_local.sh python -m call_analytics.retranscribe_reclassify \
  --date-from "$DATE" --date-to "$DATE"

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  ✓ Пайплайн завершён"
echo "════════════════════════════════════════════════════════════════"
