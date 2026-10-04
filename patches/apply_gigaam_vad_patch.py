#!/usr/bin/env python3
"""
Применяет патч VAD + overlap к modeling_gigaam.py в папке модели.
По умолчанию: models/gigaam-v3-e2e_rnnt (аналитика).

Использование:
  python patches/apply_gigaam_vad_patch.py
  python patches/apply_gigaam_vad_patch.py --model-dir models/gigaam-v3
"""
import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
PATCH_FILE = PROJECT_DIR / "patches" / "gigaam_modeling_vad_overlap.patch"


def main() -> int:
    parser = argparse.ArgumentParser(description="Применить патч VAD+overlap к GigaAM modeling_gigaam.py")
    parser.add_argument(
        "--model-dir",
        default=str(PROJECT_DIR / "models" / "gigaam-v3-e2e_rnnt"),
        help="Папка модели (содержит modeling_gigaam.py)",
    )
    parser.add_argument("--revert", action="store_true", help="Откатить патч")
    args = parser.parse_args()

    model_dir = Path(args.model_dir)
    target = model_dir / "modeling_gigaam.py"

    if not target.exists():
        print(f"  ❌ Файл не найден: {target}", file=sys.stderr)
        return 1

    if not PATCH_FILE.exists():
        print(f"  ❌ Патч не найден: {PATCH_FILE}", file=sys.stderr)
        return 1

    cmd = ["patch", "-p0", "-f", "-s"]  # -s silent
    if args.revert:
        cmd.append("-R")

    try:
        with open(PATCH_FILE, "r", encoding="utf-8", errors="replace") as f:
            result = subprocess.run(
                cmd,
                cwd=str(model_dir),
                stdin=f,
                capture_output=True,
                text=True,
            )
        if result.returncode == 0:
            action = "откачен" if args.revert else "применён"
            print(f"  ✓ Патч VAD+overlap {action}: {model_dir}")
        else:
            print(f"  ⚠️ patch: {result.stderr or result.stdout}", file=sys.stderr)
            if "already applied" in (result.stderr or "").lower():
                print("  (патч уже применён)")
                return 0
            return 1
    except FileNotFoundError:
        print("  ❌ Команда patch не найдена. Установите: apt install patch", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
