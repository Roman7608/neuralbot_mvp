#!/usr/bin/env python3
"""
Тестовый прогон STT для звонков с переводом и гудками (ID1676 и др.):
после «оставайтесь на линии» часто пропадает второе «Здравствуйте, Чери Викинги…, стажер Дарья».

Перебирает комбинации env (GigaAM: COMBO, SPLIT, STRIP, пороги) и опционально препроцессинг
(mask_transfer_tones) / сегментацию по тонам (как в analyze_op_mono).

Пример (server7, модель и БД на месте):
  USE_LLM_NORMALIZE=0 USE_LLM=0 \\
    python3 scripts/stt_transfer_tone_experiment.py --call-id 1676

С JSON:
  python3 scripts/stt_transfer_tone_experiment.py --call-id 1676 --json-out logs/stt_exp_1676.json

Опция --tone-segments — отдельный путь: нарезка по detect_tonal_segments + GigaAM по кускам
(медленнее, но иногда спасает приветствие после мелодии).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

os.environ.setdefault("USE_LLM_NORMALIZE", "0")
os.environ.setdefault("USE_LLM", "0")


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


def _mono_mix_to_temp(wav_path: Path) -> Tuple[Path, bool]:
    """Стерео → моно (L+R)/2, как в transcribe_audio_gigaam (оба канала в аналитике)."""
    audio, sr = sf.read(str(wav_path))
    tmp = Path(tempfile.mkstemp(suffix=".wav")[1])
    if audio.ndim == 2 and audio.shape[1] >= 2:
        mono = (audio[:, 0].astype(float) + audio[:, 1].astype(float)) / 2.0
        sf.write(str(tmp), mono, sr)
        return tmp, False
    sf.write(str(tmp), audio if audio.ndim == 1 else audio[:, 0], sr)
    return tmp, False


def _apply_mask_mono(mono_wav: Path, soft: bool = False) -> Path:
    from transfer_tone_mask import mask_transfer_tones

    audio, sr = sf.read(str(mono_wav))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if soft:
        masked = mask_transfer_tones(
            audio,
            sr,
            freq_lo=200.0,
            freq_hi=700.0,
            concentration_threshold=0.40,
            band_energy_ratio_min=0.06,
            min_tonal_sec=0.12,
        )
    else:
        masked = mask_transfer_tones(audio, sr)
    out = Path(tempfile.mkstemp(suffix=".wav")[1])
    sf.write(str(out), masked, sr)
    return out


def _set_env(updates: Dict[str, str]) -> Dict[str, Optional[str]]:
    prev: Dict[str, Optional[str]] = {}
    for k, v in updates.items():
        prev[k] = os.environ.get(k)
        os.environ[k] = v
    return prev


def _restore_env(prev: Dict[str, Optional[str]]) -> None:
    for k, v in prev.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _score_second_greeting(text: str) -> Dict[str, Any]:
    """Эвристика: после маркера перевода есть ли повторное приветствие / стажёр."""
    low = (text or "").lower().replace("ё", "е")
    markers = (
        "оставайтесь на линии",
        "оставайтесь на линии.",
        "остаитесь на линии",
        "ожидайте на линии",
        "переведу",
        "переключаю",
    )
    pos = -1
    hit = ""
    for m in markers:
        p = low.find(m)
        if p >= 0 and p > pos:
            pos = p
            hit = m
    if pos < 0:
        return {
            "after_transfer_marker": False,
            "marker": None,
            "second_greeting_ok": None,
            "stazhyr_mentions": low.count("стажер") + low.count("стажёр"),
        }
    tail = low[pos + len(hit) :]
    second_ok = (
        ("здравствуйте" in tail and ("стажер" in tail or "дарья" in tail))
        or ("чери" in tail and "викинг" in tail and "застав" in tail)
    )
    return {
        "after_transfer_marker": True,
        "marker": hit,
        "second_greeting_ok": second_ok,
        "stazhyr_mentions": low.count("стажер") + low.count("стажёр"),
        "tail_240_chars": tail[:240].replace("\n", " "),
    }


# id -> (описание, env-патч, preprocess: None | "mask" | "mask_soft", use_tone_segments_gigaam)
VARIANTS: List[Tuple[str, str, Dict[str, str], Optional[str], bool]] = [
    ("01_default", "как в проде (COMBO/SPLIT по умолчанию)", {}, None, False),
    ("02_combo_off", "без COMBO STT", {"GIGAAM_COMBO_ENABLED": "0"}, None, False),
    ("03_no_split", "COMBO off, без разреза по гудкам", {"GIGAAM_COMBO_ENABLED": "0", "GIGAAM_SPLIT_ON_TRANSFER_TONES": "0"}, None, False),
    ("04_strip", "COMBO off, вырез гудков → тишина", {"GIGAAM_COMBO_ENABLED": "0", "GIGAAM_STRIP_TRANSFER_TONES": "1"}, None, False),
    ("05_no_split_strip", "COMBO off, без split + strip", {"GIGAAM_COMBO_ENABLED": "0", "GIGAAM_SPLIT_ON_TRANSFER_TONES": "0", "GIGAAM_STRIP_TRANSFER_TONES": "1"}, None, False),
    ("06_split_min_0.25", "COMBO off, более чувствительный split (короткие тоны)", {"GIGAAM_COMBO_ENABLED": "0", "GIGAAM_SPLIT_TONE_MIN_DURATION_SEC": "0.25"}, None, False),
    ("07_split_min_0.9", "COMBO off, только длинные тоны для split", {"GIGAAM_COMBO_ENABLED": "0", "GIGAAM_SPLIT_TONE_MIN_DURATION_SEC": "0.9"}, None, False),
    ("08_warmup_0", "COMBO off, без прогрева тишиной", {"GIGAAM_COMBO_ENABLED": "0", "GIGAAM_WARMUP_SEC": "0"}, None, False),
    ("09_warmup_2", "COMBO off, прогрев 2 с", {"GIGAAM_COMBO_ENABLED": "0", "GIGAAM_WARMUP_SEC": "2"}, None, False),
    ("10_first_chunk_14", "COMBO off, длиннее первый чанк", {"GIGAAM_COMBO_ENABLED": "0", "GIGAAM_CHUNK_FIRST_SEC": "14"}, None, False),
    ("11_mask_file_no_split", "маска FFT тонов на файле + без split", {"GIGAAM_COMBO_ENABLED": "0", "GIGAAM_SPLIT_ON_TRANSFER_TONES": "0"}, "mask", False),
    ("12_mask_soft_no_split", "мягкая маска + без split", {"GIGAAM_COMBO_ENABLED": "0", "GIGAAM_SPLIT_ON_TRANSFER_TONES": "0"}, "mask_soft", False),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Эксперименты STT после гудков перевода")
    parser.add_argument("--call-id", type=int, required=True)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument(
        "--tone-segments",
        action="store_true",
        help="Дополнительно: вариант analyze_op_mono (GigaAM по сегментам до/после тонов)",
    )
    parser.add_argument(
        "--gigaam-path",
        type=Path,
        default=None,
        help="Папка GigaAM (по умолчанию GIGAAM_MODEL_PATH из config)",
    )
    args = parser.parse_args()

    try:
        from config import GIGAAM_MODEL_PATH
    except ImportError:
        GIGAAM_MODEL_PATH = PROJECT_DIR / "models" / "gigaam-v3"

    giga_dir = Path(args.gigaam_path) if args.gigaam_path else Path(GIGAAM_MODEL_PATH)
    if not (giga_dir / "config.json").exists():
        print(f"Нет GigaAM в {giga_dir}", file=sys.stderr)
        return 1

    from analyze_call_quality import load_gigaam_model_from_path, transcribe_audio_gigaam

    wav = _resolve_wav(args.call_id)
    print(f"call_id={args.call_id} файл={wav}", flush=True)

    model = load_gigaam_model_from_path(giga_dir)
    mono_tmp, _ = _mono_mix_to_temp(wav)
    temp_cleanup = [mono_tmp]

    results: List[Dict[str, Any]] = []

    try:
        variants = list(VARIANTS)
        if args.tone_segments:
            variants.append(
                (
                    "99_tone_segments_gigaam",
                    "нарезка по detect_tonal_segments + GigaAM по кускам (analyze_op_mono)",
                    {"GIGAAM_COMBO_ENABLED": "0", "GIGAAM_SPLIT_ON_TRANSFER_TONES": "0"},
                    None,
                    True,
                )
            )

        for vid, desc, env_patch, preprocess, use_seg in variants:
            path_for_stt = mono_tmp
            extra_cleanup: List[Path] = []
            if preprocess == "mask":
                path_for_stt = _apply_mask_mono(mono_tmp, soft=False)
                extra_cleanup.append(path_for_stt)
            elif preprocess == "mask_soft":
                path_for_stt = _apply_mask_mono(mono_tmp, soft=True)
                extra_cleanup.append(path_for_stt)

            prev = _set_env(env_patch)
            try:
                if use_seg:
                    from analyze_op_mono import _transcribe_by_tone_segments

                    text = _transcribe_by_tone_segments(path_for_stt, model, use_gigaam=True)
                    raw = text
                    segs: List = []
                else:
                    text, segs, raw = transcribe_audio_gigaam(
                        path_for_stt,
                        model,
                        normalize_with_llm=False,
                        apply_domain_normalization=False,
                    )
            finally:
                _restore_env(prev)

            score = _score_second_greeting(text)
            row = {
                "variant_id": vid,
                "description": desc,
                "env": env_patch,
                "preprocess": preprocess,
                "tone_segments": use_seg,
                "text_len": len(text or ""),
                "text_preview_500": (text or "")[:500],
                "full_text": text,
                "score": score,
            }
            results.append(row)
            ok = score.get("second_greeting_ok")
            flag = "✓" if ok else ("?" if ok is None else "✗")
            print(f"\n=== {vid} {flag} ===\n{desc}\nenv={env_patch} pre={preprocess} seg={use_seg}", flush=True)
            print(f"second_greeting_ok={ok} stazhyr_mentions={score.get('stazhyr_mentions')}", flush=True)
            if score.get("after_transfer_marker"):
                print(f"tail: {score.get('tail_240_chars', '')!r}", flush=True)
            print(f"TEXT (800): {(text or '')[:800]!r}", flush=True)

            for p in extra_cleanup:
                try:
                    p.unlink(missing_ok=True)
                except Exception:
                    pass

        out_path = args.json_out or (PROJECT_DIR / "logs" / f"stt_transfer_exp_{args.call_id}.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"call_id": args.call_id, "wav": str(wav), "variants": results}
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON: {out_path}", flush=True)
    finally:
        for p in temp_cleanup:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
