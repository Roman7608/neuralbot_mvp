"""
Диалоговая логика бота «Викинги» — общий mixin для demo и production.

Содержит:
- process_client_text — диспетчер обработки реплик клиента
- Обработчики каждого состояния (_handle_initial_state, _handle_asking_name_state, …)
- Вспомогательные функции (определение потребности, форматирование чисел, …)

Используется как mixin: конкретный класс (DemoBot / CallSession) реализует
say(), play_wav_or_tts() и инициализирует state_machine, name_extractor и т.д.
"""

import asyncio
from calendar import monthrange
import json
import logging
import re
from datetime import datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional

from dialog.conversation_state import (
    ConversationStateMachine,
    ConversationState,
    ClientNeed,
)
from dialog.department_stt_normalize import (
    is_meaningless_voice_stt,
    is_silent_immediate_admin_callback,
    normalize_department_stt_text,
    stt_implies_insurance_admin_department,
    stt_implies_service_first_to_reply,
    stt_implies_parts_department,
    stt_implies_used_cars_department,
)
from dialog.sto_to_price_inquiry import (
    is_to_booking_price_inquiry,
    is_to_booking_test_trigger_stt,
    is_to_price_inquiry,
    is_to_v2_record_choice,
    is_to_v2_service_assistant_request,
)
from dialog.service_speech_parse import parse_mileage_km_from_speech
from dialog.voice_to_booking_trace import (
    reset_voice_to_booking_call_uuid,
    set_voice_to_booking_call_uuid,
    stt_preview_for_trace,
    summarize_service_data,
    voice_to_booking_trace,
)
from dialog.rag_system_states import WAV_FILE_TEXTS
from dialog.to_booking_v2 import ToBookingV2Mixin
from services.voice.voice_phrases import (
    AFTER_HOURS_V2_ASK_NAME_INTRO,
    AFTER_HOURS_V2_CONTACT_DONE,
    DEPARTMENT_CHOICE_CHERY_TENET,
    DEPARTMENT_CLARIFY_V2_CHERY_TENET,
    HOLIDAY_FORCE_CLOSE_TEXT,
    HOLIDAY_AFTER_HOURS_V2_ASK_NAME_INTRO,
    HOLIDAY_AFTER_HOURS_V2_CONTACT_DONE,
    MONTHS_TTS_GENITIVE,
    SLOTS_SEARCH_FILLER,
    format_service_slot_offer_tts,
    format_slot_confirm_reprompt_tts,
    TRANSFER_SERVICE_ASSISTANT_SLOT,
    TO_V2_BOOKED_CALLBACK_NOTICE,
)

try:
    from config import SERVICE_BOOKING_ENABLED, VOICE_TO_BOOKING_PROD_ENABLED
except ImportError:
    SERVICE_BOOKING_ENABLED = False
    VOICE_TO_BOOKING_PROD_ENABLED = False

try:
    from telegram_bot.services.service_booking_service import (
        find_nearest_slot_1c,
        create_1c_booking,
        get_labor_and_cost,
    )
except Exception:
    find_nearest_slot_1c = None
    create_1c_booking = None
    get_labor_and_cost = None

logger = logging.getLogger(__name__)
_WORKING_HOURS_PATH = Path(__file__).resolve().parent.parent / "working_hours.json"

POSITIVE_WORDS = [
    "да", "верно", "правильно", "ага", "конечно", "точно", "именно",
    "подтверждаю", "согласен", "давайте", "угу", "ок", "окей", "хорошо",
    "нормально",
]
NEGATIVE_WORDS = [
    "нет", "неверно", "ошиблись", "не то", "другой", "неправильно",
    "никак", "отнюдь", "ничуть",
    "no",  # латиница от STT
]


def _stt_has_word(t_low: str, word: str) -> bool:
    """Слово целиком (кириллица: \\b ненадёжен — используем явные границы)."""
    from dialog.service_speech_parse import _cyr_word

    return bool(
        re.search(_cyr_word(re.escape(word)), t_low or "", flags=re.IGNORECASE)
    )


def _normalize_voice_confirm_stt(text: str) -> str:
    """
    STT после «верно?» / «да?»: обрезки «Вно.», «на.» и т.п. → формы для _voice_affirmative_stt.
    «на» только как вся реплика (не «на сервис»).
    """
    raw = (text or "").strip()
    if not raw:
        return raw
    t = re.sub(r"[^\w\s]+", " ", raw.lower(), flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    compact = re.sub(r"\s+", "", t)
    if compact in ("вно", "вноо", "erno", "verno"):
        return "верно"
    if compact in ("на", "na"):
        return "да"
    if compact in ("да", "угу", "ага", "ок", "ok", "yes", "da"):
        return compact
    t = re.sub(r"\bвно\b", "верно", t)
    t = re.sub(r"\bерно\b", "верно", t)
    return t


def _round_slot_start_for_offer(slot_start: datetime) -> datetime:
    """
    Возвращает время слота без дополнительного округления.

    Безопасное округление (только если округлённый слот реально доступен
    в 1С для того же post/acceptor) выполняется на этапе поиска слотов.
    Здесь повторно округлять нельзя, иначе возможен сдвиг и наложение.
    """
    return slot_start.replace(second=0, microsecond=0)


def _normalize_slot_offer_fields(
    slot_date: Optional[str],
    slot_time: Optional[str],
    slot_start_iso: Optional[str],
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Нормализует тройку (date/time/iso) для озвучивания и бронирования:
    если время близко к :00/:30, округляет и синхронно обновляет iso.
    """
    dt: Optional[datetime] = None
    if slot_start_iso:
        try:
            dt = datetime.fromisoformat(str(slot_start_iso))
        except (TypeError, ValueError):
            dt = None
    if dt is None and slot_date and slot_time:
        try:
            dt = datetime.strptime(f"{slot_date} {slot_time}", "%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            dt = None
    if dt is None:
        return slot_date, slot_time, slot_start_iso
    rounded = _round_slot_start_for_offer(dt)
    return (
        rounded.strftime("%Y-%m-%d"),
        rounded.strftime("%H:%M"),
        rounded.isoformat(),
    )


def _voice_affirmative_stt(t_low: str) -> bool:
    """Согласие: словарь + короткие латинские ответы STT (da/ok/yes)."""
    from dialog.department_stt_normalize import is_voice_stt_da_as_a
    from dialog.service_speech_parse import _cyr_word

    raw = (t_low or "").strip()
    if is_voice_stt_da_as_a(raw):
        return True
    t_low = _normalize_voice_confirm_stt(raw).lower()
    if re.search(_cyr_word(r"верн\w*"), t_low):
        return True
    if re.search(_cyr_word(r"правильн\w*"), t_low):
        return True
    # STT-варианты подтверждения «пойдет/пойдёт» (в т.ч. «пойзет»).
    if re.search(_cyr_word(r"не\s+по[йи]?[дз]?[её]т"), t_low):
        return False
    if re.search(_cyr_word(r"по[йи]?[дз]?[её]т"), t_low):
        return True
    if any(_stt_has_word(t_low, w) for w in POSITIVE_WORDS):
        return True
    compact = re.sub(r"[^\w\u0400-\u04FFa-zA-Z]+", "", t_low, flags=re.UNICODE).lower()
    return compact in ("da", "ok", "yes", "yeah", "yep")


_SLOT_BOOKING_CONFIRM_MARKERS: tuple[str, ...] = (
    "запиши",
    "запишите",
    "записывай",
    "записывайте",
    "записать",
    "записываюсь",
    "записываемся",
    "оформ",
    "подтверждаю",
    "согласен",
    "согласна",
    "сиши",  # STT «да» / «запиши»
)


def _voice_slot_booking_confirm_stt(t_low: str) -> bool:
    """Подтверждение предложенного слота («запишите», «да» и т.п.)."""
    if _voice_affirmative_stt(t_low):
        return True
    t_low = (t_low or "").lower()
    if re.search(r"\bсиши\b", t_low):
        return True
    if re.search(r"\bзапят\w*\b", t_low):
        return True
    # STT обрезает «за» в «записывай»: «списывай», «писывай», «пписывай».
    if re.search(r"\b(?:з|с|п)?писывай\w*\b", t_low):
        return True
    # Домашняя линия / плохой STT: «запиши» → «запиш», «спиши»; «записывай» → «ши».
    if re.search(r"\bзапиш\w*\b", t_low):
        return True
    if re.search(r"\bспиш\w*\b", t_low):
        return True
    compact = re.sub(r"[^\w\u0400-\u04FFa-zA-Z]+", "", t_low, flags=re.UNICODE).lower()
    if compact in ("ши", "спиши", "спиш", "запиш", "запиши"):
        return True
    if re.search(r"\bдавай\s+уже\b", t_low):
        return True
    if re.fullmatch(r"\s*давай\s*[\.,!]?\s*", t_low):
        return True
    if compact in ("нуавайте", "авайте"):
        return True
    return any(m in t_low for m in _SLOT_BOOKING_CONFIRM_MARKERS)


def _voice_slot_reject_stt(t_low: str) -> bool:
    """Отказ от предложенного слота (любой: «нет», «не подходит», «другое время»)."""
    return _voice_slot_plain_no_stt(t_low) or _voice_slot_not_suitable_stt(t_low)


def _voice_slot_plain_no_stt(t_low: str) -> bool:
    """Голое «нет» без уточнения даты/времени — просим назвать дату."""
    t_low = (t_low or "").lower()
    if not any(_stt_has_word(t_low, w) for w in NEGATIVE_WORDS):
        return False
    if _voice_slot_not_suitable_stt(t_low):
        return False
    from dialog.service_slot_day_part import _WEEKDAY_RE

    if _WEEKDAY_RE.search(t_low):
        return False
    return True


def _voice_slot_not_suitable_stt(t_low: str) -> bool:
    """«Не подходит», «другое время» — ищем другой слот в тот же день."""
    t_low = (t_low or "").lower()
    markers = (
        "другое время",
        "другой слот",
        "не подходит",
        "неудобно",
        "не хочу",
        "не надо",
        "не устраивает",
        "что еще",
        "что ещё",
    )
    if any(m in t_low for m in markers):
        return True
    if any(_stt_has_word(t_low, w) for w in ("другое", "другой", "другую")):
        from dialog.service_slot_day_part import _WEEKDAY_RE

        if not _WEEKDAY_RE.search(t_low):
            return True
    return False


def _normalize_booking_phone(raw) -> Optional[str]:
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


def _booking_phones_for_1c(
    *,
    phone_from_db: Optional[str] = None,
    phone_input: Optional[str] = None,
    caller_phone: Optional[str] = None,
) -> str:
    """Телефоны для 1С: из базы, названный клиентом и номер, с которого звонят."""
    seen_last10: set[str] = set()
    out: list[str] = []
    for raw in (phone_from_db, phone_input, caller_phone):
        norm = _normalize_booking_phone(raw)
        if not norm:
            continue
        key = re.sub(r"\D", "", norm)[-10:]
        if key in seen_last10:
            continue
        seen_last10.add(key)
        out.append(norm)
    # 1С: два номера через запятую; поле phone — до 50 символов (А. Шилкин, 2026-06-18).
    joined = ",".join(out)
    return joined[:50]


SLOT_NO_SLOTS_ON_DATE_MSG = "На эту дату нет, назовите другую."


def enter_slot_await_new_date(sd_data: dict) -> None:
    """После пустого дня в 1С: не повторять старый слот, ждём новую дату от клиента."""
    sd_data["proposed_date"] = None
    sd_data["proposed_time"] = None
    sd_data["proposed_post"] = None
    sd_data["proposed_acceptor_id"] = None
    sd_data["proposed_mechanic_name"] = None
    sd_data["proposed_slot_start_iso"] = None
    sd_data["offered_times"] = []
    sd_data["slot_await_new_date"] = True
    sd_data["slot_await_confirm"] = False
    sd_data["slot_confirm_reprompts"] = 0
    sd_data["slot_silence_attempts"] = 0
    sd_data["slot_unclear_attempts"] = 0


def _repair_repeated_stt_time_component(raw: str, *, maximum: int) -> Optional[int]:
    """Убирает только однозначные повторы соседней цифры: 111→11, 220→20."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    candidates = {digits}
    while any(len(value) > 2 for value in candidates):
        shortened: set[str] = set()
        for value in candidates:
            if len(value) <= 2:
                shortened.add(value)
                continue
            for idx in range(1, len(value)):
                if value[idx] == value[idx - 1]:
                    shortened.add(value[:idx] + value[idx + 1 :])
        if not shortened or shortened == candidates:
            break
        candidates = shortened
    valid = {
        int(value)
        for value in candidates
        if 1 <= len(value) <= 2 and 0 <= int(value) <= maximum
    }
    return next(iter(valid)) if len(valid) == 1 else None


_SPOKEN_SLOT_TIME_NUMBERS = {
    "ноль": 0,
    "час": 1,
    "один": 1,
    "два": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
    "двадцать": 20,
    "двадцать один": 21,
    "двадцать два": 22,
    "двадцать три": 23,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
}


def _extract_spoken_slot_time(tl: str) -> Optional[str]:
    number_words = sorted(_SPOKEN_SLOT_TIME_NUMBERS, key=len, reverse=True)
    words_re = "|".join(re.escape(word) for word in number_words)
    minute_words = ("ноль", "пять", "десять", "пятнадцать", "двадцать", "тридцать", "сорок", "пятьдесят")
    minute_re = "|".join(minute_words)
    m = re.search(
        rf"\b(?:в|к|на)?\s*({words_re})(?:\s+час\w*)?\s+({minute_re})(?:\s+минут\w*)?\b",
        tl,
        re.I,
    )
    if m:
        hh = _SPOKEN_SLOT_TIME_NUMBERS[m.group(1).lower()]
        mm = _SPOKEN_SLOT_TIME_NUMBERS[m.group(2).lower()]
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"
    m = re.search(rf"\b(?:в|к)\s+({words_re})(?:\s+час\w*)?\b", tl, re.I)
    if m:
        hh = _SPOKEN_SLOT_TIME_NUMBERS[m.group(1).lower()]
        if 0 <= hh <= 23:
            return f"{hh:02d}:00"
    return None


def _looks_like_slot_time_attempt(text_value: str) -> bool:
    """Есть явная попытка назвать время, даже если STT исказил цифры."""
    tl = (text_value or "").lower()
    return bool(
        re.search(r"\b\d{1,5}\s*[:.\-]\s*\d{1,3}\b", tl)
        or re.search(r"\b(?:в|к)\s+\d{1,4}\b", tl)
        or _extract_spoken_slot_time(tl)
    )


def _extract_slot_time_from_text(text_value: str) -> Optional[str]:
    """Извлекает время и исправляет только однозначные повторы цифр STT."""
    if not text_value:
        return None
    tl = text_value.lower()
    for m in re.finditer(r"\b(\d{1,5})\s*[:\-.]\s*(\d{1,3})\b", tl):
        hh = _repair_repeated_stt_time_component(m.group(1), maximum=23)
        mm = _repair_repeated_stt_time_component(m.group(2), maximum=59)
        if hh is not None and mm is not None:
            return f"{hh:02d}:{mm:02d}"
    for m in re.finditer(r"\b(\d{1,5})\s+(\d{2,3})\b", tl):
        hh = _repair_repeated_stt_time_component(m.group(1), maximum=23)
        mm = _repair_repeated_stt_time_component(m.group(2), maximum=59)
        if hh is not None and mm is not None:
            return f"{hh:02d}:{mm:02d}"
    m = re.search(r"\b(?:в|к)\s*(\d{3,4})\b(?!\s*(?:год|[:.\-]))", tl)
    if m:
        compact = m.group(1)
        hh = int(compact[:-2])
        mm = int(compact[-2:])
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return f"{hh:02d}:{mm:02d}"
    spoken = _extract_spoken_slot_time(tl)
    if spoken:
        return spoken
    m = re.search(r"\b(\d{1,2})\s*[-\s]\s*нол", tl)
    if m:
        hh = int(m.group(1))
        if 0 <= hh <= 23:
            return f"{hh:02d}:00"
    m = re.search(r"(?:\bв\b|\bк\b)\s*(\d{1,2})\b", tl)
    if m:
        hh = int(m.group(1))
        if 0 <= hh <= 23:
            return f"{hh:02d}:00"
    return None


_ASAP_SLOT_REQUEST_RE = re.compile(
    r"(?:"
    r"ближайш\w*|самый\s+ближн\w*|сам\w*\s+ранн\w*|"
    r"как\s+можно\s+(?:быстр\w*|скор\w*)|"
    r"чем\s+быстр\w*|поскорее|побыстрее|"
    r"\bсрочно\b|\bпобыстрей\b|\bбыстрей\b|\bбыстрее\b|"
    r"\bбыстро\b|\bраньше\b|\bпораньше\b|\bскорее\b|\bсразу\b|"
    r"\bзавтра\b|\bсегодня\b|\bсейчас\b|"
    r"\bна\s+(?:этой|текущей)\s+неделе\b|\bна\s+неделе\b|"
    r"когда\s+(?:можно|есть)|можно\s+когда|что\s+есть|какие\s+вариант|"
    r"предложи\w*|есть\s+ли\s+время|"
    r"без\s+разниц\w*|не\s+важн\w*|\bневажно\b|"
    r"в\s+любое\s+время|в\s+любой\s+день|когда\s+угодно|"
    r"хотелось\s+бы\s+попасть|хотел(?:а|ось)\s+бы\s+попасть"
    r")",
    re.IGNORECASE,
)


def _is_asap_slot_request(t_low: str) -> bool:
    """Клиент просит ближайшую запись — ищем слот в 1С без подтверждения даты."""
    return bool(_ASAP_SLOT_REQUEST_RE.search(t_low or ""))


_UNTIL_END_OF_WEEK_REQUEST_RE = re.compile(
    r"(?:\bдо\s+конца\s+(?:этой|текущей)?\s*недел\w*\b|\bдо\s+воскресень\w*\b)",
    re.IGNORECASE,
)


def _is_until_end_of_week_request(t_low: str) -> bool:
    """Диапазон «до конца недели»: поиск слота от сегодня до воскресенья."""
    return bool(_UNTIL_END_OF_WEEK_REQUEST_RE.search(t_low or ""))


_WITHIN_WEEK_REQUEST_RE = re.compile(
    r"(?:\bв\s+течени[еия]\s+недел\w*\b|"
    r"\bв\s+ближайш\w+\s+недел\w*\b|"
    r"\bв\s+ближайш\w+\s+(?:7|сем[ьи])\s+дн\w*\b)",
    re.IGNORECASE,
)


def _is_within_week_request(t_low: str) -> bool:
    """Диапазон «в течение недели»: поиск слота от текущего дня до +7 дней."""
    return bool(_WITHIN_WEEK_REQUEST_RE.search(t_low or ""))


_STEP7_WAIT_REQUEST_RE = re.compile(
    r"(?:\bподожд\w*|\bпогод\w*|\bсекунд\w*|\bминут(?:у|ку|очку)?\b|"
    r"\bсейчас\b[^.!?]{0,30}\b(?:скажу|посмотр\w*|подума\w*))",
    re.IGNORECASE,
)


def _is_step7_wait_request(text: str) -> bool:
    """Клиент просит паузу; «сейчас» в такой фразе не означает ближайший слот."""
    return bool(_STEP7_WAIT_REQUEST_RE.search(text or ""))


def _is_step7_slot_availability_request(text: str, t_low: str) -> bool:
    """
    Запрос ближайшего слота на шаге «назовите дату и время».
    STT часто обрезает «когда можно» до «можно» / «а можно».
    """
    if _is_step7_wait_request(text):
        return False
    if _is_asap_slot_request(t_low):
        return True
    compact = re.sub(r"[^\w\s]+", " ", (text or "").strip(), flags=re.UNICODE)
    compact = re.sub(r"\s+", " ", compact).strip().lower()
    if compact in {
        "можно",
        "а можно",
        "когда",
        "а когда",
        "когда можно",
        "можно когда",
        "когда есть",
        "а когда есть",
        "есть когда",
        "без разницы",
        "не важно",
        "неважно",
        "когда угодно",
    }:
        return True
    return False


def _set_asap_desired_date(sd_data: dict) -> None:
    from dialog.business_calendar import is_business_day, next_business_day
    from dialog.dealer_time import dealer_local_now_naive

    today = dealer_local_now_naive().date()
    if is_business_day(today):
        sd_data["desired_date"] = today.strftime("%Y-%m-%d")
    else:
        sd_data["desired_date"] = next_business_day(today).strftime("%Y-%m-%d")
    sd_data["asap_slot"] = True


# Маркеры сервиса: клиент просит линию приёмщика/приёмки → сразу перевод на ассистента сервиса
# (в т.ч. обход 14_service_choice при включённой записи на ТО).
SERVICE_ASSISTANT_DIRECT_MARKERS: tuple[str, ...] = (
    "приемщик",
    "приемщика",
    "приемщику",
    "приемщиком",
    "приемщики",
    "приемщиков",
    "приёмщик",
    "приёмщика",
    "приёмщику",
    "приёмщиком",
    "приёмщики",
    "приёмщиков",
    "приемка",
    "приемку",
    "приемке",
    "приемки",
    "приёмка",
    "приёмку",
    "приёмке",
    "приёмки",
    # «с приёмкой» не матчится подстрокой «приёмка» — явно для ТЗ.
    "с приемкой",
    "с приёмкой",
    # STT выбора отдела: «со станции» / «со станцией» = СТО.
    "со станции",
    "со станцией",
    # По ТЗ: диагностика/диагностику/диагностики/диагност (STT) -> ассистент сервиса.
    "диагностик",
    "диагност",
    # Развал-схождение / STT «развал с хождения» → ассистент сервиса (не меню отделов).
    "развал",
    "схожден",
    "хожден",
)

# Маркетинг / бухгалтерия / клиентская линия → администратор (см. _handle_initial_state admin_markers).
VOICE_MARKETING_ADMIN_MARKERS: tuple[str, ...] = (
    "маркетинг",
    "с маркетингом",
    "маркетолог",
    "маркетолога",
    "маркетологу",
    "маркетологом",
    "маркетологи",
    "бухгалтерия",
    "бухгалтерии",
    "бухгалтерию",
    "бухгалтерской",
    "бухгалтерский",
    "клиентская",
    "клиентскую",
    "клиентской",
    "клиентское",
    "клиентские",
    "клиентский",
)

# Типичные искажения STT для «кузовн…» / кузовного цеха: те же действия, что и для «кузовной»,
# без подмены слов в normalize_department_stt_text — только совпадение по реплике клиента.
_BODY_SHOP_STT_MARKERS: tuple[str, ...] = (
    "зовной",  # STT без «ку» в «кузовной»
    "казавного",
    "казавной",
    "казавном",
    "казавным",
    "кузавно",
    "позваного",
    "позавного",
    "позавным",
    "позавной",
    "позывной",
    "позывного",
    "позывным",
    "кузав",
    "кузавного",
    "кузавной",
    "кузавным",
)

# Тестовый триггер голосового бота: принудительный вход в ветку записи на ТО.
TO_BOOKING_TEST_TRIGGERS: tuple[str, ...] = (
    "австралия",
)

# «мастер» в падежах = та же линия, что ассистент сервиса (без STT-замен в department_stt_normalize)
_RE_MASTER_FOR_SERVICE = re.compile(
    r"(?i)\bмастер(?:а|у|ом|е|ы|ов|ам|ами|ах)?\b"
)
# Заправка/дозаправка/проверка кондиционера → ассистент сервиса (INITIAL, первые 2 вопроса v2).
_RE_AC_REFILL = re.compile(r"(?<![а-яё])(?:дозаправ|дзаправ|заправ)")
_RE_MANAGER_TO_ADMIN = re.compile(r"(?i)\bменеджер(?:а)?\b")
_RE_SPECIALIST_WORD = re.compile(r"(?i)\bспециалист\w*\b")
_TO_BOOKING_ACTION_RE = re.compile(
    r"(?i)\b(?:запи(?:с|ш)\w*|пройти|проход\w*)\b"
)
_TO_BOOKING_SUBJECT_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:то|ту|тв|тио|тэо|тео|тего|тго|teo|tio)\b|"
    r"\bтехническ\w*\s+обслуживан\w*\b|"
    r"\bтехобслуживан\w*\b"
    r")"
)
# «ТО сервис», «сервис ТО», «то на сервис» — явный запрос записи на ТО.
_TO_SERVICE_COMBO_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:то|ту|тио|тэо|тео|tio|teo)\b.{0,40}\b(?:сервис\w*|эстэо)\b|"
    r"\b(?:сервис\w*|эстэо)\b.{0,40}\b(?:то|ту|тио|тэо|тео|tio|teo)\b"
    r")"
)
# «отдел технического обслуживания», «по техническому обслуживанию» — перевод на линию, не запись.
_TO_DEPARTMENT_CONTEXT_RE = re.compile(
    r"(?i)(?:"
    r"\b(?:отдел\w*|техотдел)\b|"
    r"\b(?:по|с|со)\s+(?:отдел\w*\s+)?(?:техническ\w*\s+)?обслуживан\w*\b"
    r")"
)

# Контекст гарантийного/сервисного ремонта: «специалист по гарантии» → ассистент сервиса, не админ.
_WARRANTY_SERVICE_CONTEXT_MARKERS: tuple[str, ...] = (
    "гарантийн",
    "гарантин",
    "карантин",
    "по гарантии",
    "гарантия",
    "гарантию",
    "гарантии",
    "гарантий",
    "гарантией",
    "гарантийный ремонт",
    "гарантийному ремонту",
    "гарантийного ремонта",
)
# «Специалист по …»: окраска / рихтовка / кузов → кузовной цех (до слесарного «по ремонту»).
_BODY_SPECIALIST_CONTEXT_MARKERS: tuple[str, ...] = (
    "окраск",
    "рихтовк",
    "кузовн",
    "кузов",
    "маляр",
    "лкп",
    "покраск",
    "покрас",
    "шпаклев",
    "стапель",
    *_BODY_SHOP_STT_MARKERS,
)


def _stt_implies_warranty_service(t_low: str) -> bool:
    if not t_low:
        return False
    if any(m in t_low for m in _WARRANTY_SERVICE_CONTEXT_MARKERS):
        return True
    return "гарант" in t_low and ("ремонт" in t_low or "обслуж" in t_low)


def _stt_implies_short_to_booking_request(text: str) -> bool:
    """Короткий STT-обрывок «пройти» / «П пройти» в ответе про отдел = пройти ТО."""
    normalized = re.sub(r"[^\w\s]+", " ", (text or "").lower(), flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized in {"пройти", "п пройти", "ппройти"}


def _stt_implies_standalone_to_maintenance_request(normalized: str) -> bool:
    """
    Смысловое упоминание технического обслуживания без обязательного глагола.

    Допускает повторы, вводные слова и обратный порядок слов, но не принимает
    формулировки про отдел/перевод («по техническому обслуживанию»).
    """
    if not normalized or _TO_DEPARTMENT_CONTEXT_RE.search(normalized):
        return False
    return bool(
        re.search(r"\bтехобслуживан\w*\b", normalized, re.I)
        or re.search(
            r"\bтехническ\w*(?:\s+\w+){0,3}\s+обслуживан\w*\b",
            normalized,
            re.I,
        )
        or re.search(
            r"\bобслуживан\w*(?:\s+\w+){0,2}\s+техническ\w*\b",
            normalized,
            re.I,
        )
    )


def _stt_implies_to_booking_request(text: str) -> bool:
    """Явное намерение записаться на ТО или пройти ТО, в любом порядке слов."""
    normalized = re.sub(r"[^\w\s]+", " ", (text or "").lower(), flags=re.UNICODE)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return False
    # Короткий ответ на стартовый вопрос об отделе: «ТО» и типичные варианты STT
    # сами по себе означают выбор автоматической записи на техобслуживание.
    if normalized in {"то", "тэо", "тео"}:
        return True
    has_plain_to = bool(re.search(r"\bто\b", normalized, re.I))
    has_technical_to_token = bool(
        re.search(
            r"\b(?:тэо|тео|тио|тго|teo|tio)\b|"
            r"\bтехническ\w*\s+обслуживан\w*\b|"
            r"\bтехобслуживан\w*\b",
            normalized,
            re.I,
        )
    )
    has_to_intent_context = bool(
        re.search(
            r"\b(?:нужн\w*|надо|хочу|хотел\w*|интересует|подходит|"
            r"пора|пройти|сделать|выполнить|планов\w*|очередн\w*)\b",
            normalized,
            re.I,
        )
        or re.search(
            r"\bто\s*[-]?\s*(?:\d+|перв\w*|втор\w*|трет\w*|четвер\w*|"
            r"пят\w*|шест\w*|седьм\w*|восьм\w*|девят\w*)\b",
            normalized,
            re.I,
        )
    )
    # Частица «-то» в разговорных репликах ("чё поговорить-то") не является ТО.
    if (
        has_plain_to
        and not has_technical_to_token
        and not has_to_intent_context
        and not _TO_BOOKING_ACTION_RE.search(normalized)
    ):
        return False
    # В этой функции реплика уже рассматривается как ответ на стартовый вопрос
    # об отделе. Поэтому отдельное «ТО» внутри более длинной реплики также означает
    # выбор записи: «мне нужно сделать ТО на Chery». Исключаем явный запрос отдела.
    if _TO_BOOKING_SUBJECT_RE.search(normalized) and not _TO_DEPARTMENT_CONTEXT_RE.search(
        normalized
    ):
        return True
    # Голосовой STT: «ТО сделать / ТО пройти» → «го сделать / го пройти».
    # Это явное намерение пройти ТО, а не общий запрос сервисного отдела.
    if re.search(r"\bго\s+(?:сделать|пройти)\b", normalized, re.I):
        return True
    if _stt_implies_short_to_booking_request(normalized):
        return True
    if _TO_SERVICE_COMBO_RE.search(normalized):
        return True
    if _stt_implies_standalone_to_maintenance_request(normalized):
        return True
    action = _TO_BOOKING_ACTION_RE.search(normalized)
    subject = _TO_BOOKING_SUBJECT_RE.search(normalized)
    if action and subject and abs(action.start() - subject.start()) <= 80:
        return True
    # STT иногда склеивает «записаться на ТО» в одно слово.
    compact = normalized.replace(" ", "")
    return bool(
        re.search(
            r"(?i)(?:запи(?:с|ш)\w*(?:на)?(?:то|ту|тио|тэо|тео|тго)|"
            r"(?:то|ту|тио|тэо|тео|тго)(?:пройти|проход\w*))",
            compact,
        )
    )


def _body_shop_context(t_low: str) -> bool:
    return any(m in t_low for m in _BODY_SPECIALIST_CONTEXT_MARKERS)


def _stt_implies_body_shop_specialist(t_low: str, raw_low: str = "") -> bool:
    """«со специалистом по окраске / рихтовке / кузовному ремонту» → кузовной цех."""
    raw = (raw_low or t_low).strip()
    if not _RE_SPECIALIST_WORD.search(raw):
        return False
    if _body_shop_context(raw) or _body_shop_context(t_low):
        return True
    # STT-нормализация могла уже свести фразу к «цех кузовного ремонта» / «окраска кузова».
    return "цех кузовного ремонта" in t_low or "окраска кузова" in t_low


def _stt_implies_ac_service_request(t_low: str, raw_low: str = "") -> bool:
    """
    Проверка / заправка / дозаправка кондиционера → ассистент сервиса.
    Не срабатывает на «эвакуация кондиционера» и прочее без проверк/заправ.
    """
    combined = f"{t_low} {(raw_low or '').strip().lower()}".strip()
    if not combined or "кондицион" not in combined:
        return False
    if "провер" in combined:
        return True
    return _RE_AC_REFILL.search(combined) is not None


def _stt_implies_wheel_alignment_service_request(t_low: str, raw_low: str = "") -> bool:
    """Развал / схождение (в т.ч. STT «развал с хождения») → ассистент сервиса."""
    combined = f"{t_low} {(raw_low or '').strip().lower()}".strip()
    if not combined:
        return False
    if "развал" in combined:
        return True
    if "схожден" in combined or "хожден" in combined:
        return True
    return re.search(r"(?i)развосхожден", combined) is not None


def _stt_implies_service_specialist(t_low: str, raw_low: str = "") -> bool:
    """«со специалистом по ремонту» (без кузовного контекста) → ассистент сервиса."""
    raw = (raw_low or t_low).strip()
    if not _RE_SPECIALIST_WORD.search(raw):
        return False
    if _body_shop_context(raw) or _body_shop_context(t_low):
        return False
    if "цех кузовного ремонта" in t_low or "окраска кузова" in t_low:
        return False
    combined = f"{raw} {t_low}"
    if _stt_implies_warranty_service(combined):
        return True
    if re.search(r"(?i)\bпо\s+ремонт", combined):
        return True
    return "ремонт" in combined


def _specialist_routes_to_department_not_admin(t_low: str, raw_low: str = "") -> bool:
    return (
        _stt_implies_body_shop_specialist(t_low, raw_low)
        or _stt_implies_service_specialist(t_low, raw_low)
    )


def _hits_voice_admin_marker(
    t_low: str, admin_markers: tuple[str, ...], raw_low: str = ""
) -> bool:
    """
    Маркеры перевода на администратора (подстроки).
    «Специалист» с темой окраски/кузова/ремонта/гарантии — не админ (см. _specialist_routes_*).
    """
    for m in admin_markers:
        if m == "специалист":
            if not _RE_SPECIALIST_WORD.search((raw_low or t_low).strip()):
                continue
            if _specialist_routes_to_department_not_admin(t_low, raw_low):
                continue
            return True
        if m in t_low:
            return True
    return False


def _is_sunday_samara_now() -> bool:
    try:
        now = datetime.now(ZoneInfo("Europe/Samara"))
        return now.weekday() == 6
    except Exception:
        return False


def _is_locksmith_branch_marker(t_low: str, raw_low: str = "") -> bool:
    combined = f"{raw_low} {t_low}"
    markers = (
        "слесар",
        "слесарн",
        "слесработ",
        "лесарный",
        "слесарный цех",
        "цех слесарного ремонта",
        "слесарного ремонта",
    )
    return any(m in combined for m in markers)


def number_to_words_ru(num: int) -> str:
    units = {
        0: "ноль", 1: "один", 2: "два", 3: "три", 4: "четыре",
        5: "пять", 6: "шесть", 7: "семь", 8: "восемь", 9: "девять",
    }
    teens = {
        10: "десять", 11: "одиннадцать", 12: "двенадцать", 13: "тринадцать",
        14: "четырнадцать", 15: "пятнадцать", 16: "шестнадцать",
        17: "семнадцать", 18: "восемнадцать", 19: "девятнадцать",
    }
    tens = {
        2: "двадцать", 3: "тридцать", 4: "сорок", 5: "пятьдесят",
        6: "шестьдесят", 7: "семьдесят", 8: "восемьдесят", 9: "девяносто",
    }
    hundreds = {
        1: "сто", 2: "двести", 3: "триста", 4: "четыреста", 5: "пятьсот",
        6: "шестьсот", 7: "семьсот", 8: "восемьсот", 9: "девятьсот",
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
    if day < 1 or day > 31:
        return str(day)
    special = {
        1: "п+ервое", 2: "втор+ое", 3: "тр+етье", 4: "четв+ертое",
        5: "п+ятое", 6: "шест+ое", 7: "седьм+ое", 8: "восьм+ое",
        9: "дев+ятое", 10: "дес+ятое", 11: "од+иннадцатое", 12: "двен+адцатое",
        13: "трин+адцатое", 14: "чет+ырнадцатое", 15: "пятн+адцатое",
        16: "шестн+адцатое", 17: "семн+адцатое", 18: "восемн+адцатое",
        19: "девятн+адцатое", 20: "двадц+атое", 21: "двадцать п+ервое",
        22: "двадцать втор+ое", 23: "двадцать тр+етье",
        24: "двадцать четв+ертое", 25: "двадцать п+ятое",
        26: "двадцать шест+ое", 27: "дв+адцать седьм+ое",
        28: "двадцать восьм+ое", 29: "двадцать дев+ятое",
        30: "тридц+атое", 31: "тридцать п+ервое",
    }
    return special.get(day, str(day))


def _slot_time_hhmm_to_tts(slot_time: str) -> str:
    """HH:MM → слова для TTS («восемь ноль ноль»)."""
    time_parts = (slot_time or "10:00").split(":")
    h = int(time_parts[0])
    m = int(time_parts[1]) if len(time_parts) > 1 else 0
    h_words = number_to_words_ru(h)
    m_words = number_to_words_ru(m) if m > 0 else "ноль"
    return f"{h_words} {m_words}" if m > 0 else f"{h_words} ноль ноль"


def replace_numbers_for_tts(text: str) -> str:
    def time_repl(m: re.Match) -> str:
        h = int(m.group(1))
        mi = int(m.group(2))
        h_words = number_to_words_ru(h)
        if mi == 0:
            return f"{h_words} ноль ноль"
        m_words = number_to_words_ru(mi)
        return f"{h_words} {m_words}"

    # Время 10:00
    text = re.sub(r"\b(\d{1,2}):(\d{2})\b", time_repl, text)
    # Время 8-00 / 21-00 (в текстах RAG часто через дефис — сырой TTS часто «глотает» цифры)
    text = re.sub(r"\b(\d{1,2})-(\d{2})\b", time_repl, text)

    def num_repl(m: re.Match) -> str:
        try:
            n = int(m.group(0))
        except ValueError:
            return m.group(0)
        return number_to_words_ru(n)

    text = re.sub(r"\b\d{1,3}\b", num_repl, text)
    return text


class BotDialogMixin(ToBookingV2Mixin):
    """
    Mixin с диалоговой логикой бота.

    Конкретный класс ОБЯЗАН предоставить:
      - self.state_machine: ConversationStateMachine
      - self.name_extractor: NameExtractor
      - self.date_parser: DateParser
      - self.car_brand_extractor: CarBrandExtractor
      - self.db_manager: DatabaseManager
      - self.leads_manager: LeadsManager
      - self.rag_system: RAGSystemStates | None
      - self.service_day_part: str | None
      - async def say(text: str) -> None
      - async def play_wav_or_tts(wav_file, text) -> None
    """

    # ------------------------------------------------------------------
    # Основной диспетчер
    # ------------------------------------------------------------------

    def _log(self, fmt: str, *args) -> None:
        """Лог с префиксом call_uuid для трассировки диалога."""
        uid = getattr(self, "call_uuid", "demo")
        logger.info("[%s] BOT_LOGIC " + fmt, uid, *args)

    # ------------------------------------------------------------------
    # Нерабочие часы (20:00–8:00, Europe/Samara)
    # ------------------------------------------------------------------

    def _is_may9_holiday_scripts_day(self) -> bool:
        """Скрипты/WAV с поздравлением к 9 мая — только в этот календарный день."""
        tz = ZoneInfo("Europe/Samara")
        now = datetime.now(tz)
        return (now.month, now.day) == (5, 9)

    def _get_non_working_audio_set(self) -> str:
        """
        Определяет набор after-hours аудио.
        default: обычное нерабочее время по часам.
        holiday: весь день нерабочий (дата из working_hours.json).
        """
        tz = ZoneInfo("Europe/Samara")
        now = datetime.now(tz)
        day_key = now.strftime("%Y-%m-%d")

        # Автоматически нерабочий только 9 мая (business_calendar); прочие даты —
        # voice_bot_special_days в working_hours.json.
        try:
            from dialog.business_calendar import is_holiday

            if is_holiday(now.date()):
                return "holiday"
        except Exception:
            pass

        try:
            with open(_WORKING_HOURS_PATH, encoding="utf-8") as f:
                schedule = json.load(f)
            special = (schedule.get("voice_bot_special_days") or {}).get(day_key)
            if not isinstance(special, dict):
                return "default"
            if (special.get("mode") or "").strip().lower() != "full_non_working":
                return "default"
            audio_set = (special.get("audio_set") or "holiday").strip().lower()
            return audio_set or "holiday"
        except Exception:
            return "default"

    def _is_non_working_hours(self) -> bool:
        """Проверяет, что сейчас нерабочее время или дата — спец-нерабочий день."""
        try:
            if self._get_non_working_audio_set() != "default":
                return True
            tz = ZoneInfo("Europe/Samara")
            now = datetime.now(tz)
            with open(_WORKING_HOURS_PATH, encoding="utf-8") as f:
                schedule = json.load(f)
            day_key = now.strftime("%Y-%m-%d")
            rec = (schedule.get("overrides") or {}).get(day_key)
            if not isinstance(rec, dict):
                rec = schedule.get("default") or {"start": "08:00", "end": "20:00"}

            def _parse_hhmm(value: object, fallback: time) -> time:
                try:
                    hour_s, minute_s = str(value).strip().split(":", 1)
                    return time(int(hour_s), int(minute_s))
                except (TypeError, ValueError):
                    return fallback

            start = _parse_hhmm(rec.get("start"), time(8, 0))
            end = _parse_hhmm(rec.get("end"), time(20, 0))
            if start == time(0, 0) and end == time(0, 0):
                return True
            return not (start <= now.time().replace(tzinfo=None) < end)
        except Exception:
            # Безопасный fallback согласован с фактическим графиком СТО.
            tz = ZoneInfo("Europe/Samara")
            hour = datetime.now(tz).hour
            return hour < 8 or hour >= 20

    def _get_after_hours_wav(self, need: Optional[ClientNeed]) -> str:
        """Возвращает WAV-файл для фразы нерабочего времени по потребности."""
        if self._get_non_working_audio_set() == "holiday":
            _MAP_HOLIDAY = {
                ClientNeed.USED_CARS: "20_after_hours_holiday_used_cars.wav",
                ClientNeed.NEW_CARS_CHERY_TENET: "20_after_hours_holiday_chery_tenet.wav",
                ClientNeed.SERVICE: "20_after_hours_holiday_service.wav",
                ClientNeed.BODY_REPAIR: "20_after_hours_holiday_body.wav",
                ClientNeed.PARTS: "20_after_hours_holiday_parts.wav",
                ClientNeed.ACCOUNTING: "20_after_hours_holiday_admin.wav",
            }
            return _MAP_HOLIDAY.get(need, "20_after_hours_holiday_admin.wav")
        _MAP = {
            ClientNeed.USED_CARS: "20_after_hours_used_cars.wav",
            ClientNeed.NEW_CARS_CHERY_TENET: "20_after_hours_chery_tenet.wav",
            ClientNeed.SERVICE: "20_after_hours_service.wav",
            ClientNeed.BODY_REPAIR: "20_after_hours_body.wav",
            ClientNeed.PARTS: "20_after_hours_parts.wav",
            ClientNeed.ACCOUNTING: "20_after_hours_admin.wav",
        }
        return _MAP.get(need, "20_after_hours_admin.wav")

    async def _do_after_hours_closure(self, need: Optional[ClientNeed] = None) -> None:
        """Фраза нерабочего времени, сохранение лида, завершение."""
        wav_file = self._get_after_hours_wav(need)
        need_str = need.value if need else "admin"
        service_data = self.state_machine.service_data or {}
        client_name = service_data.get("fio") or self.state_machine.client_name or None
        phone = (
            service_data.get("phone")
            or getattr(self, "caller_phone", None)
        )
        _vox = getattr(self, "_vox", None)
        _sid = _vox.get_session_id() if _vox is not None else None
        self.leads_manager.save_lead(
            client_name=client_name,
            phone=phone,
            need=need_str,
            service_data=service_data,
            outcome="нерабочее время",
            working_hours=False,
            voice_contact_outcome="bot_only",
            voice_bot_session_id=_sid,
        )
        setattr(self, "_voice_lead_saved", True)
        self._log("after_hours_closure: need=%s, wav=%s", need_str, wav_file)
        await self.play_wav_or_tts(wav_file, None)
        self.state_machine.transition_to(ConversationState.ENDED)

    async def _transfer_or_after_hours(
        self,
        wav_file: str,
        need: Optional[ClientNeed] = None,
        *,
        voice_admin_reason: Optional[str] = None,
        skip_announcement: bool = False,
    ) -> None:
        """Перевод или фраза нерабочего времени + завершение."""
        if self._is_non_working_hours():
            # Всегда фраза 20_* + завершение; без тихого AMI (тишина/NLU/«админ» и т.д.).
            await self._do_after_hours_closure(need)
            return
        hook = getattr(self, "on_voice_transfer_decision", None)
        if hook is not None:
            await hook(wav_file, need, voice_admin_reason)
        if not skip_announcement:
            await self.play_wav_or_tts(wav_file, None)
        else:
            exec_ami = getattr(self, "execute_transfer_ami_for_wav", None)
            if callable(exec_ami):
                await exec_ami(wav_file)
        self.state_machine.transition_to(ConversationState.ENDED)

    async def _play_transfer_announcement(self, text: str) -> None:
        """Озвучка «перевода» только в рабочее время."""
        if self._is_non_working_hours():
            self._log("after_hours: skip transfer announcement: %s", (text or "")[:80])
            return
        await self.play_wav_or_tts(None, text)

    async def _holiday_force_close_after_first_reply(self) -> None:
        """
        Праздничный спец-день (audio_set=holiday):
        после первой реакции клиента бот только сообщает о нерабочем дне и завершает звонок.
        Никаких переводов на отделы/администратора.
        """
        phone = (
            getattr(self, "caller_phone", None)
            or (self.state_machine.service_data or {}).get("phone")
        )
        _vox = getattr(self, "_vox", None)
        _sid = _vox.get_session_id() if _vox is not None else None
        need = self.state_machine.identified_need
        need_str = need.value if need else "secretary"
        self.leads_manager.save_lead(
            client_name=self.state_machine.client_name or None,
            phone=phone,
            need=need_str,
            outcome="нерабочее время",
            working_hours=False,
            voice_contact_outcome="bot_only",
            voice_bot_session_id=_sid,
        )
        setattr(self, "_voice_lead_saved", True)
        if self._is_may9_holiday_scripts_day():
            await self.play_wav_or_tts(None, HOLIDAY_FORCE_CLOSE_TEXT)
        else:
            need = self.state_machine.identified_need
            await self.play_wav_or_tts(self._get_after_hours_wav(need), None)
        self.state_machine.transition_to(ConversationState.ENDED)

    async def process_client_text(
        self,
        text: str,
        *,
        empty_kind: Optional[str] = None,
        name_listen_deadline_ts: Optional[float] = None,
    ) -> None:
        _trace_token = set_voice_to_booking_call_uuid(getattr(self, "call_uuid", None))
        try:
            await self._process_client_text_impl(
                text,
                empty_kind=empty_kind,
                name_listen_deadline_ts=name_listen_deadline_ts,
            )
        finally:
            reset_voice_to_booking_call_uuid(_trace_token)

    async def _process_client_text_impl(
        self,
        text: str,
        *,
        empty_kind: Optional[str] = None,
        name_listen_deadline_ts: Optional[float] = None,
    ) -> None:
        if self.state_machine.state == ConversationState.ENDED:
            self._log("process_client_text: session already ended, ignoring input")
            return
        raw_in = (text or "").strip()
        # CRM/АТС: «нажмите (кнопку) 5» — молча на администратора в любом состоянии (до меню отделов).
        if raw_in and is_silent_immediate_admin_callback(raw_in):
            self.state_machine.post_greeting_name_done = True
            await self._transfer_or_after_hours(
                "03_transfer_admin.wav",
                None,
                voice_admin_reason="silent_callback",
                skip_announcement=True,
            )
            return
        text = normalize_department_stt_text(raw_in)
        state = self.state_machine.state
        self._log("process_client_text state=%s text='%s'", state.value, (text or "")[:80])
        if state in (
            ConversationState.SERVICE_DATA_COLLECTION,
            ConversationState.SERVICE_SLOT_SELECTION,
            ConversationState.SERVICE_BOOKED,
        ):
            voice_to_booking_trace(
                "dialog_turn",
                state=state.value,
                stt_len=len((text or "").strip()),
                stt_preview=stt_preview_for_trace(text),
            )
        t_low = (text or "").lower()
        if (
            self.state_machine.service_data.get("sunday_service_limit_announced")
            and _is_sunday_samara_now()
            and is_to_v2_service_assistant_request(text)
        ):
            await self._play_transfer_announcement("Поняла, перевожу Вас на администратора.")
            await self._transfer_or_after_hours(
                "03_transfer_admin.wav",
                None,
                voice_admin_reason="client_request",
                skip_announcement=True,
            )
            return
        # Спец-день (9 мая): после первой реакции клиента всегда озвучиваем праздничное
        # закрытие и завершаем звонок, без переводов по отделам и без иных развилок.
        if state == ConversationState.INITIAL and self._get_non_working_audio_set() == "holiday":
            await self._holiday_force_close_after_first_reply()
            return
        noise_phrases = [
            "подписывайтесь на наш канал", "подпишитесь на наш канал",
            "подписывайтесь на наши каналы", "подпишитесь на наши каналы",
        ]
        if any(p in t_low for p in noise_phrases):
            await self.play_wav_or_tts("17_repeat.wav", None)
            return
        if _RE_MANAGER_TO_ADMIN.search(t_low):
            in_to_v2 = (
                self.state_machine.service_data.get("to_v2")
                and state in (
                    ConversationState.SERVICE_DATA_COLLECTION,
                    ConversationState.SERVICE_SLOT_SELECTION,
                    ConversationState.SERVICE_BOOKED,
                )
            )
            menu14_service = (
                state == ConversationState.NEED_IDENTIFIED
                and self.state_machine.identified_need == ClientNeed.SERVICE
                and SERVICE_BOOKING_ENABLED
                and getattr(self.state_machine, "menu14_booking_active", False)
            )
            if not in_to_v2 and not menu14_service:
                self._log("process_client_text: manager keyword -> admin transfer")
                await self._play_transfer_announcement("Перевожу Вас на администратора.")
                await self._transfer_or_after_hours(
                    "03_transfer_admin.wav", None, voice_admin_reason="client_request", skip_announcement=True
                )
                return
        if stt_implies_insurance_admin_department(raw_in) or stt_implies_insurance_admin_department(
            text or ""
        ):
            self._log("process_client_text: insurance -> admin transfer")
            self.state_machine.post_greeting_name_done = True
            await self._play_transfer_announcement("Перевожу Вас на администратора.")
            await self._transfer_or_after_hours(
                "03_transfer_admin.wav", None, voice_admin_reason="client_request", skip_announcement=True
            )
            return

        state = self.state_machine.state
        self._dialog_empty_kind = empty_kind
        if state == ConversationState.INITIAL:
            await self._handle_initial_state(
                raw_in,
                text,
                empty_kind=empty_kind,
                name_listen_deadline_ts=name_listen_deadline_ts,
            )
        elif state == ConversationState.AFTER_HOURS_NAME:
            await self._handle_after_hours_name_state(raw_in, text)
        elif state == ConversationState.ASKING_NAME:
            await self._handle_asking_name_state(text)
        elif state == ConversationState.NEED_IDENTIFIED:
            await self._handle_need_identified_state(text)
        elif state == ConversationState.SERVICE_DATA_COLLECTION:
            await self._handle_service_data_collection_state(text)
        elif state == ConversationState.SERVICE_SLOT_SELECTION:
            await self._handle_service_slot_selection_state(text)
        elif state == ConversationState.SERVICE_BOOKED:
            await self._handle_service_booked_state(raw_in, text)
        elif state == ConversationState.TRANSFERRING:
            await self._handle_transferring_state(text)
        else:
            await self._handle_default_state(text)
        self._dialog_empty_kind = None

    # ------------------------------------------------------------------
    # INITIAL
    # ------------------------------------------------------------------

    def _ponyala_transfer_line_for_need(self, need: ClientNeed) -> str:
        m = {
            ClientNeed.NEW_CARS_CHERY_TENET: (
                "Поняла, перевожу Вас на отдел продаж новых автомобилей Чери и Тэнет."
            ),
            ClientNeed.USED_CARS: "Поняла, перевожу Вас на отдел автомобилей с пробегом.",
            ClientNeed.BODY_REPAIR: "Поняла, перевожу Вас в цех кузовного ремонта.",
            ClientNeed.PARTS: "Поняла, перевожу Вас в отдел запасных частей.",
            ClientNeed.SERVICE: "Поняла, перевожу Вас на ассистента сервиса.",
        }
        return m.get(need, "Поняла, перевожу Вас на администратора.")

    def _wav_for_confirmed_need(self, need: ClientNeed) -> str:
        return {
            ClientNeed.SERVICE: "16_transfer_service_assistant.wav",
            ClientNeed.USED_CARS: "04_transfer_used_cars.wav",
            ClientNeed.BODY_REPAIR: "13_transfer_body_repair.wav",
            ClientNeed.NEW_CARS_CHERY_TENET: "05_transfer_chery_tenet.wav",
            ClientNeed.PARTS: "11_transfer_parts.wav",
        }.get(need, "03_transfer_admin.wav")

    async def _department_phase_transfer_for_need(
        self,
        need: ClientNeed,
        *,
        raw_client_text: Optional[str] = None,
        allow_booking_reentry: bool = True,
    ) -> None:
        """
        После выбора отдела: «Поняла…», затем AMI (рабочее время) или v2-нерабочее (имя + подтверждение).
        """
        scen = getattr(self, "voice_scenario", "legacy")
        rc = (raw_client_text or "").strip()
        rc_low = rc.lower()
        v2_night = scen == "v2" and self._is_non_working_hours()

        if (
            allow_booking_reentry
            and need == ClientNeed.SERVICE
            and SERVICE_BOOKING_ENABLED
        ):
            test_trigger = is_to_booking_test_trigger_stt(rc)
            if VOICE_TO_BOOKING_PROD_ENABLED or test_trigger:
                mode = "prod" if VOICE_TO_BOOKING_PROD_ENABLED else "australia"
                self._log("SERVICE booking choice enabled: mode=%s", mode)
                if rc and is_silent_immediate_admin_callback(rc):
                    self.state_machine.post_greeting_name_done = True
                    await self._transfer_or_after_hours(
                        "03_transfer_admin.wav",
                        None,
                        voice_admin_reason="silent_callback",
                        skip_announcement=True,
                    )
                    return
                if test_trigger or _stt_implies_to_booking_request(rc):
                    self._log("SERVICE explicit TO booking -> booking choice")
                    await self._open_service_booking_choice()
                    return
                self._log("SERVICE request without TO booking intent -> direct assistant")
            # Режим отката: SERVICE без «Австралии» идёт на ассистента 697777.

        if v2_night and rc and is_silent_immediate_admin_callback(rc):
            self.state_machine.post_greeting_name_done = True
            self.state_machine.set_identified_need(need)
            await self._transfer_or_after_hours(
                "03_transfer_admin.wav",
                None,
                voice_admin_reason="silent_callback",
                skip_announcement=True,
            )
            return

        self.state_machine.set_identified_need(need)
        if v2_night:
            service_data = self.state_machine.service_data or {}
            if service_data.get("fio") or self.state_machine.client_name:
                self._log(
                    "v2 after_hours: contact already collected, skip name request need=%s",
                    need.value,
                )
                await self._do_after_hours_closure(need)
            else:
                await self._v2_after_hours_begin_name_flow(need)
            return
        line = self._ponyala_transfer_line_for_need(need)
        await self._play_transfer_announcement(line)
        wav = self._wav_for_confirmed_need(need)
        await self._transfer_or_after_hours(wav, need, skip_announcement=True)

    async def _try_department_phase_from_client_speech(
        self,
        raw_text: str,
        text: str,
        *,
        log_prefix: str = "INITIAL",
        allow_booking_reentry: bool = True,
        ignore_service_direction: bool = False,
    ) -> bool:
        """Распознать отдел по реплике и перевести. True — перевод инициирован."""
        t_low = text.lower()
        raw_low = (raw_text or "").strip().lower()
        sales_prefix_to_new_cars = bool(
            (
                re.search(r"^\s*продаж\w*\b", raw_low, re.I)
                or re.search(r"^\s*продаж\w*\b", t_low, re.I)
            )
            and not any(
                x in t_low
                for x in (
                    "запчаст",
                    "детал",
                )
            )
        )

        async def _route_service_with_sunday_override() -> None:
            if _is_sunday_samara_now():
                self._log(
                    "%s: sunday service marker -> admin_or_booking_choice",
                    log_prefix,
                )
                await self._start_sunday_locksmith_admin_or_booking_choice()
                return
            await self._department_phase_transfer_for_need(
                ClientNeed.SERVICE,
                raw_client_text=raw_text,
                allow_booking_reentry=allow_booking_reentry,
            )

        if sales_prefix_to_new_cars:
            self._log("%s: sales-prefix without parts/details -> NEW_CARS_CHERY_TENET", log_prefix)
            await self._department_phase_transfer_for_need(
                ClientNeed.NEW_CARS_CHERY_TENET,
                raw_client_text=raw_text,
                allow_booking_reentry=allow_booking_reentry,
            )
            return True

        if SERVICE_BOOKING_ENABLED and allow_booking_reentry and (
            is_to_booking_test_trigger_stt(text) or is_to_booking_test_trigger_stt(raw_text)
        ):
            self._log("%s: TO booking test trigger -> SERVICE", log_prefix)
            await _route_service_with_sunday_override()
            return True
        if SERVICE_BOOKING_ENABLED and allow_booking_reentry and (
            _stt_implies_short_to_booking_request(raw_text)
            or _stt_implies_short_to_booking_request(text)
        ):
            self._log("%s: short «пройти» STT -> explicit TO booking", log_prefix)
            await _route_service_with_sunday_override()
            return True
        if not ignore_service_direction and (
            is_to_price_inquiry(raw_text)
            or is_to_price_inquiry(text)
        ):
            self._log("%s: TO price inquiry -> SERVICE", log_prefix)
            await _route_service_with_sunday_override()
            return True
        if stt_implies_parts_department(raw_text) or stt_implies_parts_department(text):
            self._log("%s: запчасти (приоритет) -> PARTS", log_prefix)
            await self._department_phase_transfer_for_need(
                ClientNeed.PARTS,
                raw_client_text=raw_text,
                allow_booking_reentry=allow_booking_reentry,
            )
            return True
        if stt_implies_insurance_admin_department(raw_text) or stt_implies_insurance_admin_department(
            text
        ):
            self._log("%s: страхование -> admin", log_prefix)
            await self._play_transfer_announcement("Перевожу Вас на администратора.")
            await self._transfer_or_after_hours(
                "03_transfer_admin.wav", None, voice_admin_reason="client_request", skip_announcement=True
            )
            return True
        if not ignore_service_direction and (
            stt_implies_service_first_to_reply(raw_text)
            or stt_implies_service_first_to_reply(text)
        ):
            self._log("%s: первое (STT) -> SERVICE", log_prefix)
            await _route_service_with_sunday_override()
            return True
        if stt_implies_used_cars_department(raw_text) or stt_implies_used_cars_department(text):
            self._log("%s: Б/У / автомобили с пробегом -> USED_CARS", log_prefix)
            await self._department_phase_transfer_for_need(
                ClientNeed.USED_CARS,
                raw_client_text=raw_text,
                allow_booking_reentry=allow_booking_reentry,
            )
            return True

        if any(phrase in t_low for phrase in [
            "трейд-ин", "трейдин", "трейд ин", "троидин",
            "соедините с авто с пробегом", "соедините с автомобилями с пробегом",
            "авто с пробегом", "автомобили с пробегом", "отдел пробег",
            "переведите на пробег", "перевести на пробег", "с пробегом соедините",
            "переведите на трейд", "соедините с трейд",
        ]) or re.search(r"трейд[\s-]+ин", t_low):
            self._log("%s: explicit USED_CARS phrase -> department transfer", log_prefix)
            await self._department_phase_transfer_for_need(
                ClientNeed.USED_CARS,
                raw_client_text=raw_text,
                allow_booking_reentry=allow_booking_reentry,
            )
            return True

        admin_markers = (
            "администратор", "администратора", "administrator",
            "консультант", "консультанта",
            "человек", "человека", "оператор", "оператора",
            "ресепшн", "ресепшен", "reception",
            "отдел кредитования", "специалист",
            "ка дайте",
            *VOICE_MARKETING_ADMIN_MARKERS,
        )
        if _hits_voice_admin_marker(t_low, admin_markers, raw_low):
            # В рабочие дни администратор и ассистент сервиса не подменяют друг друга:
            # явный запрос администратора всегда ведём на администратора.
            self._log("%s: admin/human marker in service flow -> ADMIN", log_prefix)
            await self._play_transfer_announcement("Перевожу Вас на администратора.")
            await self._transfer_or_after_hours(
                "03_transfer_admin.wav",
                None,
                voice_admin_reason="client_request",
                skip_announcement=True,
            )
            return True
        if _stt_implies_body_shop_specialist(t_low, raw_low):
            self._log("%s: specialist + body shop -> BODY_REPAIR", log_prefix)
            await self._department_phase_transfer_for_need(
                ClientNeed.BODY_REPAIR,
                raw_client_text=raw_text,
                allow_booking_reentry=allow_booking_reentry,
            )
            return True
        if not ignore_service_direction and _stt_implies_service_specialist(t_low, raw_low):
            self._log("%s: specialist + service repair -> SERVICE", log_prefix)
            await _route_service_with_sunday_override()
            return True

        if "маляр" in t_low:
            self._log("%s: малярка -> BODY_REPAIR", log_prefix)
            await self._department_phase_transfer_for_need(
                ClientNeed.BODY_REPAIR,
                raw_client_text=raw_text,
                allow_booking_reentry=allow_booking_reentry,
            )
            return True

        if not ignore_service_direction and _stt_implies_ac_service_request(t_low, raw_low):
            self._log("%s: AC check/refill -> SERVICE", log_prefix)
            await _route_service_with_sunday_override()
            return True

        if not ignore_service_direction and _stt_implies_wheel_alignment_service_request(t_low, raw_low):
            self._log("%s: wheel alignment -> SERVICE", log_prefix)
            await _route_service_with_sunday_override()
            return True

        service_markers = [
            "диспетчер", "диспетчера",
            "на сервис", "записать на сервис", "запись на сервис", "машину на сервис",
            "запись", "записаться",
            "на сервис записать", "сервис", "ремонт", "обслуживание", "техобслуживание",
            "на замену", "заменить", "поменять", "шиномонтаж", "на диагностику", "установить",
            "починить", "починить машину", "починить автомобиль",
            "машину нужно сделать", "автомобиль нужно сделать",
            "найти неисправность", "подвеска", "замена масла", "ремонт двигателя", "замена колодок",
            "гарантия", "гарантию", "гарантии", "гарантий", "гарантией",
            "слесарный", "слесарная", "слесарного",
            "слесарн",
            "слесар",
            "слесработ",
            "лесарный", "сарный",
            "слесарный цех", "цех слесарного ремонта", "слесарного ремонта", "сто", "эстэо",
            "на то записаться", "на ту", "на тэо записаться",
            "пройти то", "пройти ту", "тхобслуж", "тхбслуж", "техобслуж",
            "замену масла", "заменить масло", "масло заменить", "масло поменять", "поменять масло",
            "заменить фильтр", "поменять фильтр", "заменить колодки", "поменять колодки",
            "продиагностировать",
            "диспетчер сервиса", "диспетчера сервиса", "ассистент сервиса", "ассистенту сервиса",
            *SERVICE_ASSISTANT_DIRECT_MARKERS,
            "технический отдел",
            "технического отдела",
            "техническому отделу",
            "техническим отделом",
            "тех отдел",
            "техотдел",
            "техосмотр", "техосмотра", "техосмотру", "на техосмотр", "записаться на техосмотр",
            "сервис nissan", "сервис ниссан", "сервис нисан", "сервис нисссан",
            "nissan сервис", "ниссан сервис", "нисан сервис", "нисссан сервис",
            "nissan", "ниссан", "нисан", "нисссан", "nisssan",
            "сделать тго", "тго",
            "обслуживание автомобиля", "обслуживание машины",
            "обслужить автомобиль", "обслужить машину",
            "мойка",
            "мойку",
            "мойке",
            "мойки",
            "автомойка",
            "автомойку",
            "автомойке",
            "автомойки",
            "развал",
            "схожден",
            "хожден",
        ]
        service_hit = (
            any(m in t_low for m in service_markers)
            or _stt_implies_wheel_alignment_service_request(t_low, raw_low)
            or re.search(r"\b(?:то|ту|тио|тео|тэо)\b", t_low)
            or re.search(r"\bтехническ\w*\s+обслуживан\w*\b", t_low)
            or _RE_MASTER_FOR_SERVICE.search(t_low) is not None
        )
        if service_hit:
            body_early = any(
                w in t_low
                for w in (
                    "кузов",
                    "кузовной",
                    "кузовного",
                    "кузовном",
                    "кузовная",
                    "кузовные",
                    "кузова",
                    "кузовом",
                    *_BODY_SHOP_STT_MARKERS,
                )
            )
            if body_early:
                self._log(
                    "%s: кузовной контекст -> BODY_REPAIR (не принудительный SERVICE по маркерам)",
                    log_prefix,
                )
                await self._department_phase_transfer_for_need(
                    ClientNeed.BODY_REPAIR,
                    raw_client_text=raw_text,
                    allow_booking_reentry=allow_booking_reentry,
                )
                return True
            if not ignore_service_direction:
                nissan_here = any(
                    n in t_low
                    for n in ("nissan", "ниссан", "нисан", "нисссан", "nisssan")
                )
                self._log(
                    "%s: %s -> _department_phase_transfer_for_need(SERVICE)",
                    log_prefix,
                    "Nissan+service" if nissan_here else "service_marker/TO",
                )
                await _route_service_with_sunday_override()
                return True

        other_brand_keywords = [
            "джили", "geely", "мерседес", "mercedes", "бмв", "bmw", "ауди", "audi",
            "тойота", "toyota", "лада", "ваз", "hyundai",
            "хьендай", "хендай", "киа", "kia", "мазда", "mazda", "митсубиси", "mitsubishi",
        ]
        if (
            ("какие" in t_low or "другие" in t_low or "еще" in t_low or "кроме" in t_low)
            and ("марк" in t_low or "автомоб" in t_low or "машин" in t_low or "бренд" in t_low)
        ) or ("не чери" in t_low or "не тенет" in t_low or "не тэнет" in t_low) or any(
            b in t_low for b in other_brand_keywords
        ):
            await self.play_wav_or_tts(
                None, self._ponyala_transfer_line_for_need(ClientNeed.NEW_CARS_CHERY_TENET)
            )
            await self._transfer_or_after_hours(
                "05_transfer_chery_tenet.wav",
                ClientNeed.NEW_CARS_CHERY_TENET,
                skip_announcement=True,
            )
            return True

        has_sell = any(w in t_low for w in ["продать", "продажа", "выкуп", "сдать", "оценить", "выкупить"])
        used_cars_direct = any(p in t_low for p in [
            "выкупите автомобиль", "выкупите мое авто", "выкупите мой автомобиль",
            "купите у меня автомобиль", "с пробегом",
        ])
        has_car = bool(re.search(r"автомобил|авто|машин", t_low))
        if (has_sell and has_car) or used_cars_direct:
            await self._department_phase_transfer_for_need(
                ClientNeed.USED_CARS,
                raw_client_text=raw_text,
                allow_booking_reentry=allow_booking_reentry,
            )
            return True

        need = self._identify_need(text)

        if need:
            if ignore_service_direction and need == ClientNeed.SERVICE:
                return False
            if need == ClientNeed.NEW_CARS_CHERY_TENET and (
                "не чери" in t_low or "не тенет" in t_low or "не тэнет" in t_low
                or "другие" in t_low or "кроме" in t_low or "другая марка" in t_low
            ):
                self._log("%s: need=CHERY_TENET but 'other brand' -> 05_transfer_chery_tenet", log_prefix)
                await self.play_wav_or_tts(
                    None, self._ponyala_transfer_line_for_need(ClientNeed.NEW_CARS_CHERY_TENET)
                )
                await self._transfer_or_after_hours(
                    "05_transfer_chery_tenet.wav",
                    ClientNeed.NEW_CARS_CHERY_TENET,
                    skip_announcement=True,
                )
                return True
            self._log(
                "%s: need=%s from _identify_need -> _department_phase_transfer_for_need",
                log_prefix,
                need.value,
            )
            if need == ClientNeed.SERVICE:
                await _route_service_with_sunday_override()
            else:
                await self._department_phase_transfer_for_need(
                    need,
                    raw_client_text=raw_text,
                    allow_booking_reentry=allow_booking_reentry,
                )
            return True

        return False

    async def _start_sunday_locksmith_admin_or_booking_choice(self) -> None:
        await self.play_wav_or_tts(
            None,
            "В воскресенье приемка слесарного цеха не работает. "
            "Я могу записать Вас на техобслуживание или слесарные работы, "
            "поставив резерв на удобное время. "
            "В понедельник в рабочее время диспетчер с Вами созвонится для уточнения деталей записи. "
            "Скажите запиши, если хотите записаться, или администратор, и я переведу Вас на человека.",
        )
        self.state_machine.set_identified_need(ClientNeed.SERVICE)
        self.state_machine.menu14_booking_active = True
        # Фиксируем, что ограничение воскресной приемки уже озвучено в этом звонке.
        self.state_machine.service_data["sunday_service_limit_announced"] = True
        self.state_machine.service_data["sunday_locksmith_choice_active"] = True
        self.state_machine.service_data["sunday_locksmith_unclear_attempts"] = 0
        self.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)

    def _is_sunday_locksmith_choice_active(self) -> bool:
        return bool(
            (self.state_machine.service_data or {}).get("sunday_locksmith_choice_active")
        )

    async def _handle_sunday_locksmith_choice(self, text: str) -> bool:
        sd = self.state_machine.service_data
        t = (text or "").lower().strip()
        if is_to_booking_price_inquiry(text):
            sd["sunday_locksmith_choice_active"] = False
            sd["sunday_locksmith_unclear_attempts"] = 0
            self.state_machine.menu14_booking_active = False
            await self._play_transfer_announcement("Поняла, перевожу Вас на администратора.")
            await self._transfer_or_after_hours(
                "03_transfer_admin.wav",
                None,
                voice_admin_reason="client_request",
                skip_announcement=True,
            )
            return True
        admin_markers = (
            "администратор",
            "администратора",
            "диспетчер",
            "диспетчера",
            "ассистент",
            "ассистента",
            "человек",
            "человека",
            "оператор",
            "оператора",
            *SERVICE_ASSISTANT_DIRECT_MARKERS,
        )
        if any(m in t for m in admin_markers):
            sd["sunday_locksmith_choice_active"] = False
            sd["sunday_locksmith_unclear_attempts"] = 0
            self.state_machine.menu14_booking_active = False
            await self._play_transfer_announcement("Поняла, перевожу Вас на администратора.")
            await self._transfer_or_after_hours(
                "03_transfer_admin.wav",
                None,
                voice_admin_reason="client_request",
                skip_announcement=True,
            )
            return True
        if is_to_v2_record_choice(text) or _stt_implies_to_booking_request(text):
            sd["sunday_locksmith_choice_active"] = False
            sd["sunday_locksmith_unclear_attempts"] = 0
            self.state_machine.menu14_booking_active = False
            await self._to_v2_begin_data_collection()
            return True
        attempts = int(sd.get("sunday_locksmith_unclear_attempts") or 0) + 1
        sd["sunday_locksmith_unclear_attempts"] = attempts
        if attempts < 2:
            await self.play_wav_or_tts(None, "Простите, повторите еще раз.")
            return True
        sd["sunday_locksmith_choice_active"] = False
        sd["sunday_locksmith_unclear_attempts"] = 0
        self.state_machine.menu14_booking_active = False
        await self._play_transfer_announcement("Перевожу Вас на администратора.")
        await self._transfer_or_after_hours(
            "03_transfer_admin.wav",
            None,
            voice_admin_reason="client_request",
            skip_announcement=True,
        )
        return True

    async def _v2_after_hours_begin_name_flow(self, need: Optional[ClientNeed]) -> None:
        """v2 + нерабочее: запрос имени для перезвона (окно записи — внешний цикл сессии)."""
        self.state_machine.transition_to(ConversationState.AFTER_HOURS_NAME)
        audio_set = self._get_non_working_audio_set()
        need_str = need.value if need else "secretary"
        self._log("v2 after_hours: ask name for need=%s, audio_set=%s", need_str, audio_set)
        if audio_set == "holiday" and self._is_may9_holiday_scripts_day():
            await self.play_wav_or_tts(
                "23_after_hours_v2_holiday_ask_name.wav",
                HOLIDAY_AFTER_HOURS_V2_ASK_NAME_INTRO,
            )
            return
        await self.play_wav_or_tts("23_after_hours_v2_ask_name.wav", AFTER_HOURS_V2_ASK_NAME_INTRO)

    async def _handle_after_hours_name_state(self, raw_text: str, text: str) -> None:
        """
        v2 + нерабочее: одно окно записи (~5 с) после 23 — затем фраза 24 и лид
        вне зависимости от ответа (без переспрашивания имени).
        """
        need = self.state_machine.identified_need
        raw = (raw_text or "").strip()
        if raw and not is_meaningless_voice_stt(raw):
            name_candidate = self.name_extractor.extract_name(text)
            if not name_candidate and (text or "").strip():
                name_candidate = (text or "").strip()[:80]
            if name_candidate:
                self.state_machine.set_name(name_candidate)
        if self._get_non_working_audio_set() == "holiday" and self._is_may9_holiday_scripts_day():
            await self.play_wav_or_tts(
                "24_after_hours_v2_holiday_contact_done.wav",
                HOLIDAY_AFTER_HOURS_V2_CONTACT_DONE,
            )
        else:
            await self.play_wav_or_tts("24_after_hours_v2_contact_done.wav", AFTER_HOURS_V2_CONTACT_DONE)
        phone = (
            getattr(self, "caller_phone", None)
            or (self.state_machine.service_data or {}).get("phone")
        )
        _vox = getattr(self, "_vox", None)
        _sid = _vox.get_session_id() if _vox is not None else None
        need_str = need.value if need else "secretary"
        self.leads_manager.save_lead(
            client_name=self.state_machine.client_name or None,
            phone=phone,
            need=need_str,
            outcome="нерабочее время",
            working_hours=False,
            voice_contact_outcome="bot_only",
            voice_bot_session_id=_sid,
        )
        setattr(self, "_voice_lead_saved", True)
        self._log("after_hours_name: lead saved need=%s name=%r", need_str, self.state_machine.client_name)
        self.state_machine.transition_to(ConversationState.ENDED)

    async def _handle_initial_state(
        self,
        raw_text: str,
        text: str,
        *,
        empty_kind: Optional[str] = None,
        name_listen_deadline_ts: Optional[float] = None,
    ) -> None:
        # CRM/АТС/IVR/форма сайта — молча на администратора (рабочее и нерабочее; нерабочее без 20_*).
        if is_silent_immediate_admin_callback(raw_text):
            self.state_machine.post_greeting_name_done = True
            await self._transfer_or_after_hours(
                "03_transfer_admin.wav",
                None,
                voice_admin_reason="silent_callback",
                skip_announcement=True,
            )
            return

        # --- Первая реплика после «Как Вас зовут» (legacy): всегда имя. ---
        if not self.state_machine.post_greeting_name_done:
            # Пустой текст: vad_no_audio = ждали окно имени, речи нет → к отделам.
            # stt_empty = звук был, STT пустой → к отделам только если истёк общий дедлайн имени (см. session).
            if not (text or "").strip():
                now = asyncio.get_running_loop().time()
                if empty_kind == "vad_no_audio":
                    close_name_phase = True
                elif empty_kind == "stt_empty":
                    close_name_phase = (
                        name_listen_deadline_ts is None or now >= float(name_listen_deadline_ts)
                    )
                else:
                    close_name_phase = True
                if close_name_phase:
                    self.state_machine.post_greeting_name_done = True
                    self._log(
                        "INITIAL: фаза имени завершена без имени (empty_kind=%r deadline=%s now=%.3f)",
                        empty_kind,
                        name_listen_deadline_ts,
                        now,
                    )
                return
            name_candidate = self.name_extractor.extract_name(text)
            if not name_candidate and (text or "").strip():
                name_candidate = (text or "").strip()[:80]
            if name_candidate:
                self.state_machine.set_name(name_candidate)
            self.state_machine.post_greeting_name_done = True
            return

        if await self._try_department_phase_from_client_speech(raw_text, text):
            return

        dps = getattr(self.state_machine, "department_prompts_shown", 0)
        scen = getattr(self, "voice_scenario", "legacy")
        # v2: пустой STT в пределах 8 с после приветствия или WAV 22 — ждём; по истечении — меню / админ.
        if (
            scen == "v2"
            and not (raw_text or "").strip()
            and not (text or "").strip()
            and empty_kind in ("stt_empty", "vad_no_audio")
        ):
            now = asyncio.get_running_loop().time()
            if name_listen_deadline_ts is not None and now < float(name_listen_deadline_ts):
                self._log(
                    "INITIAL v2: пустая реплика (dps=%s, %s) — в окне ожидания до %.3f",
                    dps,
                    empty_kind,
                    name_listen_deadline_ts,
                )
                return
            self._log(
                "INITIAL v2: окно ожидания после фразы бота истекло (dps=%s) — следующий шаг",
                dps,
            )
        # После меню отделов (12 для legacy, 22 для v2) пустой STT/VAD: не трогаем state,
        # ждём следующий цикл записи (только legacy; v2 — см. дедлайн выше).
        if (
            scen != "v2"
            and dps >= 1
            and not (raw_text or "").strip()
            and not (text or "").strip()
            and empty_kind in ("vad_no_audio", "stt_empty")
        ):
            self._log(
                "INITIAL: пустая реплика после меню отделов (legacy, dps=%s empty_kind=%r) — ждём следующую запись",
                dps,
                empty_kind,
            )
            return
        await self._ask_about_departments()
        return

    # ------------------------------------------------------------------
    # ASKING_NAME
    # ------------------------------------------------------------------

    async def _handle_asking_name_state(self, text: str) -> None:
        t = text.lower().strip()
        has_positive = any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in POSITIVE_WORDS)
        has_negative = any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in NEGATIVE_WORDS)

        if self.state_machine.client_name and has_positive:
            need = self._identify_need(text)
            if need:
                await self._do_transfer_or_service_choice(need)
            else:
                await self.play_wav_or_tts("19_pleasant_help.wav", None)
                self.state_machine.state = ConversationState.INITIAL
            return

        if has_negative:
            self.state_machine.client_name = None

        if self.state_machine.client_name and not has_positive and not has_negative:
            repeated_name = self.name_extractor.extract_name(text)
            if repeated_name and repeated_name == self.state_machine.client_name:
                need = self._identify_need(text)
                if need:
                    await self._do_transfer_or_service_choice(need)
                else:
                    await self.play_wav_or_tts("19_pleasant_help.wav", None)
                    self.state_machine.state = ConversationState.INITIAL
                return
            need = self._identify_need(text)
            if need:
                await self._do_transfer_or_service_choice(need)
                return
            self.state_machine.state = ConversationState.INITIAL
            await self._ask_about_departments()
            return

        name = self.name_extractor.extract_name(text)
        if name:
            self.state_machine.set_name(name)
            await self.play_wav_or_tts(None, f"Я правильно поняла, Вас зовут {name}?")
        else:
            self.state_machine.increment_name_attempt()
            if self.state_machine.client_name_attempts >= 2:
                await self.play_wav_or_tts(None, "Спасибо. Чем я могу Вам помочь?")
                self.state_machine.state = ConversationState.INITIAL
            else:
                await self.play_wav_or_tts("02_ask_name.wav", None)

    # ------------------------------------------------------------------
    # NEED_IDENTIFIED
    # ------------------------------------------------------------------

    async def _handle_need_identified_state(self, text: str) -> None:
        t = text.lower()
        need = self.state_machine.identified_need
        if need == ClientNeed.SERVICE and self._is_sunday_locksmith_choice_active():
            if await self._handle_sunday_locksmith_choice(text):
                return
        if stt_implies_insurance_admin_department(text):
            self._log("NEED_IDENTIFIED: insurance -> admin")
            await self._play_transfer_announcement("Перевожу Вас на администратора.")
            await self._transfer_or_after_hours(
                "03_transfer_admin.wav", None, voice_admin_reason="client_request", skip_announcement=True
            )
            return
        if any(m in t for m in VOICE_MARKETING_ADMIN_MARKERS):
            self._log("NEED_IDENTIFIED: marketing/admin markers -> admin")
            await self._play_transfer_announcement("Перевожу Вас на администратора.")
            await self._transfer_or_after_hours(
                "03_transfer_admin.wav", None, voice_admin_reason="client_request", skip_announcement=True
            )
            return
        if "маляр" in t:
            self._log("NEED_IDENTIFIED: малярка -> BODY_REPAIR")
            await self._department_phase_transfer_for_need(ClientNeed.BODY_REPAIR, raw_client_text=text)
            return
        if need == ClientNeed.SERVICE:
            if SERVICE_BOOKING_ENABLED and getattr(self.state_machine, "menu14_booking_active", False):
                # Меню записи на ТО не отменяет общий выбор направления. Если клиент
                # назвал любое направление из главного меню, переводим именно туда,
                # а не считаем реплику неясным ответом на «записать или перевести».
                # Повторное «на ТО»/«техобслуживание» само по себе не является сменой
                # направления: после меню 14 это выбор самостоятельной записи.
                explicit_service_direction = (
                    is_to_v2_service_assistant_request(text)
                    or "слесар" in t
                    or "станци" in t
                )
                if await self._try_department_phase_from_client_speech(
                    text,
                    text,
                    log_prefix="NEED_IDENTIFIED menu14 department switch",
                    allow_booking_reentry=False,
                    ignore_service_direction=not explicit_service_direction,
                ):
                    return
                if await self._handle_need_identified_to_v2_menu14(text):
                    return
            if SERVICE_BOOKING_ENABLED and is_to_booking_test_trigger_stt(text):
                self._log(
                    "NEED_IDENTIFIED SERVICE: test trigger -> to_v2 step 1"
                )
                voice_to_booking_trace("to_service_data_collection", trigger="booking_test_word")
                await self._to_v2_begin_data_collection()
                return
            if is_to_v2_record_choice(text):
                self._log("NEED_IDENTIFIED SERVICE: record_marker -> to_v2 step 1")
                voice_to_booking_trace("to_service_data_collection", trigger="record_marker")
                self.state_machine.menu14_booking_active = False
                await self._to_v2_begin_data_collection()
                return
            human_markers = [
                "переведите",
                "перевести",
                "ассистент",
                "человек",
                "оператор",
                "живой",
                "менеджер",
                *SERVICE_ASSISTANT_DIRECT_MARKERS,
                # формулировка из меню АТС / речи клиента → линия ассистента сервиса
                "станция техобслуживания",
                "станцию техобслуживания",
                "станции техобслуживания",
                "станция технического обслуживания",
                "станцию технического обслуживания",
            ]
            if any(m in t for m in human_markers):
                self._log("NEED_IDENTIFIED SERVICE: human_marker -> 16_transfer_service_assistant, ENDED")
                await self._transfer_or_after_hours("16_transfer_service_assistant.wav", ClientNeed.SERVICE)
                return
        other_brand_keywords = [
            "джили", "geely", "мерседес", "mercedes", "бмв", "bmw", "ауди", "audi",
            "тойота", "toyota", "лада", "ваз", "hyundai",
            "хьендай", "хендай", "киа", "kia", "мазда", "mazda", "митсубиси", "mitsubishi",
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
            need = self.state_machine.identified_need
            if self._is_non_working_hours():
                await self._do_after_hours_closure(need)
                return
            if need == ClientNeed.NEW_CARS_CHERY_TENET:
                await self.play_wav_or_tts(
                    None, "Я переведу Вас в отдел продаж Чери и Тэнет, там Вас проконсультируют.")
            elif need == ClientNeed.USED_CARS:
                await self.play_wav_or_tts(
                    None, "Я переведу звонок в отдел продаж автомобилей с пробегом, там Вас проконсультируют.")
            else:
                await self._transfer_or_after_hours(
                    "05_transfer_chery_tenet.wav", ClientNeed.NEW_CARS_CHERY_TENET
                )
                return
            self.state_machine.transition_to(ConversationState.ENDED)
            return

        if any(w in t for w in NEGATIVE_WORDS):
            alt_need = self._identify_need(text)
            if alt_need and alt_need != self.state_machine.identified_need:
                self._log("NEED_IDENTIFIED: NEGATIVE + alt_need=%s -> _do_transfer_or_service_choice", alt_need.value if alt_need else None)
                await self._do_transfer_or_service_choice(alt_need)
                return
            self.state_machine.increment_need_attempt()
            if self.state_machine.should_transfer_to_consultant_need():
                await self._transfer_or_after_hours(
                    "03_transfer_admin.wav", None, voice_admin_reason="nlu_failed"
                )
                return
            self.state_machine.reject_need()
            self.state_machine.state = ConversationState.INITIAL
            await self._ask_about_departments()
        elif any(w in t for w in POSITIVE_WORDS):
            self._log("NEED_IDENTIFIED: POSITIVE_WORDS -> confirm_need, _handle_confirmed_need")
            self.state_machine.confirm_need()
            await self._handle_confirmed_need()
        else:
            alt_need = self._identify_need(text)
            if alt_need and alt_need != self.state_machine.identified_need:
                await self._do_transfer_or_service_choice(alt_need)
                return
            if not hasattr(self.state_machine, "need_unclear_attempts"):
                self.state_machine.need_unclear_attempts = 0
            self.state_machine.need_unclear_attempts = getattr(
                self.state_machine, "need_unclear_attempts", 0
            ) + 1
            if self.state_machine.need_unclear_attempts >= 2:
                await self._transfer_or_after_hours(
                    "03_transfer_admin.wav", None, voice_admin_reason="nlu_failed"
                )
            else:
                self.state_machine.state = ConversationState.INITIAL
                await self._ask_about_departments()

    # ------------------------------------------------------------------
    # CONFIRMED NEED → перевод / запись на сервис
    # ------------------------------------------------------------------

    async def _open_service_booking_choice(self) -> None:
        """Открыть меню: самостоятельная запись на ТО или ассистент сервиса."""
        await self.play_wav_or_tts(None, "Поняла.")
        await self.play_wav_or_tts("14_service_choice.wav", None)
        self.state_machine.set_identified_need(ClientNeed.SERVICE)
        self.state_machine.menu14_booking_active = True
        self.state_machine.menu14_unclear_attempts = 0
        self.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)

    async def _do_transfer_or_service_choice(self, need: Optional[ClientNeed] = None) -> None:
        """Перевод сразу или выбор «записать / перевести» для сервиса."""
        n = need or self.state_machine.identified_need
        if not n:
            self._log("_do_transfer_or_service_choice: need=None, skip")
            return
        if n == ClientNeed.SERVICE:
            if SERVICE_BOOKING_ENABLED and VOICE_TO_BOOKING_PROD_ENABLED:
                self._log("_do_transfer_or_service_choice: SERVICE -> booking choice (prod)")
                await self._open_service_booking_choice()
            else:
                self._log(
                    "_do_transfer_or_service_choice: SERVICE -> "
                    "16_transfer_service_assistant, ENDED (rollback mode)"
                )
            await self._transfer_or_after_hours("16_transfer_service_assistant.wav", n)
        else:
            self._log("_do_transfer_or_service_choice: need=%s -> _handle_confirmed_need", n.value)
            self.state_machine.set_identified_need(n)
            await self._handle_confirmed_need()

    async def _handle_confirmed_need(self) -> None:
        need = self.state_machine.identified_need
        if not need:
            self._log("_handle_confirmed_need: identified_need=None, skip")
            return
        self._log("_handle_confirmed_need: need=%s -> transfer or after_hours", need.value)
        if need == ClientNeed.SERVICE:
            # Единый перевод на ассистента сервиса (нет отдельного сценария «да» → мастер)
            await self._transfer_or_after_hours("16_transfer_service_assistant.wav", need)
        elif need == ClientNeed.USED_CARS:
            await self._transfer_or_after_hours("04_transfer_used_cars.wav", need)
        elif need == ClientNeed.BODY_REPAIR:
            await self._transfer_or_after_hours("13_transfer_body_repair.wav", need)
        elif need == ClientNeed.NEW_CARS_CHERY_TENET:
            await self._transfer_or_after_hours("05_transfer_chery_tenet.wav", need)
        elif need == ClientNeed.PARTS:
            await self._transfer_or_after_hours("11_transfer_parts.wav", need)
        else:
            await self._transfer_or_after_hours(
                "03_transfer_admin.wav", need, voice_admin_reason="fallback"
            )

    # ------------------------------------------------------------------
    # SERVICE_DATA_COLLECTION
    # ------------------------------------------------------------------

    async def _handle_service_data_collection_state(self, text: str) -> None:
        sd_data = self.state_machine.service_data
        if sd_data.get("to_v2"):
            await self._handle_service_data_collection_v2(text)
            return
        t_low = text.lower()

        from dialog.service_speech_parse import prepare_booking_date_stt

        norm_date_text, slot_d9, slot_d10 = prepare_booking_date_stt(text, sd_data)
        new_date = (
            self.date_parser.parse_date(
                norm_date_text,
                slot_day9_hint=slot_d9,
                slot_day10_hint=slot_d10,
            )
            if self.date_parser
            else None
        )

        new_fio_dict = self.name_extractor.extract_fio(text)
        new_phone = self.name_extractor.extract_phone(text)
        # Из реплики на шаге «Назовите марку и модель» сохраняем только марку/модель:
        # год выпуска в этом шаге игнорируем, чтобы не ломать распознавание авто
        # шумом STT и смешанными ответами.
        is_car_brand_model_step = not sd_data.get("car_confirmed")
        year_match = re.search(r"\b(19|20)\d{2}\b", text)
        if year_match and not is_car_brand_model_step:
            sd_data["car_year"] = year_match.group(0)
        mileage_value = parse_mileage_km_from_speech(
            text,
            t_low,
            car_year=sd_data.get("car_year"),
        )

        # \b‑границы важны: иначе «то» матчится внутри «авТОмобиль» и любая реплика
        # с упоминанием машины ошибочно считается списком работ.
        works_found = bool(re.search(
            r"(\bто\d*\b|\bто-\d+\b|\bтехнич\w*\s+обслужив\w*\b|\bобслужив\w*\b|"
            r"\bтехобслужив\w*\b|\bремонт\w*\b|\bзамен\w*\b|\bколодк\w*\b|\bмасл\w*\b|"
            r"\bдиагност\w*\b|\bфильтр\w*\b)",
            t_low, flags=re.IGNORECASE,
        ))

        day_part = self._extract_day_part(text)
        if day_part:
            self.service_day_part = day_part

        if new_fio_dict:
            new_fio_value = (
                f"{new_fio_dict['surname']} {new_fio_dict['name']} {new_fio_dict['patronymic']}".strip()
            )
            current_fio_value = (sd_data.get("fio") or "").strip()
            # Не даём STT-искажениям марки/модели (напр. «Шваган Тигуан»)
            # перетирать уже собранное корректное ФИО.
            if not current_fio_value:
                sd_data["fio"] = new_fio_value
            elif new_fio_value != current_fio_value:
                maybe_car = self.car_brand_extractor.extract_car_info(new_fio_value)
                if maybe_car:
                    logger.info(
                        "SERVICE skip FIO overwrite: looks like car info current=%r new=%r",
                        current_fio_value,
                        new_fio_value,
                    )
                else:
                    sd_data["fio"] = new_fio_value
        if new_phone:
            sd_data["phone"] = new_phone
        if new_date:
            sd_data["desired_date"] = new_date.strftime("%Y-%m-%d")
        elif _is_asap_slot_request(t_low):
            _set_asap_desired_date(sd_data)
        if mileage_value:
            sd_data["mileage"] = mileage_value
            # Распознанный пробег — в raw только число, не вся реплика клиента.
            sd_data["mileage_raw"] = mileage_value

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
                    if "вечер" in t_low and h < 12:
                        h += 12
                    if "дня" in t_low and h < 12:
                        h += 12
                    desired_time = f"{h:02d}:00"
        if desired_time:
            sd_data["desired_time"] = desired_time

        if works_found:
            work_text = text
            if mileage_value:
                work_text = re.sub(r"\d+\s*(?:тысяч|тыс|тыс\.)", "", work_text, flags=re.IGNORECASE)
                work_text = re.sub(r"\d+(?:\s+\d{3})*\s*(?:километров|км)", "", work_text, flags=re.IGNORECASE)
                work_text = re.sub(r"\s*пробег\s*", "", work_text, flags=re.IGNORECASE)
                work_text = work_text.strip()

            work_words = work_text.split()
            cleaned_words = []
            for word in work_words:
                word_lower = word.lower()
                if word_lower in ("километров", "км", "тысяч", "тыс", "пробег"):
                    continue
                if word_lower in ("в", "на", "по", "к", "для", "за", "с", "у"):
                    continue
                cleaned_words.append("ТО" if word_lower == "то" else word)

            work_text = " ".join(cleaned_words).strip()
            if re.search(r"технич\w*\s+обслужив\w*", work_text, flags=re.IGNORECASE):
                work_text = "техническое обслуживание"

            time_only = re.search(
                r"(после\s+обеда|вторая\s+половина\s+дня|утром|вечером|днем|после\s+обеду|после\s+полу?дня)",
                work_text.lower(),
            )
            if time_only and len(re.sub(r"\s+", "", work_text)) <= 15:
                work_text = ""
            if work_text and len(work_text) > 1 and not time_only:
                sd_data["work_list"] = work_text
            else:
                fallback_text = text
                if "то" in t_low and "то" not in fallback_text.lower().split():
                    fallback_text = re.sub(r"\bто\b", "ТО", fallback_text, flags=re.IGNORECASE)
                if not time_only and not re.search(
                    r"(после\s+обеда|вечером|утром|вторая\s+половина\s+дня)", fallback_text.lower()
                ):
                    sd_data["work_list"] = fallback_text

        caller_ph = getattr(self, "caller_phone", None)
        voice_to_booking_trace(
            "sd_turn",
            **summarize_service_data(sd_data, caller_phone=caller_ph),
        )

        # Шаг 1: ФИО и телефон
        if not sd_data.get("fio") or not sd_data.get("phone"):
            if not sd_data.get("fio"):
                await self.play_wav_or_tts(None, "Назовите, пожалуйста, Ваши фамилию, имя и отчество полностью.")
            else:
                await self.play_wav_or_tts(None, "Назовите Ваш номер телефона для связи.")
            return

        # Шаг 2: Автомобиль
        if not sd_data.get("car_confirmed"):
            if not sd_data.get("client_found_in_db") and sd_data.get("car_attempts", 0) == 0:
                caller_ph = getattr(self, "caller_phone", None)
                voice_to_booking_trace(
                    "client_lookup_start",
                    **summarize_service_data(sd_data, caller_phone=caller_ph),
                )
                client = await self.db_manager.find_client_async(
                    fio=sd_data["fio"], phone=sd_data["phone"]
                )
                voice_to_booking_trace(
                    "client_lookup_result",
                    found=bool(client),
                    client_cols=(list(client.keys())[:24] if isinstance(client, dict) and client else None),
                )
                if client:
                    self._process_client_from_db(client, sd_data)
                    car_info_tts = self._format_car_for_tts(sd_data)
                    await self.play_wav_or_tts(
                        None, f"У Вас автомобиль {car_info_tts}, верно?")
                    return
                else:
                    sd_data["client_found_in_db"] = False
                    sd_data["car_attempts"] = sd_data.get("car_attempts", 0) + 1
                    if sd_data["car_attempts"] >= 2:
                        sd_data["car_confirmed"] = True
                        await self.play_wav_or_tts(None, "Назовите, пожалуйста, пробег и желаемый список работ.")
                        return
                    await self.play_wav_or_tts(None, "Назовите, пожалуйста, марку и модель автомобиля.")
                    return

            if _voice_affirmative_stt(t_low):
                sd_data["car_confirmed"] = True
                await self.play_wav_or_tts(None, "Назовите, пожалуйста, пробег и желаемый список работ.")
                return
            elif any(w in t_low for w in NEGATIVE_WORDS):
                car_info = self.car_brand_extractor.extract_car_info(text)
                if car_info:
                    brand, model = car_info
                    car_info_tts = self.car_brand_extractor.format_for_tts(brand, model)
                    sd_data.update({
                        "car_brand": brand,
                        "car_model": f"{brand} {model}" if model else brand,
                        "car_attempts": sd_data.get("car_attempts", 0) + 1,
                    })
                    await self.play_wav_or_tts(None, f"У Вас {car_info_tts}, верно?")
                    return
                else:
                    sd_data["car_attempts"] = sd_data.get("car_attempts", 0) + 1
                    if sd_data["car_attempts"] < 2:
                        await self.play_wav_or_tts(None, "Назовите, пожалуйста, марку и модель автомобиля.")
                        return
                    else:
                        sd_data["car_confirmed"] = True
                        await self.play_wav_or_tts(None, "Назовите, пожалуйста, пробег и желаемый список работ.")
                        return
            else:
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
                        await self.play_wav_or_tts(None, f"У Вас {car_info_tts}, верно?")
                        return
                    else:
                        sd_data["car_attempts"] = sd_data.get("car_attempts", 0) + 1
                        if sd_data["car_attempts"] >= 2:
                            sd_data["car_confirmed"] = True
                            await self.play_wav_or_tts(None, "Назовите, пожалуйста, пробег и желаемый список работ.")
                            return
                        else:
                            await self.play_wav_or_tts(None, "Назовите, пожалуйста, марку и модель автомобиля.")
                            return
                else:
                    # Если на вопрос «верно?» пришёл неясный ответ (не да/нет и без новой модели),
                    # обязательно повторяем вопрос, чтобы не зависать в тишине.
                    sd_data["car_attempts"] = sd_data.get("car_attempts", 0) + 1
                    if sd_data["car_attempts"] >= 2:
                        sd_data["car_confirmed"] = True
                        await self.play_wav_or_tts(None, "Назовите, пожалуйста, пробег и желаемый список работ.")
                        return
                    car_info_tts = self._format_car_for_tts(sd_data)
                    await self.play_wav_or_tts(None, f"У Вас {car_info_tts}, верно?")
                    return

        # Шаг 3: Пробег и список работ
        if sd_data.get("car_confirmed") and not sd_data.get("mileage_work_confirmed"):
            if _voice_affirmative_stt(t_low) and sd_data.get("mileage") and sd_data.get("work_list"):
                sd_data["mileage_work_confirmed"] = True
                await self.play_wav_or_tts(None, "Назовите, пожалуйста, желаемый день и время начала работ.")
                return
            elif any(w in t_low for w in NEGATIVE_WORDS) and sd_data.get("mileage_work_attempts", 0) > 0:
                sd_data["mileage_work_attempts"] = sd_data.get("mileage_work_attempts", 0) + 1
                if sd_data["mileage_work_attempts"] < 2:
                    if not sd_data.get("mileage"):
                        await self.play_wav_or_tts(None, "Назовите, пожалуйста, пробег.")
                    elif not sd_data.get("work_list"):
                        await self.play_wav_or_tts(None, "Назовите, пожалуйста, список работ.")
                    else:
                        await self.play_wav_or_tts(None, "Назовите, пожалуйста, пробег и желаемый список работ.")
                    return
                else:
                    sd_data["mileage_work_confirmed"] = True
                    await self.play_wav_or_tts(None, "Назовите, пожалуйста, желаемый день и время начала работ.")
                    return
            elif sd_data.get("mileage") and sd_data.get("work_list"):
                if sd_data.get("mileage_work_attempts", 0) == 0:
                    mileage_num = int(sd_data["mileage"])
                    mileage_words = number_to_words_ru(mileage_num // 1000) if mileage_num >= 1000 else number_to_words_ru(mileage_num)
                    mileage_text = f"{mileage_words} тысяч километров" if mileage_num >= 1000 else f"{mileage_words} километров"
                    sd_data["mileage_work_attempts"] = 1
                    await self.play_wav_or_tts(
                        None, f"Пробег {mileage_text}, список работ: {sd_data['work_list']}. Всё верно?")
                    return
                else:
                    sd_data["mileage_work_confirmed"] = True
                    await self.play_wav_or_tts(None, "Назовите, пожалуйста, желаемый день и время начала работ.")
                    return
            else:
                mileage_attempts = sd_data.get("mileage_work_attempts", 0)
                if mileage_attempts == 0:
                    if not sd_data.get("mileage") and not sd_data.get("work_list"):
                        await self.play_wav_or_tts(None, "Назовите, пожалуйста, пробег и желаемый список работ.")
                    elif not sd_data.get("mileage"):
                        await self.play_wav_or_tts(None, "Назовите, пожалуйста, пробег.")
                    elif not sd_data.get("work_list"):
                        # Прямой переспрос про работы. Голосовому помощнику доступны
                        # только ТО / замена масла / тормозных колодок (см. ТЗ);
                        # озвучиваем это клиенту, чтобы он понимал ограничения.
                        await self.play_wav_or_tts(
                            None,
                            "Пожалуйста, назовите желаемые работы. Я могу записать "
                            "только на техн+ическое обсл+уживание или простые механические работы — замену "
                            "масла или тормозных колодок.",
                        )
                    sd_data["mileage_work_attempts"] = 1
                    return
                else:
                    # Второй раз клиент так и не назвал работы. Считаем «операция не
                    # названа» и резервируем 120 минут (см. LABOR_MINUTES_OPERATION_UNKNOWN
                    # в service_booking_service.py); пробег, если есть, — оставляем.
                    if not sd_data.get("work_list"):
                        sd_data["work_list"] = "операция не названа"
                        sd_data["operation_unknown"] = True
                    sd_data["mileage_work_confirmed"] = True
                    await self.play_wav_or_tts(None, "Назовите, пожалуйста, желаемый день и время начала работ.")
                    return

        # Шаг 4: Дата и время
        if sd_data.get("mileage_work_confirmed") and not sd_data.get("date_time_confirmed"):
            if _is_within_week_request(t_low):
                sd_data["within_week_slot"] = True
                sd_data["until_week_end_slot"] = False
                sd_data["asap_slot"] = False
                sd_data["date_time_confirmed"] = True
                await self._process_service_booking()
                return
            if _is_until_end_of_week_request(t_low):
                sd_data["until_week_end_slot"] = True
                sd_data["within_week_slot"] = False
                sd_data["asap_slot"] = False
                sd_data["date_time_confirmed"] = True
                await self._process_service_booking()
                return
            if _is_asap_slot_request(t_low) or sd_data.get("asap_slot"):
                if not sd_data.get("desired_date"):
                    _set_asap_desired_date(sd_data)
                else:
                    sd_data["asap_slot"] = True
                sd_data["date_time_confirmed"] = True
                await self._process_service_booking()
                return
            if _voice_affirmative_stt(t_low) and sd_data.get("desired_date"):
                sd_data["date_time_confirmed"] = True
                await self._process_service_booking()
                return
            elif any(w in t_low for w in NEGATIVE_WORDS) and sd_data.get("date_time_attempts", 0) > 0:
                sd_data["date_time_confirmed"] = False
                sd_data["desired_date"] = None
                self.service_day_part = None
                sd_data["date_time_attempts"] = sd_data.get("date_time_attempts", 0) + 1
                if sd_data["date_time_attempts"] < 2:
                    await self.play_wav_or_tts(None, "Назовите, пожалуйста, желаемый день и время начала работ.")
                    return
                else:
                    sd_data["date_time_confirmed"] = True
                    await self._process_service_booking()
                    return
            if sd_data.get("desired_date") and sd_data.get("date_time_attempts", 0) == 0:
                dt = datetime.strptime(sd_data["desired_date"], "%Y-%m-%d")
                day_txt = day_to_ordinal_ru(dt.day)
                sd_data["date_time_attempts"] = 1
                await self.play_wav_or_tts(
                    None,
                    f"Желаемая дата: {day_txt} {MONTHS_TTS_GENITIVE[dt.month]}. Всё верно?",
                )
                return
            elif sd_data.get("desired_date") and sd_data.get("date_time_attempts", 0) > 0:
                # Уже задали «Всё верно?». Раньше любая вторая реплика (в т.ч. пустая STT)
                # ошибочно считалась согласием и шла в 1С → обрыв/перевод. Ждём да/нет
                # или новую дату (она подхватывается в начале обработчика через parse_date).
                raw_st = (text or "").strip()
                dt = datetime.strptime(sd_data["desired_date"], "%Y-%m-%d")
                day_txt = day_to_ordinal_ru(dt.day)
                if not raw_st or is_meaningless_voice_stt(text):
                    await self.play_wav_or_tts(
                        None,
                        f"Желаемая дата: {day_txt} {MONTHS_TTS_GENITIVE[dt.month]}. Всё верно?",
                    )
                    return
                await self.play_wav_or_tts(
                    None,
                    "Скажите, пожалуйста, «да» или «нет». Или назовите другой желаемый день.",
                )
                return
            elif any(w in t_low for w in NEGATIVE_WORDS):
                sd_data["date_time_attempts"] = sd_data.get("date_time_attempts", 0) + 1
                if sd_data["date_time_attempts"] < 2:
                    await self.play_wav_or_tts(None, "Назовите, пожалуйста, желаемый день и время начала работ.")
                    return
                else:
                    sd_data["date_time_confirmed"] = True
                    await self._process_service_booking()
                    return
            else:
                if not sd_data.get("desired_date"):
                    sd_data["date_time_attempts"] = sd_data.get("date_time_attempts", 0) + 1
                    empty_kind = getattr(self, "_dialog_empty_kind", None)
                    if empty_kind in ("vad_no_audio", "stt_empty"):
                        if (
                            empty_kind == "stt_empty"
                            and int(sd_data.get("date_stt_soft_reask") or 0) < 1
                        ):
                            sd_data["date_stt_soft_reask"] = 1
                            await self.play_wav_or_tts(
                                None,
                                "Не расслышала, повторите дату и время, пожалуйста.",
                            )
                            return
                        await self._offer_nearest_slot_after_date_silence()
                        return
                    if sd_data["date_time_attempts"] >= 2:
                        await self._offer_nearest_slot_after_date_silence()
                        return
                    await self.play_wav_or_tts(None, "Назовите, пожалуйста, желаемый день и время начала работ.")
                    return
                voice_to_booking_trace("sd_date_confirm_fallback", note="desired_date_set_no_branch")
                dt_fb = datetime.strptime(sd_data["desired_date"], "%Y-%m-%d")
                day_txt_fb = day_to_ordinal_ru(dt_fb.day)
                await self.play_wav_or_tts(
                    None,
                    f"Желаемая дата: {day_txt_fb} {MONTHS_TTS_GENITIVE[dt_fb.month]}. Всё верно?",
                )
                return

    # ------------------------------------------------------------------
    # SERVICE_SLOT_SELECTION
    # ------------------------------------------------------------------

    async def _handle_service_slot_selection_state(self, text: str) -> None:
        from datetime import date as _date

        from dialog.dealer_time import dealer_local_now_naive
        from dialog.service_slot_alternatives import (
            is_slot_alt_after_stt,
            is_slot_alt_before_stt,
            pick_alt_from_parsed_date,
            sd_entry_to_picked,
        )
        from dialog.service_slot_day_part import resolve_weekday_booking_date
        from dialog.service_slot_pick import PickedSlot, pick_slot_for_day
        from dialog.service_slot_time import snap_hhmm_to_step
        from dialog.service_speech_parse import (
            prepare_booking_date_stt,
        )
        from dialog.sto_to_price_inquiry import is_service_human_transfer_request

        raw_slot_text = text or ""
        sd_data = self.state_machine.service_data
        text, slot_day9_hint, slot_day10_hint = prepare_booking_date_stt(
            raw_slot_text,
            sd_data,
        )
        t_low = text.lower()
        if is_service_human_transfer_request(text):
            await self._transfer_service_assistant_slot_selection(
                reason="slot_human_transfer",
            )
            return
        if await self._handle_service_slot_selection_v2(text):
            return

        def _format_offer(slot_date: str, slot_time: str) -> str:
            dt = datetime.strptime(slot_date, "%Y-%m-%d")
            day_txt = day_to_ordinal_ru(dt.day)
            return format_service_slot_offer_tts(
                day_txt,
                MONTHS_TTS_GENITIVE[dt.month],
                _slot_time_hhmm_to_tts(slot_time),
                slot_hhmm=slot_time,
            )

        def _format_confirm_reprompt_with_slot() -> str:
            slot_date = sd_data.get("proposed_date")
            slot_time = sd_data.get("proposed_time")
            if slot_date and slot_time:
                try:
                    dt = datetime.strptime(slot_date, "%Y-%m-%d")
                    day_txt = day_to_ordinal_ru(dt.day)
                    month_tts = MONTHS_TTS_GENITIVE[dt.month]
                    time_tts = _slot_time_hhmm_to_tts(slot_time)
                    return format_slot_confirm_reprompt_tts(
                        day_text=day_txt,
                        month_tts=month_tts,
                        time_tts=time_tts,
                    )
                except ValueError:
                    pass
            return format_slot_confirm_reprompt_tts()

        async def _play_slot_reprompt() -> None:
            if sd_data.get("slot_await_confirm"):
                await self.play_wav_or_tts(
                    None,
                    _format_confirm_reprompt_with_slot(),
                )
            elif sd_data.get("proposed_date") and sd_data.get("proposed_time"):
                await self.play_wav_or_tts(
                    None,
                    _format_offer(sd_data["proposed_date"], sd_data["proposed_time"]),
                )
            else:
                await self.play_wav_or_tts(None, "Повторите, пожалуйста, удобное время.")

        def _offered_times_set() -> set[str]:
            raw = sd_data.get("offered_times") or []
            if isinstance(raw, list):
                return set(str(x) for x in raw)
            return set()

        def _remember_offer(slot_date: str, slot_time: str) -> None:
            sd_data["proposed_date"] = slot_date
            sd_data["proposed_time"] = slot_time
            offered = list(_offered_times_set())
            if slot_time not in offered:
                offered.append(slot_time)
            sd_data["offered_times"] = offered

        def _remember_picked(picked: PickedSlot) -> None:
            slot_date = picked.date_str
            slot_time = picked.time_str
            slot_start_iso = picked.start_iso
            if picked.start_iso:
                try:
                    st = datetime.fromisoformat(picked.start_iso)
                    slot_date = st.strftime("%Y-%m-%d")
                    slot_time = st.strftime("%H:%M")
                except (TypeError, ValueError):
                    pass
            slot_date, slot_time, slot_start_iso = _normalize_slot_offer_fields(
                slot_date,
                slot_time,
                slot_start_iso or (f"{slot_date}T{slot_time}:00" if slot_date and slot_time else None),
            )
            if not slot_date or not slot_time:
                return
            _remember_offer(slot_date, slot_time)
            sd_data["proposed_post"] = picked.post_id
            sd_data["proposed_acceptor_id"] = picked.acceptor_id
            picked_mechanic = (picked.mechanic_name or "").strip()
            if picked_mechanic:
                sd_data["proposed_mechanic_name"] = picked_mechanic
            sd_data["proposed_slot_start_iso"] = slot_start_iso or f"{slot_date}T{slot_time}:00"

        async def _offer_slot_summary(picked: PickedSlot, message: str) -> None:
            """Озвучить готовую фразу о слоте (без дубля «Могу записать вас?»)."""
            sd_data["slot_await_new_date"] = False
            _remember_picked(picked)
            await self.play_wav_or_tts(None, message)
            sd_data["slot_unclear_attempts"] = 0
            sd_data["slot_silence_attempts"] = 0
            sd_data["slot_await_confirm"] = True
            sd_data["slot_confirm_reprompts"] = 0

        async def _say_slot_offer(
            picked: PickedSlot,
            *,
            prefix: Optional[str] = None,
        ) -> None:
            sd_data["slot_await_new_date"] = False
            _remember_picked(picked)
            msg = _format_offer(
                sd_data["proposed_date"],
                sd_data["proposed_time"],
            )
            if prefix:
                msg = f"{prefix} {msg}"
            await self.play_wav_or_tts(None, msg)
            sd_data["slot_unclear_attempts"] = 0
            sd_data["slot_silence_attempts"] = 0
            sd_data["slot_await_confirm"] = True
            sd_data["slot_confirm_reprompts"] = 0

        async def _book_proposed_slot() -> None:
            slot_start_iso = sd_data.get("proposed_slot_start_iso")
            slot_date = sd_data.get("proposed_date") or sd_data.get("desired_date")
            slot_time = sd_data.get("proposed_time") or "10:00"
            if slot_start_iso:
                try:
                    slot_start = datetime.fromisoformat(slot_start_iso)
                    slot_date = slot_start.strftime("%Y-%m-%d")
                    slot_time = slot_start.strftime("%H:%M")
                    sd_data["proposed_date"] = slot_date
                    sd_data["proposed_time"] = slot_time
                except (TypeError, ValueError):
                    slot_start = None
            else:
                slot_start = None
            if slot_start is None and slot_date and slot_time:
                try:
                    slot_start = datetime.strptime(
                        f"{slot_date} {slot_time}",
                        "%Y-%m-%d %H:%M",
                    )
                except (TypeError, ValueError):
                    slot_start = None
            if slot_start is not None:
                comparable_start = (
                    slot_start.replace(tzinfo=None)
                    if slot_start.tzinfo
                    else slot_start
                )
                if comparable_start <= dealer_local_now_naive():
                    sd_data["desired_date"] = None
                    sd_data["desired_time"] = None
                    enter_slot_await_new_date(sd_data)
                    await self.play_wav_or_tts(
                        None,
                        "Выбранное время уже прошло. Назовите другую дату и время.",
                    )
                    return
            sd_data["slot_await_confirm"] = False
            sd_data["slot_confirm_reprompts"] = 0
            sd_data.pop("slot_day9_hint", None)
            sd_data.pop("slot_day10_hint", None)

            fio_value = sd_data.get("fio_from_db") or sd_data.get("fio")
            phone_value = _booking_phones_for_1c(
                phone_from_db=sd_data.get("phone_from_db"),
                phone_input=sd_data.get("phone"),
                caller_phone=getattr(self, "caller_phone", None),
            )
            car_brand_value, car_model_value = self._to_v2_car_submission_values(sd_data)
            # Нормализованные поля для MAX берем из общей цепочки нормализации авто.
            car_brand_norm = (sd_data.get("car_brand") or "").strip()
            car_model_norm = (self._to_v2_service_data_model_name(sd_data) or "").strip()
            if not car_brand_norm or not car_model_norm:
                fallback_brand, fallback_model = self._to_v2_car_submission_values(sd_data)
                if not car_brand_norm:
                    car_brand_norm = (fallback_brand or "").strip()
                if not car_model_norm:
                    car_model_norm = (fallback_model or "").strip()

            booking_id: Optional[str] = None
            if create_1c_booking and slot_date and slot_time:
                try:
                    from dialog.service_speech_parse import resolve_mileage_km_for_booking

                    resolved_mileage = resolve_mileage_km_for_booking(
                        mileage=sd_data.get("mileage"),
                        work_list=sd_data.get("work_list"),
                        car_year=sd_data.get("car_year"),
                    )
                    if resolved_mileage:
                        sd_data["mileage"] = resolved_mileage
                    if slot_start is None:
                        slot_start = datetime.strptime(
                            f"{slot_date} {slot_time}",
                            "%Y-%m-%d %H:%M",
                        )
                    from dialog.sto_to_price_inquiry import engine_gearbox_params_for_1c

                    eg_params = engine_gearbox_params_for_1c(sd_data)
                    booking_id = await create_1c_booking(
                        fio=fio_value or (self.state_machine.client_name or ""),
                        phone=phone_value or "",
                        car_brand=car_brand_value,
                        car_model=car_model_value,
                        car_year=sd_data.get("car_year") or "",
                        car_mileage=resolved_mileage or "",
                        work_wishes=sd_data.get("work_list") or "",
                        slot_start=slot_start,
                        post_id=str(sd_data.get("proposed_post") or ""),
                        acceptor_id=str(sd_data.get("proposed_acceptor_id") or ""),
                        duration_min=sd_data.get("proposed_duration_min"),
                        transmission=eg_params["transmission"],
                        engine_volume=eg_params["engine_volume"],
                        drive=eg_params["drive"],
                    )
                    logger.info(
                        "SERVICE booking 1C result: booking_id=%s slot=%s %s",
                        booking_id, slot_date, slot_time,
                    )
                except Exception:
                    logger.exception("SERVICE booking 1C create failed")

            voice_to_booking_trace(
                "booking_1c",
                booking_id=(str(booking_id)[:64] if booking_id else None),
                slot_date=slot_date,
                slot_time=slot_time,
                success=bool(booking_id),
                pending_1c=bool(booking_id and str(booking_id).startswith("local_")),
            )

            if booking_id:
                sd_data["service_1c_booking_id"] = booking_id
                notify_slot_start = slot_start
                if notify_slot_start is None and slot_date and slot_time:
                    notify_slot_start = datetime.strptime(
                        f"{slot_date} {slot_time}",
                        "%Y-%m-%d %H:%M",
                    )
                if notify_slot_start:
                    phone_raw = (sd_data.get("phone_raw") or "").strip()
                    phone_for_notify = (phone_value or "").strip() or phone_raw
                    # MAX-уведомление отправляем после завершения сессии:
                    # так в note попадет финальная цена, если ее озвучат уже после записи.
                    sd_data["max_booking_notify_payload"] = {
                        "fio": fio_value or (self.state_machine.client_name or ""),
                        "phone": phone_for_notify,
                        "phone_named_by_client": (phone_raw or sd_data.get("phone") or ""),
                        "car_brand": car_brand_norm,
                        "car_model": car_model_norm,
                        "car_brand_raw": car_brand_value,
                        "car_model_raw": car_model_value,
                        "car_brand_norm": car_brand_norm,
                        "car_model_norm": car_model_norm,
                        "car_year": sd_data.get("car_year") or "",
                        "mileage_norm": sd_data.get("mileage") or "",
                        "mileage_raw": sd_data.get("mileage_raw") or "",
                        "work_wishes": sd_data.get("work_list") or "",
                        "slot_start_iso": notify_slot_start.isoformat(),
                        "booking_id": str(booking_id),
                        "mechanic_name": (sd_data.get("proposed_mechanic_name") or ""),
                    }
                    sd_data["max_booking_notify_sent"] = False
                dt = datetime.strptime(slot_date, "%Y-%m-%d")
                day_txt = day_to_ordinal_ru(dt.day)
                booked_text = (
                    f"Вы записаны на {day_txt} {MONTHS_TTS_GENITIVE[dt.month]} на "
                    f"{_slot_time_hhmm_to_tts(slot_time)}. "
                    f"{TO_V2_BOOKED_CALLBACK_NOTICE}. "
                    f"Спасибо, есть ли у Вас еще вопросы?"
                )
                await self.play_wav_or_tts(None, booked_text)
                self.state_machine.transition_to(ConversationState.SERVICE_BOOKED)
                voice_to_booking_trace(
                    "to_service_booked",
                    service_1c_booking_id=str(booking_id)[:64],
                    pending_1c=bool(str(booking_id).startswith("local_")),
                )
            else:
                await self._transfer_to_service_on_network_error()
            sd_data["slot_unclear_attempts"] = 0

        async def _slot_confirm_unclear_response(
            *,
            allow_auto_book: bool = True,
        ) -> bool:
            """Ожидание «да» на предложенный слот: 1 переспрос; автозапись только при тишине."""
            if not (
                sd_data.get("slot_await_confirm")
                and sd_data.get("proposed_date")
                and sd_data.get("proposed_time")
                and not sd_data.get("slot_await_alt_choice")
            ):
                return False
            from dialog.service_speech_parse import looks_like_slot_date_attempt

            if sd_data.get("nearest_after_unrecognized_date"):
                sd_data["nearest_after_unrecognized_date"] = False
                await self._transfer_service_assistant_slot_selection(
                    reason="nearest_after_unrecognized_date_unclear",
                )
                return True
            if raw_st and looks_like_slot_date_attempt(raw_st):
                sd_data["slot_confirm_reprompts"] = 0
                sd_data["slot_await_confirm"] = False
                return False
            reps = int(sd_data.get("slot_confirm_reprompts") or 0) + 1
            sd_data["slot_confirm_reprompts"] = reps
            if reps >= 2 and allow_auto_book:
                await _book_proposed_slot()
                return True
            await self.play_wav_or_tts(
                None,
                _format_confirm_reprompt_with_slot(),
            )
            return True

        async def _offer_nearest_slot_fallback() -> None:
            """Ближайший слот в 1С, если клиент не назвал другую дату после «нет на этот день»."""
            sd_data["slot_await_new_date"] = False
            sd_data["slot_silence_attempts"] = 0
            base_date = sd_data.get("desired_date") or dealer_local_now_naive().date().isoformat()
            slot_tuple = await self._find_slot_from_1c(
                desired_date=base_date,
                work_list=sd_data.get("work_list", ""),
                day_part=self.service_day_part,
            )
            if slot_tuple == "no_slots":
                await self._transfer_to_service_no_slots()
                return
            if not slot_tuple:
                await self._transfer_to_service_on_network_error()
                return
            slot_date, slot_time, slot_post, slot_acceptor, slot_start_iso = slot_tuple  # type: ignore[misc]
            sd_data["proposed_post"] = slot_post
            sd_data["proposed_acceptor_id"] = slot_acceptor
            sd_data["proposed_slot_start_iso"] = slot_start_iso
            prefix = None
            if base_date and slot_date != base_date:
                try:
                    req_dt = datetime.strptime(base_date, "%Y-%m-%d")
                    prefix = (
                        f"На {day_to_ordinal_ru(req_dt.day)} "
                        f"{MONTHS_TTS_GENITIVE[req_dt.month]} свободных мест нет."
                    )
                except ValueError:
                    prefix = None
            await _say_slot_offer(
                PickedSlot(slot_date, slot_time, slot_post, slot_start_iso, slot_acceptor),
                prefix=prefix,
            )

        async def _offer_slots_around(requested_date_str: str) -> bool:
            """Сообщить о ближайших слотах до/после запрошенной даты. True — озвучено."""
            return await self._announce_slots_around_date(requested_date_str)

        async def _apply_slot_alt_choice(which: str) -> bool:
            key = "slot_alt_before" if which == "before" else "slot_alt_after"
            picked = sd_entry_to_picked(sd_data.get(key))
            if not picked:
                return False
            sd_data["slot_await_alt_choice"] = False
            await _say_slot_offer(picked)
            return True

        async def _handle_slot_alt_choice() -> bool:
            if not sd_data.get("slot_await_alt_choice"):
                return False
            before_entry = sd_data.get("slot_alt_before")
            after_entry = sd_data.get("slot_alt_after")
            parsed_dates: list[datetime] = []
            # Приоритет даты над маркерами «раньше/позже»: «тридцать первое»
            # должно трактоваться как календарная дата, а не «первый вариант».
            if self.date_parser:
                parsed_dates = self.date_parser.parse_dates(
                    text,
                    slot_day9_hint=slot_day9_hint,
                    slot_day10_hint=slot_day10_hint,
                )
                for parsed_dt in parsed_dates:
                    which = pick_alt_from_parsed_date(
                        parsed_dt.date(),
                        before_entry,
                        after_entry,
                    )
                    if which:
                        explicit_time = _extract_slot_time_from_text(text)
                        if explicit_time:
                            snapped_time = snap_hhmm_to_step(explicit_time)
                            hh, mm = map(int, snapped_time.split(":")[:2])
                            req_date = parsed_dt.date()
                            dp = self._extract_day_part(text) or self.service_day_part
                            if dp:
                                self.service_day_part = dp
                            after_dt = datetime.combine(
                                req_date,
                                datetime.min.time(),
                            ).replace(hour=hh, minute=mm)
                            return await _pick_and_offer(
                                req_date.strftime("%Y-%m-%d"),
                                day_part=dp,
                                after_dt=after_dt,
                                allow_evening=dp == "after_lunch",
                                requested_time_hhmm=snapped_time,
                            )
                        return await _apply_slot_alt_choice(which)
            # Если клиент назвал конкретную дату, но она не равна ни одному из
            # альтернативных вариантов, не трактуем «позже/раньше» как выбор
            # альтернативы — ниже сработает обычная обработка даты.
            if parsed_dates:
                return False
            if is_slot_alt_before_stt(t_low):
                return await _apply_slot_alt_choice("before")
            if is_slot_alt_after_stt(t_low):
                return await _apply_slot_alt_choice("after")
            if _voice_slot_booking_confirm_stt(t_low):
                if before_entry and not after_entry:
                    return await _apply_slot_alt_choice("before")
                if after_entry and not before_entry:
                    return await _apply_slot_alt_choice("after")
                # Два варианта — просим выбрать явно.
                await self.play_wav_or_tts(
                    None,
                    "Скажите «раньше» или «позже», или назовите дату.",
                )
                return True
            return False

        async def _offer_picked(
            picked: Optional[PickedSlot],
            *,
            requested_date_str: Optional[str] = None,
            requested_time_hhmm: Optional[str] = None,
        ) -> bool:
            if picked:
                sd_data["slot_await_alt_choice"] = False
                prefix: Optional[str] = None
                if requested_date_str and requested_time_hhmm:
                    picked_date = picked.date_str
                    picked_time = picked.time_str
                    if picked.start_iso:
                        try:
                            picked_dt = datetime.fromisoformat(picked.start_iso)
                            picked_date = picked_dt.strftime("%Y-%m-%d")
                            picked_time = picked_dt.strftime("%H:%M")
                        except (TypeError, ValueError):
                            pass
                    if picked_date == requested_date_str and picked_time != requested_time_hhmm:
                        try:
                            req_dt = datetime.strptime(requested_date_str, "%Y-%m-%d")
                            prefix = (
                                f"На {day_to_ordinal_ru(req_dt.day)} "
                                f"{MONTHS_TTS_GENITIVE[req_dt.month]} "
                                f"в {_slot_time_hhmm_to_tts(requested_time_hhmm)} свободных мест нет."
                            )
                        except ValueError:
                            prefix = None
                await _say_slot_offer(picked, prefix=prefix)
                return True
            if requested_date_str:
                if await _offer_slots_around(requested_date_str):
                    return True
            enter_slot_await_new_date(sd_data)
            sd_data["slot_await_alt_choice"] = False
            if requested_date_str:
                requested_dt = datetime.strptime(requested_date_str, "%Y-%m-%d")
                no_slots_msg = (
                    f"На {day_to_ordinal_ru(requested_dt.day)} "
                    f"{MONTHS_TTS_GENITIVE[requested_dt.month]} свободных слотов нет. "
                    "Назовите другую дату, пожалуйста."
                )
            else:
                no_slots_msg = SLOT_NO_SLOTS_ON_DATE_MSG
            await self.play_wav_or_tts(None, no_slots_msg)
            return True

        async def _pick_and_offer(
            date_str: str,
            *,
            day_part: Optional[str] = None,
            after_dt: Optional[datetime] = None,
            exclude_times: Optional[set[str]] = None,
            allow_evening: bool = False,
            requested_time_hhmm: Optional[str] = None,
        ) -> bool:
            sd_data["last_slot_request_date"] = date_str
            duration = self._service_booking_duration_min()
            picked = await pick_slot_for_day(
                _date.fromisoformat(date_str),
                duration,
                day_part=day_part,
                after_dt=after_dt,
                exclude_times=exclude_times,
                allow_evening_after_lunch=allow_evening,
            )
            return await _offer_picked(
                picked,
                requested_date_str=date_str,
                requested_time_hhmm=requested_time_hhmm,
            )

        raw_st = (text or "").strip()
        if not raw_st or is_meaningless_voice_stt(text):
            if await _slot_confirm_unclear_response():
                return
            if sd_data.get("slot_await_new_date"):
                sd_data["slot_silence_attempts"] = sd_data.get("slot_silence_attempts", 0) + 1
                if sd_data["slot_silence_attempts"] < 2:
                    return
                await _offer_nearest_slot_fallback()
                return
            sd_data["slot_silence_attempts"] = sd_data.get("slot_silence_attempts", 0) + 1
            if sd_data["slot_silence_attempts"] >= 2:
                if self._is_non_working_hours():
                    await self._do_after_hours_closure(ClientNeed.SERVICE)
                else:
                    await self._transfer_service_time_clarify("time_clarify_slot_silence")
                return
            if sd_data.get("proposed_date") and sd_data.get("proposed_time"):
                await _play_slot_reprompt()
            else:
                await self.play_wav_or_tts(None, "Повторите, пожалуйста, удобное время.")
                return

        noise_phrases = [
            "подписывайтесь", "канал", "редактор субтитров", "спасибо за просмотр",
            "смотрите видео", "продолжение следует",
        ]
        if any(p in t_low for p in noise_phrases):
            if sd_data.get("proposed_date") and sd_data.get("proposed_time"):
                await _play_slot_reprompt()
            else:
                await self.play_wav_or_tts(None, "Повторите, пожалуйста, удобное время.")
            return

        if self.date_parser:
            past_date = self.date_parser.parse_explicit_past_date(
                text,
                dealer_local_now_naive(),
            )
            if past_date:
                sd_data["desired_date"] = None
                sd_data["desired_time"] = None
                sd_data["nearest_after_unrecognized_date"] = False
                enter_slot_await_new_date(sd_data)
                await self.play_wav_or_tts(
                    None,
                    "Эта дата уже прошла. Назовите, пожалуйста, другую дату и время.",
                )
                return

        if await _handle_slot_alt_choice():
            return

        explicit_time_for_confirm = _extract_slot_time_from_text(text)
        if sd_data.get("slot_await_confirm") and _voice_slot_booking_confirm_stt(t_low):
            has_alt_marker = any(
                w in t_low for w in ("позже", "попозже", "позднее", "дальше", "лучше", "вместо")
            )
            has_reject_marker = _voice_slot_not_suitable_stt(t_low) or _voice_slot_plain_no_stt(t_low)
            # На этапе подтверждения «записывай/да» фиксируем последний предложенный слот.
            # Это защищает от STT-ошибок во времени внутри подтверждающей фразы.
            if not has_alt_marker and not has_reject_marker:
                proposed_time = str(sd_data.get("proposed_time") or "").strip()
                if explicit_time_for_confirm and proposed_time:
                    snapped_time = snap_hhmm_to_step(explicit_time_for_confirm)
                    # «Давайте лучше 15:30» — изменение времени, не финальное подтверждение.
                    if snapped_time != proposed_time:
                        await _book_proposed_slot()
                        return
                    else:
                        await _book_proposed_slot()
                        return
                else:
                    await _book_proposed_slot()
                    return

        if _is_within_week_request(t_low):
            sd_data["within_week_slot"] = True
            sd_data["until_week_end_slot"] = False
            sd_data["asap_slot"] = False
            sd_data["date_time_confirmed"] = True
            await self._process_service_booking()
            return

        if _is_until_end_of_week_request(t_low):
            sd_data["until_week_end_slot"] = True
            sd_data["within_week_slot"] = False
            sd_data["asap_slot"] = False
            sd_data["date_time_confirmed"] = True
            await self._process_service_booking()
            return

        weekday_date = resolve_weekday_booking_date(text, dealer_local_now_naive())
        if weekday_date:
            date_str = weekday_date.strftime("%Y-%m-%d")
            sd_data["desired_date"] = date_str
            dp = self._extract_day_part(text) or self.service_day_part
            if dp:
                self.service_day_part = dp
            ext_time = _extract_slot_time_from_text(text)
            if _looks_like_slot_time_attempt(text) and not ext_time:
                sd_data["proposed_date"] = None
                sd_data["proposed_time"] = None
                sd_data["proposed_post"] = None
                sd_data["proposed_acceptor_id"] = None
                sd_data["proposed_mechanic_name"] = None
                sd_data["proposed_slot_start_iso"] = None
                sd_data["slot_await_confirm"] = False
                sd_data["slot_confirm_reprompts"] = 0
                await self.play_wav_or_tts(
                    None,
                    "Не удалось распознать время. Повторите, пожалуйста, желаемое время.",
                )
                return
            after_dt = None
            if ext_time:
                ext_time = snap_hhmm_to_step(ext_time)
                hh, mm = map(int, ext_time.split(":")[:2])
                after_dt = datetime.combine(weekday_date, datetime.min.time()).replace(
                    hour=hh,
                    minute=mm,
                )
            if await _pick_and_offer(
                date_str,
                day_part=dp,
                after_dt=after_dt,
                requested_time_hhmm=ext_time,
            ):
                return

        if self.date_parser:
            from dialog.service_speech_parse import looks_like_slot_date_attempt

            slot_date_attempt = looks_like_slot_date_attempt(text)
            time_fix_on_confirm = bool(
                sd_data.get("slot_await_confirm")
                and sd_data.get("proposed_date")
                and _extract_slot_time_from_text(text)
                and (_voice_slot_not_suitable_stt(t_low) or _voice_slot_plain_no_stt(t_low))
            )
            # На подтверждении слота фраза «нет, в двенадцать» — это корректировка
            # времени, а не попытка назвать календарную дату.
            if slot_date_attempt and time_fix_on_confirm:
                slot_date_attempt = False
            requested_dates = self.date_parser.parse_dates(
                text,
                slot_day9_hint=slot_day9_hint,
                slot_day10_hint=slot_day10_hint,
            )
            explicit_month_in_reply = bool(
                re.search(
                    r"\b(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|"
                    r"сентябр|октябр|ноябр|декабр)\w*\b",
                    text,
                    re.IGNORECASE,
                )
            )
            if time_fix_on_confirm and not explicit_month_in_reply:
                requested_dates = []
            if (
                not requested_dates
                and sd_data.get("proposed_date")
                and not explicit_month_in_reply
            ):
                from datetime import date as _date_cls

                months_gen = [
                    "", "января", "февраля", "марта", "апреля", "мая",
                    "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря",
                ]
                proposed_dt = _date_cls.fromisoformat(sd_data["proposed_date"])
                month_hint = months_gen[proposed_dt.month]
                requested_dates = self.date_parser.parse_dates(
                    f"{text} {month_hint}",
                    slot_day9_hint=slot_day9_hint,
                    slot_day10_hint=slot_day10_hint,
                )
            if requested_dates:
                sd_data["nearest_after_unrecognized_date"] = False
                sd_data["slot_await_new_date"] = False
                # Если клиент называет ту же дату+время, что уже предложил бот,
                # считаем это подтверждением и сразу бронируем без лишнего переспроса.
                if (
                    sd_data.get("slot_await_confirm")
                    and sd_data.get("proposed_date")
                    and sd_data.get("proposed_time")
                ):
                    proposed_date = str(sd_data.get("proposed_date") or "")
                    proposed_time = str(sd_data.get("proposed_time") or "")
                    ext_time_same = _extract_slot_time_from_text(text)
                    if ext_time_same:
                        snapped_same = snap_hhmm_to_step(ext_time_same)
                        same_date = any(
                            d.strftime("%Y-%m-%d") == proposed_date
                            for d in requested_dates
                        )
                        if same_date and snapped_same == proposed_time:
                            await _book_proposed_slot()
                            return
                proposed = sd_data.get("proposed_date")
                if proposed and len(requested_dates) > 1:
                    requested_dates = sorted(
                        requested_dates,
                        key=lambda d: d.strftime("%Y-%m-%d") == proposed,
                    )
                dp = self._extract_day_part(text) or self.service_day_part
                if dp:
                    self.service_day_part = dp
                ext_time = _extract_slot_time_from_text(text)
                if _looks_like_slot_time_attempt(text) and not ext_time:
                    sd_data["desired_date"] = requested_dates[0].strftime("%Y-%m-%d")
                    sd_data["proposed_date"] = None
                    sd_data["proposed_time"] = None
                    sd_data["proposed_post"] = None
                    sd_data["proposed_acceptor_id"] = None
                    sd_data["proposed_mechanic_name"] = None
                    sd_data["proposed_slot_start_iso"] = None
                    sd_data["slot_await_confirm"] = False
                    sd_data["slot_confirm_reprompts"] = 0
                    await self.play_wav_or_tts(
                        None,
                        "Не удалось распознать время. Повторите, пожалуйста, желаемое время.",
                    )
                    return
                after_dt = None
                if ext_time:
                    ext_time = snap_hhmm_to_step(ext_time)
                    base_d = requested_dates[0]
                    hh, mm = map(int, ext_time.split(":")[:2])
                    after_dt = datetime.combine(base_d, datetime.min.time()).replace(
                        hour=hh, minute=mm,
                    )
                for parsed_dt in requested_dates:
                    date_str = parsed_dt.strftime("%Y-%m-%d")
                    sd_data["desired_date"] = date_str
                    if await _pick_and_offer(
                        date_str,
                        day_part=dp,
                        after_dt=after_dt if parsed_dt == requested_dates[0] else None,
                        allow_evening=dp == "after_lunch",
                        requested_time_hhmm=ext_time if parsed_dt == requested_dates[0] else None,
                    ):
                        return
                return
            if slot_date_attempt:
                await self.play_wav_or_tts(
                    None,
                    "Не удалось распознать дату. Повторите, пожалуйста, желаемую дату и время.",
                )
                return

        if _looks_like_slot_time_attempt(text) and not _extract_slot_time_from_text(text):
            sd_data["proposed_date"] = None
            sd_data["proposed_time"] = None
            sd_data["proposed_post"] = None
            sd_data["proposed_acceptor_id"] = None
            sd_data["proposed_mechanic_name"] = None
            sd_data["proposed_slot_start_iso"] = None
            sd_data["slot_await_confirm"] = False
            sd_data["slot_confirm_reprompts"] = 0
            await self.play_wav_or_tts(
                None,
                "Не удалось распознать время. Повторите, пожалуйста, желаемое время.",
            )
            return

        if _is_asap_slot_request(t_low):
            _set_asap_desired_date(sd_data)
            slot_tuple = await self._find_slot_from_1c(
                desired_date=sd_data["desired_date"],
                work_list=sd_data.get("work_list", ""),
                day_part=self.service_day_part,
            )
            if slot_tuple == "no_slots":
                await self._transfer_to_service_no_slots()
                return
            if not slot_tuple:
                await self._transfer_to_service_on_network_error()
                return
            slot_date, slot_time, slot_post, slot_acceptor, slot_start_iso = slot_tuple  # type: ignore[misc]
            sd_data["proposed_post"] = slot_post
            sd_data["proposed_acceptor_id"] = slot_acceptor
            sd_data["proposed_slot_start_iso"] = slot_start_iso
            await _say_slot_offer(
                PickedSlot(slot_date, slot_time, slot_post, slot_start_iso, slot_acceptor),
            )
            return

        explicit_time = _extract_slot_time_from_text(text)
        if explicit_time:
            snapped_explicit_time = snap_hhmm_to_step(explicit_time)
            proposed_time = sd_data.get("proposed_time")
            has_reject_marker = _voice_slot_not_suitable_stt(t_low) or _voice_slot_plain_no_stt(t_low)
            if (
                sd_data.get("slot_await_confirm")
                and proposed_time
                and snapped_explicit_time == proposed_time
                and _voice_slot_booking_confirm_stt(t_low)
            ):
                await _book_proposed_slot()
                return
            # «Нет, в двенадцать» на этапе подтверждения = коррекция времени
            # в уже предложенной дате; не тянем старый day_part (утро/обед),
            # чтобы не отрезать подходящие слоты по новому часу.
            if sd_data.get("slot_await_confirm") and has_reject_marker:
                dp = self._extract_day_part(text)
            else:
                dp = self._extract_day_part(text) or self.service_day_part
            if dp:
                self.service_day_part = dp
            target_day_part = (
                dp if (sd_data.get("slot_await_confirm") and has_reject_marker) else self.service_day_part
            )
            base_date = sd_data.get("proposed_date") or sd_data.get("desired_date")
            if base_date:
                hh, mm = map(int, snapped_explicit_time.split(":")[:2])
                after_dt = datetime.combine(
                    _date.fromisoformat(base_date),
                    datetime.min.time(),
                ).replace(hour=hh, minute=mm)
                if await _pick_and_offer(
                    base_date,
                    day_part=target_day_part,
                    after_dt=after_dt,
                    allow_evening=target_day_part == "after_lunch",
                    requested_time_hhmm=snapped_explicit_time,
                ):
                    return

        day_part = self._extract_day_part(text)
        if day_part:
            self.service_day_part = day_part
            base_date = sd_data.get("proposed_date") or sd_data.get("desired_date")
            if base_date and await _pick_and_offer(
                base_date,
                day_part=day_part,
                allow_evening=day_part == "after_lunch",
            ):
                return

            if any(w in t_low for w in ["позже", "попозже", "позднее"]):
                sd_data["nearest_after_unrecognized_date"] = False
                base_time = sd_data.get("proposed_time") or "10:00"
                base_date = sd_data.get("proposed_date") or sd_data.get("desired_date")
                if base_date:
                    after_dt = None
                    try:
                        h0, m0 = map(int, base_time.split(":")[:2])
                        after_dt = datetime.combine(
                            _date.fromisoformat(base_date),
                            datetime.min.time(),
                        ).replace(hour=h0, minute=m0) + timedelta(hours=2)
                    except Exception:
                        after_dt = None
                picked = await pick_slot_for_day(
                    _date.fromisoformat(base_date),
                    self._service_booking_duration_min(),
                    day_part=self.service_day_part,
                    after_dt=after_dt,
                    exclude_times=_offered_times_set(),
                    allow_evening_after_lunch=True,
                )
                if picked:
                    sd_data["slot_await_alt_choice"] = False
                    await _say_slot_offer(picked)
                    return
                sd_data["slot_confirm_reprompts"] = 0
                sd_data["slot_await_confirm"] = False
                enter_slot_await_new_date(sd_data)
                await self.play_wav_or_tts(
                    None,
                    "На этот день позже записать не могу. "
                    "Назовите другое время или другую дату.",
                )
                return

        if any(w in t_low for w in ["дальше"]):
            base_time = sd_data.get("proposed_time") or "10:00"
            base_date = sd_data.get("proposed_date") or sd_data.get("desired_date")
            after_dt = None
            if base_date:
                try:
                    h0, m0 = map(int, base_time.split(":")[:2])
                    after_dt = datetime.combine(
                        _date.fromisoformat(base_date),
                        datetime.min.time(),
                    ).replace(hour=h0, minute=m0) + timedelta(hours=2)
                except Exception:
                    after_dt = None
                if await _pick_and_offer(
                    base_date,
                    day_part=self.service_day_part,
                    after_dt=after_dt,
                    exclude_times=_offered_times_set(),
                    allow_evening=self.service_day_part == "after_lunch",
                ):
                    return
                return

        if _voice_slot_not_suitable_stt(t_low):
            base_date = sd_data.get("proposed_date") or sd_data.get("desired_date")
            proposed_time = sd_data.get("proposed_time")
            if base_date and proposed_time:
                try:
                    h0, m0 = map(int, proposed_time.split(":")[:2])
                    after_dt = datetime.combine(
                        _date.fromisoformat(base_date),
                        datetime.min.time(),
                    ).replace(hour=h0, minute=m0) + timedelta(minutes=10)
                except Exception:
                    after_dt = None
                picked = await pick_slot_for_day(
                    _date.fromisoformat(base_date),
                    self._service_booking_duration_min(),
                    day_part=self.service_day_part,
                    after_dt=after_dt,
                    exclude_times=_offered_times_set(),
                    allow_evening_after_lunch=True,
                )
                if picked:
                    await _say_slot_offer(picked)
                    return
                only_time = _slot_time_hhmm_to_tts(proposed_time)
                await self.play_wav_or_tts(
                    None,
                    f"На эту дату свободно только время {only_time}. "
                    f"Записать или выберете другую дату?",
                )
                sd_data["slot_unclear_attempts"] = 0
                return

        if _voice_slot_plain_no_stt(t_low):
            if sd_data.get("nearest_after_unrecognized_date"):
                sd_data["nearest_after_unrecognized_date"] = False
                sd_data["desired_date"] = None
                sd_data["date_time_attempts"] = 0
                enter_slot_await_new_date(sd_data)
            await self.play_wav_or_tts(
                None,
                "Назовите желаемую дату и желаемое время.",
            )
            sd_data["slot_unclear_attempts"] = 0
            return

        if _voice_slot_booking_confirm_stt(t_low):
            await _book_proposed_slot()
            return

        if await _slot_confirm_unclear_response(allow_auto_book=False):
            return

        sd_data["slot_unclear_attempts"] = sd_data.get("slot_unclear_attempts", 0) + 1
        if sd_data.get("slot_await_new_date"):
            if sd_data["slot_unclear_attempts"] >= 2:
                await _offer_nearest_slot_fallback()
                return
            await self.play_wav_or_tts(None, "Назовите другую дату, пожалуйста.")
            return
        if sd_data["slot_unclear_attempts"] >= 2:
            last_req = sd_data.get("last_slot_request_date")
            if last_req and await _offer_slots_around(last_req):
                return
            if self._is_non_working_hours():
                await self._do_after_hours_closure(ClientNeed.SERVICE)
            else:
                await _offer_nearest_slot_fallback()
            return
        if sd_data.get("proposed_date") and sd_data.get("proposed_time"):
            await _play_slot_reprompt()
        else:
            await self.play_wav_or_tts(
                None,
                "Давайте выберем другое время. Утром, в обед, после обеда или вечером?",
            )

    # ------------------------------------------------------------------
    # SERVICE_BOOKED
    # ------------------------------------------------------------------

    async def _handle_service_booked_state(self, raw_text: str, text: str) -> None:
        t_low = text.lower()
        sd_data = self.state_machine.service_data

        if sd_data.get("to_v2_substate") == "price_offer_transfer":
            await self._handle_service_booked_price_transfer_offer(text)
            return

        if sd_data.get("to_v2_substate") == "price" and sd_data.get("price_post_booking"):
            await self._to_v2_price_block_continue(text)
            return

        if is_to_booking_price_inquiry(text):
            await self._handle_service_booked_price_inquiry(text)
            return

        sunday_admin_transfer_markers = (
            "диспетчер",
            "диспетчера",
            "ассистент сервиса",
            "ассистента сервиса",
            "ассистенту сервиса",
        )
        if _is_sunday_samara_now() and any(m in t_low for m in sunday_admin_transfer_markers):
            await self._play_transfer_announcement("Поняла, перевожу Вас на администратора.")
            await self._transfer_or_after_hours(
                "03_transfer_admin.wav",
                None,
                voice_admin_reason="client_request",
                skip_announcement=True,
            )
            return

        if not (raw_text or text or "").strip():
            empty_attempts = int(sd_data.get("service_booked_empty_attempts") or 0) + 1
            sd_data["service_booked_empty_attempts"] = empty_attempts
            if empty_attempts <= 1:
                await self.play_wav_or_tts(
                    None,
                    "Не расслышала, есть ли у Вас еще вопросы?",
                )
                return
            await self.play_wav_or_tts("09_goodbye.wav", None)
            self.state_machine.transition_to(ConversationState.ENDED)
            return

        sd_data["service_booked_empty_attempts"] = 0

        if (raw_text or text or "").strip():
            if await self._try_department_phase_from_client_speech(
                raw_text,
                text,
                log_prefix="SERVICE_BOOKED",
                allow_booking_reentry=False,
            ):
                return

        explicit_followup_interest = bool(
            re.search(r"\b(?:есть|хочу|нужно|интересует)\b[^.!?]{0,40}\bвопрос\w*\b", t_low)
            or re.search(r"\bвопрос\w*\b[^.!?]{0,40}\b(?:есть|по|про)\b", t_low)
        )
        closing_phrase = bool(
            re.search(r"\b(?:ну\s+)?(?:все|всё)\b", t_low)
            or re.search(r"\bна\s+этом\s+(?:все|всё)\b", t_low)
            or re.search(r"\b(?:больше\s+ничего|ничего\s+больше)\b", t_low)
        )
        no_questions = bool(
            re.search(r"\b(?:без\s+вопрос\w*|вопрос\w*\s+больше\s+нет)\b", t_low)
            or re.search(r"\b(?:нет|неа)\b[^.!?]{0,30}\bвопрос\w*\b", t_low)
            or re.search(r"\bвопрос\w*\b[^.!?]{0,30}\b(?:нет|неа)\b", t_low)
            or (closing_phrase and not explicit_followup_interest)
        )
        if no_questions:
            await self.play_wav_or_tts("09_goodbye.wav", None)
            self.state_machine.transition_to(ConversationState.ENDED)
            return

        if any(w in t_low for w in ["да", "есть", "вопрос", "хочу", "нужно", "интересует"]):
            if self.rag_system:
                res = self.rag_system.find_best_response(text)
                if res:
                    await self.play_wav_or_tts(Path(res[0]).name, None)
                    return
            if self._is_non_working_hours():
                await self._do_after_hours_closure(None)
            else:
                await self._transfer_or_after_hours(
                    "16_transfer_service_assistant.wav",
                    ClientNeed.SERVICE,
                )
        else:
            await self.play_wav_or_tts("09_goodbye.wav", None)
            self.state_machine.transition_to(ConversationState.ENDED)

    # ------------------------------------------------------------------
    # TRANSFERRING / DEFAULT
    # ------------------------------------------------------------------

    async def _handle_transferring_state(self, text: str) -> None:
        self.state_machine.transition_to(ConversationState.ENDED)

    async def _handle_default_state(self, text: str) -> None:
        if self.rag_system:
            res = self.rag_system.find_best_response(text)
            if res:
                await self.play_wav_or_tts(Path(res[0]).name, None)
                return
        await self.play_wav_or_tts(None, "Минутку, я Вас соединяю со специалистом.")

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    def _identify_need(self, text: str) -> Optional[ClientNeed]:
        t = text.lower()
        if stt_implies_parts_department(text):
            return ClientNeed.PARTS
        if stt_implies_insurance_admin_department(text):
            return None
        if stt_implies_used_cars_department(text):
            return ClientNeed.USED_CARS
        # Эхо формулировки меню / явный выбор отдела автомобилей с пробегом → перевод на 696157
        if re.search(r"отдел\w*\s+автомобил\w*\s+с\s+пробег", t):
            return ClientNeed.USED_CARS
        if re.search(r"трейд[\s-]+ин", t) or any(
            p in t
            for p in (
                "отдел авто с пробегом",
                "трейд-ин",
                "трейдин",
                "трейд ин",
                "троидин",
            )
        ):
            return ClientNeed.USED_CARS
        scores = {
            ClientNeed.BODY_REPAIR: 0, ClientNeed.SERVICE: 0,
            ClientNeed.USED_CARS: 0, ClientNeed.NEW_CARS_CHERY_TENET: 0,
            ClientNeed.PARTS: 0,
        }
        body_repair_keywords = [
            "покраска", "окраска", "покрасить", "окрасить", "кузов", "рихтовка", "рихтовать",
            "отрихтовать", "отрихтовка",
            "направление", "направлению", "направлении", "по направлению",
            "направление от страховой", "подбор краски", "краска", "повреждение",
            "дэтэпэ", "после аварии", "вытянуть кузов",
            "разбит", "бампер", "бампера", "капот", "двери",
            "крыло", "крышу", "крыша", "кузовной", "кузовной цех", "цех кузовного ремонта",
            "цех по покраске", "цех покраск",
            # ТЗ: малярный / малярным и т.п. → кузовной цех
            "маляр",
            *_BODY_SHOP_STT_MARKERS,
        ]
        service_verbs = [
            "поменять", "замена", "заменить", "установить", "поставить",
            "сделать", "починить", "произвести", "отрегулировать", "продиагностировать", "настроить",
            "на замену", "шиномонтаж", "найти неисправность",
            "подвеска", "замена масла", "замену масла", "заменить масло", "ремонт двигателя", "замена колодок",
            "обслужить", "обслуживание",
        ]
        service_keywords = [
            "сервис", "ремонт", "обслуживание", "техобслуживание", "шиномонтаж",
            "на диагностику", "подвеска",
            "починить", "починить машину", "починить автомобиль",
            "машину нужно сделать", "автомобиль нужно сделать",
            "гарантия", "гарантию", "гарантии", "гарантий", "гарантией",
            "слесарный", "слесарная", "слесарного",
            "слесарн",
            "слесар",
            "слесработ",
            "лесарный", "сарный",
            "слесарный цех", "цех слесарного ремонта", "слесарного ремонта",
            "диспетчер сервиса", "диспетчера сервиса", "ассистент сервиса", "ассистенту сервиса",
            *SERVICE_ASSISTANT_DIRECT_MARKERS,
            "техосмотр", "техосмотра", "техосмотру", "на техосмотр", "записаться на техосмотр",
            "сервис nissan", "сервис ниссан", "сервис нисан", "сервис нисссан",
            "nissan сервис", "ниссан сервис", "нисан сервис", "нисссан сервис",
            "nissan", "ниссан", "нисан", "нисссан", "nisssan",
            "обслужить", "тго",
            "мойка",
            "мойку",
            "мойке",
            "мойки",
            "автомойка",
            "автомойку",
            "автомойке",
            "автомойки",
            # синонимы для слесарного: поменять = заменить (отдельно «масло»/«фильтр» не добавляем — ложные срабатывания)
            "поменять", "заменить",
        ]

        has_service_verb = any(v in t for v in service_verbs)
        has_service_keyword = (
            _stt_implies_ac_service_request(t)
            or _stt_implies_wheel_alignment_service_request(t)
            or any(w in t for w in service_keywords)
            or re.search(r"\b(?:то|тио|тео|тэо|сто|эстэо)\b", t)
            or re.search(r"\b[tт]го\b", t)
            or re.search(r"\bсделать\s+[tт]го\b", t)
            or re.search(r"\bобслуживан\w*\s+автомобил", t)
            or re.search(r"\bобслуживан\w*\s+машин", t)
            or re.search(r"\bобслужи\w+\s+автомобил", t)
            or re.search(r"\bобслужи\w+\s+машин", t)
            or _RE_MASTER_FOR_SERVICE.search(t) is not None
        )
        has_buy = any(w in t for w in [
            "купить", "покупка", "хочу", "нужен", "нужно", "интересует", "приобрести", "с гарантией",
        ])
        has_sell = any(w in t for w in ["продать", "продажа", "выкуп", "сдать", "оценить", "выкупить"])
        has_new = any(w in t for w in ["новый", "новую", "новое", "новые"])
        used_markers = [
            "трейд-ин", "трейдин", "трейд ин", "троидин",
            "продать автомобиль", "выкуп", "с пробегом", "б/у", "подержанный",
            "бэу", "бэуш", "подержан", "бу", "подержаные", "подержанные",
            "не новые", "неновые", "бывший в употреблении",
            "выкупите автомобиль", "выкупите мое авто", "выкупите мой автомобиль",
            "купите у меня автомобиль",
        ]
        has_used = any(w in t for w in used_markers)
        has_car = bool(re.search(r"автомобил|авто|машин", t))
        has_chery_tenet = any(m in t for m in [
            "чери", "тене", "тенет", "тигго",
            "менеджера по продажам", "отдел продаж чери", "отдел продаж тенет",
            "новый тенет", "новый чери", "новый тигго", "новую чери", "новую тенет",
        ])

        buy_from_client_phrases = [
            "купите у меня", "купить у меня", "можете купить",
            "купите мой", "купить мой", "выкупаете", "выкупите",
        ]
        if any(phrase in t for phrase in buy_from_client_phrases) and has_car:
            return ClientNeed.USED_CARS
        if has_car and ("купите" in t or "купить" in t or "выкупите" in t or "выкупить" in t) and any(
            p in t for p in ["у меня", "мой", "моя", "мою", "мои"]
        ):
            return ClientNeed.USED_CARS
        if has_sell and has_car:
            return ClientNeed.USED_CARS

        if any(w in t for w in body_repair_keywords):
            scores[ClientNeed.BODY_REPAIR] += 5
        if has_service_keyword:
            scores[ClientNeed.SERVICE] += 3
        if has_service_verb:
            scores[ClientNeed.SERVICE] += 2
        if has_buy and has_car:
            if has_new:
                scores[ClientNeed.NEW_CARS_CHERY_TENET] += 6
                scores[ClientNeed.SERVICE] -= 2
            if has_used:
                scores[ClientNeed.USED_CARS] += 4
            if not has_new and not has_used:
                scores[ClientNeed.USED_CARS] += 2
        if "отдел продаж" in t or "дел продаж" in t:
            if has_used:
                scores[ClientNeed.USED_CARS] += 4
            if has_new or (not has_new and not has_used):
                scores[ClientNeed.NEW_CARS_CHERY_TENET] += 3
        if has_chery_tenet:
            scores[ClientNeed.NEW_CARS_CHERY_TENET] += 5
        if has_buy and has_car and not has_new and not has_used and has_chery_tenet:
            scores[ClientNeed.NEW_CARS_CHERY_TENET] += 4

        parts_keywords = [
            "запчасти", "запасных частей", "запчаст", "зачаст", "зачасти", "детали", "расходники",
            "отдел запасных частей", "отдел зап", "запчастей", "зачастей", "фильтра", "фильтры", "фильтр", "фильры",
            "колодки", "колодок", "свечи", "свечей",
        ]
        if any(w in t for w in parts_keywords):
            scores[ClientNeed.PARTS] += 5
        # замена на слесарке: поменять/заменить/замена + расходники — приоритет SERVICE над «просто запчасти»
        _want_replace = "замена" in t or "поменять" in t or "заменить" in t
        _consumable_for_service = any(
            x in t
            for x in (
                "масло", "фильтр", "фильтры", "фильтра", "фильры",
                "колод", "колодок", "свеч", "свечей",
            )
        )
        if _want_replace and _consumable_for_service:
            scores[ClientNeed.SERVICE] += 4
            scores[ClientNeed.PARTS] -= 3

        if "автомобил" in t and "с пробегом" in t:
            scores[ClientNeed.USED_CARS] += 6

        best_need = max(scores, key=scores.get)
        if scores[best_need] < 3:
            return None
        self._log(
            "_identify_need: text='%s' -> need=%s (scores=%s)",
            text[:60] if text else "",
            best_need.value,
            {k.value: v for k, v in scores.items() if v > 0},
        )
        return best_need

    async def _ask_about_departments(self) -> None:
        """ИнструкцияRAG: без обращения по имени. Одно полное перечисление отделов (22 в v2, 12 в legacy), затем только админ."""
        sm = self.state_machine
        scen = getattr(self, "voice_scenario", "legacy")
        if sm.department_prompts_shown >= 1:
            self._log(
                "_ask_about_departments[%s]: повтор меню отделов запрещён, fallback на администратора",
                scen,
            )
            if self._is_non_working_hours():
                if scen == "v2" and not (
                    (sm.service_data or {}).get("fio") or sm.client_name
                ):
                    await self._v2_after_hours_begin_name_flow(None)
                else:
                    await self._do_after_hours_closure(None)
                return
            await self._play_transfer_announcement("Перевожу Вас на администратора.")
            await self._transfer_or_after_hours(
                "03_transfer_admin.wav", None, voice_admin_reason="nlu_failed", skip_announcement=True
            )
            return
        sm.department_prompts_shown += 1
        if scen == "v2":
            text = DEPARTMENT_CLARIFY_V2_CHERY_TENET
            await self.play_wav_or_tts("22_department_clarify_v2.wav", text)
            return
        phrase = getattr(self, "get_department_choice_phrase", None)
        text = phrase() if callable(phrase) else DEPARTMENT_CHOICE_CHERY_TENET
        await self.play_wav_or_tts("12_ask_department.wav", text)

    async def _confirm_need(self, need: ClientNeed) -> None:
        """ИнструкцияRAG: без обращения по имени."""
        mapping = {
            ClientNeed.SERVICE: "м+астера-консультанта слесарного цеха",
            ClientNeed.USED_CARS: "отдел продажи автомобилей с пробегом",
            ClientNeed.BODY_REPAIR: "м+астера-консультанта кузовного цеха",
            ClientNeed.NEW_CARS_CHERY_TENET: "отдел продажи новых автомобилей Ч+ери и Т+энет",
            ClientNeed.PARTS: "отдел запасных частей",
        }
        n_text = mapping.get(need, "консультация")
        await self.play_wav_or_tts(None, f"Я правильно поняла, Вас интересует {n_text}?")

    async def _process_service_booking(self) -> None:
        sd_data = self.state_machine.service_data
        if sd_data.get("within_week_slot"):
            await self._offer_slot_within_next_7_days_range()
            return
        if sd_data.get("until_week_end_slot"):
            await self._offer_slot_until_end_of_week_range()
            return
        # «На этой неделе» / «сейчас» / другой ASAP-запрос — предложить
        # первый доступный слот начиная с текущего дня. Если до конца недели
        # слотов нет, тот же поиск вернёт ближайшее свободное время после неё.
        if sd_data.get("asap_slot"):
            await self._offer_nearest_slot_after_date_silence()
            return
        date_str = sd_data.get("desired_date", "2026-01-20")
        desired_time = sd_data.get("desired_time")
        voice_to_booking_trace(
            "process_service_booking",
            **summarize_service_data(sd_data, caller_phone=getattr(self, "caller_phone", None)),
        )
        # Сначала только запрошенный день; иначе — слоты до/после, без автозаписи на другой день.
        search_task = asyncio.create_task(
            self._find_slot_from_1c(
            desired_date=date_str,
            work_list=sd_data.get("work_list", ""),
            day_part=self.service_day_part,
            start_time=desired_time,
                same_day_only=True,
            )
        )
        await self.play_wav_or_tts("42_slots_search_filler.wav", SLOTS_SEARCH_FILLER)
        slot_tuple = await search_task
        if slot_tuple is None:
            await self._transfer_to_service_on_network_error()
            return
        if slot_tuple == "no_slots":
            if await self._announce_slots_around_date(date_str):
                self.state_machine.transition_to(ConversationState.SERVICE_SLOT_SELECTION)
                voice_to_booking_trace(
                    "to_service_slot_selection",
                    mode="around_requested_date",
                    requested_date=date_str,
                )
                return
            await self._transfer_to_service_no_slots()
            return

        slot_date, slot_time, slot_post, slot_acceptor, slot_start_iso = slot_tuple  # type: ignore[misc]
        slot_date, slot_time, slot_start_iso = _normalize_slot_offer_fields(
            slot_date,
            slot_time,
            slot_start_iso or f"{slot_date}T{slot_time}:00",
        )
        if not slot_date or not slot_time:
            await self._transfer_to_service_on_network_error()
            return
        sd_data["proposed_date"] = slot_date
        sd_data["proposed_time"] = slot_time
        sd_data["proposed_post"] = slot_post
        sd_data["proposed_acceptor_id"] = slot_acceptor
        sd_data["proposed_slot_start_iso"] = slot_start_iso or f"{slot_date}T{slot_time}:00"

        dt = datetime.strptime(slot_date, "%Y-%m-%d")
        day_txt = day_to_ordinal_ru(dt.day)
        await self.play_wav_or_tts(
            None,
            format_service_slot_offer_tts(
                day_txt, MONTHS_TTS_GENITIVE[dt.month], _slot_time_hhmm_to_tts(slot_time),
                slot_hhmm=slot_time,
            ),
        )
        sd_data["slot_await_confirm"] = True
        sd_data["slot_confirm_reprompts"] = 0
        sd_data["slot_unclear_attempts"] = 0
        sd_data["slot_silence_attempts"] = 0
        self.state_machine.transition_to(ConversationState.SERVICE_SLOT_SELECTION)
        voice_to_booking_trace(
            "to_service_slot_selection",
            proposed_date=slot_date,
            proposed_time=slot_time,
            proposed_post=slot_post,
            proposed_acceptor_id=slot_acceptor or None,
        )

    async def _offer_slot_within_next_7_days_range(self) -> None:
        """«В течение недели»: ищем слот от сегодня до +7 дней, иначе — ближайший после диапазона."""
        from datetime import date as _date

        from dialog.dealer_time import dealer_local_now_naive
        from dialog.service_slot_day_part import day_part_time_window

        sd_data = self.state_machine.service_data
        sd_data["within_week_slot"] = False
        sd_data["until_week_end_slot"] = False
        sd_data["asap_slot"] = False
        now = dealer_local_now_naive()
        today = now.date()
        week_horizon_end = today + timedelta(days=7)
        days_in_horizon = max((week_horizon_end - today).days + 1, 1)
        duration = self._service_booking_duration_min()
        time_window = day_part_time_window(self.service_day_part)

        voice_to_booking_trace(
            "slots_search_within_week",
            phase="request",
            from_date=today.isoformat(),
            to_date=week_horizon_end.isoformat(),
            duration_min=duration,
            day_part=self.service_day_part,
            time_window=list(time_window) if time_window else None,
        )
        await self.play_wav_or_tts(None, "Смотрю свободное время в течение ближайшей недели.")

        slot_in_horizon = None
        if find_nearest_slot_1c:
            slot_in_horizon, _in_priority = await find_nearest_slot_1c(
                preferred_date=today,
                slot_duration_min=duration,
                days_priority=days_in_horizon,
                time_window=time_window,
            )
        if slot_in_horizon and slot_in_horizon.start.date() <= week_horizon_end:
            rounded_start = _round_slot_start_for_offer(slot_in_horizon.start)
            slot_date = rounded_start.strftime("%Y-%m-%d")
            slot_time = rounded_start.strftime("%H:%M")
            sd_data["desired_date"] = slot_date
            sd_data["proposed_date"] = slot_date
            sd_data["proposed_time"] = slot_time
            sd_data["proposed_post"] = (slot_in_horizon.post_id or "").strip()
            sd_data["proposed_acceptor_id"] = (slot_in_horizon.acceptor_id or "").strip()
            sd_data["proposed_mechanic_name"] = (slot_in_horizon.mechanic_name or "").strip() or None
            sd_data["proposed_slot_start_iso"] = rounded_start.isoformat()
            dt = datetime.strptime(slot_date, "%Y-%m-%d")
            await self.play_wav_or_tts(
                None,
                format_service_slot_offer_tts(
                    day_to_ordinal_ru(dt.day),
                    MONTHS_TTS_GENITIVE[dt.month],
                    _slot_time_hhmm_to_tts(slot_time),
                    slot_hhmm=slot_time,
                ),
            )
            sd_data["slot_await_confirm"] = True
            sd_data["slot_confirm_reprompts"] = 0
            sd_data["slot_unclear_attempts"] = 0
            sd_data["slot_silence_attempts"] = 0
            sd_data["slot_await_alt_choice"] = False
            self.state_machine.transition_to(ConversationState.SERVICE_SLOT_SELECTION)
            voice_to_booking_trace(
                "to_service_slot_selection",
                mode="within_next_7_days_range_hit",
                proposed_date=slot_date,
                proposed_time=slot_time,
            )
            return

        after_horizon_date = week_horizon_end + timedelta(days=1)
        sd_data["desired_date"] = after_horizon_date.strftime("%Y-%m-%d")
        voice_to_booking_trace(
            "slots_search_within_week",
            phase="fallback",
            fallback_date=after_horizon_date.isoformat(),
        )
        await self.play_wav_or_tts(
            None,
            "В течение ближайшей недели свободного времени нет. Предлагаю ближайший свободный слот.",
        )
        await self._offer_nearest_slot_after_date_silence()

    async def _offer_slot_until_end_of_week_range(self) -> None:
        """«До конца недели»: ищем слот до воскресенья, иначе — ближайший после недели."""
        from datetime import date as _date

        from dialog.dealer_time import dealer_local_now_naive
        from dialog.service_slot_day_part import day_part_time_window

        sd_data = self.state_machine.service_data
        sd_data["until_week_end_slot"] = False
        sd_data["asap_slot"] = False
        now = dealer_local_now_naive()
        today = now.date()
        week_end = today + timedelta(days=(6 - today.weekday()))
        days_until_week_end = max((week_end - today).days + 1, 1)
        duration = self._service_booking_duration_min()
        time_window = day_part_time_window(self.service_day_part)

        voice_to_booking_trace(
            "slots_search_week_range",
            phase="request",
            from_date=today.isoformat(),
            to_date=week_end.isoformat(),
            duration_min=duration,
            day_part=self.service_day_part,
            time_window=list(time_window) if time_window else None,
        )
        await self.play_wav_or_tts(None, "Смотрю свободное время до конца недели.")

        slot_in_week = None
        if find_nearest_slot_1c:
            slot_in_week, _in_priority = await find_nearest_slot_1c(
                preferred_date=today,
                slot_duration_min=duration,
                days_priority=days_until_week_end,
                time_window=time_window,
            )
        if slot_in_week and slot_in_week.start.date() <= week_end:
            rounded_start = _round_slot_start_for_offer(slot_in_week.start)
            slot_date = rounded_start.strftime("%Y-%m-%d")
            slot_time = rounded_start.strftime("%H:%M")
            sd_data["desired_date"] = slot_date
            sd_data["proposed_date"] = slot_date
            sd_data["proposed_time"] = slot_time
            sd_data["proposed_post"] = (slot_in_week.post_id or "").strip()
            sd_data["proposed_acceptor_id"] = (slot_in_week.acceptor_id or "").strip()
            sd_data["proposed_mechanic_name"] = (slot_in_week.mechanic_name or "").strip() or None
            sd_data["proposed_slot_start_iso"] = rounded_start.isoformat()
            dt = datetime.strptime(slot_date, "%Y-%m-%d")
            await self.play_wav_or_tts(
                None,
                format_service_slot_offer_tts(
                    day_to_ordinal_ru(dt.day),
                    MONTHS_TTS_GENITIVE[dt.month],
                    _slot_time_hhmm_to_tts(slot_time),
                    slot_hhmm=slot_time,
                ),
            )
            sd_data["slot_await_confirm"] = True
            sd_data["slot_confirm_reprompts"] = 0
            sd_data["slot_unclear_attempts"] = 0
            sd_data["slot_silence_attempts"] = 0
            sd_data["slot_await_alt_choice"] = False
            self.state_machine.transition_to(ConversationState.SERVICE_SLOT_SELECTION)
            voice_to_booking_trace(
                "to_service_slot_selection",
                mode="until_week_end_range_hit",
                proposed_date=slot_date,
                proposed_time=slot_time,
            )
            return

        after_week_date = week_end + timedelta(days=1)
        sd_data["desired_date"] = after_week_date.strftime("%Y-%m-%d")
        slot_tuple = await self._find_slot_from_1c(
            desired_date=sd_data["desired_date"],
            work_list=sd_data.get("work_list", ""),
            day_part=self.service_day_part,
            same_day_only=False,
        )
        if slot_tuple == "no_slots":
            await self._transfer_to_service_no_slots()
            return
        if not slot_tuple:
            await self._transfer_to_service_on_network_error()
            return
        slot_date, slot_time, slot_post, slot_acceptor, slot_start_iso = slot_tuple  # type: ignore[misc]
        slot_date, slot_time, slot_start_iso = _normalize_slot_offer_fields(
            slot_date,
            slot_time,
            slot_start_iso or f"{slot_date}T{slot_time}:00",
        )
        if not slot_date or not slot_time:
            await self._transfer_to_service_on_network_error()
            return
        sd_data["proposed_date"] = slot_date
        sd_data["proposed_time"] = slot_time
        sd_data["proposed_post"] = slot_post
        sd_data["proposed_acceptor_id"] = slot_acceptor
        sd_data["proposed_slot_start_iso"] = slot_start_iso or f"{slot_date}T{slot_time}:00"
        dt = _date.fromisoformat(slot_date)
        await self.play_wav_or_tts(
            None,
            "До конца недели свободных слотов нет. "
            + format_service_slot_offer_tts(
                day_to_ordinal_ru(dt.day),
                MONTHS_TTS_GENITIVE[dt.month],
                _slot_time_hhmm_to_tts(slot_time),
                slot_hhmm=slot_time,
            ),
        )
        sd_data["slot_await_confirm"] = True
        sd_data["slot_confirm_reprompts"] = 0
        sd_data["slot_unclear_attempts"] = 0
        sd_data["slot_silence_attempts"] = 0
        sd_data["slot_await_alt_choice"] = False
        self.state_machine.transition_to(ConversationState.SERVICE_SLOT_SELECTION)
        voice_to_booking_trace(
            "to_service_slot_selection",
            mode="until_week_end_range_fallback_after_week",
            proposed_date=slot_date,
            proposed_time=slot_time,
        )

    async def _announce_slots_around_date(self, requested_date_str: str) -> bool:
        """Озвучить ближайшие слоты до/после даты. True — есть хотя бы один вариант."""
        from datetime import date as _date

        from dialog.service_slot_alternatives import (
            format_slots_around_date_tts,
            slot_info_to_sd_entry,
        )
        from dialog.service_slot_day_part import day_part_time_window
        from telegram_bot.services.service_booking_service import find_slots_around_date_1c

        sd_data = self.state_machine.service_data
        target = _date.fromisoformat(requested_date_str)
        requested_time = str(sd_data.get("desired_time") or "").strip() or None
        request_time_window = day_part_time_window(self.service_day_part)
        before_info, after_info = await find_slots_around_date_1c(
            target,
            slot_duration_min=self._service_booking_duration_min(),
            time_window=request_time_window,
            min_time_hhmm=requested_time,
        )
        if not before_info and not after_info:
            return False
        sd_data["last_slot_request_date"] = requested_date_str
        sd_data["slot_alt_before"] = (
            slot_info_to_sd_entry(before_info) if before_info else None
        )
        sd_data["slot_alt_after"] = (
            slot_info_to_sd_entry(after_info) if after_info else None
        )
        sd_data["slot_await_alt_choice"] = True
        sd_data["slot_await_confirm"] = False
        sd_data["slot_await_new_date"] = False
        sd_data["slot_unclear_attempts"] = 0
        sd_data["slot_silence_attempts"] = 0
        sd_data["proposed_date"] = None
        sd_data["proposed_time"] = None
        sd_data["proposed_post"] = None
        sd_data["proposed_acceptor_id"] = None
        sd_data["proposed_mechanic_name"] = None
        sd_data["proposed_slot_start_iso"] = None
        msg = format_slots_around_date_tts(
            target,
            before_date=before_info.start.date() if before_info else None,
            before_time=before_info.start.strftime("%H:%M") if before_info else None,
            after_date=after_info.start.date() if after_info else None,
            after_time=after_info.start.strftime("%H:%M") if after_info else None,
        )
        await self.play_wav_or_tts(None, msg)
        return True

    async def _transfer_service_assistant_slot_selection(self, *, reason: str) -> None:
        """Перевод на ассистента сервиса при выборе даты/слота (фраза «Поняла…»)."""
        voice_to_booking_trace("service_transfer", reason=reason)
        await self.play_wav_or_tts(None, TRANSFER_SERVICE_ASSISTANT_SLOT)
        await self._transfer_or_after_hours(
            "16_transfer_service_assistant.wav",
            ClientNeed.SERVICE,
            skip_announcement=True,
        )

    async def _transfer_to_service_on_network_error(self) -> None:
        voice_to_booking_trace("service_transfer", reason="network_error")
        await self.play_wav_or_tts(
            None,
            "Извините, ошибка в сети, перевожу Вас на ассистента сервиса.",
        )
        await self._transfer_or_after_hours(
            "16_transfer_service_assistant.wav",
            ClientNeed.SERVICE,
            skip_announcement=True,
        )

    async def _transfer_to_service_no_slots(self) -> None:
        """1С отдал пустой массив слотов — это не сетевая ошибка, формулировка должна
        отличаться, чтобы не вводить клиента в заблуждение."""
        voice_to_booking_trace("service_transfer", reason="no_slots")
        await self.play_wav_or_tts(
            None,
            "Извините, свободных слотов нет, перевожу Вас на ассистента.",
        )
        await self._transfer_or_after_hours(
            "16_transfer_service_assistant.wav",
            ClientNeed.SERVICE,
            skip_announcement=True,
        )

    async def _offer_nearest_slot_after_date_silence(
        self,
        *,
        after_unrecognized_date: bool = False,
    ) -> None:
        """Тишина/неясная дата: найти ближайший слот и предложить запись."""
        sd_data = self.state_machine.service_data
        if not sd_data.get("desired_date"):
            _set_asap_desired_date(sd_data)
        sd_data["asap_slot"] = True
        sd_data["date_time_confirmed"] = True
        voice_to_booking_trace(
            "nearest_slot_after_date_silence",
            desired_date=sd_data.get("desired_date"),
        )
        await self.play_wav_or_tts(None, "Смотрю ближайшее свободное время.")
        slot_tuple = await self._find_slot_from_1c(
            desired_date=sd_data.get("desired_date"),
            work_list=sd_data.get("work_list", ""),
            day_part=self.service_day_part,
            same_day_only=False,
        )
        if slot_tuple == "no_slots":
            await self._transfer_to_service_no_slots()
            return
        if not slot_tuple:
            await self._transfer_to_service_on_network_error()
            return
        slot_date, slot_time, slot_post, slot_acceptor, slot_start_iso = slot_tuple  # type: ignore[misc]
        slot_date, slot_time, slot_start_iso = _normalize_slot_offer_fields(
            slot_date,
            slot_time,
            slot_start_iso or f"{slot_date}T{slot_time}:00",
        )
        if not slot_date or not slot_time:
            await self._transfer_to_service_on_network_error()
            return
        sd_data["proposed_date"] = slot_date
        sd_data["proposed_time"] = slot_time
        sd_data["proposed_post"] = slot_post
        sd_data["proposed_acceptor_id"] = slot_acceptor
        sd_data["proposed_slot_start_iso"] = slot_start_iso or f"{slot_date}T{slot_time}:00"
        sd_data["slot_await_confirm"] = True
        sd_data["slot_await_alt_choice"] = False
        sd_data["slot_confirm_reprompts"] = 0
        sd_data["slot_unclear_attempts"] = 0
        sd_data["slot_silence_attempts"] = 0
        sd_data["nearest_after_unrecognized_date"] = after_unrecognized_date
        dt = datetime.strptime(slot_date, "%Y-%m-%d")
        from services.voice.voice_phrases import format_nearest_slot_offer_tts

        await self.play_wav_or_tts(
            None,
            format_nearest_slot_offer_tts(
                day_to_ordinal_ru(dt.day),
                MONTHS_TTS_GENITIVE[dt.month],
                _slot_time_hhmm_to_tts(slot_time),
                slot_hhmm=slot_time,
            ),
        )
        self.state_machine.transition_to(ConversationState.SERVICE_SLOT_SELECTION)
        voice_to_booking_trace(
            "to_service_slot_selection",
            mode="nearest_after_silence",
            proposed_date=slot_date,
            proposed_time=slot_time,
        )

    async def _offer_nearest_slot_in_month(self, year: int, month: int) -> None:
        """Предложить ближайший свободный слот в указанном месяце."""
        from datetime import date as _date
        from dialog.dealer_time import dealer_local_now_naive
        from dialog.service_slot_day_part import day_part_time_window

        sd_data = self.state_machine.service_data
        now = dealer_local_now_naive()
        target_start = _date(year, month, 1)
        if target_start.year == now.year and target_start.month == now.month:
            target_start = now.date()
        month_last_day = monthrange(year, month)[1]
        target_end = _date(year, month, month_last_day)
        if target_start > target_end:
            await self.play_wav_or_tts(
                None,
                "В этом месяце свободных слотов нет. Назовите, пожалуйста, другую дату.",
            )
            return
        duration = self._service_booking_duration_min()
        time_window = day_part_time_window(self.service_day_part)
        await self.play_wav_or_tts(None, f"Смотрю ближайшее свободное время в {MONTHS_TTS_GENITIVE[month]}.")
        if not find_nearest_slot_1c:
            await self._transfer_to_service_on_network_error()
            return
        days_priority = (target_end - target_start).days + 1
        slot, _in_priority = await find_nearest_slot_1c(
            preferred_date=target_start,
            slot_duration_min=duration,
            days_priority=days_priority,
            time_window=time_window,
        )
        if not slot or slot.start.year != year or slot.start.month != month:
            await self.play_wav_or_tts(
                None,
                f"В {MONTHS_TTS_GENITIVE[month]} свободных слотов нет. Назовите, пожалуйста, другую дату.",
            )
            return
        rounded_start = _round_slot_start_for_offer(slot.start)
        slot_date = rounded_start.strftime("%Y-%m-%d")
        slot_time = rounded_start.strftime("%H:%M")
        sd_data["desired_date"] = slot_date
        sd_data["proposed_date"] = slot_date
        sd_data["proposed_time"] = slot_time
        sd_data["proposed_post"] = (slot.post_id or "").strip()
        sd_data["proposed_acceptor_id"] = (slot.acceptor_id or "").strip()
        sd_data["proposed_mechanic_name"] = (slot.mechanic_name or "").strip() or None
        sd_data["proposed_slot_start_iso"] = rounded_start.isoformat()
        sd_data["slot_await_confirm"] = True
        sd_data["slot_await_alt_choice"] = False
        sd_data["slot_confirm_reprompts"] = 0
        sd_data["slot_unclear_attempts"] = 0
        sd_data["slot_silence_attempts"] = 0
        dt = datetime.strptime(slot_date, "%Y-%m-%d")
        await self.play_wav_or_tts(
            None,
            format_service_slot_offer_tts(
                day_to_ordinal_ru(dt.day),
                MONTHS_TTS_GENITIVE[dt.month],
                _slot_time_hhmm_to_tts(slot_time),
                slot_hhmm=slot_time,
            ),
        )
        self.state_machine.transition_to(ConversationState.SERVICE_SLOT_SELECTION)
        voice_to_booking_trace(
            "to_service_slot_selection",
            mode="nearest_in_month",
            proposed_date=slot_date,
            proposed_time=slot_time,
        )

    async def _transfer_service_time_clarify(self, reason: str) -> None:
        """Перевод на ассистента сервиса после неясных ответов о времени."""
        voice_to_booking_trace("service_transfer", reason=reason)
        await self.play_wav_or_tts(None, "Перевожу на специалиста для уточнения времени.")
        await self._transfer_or_after_hours(
            "16_transfer_service_assistant.wav",
            ClientNeed.SERVICE,
            skip_announcement=True,
        )

    def _service_booking_duration_min(self) -> int:
        try:
            from telegram_bot.services.service_booking_service import (
                DEFAULT_LABOR_MINUTES,
                LABOR_MINUTES_OPERATION_UNKNOWN,
            )

            labor_min = DEFAULT_LABOR_MINUTES
            if get_labor_and_cost:
                sd = self.state_machine.service_data or {}
                mileage_km = None
                raw_m = sd.get("mileage")
                if raw_m is not None:
                    try:
                        mileage_km = int(str(raw_m).replace(" ", ""))
                    except (TypeError, ValueError):
                        mileage_km = None
                labor_min, _cost = get_labor_and_cost(
                    sd.get("work_list") or "",
                    brand=sd.get("car_brand"),
                    model=sd.get("car_model"),
                    mileage_km=mileage_km,
                )
            duration = max(int(labor_min or DEFAULT_LABOR_MINUTES), DEFAULT_LABOR_MINUTES)
            if (self.state_machine.service_data or {}).get("operation_unknown"):
                duration = max(duration, LABOR_MINUTES_OPERATION_UNKNOWN)
            return duration
        except Exception:
            return 150

    async def _find_slot_from_1c(
        self,
        *,
        desired_date: Optional[str],
        work_list: str,
        day_part: Optional[str],
        start_time: Optional[str] = None,
        same_day_only: bool = False,
    ) -> "tuple[str, str, str, str, str] | str | None":
        """Возвращает:
          - tuple(date, time, post_id, acceptor_id, iso) — слот найден;
          - 'no_slots'                   — 1С ответил пустым массивом (всё ОК, но мест нет);
          - None                         — сетевая/программная ошибка (фраза «ошибка в сети»).
        """
        if not (find_nearest_slot_1c and desired_date):
            voice_to_booking_trace(
                "slots_search",
                result="skip",
                detail="no_find_nearest_slot_1c_or_no_date",
                desired_date=desired_date,
            )
            return None
        try:
            from datetime import date as _date
            from telegram_bot.services.service_booking_service import (
                DEFAULT_LABOR_MINUTES,
                LABOR_MINUTES_OPERATION_UNKNOWN,
            )

            duration = self._service_booking_duration_min()
            if (self.state_machine.service_data or {}).get("operation_unknown"):
                from telegram_bot.services.service_booking_service import (
                    LABOR_MINUTES_OPERATION_UNKNOWN,
                )

                duration = max(duration, LABOR_MINUTES_OPERATION_UNKNOWN)
                logger.info(
                    "SERVICE 1C: operation_unknown=True → duration=%d", duration,
                )
            preferred_date = _date.fromisoformat(desired_date)

            from dialog.service_slot_day_part import day_part_time_window

            time_window = day_part_time_window(day_part)

            after_dt = None
            if start_time:
                try:
                    from dialog.service_slot_time import snap_hhmm_to_step

                    start_time = snap_hhmm_to_step(str(start_time))
                    hh, mm = map(int, start_time.split(":")[:2])
                    after_dt = datetime.combine(preferred_date, datetime.min.time()).replace(
                        hour=hh, minute=mm
                    )
                except Exception:
                    after_dt = None

            voice_to_booking_trace(
                "slots_search",
                phase="request",
                desired_date=desired_date,
                duration_min=duration,
                day_part=day_part,
                start_time=start_time,
                time_window=list(time_window) if time_window else None,
                same_day_only=same_day_only,
            )

            days_first = 0 if same_day_only else 3
            slot, in_priority = await find_nearest_slot_1c(
                preferred_date=preferred_date,
                slot_duration_min=duration,
                days_priority=days_first,
                time_window=time_window,
                after_datetime=after_dt,
                raise_on_error=True,
            )
            if not slot and same_day_only:
                logger.info(
                    "SERVICE 1C: no slots on %s (same_day_only)",
                    preferred_date.isoformat(),
                )
            elif not slot and not same_day_only:
                logger.info(
                    "SERVICE 1C: no slots in 3-day window, retry 30-day for %s",
                    preferred_date.isoformat(),
                )
                slot, in_priority = await find_nearest_slot_1c(
                    preferred_date=preferred_date,
                    slot_duration_min=duration,
                    days_priority=30,
                    time_window=time_window,
                    after_datetime=after_dt,
                    raise_on_error=True,
                )
            if not slot:
                if same_day_only:
                    logger.info(
                        "SERVICE 1C: no slots on requested day %s",
                        preferred_date.isoformat(),
                    )
                else:
                    logger.info("SERVICE 1C: no slots in 30-day window either")
                voice_to_booking_trace(
                    "slots_search",
                    result="no_slots",
                    duration_min=duration,
                    preferred_date=str(preferred_date),
                )
                return "no_slots"
            rounded_start = _round_slot_start_for_offer(slot.start)
            slot_date = rounded_start.strftime("%Y-%m-%d")
            slot_time = rounded_start.strftime("%H:%M")
            post_id = (slot.post_id or "").strip()
            acceptor_id = (slot.acceptor_id or "").strip()
            mechanic_name = (slot.mechanic_name or "").strip()
            sd = self.state_machine.service_data
            if sd is not None:
                sd["proposed_duration_min"] = duration
                sd["proposed_mechanic_name"] = mechanic_name or None
            logger.info(
                "SERVICE slot from 1C HTTP: %s %s post_id=%s acceptor_id=%s mechanic=%s in_priority=%s duration=%d",
                slot_date, slot_time, post_id or "?", acceptor_id or "?", mechanic_name or "?", in_priority, duration,
            )
            voice_to_booking_trace(
                "slots_search",
                result="ok",
                slot_date=slot_date,
                slot_time=slot_time,
                proposed_post=post_id or None,
                proposed_acceptor_id=acceptor_id or None,
                proposed_mechanic_name=mechanic_name or None,
                in_priority=bool(in_priority),
                duration_min=duration,
            )
            return slot_date, slot_time, post_id, acceptor_id, rounded_start.isoformat()
        except Exception:
            logger.exception("SERVICE slot selection via 1C HTTP failed")
            voice_to_booking_trace("slots_search", result="error", detail="exception")
            return None

    def _extract_day_part(self, text: str) -> Optional[str]:
        from dialog.service_slot_day_part import extract_day_part

        return extract_day_part(text)

    def _process_client_from_db(self, client: dict, sd_data: dict) -> None:
        """Заполняет sd_data информацией о клиенте из базы."""

        def _normalize_phone(raw) -> Optional[str]:
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

        model_from_db = str(client.get("Модель", "")).replace("nan", "").strip()
        if model_from_db:
            car_info_extracted = self.car_brand_extractor.extract_car_info(model_from_db)
            if car_info_extracted:
                brand, model = car_info_extracted
                car_info = f"{brand} {model}" if model else brand
            else:
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
            brand_from_db = str(client.get("Марка", "")).replace("nan", "").strip()
            if brand_from_db:
                car_info = brand_from_db
                brand = self.car_brand_extractor.normalize_brand(brand_from_db)
                model = None
            else:
                car_raw = str(client.get("Автомобиль", "")).replace("nan", "")
                words = car_raw.split()
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
                    model = self.car_brand_extractor.normalize_model(tokens[1]) if len(tokens) > 1 else None
                else:
                    brand = None
                    model = None

        car_brand_value = brand if brand else (car_info.split()[0] if car_info else None)
        sd_data.update({
            "client_found_in_db": True,
            "car_model": car_info,
            "car_brand": car_brand_value,
        })

    def _client_first_name_for_tts(self) -> Optional[str]:
        """Возвращает имя клиента, которое можно подставлять в начало TTS‑фраз
        (формате «Роман, …»). None — если имя неизвестно или равно строковому "None".

        Источники по приоритету:
          1) state_machine.client_name (вычисленное name_extractor / set_name);
          2) sd_data["fio"] (взятое из реплики «Назовите ФИО»), берём первое слово.
        """
        name = (self.state_machine.client_name or "").strip()
        if name and name.lower() != "none":
            return name
        sd = self.state_machine.service_data or {}
        fio = (sd.get("fio") or "").strip()
        if fio:
            tokens = fio.split()
            if tokens:
                return tokens[0]
        return None

    def _format_car_for_tts(self, sd_data: dict) -> str:
        """Форматирует информацию об автомобиле для TTS."""
        brand = sd_data.get("car_brand")
        model_str = sd_data.get("car_model", "")
        if brand:
            model_only = model_str.replace(brand, "").strip() if brand in model_str else None
            return self.car_brand_extractor.format_for_tts(brand, model_only)
        brand_map = {
            "peugeot": "Пеж+о", "nissan": "Нисс+ан",
            "chery": "Ч+ери",
        }
        tokens = model_str.split()
        if tokens:
            mapped = brand_map.get(tokens[0].lower())
            if mapped:
                tokens[0] = mapped
            return " ".join(tokens)
        return model_str
