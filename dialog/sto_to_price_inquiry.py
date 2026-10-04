"""
Блок D (цена ТО по Excel) и маркеры сценария записи на ТО v2.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from dialog.department_stt_normalize import is_meaningless_voice_stt
from dialog.sto_to_summary_table import (
    filter_sto_to_candidates_by_spec,
    lookup_sto_to_regulation_with_filters,
    next_sto_to_disambiguation_field,
    parse_engine_gearbox_cell,
    _collect_sto_to_candidates,
)

_PRICE_MARKERS: tuple[str, ...] = (
    "сколько стоит",
    "какая цена",
    "какая стоимость",
    "стоимость",
    "цена",
    "цену",
    "узнать цену",
    "узнать стоимость",
    "почем",
    "почём",
    "сколько будет",
    "сколько заплатить",
    "сколько обойд",
    "сколько выйдет",
    "сколько заплат",
)

_TO_MARKERS_RE = re.compile(
    r"(\bто\b|\bто[-\s]?\d+\b|\bто-\d|\bтехобслуж|\bтехническ\w*\s+обслуж|\bобслужив\w*\s+авто)",
    re.IGNORECASE,
)

TO_V2_CONTINUE_MARKERS: tuple[str, ...] = (
    "дальше",
    "далее",
    "продолжай",
    "продолжаем",
    "продолжаем запись",
    "запиши",
    "записывай",
    "записывайте",
    "запишите",
    "записать",
    "запис",
    "пиши",
    "давай",
    "давайте",
    "сделай сама",
    "согласен",
    "согласна",
)

TO_V2_RECORD_MARKERS: tuple[str, ...] = (
    *TO_V2_CONTINUE_MARKERS,
    "записаться",
    "сделай",
    "сама",
    "робот",
    "помощник",
    "австралия",
)

# Тестовый вход в ветку записи: STT часто даёт падежи и обрезки («австрали», «Трали я.», «Сале.»).
_TO_BOOKING_TEST_TRIGGER_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(?:australia|австрали\w*)\b"),
    re.compile(r"(?i)\bавстрал\w*"),
    re.compile(r"(?i)(?:австрал|страл\w*|трал\w*)"),
    re.compile(r"(?i)рали\s+я"),
    re.compile(r"(?i)^сале\.?$"),
)


def _booking_test_trigger_on_stt_fragment(text: str) -> bool:
    raw = (text or "").strip()
    if not raw:
        return False
    for pat in _TO_BOOKING_TEST_TRIGGER_RES:
        if pat.search(raw):
            return True
    t_norm = re.sub(r"[^\w\s]+", " ", raw.lower())
    t_norm = re.sub(r"\s+", " ", t_norm).strip()
    for pat in _TO_BOOKING_TEST_TRIGGER_RES:
        if pat.search(t_norm):
            return True
    return "австралия" in t_norm


def is_to_booking_test_trigger_stt(text: str) -> bool:
    """Триггер «австралия» / australia для тестовой ветки записи на ТО."""
    return _booking_test_trigger_on_stt_fragment(text)


# STT после меню 14: обрезки и эхо («тама»→«сама», «пиши»→«запиши», «ама»→«сама»).
_TO_V2_RECORD_STT_SUBS: tuple[tuple[str, str], ...] = (
    (r"\bтама\b", "сама"),
    (r"\bтамо\b", "сама"),
    (r"\bама\b", "сама"),
    (r"\bпиши\b", "запиши"),
    (r"\bпишите\b", "запишите"),
    (r"\bавай\b", "давай"),
)


def normalize_to_v2_record_stt(text: str) -> str:
    t = re.sub(r"[^\w\s]+", " ", (text or "").lower(), flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    for pattern, repl in _TO_V2_RECORD_STT_SUBS:
        t = re.sub(pattern, repl, t)
    return t


TO_V2_SERVICE_ASSISTANT_MARKERS: tuple[str, ...] = (
    "диспетчер",
    "ассистент сервиса",
    "ассистент",
    "ассистента",
    "человек",
    "менеджер",
    "оператор",
    "живой",
    "переведите",
    "переведи",
    "преведи",
    "периведи",
    "переводите",
    "перевести",
    "переводи",
    "переключите",
    "переключи",
    "переключу",
    "приемщик",
    "приемщика",
    "приёмщик",
    "приёмщика",
    "приемка",
    "приёмка",
    "мастерприемщик",
    "мастерприёмщик",
    # STT-ошибки: «настер», «настерприемщик».
    "настер",
    "настерприемщик",
    "настерприёмщик",
    "специалист",
    "мастер",
    # «Консультация / консультант» в ответ на меню 14 = запрос живого сотрудника.
    "консульт",
)

_HUMAN_TRANSFER_TARGET_RE = re.compile(
    r"\b(?:"
    r"человек\w*|диспетчер\w*|ассистент\w*|оператор\w*|"
    r"менеджер\w*|приемщик\w*|приёмщик\w*|специалист\w*|"
    r"консультант\w*|"
    r"жив(?:ой|ого|ому)\b"
    r")\b",
    re.IGNORECASE,
)
_HUMAN_TRANSFER_VERB_RE = re.compile(
    r"\b(?:перевед\w+|переключ\w+|соедин\w+|дайте|дай)\b",
    re.IGNORECASE,
)


def _normalize_to_price_text(text: str) -> str:
    """Нормализация STT для темы цены ТО: «НТО»/«nто» -> «ТО»."""
    t = (text or "").lower()
    t = re.sub(r"\b[нn]\s*т\s*о\b", " то ", t, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", t).strip()


def is_to_price_inquiry(text: str) -> bool:
    t = _normalize_to_price_text(text)
    if not any(p in t for p in _PRICE_MARKERS):
        return False
    return bool(_TO_MARKERS_RE.search(t))


def normalize_to_booking_price_stt(text: str) -> str:
    """STT в блоке записи на ТО: «товар» → «то» и т.п."""
    t = (text or "").lower()
    t = re.sub(r"\bтовар\w*\b", " то ", t)
    # После записи STT может отбросить «сколько» и распознать «ТО» как «того».
    t = re.sub(r"\bстоит\s+(?:то|того)\b", " сколько стоит то ", t)
    t = re.sub(r"\bпоч[её]м\s+(?:то|того)\b", " сколько стоит то ", t)
    return re.sub(r"\s+", " ", t).strip()


def is_to_booking_price_inquiry(text: str) -> bool:
    """В блоке записи на ТО любой вопрос о стоимости считаем вопросом о цене ТО."""
    t = normalize_to_booking_price_stt(text)
    return any(p in t for p in _PRICE_MARKERS)


def is_non_to_price_inquiry(text: str) -> bool:
    t = (text or "").lower()
    if not any(p in t for p in _PRICE_MARKERS):
        return False
    return not is_to_price_inquiry(text)


def is_to_v2_continue(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in TO_V2_CONTINUE_MARKERS)


def is_to_v2_service_assistant_request(text: str) -> bool:
    return is_service_human_transfer_request(text)


def is_service_human_transfer_request(text: str) -> bool:
    """Просьба перевести на живого ассистента / диспетчера (в т.ч. выбор слота)."""
    raw = (text or "").strip()
    if not raw or is_meaningless_voice_stt(raw):
        return False
    t = raw.lower()
    compact = re.sub(r"[^\w\u0400-\u04FFa-zA-Z]+", "", t, flags=re.UNICODE)
    # Ответ сразу после меню 14: STT обрезает «перевести» до одиночного «вести».
    if compact in {"вести", "привести"}:
        return True
    # STT может оставить только «дайте» без дополнения («дайте [человека]»).
    if compact in {"дайте", "дай"}:
        return True
    # Слитные формы и STT-опечатки «мастер-приемщик/настерприемщик».
    if any(token in compact for token in ("мастерприемщик", "мастерприёмщик", "настерприемщик", "настерприёмщик", "настер")):
        return True
    if any(m in t for m in TO_V2_SERVICE_ASSISTANT_MARKERS):
        return True
    if _HUMAN_TRANSFER_TARGET_RE.search(t) and _HUMAN_TRANSFER_VERB_RE.search(t):
        return True
    if re.search(
        r"\b(?:на|к)\s+(?:человек|диспетчер|ассистент|оператор|менеджер|специалист)\w*\b",
        t,
    ):
        return True
    if is_to_v2_continue(t):
        return False
    return False


def is_price_transfer_confirm_stt(text: str) -> bool:
    """Согласие на перевод после «цена ТО не найдена» (в т.ч. STT «Превести.»)."""
    from dialog.bot_logic import _voice_affirmative_stt

    raw = (text or "").strip()
    if not raw or is_meaningless_voice_stt(raw):
        return False
    t_low = raw.lower()
    if _voice_affirmative_stt(t_low):
        return True
    if is_to_v2_service_assistant_request(text):
        return True
    transfer_stt = ("превести", "преведите", "переведите", "переводи", "соедините")
    return any(w in t_low for w in transfer_stt)


def is_to_v2_record_choice(text: str) -> bool:
    if is_to_booking_test_trigger_stt(text):
        return True
    t = normalize_to_v2_record_stt(text)
    return any(m in t for m in TO_V2_RECORD_MARKERS)


def is_menu14_unclear(text: str) -> bool:
    raw = (text or "").strip()
    if not raw or is_meaningless_voice_stt(raw):
        return True
    t = raw.lower()
    if any(u in t for u in ("алло", "алле", "что?", "не понял", "не поняла", "повторите", "не расслыш")):
        return True
    if "сервис" in t and not is_to_v2_record_choice(t) and not is_to_v2_service_assistant_request(t):
        return True
    return False


_GATE5_CHECKIN_MARKERS: tuple[str, ...] = (
    "алло",
    "алле",
    "але",
    "алё",
    "слушаю",
    "я здесь",
    "не понял",
    "не поняла",
    "повторите",
    "не расслыш",
    "не слышу",
    "ну что",
    "вы тут",
    "ты тут",
)


def is_to_v2_gate5_unclear(text: str) -> bool:
    """
    Реплика на gate5 без выбора «дальше» / «диспетчер»:
    проверка связи («алло»), непонятный ответ — переспрос, не перевод.
    """
    raw = (text or "").strip()
    if not raw or is_meaningless_voice_stt(raw):
        return True
    t = raw.lower()
    if any(u in t for u in _GATE5_CHECKIN_MARKERS):
        return True
    compact = re.sub(r"[^\w\u0400-\u04FFa-zA-Z]+", "", t, flags=re.UNICODE).lower()
    return compact in ("алло", "алле", "але", "слушаю", "угу", "ага")


def is_chery_tenet_brand(brand: Optional[str]) -> bool:
    b = (brand or "").lower()
    if any(x in b for x in ("chery", "чери")):
        return True
    tenet_markers = (
        "tenet", "tent", "tennet", "tenant", "tnt", "teng",
        "тенет", "тэнет", "тенеет", "тэнт", "тент", "тинет", "теннет",
        "тенеед", "тенёт", "тенат", "танет", "тенит", "тэнит", "тэнэт",
    )
    return any(m in b for m in tenet_markers)


def parse_disambiguation_answer(text: str, field: str) -> Optional[Any]:
    t = (text or "").lower()
    if field == "transmission":
        normalized = re.sub(r"[^\w]+", " ", t, flags=re.UNICODE).strip()
        compact = normalized.replace(" ", "")
        if any(x in normalized for x in ("механ", "мех", "manual", "ручн")) or compact == "mt":
            return "mt"
        if any(x in normalized for x in ("автомат", "вариатор", "robot", "cvt")):
            return "at"
        if compact in {"атомат", "ат", "at", "автамат", "автомт"}:
            return "at"
        return None
    if field == "volume_l":
        normalized = re.sub(r"[^\w.,]+", " ", t, flags=re.UNICODE).strip(" .,")
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:л|l)?", normalized)
        if m:
            try:
                return float(m.group(1).replace(",", "."))
            except (TypeError, ValueError):
                return None
        if "полтора" in normalized or "1.5" in normalized or "1,5" in normalized:
            return 1.5
        if (
            "1.6" in normalized
            or "1,6" in normalized
            or "один и шесть" in normalized
            or "один шесть" in normalized
        ):
            return 1.6
        if (
            "2.0" in normalized
            or "2,0" in normalized
            or "два литра" in normalized
            or "два ноль" in normalized
            or normalized in {"два", "дво", "ва", "тро"}
        ):
            return 2.0
        return None
    if field == "drive":
        if any(x in t for x in ("полн", "4wd", "4x4", "awd")):
            return "awd"
        if any(x in t for x in ("передн", "fwd")):
            return "fwd"
        return None
    return None


def disambiguation_prompt(field: str, *, repeat: bool = False) -> tuple[str, str]:
    """(wav_filename, tts_text) для уточняющего вопроса блока D."""
    from services.voice.voice_phrases import (
        TO_V2_PRICE_ASK_DRIVE,
        TO_V2_PRICE_ASK_TRANSMISSION,
        TO_V2_PRICE_ASK_VOLUME,
        TO_V2_PRICE_REPEAT_DRIVE,
        TO_V2_PRICE_REPEAT_TRANSMISSION,
        TO_V2_PRICE_REPEAT_VOLUME,
    )

    key = (field, repeat)
    mapping: dict[tuple[str, bool], tuple[str, str]] = {
        ("transmission", False): ("35_to_v2_price_ask_transmission.wav", TO_V2_PRICE_ASK_TRANSMISSION),
        ("transmission", True): ("38_to_v2_price_repeat_transmission.wav", TO_V2_PRICE_REPEAT_TRANSMISSION),
        ("drive", False): ("37_to_v2_price_ask_drive.wav", TO_V2_PRICE_ASK_DRIVE),
        ("drive", True): ("40_to_v2_price_repeat_drive.wav", TO_V2_PRICE_REPEAT_DRIVE),
        ("volume_l", False): ("36_to_v2_price_ask_volume.wav", TO_V2_PRICE_ASK_VOLUME),
        ("volume_l", True): ("39_to_v2_price_repeat_volume.wav", TO_V2_PRICE_REPEAT_VOLUME),
    }
    return mapping.get(key, ("17_repeat.wav", "Повторите, пожалуйста."))


def disambiguation_question_tts(field: str, *, repeat: bool = False) -> str:
    return disambiguation_prompt(field, repeat=repeat)[1]


def ruble_word_form_ru(amount: int) -> str:
    """Склонение «рубль/рубля/рублей» по последним цифрам суммы."""
    n = abs(int(amount))
    last2 = n % 100
    last1 = n % 10
    if 11 <= last2 <= 14:
        return "рублей"
    if last1 == 1:
        return "рубль"
    if last1 in (2, 3, 4):
        return "рубля"
    return "рублей"


def rubles_amount_to_words_ru(amount: int) -> str:
    """Сумма в рублях словами для TTS (до 999 999)."""
    from dialog.bot_logic import number_to_words_ru

    n = int(round(amount))
    if n <= 0:
        return "ноль рублей"
    rub = ruble_word_form_ru(n)
    if n <= 999:
        return f"{number_to_words_ru(n)} {rub}"
    thousands, rem = divmod(n, 1000)
    th = number_to_words_ru(thousands)
    if rem == 0:
        return f"{th} тысяч {rub}"
    return f"{th} тысяч {number_to_words_ru(rem)} {rub}"


def format_price_announcement_tts(to_label: str, price_rub: float) -> str:
    """Озвучивание суммы: числительные словами, без сырых цифр и «10 т.км»."""
    amount_words = rubles_amount_to_words_ru(int(round(price_rub)))
    label = (to_label or "").strip()
    if re.search(r"\d+\s*т\.?\s*км", label, flags=re.IGNORECASE):
        text = (
            f"Ориентировочная стоимость — {amount_words}. "
            f"Предварительный расчёт по регламенту."
        )
    elif label and label.upper() != "ТО":
        text = (
            f"Ориентировочная стоимость {label} — {amount_words}. "
            f"Предварительный расчёт по регламенту."
        )
    else:
        text = (
            f"Ориентировочная стоимость — {amount_words}. "
            f"Предварительный расчёт по регламенту."
        )
    return text


def format_price_announcement(to_label: str, price_rub: float) -> str:
    """Alias для совместимости — всегда TTS с числительными."""
    return format_price_announcement_tts(to_label, price_rub)


def format_to_price_for_lead(sd_data: Optional[dict[str, Any]]) -> str:
    """
    Строка для лида: цена и комплектация, если озвучивали; иначе «цена не называлась».
    """
    if not sd_data or not sd_data.get("price_announced"):
        return "цена не называлась"
    raw = sd_data.get("price_rub")
    if raw is None:
        return "цена не называлась"
    try:
        amount = int(round(float(raw)))
    except (TypeError, ValueError):
        return "цена не называлась"
    if amount <= 0:
        return "цена не называлась"

    parts: list[str] = []
    pb = sd_data.get("price_block") or {}
    tx = pb.get("transmission")
    if tx == "at":
        parts.append("КПП автомат")
    elif tx == "mt":
        parts.append("КПП механика")
    vol = pb.get("volume_l")
    if vol is not None:
        try:
            parts.append(f"объём {float(vol):g} л")
        except (TypeError, ValueError):
            pass
    drive = pb.get("drive")
    if drive == "awd":
        parts.append("полный привод")
    elif drive == "fwd":
        parts.append("передний привод")

    label = (sd_data.get("to_label") or "").strip()
    if label and label.upper() != "ТО":
        price_part = f"стоимость {label}: {amount} руб"
    else:
        price_part = f"стоимость ТО: {amount} руб"
    if parts:
        return f"комплектация: {', '.join(parts)}; {price_part}"
    return price_part


def _normalize_model_for_sto_lookup(brand: str, model: str) -> str:
    """Модель для Excel: латиница TIGGO, без дубля марки («Chery ТИГГО 7» → «Chery TIGGO 7»)."""
    b = (brand or "").strip()
    m = (model or "").strip()
    if not m:
        return m
    if b and m.upper().startswith(b.upper()):
        m = m[len(b) :].strip()
    m = re.sub(r"\bтигго\b", "TIGGO", m, flags=re.IGNORECASE)
    m = re.sub(r"\bтенет\b", "TENET", m, flags=re.IGNORECASE)
    m = re.sub(r"\bтэнет\b", "TENET", m, flags=re.IGNORECASE)
    m = re.sub(r"\s+", " ", m).strip()
    if b:
        return f"{b} {m}".strip() if m else b
    return m


def _auto_fill_sole_disambig_choices(
    candidates: list[dict[str, Any]],
    engine_gearbox_col: Optional[str],
    chosen: dict[str, Any],
) -> None:
    """Если по всем кандидатам одно значение КПП/объёма/привода — подставляем без вопроса."""
    from dialog.sto_to_summary_table import filter_sto_to_candidates_by_spec, parse_engine_gearbox_cell

    if not engine_gearbox_col or not candidates:
        return
    changed = True
    while changed:
        changed = False
        filtered = filter_sto_to_candidates_by_spec(
            candidates,
            engine_gearbox_col=engine_gearbox_col,
            transmission=chosen.get("transmission"),
            volume_l=chosen.get("volume_l"),
            drive=chosen.get("drive"),
        )
        if not filtered:
            return
        for field in ("transmission", "volume_l", "drive"):
            if chosen.get(field) is not None:
                continue
            distinct: set[Any] = set()
            for row in filtered:
                spec = parse_engine_gearbox_cell(row.get(engine_gearbox_col))
                val = getattr(spec, field, None)
                if val is not None:
                    distinct.add(val)
            if len(distinct) == 1:
                chosen[field] = next(iter(distinct))
                changed = True


def resolve_to_price_from_service_data(sd_data: dict[str, Any]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """
    Возвращает (result_dict, next_disambig_field).
    result_dict: to_label, price_rub, price_announced=True — если цена готова.
    """
    brand = sd_data.get("car_brand") or ""
    model = _normalize_model_for_sto_lookup(brand, sd_data.get("car_model") or "")
    raw_m = sd_data.get("mileage")
    mileage_km: Optional[int] = None
    if raw_m is not None:
        try:
            mileage_km = int(str(raw_m).replace(" ", ""))
        except (TypeError, ValueError):
            mileage_km = None

    if not is_chery_tenet_brand(brand):
        return None, None

    pb = sd_data.setdefault("price_block", {})
    chosen = {
        "transmission": pb.get("transmission"),
        "volume_l": pb.get("volume_l"),
        "drive": pb.get("drive"),
    }

    candidates, _, cols = _collect_sto_to_candidates(brand, model, mileage_km)
    eg_col = cols.get("engine_gearbox_col")
    if not candidates:
        return None, None

    _auto_fill_sole_disambig_choices(candidates, eg_col, chosen)
    for key in ("transmission", "volume_l", "drive"):
        if chosen.get(key) is not None:
            pb[key] = chosen[key]

    filtered = filter_sto_to_candidates_by_spec(
        candidates,
        engine_gearbox_col=eg_col,
        transmission=chosen.get("transmission"),
        volume_l=chosen.get("volume_l"),
        drive=chosen.get("drive"),
    )
    if len(filtered) > 1 and eg_col:
        field = next_sto_to_disambiguation_field(filtered, eg_col, chosen)
        if field:
            return None, field

    result = lookup_sto_to_regulation_with_filters(
        brand,
        model,
        mileage_km,
        transmission=chosen.get("transmission"),
        volume_l=chosen.get("volume_l"),
        drive=chosen.get("drive"),
    )
    if not result.found or result.price_total <= 0:
        return None, None

    return {
        "to_label": result.to_label,
        "price_rub": result.price_total,
        "price_announced": True,
    }, None


def engine_gearbox_params_for_1c(
    sd_data: Optional[dict[str, Any]] = None,
    *,
    price_block: Optional[dict[str, Any]] = None,
) -> dict[str, str]:
    """
    Параметры КПП / объёма / привода для query 1С.
    Если не выясняли (блок D) — «0».
    """
    pb = price_block if price_block is not None else (sd_data or {}).get("price_block") or {}

    transmission = pb.get("transmission")
    if transmission in ("mt", "at"):
        tx = str(transmission)
    else:
        tx = "0"

    volume_l = pb.get("volume_l")
    if volume_l is not None:
        try:
            vol = f"{float(volume_l):g}"
        except (TypeError, ValueError):
            vol = "0"
    else:
        vol = "0"

    drive = pb.get("drive")
    if drive in ("fwd", "awd"):
        dr = str(drive)
    else:
        dr = "0"

    return {
        "transmission": tx,
        "engine_volume": vol,
        "drive": dr,
    }
