#!/usr/bin/env bash
# Тот же набор гипотез (H0–H4), что и stt_compare.sh, но POST /transcribe (корректный разбор WAV).
# Вариант STT для POST /transcribe: STT_TRANSCRIBE_VARIANT=primary|backup
# Совместимость: WHISPER_MODEL_SIZE=medium|large → primary|backup
# Примечание: эндпоинт /transcribe не принимает initial_prompt — H1/H4 совпадают с H0/H4 по запросу (как и в bytes, где prompt игнорировался).
set -euo pipefail
DIR="${STT_WAV_DIR:-/home/vikingi/VikingiAll/experiments/19-03-2026_08-46-33/wav_8k_mono}"
URL="${STT_URL:-http://127.0.0.1:8001}/transcribe"
_raw="${STT_TRANSCRIBE_VARIANT:-${WHISPER_MODEL_SIZE:-primary}}"
case "${_raw}" in
  medium) STT_TRANSCRIBE_VARIANT=primary ;;
  large) STT_TRANSCRIBE_VARIANT=backup ;;
  *) STT_TRANSCRIBE_VARIANT="${_raw}" ;;
esac
WORKDIR="/tmp/stt_${STT_TRANSCRIBE_VARIANT}_$$"
mkdir -p "$WORKDIR"

prep_none()    { echo "$1"; }
prep_loud()    { ffmpeg -y -i "$1" -af "volume=8dB" -ar 8000 -ac 1 -c:a pcm_s16le "$2/out.wav" 2>/dev/null && echo "$2/out.wav"; }
prep_trim()    { ffmpeg -y -i "$1" -af "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB:detection=peak,areverse,silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB:detection=peak,areverse" -ar 8000 -ac 1 -c:a pcm_s16le "$2/out.wav" 2>/dev/null && echo "$2/out.wav"; }
prep_loudtrim(){ ffmpeg -y -i "$1" -af "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB:detection=peak,areverse,silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB:detection=peak,areverse,volume=8dB" -ar 8000 -ac 1 -c:a pcm_s16le "$2/out.wav" 2>/dev/null && echo "$2/out.wav"; }

call_stt() {
  local wav="$1" tag="$2" src_bn="$3"
  shift 3
  local extra=()
  while [[ $# -gt 0 ]]; do extra+=("$1"); shift; done
  local body
  if ! body=$(curl -sS -X POST "$URL" \
      -F "file=@${wav}" \
      -F "model_size=${STT_TRANSCRIBE_VARIANT}" \
      -F "language=ru" \
      -F "priority=batch" \
      "${extra[@]}"); then
    echo -e "${tag}\t${src_bn}\tCURL_ERR"
    return
  fi
  local text
  text=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('text','') or '')" 2>/dev/null || echo "JSON_ERR")
  echo -e "${tag}\t${src_bn}\t${text}"
}

TSV="$WORKDIR/all.tsv"
: > "$TSV"

for f in "$DIR"/*.wav; do
  [[ -e "$f" ]] || { echo "Нет WAV в $DIR"; exit 1; }
  bn=$(basename "$f")
  sub="$WORKDIR/$bn"
  mkdir -p "$sub/loud" "$sub/trim" "$sub/loudtrim"

  w0=$(prep_none "$f")
  wl=$(prep_loud "$f" "$sub/loud")
  wt=$(prep_trim "$f" "$sub/trim")
  wlt=$(prep_loudtrim "$f" "$sub/loudtrim")

  call_stt "$w0"  "H0_base"         "$bn" >> "$TSV"
  call_stt "$w0"  "H1_prompt"       "$bn" >> "$TSV"
  call_stt "$wl"  "H2_loud8db"       "$bn" >> "$TSV"
  call_stt "$wt"  "H3_trim"          "$bn" >> "$TSV"
  call_stt "$wlt" "H4_loudtrim+prompt" "$bn" >> "$TSV"
done

printf 'engine\tstt_%s\n' "${STT_TRANSCRIBE_VARIANT}"
python3 - "$TSV" << 'PY'
import re, sys
path = sys.argv[1]
rows = [line.rstrip("\n").split("\t", 2) for line in open(path, encoding="utf-8") if line.strip()]
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

from collections import Counter, defaultdict
by_hyp = defaultdict(Counter)
for hyp, fn, text in rows:
    by_hyp[hyp][bucket(text)] += 1

order = ["H0_base","H1_prompt","H2_loud8db","H3_trim","H4_loudtrim+prompt"]
print("hypothesis\tok\tempty\thalluc\tother")
for h in order:
    c = by_hyp.get(h, Counter())
    print(f"{h}\t{c['ok']}\t{c['empty']}\t{c['halluc']}\t{c['other']}")
PY

echo "Полная таблица: $TSV"
