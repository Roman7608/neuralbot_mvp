#!/usr/bin/env python3
"""
Тест: офлайн-транскрипция с префиксом тишины для прогрева STT.
Использование: USE_GIGAAM=1 python scripts/transcribe_with_warmup.py /mnt/audio_calls/calls/auto/2026-03-20/2026_03_20_09_47_50_6DA.wav
"""
import os
import sys
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
os.environ.setdefault("USE_GIGAAM", "1")

import numpy as np
import soundfile as sf


def main() -> None:
    # Файл со скрина
    path = Path("/mnt/audio_calls/calls/auto/2026-03-20/2026_03_20_09_47_50_6DA.wav")
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    if not path.exists():
        print(f"Файл не найден: {path}", file=sys.stderr)
        sys.exit(1)

    warmup_sec = float(os.environ.get("GIGAAM_WARMUP_SEC", "1.0"))
    print(f"Файл: {path}", file=sys.stderr)
    print(f"Префикс тишины: {warmup_sec} сек", file=sys.stderr)

    audio, sr = sf.read(str(path))
    if audio.ndim == 2:
        n_silence = int(warmup_sec * sr)
        silence = np.zeros((n_silence, audio.shape[1]), dtype=audio.dtype)
    else:
        n_silence = int(warmup_sec * sr)
        silence = np.zeros(n_silence, dtype=audio.dtype)

    audio_with_warmup = np.concatenate([silence, audio])
    fd, tmp_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    sf.write(tmp_path, audio_with_warmup, sr)

    from analyze_call_quality import load_gigaam_model, transcribe_audio_gigaam

    model = load_gigaam_model()
    normalized, segments, raw = transcribe_audio_gigaam(
        Path(tmp_path),
        model,
        normalize_with_llm=False,
    )
    try:
        Path(tmp_path).unlink()
    except Exception:
        pass

    print("\n--- Транскрипт (+0.5 сек тишины) ---")
    print(normalized)


if __name__ == "__main__":
    main()
