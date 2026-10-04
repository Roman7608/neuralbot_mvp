#!/usr/bin/env python3
"""
Бенчмарк STT: Silero (публичный JIT) + Faster-Whisper small + GigaAM v3.

Те же WAV и гипотезы H0–H4, что в stt_compare*.sh.

Важно про Silero:
  - В открытом models.yml языков STT только: en, de, es, ua — русского НЕТ.
  - JIT-модели собраны под старый PyTorch; на torch 2.10 украинская модель часто падает в STFT.
  - По умолчанию берётся язык из SILERO_STT_LANG (по умолчанию «en»): это НЕ русский ASR,
    метрики ok/halluc на русской речи для Silero не интерпретировать как качество RU.

Переменные окружения:
  SILERO_STT_LANG   — en|de|es|ua (по умолчанию en)
  SKIP_SILERO=1     — не грузить Silero
  SKIP_GIGAAM=1     — не грузить GigaAM
  FW_DEVICE         — cuda|cpu (по умолчанию cuda)
  FW_COMPUTE        — float16|int8_float16|... (по умолчанию float16)

Запуск:
  ./venv/bin/python experiments/run_stt_silero_whisper_small_gigaam.py
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_WAV_DIR = PROJECT / "experiments/19-03-2026_08-46-33/wav_8k_mono"
MODEL_GIGA = PROJECT / "models/gigaam-v3"

HYPOTHESES = [
    ("H0_base", None),
    ("H1_prompt", None),  # совместимость схемы; для локальных движков = тот же файл, что H0
    ("H2_loud8db", "volume=8dB"),
    (
        "H3_trim",
        "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB:detection=peak,"
        "areverse,silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB:detection=peak,areverse",
    ),
    (
        "H4_loudtrim+prompt",
        "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB:detection=peak,"
        "areverse,silenceremove=start_periods=1:start_duration=0.1:start_threshold=-40dB:detection=peak,areverse,volume=8dB",
    ),
]


def ffmpeg_prep(wav: Path, work: Path, tag: str, af: str | None) -> Path:
    if af is None:
        return wav
    work.mkdir(parents=True, exist_ok=True)
    out = work / f"{wav.stem}_{tag}.wav"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(wav),
        "-af",
        af,
        "-ar",
        "8000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def transcribe_whisper_small(path: Path, model) -> str:
    segments, _info = model.transcribe(
        str(path),
        language="ru",
        beam_size=5,
        vad_filter=True,
    )
    return " ".join(s.text.strip() for s in segments).strip()


def transcribe_silero(path: Path, model, decoder, read_audio, prepare_model_input, device) -> str:
    import torch
    import torch.nn.functional as F

    wav = read_audio(str(path), target_sr=16000)
    inp = prepare_model_input([wav], device=device)
    with torch.no_grad():
        out = model(inp)
    probs = F.softmax(out, dim=-1)[0]
    text = decoder(probs, wav_len=len(wav), word_align=False)
    if isinstance(text, tuple):
        text = text[0]
    return (text or "").strip()


def transcribe_gigaam(path: Path, model) -> str:
    return (model.transcribe(str(path.resolve())) or "").strip()


def bucket_scores(rows: list[tuple[str, str, str]]) -> None:
    hall = re.compile(r"продолжение|субтитры|dimatorzok", re.I)
    ok = re.compile(r"^(да|согласна|согласен|хорошо)\.?$", re.I)

    def norm(t: str) -> str:
        t = (t or "").strip().lower()
        t = re.sub(r"[^\wа-яё]+", "", t, flags=re.I)
        return t

    def bucket(text: str) -> str:
        t = (text or "").strip()
        if not t:
            return "empty"
        if t.upper().startswith("ERR:"):
            return "error"
        if hall.search(t):
            return "halluc"
        n = norm(t)
        if ok.match(n) or n in ("да", "согласна", "согласен", "хорошо"):
            return "ok"
        if n.startswith("да") and len(n) <= 4:
            return "ok"
        return "other"

    by: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for eng, hyp, text in rows:
        by[(eng, hyp)][bucket(text)] += 1

    print("\n=== Сводка (ok / empty / halluc / other / error) ===")
    print("engine\thypothesis\tok\tempty\thalluc\tother\terror")
    engines = sorted({e for e, _ in by})
    hyps = [h for h, _ in HYPOTHESES]
    for e in engines:
        for h in hyps:
            c = by.get((e, h), Counter())
            print(
                f"{e}\t{h}\t{c['ok']}\t{c['empty']}\t{c['halluc']}\t{c['other']}\t{c['error']}"
            )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav-dir", type=Path, default=DEFAULT_WAV_DIR)
    ap.add_argument(
        "--out",
        type=Path,
        default=PROJECT / "experiments/stt_silero_fw_small_gigaam.tsv",
    )
    args = ap.parse_args()

    wavs = sorted(args.wav_dir.glob("*.wav"))
    if not wavs:
        print("Нет WAV в", args.wav_dir, file=sys.stderr)
        sys.exit(1)

    work_root = Path(tempfile.mkdtemp(prefix="stt_triple_"))
    rows: list[tuple[str, str, str, str]] = []

    # --- Faster-Whisper small ---
    fw_model = None
    try:
        from faster_whisper import WhisperModel

        dev = os.environ.get("FW_DEVICE", "cuda")
        ct = os.environ.get("FW_COMPUTE_TYPE", "float16")
        if dev == "cuda":
            import torch

            if not torch.cuda.is_available():
                dev = "cpu"
                ct = "int8"
        print("Загрузка Faster-Whisper small...", flush=True)
        fw_model = WhisperModel("small", device=dev, compute_type=ct)
    except Exception as e:
        print("Whisper small: не загружен —", e, file=sys.stderr)

    # --- Silero (публичный STT, язык НЕ ru) ---
    silero_bundle = None
    silero_lang = os.environ.get("SILERO_STT_LANG", "en").strip().lower()
    silero_engine_name = f"silero_{silero_lang}_not_ru"
    if os.environ.get("SKIP_SILERO", "").strip() in ("1", "true", "yes"):
        print("Silero: SKIP_SILERO=1", flush=True)
    else:
        try:
            import torch
            import silero

            device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
            print(f"Загрузка Silero STT language={silero_lang}...", flush=True)
            model, decoder, (_, _, read_audio, prepare_model_input) = silero.silero_stt(
                language=silero_lang,
                device=device,
            )
            # проверочный прогон
            w = read_audio(str(wavs[0]), target_sr=16000)
            pi = prepare_model_input([w], device=device)
            with torch.no_grad():
                _ = model(pi)
            silero_bundle = (model, decoder, read_audio, prepare_model_input, device)
            print("Silero: OK (не забудьте: это не русская модель)", flush=True)
        except Exception as e:
            print("Silero: пропуск —", e, file=sys.stderr)

    # --- GigaAM v3 ---
    giga = None
    if os.environ.get("SKIP_GIGAAM", "").strip() in ("1", "true", "yes"):
        print("GigaAM: SKIP_GIGAAM=1", flush=True)
    else:
        try:
            from transformers import AutoModel
            import torch

            print("Загрузка GigaAM v3...", flush=True)
            giga = AutoModel.from_pretrained(
                str(MODEL_GIGA),
                trust_remote_code=True,
                low_cpu_mem_usage=False,
            )
            giga = giga.to(
                torch.device("cuda" if torch.cuda.is_available() else "cpu")
            ).eval()
            print("GigaAM: OK", flush=True)
        except Exception as e:
            print("GigaAM: пропуск —", e, file=sys.stderr)

    print(f"\nПрогон {len(wavs)} файлов × {len(HYPOTHESES)} гипотез...\n", flush=True)

    for wav in wavs:
        base_prep: dict[str, Path] = {}
        for hyp_tag, af in HYPOTHESES:
            if hyp_tag == "H1_prompt":
                path = base_prep["H0_base"]
            elif af is None:
                path = ffmpeg_prep(wav, work_root / wav.stem, hyp_tag, None)
                base_prep[hyp_tag] = path
            else:
                path = ffmpeg_prep(wav, work_root / wav.stem, hyp_tag, af)

            if fw_model is not None:
                try:
                    t = transcribe_whisper_small(path, fw_model)
                except Exception as e:
                    t = f"ERR:{e}"
                rows.append(("whisper_small", hyp_tag, wav.name, t))

            if silero_bundle is not None:
                model, decoder, read_audio, prepare_model_input, device = silero_bundle
                try:
                    t = transcribe_silero(
                        path, model, decoder, read_audio, prepare_model_input, device
                    )
                except Exception as e:
                    t = f"ERR:{e}"
                rows.append((silero_engine_name, hyp_tag, wav.name, t))

            if giga is not None:
                try:
                    t = transcribe_gigaam(path, giga)
                except Exception as e:
                    t = f"ERR:{e}"
                rows.append(("gigaam_v3", hyp_tag, wav.name, t))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    meta = (
        f"# silero_lang={silero_lang} (public STT has no ru; metrics not RU-quality)\n"
        f"# whisper_small=faster-whisper\n"
        f"# gigaam_v3=local {MODEL_GIGA}\n"
    )
    with args.out.open("w", encoding="utf-8") as f:
        f.write(meta)
        f.write("engine\thypothesis\tfile\ttext\n")
        for eng, hyp, fn, text in rows:
            f.write(f"{eng}\t{hyp}\t{fn}\t{text}\n")

    print(f"TSV: {args.out}")
    bucket_scores([(a, b, d) for a, b, _, d in rows])


if __name__ == "__main__":
    main()
