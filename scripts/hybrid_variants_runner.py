#!/usr/bin/env python3
"""
Перебор гибридных STT-вариантов для одного звонка.

Гибрид:
- CTC: первые 5 секунд,
- RNNT: с 4-й секунды (overlap 1 сек),
- склейка с дедупом по словам.

Что варьируется:
- пауза перед распознаванием (warmup),
- первый чанк GigaAM,
- перекрытие чанков GigaAM.

Назначение:
- подобрать вариант, который лучше вытаскивает стартовую фразу.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import soundfile as sf

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def _normalize_word(word: str) -> str:
    w = (word or "").lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", "", w)


def _dedup_join_texts(left: str, right: str, max_overlap_words: int = 16) -> str:
    left = (left or "").strip()
    right = (right or "").strip()
    if not left:
        return right
    if not right:
        return left

    lw = left.split()
    rw = right.split()
    if not lw or not rw:
        return f"{left} {right}".strip()

    lk = [_normalize_word(x) for x in lw]
    rk = [_normalize_word(x) for x in rw]
    max_n = min(max_overlap_words, len(lw), len(rw))
    trim = 0
    for n in range(max_n, 0, -1):
        if lk[-n:] == rk[:n]:
            trim = n
            break
    return " ".join(lw + rw[trim:]).strip()


def _score_intro(intro: str) -> float:
    low = intro.lower().replace("ё", "е")
    score = 0.0
    if "официальный дилер чери тенет" in low:
        score += 3.0
    if "администратор диана" in low:
        score += 2.0
    for kw in ("дилер", "чери", "тенет", "администратор", "диана"):
        if kw in low:
            score += 0.6
    # Небольшой штраф за визуальный мусор в начале
    score -= low[:120].count("?") * 0.1
    return round(score, 3)


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


def _mono_audio(path: Path) -> Tuple[np.ndarray, int]:
    audio, sr = sf.read(str(path))
    if audio.ndim == 2:
        audio = (audio[:, 0].astype(float) + audio[:, 1].astype(float)) / 2.0
    elif audio.ndim > 1:
        audio = audio[:, 0]
    return np.asarray(audio, dtype=np.float32), sr


def _set_env(tmp: Dict[str, str]) -> Dict[str, str | None]:
    prev: Dict[str, str | None] = {}
    for k, v in tmp.items():
        prev[k] = os.environ.get(k)
        os.environ[k] = v
    return prev


def _restore_env(prev: Dict[str, str | None]) -> None:
    for k, v in prev.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def main() -> int:
    parser = argparse.ArgumentParser(description="Гибридный перебор вариантов STT")
    parser.add_argument("--call-id", type=int, required=True, help="ID звонка")
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Путь JSON с результатами (по умолчанию logs/hybrid_variants_<id>.json)",
    )
    args = parser.parse_args()

    from analyze_call_quality import load_gigaam_model_from_path, transcribe_audio_gigaam

    wav = _resolve_wav(args.call_id)
    audio, sr = _mono_audio(wav)
    duration = len(audio) / sr
    if duration < 7.0:
        raise RuntimeError("Слишком короткий файл для гибрида (нужно > 7 сек)")

    # Базовая схема гибрида
    split_sec = 5.0
    overlap_sec = 1.0
    rnnt_start_sec = split_sec - overlap_sec
    ctc_audio = audio[: int(split_sec * sr)]
    rnnt_audio = audio[int(rnnt_start_sec * sr) :]

    fd1, p1 = tempfile.mkstemp(suffix=".wav")
    fd2, p2 = tempfile.mkstemp(suffix=".wav")
    os.close(fd1)
    os.close(fd2)
    sf.write(p1, ctc_audio, sr)
    sf.write(p2, rnnt_audio, sr)

    ctc_model = load_gigaam_model_from_path(Path("models/gigaam-v3"))
    rnnt_model = load_gigaam_model_from_path(Path("models/gigaam-v3-e2e_rnnt"))

    variants: List[Dict] = [
        {"name": "v01", "warmup": 0.0, "first_chunk": 6.0, "chunk_overlap": 1.0},
        {"name": "v02", "warmup": 0.0, "first_chunk": 8.0, "chunk_overlap": 1.0},
        {"name": "v03", "warmup": 0.0, "first_chunk": 10.0, "chunk_overlap": 1.0},
        {"name": "v04", "warmup": 0.3, "first_chunk": 6.0, "chunk_overlap": 1.0},
        {"name": "v05", "warmup": 0.3, "first_chunk": 8.0, "chunk_overlap": 1.5},
        {"name": "v06", "warmup": 0.3, "first_chunk": 10.0, "chunk_overlap": 1.5},
        {"name": "v07", "warmup": 0.5, "first_chunk": 6.0, "chunk_overlap": 1.5},
        {"name": "v08", "warmup": 0.5, "first_chunk": 8.0, "chunk_overlap": 1.5},
        {"name": "v09", "warmup": 0.5, "first_chunk": 8.0, "chunk_overlap": 2.0},
        {"name": "v10", "warmup": 0.5, "first_chunk": 10.0, "chunk_overlap": 2.0},
        # Aggressive set: longer first chunk + higher overlap for stubborn long greetings
        {"name": "v11", "warmup": 0.5, "first_chunk": 12.0, "chunk_overlap": 2.0},
        {"name": "v12", "warmup": 0.5, "first_chunk": 14.0, "chunk_overlap": 2.0},
        {"name": "v13", "warmup": 0.5, "first_chunk": 12.0, "chunk_overlap": 2.5},
        {"name": "v14", "warmup": 0.5, "first_chunk": 14.0, "chunk_overlap": 2.5},
        {"name": "v15", "warmup": 0.8, "first_chunk": 10.0, "chunk_overlap": 2.0},
        {"name": "v16", "warmup": 0.8, "first_chunk": 12.0, "chunk_overlap": 2.0},
        {"name": "v17", "warmup": 0.8, "first_chunk": 14.0, "chunk_overlap": 2.0},
        {"name": "v18", "warmup": 0.8, "first_chunk": 12.0, "chunk_overlap": 2.5},
        {"name": "v19", "warmup": 1.0, "first_chunk": 12.0, "chunk_overlap": 2.5},
        {"name": "v20", "warmup": 1.0, "first_chunk": 14.0, "chunk_overlap": 3.0},
    ]

    results: List[Dict] = []
    try:
        for v in variants:
            env_patch = {
                "GIGAAM_CHUNK_FIRST_SEC": str(v["first_chunk"]),
                "GIGAAM_CHUNK_OVERLAP_SEC": str(v["chunk_overlap"]),
            }
            prev = _set_env(env_patch)
            try:
                # CTC-часть
                os.environ["GIGAAM_WARMUP_SEC"] = str(v["warmup"])
                ctc_norm, _, ctc_raw = transcribe_audio_gigaam(
                    Path(p1),
                    ctc_model,
                    normalize_with_llm=False,
                    apply_domain_normalization=False,
                    _allow_transfer_split=False,
                    _allow_combo=False,
                )
                # RNNT-часть (без warmup)
                os.environ["GIGAAM_WARMUP_SEC"] = "0.0"
                rnnt_norm, _, rnnt_raw = transcribe_audio_gigaam(
                    Path(p2),
                    rnnt_model,
                    normalize_with_llm=False,
                    apply_domain_normalization=False,
                    _allow_transfer_split=False,
                    _allow_combo=False,
                )
            finally:
                _restore_env(prev)

            combo_norm = _dedup_join_texts(ctc_norm, rnnt_norm, max_overlap_words=16)
            combo_raw = _dedup_join_texts(ctc_raw, rnnt_raw, max_overlap_words=16)
            intro = combo_norm[:220]
            score = _score_intro(intro)
            row = {
                **v,
                "intro_score": score,
                "intro_preview": intro,
                "combo_text": combo_norm,
                "combo_raw": combo_raw,
            }
            results.append(row)
            print(
                f"[{v['name']}] warmup={v['warmup']} first={v['first_chunk']} ov={v['chunk_overlap']} score={score} | {intro[:120]}",
                flush=True,
            )
    finally:
        Path(p1).unlink(missing_ok=True)
        Path(p2).unlink(missing_ok=True)

    results_sorted = sorted(results, key=lambda x: x["intro_score"], reverse=True)
    out = {
        "call_id": args.call_id,
        "wav": str(wav),
        "target_phrase": "Официальный дилер Чери Тенет, администратор Диана",
        "split_sec": split_sec,
        "overlap_sec": overlap_sec,
        "results": results_sorted,
    }

    out_path = args.json_out or (PROJECT_DIR / "logs" / f"hybrid_variants_{args.call_id}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {out_path}", flush=True)

    print("\nTOP-3:", flush=True)
    for r in results_sorted[:3]:
        print(
            f"- {r['name']}: score={r['intro_score']} warmup={r['warmup']} first={r['first_chunk']} ov={r['chunk_overlap']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

