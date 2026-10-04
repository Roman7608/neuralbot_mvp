#!/usr/bin/env bash
set -euo pipefail
DIR="/home/vikingi/VikingiAll/experiments/19-03-2026_08-46-33/wav_8k_mono"
URL="http://127.0.0.1:8001/transcribe/bytes"
WORKDIR="/tmp/stt_ab_$$"
mkdir -p "$WORKDIR"

prep_none()    { echo "$1"; }
prep_loud()    { ffmpeg -y -i "$1" -af "volume=8dB" -ar 8000 -ac 1 -c:a pcm_s16le "$2/out.wav" 2>/dev/null && echo "$2/out.wav"; }
prep_trim()    { ffmpeg -y -i "$1" -af "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB:detection=peak,areverse,silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB:detection=peak,areverse" -ar 8000 -ac 1 -c:a pcm_s16le "$2/out.wav" 2>/dev/null && echo "$2/out.wav"; }
prep_loudtrim(){ ffmpeg -y -i "$1" -af "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB:detection=peak,areverse,silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB:detection=peak,areverse,volume=8dB" -ar 8000 -ac 1 -c:a pcm_s16le "$2/out.wav" 2>/dev/null && echo "$2/out.wav"; }

call_stt() {
  local wav="$1" tag="$2"
  shift 2
  local extra=()
  while [[ $# -gt 0 ]]; do extra+=("$1"); shift; done
  local body
  if ! body=$(curl -sS -X POST "$URL" -F "file=@${wav}" "${extra[@]}"); then
    echo -e "${tag}\t$(basename "$wav")\tCURL_ERR\t"
    return
  fi
  local text
  text=$(echo "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('text','') or '')" 2>/dev/null || echo "JSON_ERR")
  echo -e "${tag}\t$(basename "$wav")\t${text}"
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

  call_stt "$w0"  "H0_base"                    >> "$TSV"
  call_stt "$w0"  "H1_prompt" -F "initial_prompt=Короткие ответы по-русски: да, согласна, хорошо." >> "$TSV"
  call_stt "$wl"  "H2_loud8db"                 >> "$TSV"
  call_stt "$wt"  "H3_trim"                    >> "$TSV"
  call_stt "$wlt" "H4_loudtrim+prompt" -F "initial_prompt=Короткие ответы по-русски: да, согласна, хорошо." >> "$TSV"
done

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
