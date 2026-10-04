#!/usr/bin/env python3
"""
Сравнение гипотез GigaAM по началу транскрипта (вынесите файл ВНЕ репозитория при желании).

Запуск на сервере (venv + pandas, как у retranscribe):
  cd /path/to/VikingiAll
  ./run_local.sh python3 experiments/gigaam_start_hypotheses_runner.py --project-root /path/to/VikingiAll \\
      --call-ids 1613 1610 1601 --head-only --json-out /tmp/gigaam_hypotheses.json

Или установить зависимости в текущий Python:
  pip install -r requirements-gigaam.txt

Двухпроходка (head + полный файл + склейка):
  ./run_local.sh python3 experiments/gigaam_start_hypotheses_runner.py --project-root /path/to/VikingiAll \\
      --call-ids 1613 1610 1601 --two-pass --head-sec 6 --json-out /tmp/gigaam_two_pass.json

Затем:
  export PYTHONPATH=/path/to/VikingiAll
  python3 experiments/gigaam_start_hypotheses_runner.py --project-root /path/to/VikingiAll \\
      --call-ids 1611 1613 1615

Или по путям к WAV:
  python gigaam_start_hypotheses_runner.py --project-root /path/to/VikingiAll \\
      --wav /data/a.wav /data/b.wav

Подключение к БД — как у админки/ retranscribe (postgresql_config / переменные окружения).

Гипотезы задаются в SCENARIOS ниже. Для каждой: полный прогон transcribe_audio_gigaam
с подстановкой переменных окружения перед вызовом (как в analyze_call_quality.py).

normalize_with_llm=False — сравниваем сырой пайплайн нормализации без LLM (честнее для STT).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --- Сценарии: имя -> патч к базовым переменным (строки) ---
BASE_ENV: Dict[str, str] = {
    "GIGAAM_WARMUP_SEC": "1.0",
    "GIGAAM_FIRST_CHUNK_MAX_SEC": "8",
    "USE_GIGAAM_LONGFORM_VAD": "1",
    "GIGAAM_LONGFORM_OVERLAP_SEC": "2.0",
}

SCENARIOS: List[Tuple[str, Dict[str, str]]] = [
    ("baseline", {}),
    ("warmup_0", {"GIGAAM_WARMUP_SEC": "0"}),
    ("warmup_1_2", {"GIGAAM_WARMUP_SEC": "1.2"}),
    ("warmup_1_5", {"GIGAAM_WARMUP_SEC": "1.5"}),
    ("first_chunk_4", {"GIGAAM_FIRST_CHUNK_MAX_SEC": "4"}),
    ("first_chunk_8", {"GIGAAM_FIRST_CHUNK_MAX_SEC": "8"}),
    ("first_chunk_off", {"GIGAAM_FIRST_CHUNK_MAX_SEC": "0"}),
    ("longform_vad_off", {"USE_GIGAAM_LONGFORM_VAD": "0"}),
    ("overlap_2", {"GIGAAM_LONGFORM_OVERLAP_SEC": "2.0"}),
]

GIGAAM_ENV_KEYS = (
    "GIGAAM_WARMUP_SEC",
    "GIGAAM_FIRST_CHUNK_MAX_SEC",
    "USE_GIGAAM_LONGFORM_VAD",
    "GIGAAM_LONGFORM_OVERLAP_SEC",
)


def _apply_gigaam_env(base: Dict[str, str], patch: Dict[str, str]) -> Dict[str, str]:
    merged = {**base, **patch}
    backup = {k: os.environ.get(k) for k in GIGAAM_ENV_KEYS}
    for k in GIGAAM_ENV_KEYS:
        v = merged.get(k)
        if v is None or v == "":
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)
    return backup


def _restore_gigaam_env(backup: Dict[str, Optional[str]]) -> None:
    for k, v in backup.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def _resolve_wav_paths(
    project_root: Path,
    call_ids: Optional[List[int]],
    wav_paths: Optional[List[Path]],
) -> List[Tuple[str, Path]]:
    out: List[Tuple[str, Path]] = []
    if wav_paths:
        for p in wav_paths:
            out.append((p.name, p.resolve()))
        return out
    if not call_ids:
        raise SystemExit("Укажите --call-ids или --wav")
    sys.path.insert(0, str(project_root))
    from database.postgresql_manager import CallAnalyticsDB  # noqa: E402

    for cid in call_ids:
        row = CallAnalyticsDB.get_call_with_details(cid)
        if not row or not row.get("file_path"):
            print(f"[skip] call_id={cid}: нет записи или file_path", file=sys.stderr)
            continue
        fp = Path(row["file_path"])
        if not fp.is_absolute():
            fp = project_root / fp
        if not fp.exists():
            print(f"[skip] call_id={cid}: файл не найден: {fp}", file=sys.stderr)
            continue
        out.append((f"call_id={cid}", fp))
    return out


def _first_segment_preview(segments: List[Dict[str, Any]], max_len: int = 220) -> str:
    if not segments:
        return ""
    t0 = (segments[0].get("text") or "").strip()
    return t0[:max_len]


def _merge_head_full(
    head: str,
    full: str,
    max_overlap_words: int = 16,
) -> str:
    """
    Склейка: текст «голова» + полный текст, без дубля на стыке.
    Ищем максимальное совпадение суффикса head и префикса full по словам.
    """
    a = (head or "").strip().split()
    b = (full or "").strip().split()
    if not a:
        return (full or "").strip()
    if not b:
        return (head or "").strip()
    upper = min(max_overlap_words, len(a), len(b))
    for k in range(upper, 0, -1):
        if a[-k:] == b[:k]:
            return " ".join(a + b[k:])
    return " ".join(a) + " " + " ".join(b)


def _head_only_transcribe(
    project_root: Path,
    model: Any,
    wav_path: Path,
    head_sec: float = 6.0,
    warmup_sec: float = 1.0,
) -> str:
    """Отдельный проход: только первые head_sec секунд, один вызов model.transcribe (без longform)."""
    import numpy as np
    import soundfile as sf

    audio, sr = sf.read(str(wav_path))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = np.asarray(audio, dtype=np.float32)
    n = min(int(head_sec * sr), len(audio))
    chunk = audio[:n]
    if warmup_sec > 0:
        silence = np.zeros(int(warmup_sec * sr), dtype=np.float32)
        chunk = np.concatenate([silence, chunk])
    fd, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        sf.write(tmp, chunk, sr)
        fn = getattr(model, "transcribe", None)
        if fn is None and hasattr(model, "model"):
            fn = getattr(model.model, "transcribe", None)
        if fn is None:
            return ""
        raw = fn(tmp)
        if isinstance(raw, str):
            return raw.strip()
        return (raw or "").strip() if raw is not None else ""
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Сравнение гипотез GigaAM по началу транскрипта")
    parser.add_argument(
        "--project-root",
        type=Path,
        required=True,
        help="Корень проекта VikingiAll (для импорта analyze_call_quality и БД)",
    )
    parser.add_argument("--call-ids", type=int, nargs="*", help="ID звонков из таблицы calls")
    parser.add_argument("--wav", type=Path, nargs="*", help="Прямые пути к WAV")
    parser.add_argument(
        "--head-only",
        action="store_true",
        help="Дополнительно: проход только по первым 6 с (model.transcribe), колонка head6s",
    )
    parser.add_argument(
        "--two-pass",
        action="store_true",
        help="Двухпроходка: head (model.transcribe на первых N с) + полный transcribe_audio_gigaam, склейка без дубля",
    )
    parser.add_argument(
        "--head-sec",
        type=float,
        default=6.0,
        help="Длина «головы» в секундах для --two-pass и --head-only (по умолчанию 6)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Сохранить результаты в JSON",
    )
    args = parser.parse_args()

    project_root = args.project_root.resolve()
    sys.path.insert(0, str(project_root))

    from analyze_call_quality import load_gigaam_model, transcribe_audio_gigaam  # noqa: E402

    items = _resolve_wav_paths(project_root, args.call_ids, args.wav)
    if not items:
        print("Нет файлов для обработки", file=sys.stderr)
        return 1

    print("Загрузка GigaAM (один раз)...", flush=True)
    model = load_gigaam_model()

    results: List[Dict[str, Any]] = []

    for label, wav_path in items:
        print(f"\n=== {label} :: {wav_path} ===", flush=True)
        row: Dict[str, Any] = {"label": label, "path": str(wav_path), "scenarios": {}}

        if args.head_only:
            h = _head_only_transcribe(
                project_root, model, wav_path, head_sec=args.head_sec, warmup_sec=1.0
            )
            row["head6s_transcribe"] = h[:400]
            print(f"  [head6s only] {h[:220]!r}", flush=True)

        if args.two_pass:
            head_txt = _head_only_transcribe(
                project_root, model, wav_path, head_sec=args.head_sec, warmup_sec=1.0
            )
            backup = _apply_gigaam_env(BASE_ENV, {})
            try:
                full_norm, _, _ = transcribe_audio_gigaam(
                    wav_path,
                    model,
                    normalize_with_llm=False,
                )
            finally:
                _restore_gigaam_env(backup)
            merged = _merge_head_full(head_txt, full_norm or "")
            row["two_pass"] = {
                "head_sec": args.head_sec,
                "head_raw": head_txt[:800],
                "full_start": (full_norm or "")[:800],
                "merged_start": merged[:800],
            }
            print(f"  [two-pass head] {head_txt[:200]!r}", flush=True)
            print(f"  [two-pass full@start] {(full_norm or '')[:200]!r}", flush=True)
            print(f"  [two-pass merged@start] {merged[:280]!r}", flush=True)

        for scen_name, patch in SCENARIOS:
            backup = _apply_gigaam_env(BASE_ENV, patch)
            try:
                normalized, segments, raw = transcribe_audio_gigaam(
                    wav_path,
                    model,
                    normalize_with_llm=False,
                )
                preview = (normalized or "")[:400]
                first_seg = _first_segment_preview(segments or [], 300)
                row["scenarios"][scen_name] = {
                    "env": {**BASE_ENV, **patch},
                    "start_normalized": preview,
                    "first_segment": first_seg,
                    "raw_len": len(raw or ""),
                }
                print(f"  [{scen_name}] начало: {preview[:180]!r}", flush=True)
            except Exception as e:
                row["scenarios"][scen_name] = {"error": str(e)}
                print(f"  [{scen_name}] ERROR: {e}", flush=True)
            finally:
                _restore_gigaam_env(backup)

        results.append(row)

    if args.json_out:
        args.json_out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON: {args.json_out}", flush=True)

    print("\nГотово. Сравните колонки «начало» глазами; лучший сценарий — в SCENARIOS правьте BASE_ENV на проде.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
