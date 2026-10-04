#!/usr/bin/env python3
"""
Генерация приветствия голосового бота (единый сценарий Чери/Тэнет).

Пишет audio_responses/01_greeting_chery_tenet.wav из текста GREETING_CHERY_TENET
в services/voice/voice_phrases.py.

Для полного набора WAV удобнее: python3 generate_all_voice_wavs.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from services.voice.voice_phrases import GREETING_CHERY_TENET


def generate_greeting_variants():
    import torch
    from torch import package
    import soundfile as sf
    import numpy as np

    downloads_dir = Path.home() / "Загрузки"
    model_file = downloads_dir / "SileroTTS-model-v5_1_ru" / "data" / "v5_1_ru.pt"
    model_file_alt = downloads_dir / "v5_ru.pt"

    if model_file.exists():
        model_path = model_file
    elif model_file_alt.exists():
        model_path = model_file_alt
    else:
        print("❌ Модель Silero не найдена в ~/Загрузки (SileroTTS-model-v5_1_ru или v5_ru.pt)")
        return False

    print(f"Загрузка модели: {model_path}")
    imp = package.PackageImporter(str(model_path))
    model = imp.load_pickle("tts_models", "model")
    model.to("cpu")

    sample_rate = 48000
    speed = 1.1
    output_file = PROJECT_ROOT / "audio_responses" / "01_greeting_chery_tenet.wav"
    output_file.parent.mkdir(parents=True, exist_ok=True)

    print("Генерация 01_greeting_chery_tenet.wav …")
    audio = model.apply_tts(text=GREETING_CHERY_TENET, speaker="xenia", sample_rate=sample_rate)
    if isinstance(audio, torch.Tensor):
        audio_np = audio.cpu().numpy().astype(np.float32)
    else:
        audio_np = np.array(audio, dtype=np.float32)
    if audio_np.max() > 1.0 or audio_np.min() < -1.0:
        audio_np = audio_np / np.max(np.abs(audio_np))
    if speed != 1.0:
        target_len = int(len(audio_np) / speed)
        indices = np.linspace(0, len(audio_np) - 1, target_len).astype(int)
        audio_np = audio_np[indices]

    sf.write(str(output_file), audio_np, sample_rate)
    print(f"✅ {output_file}")
    return True


if __name__ == "__main__":
    sys.exit(0 if generate_greeting_variants() else 1)
