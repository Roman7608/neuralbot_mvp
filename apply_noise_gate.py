#!/usr/bin/env python3
"""
Применение noise gate к аудио: отсекает тихие участки (клиент) при смешанном канале.
Использование: python apply_noise_gate.py путь/к/файлу.wav [канал] [--threshold-db -25]
Выход: Analytic/<имя>_gated.wav
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

PROJECT_DIR = Path(__file__).resolve().parent


def rms_to_db(rms: float, eps: float = 1e-10) -> float:
    return 20 * np.log10(rms + eps)


def db_to_linear(db: float) -> float:
    return 10 ** (db / 20)


def apply_noise_gate(
    audio: np.ndarray,
    sr: int,
    threshold_db: float = -28,
    frame_ms: float = 30,
    attack_ms: float = 5,
    release_ms: float = 50,
) -> np.ndarray:
    """
    Применяет noise gate: ослабляет участки ниже threshold_db.
    frame_ms — длина анализа, attack/release — сглаживание переключений.
    """
    frame_samples = int(sr * frame_ms / 1000)
    attack_samples = max(1, int(sr * attack_ms / 1000))
    release_samples = max(1, int(sr * release_ms / 1000))
    threshold_linear = db_to_linear(threshold_db)

    n = len(audio)
    out = np.zeros_like(audio)
    gate = 0.0

    pos = 0
    while pos < n:
        end = min(pos + frame_samples, n)
        frame = audio[pos:end]
        rms = np.sqrt(np.mean(frame.astype(np.float64) ** 2))
        rms_db = rms_to_db(rms)

        # Логика gate: выше порога — открыт, ниже — закрыт
        target = 1.0 if rms >= threshold_linear else 0.0

        # Attack/release для плавности
        if target > gate:
            step = 1.0 / attack_samples
            gate = min(gate + step, target)
        else:
            step = 1.0 / release_samples
            gate = max(gate - step, target)

        out[pos:end] = audio[pos:end] * gate
        pos = end

    return out


def main():
    parser = argparse.ArgumentParser(description="Noise gate: отсекает тихие участки (клиент)")
    parser.add_argument("wav", nargs="?", default="Analytic/05 января - Щег - Павел.wav", help="WAV файл")
    parser.add_argument("--channel", "-c", type=int, default=1, help="Канал (0 или 1)")
    parser.add_argument("--threshold-db", "-t", type=float, default=-28, help="Порог в dB (тише — отсекается)")
    parser.add_argument("--frame-ms", type=float, default=30, help="Длина анализа, мс")
    args = parser.parse_args()

    wav_path = PROJECT_DIR / args.wav
    if not wav_path.exists():
        print(f"Файл не найден: {wav_path}")
        return 1

    audio, sr = sf.read(str(wav_path))
    if audio.ndim == 1:
        mono = audio.astype(np.float64)
    else:
        mono = audio[:, args.channel].astype(np.float64)

    print(f"Применение noise gate: threshold={args.threshold_db} dB, frame={args.frame_ms} ms...")
    gated = apply_noise_gate(mono, sr, threshold_db=args.threshold_db, frame_ms=args.frame_ms)

    out_name = wav_path.stem + "_gated.wav"
    out_path = PROJECT_DIR / "Analytic" / out_name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), gated, sr)
    print(f"Сохранено: {out_path}")
    print("Прослушайте результат.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
