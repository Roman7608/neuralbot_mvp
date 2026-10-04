#!/usr/bin/env python3
"""
Пакетный прогон GigaAM-v3 по тем же WAV, что и stt_compare*.sh.

Требования: рабочий стек PyTorch + torchaudio + transformers (GigaAM trust_remote_code).
В каталоге models/gigaam-v3 должен быть modeling_gigaam.py (скопировать из HF modules при необходимости).

На server7 с torch 2.10+cu128 загрузка модели часто падает в MelSpectrogram (meta tensors).
Обход: отдельный venv с torch 2.2–2.4 или дождаться фикса torchaudio.

Запуск (когда загрузка заработает):
  TORCH_DEFAULT_DEVICE=cpu  # иногда помогает на других версиях
  ./venv/bin/python experiments/run_gigaam_stt_batch.py
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = PROJECT / "experiments/19-03-2026_08-46-33/wav_8k_mono"

if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


def prep_none(wav: Path, work: Path) -> Path:
    return wav


def prep_ffmpeg(wav: Path, work: Path, af: str) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    out = work / f"{wav.stem}_prep.wav"
    cmd = [
        "ffmpeg", "-y", "-i", str(wav),
        "-af", af,
        "-ar", "8000", "-ac", "1", "-c:a", "pcm_s16le",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


AF_TRIM = "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB:detection=peak,areverse,silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB:detection=peak,areverse"
AF_LOUDTRIM = f"{AF_TRIM},volume=8dB"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav-dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--out", type=Path, default=PROJECT / "experiments/stt_gigaam_batch.tsv")
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args()

    try:
        import torch
    except ImportError as e:
        print("Нет torch:", e, file=sys.stderr)
        sys.exit(2)

    from analyze_call_quality import load_gigaam_model
    print("Загрузка GigaAM (с патчем meta tensors)...", flush=True)
    try:
        model = load_gigaam_model()
    except Exception as e:
        print("Не удалось загрузить GigaAM:", e, file=sys.stderr)
        sys.exit(3)

    wavs = sorted(args.wav_dir.glob("*.wav"))
    if not wavs:
        print("Нет WAV в", args.wav_dir, file=sys.stderr)
        sys.exit(1)

    rows: list[tuple[str, str, str]] = []
    tmp = Path("/tmp/gigaam_stt_batch_prep")
    tmp.mkdir(exist_ok=True)

    variants = [
        ("H0_base", lambda w: prep_none(w, tmp)),
        ("H1_prompt", lambda w: prep_none(w, tmp)),
        ("H2_loud8db", lambda w: prep_ffmpeg(w, tmp / "loud", "volume=8dB")),
        ("H3_trim", lambda w: prep_ffmpeg(w, tmp / "trim", AF_TRIM)),
        ("H4_loudtrim+prompt", lambda w: prep_ffmpeg(w, tmp / "loudtrim", AF_LOUDTRIM)),
    ]

    for wav in wavs:
        for tag, prep in variants:
            path = prep(wav)
            try:
                text = model.transcribe(str(path.resolve()))
            except Exception as e:
                text = f"ERR:{e}"
            rows.append(("gigaam_v3", tag, wav.name, (text or "").strip()))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        f.write("engine\thypothesis\tfile\ttext\n")
        for engine, tag, fn, text in rows:
            f.write(f"{engine}\t{tag}\t{fn}\t{text}\n")
    print("Записано:", args.out, "строк:", len(rows), flush=True)


if __name__ == "__main__":
    main()
