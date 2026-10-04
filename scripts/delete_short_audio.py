#!/usr/bin/env python3
"""
Удаление аудиофайлов короче 60 секунд с HDD (AUDIO_CALLS_ROOT).

Запуск:
  python scripts/delete_short_audio.py [--dry-run]

--dry-run: только показать, что будет удалено, без удаления.
"""
import argparse
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

try:
    from config import AUDIO_CALLS_ROOT
except ImportError:
    AUDIO_CALLS_ROOT = Path("/mnt/audio_calls/calls")

MIN_DURATION_SEC = 60


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Только показать файлы, не удалять")
    args = parser.parse_args()

    root = Path(AUDIO_CALLS_ROOT)
    if not root.exists():
        print(f"❌ Папка не найдена: {root}")
        return 1

    try:
        import soundfile as sf
    except ImportError:
        print("pip install soundfile")
        return 1

    to_delete = []
    for ext in ("*.wav", "*.mp3", "*.ogg"):
        for path in root.rglob(ext):
            try:
                info = sf.info(str(path))
                dur = info.frames / info.samplerate
                if dur < MIN_DURATION_SEC:
                    to_delete.append((path, dur))
            except Exception as e:
                print(f"  ⚠️ {path}: {e}")

    if not to_delete:
        print(f"Файлов короче {MIN_DURATION_SEC} с не найдено.")
        return 0

    print(f"Найдено файлов < {MIN_DURATION_SEC} с: {len(to_delete)}")
    for path, dur in sorted(to_delete, key=lambda x: str(x[0])):
        print(f"  {dur:.1f} с: {path}")

    if args.dry_run:
        print("\n[--dry-run] Удаление не выполнено.")
        return 0

    deleted = 0
    for path, _ in to_delete:
        try:
            path.unlink()
            deleted += 1
        except OSError as e:
            print(f"  ❌ Не удалось удалить {path}: {e}")

    print(f"\n✅ Удалено: {deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
