#!/usr/bin/env python3
"""
Анализ качества звонков Отдела продаж (ОП) по каналу сотрудника.

Логика: стерео WAV — в одном канале клиент, в другом менеджер ОП.
Транскрибируем только канал менеджера (без диаризации и смешивания).
Оценка: правила или LLM при USE_LLM_EVALUATE=1 (evaluate_call_auto).

Использование: python analyze_op_mono.py [путь_к_wav]
По умолчанию: Analytic/ex.wav
Канал сотрудника: EMPLOYEE_CHANNEL=0 или 1 (по умолчанию 1 — правый).
"""

import os
import sys
import tempfile
from pathlib import Path

import soundfile as sf

PROJECT_DIR = Path(__file__).resolve().parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from text_normalization import normalize_transcript
from transfer_tone_mask import mask_transfer_tones_in_file, detect_tonal_segments
from analyze_call_quality import (
    load_stt_model,
    load_gigaam_model,
    transcribe_audio,
    transcribe_audio_gigaam,
    evaluate_call_auto,
    extract_manager_name_from_full_transcript,
    extract_customer_name_from_full_transcript,
)

DEFAULT_OP_FILE = PROJECT_DIR / "Analytic" / "ex.wav"
EMPLOYEE_CHANNEL = int(os.environ.get("EMPLOYEE_CHANNEL", "1"))  # 0=левый, 1=правый


def extract_employee_channel_mono(wav_path: Path, channel: int = 1) -> Path:
    """
    Извлекает один канал (речь сотрудника) из стерео WAV, сохраняет моно во временный файл.
    channel: 0 = левый, 1 = правый.
    """
    audio, sr = sf.read(str(wav_path))
    if audio.ndim == 1:
        return wav_path  # уже моно — возвращаем как есть
    if audio.shape[1] < 2:
        return wav_path
    mono = audio[:, channel]
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    sf.write(path, mono, sr)
    return Path(path)


def extract_both_channels_mono(wav_path: Path) -> Path:
    """
    Смешивает оба канала стерео WAV в моно (L+R)/2 и сохраняет во временный файл.
    Для исходящих и входящих звонков — один транскрипт с речью обеих сторон.
    """
    audio, sr = sf.read(str(wav_path))
    if audio.ndim == 1:
        return wav_path
    if audio.shape[1] < 2:
        return wav_path
    mono = (audio[:, 0].astype(float) + audio[:, 1].astype(float)) / 2.0
    fd, path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    sf.write(path, mono, sr)
    return Path(path)


# Минимальная длина речевого сегмента при двухпроходной транскрипции (сек); короче — пропускаем
MIN_SPEECH_SEGMENT_SEC = 0.8
# Если первый речевой кусок (до тонов) длиннее N сек — считаем, что в начале речь администратора;
# тоны в первых N сек игнорируем, чтобы не резать её (файл 10 и подобные).
HEAD_PROTECT_SEC = 25.0
# Начало речевого сегмента после гудков сдвигаем на N сек раньше (захват приветствия менеджера).
# 1.2 с давало потерю контента в файлах 2, 10 и срез приветствия в 9 — откат к 0.6.
POST_TONE_OVERLAP_SEC = 0.6


def get_tonal_speech_segments(mono_path: Path):
    """
    Детекция тонов и разбиение на речевые интервалы (сэмплы).
    Возвращает (audio, sr, speech_intervals).
    Если тонов нет — speech_intervals = [(0, n)], один сегмент на весь файл.
    """
    audio, sr = sf.read(str(mono_path))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    n = len(audio)
    tonal = detect_tonal_segments(
        audio, sr,
        freq_lo=180.0,
        freq_hi=900.0,
        concentration_threshold=0.35,
        band_energy_ratio_min=0.05,
        min_tonal_sec=0.15,
    )
    if not tonal:
        return audio, sr, [(0, n)]

    overlap_samples = int(POST_TONE_OVERLAP_SEC * sr)
    tonal_sorted = sorted(tonal, key=lambda x: x[0])
    speech_intervals = []
    prev_end = 0
    for s0, s1 in tonal_sorted:
        if s0 > prev_end:
            speech_intervals.append((prev_end, s0))
        prev_end = max(prev_end, max(0, s1 - overlap_samples))
    if prev_end < n:
        speech_intervals.append((prev_end, n))

    head_protect_samples = int(HEAD_PROTECT_SEC * sr)
    has_long_segment = any((b - a) > head_protect_samples for a, b in speech_intervals)
    if has_long_segment:
        tonal_filtered = [(s0, s1) for s0, s1 in tonal_sorted if s0 >= head_protect_samples]
        if tonal_filtered:
            tonal_sorted = sorted(tonal_filtered, key=lambda x: x[0])
            speech_intervals = []
            prev_end = 0
            for s0, s1 in tonal_sorted:
                if s0 > prev_end:
                    speech_intervals.append((prev_end, s0))
                prev_end = max(prev_end, max(0, s1 - overlap_samples))
            if prev_end < n:
                speech_intervals.append((prev_end, n))

    min_samples = int(MIN_SPEECH_SEGMENT_SEC * sr)
    if speech_intervals:
        first = speech_intervals[0]
        rest = [(a, b) for a, b in speech_intervals[1:] if (b - a) >= min_samples]
        speech_intervals = [first] + rest
    else:
        speech_intervals = [(a, b) for a, b in speech_intervals if (b - a) >= min_samples]
    if not speech_intervals:
        return audio, sr, [(0, n)]
    return audio, sr, speech_intervals


def _transcribe_by_tone_segments(mono_path: Path, model, use_gigaam: bool = False) -> str:
    """
    Двухпроходная транскрипция (вариант C): детекция гудков, разрез аудио на сегменты
    «до гудков» и «после гудков», транскрипция каждого отдельно, склейка.
    Цель — не терять приветствие после мелодии перевода и не резать речь администратора в начале.
    use_gigaam: если True — транскрибировать сегменты через GigaAM (с нормализацией).
    """
    audio, sr = sf.read(str(mono_path))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    n = len(audio)
    # В смешанном моно (L+R)/2 гудки слабее — используем более мягкие пороги для детекции
    tonal = detect_tonal_segments(
        audio, sr,
        freq_lo=180.0,
        freq_hi=900.0,
        concentration_threshold=0.35,
        band_energy_ratio_min=0.05,
        min_tonal_sec=0.15,
    )
    if not tonal:
        print(f"  ⚠️ Тональных участков не обнаружено — транскрипция файла целиком (вариант C не сработал)", flush=True)
        txt, _, _ = transcribe_audio(
            mono_path, model,
            vad_filter=False,
            no_speech_threshold=0.4,
        )
        return txt

    overlap_samples = int(POST_TONE_OVERLAP_SEC * sr)
    tonal_sorted = sorted(tonal, key=lambda x: x[0])
    speech_intervals: list = []
    prev_end = 0
    for s0, s1 in tonal_sorted:
        if s0 > prev_end:
            speech_intervals.append((prev_end, s0))
        # Конец тона сдвигаем раньше — следующий речевой сегмент начнётся на OVERLAP раньше (не режем приветствие)
        prev_end = max(prev_end, max(0, s1 - overlap_samples))
    if prev_end < n:
        speech_intervals.append((prev_end, n))

    # Защита начала: если есть длинный речевой кусок (> HEAD_PROTECT_SEC), в начале файла речь
    # администратора — тоны в первых N сек не режем (ложные срабатывания в файле 10)
    head_protect_samples = int(HEAD_PROTECT_SEC * sr)
    has_long_segment = any((b - a) > head_protect_samples for a, b in speech_intervals)
    if has_long_segment:
        tonal_filtered = [(s0, s1) for s0, s1 in tonal_sorted if s0 >= head_protect_samples]
        if tonal_filtered:
            tonal_sorted = sorted(tonal_filtered, key=lambda x: x[0])
            speech_intervals = []
            prev_end = 0
            for s0, s1 in tonal_sorted:
                if s0 > prev_end:
                    speech_intervals.append((prev_end, s0))
                prev_end = max(prev_end, max(0, s1 - overlap_samples))
            if prev_end < n:
                speech_intervals.append((prev_end, n))
            print(f"  🛡 Тоны в первых {HEAD_PROTECT_SEC:.0f} с отброшены (длинный речевой кусок в начале)", flush=True)

    min_samples = int(MIN_SPEECH_SEGMENT_SEC * sr)
    # Первый сегмент не отбрасываем по длине — там может быть речь администратора с начала файла
    if speech_intervals:
        first = speech_intervals[0]
        rest = [(a, b) for a, b in speech_intervals[1:] if (b - a) >= min_samples]
        speech_intervals = [first] + rest
    else:
        speech_intervals = [(a, b) for a, b in speech_intervals if (b - a) >= min_samples]
    if not speech_intervals:
        if use_gigaam:
            txt, _, _ = transcribe_audio_gigaam(mono_path, model)
        else:
            txt, _, _ = transcribe_audio(
                mono_path, model,
                vad_filter=False,
                no_speech_threshold=0.4,
            )
        return txt

    # Лог границ в секундах (для отладки: приветствие после гудков должно попасть в первый речевой сегмент после тона)
    for i, (s0, s1) in enumerate(tonal_sorted):
        print(f"  🔇 Тон {i + 1}: {s0 / sr:.1f}–{s1 / sr:.1f} с", flush=True)
    for i, (a, b) in enumerate(speech_intervals):
        print(f"  ✂️ Речь {i + 1}: {a / sr:.1f}–{b / sr:.1f} с ({(b - a) / sr:.1f} с)", flush=True)
    temp_files: list = []
    pieces: list = []
    try:
        for i, (start, end) in enumerate(speech_intervals):
            seg_audio = audio[start:end]
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            temp_files.append(Path(path))
            sf.write(path, seg_audio, sr)
            if use_gigaam:
                seg_txt, _, _ = transcribe_audio_gigaam(Path(path), model)
            else:
                # Сегменты после тонов (i > 0): ниже порог «нет речи», чтобы не терять приветствие менеджера
                no_speech = 0.3 if i > 0 else 0.4
                seg_txt, _, _ = transcribe_audio(
                    Path(path), model,
                    vad_filter=False,
                    no_speech_threshold=no_speech,
                    no_initial_prompt=(i > 0),
                )
            if seg_txt and seg_txt.strip():
                pieces.append(seg_txt.strip())
        return " ".join(pieces) if pieces else ""
    finally:
        for p in temp_files:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass


def run_op_mono(
    wav_path: Path,
    employee_channel: int = EMPLOYEE_CHANNEL,
    model=None,
    use_both_channels: bool = False,
    use_mask_transfer_tones: bool = False,
    use_segment_by_tones: bool = False,
    use_gigaam: bool = False,
) -> dict:
    """
    Обработка одного файла ОП: извлечение канала (или обоих), опционально маскирование
    тонов перевода или двухпроходная транскрипция по сегментам (речь до/после гудков).
    model: если передан, используется он; иначе загружается через load_stt_model() или load_gigaam_model().
    use_gigaam: если True — использовать GigaAM для STT (результат нормализуется так же, как для Whisper).
    use_both_channels: если True — смешиваем оба канала в моно (продакт SALE).
    use_mask_transfer_tones: если True — заменяем тональные участки на тишину перед STT.
    use_segment_by_tones: если True — детектируем гудки, режем аудио на сегменты до/после,
      транскрибируем каждый отдельно и склеиваем (вариант C: сохранить приветствие после гудков).
    """
    if not wav_path.exists():
        return {"error": f"Файл не найден: {wav_path}", "transcript": "", "scores": {}}

    temp_mono = None
    try:
        if use_both_channels:
            print(f"  🔊 Смешивание обоих каналов в моно...", flush=True)
            temp_mono = extract_both_channels_mono(wav_path)
        else:
            print(f"  🔊 Извлечение канала сотрудника (канал {employee_channel})...", flush=True)
            temp_mono = extract_employee_channel_mono(wav_path, employee_channel)

        if use_mask_transfer_tones and not use_segment_by_tones:
            print(f"  🔇 Маскирование тонов перевода...", flush=True)
            masked_path = mask_transfer_tones_in_file(temp_mono)
            if masked_path != temp_mono and temp_mono.exists():
                try:
                    temp_mono.unlink()
                except Exception:
                    pass
            temp_mono = masked_path

        if model is None:
            model = load_gigaam_model() if use_gigaam else load_stt_model()

        if use_segment_by_tones:
            transcript = _transcribe_by_tone_segments(temp_mono, model, use_gigaam=use_gigaam)
        else:
            if use_gigaam:
                transcript, _, _ = transcribe_audio_gigaam(temp_mono, model)
            else:
                transcript, _, _ = transcribe_audio(
                    temp_mono, model,
                    vad_filter=False,
                    no_speech_threshold=0.4,
                )
        transcript = normalize_transcript(transcript)

        scores = evaluate_call_auto(transcript)
        manager_name = extract_manager_name_from_full_transcript(transcript) or "—"
        customer_name = extract_customer_name_from_full_transcript(transcript, manager_name) or "Не указано"

        return {
            "file": str(wav_path.name),
            "transcript": transcript,
            "scores": scores,
            "manager_name": manager_name,
            "customer_name": customer_name,
        }
    finally:
        if temp_mono and temp_mono != wav_path and temp_mono.exists():
            try:
                temp_mono.unlink()
            except Exception:
                pass


def main():
    wav_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OP_FILE
    print(f"=== ОП (моно-канал сотрудника): {wav_path} ===", flush=True)
    result = run_op_mono(wav_path)
    if "error" in result:
        print(f"Ошибка: {result['error']}", flush=True)
        return 1
    print(f"Менеджер: {result['manager_name']}, Клиент: {result['customer_name']}", flush=True)
    print(f"Общий балл: {result['scores'].get('total_score', 0):.2f}/5.0", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
