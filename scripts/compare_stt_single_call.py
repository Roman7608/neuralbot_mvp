#!/usr/bin/env python3
"""
Один звонок: два варианта GigaAM (разные папки моделей).
Без доменной нормализации (normalize_text / normalize_transcript) — чистое сравнение STT.

Пример (server7, БД с file_path):
  USE_GIGAAM=1 ./run_local.sh python3 scripts/compare_stt_single_call.py --call-id 1651

Опционально пути к GigaAM:
  --gigaam-a models/gigaam-v3
  --gigaam-b models/gigaam-v3-e2e_rnnt
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Сравнение STT — LLM в нормализации не используем
os.environ.setdefault("USE_LLM_NORMALIZE", "0")

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def main() -> int:
    parser = argparse.ArgumentParser(description="Сравнение GigaAM x2 для одного call_id")
    parser.add_argument("--call-id", type=int, required=True, help="ID звонка в БД")
    parser.add_argument(
        "--gigaam-a",
        type=Path,
        default=None,
        help="Первая модель GigaAM (по умолчанию config GIGAAM_MODEL_PATH, обычно e2e_ctc)",
    )
    parser.add_argument(
        "--gigaam-b",
        type=Path,
        default=None,
        help="Вторая модель GigaAM (по умолчанию models/gigaam-v3-e2e_rnnt, если есть)",
    )
    parser.add_argument("--json-out", type=Path, default=None, help="Сохранить результаты в JSON")
    args = parser.parse_args()

    try:
        from config import GIGAAM_MODEL_PATH
    except ImportError:
        GIGAAM_MODEL_PATH = PROJECT_DIR / "models" / "gigaam-v3"

    gigaam_a = Path(args.gigaam_a) if args.gigaam_a else Path(GIGAAM_MODEL_PATH)
    gigaam_b = Path(args.gigaam_b) if args.gigaam_b else (PROJECT_DIR / "models" / "gigaam-v3-e2e_rnnt")

    from database.postgresql_manager import CallAnalyticsDB
    from analyze_call_quality import (
        load_gigaam_model_from_path,
        transcribe_audio_gigaam,
    )

    row = CallAnalyticsDB.get_call_with_details(args.call_id)
    if not row or not row.get("file_path"):
        print(f"Звонок {args.call_id} не найден или нет file_path", file=sys.stderr)
        return 1

    fp = Path(row["file_path"])
    if not fp.is_absolute():
        fp = PROJECT_DIR / fp
    if not fp.exists():
        print(f"Файл не найден: {fp}", file=sys.stderr)
        return 1

    print(f"Файл: {fp}", flush=True)
    print(f"GigaAM A: {gigaam_a}", flush=True)
    print(f"GigaAM B: {gigaam_b}", flush=True)

    out: dict = {"call_id": args.call_id, "wav": str(fp), "gigaam_a": None, "gigaam_b": None}

    # GigaAM A
    if not (gigaam_a / "config.json").exists():
        print(f"[skip] GigaAM A: нет config.json в {gigaam_a}", file=sys.stderr)
    else:
        print("\n--- GigaAM A ---", flush=True)
        m_a = load_gigaam_model_from_path(gigaam_a)
        txt_a, _, raw_a = transcribe_audio_gigaam(
            fp,
            m_a,
            normalize_with_llm=False,
            apply_domain_normalization=False,
        )
        out["gigaam_a"] = {"path": str(gigaam_a), "text": txt_a, "raw": raw_a}
        print(txt_a[:800] if txt_a else "(пусто)")

    # GigaAM B
    if not (gigaam_b / "config.json").exists():
        print(f"[skip] GigaAM B: нет config.json в {gigaam_b}", file=sys.stderr)
    else:
        print("\n--- GigaAM B ---", flush=True)
        m_b = load_gigaam_model_from_path(gigaam_b)
        txt_b, _, raw_b = transcribe_audio_gigaam(
            fp,
            m_b,
            normalize_with_llm=False,
            apply_domain_normalization=False,
        )
        out["gigaam_b"] = {"path": str(gigaam_b), "text": txt_b, "raw": raw_b}
        print(txt_b[:800] if txt_b else "(пусто)")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON: {args.json_out}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
