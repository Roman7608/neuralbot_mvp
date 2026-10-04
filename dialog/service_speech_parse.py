"""Извлечение пробега из речи (запись на ТО). Год выпуска не считается пробегом."""

from __future__ import annotations

import re
from typing import Optional

_MODEL_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_YEAR_4DIGIT_RE = re.compile(r"\b(19|20)\d{2}\b")
_YEAR_2DIGIT_ORDINAL_RE = re.compile(
    r"\b(\d{2})\s*[-]?\s*(?:й|го|года|г\.?)\b",
    re.IGNORECASE,
)
_YEAR_WORD_TWENTY_THIRTY_RE = re.compile(
    r"\b(двадцать|тридцать)\s+([а-яё]+)\b",
    re.IGNORECASE,
)
_YEAR_WORD_ROUND_TENS_RE = re.compile(r"\b(двадцат\w*|тридцат\w*)\b", re.IGNORECASE)
_TO_MILEAGE_IN_WORKS_RE = re.compile(
    r"(?:\bто\b|\bтого\b|\bтехнич\w*\s+обслуж\w*|"
    r"обслужив\w*|т\.?\s*о\.?)"
    r"[^\d]{0,24}(\d+(?:\s+\d{3})*)",
    re.IGNORECASE,
)

_ONES_WORDS = {
    "ноль": 0,
    "нуль": 0,
    "один": 1,
    "одна": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
}
_TEENS_WORDS = {
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
}
_TENS_WORDS = {
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
}
_HUNDREDS_WORDS = {
    "сто": 100,
    "двести": 200,
    "триста": 300,
    "четыреста": 400,
    "пятьсот": 500,
    "шестьсот": 600,
    "семьсот": 700,
    "восемьсот": 800,
    "девятьсот": 900,
}
_THOUSANDS_WORDS = {
    "тысяча",
    "тысячи",
    "тысяч",
    "тыс",
    "тыс.",
}


def _parse_number_words_under_1000(tokens: list[str]) -> Optional[int]:
    if not tokens:
        return None
    value = 0
    for token in tokens:
        if token in _HUNDREDS_WORDS:
            value += _HUNDREDS_WORDS[token]
            continue
        if token in _TENS_WORDS:
            value += _TENS_WORDS[token]
            continue
        if token in _TEENS_WORDS:
            value += _TEENS_WORDS[token]
            continue
        if token in _ONES_WORDS:
            value += _ONES_WORDS[token]
            continue
        return None
    return value if value > 0 else None


def _parse_spoken_mileage_km(text_low: str) -> Optional[int]:
    clean = re.sub(r"[^a-zа-яё0-9\s]", " ", text_low, flags=re.IGNORECASE)
    tokens = [t for t in clean.split() if t]
    if not tokens:
        return None

    # «четыре триста» -> 4300 (частый STT-формат без «тысяч»).
    if (
        len(tokens) >= 2
        and tokens[0] in _ONES_WORDS
        and tokens[1] in _HUNDREDS_WORDS
    ):
        prefix = _ONES_WORDS[tokens[0]] * 1000
        suffix = _parse_number_words_under_1000(tokens[1:3]) or 0
        if prefix > 0:
            return prefix + suffix

    if any(t in _THOUSANDS_WORDS for t in tokens):
        idx = next(i for i, t in enumerate(tokens) if t in _THOUSANDS_WORDS)
        before = tokens[:idx]
        after = tokens[idx + 1 :]
        left = _parse_number_words_under_1000(before) if before else 1
        if left is None:
            return None
        right = _parse_number_words_under_1000(after) if after else 0
        if right is None:
            return None
        return left * 1000 + right

    return None


def is_model_year_digits(val: str) -> bool:
    return bool(_MODEL_YEAR_RE.fullmatch((val or "").strip()))


def mileage_km_for_1c(
    raw: Optional[str],
    *,
    car_year: Optional[str] = None,
) -> Optional[str]:
    """Пробег для query 1С: без пустых значений и без года выпуска."""
    s = (raw or "").strip()
    if not s or len(s) < 3:
        return None
    if is_model_year_digits(s):
        return None
    if car_year and s == str(car_year).strip():
        return None
    return s


def parse_mileage_km_from_work_list(
    work_list: Optional[str],
    *,
    car_year: Optional[str] = None,
) -> Optional[str]:
    """Пробег из списка работ: «ТО 30 000», «Того 30 000», «техобслуживание 30000»."""
    text = (work_list or "").strip()
    if not text:
        return None
    m = _TO_MILEAGE_IN_WORKS_RE.search(text)
    if m:
        parsed = mileage_km_for_1c(m.group(1).replace(" ", ""), car_year=car_year)
        if parsed:
            return parsed
    t_low = text.lower()
    thousands = re.search(r"(\d+)\s*(?:тысяч|тыс|тыс\.)", t_low)
    if thousands:
        parsed = mileage_km_for_1c(str(int(thousands.group(1)) * 1000), car_year=car_year)
        if parsed:
            return parsed
    for m in re.finditer(r"(\d+(?:\s+\d{3})*)", text):
        val = m.group(1).replace(" ", "")
        parsed = mileage_km_for_1c(val, car_year=car_year)
        if parsed and int(parsed) >= 1000:
            return parsed
    return None


def resolve_mileage_km_for_booking(
    *,
    mileage: Optional[str] = None,
    work_list: Optional[str] = None,
    car_year: Optional[str] = None,
) -> Optional[str]:
    """Пробег для записи в 1С: шаг 4 → fallback из work_list."""
    from_mileage = mileage_km_for_1c(mileage, car_year=car_year)
    if from_mileage:
        return from_mileage
    return parse_mileage_km_from_work_list(work_list, car_year=car_year)


def normalize_car_model_for_1c(car_brand: str, car_model: str) -> str:
    """«Tenet Т4» → «Т4» (без дублирования марки в model)."""
    model = (car_model or "").strip()
    brand = (car_brand or "").strip()
    if brand and model.lower().startswith(brand.lower()):
        rest = model[len(brand):].strip(" -")
        if rest:
            return rest
    return model


_ORDINAL_DIGIT_STT: dict[str, str] = {
    "1": "первое",
    "2": "второе",
    "3": "третье",
    "4": "четвертое",
    "5": "пятое",
    "6": "шестое",
    "7": "седьмое",
    "8": "восьмое",
    "9": "девятое",
    "10": "десятое",
    "11": "одиннадцатое",
    "12": "двенадцатое",
    "13": "тринадцатое",
    "14": "четырнадцатое",
    "15": "пятнадцатое",
    "16": "шестнадцатое",
    "17": "семнадцатое",
    "18": "восемнадцатое",
    "19": "девятнадцатое",
    "20": "двадцатое",
    "21": "двадцать первое",
    "22": "двадцать второе",
    "23": "двадцать третье",
    "24": "двадцать четвертое",
    "25": "двадцать пятое",
    "26": "двадцать шестое",
    "27": "двадцать седьмое",
    "28": "двадцать восьмое",
    "29": "двадцать девятое",
    "30": "тридцатое",
    "31": "тридцать первое",
}

_DATE_MONTH_WORD_RE = (
    r"(?:января|февраля|марта|апреля|мая|июня|июля|августа|"
    r"сентября|октября|ноября|декабря|январь|февраль|март|апрель|"
    r"май|июнь|июль|август|сентябрь|октябрь|ноябрь|декабрь)"
)


def _ordinal_case_forms(ordinal: str) -> tuple[str, ...]:
    """Формы даты для «первое/первого/первому» и составных числительных."""
    forms = {ordinal}
    words = ordinal.split()
    last = words[-1]
    stem = " ".join(words[:-1])
    prefix = f"{stem} " if stem else ""
    if last == "третье":
        forms.update((f"{prefix}третьего", f"{prefix}третьему"))
    elif last.endswith("ое"):
        base = last[:-2]
        forms.update((f"{prefix}{base}ого", f"{prefix}{base}ому"))
    elif last.endswith("ее"):
        base = last[:-2]
        forms.update((f"{prefix}{base}его", f"{prefix}{base}ему"))
    return tuple(sorted(forms, key=len, reverse=True))


def _build_ordinal_prefix_stt_variants() -> tuple[tuple[str, str], ...]:
    """
    Варианты потери/отделения первых 1–2 букв:
    «д вадцатое», «дв адцатое», «вадцатое», «адцатое».
    Неоднозначные варианты исключаются.
    """
    canonical_forms = {
        form
        for ordinal in _ORDINAL_DIGIT_STT.values()
        for form in _ordinal_case_forms(ordinal)
    }
    candidates: dict[str, set[str]] = {}
    for ordinal in _ORDINAL_DIGIT_STT.values():
        for canonical in _ordinal_case_forms(ordinal):
            first_word, *tail = canonical.split()
            suffix = f" {' '.join(tail)}" if tail else ""
            if len(first_word) < 4:
                continue
            variants = (
                f"{first_word[:1]} {first_word[1:]}{suffix}",
                f"{first_word[:2]} {first_word[2:]}{suffix}",
                f"{first_word[1:]}{suffix}",
                f"{first_word[2:]}{suffix}",
            )
            for variant in variants:
                # «семнадцатое» — самостоятельная корректная дата 17-е, хотя это
                # также возможная потеря «во» у «восемнадцатое». Не угадываем.
                if variant in canonical_forms and variant != canonical:
                    continue
                candidates.setdefault(variant, set()).add(canonical)
    unique = (
        (variant, next(iter(canonicals)))
        for variant, canonicals in candidates.items()
        if len(canonicals) == 1
    )
    return tuple(sorted(unique, key=lambda item: len(item[0]), reverse=True))


_ORDINAL_PREFIX_STT_VARIANTS = _build_ordinal_prefix_stt_variants()
def _normalize_ordinal_prefix_stt_before_month(text: str) -> str:
    """Исправляет обрезки числительных только непосредственно перед месяцем."""
    t = text
    month_matches = list(re.finditer(rf"\b{_DATE_MONTH_WORD_RE}\b", t, re.IGNORECASE))
    for month_match in reversed(month_matches):
        prefix = t[:month_match.start()]
        for variant, canonical in _ORDINAL_PREFIX_STT_VARIANTS:
            variant_re = r"\s+".join(re.escape(part) for part in variant.split())
            match = re.search(
                rf"(?<![\w]){variant_re}[\s,]+$",
                prefix,
                re.IGNORECASE,
            )
            if not match:
                continue
            t = f"{prefix[:match.start()]}{canonical} {t[month_match.start():]}"
            break
    return t


def _cyr_word(pattern: str) -> str:
    """Границы слова для кириллицы (\\b в Python не работает на «десятое»)."""
    return rf"(?<![а-яёА-ЯЁ]){pattern}(?![а-яёА-ЯЁ])"


def normalize_stt_booking_date_text(
    text: str,
    *,
    slot_day9_hint: bool = False,
    slot_day10_hint: bool = False,
) -> str:
    """STT дат записи: «2рое июля», «5тое июля», «Ты Юля» (≈9 июля), «юля» → «июля»."""
    t = text or ""
    # STT: «двадцть» / «двацть» -> «двадцать».
    t = re.sub(r"\bдвадцть\b", "двадцать", t, flags=re.IGNORECASE)
    t = re.sub(r"\bдвацть\b", "двадцать", t, flags=re.IGNORECASE)
    # STT: «аву» как обломок «августа».
    t = re.sub(r"\bаву\b", "августа", t, flags=re.IGNORECASE)
    t = re.sub(r"\bты\s+юля\b", "девятое июля", t, flags=re.IGNORECASE)
    t = re.sub(r"(?<![и])юля\b", "июля", t, flags=re.IGNORECASE)
    # STT: одиночное «Июля» в начале реплики — обломок, не месяц.
    t = re.sub(r"^\s*июля\s*[\.,!]?\s+", "", t, flags=re.IGNORECASE)
    t = _normalize_ordinal_prefix_stt_before_month(t)
    # Цифровой обломок «3цатое» не выводится из общего буквенного правила.
    t = re.sub(
        rf"(?<![\w])3\s*[-]?\s*цат(ое|ого|ому)"
        rf"(?=[\s,]+{_DATE_MONTH_WORD_RE}\b)",
        r"тридцат\1",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «3ц июля» — короткий обломок «тридцатое июля».
    t = re.sub(
        rf"(?<![\w])3\s*ц(?=[\s,]+{_DATE_MONTH_WORD_RE}\b)",
        "тридцатое",
        t,
        flags=re.IGNORECASE,
    )

    def _ordinal_repl(m: re.Match[str]) -> str:
        return _ORDINAL_DIGIT_STT.get(m.group(1), m.group(0))

    t = re.sub(r"\b(\d{1,2})\s*ро(?:е|го|й)?\b", _ordinal_repl, t, flags=re.IGNORECASE)
    # STT: «5тое июля» (цифра без пробела перед «тое»).
    t = re.sub(r"\b(\d{1,2})то(?:е|го|й)\b", _ordinal_repl, t, flags=re.IGNORECASE)
    # STT: «10ое июля» (цифра + «ое» без «т»).
    t = re.sub(r"\b(\d{1,2})ое\b", _ordinal_repl, t, flags=re.IGNORECASE)
    # STT: «10е июля» (цифра + «е»).
    t = re.sub(r"\b10е\b", "десятое", t, flags=re.IGNORECASE)
    # STT: «15е октября» -> «пятнадцатое октября» (цифра + «е» перед названием месяца).
    t = re.sub(
        rf"\b(\d{{1,2}})е(?=[\s,]+{_DATE_MONTH_WORD_RE}\b)",
        _ordinal_repl,
        t,
        flags=re.IGNORECASE,
    )
    # STT: «тридцать 1ер августа» -> «тридцать первое августа».
    t = re.sub(r"\b1ер(?:вое|вого|вому)?\b", "первое", t, flags=re.IGNORECASE)
    t = re.sub(r"\b1\s*ер(?:вое|вого|вому)?\b", "первое", t, flags=re.IGNORECASE)
    # STT: «9ятое» ≈ «девятое» (9 + «ятое», без «дев»).
    t = re.sub(r"\b9ято(?:е|го|й)\b", "девятое", t, flags=re.IGNORECASE)
    # STT: обрезанное «девятое» без «де» / «д».
    t = re.sub(r"\bевято(?:е|го|й)\b", "девятое", t, flags=re.IGNORECASE)
    t = re.sub(r"\bвято(?:е|го|й)\b", "девятое", t, flags=re.IGNORECASE)
    t = re.sub(r"\bптое\b", "девятое", t, flags=re.IGNORECASE)
    # STT: «2цать девя-е число» / «двадцать девя-е число».
    t = re.sub(
        r"\b2\s*цат(?:ь)?\s+девя\s*[-\s]?\s*(?:е|ое|ого|ому)\b",
        "двадцать девятое",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bдвадцать\s+девя\s*[-\s]?\s*(?:е|ое|ого|ому)\b",
        "двадцать девятое",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «Победа» ≈ «после обеда».
    t = re.sub(r"\bпобед\w*\b", "после обеда", t, flags=re.IGNORECASE)
    # «пятое»→«девятое» только при явных маркерах 9-го в этой же реплике (не для «десятое»).
    if slot_day9_hint and not slot_day10_hint:
        t = re.sub(r"\bна\s+5\s+июл\w*\b", "на 9 июля", t, flags=re.IGNORECASE)
        t = re.sub(r"\bпятое\b", "девятое", t, flags=re.IGNORECASE)
    return t


_SLOT_DAY9_STT_RE = re.compile(
    r"\b(?:9ято|вято|евято|птое)(?:е|го|й)?\b",
    re.IGNORECASE,
)


def slot_day9_stt_markers(text: str) -> bool:
    """Реплика похожа на попытку назвать 9-е число (в т.ч. обрезки STT)."""
    raw = (text or "").lower()
    # «десятое» содержит «девят» как подстроку — сначала отсекаем 10-е.
    if slot_day10_stt_markers(raw):
        return False
    if re.search(_cyr_word(r"девят(?:ое|ого|ому)"), raw):
        return True
    if _SLOT_DAY9_STT_RE.search(raw):
        return True
    return bool(re.search(r"\bна\s+9\s+июл", raw))


def slot_day10_stt_markers(text: str) -> bool:
    """Явное 10-е число: «10 июля», «десятое июля», «на десятое»."""
    raw = (text or "").lower()
    if re.search(_cyr_word(r"(?:на\s+)?десят(?:ое|ого|ому)"), raw):
        return True
    if re.search(r"\b10\b", raw):
        return True
    if re.search(r"\b10ое\b", raw):
        return True
    if re.search(r"\b10тое\b", raw):
        return True
    if re.search(r"\bдесять\b", raw):
        return True
    if re.search(_cyr_word(r"десятому"), raw):
        return True
    if re.search(_cyr_word(r"десят(?:ое|ого)\s+июл\w*"), raw):
        return True
    return False


def looks_like_slot_date_attempt(text: str) -> bool:
    """Реплика похожа на попытку назвать дату (даже если парсер ещё не справился)."""
    norm = normalize_stt_booking_date_text(text or "").lower()
    if not norm.strip():
        return False
    if re.search(
        r"\b(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)",
        norm,
    ):
        return True
    if re.search(r"\b\d{1,2}\s*ро", norm):
        return True
    if re.search(r"\b\d{1,2}то", norm):
        return True
    if re.search(r"\b9ято", norm):
        return True
    if re.search(r"\b(?:вято|евято|птое)(?:е|го|й)?\b", norm):
        return True
    if re.search(r"\bна\s+9\s+июл", norm):
        return True
    if re.search(
        r"\b(?:перв|втор|трет|четверт|пят|шест|седьм|восьм|девят|десят|"
        r"одиннадцат|двенадцат|тринадцат|четырнадцат|пятнадцат|"
        r"шестнадцат|семнадцат|восемнадцат|девятнадцат|двадцат|тридцат)",
        norm,
    ):
        return True
    return False


def prepare_booking_date_stt(
    text: str,
    sd_data: Optional[dict] = None,
) -> tuple[str, bool, bool]:
    """Нормализация STT даты: подсказки 9/10 только из текущей реплики (не накапливаются)."""
    raw = text or ""
    if sd_data is not None:
        sd_data.pop("slot_day9_hint", None)
        sd_data.pop("slot_day10_hint", None)
    day10 = slot_day10_stt_markers(raw)
    day9 = slot_day9_stt_markers(raw)
    norm = normalize_stt_booking_date_text(
        raw,
        slot_day9_hint=day9,
        slot_day10_hint=day10,
    )
    return norm, day9, day10


def parse_car_year_from_speech(text: str, t_low: Optional[str] = None) -> Optional[str]:
    """Год выпуска из реплики шага «год и пробег» (в т.ч. STT «Пяты 10 000» ≈ 25-й год)."""
    raw = text or ""
    t_low = t_low if t_low is not None else raw.lower()
    candidates: list[tuple[int, str]] = []

    for m in _YEAR_4DIGIT_RE.finditer(raw):
        candidates.append((m.start(), m.group(0)))

    for m in _YEAR_2DIGIT_ORDINAL_RE.finditer(t_low):
        yy = int(m.group(1))
        if 10 <= yy <= 39:
            candidates.append((m.start(), str(2000 + yy)))

    # Словесные годы (в т.ч. «двадцать третье … двадцать четвертый») — берём последний.
    words_blob = re.sub(r"[-–—]+", " ", t_low)
    unit_map = {
        "перв": 1,
        "втор": 2,
        "трет": 3,
        "четверт": 4,
        "пят": 5,
        "шест": 6,
        "седьм": 7,
        "восьм": 8,
        "девят": 9,
    }
    for m in _YEAR_WORD_TWENTY_THIRTY_RE.finditer(words_blob):
        tens_word = m.group(1).lower()
        unit_word = m.group(2).lower().replace("ё", "е")
        tens = 20 if tens_word.startswith("двадц") else 30
        unit = next((v for k, v in unit_map.items() if unit_word.startswith(k)), None)
        if unit is None:
            continue
        yy = tens + unit
        if 10 <= yy <= 39:
            candidates.append((m.start(), str(2000 + yy)))

    for m in _YEAR_WORD_ROUND_TENS_RE.finditer(words_blob):
        w = m.group(1).lower()
        yy = 20 if w.startswith("двадцат") else 30
        candidates.append((m.start(), str(2000 + yy)))

    # STT: «205-й год» ≈ «25-й» / «2025»
    m = re.search(r"\b205\s*[-]?\s*(?:й|го|года|г\.?)\b", t_low)
    if m:
        candidates.append((m.start(), "2025"))
    # STT: начало «2025-й» потеряло первую двойку и окончание:
    # «025-. 10 000 км.» — второе число явно является пробегом.
    if (
        re.search(r"^\s*0?25(?:\D|$)", t_low)
        and re.search(r"\d+(?:\s+\d{3})+\s*(?:км|километр)", t_low)
    ):
        candidates.append((0, "2025"))
    # STT: «Пяты» без «двадцать» — при наличии пробега в той же реплике.
    m = re.search(r"\bпят\w+\b", t_low)
    if (
        m
        and not re.search(r"\bпять\s*(?:тысяч|тыс)", t_low)
        and re.search(r"\d", raw)
    ):
        candidates.append((m.start(), "2025"))
    if candidates:
        return max(candidates, key=lambda item: item[0])[1]
    return None


def parse_mileage_km_from_speech(
    text: str,
    t_low: Optional[str] = None,
    *,
    car_year: Optional[str] = None,
) -> Optional[str]:
    """Пробег в км из реплики шага «год и пробег»."""
    t_low = t_low if t_low is not None else (text or "").lower()
    year_shard_205 = bool(re.search(r"\b205\s*[-]?\s*(?:й|го|года|г\.?)\b", t_low))
    thousands = re.search(r"(\d+)\s*(?:тысяч|тыс|тыс\.)", t_low)
    if thousands:
        return mileage_km_for_1c(
            str(int(thousands.group(1)) * 1000),
            car_year=car_year,
        )
    spoken_mileage = _parse_spoken_mileage_km(t_low)
    if spoken_mileage:
        parsed = mileage_km_for_1c(str(spoken_mileage), car_year=car_year)
        if parsed:
            return parsed
    # Если явно произнесены километры, это надёжнее первого числа (года).
    km_value = re.search(
        r"(\d+(?:\s+\d{3})*)\s*(?:км|километр\w*)",
        t_low,
        flags=re.IGNORECASE,
    )
    if km_value:
        parsed = mileage_km_for_1c(
            km_value.group(1).replace(" ", ""),
            car_year=car_year,
        )
        if parsed:
            return parsed
    for m in re.finditer(r"(\d+(?:\s+\d{3})*)", text or ""):
        val = m.group(1).replace(" ", "")
        if year_shard_205 and val == "205":
            continue
        parsed = mileage_km_for_1c(val, car_year=car_year)
        if parsed:
            return parsed
    return None


def mileage_label_for_storage_and_notify(
    *,
    mileage_norm: Optional[str],
    mileage_raw: Optional[str] = None,
) -> str:
    """
    Для MAX/карточки: только километраж, без всей STT-фразы
    («Как же у вас там всё сложно. 14 000. …» → «14000»).
    """
    for cand in (mileage_norm, mileage_raw):
        s = (cand or "").strip()
        if not s:
            continue
        if re.fullmatch(r"\d[\d\s]*", s):
            return re.sub(r"\s+", "", s)
    raw = (mileage_raw or "").strip()
    if not raw:
        return ""
    # Из длинной реплики берём число пробега (4–7 цифр / с разрядами), не год 19xx/20xx.
    for m in re.finditer(r"\b(\d{1,3}(?:\s\d{3})+|\d{4,7})\b", raw):
        digits = m.group(1).replace(" ", "")
        if re.fullmatch(r"19\d{2}|20\d{2}", digits):
            continue
        return digits
    return ""
