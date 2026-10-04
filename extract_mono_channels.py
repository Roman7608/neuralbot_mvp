#!/usr/bin/env python3
"""Извлечение каналов из стерео WAV.
Использование:
  python extract_mono_channels.py [0|1]
    — пара по умолчанию: ex.wav, STO/2.wav -> mono1_ch1.wav, mono2_ch1.wav
  python extract_mono_channels.py [0|1] op.wav sto.wav
    — произвольная пара -> mono3_ch1.wav, mono4_ch1.wav (в Analytic/)
"""

import sys
from pathlib import Path

import soundfile as sf

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_OP = PROJECT_DIR / "Analytic" / "ex.wav"
DEFAULT_STO = PROJECT_DIR / "Analytic" / "STO" / "2.wav"


def extract_channel(wav_path: Path, out_path: Path, channel: int = 1) -> bool:
    if not wav_path.exists():
        print(f"Файл не найден: {wav_path}")
        return False
    audio, sr = sf.read(str(wav_path))
    if audio.ndim == 1:
        mono = audio
    else:
        mono = audio[:, channel]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_path), mono, sr)
    print(f"Сохранено: {out_path} (канал {channel} из {wav_path.name})")
    return True


def main():
    channel = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    if len(sys.argv) >= 4:
        op_file = PROJECT_DIR / sys.argv[2]
        sto_file = PROJECT_DIR / sys.argv[3]
        out1 = PROJECT_DIR / "Analytic" / ("mono3_ch1.wav" if channel == 1 else "mono3.wav")
        out2 = PROJECT_DIR / "Analytic" / ("mono4_ch1.wav" if channel == 1 else "mono4.wav")
    else:
        op_file = DEFAULT_OP
        sto_file = DEFAULT_STO
        out1 = PROJECT_DIR / "Analytic" / ("mono1_ch1.wav" if channel == 1 else "mono1.wav")
        out2 = PROJECT_DIR / "Analytic" / ("mono2_ch1.wav" if channel == 1 else "mono2.wav")

    print(f"Извлечение канала {channel} (0=клиент, 1=сотрудник)...")
    ok1 = extract_channel(op_file, out1, channel)
    ok2 = extract_channel(sto_file, out2, channel)
    if ok1 and ok2:
        print(f"\nГотово. Прослушайте: {out1.name}, {out2.name}")
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    sys.exit(main())
