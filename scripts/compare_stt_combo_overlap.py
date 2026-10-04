#!/usr/bin/env python3
"""
Комбо-STT для одного звонка:
- CTC на первых N секундах (по умолчанию 5.0),
- RNNT с (N - overlap) секунды до конца (по умолчанию overlap=1.0),
- склейка с дедупликацией дублирующихся слов на стыке.

Пример:
  USE_GIGAAM=1 ./run_local.sh python3 scripts/compare_stt_combo_overlap.py \
    --call-id 1576 \
    --json-out logs/stt_combo_1576_overlap1.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import List

import numpy as np
import soundfile as sf

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def _normalize_word(word: str) -> str:
    w = (word or "").lower().replace("ё", "е")
    w = re.sub(r"[^a-zа-я0-9]+", "", w)
    return w


def _dedup_overlap(left: str, right: str, max_overlap_words: int = 16) -> str:
    if not left:
        return right.strip()
    if not right:
        return left.strip()

    left_words = left.split()
    right_words = right.split()
    if not left_words or not right_words:
        return (left.strip() + " " + right.strip()).strip()

    left_key = [_normalize_word(w) for w in left_words]
    right_key = [_normalize_word(w) for w in right_words]
    max_n = min(max_overlap_words, len(left_words), len(right_words))

    trim = 0
    for n in range(max_n, 0, -1):
        if left_key[-n:] == right_key[:n]:
            trim = n
            break

    merged = left_words + right_words[trim:]
    return " ".join(merged).strip()


def _resolve_wav(call_id: int) -> Path:
    from database.postgresql_manager import CallAnalyticsDB

    row = CallAnalyticsDB.get_call_with_details(call_id)
    if not row or not row.get("file_path"):
        raise RuntimeError(f"Звонок {call_id} не найден или нет file_path")

    fp = Path(row["file_path"])
    if not fp.is_absolute():
        fp = PROJECT_DIR / fp
    if not fp.exists():
        raise FileNotFoundError(f"Файл не найден: {fp}")
    return fp


def _mono_audio(path: Path) -> tuple[np.ndarray, int]:
    audio, sr = sf.read(str(path))
    if audio.ndim == 2:
        audio = audio.mean(axis=1)
    return np.asarray(audio, dtype=np.float32), sr


def main() -> int:
    parser = argparse.ArgumentParser(description="Комбо CTC+RNNT с перекрытием")
    parser.add_argument("--call-id", type=int, required=True, help="ID звонка")
    parser.add_argument("--ctc-seconds", type=float, default=5.0, help="Длина CTC-окна от начала")
    parser.add_argument("--overlap-seconds", type=float, default=1.0, help="Перекрытие RNNT с CTC")
    parser.add_argument("--ctc-model", type=Path, default=Path("models/gigaam-v3"), help="CTC-модель")
    parser.add_argument(
        "--rnnt-model",
        type=Path,
        default=Path("models/gigaam-v3-e2e_rnnt"),
        help="RNNT-модель",
    )
    parser.add_argument("--json-out", type=Path, default=None, help="Путь для JSON результата")
    args = parser.parse_args()

    from analyze_call_quality import load_gigaam_model_from_path, transcribe_audio_gigaam

    wav = _resolve_wav(args.call_id)
    audio, sr = _mono_audio(wav)
    duration = len(audio) / sr

    ctc_end = max(0.5, min(args.ctc_seconds, duration))
    rnnt_start = max(0.0, min(ctc_end - max(0.0, args.overlap_seconds), duration))

    ctc_samples = int(ctc_end * sr)
    rnnt_samples = int(rnnt_start * sr)

    ctc_audio = audio[:ctc_samples]
    rnnt_audio = audio[rnnt_samples:]

    fd1, p1 = tempfile.mkstemp(suffix=".wav")
    fd2, p2 = tempfile.mkstemp(suffix=".wav")
    os.close(fd1)
    os.close(fd2)
    sf.write(p1, ctc_audio, sr)
    sf.write(p2, rnnt_audio, sr)

    ctc_txt = ""
    rnnt_txt = ""
    try:
        ctc = load_gigaam_model_from_path(args.ctc_model)
        rnnt = load_gigaam_model_from_path(args.rnnt_model)

        os.environ["GIGAAM_WARMUP_SEC"] = "0.0"
        ctc_txt, _, _ = transcribe_audio_gigaam(
            Path(p1),
            ctc,
            normalize_with_llm=False,
            apply_domain_normalization=False,
        )

        os.environ["GIGAAM_WARMUP_SEC"] = "1.0"
        rnnt_txt, _, _ = transcribe_audio_gigaam(
            Path(p2),
            rnnt,
            normalize_with_llm=False,
            apply_domain_normalization=False,
        )
    finally:
        Path(p1).unlink(missing_ok=True)
        Path(p2).unlink(missing_ok=True)

    combo = _dedup_overlap(ctc_txt.strip(), rnnt_txt.strip(), max_overlap_words=16)
    result = {
        "call_id": args.call_id,
        "wav": str(wav),
        "ctc_model": str(args.ctc_model),
        "rnnt_model": str(args.rnnt_model),
        "ctc_seconds": ctc_end,
        "overlap_seconds": args.overlap_seconds,
        "rnnt_start_seconds": rnnt_start,
        "ctc_text": ctc_txt,
        "rnnt_text": rnnt_txt,
        "combo_text": combo,
    }

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"JSON: {args.json_out}", flush=True)

    print("\n=== COMBO (CTC + RNNT overlap) ===\n", flush=True)
    print(combo, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

