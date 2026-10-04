#!/usr/bin/env python3
"""
Предзагрузка GigaAM-v3 (ai-sage/GigaAM-v3) в локальную папку.

Запустите ОДИН РАЗ при наличии интернета:
    python download_gigaam.py

Скачивает:
  - e2e_ctc в models/gigaam-v3 (legacy)
  - e2e_rnnt в models/gigaam-v3-e2e_rnnt (голосовой бот + аналитика, лучше WER)

После загрузки analyze_call_quality.py работает офлайн (USE_GIGAAM=1 или по умолчанию).
"""

import sys
from pathlib import Path

try:
    from huggingface_hub import snapshot_download, hf_hub_download
except ImportError:
    print("Установите huggingface_hub: pip install huggingface_hub")
    sys.exit(1)

try:
    from config import (
        GIGAAM_MODEL_PATH,
        GIGAAM_REVISION,
        GIGAAM_ANALYTICS_MODEL_PATH,
        GIGAAM_ANALYTICS_REVISION,
    )
except ImportError:
    GIGAAM_MODEL_PATH = Path(__file__).resolve().parent / "models" / "gigaam-v3"
    GIGAAM_REVISION = "e2e_ctc"
    GIGAAM_ANALYTICS_MODEL_PATH = Path(__file__).resolve().parent / "models" / "gigaam-v3"
    GIGAAM_ANALYTICS_REVISION = "e2e_ctc"

REPO_ID = "ai-sage/GigaAM-v3"
REQUIRED_FILES = ["config.json", "pytorch_model.bin", "modeling_gigaam.py"]


def _download_revision(path: Path, revision: str, label: str) -> bool:
    """Скачивает ревизию в path. Возвращает True если загрузка выполнена, False если уже есть."""
    path.mkdir(parents=True, exist_ok=True)

    missing = [f for f in REQUIRED_FILES if not (path / f).exists()]
    has_weights = (path / "pytorch_model.bin").exists() or (path / "model.safetensors").exists()

    if not missing and has_weights:
        print(f"  ✓ GigaAM-v3 ({revision}) уже загружена в {path}")
        return False

    if "modeling_gigaam.py" in missing and has_weights:
        print(f"  📥 Докачка modeling_gigaam.py (остальное есть)...")
        hf_hub_download(
            repo_id=REPO_ID,
            revision=revision,
            filename="modeling_gigaam.py",
            local_dir=str(path),
            local_dir_use_symlinks=False,
        )
        print(f"  ✓ modeling_gigaam.py сохранён в {path}")
        return True

    print(f"  📥 Загрузка {REPO_ID} (revision={revision}) в {path} ({label})...")
    print("     Требуется интернет.\n")

    snapshot_download(
        repo_id=REPO_ID,
        revision=revision,
        local_dir=str(path),
        local_dir_use_symlinks=False,
    )

    print(f"  ✓ GigaAM-v3 ({revision}) успешно загружена в {path}\n")
    return True


def _apply_patch(model_dir: Path, patch_name: str) -> bool:
    """Применить патч к modeling_gigaam.py. Возвращает True при успехе."""
    import subprocess
    patch_file = Path(__file__).resolve().parent / "patches" / patch_name
    if not patch_file.exists():
        return False
    try:
        with open(patch_file, "r", encoding="utf-8", errors="replace") as f:
            result = subprocess.run(
                ["patch", "-p0", "-f", "-s"],
                cwd=str(model_dir),
                stdin=f,
                capture_output=True,
                text=True,
            )
        return result.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def _apply_vad_patch(model_dir: Path) -> bool:
    """Применить патч VAD+overlap к modeling_gigaam.py."""
    return _apply_patch(model_dir, "gigaam_modeling_vad_overlap.patch")


def _apply_first_chunk_patch(model_dir: Path) -> bool:
    """Применить патч first_chunk_max_sec (короткий первый чанк для приветствия)."""
    return _apply_patch(model_dir, "gigaam_first_chunk.patch")


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Загрузка GigaAM-v3")
    parser.add_argument(
        "--no-apply-patch",
        action="store_true",
        help="Не применять патч VAD+overlap к e2e_rnnt (старый режим транскрибации)",
    )
    args = parser.parse_args()

    print("GigaAM-v3: предзагрузка моделей\n")

    _download_revision(
        Path(GIGAAM_MODEL_PATH),
        GIGAAM_REVISION,
        "голосовой бот",
    )

    analytics_downloaded = _download_revision(
        Path(GIGAAM_ANALYTICS_MODEL_PATH),
        GIGAAM_ANALYTICS_REVISION,
        "аналитика",
    )

    analytics_dir = Path(GIGAAM_ANALYTICS_MODEL_PATH)
    if not args.no_apply_patch and (analytics_dir / "modeling_gigaam.py").exists():
        print("  Применение патчей к e2e_rnnt...")
        if _apply_vad_patch(analytics_dir):
            print("  ✓ Патч VAD+overlap применён")
        else:
            print("  ⚠️ Не удалось применить VAD. Запустите: python patches/apply_gigaam_vad_patch.py")
        if _apply_first_chunk_patch(analytics_dir):
            print("  ✓ Патч first_chunk (приветствие) применён")

    print("\nГотово. analyze_call_quality.py и админка работают офлайн.")


if __name__ == "__main__":
    main()
