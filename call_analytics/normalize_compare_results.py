#!/usr/bin/env python3
"""
Прогон текстов из compare_whisper_gigaam.txt через нормализатор.

С LLM (нужен GPU, LLM_MODEL_PATH в .env):
  USE_LLM_NORMALIZE=1 ./run_local.sh python3 -m call_analytics.normalize_compare_results
  → storage/compare_whisper_gigaam_normalized.txt

Только правила (без LLM):
  ./run_local.sh python3 -m call_analytics.normalize_compare_results --no-llm
  → storage/compare_whisper_gigaam_rules.txt
"""

import argparse
import os
import re
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

INPUT_FILE = PROJECT_DIR / "storage" / "compare_whisper_gigaam.txt"


def parse_compare_file(content: str) -> list[dict]:
    """Парсит файл сравнения, возвращает список блоков {header, whisper_large, whisper_medium, gigaam}."""
    blocks = []
    parts = content.split("\n============================================================\n")
    # parts[0]=empty, parts[1]=header, parts[2]=body, parts[3]=header, ...
    for i in range(1, len(parts) - 1, 2):
        header = parts[i].strip()
        body = parts[i + 1]

        def extract_section(marker: str) -> str:
            m = re.search(rf"--- {re.escape(marker)} ---\s*\n(.*?)(?=\n--- |\Z)", body, re.DOTALL)
            return (m.group(1) or "").strip()

        blocks.append({
            "header": header,
            "whisper_large": extract_section("Whisper large"),
            "whisper_medium": extract_section("Whisper medium"),
            "gigaam": extract_section("GigaAM"),
        })
    return blocks


def main() -> int:
    parser = argparse.ArgumentParser(description="Нормализация текстов сравнения STT")
    parser.add_argument("--no-llm", action="store_true", help="Только правила (regex), без LLM")
    args = parser.parse_args()

    if args.no_llm:
        os.environ["USE_LLM_NORMALIZE"] = "0"
        output_file = PROJECT_DIR / "storage" / "compare_whisper_gigaam_rules.txt"
        mode = "правила (без LLM)"
    else:
        os.environ.setdefault("USE_LLM_NORMALIZE", "1")
        output_file = PROJECT_DIR / "storage" / "compare_whisper_gigaam_normalized.txt"
        mode = "LLM" if os.environ.get("USE_LLM_NORMALIZE", "").lower() in ("1", "true", "yes") else "правила"

    if not INPUT_FILE.exists():
        print(f"Файл не найден: {INPUT_FILE}")
        print("Сначала запустите: ./run_local.sh python3 -m call_analytics.compare_whisper_gigaam")
        return 1

    content = INPUT_FILE.read_text(encoding="utf-8")
    blocks = parse_compare_file(content)
    if not blocks:
        print("Не удалось распарсить файл")
        return 1

    from text_normalization import normalize_transcript

    print(f"Нормализация ({mode}): {len(blocks)} звонков × 3 STT")
    if not args.no_llm:
        print("LLM: для пустых/коротких пропуск\n")

    results = []
    for i, b in enumerate(blocks, 1):
        print(f"\n[{i}/{len(blocks)}] {b['header']}")

        def norm(t: str) -> str:
            t = (t or "").strip()
            if not t or len(t) < 15 or t.startswith("[") or "ча. Счас3" in t:
                return t
            return normalize_transcript(t)

        nl = norm(b["whisper_large"])
        nm = norm(b["whisper_medium"])
        ng = norm(b["gigaam"])

        results.append({
            "header": b["header"],
            "whisper_large": nl,
            "whisper_medium": nm,
            "gigaam": ng,
        })

    output_file.parent.mkdir(parents=True, exist_ok=True)
    suffix = "(правила)" if args.no_llm else "(LLM)"
    with open(output_file, "w", encoding="utf-8") as f:
        for r in results:
            f.write(f"\n{'='*60}\n{r['header']}\n")
            f.write(f"{'='*60}\n--- Whisper large {suffix} ---\n{r['whisper_large']}\n")
            f.write(f"--- Whisper medium {suffix} ---\n{r['whisper_medium']}\n")
            f.write(f"--- GigaAM {suffix} ---\n{r['gigaam']}\n")

    print(f"\nРезультат: {output_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
