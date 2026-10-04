#!/usr/bin/env python3
"""
Транскрибация звонка через GigaAM — только текст в терминал, без пропусков.

Использование (server7):
  # По имени файла (ищет в /mnt/audio_calls/calls/auto/ и storage/calls)
  USE_GIGAAM=1 python scripts/transcribe_to_terminal.py 2026_03_20_08_17_08_6C0.wav

  # По полному пути
  USE_GIGAAM=1 python scripts/transcribe_to_terminal.py /mnt/audio_calls/calls/auto/2026-03-20/2026_03_20_08_17_08_6C0.wav

  # По call_id (требует БД с корректным паролем). Чанки GigaAM: лучше ./run_local.sh — подхватит дефолты пайплайна.
  USE_GIGAAM=1 ./run_local.sh python scripts/transcribe_to_terminal.py 887
"""
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

os.environ.setdefault("USE_GIGAAM", "1")


def _search_file_by_name(name: str) -> Path | None:
    """Ищет файл по имени в стандартных каталогах (autofetch, manual, storage)."""
    try:
        from config import AUDIO_CALLS_ROOT
    except ImportError:
        AUDIO_CALLS_ROOT = Path("/mnt/audio_calls/calls")
    search_dirs = [
        AUDIO_CALLS_ROOT / "auto",   # autofetch из SpRecord
        AUDIO_CALLS_ROOT / "manual", # ручная загрузка
        PROJECT_DIR / "storage" / "calls",
    ]
    for base in search_dirs:
        if not base.exists():
            continue
        for sub in base.rglob(name):
            if sub.is_file():
                return sub
    return None


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Транскрибация в терминал (GigaAM)")
    parser.add_argument("input", help="Имя файла, путь к .wav или call_id")
    parser.add_argument(
        "--path-only",
        action="store_true",
        help="Только путь, БД не трогать",
    )
    args = parser.parse_args()

    inp = args.input.strip()
    file_path = None

    # call_id или путь/имя файла
    if inp.isdigit() and not args.path_only:
        try:
            from database.postgresql_manager import CallAnalyticsDB
        except Exception as e:
            print(f"Ошибка подключения к БД: {e}", file=sys.stderr)
            print("Используйте имя файла или путь: python scripts/transcribe_to_terminal.py 2026_03_20_08_17_08_6C0.wav", file=sys.stderr)
            sys.exit(1)
        row = CallAnalyticsDB.get_call_with_details(int(inp))
        if not row or not row.get("file_path"):
            print(f"Звонок {inp} не найден или нет file_path", file=sys.stderr)
            sys.exit(1)
        fp = Path(row["file_path"])
        base = Path(os.environ.get("BASE_DIR", PROJECT_DIR))
        file_path = fp if fp.is_absolute() else base / fp
    else:
        # Путь или имя файла
        cand = Path(inp)
        if cand.is_absolute() and cand.exists():
            file_path = cand
        elif "/" in inp:
            file_path = cand if cand.is_absolute() else PROJECT_DIR / inp
        else:
            # Только имя — ищем в /mnt/audio_calls/calls/auto, manual, storage/calls
            name = inp if inp.endswith(".wav") else f"{inp}.wav"
            found = _search_file_by_name(name)
            file_path = found if found else PROJECT_DIR / inp

    if not file_path.exists():
        if "/" not in inp and not inp.endswith(".wav"):
            print(f"Файл не найден: {inp}", file=sys.stderr)
            print("Искал в: /mnt/audio_calls/calls/auto, manual, storage/calls", file=sys.stderr)
        else:
            print(f"Файл не найден: {file_path}", file=sys.stderr)
        sys.exit(1)

    # Загрузка GigaAM и транскрибация (analyze_call_quality не трогает БД)
    from analyze_call_quality import load_gigaam_model, transcribe_audio_gigaam

    print(f"Файл: {file_path}", file=sys.stderr)
    model = load_gigaam_model()
    normalized, segments, raw = transcribe_audio_gigaam(
        file_path,
        model,
        normalize_with_llm=False,
    )

    # Текст в терминал
    print(normalized)


if __name__ == "__main__":
    main()
