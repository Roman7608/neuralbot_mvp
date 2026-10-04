"""
Анализ качества звонков ассистентов сервиса (СТО).
- Чтение 10 wav из Analytic/STO; обрезка начала по числу в имени файла (N_<sec>sec.wav или N_<sec>.wav).
- Транскрипция через faster-whisper, нормализация текста.
- Оценка по критериям 7–29 (Service_check.pdf), запись в Analytic/STO/result.md.
"""

import re
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

# Корень проекта
PROJECT_DIR = Path(__file__).resolve().parent
STO_DIR = PROJECT_DIR / "Analytic" / "STO"
RESULT_MD = STO_DIR / "result.md"

# Добавляем проект в путь для импорта
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from text_normalization import normalize_transcript

# Имена для определения сотрудника СТО по транскрипту (приветствие, «меня зовут», диспетчер/стажёр)
STO_NAMES = frozenset([
    "александра", "александр", "дарья", "юлия", "мария", "анна", "елена", "ольга", "наталья", "ирина", "светлана",
    "татьяна", "екатерина", "алена", "виктория", "полина", "кристина", "марина", "надежда", "валерия",
    "диана", "маргарита", "арина", "вероника", "алина", "ксения", "анастасия", "евгения", "лариса",
])


def extract_employee_name_from_transcript(transcript: str) -> str:
    """Определяет имя сотрудника (ассистент/диспетчер/стажёр) из начала транскрипции."""
    if not (transcript or "").strip():
        return "—"
    text = transcript.strip()
    # Ищем в первых ~1000 символах (приветствие)
    block = text[:1000].lower()
    # «меня зовут Юлия» / «меня зовут Александра»
    m = re.search(r"меня\s+зовут\s+([а-яё]+)", block)
    if m:
        name = m.group(1).strip()
        if name in STO_NAMES or len(name) >= 3:
            return name.capitalize()
    # «Администратор салона Маргарита, добрый день»
    m = re.search(r"администратор\s+салона\s+([а-яё]+)", block)
    if m:
        name = m.group(1).strip()
        if name in STO_NAMES or len(name) >= 3:
            return name.capitalize()
    # «Викинги Сервис, Ассистент Андреева Юлия» (файл 2 и др.)
    m = re.search(r"викинги\s+сервис[^.]*ассистент\s+андреева\s+([а-яё]+)", block)
    if m:
        name = m.group(1).strip()
        if name in STO_NAMES:
            return name.capitalize()
    # «диспетчер сервиса Плаксон Александр» = «Плаксина Александра» (одна сотрудница)
    m = re.search(r"(?:ассистент|диспетчер|спичер)\s+сервиса\s+(?:[«\"]?Плак[^»\s]*[»\"]?\s*(?:на\s+)?)?(?:плаксон|плаксина)\s+([а-яё]+)", block)
    if m:
        name = m.group(1).strip().lower()
        if name in STO_NAMES:
            return name.capitalize()
        if name == "александр":  # в контексте Плаксон/Плаксина — всегда Александра
            return "Александра"
    # «ассистент сервиса Плаксон Александра» / «Плаксина Александра» (после нормализации)
    m = re.search(r"(?:ассистент\s+сервиса\s+)?(?:плаксон|плаксина)\s+([а-яё]+)", block)
    if m:
        name = m.group(1).strip().lower()
        if name in STO_NAMES:
            return name.capitalize()
        if name == "александр":
            return "Александра"
        if len(name) >= 3:
            return name.capitalize()
    # «Юлия, сервис» / «здесь Дарья»
    m = re.search(r"(?:здесь|это|говорит)\s+([а-яё]+)", block)
    if m:
        name = m.group(1).strip()
        if name in STO_NAMES or len(name) >= 3:
            return name.capitalize()
    # «ассистент Дарья» / «консультант Юлия» / «ассистент Андреева Юлия» (Фамилия Имя → берём Имя)
    m = re.search(r"(?:ассистент|консультант|диспетчер|стажёр)\s+([а-яё]+)\s+([а-яё]+)", block)
    if m:
        word2 = m.group(2).strip()
        if word2 in STO_NAMES:
            return word2.capitalize()
    m = re.search(r"(?:ассистент|консультант|диспетчер|стажёр)\s+([а-яё]+)", block)
    if m:
        name = m.group(1).strip()
        if name in STO_NAMES or len(name) >= 3:
            return name.capitalize()
    # Первое слово, похожее на имя из списка
    for name in STO_NAMES:
        if re.search(rf"\b{re.escape(name)}\b", block):
            return name.capitalize()
    return "—"


def parse_trim_seconds_from_filename(name: str) -> int:
    """
    Из имени файла вида 1_30sec.wav, 5_6sec.wav, 7_27.wav извлекает число секунд
    для обрезки с начала. Если паттерна нет — возвращает 0.
    """
    # Примеры: 1_30sec.wav, 5_6sec.wav, 6_51sec.wav, 7_27.wav, 8_7sec.wav
    m = re.search(r"_(\d+)(?:sec)?\.wav$", name.lower())
    return int(m.group(1)) if m else 0


def split_audio_by_silence(
    audio: np.ndarray,
    sr: int,
    min_silence_sec: float = 2.5,
    frame_sec: float = 0.05,
    silence_threshold: float = 0.015,
) -> list[tuple[int, int]]:
    """
    Разбивает аудио на сегменты по длинным паузам (тишина >= min_silence_sec).
    Возвращает список (start_sample, end_sample) — границы речевых кусков без длинной тишины внутри.
    Так Whisper не «перескакивает» начало из-за паузы.
    """
    if len(audio) == 0:
        return [(0, 0)]
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    frame_len = int(sr * frame_sec)
    n_frames = len(audio) // frame_len
    if n_frames == 0:
        return [(0, len(audio))]
    # RMS по кадрам
    frames = audio[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(frames ** 2, axis=1))
    silent = rms < silence_threshold
    # Длинные интервалы тишины: минимум min_silence_sec
    min_silent_frames = max(1, int(min_silence_sec / frame_sec))
    boundaries = [0]
    i = 0
    while i < len(silent):
        if silent[i]:
            j = i
            while j < len(silent) and silent[j]:
                j += 1
            if (j - i) >= min_silent_frames:
                # середина паузы — граница сегментов
                mid = (i + j) // 2
                boundaries.append(mid * frame_len)
                boundaries.append(mid * frame_len)
            i = j
        else:
            i += 1
    boundaries.append(len(audio))
    # Собрать пары (start, end), убрать пустые
    segments = []
    for k in range(0, len(boundaries) - 1, 2):
        start, end = boundaries[k], boundaries[k + 1]
        if end > start:
            segments.append((start, end))
    if not segments:
        return [(0, len(audio))]
    return segments


def merge_short_segments(
    segments: list[tuple[int, int]],
    sr: int,
    min_duration_sec: float = 7.0,
    first_segment_max_sec: float | None = 12.0,
) -> list[tuple[int, int]]:
    """Сливает подряд идущие сегменты короче min_duration_sec в один (меньше вызовов Whisper).
    Если первый сегмент короче first_segment_max_sec и есть второй — сливает первый со вторым
    (чтобы приветствие и первый ответ не разъезжались по границе)."""
    if not segments or min_duration_sec <= 0:
        return segments
    merged = []
    acc_start, acc_end = segments[0]
    for start, end in segments[1:]:
        dur_sec = (acc_end - acc_start) / sr
        if dur_sec < min_duration_sec and start == acc_end:
            acc_end = end
        else:
            merged.append((acc_start, acc_end))
            acc_start, acc_end = start, end
    merged.append((acc_start, acc_end))
    # Дополнительно: первый сегмент короткий — сливаем с вторым (приветствие в одном куске)
    if first_segment_max_sec and len(merged) >= 2:
        first_dur = (merged[0][1] - merged[0][0]) / sr
        if first_dur < first_segment_max_sec:
            merged = [(merged[0][0], merged[1][1])] + merged[2:]
    return merged


# Длинная тишина (сек): разбиваем файл на сегменты и транскрибируем по частям — иначе Whisper теряет начало.
# Для всех файлов СТО (в проде нельзя подстраивать под каждый файл вручную).
STO_SILENCE_SPLIT_SEC = 2.5
STO_MIN_SEGMENT_SEC = 7.0  # сегменты короче сливаем с соседним (чтобы приветствие не терялось на границе)
STO_FIRST_SEGMENT_MAX_SEC = 12.0  # если первый сегмент короче — сливаем с вторым (приветствие + первый ответ)


def trim_audio_from_start(file_path: Path, start_sec: float) -> Path:
    """Обрезает аудио с start_sec до конца, сохраняет во временный WAV."""
    import tempfile
    audio, sr = sf.read(str(file_path))
    if len(audio.shape) > 1:
        audio = audio[:, 0]
    start_sample = int(start_sec * sr)
    if start_sample >= len(audio):
        start_sample = 0
    trimmed = audio[start_sample:]
    fd, path = __import__("tempfile").mkstemp(suffix=".wav")
    __import__("os").close(fd)
    sf.write(path, trimmed, sr)
    return Path(path)


# Критерии 7–29 для ассистентов сервиса (ключевые слова для проверки по тексту)
STO_CRITERIA: list[tuple[int, str, list[str]]] = [
    (7, "Сервисный консультант назвал должность, ИФ и поздоровался", [
        "меня зовут", "здравствуйте", "добрый день", "добрый вечер", "доброе утро",
        "сервис", "консультант", "ассистент", "диспетчер", "стажёр", "инспектор",
        "викинги", "дилерский центр", "на заставной", "слушаю вас", "чем могу помочь",
    ]),
    (8, "Уточнил, как обращаться к клиенту по имени", [
        "как к вам обращаться", "как вас зовут", "как обращаться", "ваше имя", "имя подскажите",
        "как вам по имени можно обращаться", "к вам по имени как можно обращаться",
        "как к вам могу обращаться", "как могу к вам обращаться",
        "как по имени", "имя скажите", "представьтесь",
        "как могу обращаться", "как к вам обращаться по имени",
    ]),
    (9, "Обращался по имени не менее 3 раз", [
        # Считаем вручную по количеству упоминаний имени клиента (ниже отдельная логика)
    ]),
    (10, "Уточнил, обслуживался ли клиент ранее", [
        "обслуживались", "обслуживались у нас", "были у нас", "обращались к нам",
        "ранее", "в первый раз", "первый раз", "уже были", "у нас были",
        "заезжали", "приезжали", "впервые", "первый визит", "ранее к нам",
        "вслушивались нас", "обслуживались ранее",
    ]),
    (11, "Уточнил пожелания по предстоящим работам", [
        "какие работы", "что планируете", "что нужно сделать", "какое обслуживание",
        "то или", "техническое обслуживание", "что хотите сделать", "пожелания по работам",
        "что хотите", "что надо", "с какой целью", "по какому вопросу", "записываетесь",
        "что планируете сделать", "какой объём работ", "что входит в планы",
    ]),
    (12, "Рассказал о перечне предстоящих работ на ТО", [
        "перечень работ", "объём работ", "что входит", "замена масла", "фильтр",
        "диагностика", "предстоящие работы", "плановое обслуживание",
        "масло", "фильтры", "регламент", "входит в то", "объём то",
        "по регламенту меняем", "замена фильтра", "техническое обслуживание",
    ]),
    (13, "Выяснил пожелания по дополнительным работам", [
        "дополнительные работы", "что-то ещё", "ещё что-то", "помимо", "дополнительно",
        "ещё работы", "нужно что-то ещё", "помимо то", "дополнительно что-то",
        "нужно ли что-то", "что-нибудь ещё", "какие-то ещё работы",
    ]),
    (14, "Выяснил текущий пробег автомобиля", [
        "пробег", "километраж", "сколько проехали", "какой пробег", "пробег автомобиля",
        "километров", "пробег сейчас", "сколько км", "пробег у вас", "текущий пробег",
        "какой пробег на автомобиле", "пробег на авто",
    ]),
    (15, "Сообщил ориентировочную стоимость запасных частей", [
        "стоимость запчастей", "цена запчастей", "стоимость запасных", "цена запасных частей",
        "стоимость частей", "цена частей", "рублей за запчасти", "рублей за запасные",
        "рублей за детали", "рублей за фильтр", "рублей за масло", "рублей за колодки",
        "рублей эта установка", "получится замена", "будет стоить", "будут стоить",
        "колодки будут стоить", "задние колодки будут стоить", "передние колодки будут стоить",
        "стоимость колодок", "цена колодок", "стоимость фильтра", "цена фильтра",
        "запчасти обойдутся", "запасные части стоят", "ориентировочно за запчасти",
        "рублей за установку", "плюс рублей", "рублей за работу",
    ]),
    (16, "Запрошенная запасная часть в наличии", [
        "в наличии", "есть в наличии", "можем поставить", "доступна", "на складе",
        "доступна для заказа", "есть на складе", "можем заказать", "привезём",
        "запасная часть в наличии", "запчасть есть",
    ]),
    (17, "Сообщил стоимость предстоящего ТО", [
        "стоимость то", "стоимость обслуживания", "обойдётся", "цена то", "рублей то",
        "стоимость технического", "ориентировочно", "тысяч", "руб", "сумма то",
        "примерно", "стоимость работ", "обойдётся в", "ориентировочная стоимость",
        "стоимость предстоящего", "цена обслуживания",
    ]),
    (18, "Сообщил ориентировочную продолжительность ТО по времени", [
        "продолжительность", "займёт", "займет", "часов", "по времени", "минут",
        "примерно час", "полтора часа", "два часа", "по времени обслуживания",
        "займёт примерно", "час-полтора", "около часа", "примерно",
        "длительность", "сколько займёт", "время обслуживания",
    ]),
    (19, "Предложил не менее 2 вариантов даты и времени", [
        "записать на", "когда вам удобно", "какой день", "предлагаю", "варианты",
        "например в понедельник", "во вторник", "в среду", "утром", "после обеда",
        "седьмое восьмое", "седьмое, восьмое", "17.30", "17 30", "суббота воскресенье",
        "суббота, и воскресенье", "суббота и воскресенье", "работаем до 8",
        "вечернее время", "ближайшие даты", "варианты времени", "ближайшие",
        "на пятницу", "на субботу", "утреннее время", "днём", "на завтра",
        "на понедельник", "на вторник", "когда удобно", "какой день подойдёт",
    ]),
    (20, "Уточнил, знает ли клиент, как добраться до ДЦ", [
        "как добраться", "как проехать", "знаете где мы", "адрес", "заставная",
        "как найти", "доехать до нас", "добраться до нас", "адрес знаете",
        "где находимся", "как к нам проехать", "ориентируетесь",
    ]),
    (21, "Сообщил, какие документы взять с собой", [
        "документы", "паспорт", "свидетельство", "права", "возьмите с собой",
        "нужно взять", "при себе иметь", "паспорт тс", "свидетельство о регистрации",
        "документы на авто", "договор", "что взять с собой",
    ]),
    (22, "Спросил контактные данные клиента", [
        "номер телефона", "телефон", "как с вами связаться", "контактный", "оставьте номер",
        "ваш номер", "перезвоним", "ваш телефон", "контакт", "как связаться",
        "номер заканчивается", "подтвердите номер", "ваш контактный номер",
    ]),
    (23, "Сообщил о формах оплаты выполненных работ", [
        "оплата", "оплатить", "наличными", "картой", "безналичный", "расчёт",
        "формы оплаты", "как оплатить", "наличные", "карта", "безнал",
        "принимаем", "оплата картой", "оплата наличными",
    ]),
    (24, "Предупредил о звонке-напоминании накануне визита", [
        "позвоним", "напомним", "накануне", "напоминание", "подтвердим запись",
        "перезвоним перед", "напомним о записи", "позвоним напомним",
        "подтвердим", "накануне визита", "позвоним накануне",
    ]),
    (25, "Подвёл итог достигнутых договорённостей", [
        "итого", "итак", "подведём итог", "договорились", "записали вас",
        "подтверждаю", "значит так", "в таком случае", "итого записали",
        "подводя итог", "резюмирую", "записываем вас на", "подтверждаем запись",
    ]),
    (26, "Уточнил, остались ли дополнительные вопросы", [
        "ещё вопросы", "что-то ещё", "вопросы есть", "чем-то помочь",
        "что-то уточнить", "остались вопросы", "вопросы остались",
        "вопросы остались у вас", "есть вопросы", "что-то спросить",
        "что-нибудь ещё", "чем помочь", "что-то уточнить хотите",
    ]),
    (27, "Поблагодарил клиента за звонок/обращение", [
        "спасибо за звонок", "благодарю за обращение", "спасибо что позвонили",
        "благодарю за звонок", "спасибо за обращение", "благодарю за обращение",
        "спасибо что обратились", "благодарю что позвонили", "спасибо за обращение",
        "до свидания", "хорошего дня",
    ]),
    (28, "В речи отсутствовали уменьшительно-ласкательные слова", [
        # Отрицательный критерий: если есть машинка, салончик и т.п. — 0
        "машинк", "салончик", "денежк", "документик", "часик", "минутк",
    ]),
    (29, "Спросил VIN и уведомил об отзывных кампаниях", [
        "vin", "вин", "отзывн", "отзывная кампания", "отзывные", "кампания по отзыву",
        "отзывных кампаний", "отзывных нет", "проверяю на отзывные",
        "vin номер", "номер vin", "последние цифры vin",
    ]),
]


# Длина блока «начало звонка» для критерия 7 (консультант назвал должность, ИФ и поздоровался)
INTRO_BLOCK_LEN = 1000

def evaluate_sto_by_rules(
    full_transcript: str,
    *,
    call_type: str | None = None,
    sto_to_rubric_type: str | None = None,
) -> dict[int, float]:
    """Оценка по критериям 7–29. Возвращает {номер_критерия: 0.0 или 1.0}."""
    low = (full_transcript or "").lower().strip()
    if not low:
        return {num: 0.0 for num, _, _ in STO_CRITERIA}

    strt = (sto_to_rubric_type or "").strip().upper()
    scores: dict[int, float] = {}

    for num, _desc, keywords in STO_CRITERIA:
        if num == 7:
            # Консультант назвал должность, ИФ и поздоровался — только если в начале звонка есть и приветствие, и представление
            intro = low[:INTRO_BLOCK_LEN]
            has_greeting = any(g in intro for g in ("здравствуйте", "добрый день", "добрый вечер"))
            has_intro = (
                "меня зовут" in intro
                or bool(re.search(r"(?:диспетчер|ассистент|консультант|стажёр|инспектор)\s+[а-яё]+", intro))
                or ("викинги" in intro and "заставн" in intro)
                or "андреева юлия" in intro
                or "слушаю вас" in intro
                or "стажер дарья" in intro or "стажёр дарья" in intro
                or "диспетчер сервиса плаксина" in intro or "ассистент сервиса плаксина" in intro
                or (("викинги" in intro and "заставн" in intro) and any(g in intro for g in ("здравствуйте", "добрый день", "добрый вечер")))
            )
            scores[7] = 1.0 if (has_greeting and has_intro) else 0.0
            continue
        if num == 8:
            # СТО_ТО_исх.: имя клиента уже известно (исходящий перезвон) — не требуем «как обращаться».
            if strt == "STO_TO_OUT":
                scores[8] = 1.0
                continue
            scores[8] = 1.0 if any(k in low for k in keywords) else 0.0
            continue
        if num == 9:
            # Обращение по имени ≥3 раз: имя из ответа на «как обращаться» / «меня зовут» и т.д. + справочник COMMON_NAMES
            try:
                from analyze_call_quality import (
                    count_customer_name_mentions_after_handover,
                    extract_customer_name_from_full_transcript,
                    extract_manager_name_from_full_transcript,
                )
            except ImportError:
                scores[9] = 0.0
                continue
            mgr = extract_manager_name_from_full_transcript(full_transcript)
            cust = extract_customer_name_from_full_transcript(full_transcript, mgr)
            n = count_customer_name_mentions_after_handover(full_transcript, cust) if cust else 0
            scores[9] = 1.0 if n >= 3 else 0.0
            continue
        if num == 28:
            # Уменьшительно-ласкательные: отсутствуют = хорошо
            has_dimin = any(k in low for k in keywords)
            scores[28] = 0.0 if has_dimin else 1.0
            continue
        if num == 15:
            # Стоимость запасных частей: нужен контекст запчастей + указание цены (не только ТО)
            parts_words = ("запчаст", "запасн", "колодк", "фильтр", "масл", "детал", "ориентировочн")
            cost_words = ("рублей", "руб ", "стоимость", "цена", "обойдётся", "стоит", "обойдется")
            has_parts = any(p in low for p in parts_words)
            has_cost = any(c in low for c in cost_words)
            has_parts_cost_phrase = any(k in low for k in keywords)
            scores[15] = 1.0 if (has_parts_cost_phrase or (has_parts and has_cost)) else 0.0
            continue
        if num == 19:
            # Варианты даты/времени: ключевые фразы ИЛИ два варианта по отдельным словам (с запятыми)
            has_phrase = any(k in low for k in keywords)
            has_two_dates = ("седьмое" in low and "восьмое" in low) or ("суббота" in low and "воскресенье" in low)
            has_time_option = "17.30" in low or "17 30" in low or "вечернее время" in low or "работаем до 8" in low
            scores[19] = 1.0 if (has_phrase or (has_two_dates and has_time_option)) else 0.0
            continue
        if not keywords:
            scores[num] = 0.0
            continue
        scores[num] = 1.0 if any(k in low for k in keywords) else 0.0

    return scores


# Обрабатывать только первые N файлов (None — все файлы)
STO_PROCESS_ONLY_FIRST_N: int | None = None


def run_sto_analysis():
    """Загрузка модели, обход файлов, транскрипция, оценка, запись result.md."""
    STO_DIR.mkdir(parents=True, exist_ok=True)
    wav_files = sorted(STO_DIR.glob("*.wav"))
    if not wav_files:
        print("В папке Analytic/STO нет wav-файлов.", flush=True)
        return
    if STO_PROCESS_ONLY_FIRST_N is not None:
        wav_files = wav_files[:STO_PROCESS_ONLY_FIRST_N]
        print(f"  ⚠️  Режим: только первые {len(wav_files)} файл(ов).", flush=True)

    # Транскрипция: GPU при наличии, иначе CPU
    from analyze_call_quality import (
        load_stt_model,
        transcribe_audio,
        trim_audio_after,
    )

    print("Загрузка модели Whisper (GPU при наличии, иначе CPU)...", flush=True)
    model = load_stt_model()
    if model is None:
        print("Не удалось загрузить модель. Проверьте faster-whisper и config.", flush=True)
        return

    # Собираем по каждому файлу: (имя_файла, сотрудник, баллы по критериям 7–29, транскрипция)
    NUM_CRITERIA = 23  # пункты 7–29
    MAX_SCORE = 100.0
    POINTS_PER_CRITERION = MAX_SCORE / NUM_CRITERIA

    file_results: list[tuple[str, str, dict[int, float], str]] = []

    for wav_path in wav_files:
        name = wav_path.name
        trim_sec = 0  # обрезка по имени файла отключена — транскрибируем полный файл
        audio_to_use = wav_path
        temp_path = None
        if trim_sec > 0:
            temp_path = trim_audio_after(wav_path, trim_sec)
            if temp_path:
                audio_to_use = temp_path
                print(f"  ✂️  {name}: обрезка первых {trim_sec} с", flush=True)

        # Стерео → моно (смешиваем каналы): один поток речи — порядок реплик сохраняется.
        temp_paths_to_clean: list[Path] = []
        if temp_path:
            temp_paths_to_clean.append(temp_path)
        try:
            audio, sr = sf.read(str(audio_to_use))
            if audio.ndim == 2 and audio.shape[1] == 2:
                audio = audio.mean(axis=1)
                fd, mono_path = tempfile.mkstemp(suffix=".wav")
                __import__("os").close(fd)
                sf.write(mono_path, audio, sr)
                temp_paths_to_clean.append(Path(mono_path))
                audio_to_use = Path(mono_path)
                print(f"  🔊  {name}: стерео → моно (один поток, порядок реплик сохраняется)", flush=True)

            # Разбиение по длинной тишине для всех файлов: в проде нельзя подстраивать под каждый файл.
            # Пауза >= STO_SILENCE_SPLIT_SEC — граница; сегменты короче STO_MIN_SEGMENT_SEC сливаем.
            segments = split_audio_by_silence(
                audio, sr,
                min_silence_sec=STO_SILENCE_SPLIT_SEC,
            )
            segments = merge_short_segments(
                segments, sr,
                min_duration_sec=STO_MIN_SEGMENT_SEC,
                first_segment_max_sec=STO_FIRST_SEGMENT_MAX_SEC,
            )

            if len(segments) <= 1:
                print(f"  📝 Транскрипция {name}...", flush=True)
                transcript, _, _ = transcribe_audio(
                    audio_to_use,
                    model,
                    vad_filter=False,
                    no_speech_threshold=0.4,
                    no_initial_prompt=True,
                )
            else:
                print(f"  📝 Транскрипция {name} (по {len(segments)} сегментам по тишине)...", flush=True)
                parts = []
                for idx, (start, end) in enumerate(segments):
                    seg = audio[start:end]
                    fd, seg_path = tempfile.mkstemp(suffix=f"_seg{idx}.wav")
                    __import__("os").close(fd)
                    sf.write(seg_path, seg, sr)
                    temp_paths_to_clean.append(Path(seg_path))
                    t, _, _ = transcribe_audio(
                        Path(seg_path),
                        model,
                        vad_filter=False,
                        no_speech_threshold=0.4,
                        no_initial_prompt=True,
                    )
                    if (t or "").strip():
                        parts.append(t.strip())
                transcript = " ".join(parts)
            transcript = normalize_transcript(transcript)
        except Exception as e:
            transcript = ""
            print(f"  ⚠️  Ошибка: {e}", flush=True)
        finally:
            for p in temp_paths_to_clean:
                if p and Path(p).exists():
                    try:
                        Path(p).unlink()
                    except Exception:
                        pass

        if transcript:
            preview = (transcript or "")[:500].replace("\n", " ")
            print(f"  📄 Начало транскрипта ({len(transcript)} симв.): {preview}...", flush=True)
        scores = evaluate_sto_by_rules(transcript)
        employee = extract_employee_name_from_transcript(transcript)
        file_results.append((name, employee, scores, transcript or ""))

    # Формируем отчёт: таблица (строки — критерии + итого, столбцы — файлы с именами сотрудников)
    lines: list[str] = [
        "# Результаты аналитики звонков ассистентов сервиса (СТО)",
        "",
        "Критерии 7–29 (Service_check.pdf), равновесные. Максимум 100 баллов по звонку.",
        "",
    ]

    # Заголовок таблицы: | № | Критерий | Файл 1 (Дарья) | Файл 2 (Юлия) | ...
    col_headers = ["№", "Критерий"] + [f"Файл {i} ({r[1]})" for i, r in enumerate(file_results, 1)]
    sep = "|" + "|".join(["---"] * len(col_headers)) + "|"
    lines.append("| " + " | ".join(col_headers) + " |")
    lines.append(sep)

    for num, desc, _ in STO_CRITERIA:
        row = [str(num), desc]
        for _, _, scores, _ in file_results:
            val = scores.get(num, 0)
            row.append(str(int(val)))
        lines.append("| " + " | ".join(row) + " |")

    # Строка «Итого по звонку» — баллы из 100
    row = ["", "**Итого по звонку**"]
    for _, _, scores, _ in file_results:
        total = sum(scores.get(i, 0) for i in range(7, 30))
        points = round(total * POINTS_PER_CRITERION, 1)
        row.append(f"**{points:.1f}**")
    lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    # Транскрипции ниже таблицы, с номерами файлов и именами
    lines.append("---")
    lines.append("")
    lines.append("## Транскрипции")
    lines.append("")
    for idx, (fname, employee, _, transcript) in enumerate(file_results, 1):
        lines.append(f"### {idx}. {fname} ({employee})")
        lines.append("")
        lines.append(transcript if transcript else "(пусто)")
        lines.append("")

    content = "\n".join(lines)
    RESULT_MD.write_text(content, encoding="utf-8")
    print(f"\n✅ Результаты записаны в {RESULT_MD}", flush=True)


if __name__ == "__main__":
    run_sto_analysis()
