#!/usr/bin/env bash
# Последовательно: варианты primary и backup для POST /transcribe, общий TSV и сводка.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${STT_BENCH_OUT:-$ROOT}"
STT_BASE="${STT_URL:-http://127.0.0.1:8001}"
STAMP="$(date +%Y%m%d_%H%M%S)"
COMBINED="$OUT_DIR/stt_primary_backup_${STAMP}.tsv"
LOG="$OUT_DIR/stt_primary_backup_${STAMP}.log"

{
  echo "=== STT benchmark: primary + backup ($STAMP) ==="
  echo "STT_BASE=$STT_BASE"
  echo

  for SZ in primary backup; do
    echo "--- STT_TRANSCRIBE_VARIANT=$SZ ---"
    STT_TRANSCRIBE_VARIANT="$SZ" STT_URL="$STT_BASE" bash "$ROOT/stt_compare_whisper_transcribe.sh" 2>&1 | tee "/tmp/stt_${SZ}_$$.log" || true
    TSV=$(grep -F 'Полная таблица:' "/tmp/stt_${SZ}_$$.log" | tail -1 | sed 's/.*: //')
    if [[ -f "$TSV" ]]; then
      echo "engine\tstt_${SZ}" >> "$COMBINED"
      awk -v e="stt_${SZ}" 'BEGIN{FS=OFS="\t"} {print e,$0}' "$TSV" >> "$COMBINED"
      echo >> "$COMBINED"
    fi
  done

  echo "=== Сводка по движку и гипотезе (склеено) ==="
  python3 - "$COMBINED" << 'PY'
import re, sys
from collections import Counter, defaultdict
path = sys.argv[1]
hall = re.compile(r"продолжение|субтитры|dimatorzok", re.I)
ok = re.compile(r"^(да|согласна|согласен|хорошо)\.?$", re.I)

def norm(t):
    t = (t or "").strip().lower()
    t = re.sub(r"[^\wа-яё]+", "", t, flags=re.I)
    return t

def bucket(text):
    t = (text or "").strip()
    if not t: return "empty"
    if hall.search(t): return "halluc"
    n = norm(t)
    if ok.match(n) or n in ("да", "согласна", "согласен", "хорошо"): return "ok"
    if n.startswith("да") and len(n) <= 4: return "ok"
    return "other"

rows = []
for line in open(path, encoding="utf-8"):
    line = line.rstrip("\n\r")
    if not line or line.startswith("engine"):
        continue
    p = line.split("\t", 3)
    if len(p) < 3:
        continue
    eng, hyp, fn = p[0], p[1], p[2]
    text = p[3] if len(p) > 3 else ""
    rows.append((eng, hyp, bucket(text)))

by = defaultdict(Counter)
for eng, hyp, b in rows:
    by[(eng, hyp)][b] += 1

engines = sorted({e for e, _ in by.keys()})
hyps = ["H0_base","H1_prompt","H2_loud8db","H3_trim","H4_loudtrim+prompt"]
print("engine\thypothesis\tok\tempty\thalluc\tother")
for e in engines:
    for h in hyps:
        c = by.get((e, h), Counter())
        print(f"{e}\t{h}\t{c['ok']}\t{c['empty']}\t{c['halluc']}\t{c['other']}")
PY

  echo
  echo "Объединённый TSV: $COMBINED"
} 2>&1 | tee "$LOG"
