#!/usr/bin/env python3
"""
Оффлайн демо-бот «Викинги»:
- STT: локальное распознавание речи (внешняя ASR-библиотека)
- Диалог: стейт-машина + RAG по предзаписанным WAV-ответам
- LLM: опционально, по флагу USE_LLM в config.py
- TTS: Silero v5 (локальная модель)

Режим работы:
1) При запуске проигрывает приветствие.
2) Слушает микрофон, распознаёт речь.
3) Прогоняет текст через стейт-машину/интенты.
4) Отвечает голосом через Silero TTS или предзаписанные WAV.
"""

import asyncio
import logging
import math
import queue
import re
from collections import deque
from datetime import datetime
from pathlib import Path
import numpy as np
import sounddevice as sd
from faster_whisper import WhisperModel

from config import (
    AUDIO_RESPONSES_DIR,
    CLIENTS_BASE_PATH,
    LEADS_PATH,
    NORMA_PATH,
    SLOT_PATH,
    SILERO_SAMPLE_RATE,
    SILERO_SPEAKER,
    SILERO_SPEED,
    SILERO_V5_PATH,
    INPUT_DEVICE,
    OUTPUT_DEVICE,
    USE_LLM,
    USE_RAG,
    WHISPER_DEVICE,
    WHISPER_MODEL_PATH_VOICE,
    USE_STREAMING_STT,
    USE_VAD,
    VAD_SAMPLE_RATE,
    VAD_FRAME_SIZE,
    VAD_SILENCE_THRESHOLD_MS,
    VAD_SPEECH_THRESHOLD,
    MAX_RECORD_DURATION_SEC,
)
from asterisk_transfer import (
    get_exten_for_wav,
    is_transfer_to_admin_wav,
    request_transfer_to_admin,
)
from gpu_priority_lock import acquire_voice_bot_gpu, release_voice_bot_gpu
from dialog.conversation_state import (
    ConversationStateMachine,
    ConversationState,
    ClientNeed,
)
from dialog.name_extractor import NameExtractor
from dialog.date_parser import DateParser
from dialog.database_manager import DatabaseManager
from dialog.leads_manager import LeadsManager
from dialog.rag_system_states import RAGSystemStates, WAV_FILE_TEXTS
from dialog.car_brand_extractor import CarBrandExtractor


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Списки «да»/«нет» — проверять как отдельные слова (граница слова), чтобы «да» не срабатывало в «свидания»
POSITIVE_WORDS = [
    "да",
    "верно",
    "правильно",
    "ага",
    "конечно",
    "точно",
    "именно",
    "подтверждаю",
    "согласен",
    "давайте",
    "угу",
    "ок",
    "окей",
    "хорошо",
]
NEGATIVE_WORDS = [
    "нет",
    "неверно",
    "ошиблись",
    "не то",
    "другой",
    "неправильно",
    "никак",
    "отнюдь",
    "ничуть",
]


def number_to_words_ru(num: int) -> str:
    """Простое числительное на русском для 0–999 (для времени и моделей авто)."""
    units = {
        0: "ноль",
        1: "один",
        2: "два",
        3: "три",
        4: "четыре",
        5: "пять",
        6: "шесть",
        7: "семь",
        8: "восемь",
        9: "девять",
    }
    teens = {
        10: "десять",
        11: "одиннадцать",
        12: "двенадцать",
        13: "тринадцать",
        14: "четырнадцать",
        15: "пятнадцать",
        16: "шестнадцать",
        17: "семнадцать",
        18: "восемнадцать",
        19: "девятнадцать",
    }
    tens = {
        2: "двадцать",
        3: "тридцать",
        4: "сорок",
        5: "пятьдесят",
        6: "шестьдесят",
        7: "семьдесят",
        8: "восемьдесят",
        9: "девяносто",
    }
    hundreds = {
        1: "сто",
        2: "двести",
        3: "триста",
        4: "четыреста",
        5: "пятьсот",
        6: "шестьсот",
        7: "семьсот",
        8: "восемьсот",
        9: "девятьсот",
    }

    if num < 0 or num > 999:
        return str(num)
    if num < 10:
        return units[num]
    if num < 20:
        return teens[num]
    if num < 100:
        d, u = divmod(num, 10)
        if u == 0:
            return tens.get(d, str(num))
        return f"{tens.get(d, '')} {units[u]}".strip()

    h, rem = divmod(num, 100)
    parts = [hundreds.get(h, "")]
    if rem == 0:
        return parts[0]
    if rem < 10:
        parts.append(units[rem])
    elif rem < 20:
        parts.append(teens[rem])
    else:
        d, u = divmod(rem, 10)
        parts.append(tens.get(d, ""))
        if u:
            parts.append(units[u])
    return " ".join(p for p in parts if p)


def day_to_ordinal_ru(day: int) -> str:
    """Преобразует число дня месяца в порядковое числительное в родительном падеже (например, 30 -> "тридцатое")."""
    if day < 1 or day > 31:
        return str(day)
    
    # Специальные случаи
    special = {
        1: "п+ервое",
        2: "втор+ое",
        3: "тр+етье",
        4: "четв+ертое",
        5: "п+ятое",
        6: "шест+ое",
        7: "седьм+ое",
        8: "восьм+ое",
        9: "дев+ятое",
        10: "десят+ое",
        11: "од+иннадцатое",
        12: "двен+адцатое",
        13: "трин+адцатое",
        14: "чет+ырнадцатое",
        15: "пятн+адцатое",
        16: "шестн+адцатое",
        17: "семн+адцатое",
        18: "восемн+адцатое",
        19: "девятн+адцатое",
        20: "двадц+атого",
        21: "двадцать п+ервое",
        22: "двадцать втор+ое",
        23: "двадцать тр+етье",
        24: "двадцать четв+ертого",
        25: "двадцать п+ятого",
        26: "двадцать шест+ого",
        27: "двадцать с+едьмого",
        28: "двадцать восьм+ого",
        29: "двадцать дев+ятого",
        30: "тридц+атого",
        31: "тридцать п+ервое",
    }
    
    if day in special:
        return special[day]
    
    # Для остальных чисел формируем составное числительное
    if day < 20:
        return str(day)  # Fallback
    elif day < 30:
        return f"двадцать {day_to_ordinal_ru(day - 20).replace('+', '')}"
    else:
        return str(day)  # Fallback для 31+


def replace_numbers_for_tts(text: str) -> str:
    """Преобразует числа и время в текст для более естественного TTS."""

    def time_repl(m: re.Match) -> str:
        h = int(m.group(1))
        mi = int(m.group(2))
        h_words = number_to_words_ru(h)
        if mi == 0:
            return f"{h_words} ноль ноль"
        m_words = number_to_words_ru(mi)
        return f"{h_words} {m_words}"

    # Сначала время вида 10:00
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", time_repl, text)
    text = re.sub(r"\b(\d{1,2})-(\d{2})\b", time_repl, text)

    def num_repl(m: re.Match) -> str:
        try:
            n = int(m.group(0))
        except ValueError:
            return m.group(0)
        return number_to_words_ru(n)

    # Затем отдельные числа до 3-х знаков (например, 408)
    text = re.sub(r"\b\d{1,3}\b", num_repl, text)
    return text


class SileroTTS:
    """Обёртка над Silero v5, адаптированная под демо-бота."""

    def __init__(self) -> None:
        self.model = None
        self.initialized = False
        self.sample_rate = SILERO_SAMPLE_RATE

    async def init(self) -> None:
        if self.initialized:
            return

        from torch import package
        import torch

        logger.info("Загрузка модели Silero v5 из %s ...", SILERO_V5_PATH)

        def _load():
            if not SILERO_V5_PATH.exists():
                raise RuntimeError(f"Файл модели TTS не найден: {SILERO_V5_PATH}")
            importer = package.PackageImporter(str(SILERO_V5_PATH))
            model = importer.load_pickle("tts_models", "model")
            
            # Проверяем, что модель загружена
            if model is None:
                raise RuntimeError("Модель TTS не загрузилась")
            
            # Silero TTS v5 не поддерживает перенос на GPU через .to('cuda')
            # Модель работает на CPU, но синтез достаточно быстрый
            # GPU используется для STT, что более критично
            logger.info("Модель TTS работает на CPU (Silero TTS не поддерживает GPU)")
            
            # Финальная проверка, что модель не None
            if model is None:
                raise RuntimeError("Модель TTS стала None после инициализации")
            
            return model

        loop = asyncio.get_running_loop()
        self.model = await loop.run_in_executor(None, _load)
        self.initialized = True
        logger.info("Модель Silero v5 загружена.")

    async def speak(self, text: str) -> np.ndarray:
        """Синтез речи, возвращает массив PCM float32."""
        if not self.initialized:
            await self.init()

        text = text.strip()
        if not text:
            return np.zeros(0, dtype=np.float32)

        # Подготовка текста: числа/время → слова
        text = replace_numbers_for_tts(text)

        logger.info("TTS: %s", text)

        def _synth():
            import torch
            if self.model is None:
                raise RuntimeError("Модель TTS не инициализирована (None)")
            
            if hasattr(self.model, "apply_tts"):
                audio = self.model.apply_tts(
                    text=text,
                    speaker=SILERO_SPEAKER,
                    sample_rate=self.sample_rate,
                )
            else:
                audio = self.model(text, speaker=SILERO_SPEAKER)
            
            # Если аудио на GPU, переносим на CPU для numpy
            if isinstance(audio, torch.Tensor):
                if audio.is_cuda:
                    audio = audio.cpu()
                return audio.numpy().astype(np.float32)
            elif hasattr(audio, 'cpu') and hasattr(audio, 'numpy'):
                # Если это tensor-подобный объект с методами cpu() и numpy()
                try:
                    if hasattr(audio, 'is_cuda') and audio.is_cuda:
                        audio = audio.cpu()
                    return audio.numpy().astype(np.float32)
                except (AttributeError, RuntimeError):
                    pass
            # Если уже numpy массив или другой тип
            return np.array(audio, dtype=np.float32)

        loop = asyncio.get_running_loop()
        audio = await loop.run_in_executor(None, _synth)
        return audio


def detect_speech_energy(audio_chunk: np.ndarray, threshold: float = 0.01) -> bool:
    """Простой VAD на основе энергии сигнала."""
    if audio_chunk.size == 0:
        return False
    energy = np.mean(np.abs(audio_chunk))
    return energy > threshold


class AudioIO:
    """Запись с микрофона и воспроизведение звука с поддержкой VAD и streaming."""

    def __init__(self) -> None:
        self.input_device = INPUT_DEVICE
        self.output_device = OUTPUT_DEVICE
        self.audio_queue: queue.Queue = queue.Queue()
        
        # Определяем нативную частоту микрофона
        if self.input_device is not None:
            try:
                device_info = sd.query_devices(self.input_device)
                self.native_sample_rate = int(device_info['default_samplerate'])
                logger.info(f"Нативная частота микрофона (устройство {self.input_device}): {self.native_sample_rate} Hz")
            except Exception as e:
                logger.warning(f"Не удалось определить частоту микрофона: {e}, используем 44100 Hz")
                self.native_sample_rate = 44100
        else:
            self.native_sample_rate = 44100

    async def record_with_vad(
        self,
        sample_rate: int = VAD_SAMPLE_RATE,
        frame_size: int = VAD_FRAME_SIZE,
        silence_threshold_ms: int = VAD_SILENCE_THRESHOLD_MS,
        max_duration_sec: float = 30.0,
    ) -> np.ndarray:
        """Запись с микрофона с VAD - останавливается при тишине."""
        # Используем нативную частоту микрофона для записи
        record_sample_rate = self.native_sample_rate
        # Адаптируем frame_size для нативной частоты (сохраняем длительность фрейма)
        record_frame_size = int(frame_size * record_sample_rate / sample_rate)
        
        logger.info("Начало записи с VAD (макс. %.1f сек, частота записи: %d Hz)...", max_duration_sec, record_sample_rate)
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break
        audio_buffer = []
        silence_frames = 0
        # Порог тишины в фреймах для нативной частоты
        silence_frames_threshold = int((silence_threshold_ms / 1000.0) * record_sample_rate / record_frame_size)
        max_frames = int(max_duration_sec * record_sample_rate / record_frame_size)
        frames_recorded = 0
        speech_detected = False
        
        def audio_callback(indata, frames, time, status):
            if status:
                logger.warning("Аудио статус: %s", status)
            chunk = indata[:, 0].astype(np.float32)
            self.audio_queue.put(chunk.copy())
        
        stream = sd.InputStream(
            samplerate=record_sample_rate,
            channels=1,
            dtype="float32",
            device=self.input_device,
            blocksize=record_frame_size,
            callback=audio_callback,
        )
        
        try:
            stream.start()
            logger.info("Поток записи запущен, ожидание речи...")
            
            while frames_recorded < max_frames:
                try:
                    chunk = self.audio_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                
                audio_buffer.append(chunk)
                frames_recorded += 1
                
                # VAD: определяем наличие речи (порог выше фона — быстрее считаем тишину после речи)
                energy_threshold = VAD_SPEECH_THRESHOLD * 0.012
                has_speech = detect_speech_energy(chunk, threshold=energy_threshold)
                
                if has_speech:
                    speech_detected = True
                    silence_frames = 0
                else:
                    silence_frames += 1
                    # Если была речь и теперь тишина достаточно долго - останавливаемся
                    if speech_detected and silence_frames >= silence_frames_threshold:
                        logger.info("Обнаружена тишина после речи, остановка записи.")
                        break
            
            if not speech_detected:
                logger.warning("Речь не обнаружена за время записи.")
            
        finally:
            stream.stop()
            stream.close()
        
        if audio_buffer:
            recording = np.concatenate(audio_buffer)
            logger.info("Запись завершена. Длина: %.2f сек (частота: %d Hz)", len(recording) / record_sample_rate, record_sample_rate)
            
            # Ресемплируем до требуемой частоты для STT
            if record_sample_rate != sample_rate:
                from scipy import signal
                num_samples = int(len(recording) * sample_rate / record_sample_rate)
                recording = signal.resample(recording, num_samples)
                logger.info("Ресемплинг: %d Hz -> %d Hz", record_sample_rate, sample_rate)
            
            return recording
        return np.array([], dtype=np.float32)

    async def record_once(self, duration_sec: float = 5.0, sample_rate: int = 16000) -> np.ndarray:
        """Запись аудио с микрофона фиксированной длины (fallback если VAD отключен)."""
        if USE_VAD:
            return await self.record_with_vad(sample_rate=sample_rate, max_duration_sec=duration_sec)
        
        # Используем нативную частоту микрофона для записи
        record_sample_rate = self.native_sample_rate
        
        logger.info("Начало записи с микрофона (%.1f сек, частота: %d Hz)...", duration_sec, record_sample_rate)

        def _record():
            return sd.rec(
                int(duration_sec * record_sample_rate),
                samplerate=record_sample_rate,
                channels=1,
                dtype="float32",
                device=self.input_device,
            )

        loop = asyncio.get_running_loop()
        recording = await loop.run_in_executor(None, _record)

        def _wait():
            sd.wait()

        await loop.run_in_executor(None, _wait)
        
        recording = recording.flatten()
        
        # Ресемплируем до требуемой частоты для STT
        if record_sample_rate != sample_rate:
            from scipy import signal
            num_samples = int(len(recording) * sample_rate / record_sample_rate)
            recording = signal.resample(recording, num_samples)
            logger.info("Ресемплинг: %d Hz -> %d Hz", record_sample_rate, sample_rate)
        
        logger.info("Запись завершена.")
        return recording

    async def play_audio(self, audio: np.ndarray, sample_rate: int) -> None:
        if audio.size == 0:
            return

        # Пауза в начале, чтобы буфер ALSA успел заполниться (убирает заикание на первых буквах)
        silence_ms = 0.12  # 120 ms — 50 ms было недостаточно
        n_silence = int(silence_ms * sample_rate)
        if audio.ndim == 1:
            silence = np.zeros(n_silence, dtype=np.float32)
        else:
            silence = np.zeros((n_silence, audio.shape[1]), dtype=np.float32)
        audio = np.concatenate([silence, audio])

        # Применяем изменение скорости через изменение sample_rate
        effective_sample_rate = int(sample_rate * SILERO_SPEED)

        def _play():
            # Воспроизведение через OutputStream без finished_callback,
            # чтобы избежать AttributeError: out внутри sounddevice.
            old_latency = getattr(sd.default, "latency", None)
            old_blocksize = getattr(sd.default, "blocksize", None)
            try:
                sd.default.latency = "high"
                sd.default.blocksize = 4096
                channels = 1 if audio.ndim == 1 else audio.shape[1]
                # Приводим к форме (frames, channels)
                data = audio.astype(np.float32, copy=False)
                if data.ndim == 1:
                    data = data.reshape(-1, 1)
                with sd.OutputStream(
                    samplerate=effective_sample_rate,
                    channels=channels,
                    dtype="float32",
                    device=self.output_device,
                    blocksize=4096,
                ) as stream:
                    stream.write(data)
                    stream.stop()
            finally:
                if old_latency is not None:
                    sd.default.latency = old_latency
                if old_blocksize is not None:
                    sd.default.blocksize = old_blocksize

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _play)


class DemoBot:
    def __init__(self) -> None:
        self.session_uuid = "demo-session-001"
        self.state_machine = ConversationStateMachine(session_uuid=self.session_uuid)
        self.name_extractor = NameExtractor()
        self.date_parser = DateParser()
        self.car_brand_extractor = CarBrandExtractor()
        self.db_manager = DatabaseManager(CLIENTS_BASE_PATH, NORMA_PATH, SLOT_PATH)
        self.leads_manager = LeadsManager(LEADS_PATH)
        self.rag_system = (
            RAGSystemStates(AUDIO_RESPONSES_DIR, 0.85) if USE_RAG else None
        )

        self.tts = SileroTTS()
        self.audio_io = AudioIO()
        self.service_day_part: str | None = None  # пожелание по времени суток

        # WHISPER_MODEL_PATH_VOICE — компактная модель для демо-бота.
        device = WHISPER_DEVICE
        compute_type = "float16" if device == "cuda" else "int8"
        logger.info(f"Инициализация STT: {WHISPER_MODEL_PATH_VOICE}, device={device}, compute_type={compute_type}")
        try:
            self.whisper_model = WhisperModel(
                WHISPER_MODEL_PATH_VOICE,
                device=device,
                compute_type=compute_type,
                local_files_only=True,
            )
        except RuntimeError as e:
            if "CUDA" in str(e) and device == "cuda":
                logger.warning(f"Ошибка CUDA: {e}. Переключаюсь на CPU.")
                device = "cpu"
                compute_type = "int8"
                self.whisper_model = WhisperModel(
                    WHISPER_MODEL_PATH_VOICE,
                    device=device,
                    compute_type=compute_type,
                    local_files_only=True,
                )
                logger.info("STT успешно инициализирован на CPU (fallback)")
            else:
                raise

        self.silence_rounds = 0
        self.last_bot_phrase: tuple[str | None, str | None] = ("01_greeting.wav", None)
        self._wav_cache: dict[str, tuple[np.ndarray, int]] = {}
        # При работе за Asterisk: идентификатор канала для AMI Redirect (передаётся при установке звонка)
        self.asterisk_channel_id: str | None = None

    def _load_wav_cache(self) -> None:
        """Предзагрузка WAV из audio_responses в память для сокращения паузы до ответа."""
        import soundfile as sf
        self._wav_cache.clear()
        for path in sorted(AUDIO_RESPONSES_DIR.glob("*.wav")):
            try:
                data, fs = sf.read(path)
                self._wav_cache[path.name] = (data.astype(np.float32), int(fs))
            except Exception as e:
                logger.warning("Не удалось загрузить в кэш %s: %s", path.name, e)
        logger.info("Кэш WAV загружен: %d файлов", len(self._wav_cache))

    async def run(self) -> None:
        # Предзагрузка модели TTS для ускорения первого ответа
        logger.info("Предзагрузка модели TTS...")
        await self.tts.init()
        # Предзагрузка WAV в память
        self._load_wav_cache()

        # Приветствие
        await self.play_wav_or_tts("01_greeting.wav", None)

        while True:
            # Параллельный пайплайн: запись и обработка одновременно
            # 1. Начинаем запись с VAD (макс. 3 сек — меньше пауза до ответа)
            record_task = asyncio.create_task(
                self.audio_io.record_once(
                    duration_sec=MAX_RECORD_DURATION_SEC,
                    sample_rate=VAD_SAMPLE_RATE,
                )
            )
            
            # 2. Ждём завершения записи
            record_end_time = datetime.now()
            user_audio = await record_task
            record_duration = (datetime.now() - record_end_time).total_seconds()
            
            # 3. STT обработка (запускаем сразу после получения аудио)
            stt_start_time = datetime.now()
            if user_audio.size > 0:
                # Запускаем STT
                user_text = await self.stt(user_audio)
            else:
                user_text = ""
            stt_duration = (datetime.now() - stt_start_time).total_seconds()

            if not user_text.strip():
                self.silence_rounds += 1
                if self.silence_rounds == 1:
                    await self.play_wav_or_tts("17_repeat.wav", None)
                    continue
                else:
                    await self.say("Прошу прощения, я Вас не слышу. До свидания!")
                    break

            self.silence_rounds = 0
            logger.info("КЛИЕНТ: %s (STT: %.2f сек)", user_text, stt_duration)
            
            # Проверяем, не завершен ли разговор
            if self.state_machine.state == ConversationState.ENDED:
                break
            
            # Обработка текста
            process_start_time = datetime.now()
            await self.process_client_text(user_text)
            process_duration = (datetime.now() - process_start_time).total_seconds()
            total_pause = stt_duration + process_duration
            logger.info("Пауза до ответа: %.2f сек (STT: %.2f сек, обработка: %.2f сек)", total_pause, stt_duration, process_duration)

            if self.state_machine.state == ConversationState.ENDED:
                break

    async def stt(self, audio: np.ndarray) -> str:
        """Локальный STT (обычный или streaming)."""
        if audio.size == 0:
            return ""

        loop = asyncio.get_running_loop()
        
        def _transcribe():
            # STT ожидает float32, 16 kHz
            if USE_STREAMING_STT and len(audio) > 16000:  # Streaming для длинных записей
                # Для streaming используем transcribe с chunk_size
                segments, _info = self.whisper_model.transcribe(
                    audio,
                    language="ru",
                    vad_filter=True,  # Встроенный VAD фильтр
                    vad_parameters=dict(
                        threshold=0.5,
                        min_speech_duration_ms=250,
                        max_speech_duration_s=float("inf"),
                        min_silence_duration_ms=500,
                    ),
                )
            else:
                segments, _info = self.whisper_model.transcribe(audio, language="ru")
            
            text_parts = [seg.text for seg in segments]
            return " ".join(text_parts).strip()
        
        acquire_voice_bot_gpu()
        try:
            return await loop.run_in_executor(None, _transcribe)
        finally:
            release_voice_bot_gpu()

    async def say(self, text: str) -> None:
        """Озвучка текста через TTS."""
        # Произношение «Тэнет» (Т+энет), а не «Тенет» (Тен+ет) — заменяем перед TTS
        text = (text or "").replace("Тенет", "Тэнет").replace("тенет", "тэнет")
        self.last_bot_phrase = (None, text)
        tts_start = datetime.now()
        audio = await self.tts.speak(text)
        tts_duration = (datetime.now() - tts_start).total_seconds()
        logger.info("TTS синтез: %.2f сек", tts_duration)
        play_start = datetime.now()
        await self.audio_io.play_audio(audio, SILERO_SAMPLE_RATE)
        play_duration = (datetime.now() - play_start).total_seconds()
        logger.info("Воспроизведение: %.2f сек", play_duration)

    async def play_wav_or_tts(self, wav_file: str | None, text: str | None) -> None:
        """Проиграть предзаписанный WAV (если есть), иначе проговорить текст."""
        self.last_bot_phrase = (wav_file, text)

        if wav_file:
            # Сначала из кэша (предзагружено при старте)
            cached = self._wav_cache.get(wav_file)
            if cached is not None:
                data, fs = cached
                logger.info("БОТ [WAV]: %s", WAV_FILE_TEXTS.get(wav_file, ""))
                await self.audio_io.play_audio(data, fs)
                self._maybe_signal_asterisk_transfer(wav_file)
                return
            path = AUDIO_RESPONSES_DIR / wav_file
            if path.exists():
                import soundfile as sf
                data, fs = sf.read(path)
                data = data.astype(np.float32)
                self._wav_cache[wav_file] = (data, int(fs))
                logger.info("БОТ [WAV]: %s", WAV_FILE_TEXTS.get(wav_file, ""))
                await self.audio_io.play_audio(data, fs)
                self._maybe_signal_asterisk_transfer(wav_file)
                return
            # Если файла нет — fallback на текст из таблицы
            text = WAV_FILE_TEXTS.get(wav_file, text)

        if text:
            await self.say(text)

    def _maybe_signal_asterisk_transfer(self, wav_file: str) -> None:
        """После воспроизведения WAV перевода — сигнал Asterisk AMI (если настроено и есть channel_id)."""
        if not is_transfer_to_admin_wav(wav_file):
            return
        channel_id = getattr(self, "asterisk_channel_id", None)
        if not channel_id:
            return
        exten = get_exten_for_wav(wav_file)
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, lambda: request_transfer_to_admin(channel_id, exten=exten))

    async def process_client_text(self, text: str) -> None:
        # Локальный фильтр шумовых фраз (по запросу)
        t_low = (text or "").lower()
        noise_phrases = [
            "подписывайтесь на наш канал",
            "подпишитесь на наш канал",
            "подписывайтесь на наши каналы",
            "подпишитесь на наши каналы",
        ]
        if any(p in t_low for p in noise_phrases):
            await self.play_wav_or_tts("17_repeat.wav", None)
            return

        state = self.state_machine.state
        if state == ConversationState.INITIAL:
            await self._handle_initial_state(text)
        elif state == ConversationState.AFTER_HOURS_NAME:
            await self._handle_after_hours_name_state(text, text)
        elif state == ConversationState.ASKING_NAME:
            await self._handle_asking_name_state(text)
        elif state == ConversationState.NEED_IDENTIFIED:
            await self._handle_need_identified_state(text)
        elif state == ConversationState.SERVICE_DATA_COLLECTION:
            await self._handle_service_data_collection_state(text)
        elif state == ConversationState.SERVICE_SLOT_SELECTION:
            await self._handle_service_slot_selection_state(text)
        elif state == ConversationState.SERVICE_BOOKED:
            await self._handle_service_booked_state(text)
        elif state == ConversationState.TRANSFERRING:
            await self._handle_transferring_state(text)
        else:
            await self._handle_default_state(text)

    async def _handle_initial_state(self, text: str) -> None:
        t_low = text.lower()
        
        # Прямое распознавание запросов на соединение с отделом "автомобили с пробегом"
        if any(phrase in t_low for phrase in [
            "соедините с авто с пробегом", "соедините с автомобилями с пробегом",
            "авто с пробегом", "автомобили с пробегом", "отдел пробег",
            "переведите на пробег", "перевести на пробег", "с пробегом соедините"
        ]):
            need = ClientNeed.USED_CARS
            self.state_machine.set_identified_need(need)
            self.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
            await self._confirm_need(need)
            return
        
        # Упрощенная схема интентов не требует уточнения бренда

        # Фильтр шумовых фраз отключен по запросу пользователя

        name = None
        if not self.state_machine.client_name:
            name = self.name_extractor.extract_name(text)

        t_low = text.lower()
        # Вопросы про другие марки / не Ч+ери и не Т+энет
        other_brand_keywords = [
            "джили", "geely", "мерседес", "mercedes", "бмв", "bmw", "ауди", "audi",
            "тойота", "toyota", "ниссан", "nissan", "лада", "ваз", "hyundai", "хьендай", "хендай",
            "киа", "kia", "мазда", "mazda", "митсубиси", "mitsubishi",
        ]
        if (
            ("какие" in t_low or "другие" in t_low or "еще" in t_low or "кроме" in t_low)
            and ("марк" in t_low or "автомоб" in t_low or "машин" in t_low or "бренд" in t_low)
        ) or ("не чери" in t_low or "не тенет" in t_low or "не тэнет" in t_low) or any(
            b in t_low for b in other_brand_keywords
        ):
            await self.play_wav_or_tts("05_transfer_chery_tenet.wav", None)
            self.state_machine.transition_to(ConversationState.ENDED)
            return

        # Выкуп/продажа автомобиля клиентом — отдел "Автомобили с пробегом"
        has_sell = any(w in t_low for w in ["продать", "продажа", "выкуп", "сдать", "оценить", "выкупить"])
        has_car = bool(re.search(r"автомобил|авто|машин", t_low))
        if has_sell and has_car:
            self.state_machine.set_identified_need(ClientNeed.USED_CARS)
            self.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
            await self._confirm_need(ClientNeed.USED_CARS)
            return

        # Слова-маркеры сервиса: запись на сервис, ТО, ремонт, замена деталей, шиномонтаж, диагностика
        service_markers = [
            "на сервис",
            "записать на сервис",
            "запись на сервис",
            "машину на сервис",
            "на сервис записать",
            "сервис",
            "ремонт",
            "обслуживание",
            "техобслуживание",
            "на замену",
            "заменить",
            "шиномонтаж",
            "диагностика",
            "установить",
            "найти неисправность",
            "мойка",
            "мойку",
            "мойке",
            "мойки",
            "автомойка",
            "автомойку",
            "автомойке",
            "автомойки",
            "приемка",
            "приемку",
            "приемке",
            "приемки",
            "приёмка",
            "приёмку",
            "приёмке",
            "приёмки",
        ]
        if any(m in t_low for m in service_markers) or re.search(r"\bто\b", t_low):
            need = ClientNeed.SERVICE
            self.state_machine.set_identified_need(need)
            self.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
            await self._confirm_need(need)
            return

        need = self._identify_need(text)

        if name:
            self.state_machine.set_name(name)
            await self.play_wav_or_tts(
                None, f"Я правильно поняла, Вас зовут {name}?"
            )
            self.state_machine.transition_to(ConversationState.ASKING_NAME)
            return

        if need:
            # Если явно просит не Ч+ери/Т+энет или другие марки — переводим на отдел продаж
            if need == ClientNeed.NEW_CARS_CHERY_TENET and (
                "не чери" in t_low
                or "не тенет" in t_low
                or "не тэнет" in t_low
                or "другие" in t_low
                or "кроме" in t_low
                or "другая марка" in t_low
            ):
                await self.play_wav_or_tts("05_transfer_chery_tenet.wav", None)
                self.state_machine.transition_to(ConversationState.ENDED)
                return
            self.state_machine.set_identified_need(need)
            self.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
            await self._confirm_need(need)
        else:
            # Если имя не распознано, переспросим один раз
            if not self.state_machine.client_name and self.state_machine.client_name_attempts == 0:
                self.state_machine.increment_name_attempt()
                await self.play_wav_or_tts("02_ask_name.wav", None)
                self.state_machine.transition_to(ConversationState.ASKING_NAME)
                return
            self.state_machine.increment_need_attempt()
            if self.state_machine.should_transfer_to_consultant_need():
                # Вторая попытка не выявила потребность - переводим на консультанта
                await self.play_wav_or_tts("03_transfer_consultant.wav", None)
                self.state_machine.transition_to(ConversationState.ENDED)
            else:
                # Первая попытка - переспрашиваем с перечислением отделов
                await self._ask_about_departments()

    async def _handle_asking_name_state(self, text: str) -> None:
        t = text.lower().strip()
        # Проверяем «да»/«нет» как отдельные слова (чтобы «да» не срабатывало в «свидания»)
        has_positive = any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in POSITIVE_WORDS)
        has_negative = any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in NEGATIVE_WORDS)

        if self.state_machine.client_name and has_positive:
            need = self._identify_need(text)
            if need:
                self.state_machine.set_identified_need(need)
                self.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
                await self._confirm_need(need)
            else:
                await self.play_wav_or_tts("19_pleasant_help.wav", None)
                self.state_machine.state = ConversationState.INITIAL
            return

        if has_negative:
            self.state_machine.client_name = None

        # Имя есть, но ответ не «да» и не «нет»
        if self.state_machine.client_name and not has_positive and not has_negative:
            # 1) Клиент повторно называет то же имя → считаем как «да»
            repeated_name = self.name_extractor.extract_name(text)
            if repeated_name and repeated_name == self.state_machine.client_name:
                need = self._identify_need(text)
                if need:
                    self.state_machine.set_identified_need(need)
                    self.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
                    await self._confirm_need(need)
                else:
                    await self.play_wav_or_tts("19_pleasant_help.wav", None)
                    self.state_machine.state = ConversationState.INITIAL
                return
            # 2) В ответе уже есть явная потребность (купить/продать/сервис и т.п.) → переходим к её разбору
            need = self._identify_need(text)
            if need:
                self.state_machine.set_identified_need(need)
                self.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
                await self._confirm_need(need)
                return
            # 3) Иначе действительно просим сказать «да» или «нет»
            await self.play_wav_or_tts("18_ask_yes_no.wav", None)
            return

        name = self.name_extractor.extract_name(text)
        if name:
            self.state_machine.set_name(name)
            await self.play_wav_or_tts(
                None, f"Я правильно поняла, Вас зовут {name}?"
            )
        else:
            # Увеличиваем счётчик только если действительно не удалось извлечь имя
            self.state_machine.increment_name_attempt()
            if self.state_machine.client_name_attempts >= 2:
                # Продолжаем без имени
                await self.play_wav_or_tts(
                    None, "Спасибо. Чем я могу Вам помочь?"
                )
                self.state_machine.state = ConversationState.INITIAL
            else:
                await self.play_wav_or_tts("02_ask_name.wav", None)

    async def _handle_need_identified_state(self, text: str) -> None:
        t = text.lower()
        # Вопросы про другие марки / машины, не Чери/Тенет
        other_brand_keywords = [
            "джили", "geely", "мерседес", "mercedes", "бмв", "bmw", "ауди", "audi",
            "тойота", "toyota", "ниссан", "nissan", "лада", "ваз", "hyundai", "хьендай", "хендай",
            "киа", "kia", "мазда", "mazda", "митсубиси", "mitsubishi",
        ]
        if (
            ("какие" in t or "другие" in t or "еще" in t)
            and ("марк" in t or "автомоб" in t or "машин" in t or "бренд" in t)
            and ("прода" in t or "есть" in t or "предлаг" in t or "новые" in t)
        ) or (
            ("другие" in t or "еще" in t) and ("автомоб" in t or "машин" in t or "марк" in t)
        ) or ("не чери" in t or "не тенет" in t or "не тэнет" in t) or any(
            b in t for b in other_brand_keywords
        ):
            # Вопрос «какие ещё автомобили / каких марок продаёте»
            # Действуем в контексте уже выявленной потребности:
            # - если речь о новых авто Chery/Tenet → переводим в отдел продаж новых авто;
            # - если речь об авто с пробегом → переводим в отдел продаж автомобилей с пробегом;
            # - иначе — общий отдел продаж.
            need = self.state_machine.identified_need
            if need == ClientNeed.NEW_CARS_CHERY_TENET:
                await self.play_wav_or_tts(
                    None,
                    "Я переведу Вас в отдел продаж Чери и Тэнет, там Вас проконсультируют.",
                )
            elif need == ClientNeed.USED_CARS:
                await self.play_wav_or_tts(
                    None,
                    "Я переведу звонок в отдел продаж автомобилей с пробегом, там Вас проконсультируют.",
                )
            else:
                await self.play_wav_or_tts("05_transfer_chery_tenet.wav", None)
            self.state_machine.transition_to(ConversationState.ENDED)
            return
        if any(w in t for w in NEGATIVE_WORDS):
            # Если в отрицании явно есть другая потребность — переключаемся на неё
            alt_need = self._identify_need(text)
            if alt_need and alt_need != self.state_machine.identified_need:
                self.state_machine.set_identified_need(alt_need)
                self.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
                await self._confirm_need(alt_need)
                return
            self.state_machine.increment_need_attempt()
            if self.state_machine.should_transfer_to_consultant_need():
                await self.play_wav_or_tts("03_transfer_consultant.wav", None)
                self.state_machine.transition_to(ConversationState.ENDED)
                return
            # Сбрасываем потребность и возвращаемся в INITIAL для переспроса
            self.state_machine.reject_need()
            self.state_machine.state = ConversationState.INITIAL
            await self._ask_about_departments()
        elif any(w in t for w in POSITIVE_WORDS):
            self.state_machine.confirm_need()
            await self._handle_confirmed_need()
        else:
            # Если клиент назвал другой отдел/марку — переключаем потребность
            alt_need = self._identify_need(text)
            if alt_need and alt_need != self.state_machine.identified_need:
                self.state_machine.set_identified_need(alt_need)
                self.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
                await self._confirm_need(alt_need)
                return
            # Клиент не ответил Да/Нет - увеличиваем счетчик неясных ответов
            if not hasattr(self.state_machine, 'need_unclear_attempts'):
                self.state_machine.need_unclear_attempts = 0
            self.state_machine.need_unclear_attempts = getattr(self.state_machine, 'need_unclear_attempts', 0) + 1
            
            if self.state_machine.need_unclear_attempts >= 2:
                # После 2 неясных ответов переводим на консультанта
                await self.play_wav_or_tts("03_transfer_consultant.wav", None)
                self.state_machine.transition_to(ConversationState.ENDED)
            else:
                await self.play_wav_or_tts("18_ask_yes_no.wav", None)

    async def _handle_confirmed_need(self) -> None:
        need = self.state_machine.identified_need
        if not need:
            return

        if need == ClientNeed.SERVICE:
            await self.play_wav_or_tts("16_transfer_service_assistant.wav", None)
            self.state_machine.transition_to(ConversationState.ENDED)
        elif need == ClientNeed.USED_CARS:
            await self.play_wav_or_tts("04_transfer_used_cars.wav", None)
            self.state_machine.transition_to(ConversationState.ENDED)
        elif need == ClientNeed.BODY_REPAIR:
            await self.play_wav_or_tts("13_transfer_body_repair.wav", None)
            self.state_machine.transition_to(ConversationState.ENDED)
        elif need == ClientNeed.NEW_CARS_CHERY_TENET:
            await self.play_wav_or_tts("05_transfer_chery_tenet.wav", None)
            self.state_machine.transition_to(ConversationState.ENDED)
        else:
            await self.play_wav_or_tts("03_transfer_consultant.wav", None)
            self.state_machine.transition_to(ConversationState.ENDED)

    async def _handle_service_data_collection_state(self, text: str) -> None:
        import re

        sd_data = self.state_machine.service_data
        t_low = text.lower()

        new_fio_dict = self.name_extractor.extract_fio(text)
        new_phone = self.name_extractor.extract_phone(text)
        new_date = self.date_parser.parse_date(text) if self.date_parser else None
        
        # Улучшенный парсинг пробега: "120 тысяч" -> "120000", "120 тысяч километров" -> "120000"
        mileage_value = None
        mileage_match = re.search(r"(\d+)\s*(?:тысяч|тыс|тыс\.)", t_low)
        if mileage_match:
            thousands = int(mileage_match.group(1))
            mileage_value = str(thousands * 1000)
        else:
            # Обычный формат: "120000" или "120 000"
            mileage_match = re.search(r"(\d+(?:\s+\d{3})*)", text)
            if mileage_match:
                mileage_value = mileage_match.group(1).replace(" ", "")
        
        # Извлечение списка работ: ищем ключевые слова и устойчивые выражения
        works_found = bool(re.search(
            r"(то\d*|то-\d+|технич\w*\s+обслужив\w*|обслужив\w*|техобслужив\w*|ремонт|замен\w*)",
            t_low,
            flags=re.IGNORECASE,
        ))

        # Фильтр шумовых фраз отключен по запросу пользователя

        # Пожелание по времени суток (утром / после обеда / вечером)
        day_part = self._extract_day_part(text)
        if day_part:
            self.service_day_part = day_part

        if new_fio_dict:
            sd_data["fio"] = (
                f"{new_fio_dict['surname']} {new_fio_dict['name']} {new_fio_dict['patronymic']}".strip()
            )
        if new_phone:
            sd_data["phone"] = new_phone
        if new_date:
            sd_data["desired_date"] = new_date.strftime("%Y-%m-%d")
        if mileage_value and len(mileage_value) >= 3:
            sd_data["mileage"] = mileage_value

        # Извлечение желаемого времени (например, "после 15.00", "в 16:30", "в 6 вечера")
        desired_time = None
        time_match = re.search(r"(\d{1,2})\s*[:\.\-]\s*(\d{2})", t_low)
        if time_match:
            h = int(time_match.group(1))
            m = int(time_match.group(2))
            if 0 <= h <= 23 and 0 <= m <= 59:
                desired_time = f"{h:02d}:{m:02d}"
        else:
            time_match = re.search(r"(?:после|в|к)\s*(\d{1,2})\b", t_low)
            if time_match:
                h = int(time_match.group(1))
                if 0 <= h <= 23:
                    # Учет "вечера" / "дня" / "утра"
                    if "вечер" in t_low and h < 12:
                        h += 12
                    if "дня" in t_low and h < 12:
                        h += 12
                    desired_time = f"{h:02d}:00"
        if desired_time:
            sd_data["desired_time"] = desired_time
        
        # Извлечение списка работ: убираем пробег из текста и берем остальное
        if works_found:
            # Убираем пробег из текста для получения списка работ
            work_text = text
            if mileage_match:
                # Удаляем упоминание пробега из текста (включая "километров", "км", "тысяч" и т.д.)
                work_text = re.sub(r"\d+\s*(?:тысяч|тыс|тыс\.)", "", work_text, flags=re.IGNORECASE)
                work_text = re.sub(r"\d+(?:\s+\d{3})*\s*(?:километров|км)", "", work_text, flags=re.IGNORECASE)
                work_text = re.sub(r"\s*пробег\s*", "", work_text, flags=re.IGNORECASE)
                work_text = work_text.strip()
            
            # Очищаем от лишних слов и нормализуем "то" в "ТО"
            work_words = work_text.split()
            cleaned_words = []
            for word in work_words:
                word_lower = word.lower()
                # Пропускаем слова, связанные с пробегом
                if word_lower in ["километров", "км", "тысяч", "тыс", "пробег"]:
                    continue
                # Пропускаем частые предлоги
                if word_lower in ["в", "на", "по", "к", "для", "за", "с", "у"]:
                    continue
                # Нормализуем "то" в "ТО"
                if word_lower == "то":
                    cleaned_words.append("ТО")
                else:
                    cleaned_words.append(word)
            
            work_text = " ".join(cleaned_words).strip()

            # Нормализуем "техническое обслуживание"
            if re.search(r"технич\w*\s+обслужив\w*", work_text, flags=re.IGNORECASE):
                work_text = "техническое обслуживание"
            
            # Если после очистки остался текст, используем его
            time_only = re.search(
                r"(после\s+обеда|вторая\s+половина\s+дня|утром|вечером|днем|после\s+обеду|после\s+обеда|после\s+полу?дня)",
                work_text.lower(),
            )
            # Если в work_text только пожелание по времени — не записываем как работу
            if time_only and len(re.sub(r"\s+", "", work_text)) <= 15:
                work_text = ""
            if work_text and len(work_text) > 1 and not time_only:
                sd_data["work_list"] = work_text
            else:
                # Fallback: если ничего не осталось, используем исходный текст, но нормализуем "то"
                fallback_text = text
                if "то" in t_low and "то" not in fallback_text.lower().split():
                    # Если "то" было в исходном тексте, заменяем на "ТО"
                    fallback_text = re.sub(r"\bто\b", "ТО", fallback_text, flags=re.IGNORECASE)
                if not time_only and not re.search(r"(после\s+обеда|вечером|утром|вторая\s+половина\s+дня)", fallback_text.lower()):
                    sd_data["work_list"] = fallback_text

        # Шаг 1: Сбор ФИО и телефона
        if not sd_data.get("fio") or not sd_data.get("phone"):
            if not sd_data.get("fio"):
                await self.play_wav_or_tts(
                    None,
                    "Назовите, пожалуйста, Ваши фамилию, имя и отчество полностью.",
                )
            else:
                await self.play_wav_or_tts(
                    None, "Назовите Ваш номер телефона для связи."
                )
            return

        # Шаг 2: Обработка автомобиля
        if not sd_data.get("car_confirmed"):
            # Если еще не искали в базе
            if not sd_data.get("client_found_in_db") and sd_data.get("car_attempts", 0) == 0:
                client = self.db_manager.find_client(
                    fio=sd_data["fio"], phone=sd_data["phone"]
                )
                if client:
                    # Нормализуем ФИО/телефон из базы, если они есть
                    def _normalize_phone(raw: str | None) -> str | None:
                        if not raw:
                            return None
                        digits = re.sub(r"\D", "", str(raw))
                        if not digits:
                            return None
                        if len(digits) == 10:
                            return "+7" + digits
                        if len(digits) == 11 and digits.startswith("8"):
                            return "+7" + digits[1:]
                        if len(digits) == 11 and digits.startswith("7"):
                            return "+" + digits
                        return "+" + digits

                    fio_from_db = str(client.get("Контрагент", "")).replace("nan", "").strip()
                    if not fio_from_db:
                        parts = [
                            str(client.get("Фамилия", "")).strip(),
                            str(client.get("Имя", "")).strip(),
                            str(client.get("Отчество", "")).strip(),
                        ]
                        fio_from_db = " ".join([p for p in parts if p and p.lower() != "nan"]).strip()

                    phone_from_db = _normalize_phone(client.get("Телефон"))
                    if fio_from_db:
                        sd_data["fio_from_db"] = fio_from_db
                    if phone_from_db:
                        sd_data["phone_from_db"] = phone_from_db

                    # Приоритет 1: используем колонку "Модель" (может содержать "BMW X7" или просто "X7")
                    model_from_db = str(client.get("Модель", "")).replace("nan", "").strip()
                    
                    if model_from_db:
                        # Пытаемся извлечь марку и модель из колонки "Модель"
                        car_info_extracted = self.car_brand_extractor.extract_car_info(model_from_db)
                        if car_info_extracted:
                            brand, model = car_info_extracted
                            car_info = f"{brand} {model}" if model else brand
                        else:
                            # Если не удалось извлечь, используем как есть
                            brand_from_db = str(client.get("Марка", "")).replace("nan", "").strip()
                            if brand_from_db:
                                car_info = f"{brand_from_db} {model_from_db}"
                                brand = self.car_brand_extractor.normalize_brand(brand_from_db)
                                model = self.car_brand_extractor.normalize_model(model_from_db)
                            else:
                                car_info = model_from_db
                                brand = None
                                model = None
                    else:
                        # Приоритет 2: используем колонки "Марка" и "Модель" отдельно
                        brand_from_db = str(client.get("Марка", "")).replace("nan", "").strip()
                        
                        if brand_from_db:
                            car_info = brand_from_db
                            brand = self.car_brand_extractor.normalize_brand(brand_from_db)
                            model = None
                        else:
                            # Fallback: парсим колонку "Автомобиль"
                            car_raw = str(client.get("Автомобиль", "")).replace("nan", "")
                            words = car_raw.split()

                            # Убираем VIN/№ и госномер вида А123ВС45
                            plate_pattern = re.compile(r"^[А-ЯA-Z]\d{3}[А-ЯA-Z]{2}\d{2,3}$")
                            unique_words: list[str] = []
                            for w in words:
                                lower = w.lower()
                                if lower in ("vin", "№"):
                                    break
                                if plate_pattern.match(w):
                                    continue
                                if lower not in [u.lower() for u in unique_words]:
                                    unique_words.append(w)

                            car_info = " ".join(unique_words)
                            tokens = car_info.split()
                            if tokens:
                                brand = self.car_brand_extractor.normalize_brand(tokens[0])
                                model = None
                                if len(tokens) > 1:
                                    model = self.car_brand_extractor.normalize_model(tokens[1])
                            else:
                                brand = None
                                model = None
                    
                    # Форматируем для TTS
                    if brand:
                        car_info_tts = self.car_brand_extractor.format_for_tts(brand, model)
                    else:
                        # Fallback для TTS
                        brand_map = {
                            "peugeot": "Пеж+о",
                            "nissan": "Нисс+ан",
                            "chery": "Ч+ери",
                        }
                        tokens_tts = car_info.split()
                        if tokens_tts:
                            first = tokens_tts[0]
                            mapped = brand_map.get(first.lower())
                            if mapped:
                                tokens_tts[0] = mapped
                            car_info_tts = " ".join(tokens_tts)
                        else:
                            car_info_tts = car_info
                    
                    # Определяем brand для сохранения
                    car_brand_value = brand if brand else (brand_from_db if brand_from_db else (car_info.split()[0] if car_info else None))

                    sd_data.update({
                        "client_found_in_db": True,
                        "car_model": car_info,
                        "car_brand": car_brand_value,
                    })
                    await self.play_wav_or_tts(
                        None,
                        f"{self.state_machine.client_name}, у Вас автомобиль {car_info_tts}?",
                    )
                    return
                else:
                    # Клиент не найден в базе - спрашиваем марку и модель
                    sd_data["client_found_in_db"] = False
                    sd_data["car_attempts"] = sd_data.get("car_attempts", 0) + 1
                    if sd_data["car_attempts"] >= 2:
                        sd_data["car_confirmed"] = True
                        await self.play_wav_or_tts(
                            None,
                            "Назовите, пожалуйста, пробег и желаемый список работ.",
                        )
                        return
                    await self.play_wav_or_tts(
                        None,
                        "Назовите, пожалуйста, марку и модель автомобиля.",
                    )
                    return
            
            # Обработка ответа на вопрос об автомобиле
            if any(w in t_low for w in POSITIVE_WORDS):
                sd_data["car_confirmed"] = True
                # Переходим к следующему вопросу
                await self.play_wav_or_tts(
                    None,
                    "Назовите, пожалуйста, пробег и желаемый список работ.",
                )
                return
            elif any(w in t_low for w in NEGATIVE_WORDS):
                # Клиент отрицает - пытаемся извлечь информацию об автомобиле из его ответа
                car_info = self.car_brand_extractor.extract_car_info(text)
                if car_info:
                    brand, model = car_info
                    car_info_tts = self.car_brand_extractor.format_for_tts(brand, model)
                    sd_data.update({
                        "car_brand": brand,
                        "car_model": f"{brand} {model}" if model else brand,
                        "car_attempts": sd_data.get("car_attempts", 0) + 1,
                    })
                    await self.play_wav_or_tts(
                        None,
                        f"У Вас {car_info_tts}, верно?",
                    )
                    return
                else:
                    # Не удалось извлечь - еще одна попытка или переходим дальше
                    sd_data["car_attempts"] = sd_data.get("car_attempts", 0) + 1
                    if sd_data["car_attempts"] < 2:
                        await self.play_wav_or_tts(
                            None,
                            "Назовите, пожалуйста, марку и модель автомобиля.",
                        )
                        return
                    else:
                        # После 2 попыток переходим дальше в любом случае
                        sd_data["car_confirmed"] = True
                        await self.play_wav_or_tts(
                            None,
                            "Назовите, пожалуйста, пробег и желаемый список работ.",
                        )
                        return
            else:
                # Если клиент назвал марку и модель напрямую (не отвечая на вопрос)
                # Убираем служебные слова типа "это", "у меня", "автомобиль" перед извлечением
                cleaned_text = text
                for word in ["это", "у меня", "автомобиль", "машина", "авто"]:
                    cleaned_text = re.sub(rf"\b{word}\b", "", cleaned_text, flags=re.IGNORECASE).strip()
                
                if not sd_data.get("car_model"):
                    car_info = self.car_brand_extractor.extract_car_info(cleaned_text)
                    if car_info:
                        brand, model = car_info
                        car_info_tts = self.car_brand_extractor.format_for_tts(brand, model)
                        sd_data.update({
                            "car_brand": brand,
                            "car_model": f"{brand} {model}" if model else brand,
                            "car_attempts": sd_data.get("car_attempts", 0) + 1,
                        })
                        await self.play_wav_or_tts(
                            None,
                            f"У Вас {car_info_tts}, верно?",
                        )
                        return
                    else:
                        # Не удалось извлечь марку/модель - увеличиваем счетчик
                        sd_data["car_attempts"] = sd_data.get("car_attempts", 0) + 1
                        if sd_data["car_attempts"] >= 2:
                            # После 2 попыток переходим дальше в любом случае
                            sd_data["car_confirmed"] = True
                            await self.play_wav_or_tts(
                                None,
                                "Назовите, пожалуйста, пробег и желаемый список работ.",
                            )
                            return
                        else:
                            await self.play_wav_or_tts(
                                None,
                                "Назовите, пожалуйста, марку и модель автомобиля.",
                            )
                            return

        # Шаг 3: Обработка пробега и списка работ
        if sd_data.get("car_confirmed") and not sd_data.get("mileage_work_confirmed"):
            # Сначала проверяем ответ клиента на подтверждение
            if any(w in t_low for w in POSITIVE_WORDS) and sd_data.get("mileage") and sd_data.get("work_list"):
                sd_data["mileage_work_confirmed"] = True
                await self.play_wav_or_tts(
                    None,
                    "Назовите, пожалуйста, желаемый день и время начала работ.",
                )
                return
            elif any(w in t_low for w in NEGATIVE_WORDS) and sd_data.get("mileage_work_attempts", 0) > 0:
                sd_data["mileage_work_attempts"] = sd_data.get("mileage_work_attempts", 0) + 1
                if sd_data["mileage_work_attempts"] < 2:
                    # Просим только недостающую информацию
                    if not sd_data.get("mileage"):
                        await self.play_wav_or_tts(None, "Назовите, пожалуйста, пробег.")
                    elif not sd_data.get("work_list"):
                        await self.play_wav_or_tts(None, "Назовите, пожалуйста, список работ.")
                    else:
                        await self.play_wav_or_tts(None, "Назовите, пожалуйста, пробег и желаемый список работ.")
                    return
                else:
                    # После 1 попытки переходим дальше в любом случае
                    sd_data["mileage_work_confirmed"] = True
                    await self.play_wav_or_tts(
                        None,
                        "Назовите, пожалуйста, желаемый день и время начала работ.",
                    )
                    return
            elif sd_data.get("mileage") and sd_data.get("work_list"):
                # Если есть данные, но еще не подтвердили - спрашиваем подтверждение
                # Проверяем, не задавали ли уже этот вопрос
                if sd_data.get("mileage_work_attempts", 0) == 0:
                    # Первый раз спрашиваем подтверждение
                    mileage_num = int(sd_data["mileage"])
                    mileage_words = number_to_words_ru(mileage_num // 1000) if mileage_num >= 1000 else number_to_words_ru(mileage_num)
                    mileage_text = f"{mileage_words} тысяч километров" if mileage_num >= 1000 else f"{mileage_words} километров"
                    sd_data["mileage_work_attempts"] = 1
                    await self.play_wav_or_tts(
                        None,
                        f"Пробег {mileage_text}, список работ: {sd_data['work_list']}. Всё верно?",
                    )
                    return
                else:
                    # Уже спрашивали подтверждение, но клиент не ответил Да/Нет
                    # После 1 попытки переходим дальше в любом случае
                    sd_data["mileage_work_confirmed"] = True
                    await self.play_wav_or_tts(
                        None,
                        "Назовите, пожалуйста, желаемый день и время начала работ.",
                    )
                    return
            else:
                # Если еще не собрали данные - просим только недостающую информацию, и только 1 раз
                mileage_attempts = sd_data.get("mileage_work_attempts", 0)
                if mileage_attempts == 0:
                    # Первая попытка - просим недостающую информацию
                    if not sd_data.get("mileage") and not sd_data.get("work_list"):
                        await self.play_wav_or_tts(None, "Назовите, пожалуйста, пробег и желаемый список работ.")
                    elif not sd_data.get("mileage"):
                        await self.play_wav_or_tts(None, "Назовите, пожалуйста, пробег.")
                    elif not sd_data.get("work_list"):
                        await self.play_wav_or_tts(None, "Назовите, пожалуйста, список работ.")
                    sd_data["mileage_work_attempts"] = 1
                    return
                else:
                    # Уже просили 1 раз - переходим дальше в любом случае
                    sd_data["mileage_work_confirmed"] = True
                    await self.play_wav_or_tts(
                        None,
                        "Назовите, пожалуйста, желаемый день и время начала работ.",
                    )
                    return

        # Шаг 4: Обработка даты и времени
        if sd_data.get("mileage_work_confirmed") and not sd_data.get("date_time_confirmed"):
            # Сначала проверяем ответ клиента на подтверждение (Да/Нет)
            if any(w in t_low for w in POSITIVE_WORDS) and sd_data.get("desired_date"):
                sd_data["date_time_confirmed"] = True
                # Переходим к бронированию
                await self._process_service_booking()
                return
            elif any(w in t_low for w in NEGATIVE_WORDS) and sd_data.get("date_time_attempts", 0) > 0:
                sd_data["date_time_confirmed"] = False
                sd_data["desired_date"] = None
                self.service_day_part = None
                sd_data["date_time_attempts"] = sd_data.get("date_time_attempts", 0) + 1
                if sd_data["date_time_attempts"] < 2:
                    await self.play_wav_or_tts(
                        None,
                        "Назовите, пожалуйста, желаемый день и время начала работ.",
                    )
                    return
                else:
                    # После 2 попыток переходим к бронированию
                    sd_data["date_time_confirmed"] = True
                    await self._process_service_booking()
                    return
            
            # Если есть дата, но еще не спрашивали подтверждение - спрашиваем
            if sd_data.get("desired_date") and sd_data.get("date_time_attempts", 0) == 0:
                dt = datetime.strptime(sd_data["desired_date"], "%Y-%m-%d")
                day_txt = day_to_ordinal_ru(dt.day)
                months = [
                    "", "январ+я", "феврал+я", "м+арта", "апр+еля", "м+ая",
                    "и+юня", "июл+я", "августа", "сентябр+я", "октябр+я", "ноябр+я", "декабр+я",
                ]
                sd_data["date_time_attempts"] = 1
                await self.play_wav_or_tts(
                    None,
                    f"Желаемая дата: {day_txt} {months[dt.month]}. Всё верно?",
                )
                return
            elif sd_data.get("desired_date") and sd_data.get("date_time_attempts", 0) > 0:
                # Уже спрашивали подтверждение, но клиент не ответил Да/Нет
                # После 1 попытки переходим к бронированию в любом случае
                sd_data["date_time_confirmed"] = True
                await self._process_service_booking()
                return
            elif any(w in t_low for w in NEGATIVE_WORDS):
                sd_data["date_time_attempts"] = sd_data.get("date_time_attempts", 0) + 1
                if sd_data["date_time_attempts"] < 2:
                    await self.play_wav_or_tts(
                        None,
                        "Назовите, пожалуйста, желаемый день и время начала работ.",
                    )
                    return
                else:
                    # После 2 попыток переходим к бронированию
                    sd_data["date_time_confirmed"] = True
                    await self._process_service_booking()
                    return
            else:
                # Если еще не собрали дату/время
                if not sd_data.get("desired_date"):
                    sd_data["date_time_attempts"] = sd_data.get("date_time_attempts", 0) + 1
                    if sd_data["date_time_attempts"] >= 2:
                        await self.play_wav_or_tts(
                            None, "Перевожу на специалиста для уточнения времени."
                        )
                        self.state_machine.transition_to(ConversationState.ENDED)
                        return
                    await self.play_wav_or_tts(
                        None,
                        "Назовите, пожалуйста, желаемый день и время начала работ.",
                    )
                    return

    async def _handle_service_slot_selection_state(self, text: str) -> None:
        t_low = text.lower()
        sd_data = self.state_machine.service_data

        def _format_offer(slot_date: str, slot_time: str) -> str:
            dt = datetime.strptime(slot_date, "%Y-%m-%d")
            day_txt = day_to_ordinal_ru(dt.day)
            months = [
                "", "январ+я", "феврал+я", "м+арта", "апр+еля", "м+ая",
                "и+юня", "июл+я", "августа", "сентябр+я", "октябр+я", "ноябр+я", "декабр+я",
            ]
            time_parts = slot_time.split(":")
            h = int(time_parts[0])
            m = int(time_parts[1]) if len(time_parts) > 1 else 0
            h_words = number_to_words_ru(h)
            m_words = number_to_words_ru(m) if m > 0 else "ноль"
            time_tts = f"{h_words} {m_words}" if m > 0 else f"{h_words} ноль ноль"
            return f"{self.state_machine.client_name}, на {day_txt} {months[dt.month]} в {time_tts} есть свободное время. Вас записать?"

        def _extract_time_from_text(text_value: str) -> str | None:
            if not text_value:
                return None
            tl = text_value.lower()
            # 16-00 / 16:00 / 16 00
            m = re.search(r"\b(\d{1,2})\s*[:\-\.\s]\s*(\d{2})\b", tl)
            if m:
                h = int(m.group(1))
                mm = int(m.group(2))
                if 0 <= h <= 23 and 0 <= mm <= 59:
                    return f"{h:02d}:{mm:02d}"
            # 16-ноль / 16 ноль
            m = re.search(r"\b(\d{1,2})\s*[-\s]\s*нол", tl)
            if m:
                h = int(m.group(1))
                if 0 <= h <= 23:
                    return f"{h:02d}:00"
            # "в 16"
            m = re.search(r"(?:\bв\b|\bк\b|\bна\b)\s*(\d{1,2})\b", tl)
            if m:
                h = int(m.group(1))
                if 0 <= h <= 23:
                    return f"{h:02d}:00"
            return None

        # Локальная фильтрация шума именно на этапе слота
        noise_phrases = [
            "подписывайтесь", "канал", "редактор субтитров", "спасибо за просмотр",
            "смотрите видео", "продолжение следует",
        ]
        if any(p in t_low for p in noise_phrases):
            # Повторяем последнее предложение слота
            if sd_data.get("proposed_date") and sd_data.get("proposed_time"):
                await self.play_wav_or_tts(None, _format_offer(sd_data["proposed_date"], sd_data["proposed_time"]))
            else:
                await self.play_wav_or_tts(None, "Повторите, пожалуйста, удобное время.")
            return

        if any(w in t_low for w in POSITIVE_WORDS):
            # Пересчитываем длительность с учётом буфера
            hours = self.db_manager.calculate_work_hours(sd_data.get("work_list", ""))
            hours_with_buffer = hours + 0.5
            
            # Используем предложенное время из _process_service_booking
            slot_date = sd_data.get("proposed_date") or sd_data.get("desired_date")
            slot_time = sd_data.get("proposed_time") or "10:00"
            
            fio_value = sd_data.get("fio_from_db") or sd_data.get("fio")
            phone_from_db = sd_data.get("phone_from_db")
            phone_input = sd_data.get("phone")
            if phone_from_db and phone_input and phone_from_db != phone_input:
                phone_value = f"{phone_from_db}; {phone_input}"
            else:
                phone_value = phone_from_db or phone_input

            success = self.db_manager.book_slot(
                date=slot_date,
                time=slot_time,
                post=sd_data.get("proposed_post", 1),
                hours=hours_with_buffer,
                client_data={
                    "fio": fio_value,
                    "phone": phone_value,
                    "car": sd_data.get("car_model", ""),
                    "work_list": sd_data.get("work_list", ""),
                },
            )
            if success:
                dt = datetime.strptime(slot_date, "%Y-%m-%d")
                day_txt = day_to_ordinal_ru(dt.day)
                months = [
                    "", "январ+я", "феврал+я", "м+арта", "апр+еля", "м+ая",
                    "и+юня", "июл+я", "августа", "сентябр+я", "октябр+я", "ноябр+я", "декабр+я",
                ]
                time_parts = slot_time.split(":")
                h = int(time_parts[0])
                m = int(time_parts[1]) if len(time_parts) > 1 else 0
                h_words = number_to_words_ru(h)
                m_words = number_to_words_ru(m) if m > 0 else "ноль"
                time_tts = f"{h_words} {m_words}" if m > 0 else f"{h_words} ноль ноль"

                await self.play_wav_or_tts(
                    None,
                    f"{self.state_machine.client_name}, вы записаны на {day_txt} {months[dt.month]} на {time_tts}. Спасибо, есть ли у Вас еще вопросы?",
                )
                self.state_machine.transition_to(ConversationState.SERVICE_BOOKED)
            else:
                await self.play_wav_or_tts(
                    None, "Извините, возникла ошибка. Перевожу на специалиста."
                )
            # Сброс счетчика непонимания
            sd_data["slot_unclear_attempts"] = 0
        else:
            # Если клиент назвал конкретное время
            explicit_time = _extract_time_from_text(text)
            if explicit_time:
                day_part = self._extract_day_part(text) or self.service_day_part
                if day_part:
                    self.service_day_part = day_part
                hours = self.db_manager.calculate_work_hours(sd_data.get("work_list", ""))
                hours_with_buffer = hours + 0.5
                slot_date, slot_time, slot_post = self.db_manager.find_available_slot(
                    sd_data.get("desired_date"),
                    hours_with_buffer,
                    day_part,
                    start_time=explicit_time,
                )
                sd_data["proposed_date"] = slot_date
                sd_data["proposed_time"] = slot_time
                sd_data["proposed_post"] = slot_post
                await self.play_wav_or_tts(None, _format_offer(slot_date, slot_time))
                return

            # Если клиент уточнил время суток (после обеда / утром / вечером)
            day_part = self._extract_day_part(text)
            if day_part:
                self.service_day_part = day_part
                hours = self.db_manager.calculate_work_hours(sd_data.get("work_list", ""))
                hours_with_buffer = hours + 0.5
                slot_date, slot_time, slot_post = self.db_manager.find_available_slot(
                    sd_data.get("desired_date"), hours_with_buffer, day_part
                )
                sd_data["proposed_date"] = slot_date
                sd_data["proposed_time"] = slot_time
                sd_data["proposed_post"] = slot_post

                await self.play_wav_or_tts(None, _format_offer(slot_date, slot_time))
                return

            # Если клиент попросил "позже" — предлагаем +2..4 часа к предыдущему времени
            if any(w in t_low for w in ["позже", "попозже", "позднее"]):
                hours = self.db_manager.calculate_work_hours(sd_data.get("work_list", ""))
                hours_with_buffer = hours + 0.5
                base_time = sd_data.get("proposed_time") or "10:00"
                attempt = sd_data.get("later_attempts", 0)
                offset_hours = 2 if attempt == 0 else 4
                sd_data["later_attempts"] = attempt + 1

                try:
                    h0, m0 = map(int, base_time.split(":")[:2])
                    new_h = min(19, h0 + offset_hours)
                    start_time = f"{new_h:02d}:{m0:02d}"
                except Exception:
                    start_time = base_time

                slot_date, slot_time, slot_post = self.db_manager.find_available_slot(
                    sd_data.get("desired_date"),
                    hours_with_buffer,
                    self.service_day_part,
                    start_time=start_time,
                )
                sd_data["proposed_date"] = slot_date
                sd_data["proposed_time"] = slot_time
                sd_data["proposed_post"] = slot_post

                await self.play_wav_or_tts(None, _format_offer(slot_date, slot_time))
                return

            # Непонятный ответ по времени — считаем попытку
            sd_data["slot_unclear_attempts"] = sd_data.get("slot_unclear_attempts", 0) + 1
            if sd_data["slot_unclear_attempts"] >= 2:
                await self.play_wav_or_tts(
                    None, "Перевожу на специалиста для уточнения времени."
                )
                self.state_machine.transition_to(ConversationState.ENDED)
                return
            await self.play_wav_or_tts(
                None, "Давайте выберем другое время. Утром, после обеда или вечером?"
            )

    async def _handle_service_booked_state(self, text: str) -> None:
        """Обработка состояния после записи на сервис - вопросы клиента или прощание."""
        t_low = text.lower()

        # Если клиент озвучил новую потребность, обрабатываем её
        new_need = self._identify_need(text)
        if new_need:
            self.state_machine.set_identified_need(new_need)
            self.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
            await self._confirm_need(new_need)
            return

        # Проверяем, есть ли вопросы
        if any(w in t_low for w in ["да", "есть", "вопрос", "хочу", "нужно", "интересует"]):
            # Обрабатываем вопрос через RAG или LLM
            if self.rag_system:
                res = self.rag_system.find_best_response(text)
                if res:
                    await self.play_wav_or_tts(Path(res[0]).name, None)
                    return
            
            # Если не нашли ответ в RAG, используем общий ответ
            await self.play_wav_or_tts(
                None,
                "Я перевожу Вас на специалиста для ответа на Ваш вопрос.",
            )
            self.state_machine.transition_to(ConversationState.ENDED)
        else:
            # Нет вопросов - прощаемся
            await self.play_wav_or_tts("09_goodbye.wav", None)
            self.state_machine.transition_to(ConversationState.ENDED)

    async def _handle_transferring_state(self, text: str) -> None:
        # В состоянии transferring бот уже сказал о переводе и должен завершить разговор
        # Это состояние используется только как промежуточное перед ENDED
        # Если мы здесь, значит что-то пошло не так - завершаем разговор
        self.state_machine.transition_to(ConversationState.ENDED)

    async def _handle_default_state(self, text: str) -> None:
        if self.rag_system:
            res = self.rag_system.find_best_response(text)
            if res:
                await self.play_wav_or_tts(Path(res[0]).name, None)
                return

        if USE_LLM:
            # TODO: сюда интегрировать локальную LLM (через llama-cpp-python), если включен USE_LLM
            await self.say("Пока что я не подключена к LLM, но это место для её ответа.")
        else:
            await self.play_wav_or_tts(
                None, "Минутку, я Вас соединяю со специалистом."
            )

    def _is_noise_text(self, text_value: str) -> bool:
        """Фильтрует шумовые фразы из STT."""
        if not text_value or not text_value.strip():
            return True
        t = text_value.lower().strip()
        noise_phrases = [
            "редактор субтитров",
            "спасибо за просмотр",
            "смотрите видео",
            "до скорых встреч",
            "подписывайтесь",
            "лайк",
            "колокольчик",
            "канал",
            "оформление канала",
            "субтитры",
            "бойкова",
            "музыка",
            "титры",
        ]
        if any(p in t for p in noise_phrases):
            return True

        # Если слишком коротко и без цифр/ключевых слов
        if len(t) <= 2 and not re.search(r"\d", t):
            return True

        # Минимальный сигнал: хотя бы одно слово из набора
        signal_words = [
            "да", "нет", "хочу", "нужно", "можно", "сервис", "ремонт", "купить",
            "новый", "пробег", "запись", "записать", "после", "утром", "вечером",
            "автомобиль", "авто", "машина",
        ]
        if not any(w in t for w in signal_words) and not re.search(r"\d", t):
            return True

        return False

    def _identify_need(self, text: str) -> ClientNeed | None:
        import re

        t = text.lower()
        # Упрощенная схема интентов: продажи, кузовной, слесарный, прочее
        scores = {
            ClientNeed.BODY_REPAIR: 0,
            ClientNeed.SERVICE: 0,
            ClientNeed.USED_CARS: 0,
            ClientNeed.NEW_CARS_CHERY_TENET: 0,
        }

        body_repair_keywords = [
            "покраска", "кузов", "страховая", "отрихтовать", "рихтовка",
            "вытянуть кузов", "покрасить", "разбит", "окрасить",
        ]
        service_verbs = [
            "поменять", "замена", "заменить", "установить", "поставить",
            "сделать", "произвести", "отрегулировать", "продиагностировать", "настроить",
            "на замену", "шиномонтаж", "диагностика", "найти неисправность",
        ]
        service_keywords = [
            "сервис", "ремонт", "обслуживание", "техобслуживание", "то",
            "мойка",
            "мойку",
            "мойке",
            "мойки",
            "автомойка",
            "автомойку",
            "автомойке",
            "автомойки",
            "приемка",
            "приемку",
            "приемке",
            "приемки",
            "приёмка",
            "приёмку",
            "приёмке",
            "приёмки",
        ]

        has_service_verb = any(v in t for v in service_verbs)
        has_service_keyword = any(w in t for w in service_keywords) or re.search(r"\bто\b", t)
        has_buy = any(w in t for w in ["купить", "покупка", "хочу", "нужен", "нужно", "интересует", "приобрести", "с гарантией"])
        has_sell = any(w in t for w in ["продать", "продажа", "выкуп", "сдать", "оценить", "выкупить"])
        has_new = any(w in t for w in ["новый", "новую", "новое", "новые"])
        used_markers = ["с пробегом", "б/у", "бэу", "бэуш", "подержан", "бу", "подержаные", "подержанные", "не новые", "неновые"]
        has_used = any(w in t for w in used_markers)
        has_car = bool(re.search(r"автомобил|авто|машин", t))
        has_chery_tenet = any(m in t for m in ["чери", "тене", "тенет"])
        
        # Прямые запросы на выкуп/покупку автомобиля у клиента - всегда отдел "автомобили с пробегом"
        buy_from_client_phrases = [
            "купите у меня",
            "купить у меня",
            "можете купить",
            "купите мой",
            "купить мой",
            "выкупаете",
            "выкупите",
        ]
        if any(phrase in t for phrase in buy_from_client_phrases) and has_car:
            return ClientNeed.USED_CARS

        # Более общий случай: клиент просит купить / выкупить ЕГО машину
        if has_car and (
            "купите" in t or "купить" in t or "выкупите" in t or "выкупить" in t
        ) and any(p in t for p in ["у меня", "мой", "моя", "мою", "мои"]):
            return ClientNeed.USED_CARS

        # Выкуп/продажа автомобиля - всегда отдел "автомобили с пробегом"
        if has_sell and has_car:
            return ClientNeed.USED_CARS

        # Кузовной ремонт
        if any(w in t for w in body_repair_keywords):
            scores[ClientNeed.BODY_REPAIR] += 5

        # Сервис
        if has_service_keyword:
            scores[ClientNeed.SERVICE] += 3
        if has_service_verb:
            scores[ClientNeed.SERVICE] += 2

        # Новые/б/у автомобили
        if has_buy and has_car:
            if has_new:
                scores[ClientNeed.NEW_CARS_CHERY_TENET] += 6
                # Подавляем сервис при явной покупке нового авто
                scores[ClientNeed.SERVICE] -= 2
            if has_used:
                scores[ClientNeed.USED_CARS] += 4
            if not has_new and not has_used:
                scores[ClientNeed.USED_CARS] += 2

        # Отдел продаж (общий запрос "соедините с отделом продаж")
        if "отдел продаж" in t or "дел продаж" in t:
            if has_used:
                scores[ClientNeed.USED_CARS] += 4
            # Если явно сказано "новый" - усиливаем новые авто;
            # если нет ни "новый", ни "с пробегом" - по умолчанию считаем, что речь про отдел новых авто.
            if has_new or (not has_new and not has_used):
                scores[ClientNeed.NEW_CARS_CHERY_TENET] += 3

        if has_chery_tenet:
            scores[ClientNeed.NEW_CARS_CHERY_TENET] += 3

        # Выбор по максимальному баллу
        best_need = max(scores, key=scores.get)
        if scores[best_need] < 3:
            return None

        return best_need

    async def _ask_about_departments(self) -> None:
        """Переспрашивает клиента с перечислением всех отделов."""
        name = self.state_machine.client_name or ""
        greeting = f"{name}, " if name else ""
        text = (
            f"{greeting}я могу соединить Вас с Отделом продаж, "
            "цехом кузовного ремонта или цехом слесарного ремонта. "
            "Что Вас интересует?"
        )
        await self.play_wav_or_tts(None, text)

    async def _confirm_need(self, need: ClientNeed) -> None:
        name = self.state_machine.client_name
        mapping = {
            ClientNeed.SERVICE: "м+астера-консультанта слесарного цеха",
            ClientNeed.USED_CARS: "отдел продажи автомобилей с пробегом",
            ClientNeed.BODY_REPAIR: "м+астера-консультанта кузовного цеха",
            ClientNeed.NEW_CARS_CHERY_TENET: "отдел продажи новых автомобилей Ч+ери и Т+энет",
        }
        n_text = mapping.get(need, "консультация")
        if name:
            await self.play_wav_or_tts(
                None, f"{name}, перевести Вас на {n_text}?"
            )
        else:
            await self.play_wav_or_tts(
                None, f"Перевести Вас на {n_text}?"
            )

    async def _process_service_booking(self) -> None:
        from datetime import datetime

        sd_data = self.state_machine.service_data
        date_str = sd_data.get("desired_date", "2026-01-20")
        work_list = sd_data.get("work_list", "")

        hours = self.db_manager.calculate_work_hours(work_list)
        hours_with_buffer = hours + 0.5  # +30 минут на оформление и форс-мажор
        logger.info("Трудоемкость с запасом: %s ч.", hours_with_buffer)

        # Подбираем слот с учётом длительности и пожеланий по времени суток
        desired_time = sd_data.get("desired_time")
        slot_date, slot_time, slot_post = self.db_manager.find_available_slot(
            date_str, hours_with_buffer, self.service_day_part, start_time=desired_time
        )

        # Сохраняем предложенное время для использования при бронировании
        sd_data["proposed_date"] = slot_date
        sd_data["proposed_time"] = slot_time
        sd_data["proposed_post"] = slot_post

        dt = datetime.strptime(slot_date, "%Y-%m-%d")
        months = [
            "",
            "январ+я",
            "феврал+я",
            "м+арта",
            "апр+еля",
            "м+ая",
            "и+юня",
            "июл+я",
            "августа",
            "сентябр+я",
            "октябр+я",
            "ноябр+я",
            "декабр+я",
        ]
        day_txt = day_to_ordinal_ru(dt.day)

        await self.play_wav_or_tts(
            None,
            f"{self.state_machine.client_name}, на {day_txt} {months[dt.month]} в {slot_time} есть свободное время. Вас записать?",
        )
        self.state_machine.transition_to(ConversationState.SERVICE_SLOT_SELECTION)

    def _extract_day_part(self, text: str) -> str | None:
        """Определяет пожелание по времени суток из фразы клиента."""
        t = text.lower()
        if any(phrase in t for phrase in ["утром", "с утра", "до обеда", "первая половина дня", "в первую половину дня", "в первую половине дня"]):
            return "morning"
        if any(
            phrase in t
            for phrase in [
                "обед",
                "в обед",
                "после обеда",
                "после обеду",
                "днём",
                "днем",
                "в середине дня",
                "в середине дня",
            ]
        ):
            return "afternoon"
        if any(
            phrase in t
            for phrase in ["вечером", "во второй половине дня", "вторая половина дня"]
        ):
            return "evening"
        return None


async def main() -> None:
    bot = DemoBot()
    await bot.run()


if __name__ == "__main__":
    asyncio.run(main())

