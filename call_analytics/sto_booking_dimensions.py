"""
Извлечение сервисных признаков для звонков STO_IN/STO_OUT:
- марка (chery_tenet / nissan / other_brand)
- вид работ (to / warranty / diagnostics / body_shop / quality_check / other_work)
- признак «есть запись на ремонт/ТО»

Модуль рассчитан на «грязный» STT: сначала точные и контекстные маркеры,
затем мягкий fuzzy-match по токенам.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Tuple

# «сделать, то мы…» / «сделать, то ли…» — союз «то», не регламентное ТО.
_SDELAT_TO_PARTICLE_FALSE_POSITIVE = r"(?!\s+(?:есть|мы|вы|он|она|они|я|ли)\b)"


_TO_MARKERS = (
    "запись на то",
    "записаться на то",
    "хотели то сделать",
    "хотела то сделать",
    "на то сделать",
    "то сделать",
    "то уже надо делать",
    "техническое обслуживание",
    "техобслуживание",
    "тех обслуживание",
    "нулевое то",
    "то нулевое",
    "первое то",
    "второе то",
    "третье то",
    "четвертое то",
    "четвёртое то",
    "пятое то",
    "шестое то",
    "седьмое то",
    "восьмое то",
    "девятое то",
    "десятое то",
    # Предложный падеж («на третьем то у вас меняется…») — частый STT; иначе узкий слой теряет маркер (8148).
    "первом то",
    "втором то",
    "третьем то",
    "четвертом то",
    "четвёртом то",
    "пятом то",
    "шестом то",
    "седьмом то",
    "восьмом то",
    "девятом то",
    "десятом то",
    "то-1",
    "то-2",
    "то-3",
    "то-4",
    "то-5",
    "то-6",
    "то-7",
    # Разговорные формулировки планового визита (совместно с обсуждением диагностики → всё равно ТО).
    "плановое то",
    "регламентное то",
    "периодическое то",
    "техосмотр",
    "плановое обслуживание",
    "пройти то",
    "то пройти",
    "то проходить",
    # STT 12462: «записаться на очередное ТО» — «на очередное» между «на» и «то».
    "очередное то",
)

# «N-е то» как подстрока ловит «второе тогда», «третье тоже» — проверяем с границей слова.
_TO_ORDINAL_PHRASE_MARKERS = frozenset(
    {
        "нулевое то",
        "то нулевое",
        "первое то",
        "второе то",
        "третье то",
        "четвертое то",
        "четвёртое то",
        "пятое то",
        "шестое то",
        "седьмое то",
        "восьмое то",
        "девятое то",
        "десятое то",
        "первом то",
        "втором то",
        "третьем то",
        "четвертом то",
        "четвёртом то",
        "пятом то",
        "шестом то",
        "седьмом то",
        "восьмом то",
        "девятом то",
        "десятом то",
    }
)


def _technical_service_mention_is_gearbox_service_nomenclature(low: str, start: int, end: int) -> bool:
    """«техническое обслуживание коробки/вариатора» — прайс работы КПП, не регламентное ТО (11996)."""
    window = (low or "")[start : min(len(low or ""), end + 55)]
    return bool(re.search(r"\b(?:коробк|вариатор|трансмис|кпп)\w*\b", window, re.I))


def _technical_service_mention_is_third_party_referral(
    low: str, match_start: int, match_end: int
) -> bool:
    """
    «можно на любой станции технического обслуживания» при балансировке/шиномонтаже —
    совет ехать не к дилеру, не запись на регламентное ТО (9371).
    """
    window = low[max(0, match_start - 160) : match_end + 40]
    referral_ctx = any(
        p in window
        for p in (
            "балансир",
            "шиномонтаж",
            "вибрац",
            "колес",
            "ролик",
            "необязательно",
            "не обязательно",
            "любой станц",
            "в любом шиномонтаж",
        )
    )
    if not referral_ctx:
        return False
    if re.search(
        r"\bзапис\w*(?:аться|ать|ыва)?\s+на\s+(?:то|техническ)",
        window,
    ) or re.search(r"\b(?:перв|втор|трет|четвер)\w*\s+то\b", window):
        return False
    return True


def _regulatory_technical_service_mention_present(low: str) -> bool:
    """Есть «техническ… обслуживан…» про регламентное ТО авто, не КПП и не прошлый визит."""
    for m in re.finditer(r"\bтехническ\w+\s+обслуживан\w+", low):
        if _technical_service_mention_is_gearbox_service_nomenclature(low, m.start(), m.end()):
            continue
        if _technical_service_mention_is_third_party_referral(low, m.start(), m.end()):
            continue
        if _technical_service_mention_is_past_visit_reference(low, m.start(), m.end()):
            continue
        if _technical_service_mention_is_power_of_attorney_template(low, m.start(), m.end()):
            continue
        if _technical_service_mention_is_brand_capability_not_to_booking(low, m.start(), m.end()):
            continue
        return True
    return False


# Маркеры «цена/стоимость … то» — отдельное слово «то», не «цена только» (15652).
_REGULATORY_TO_PRICE_WORD_MARKERS = frozenset(
    {
        "стоимость то",
        "стоимость на то",
        "сколько стоит то",
        "цену на то",
        "цена то",
        "цену то",
        "уточнить цену на то",
        "узнать стоимость то",
        "узнать стоимость на то",
        "узнать цену на то",
    }
)


def explicit_to_marker_present(low: str, marker: str) -> bool:
    """Наличие маркера планового ТО; для порядковых «N-е то» — не префикс «второе тогда» и т.п."""
    if marker in _TO_ORDINAL_PHRASE_MARKERS:
        first, second = marker.split(" ", 1)
        pat = re.compile(rf"\b{re.escape(first)}\s+{re.escape(second)}\b")
        for m in pat.finditer(low):
            if _ordinal_before_to_stt_match_is_false_positive(low, m):
                continue
            return True
        return False
    if marker == "техническое обслуживание":
        return _regulatory_technical_service_mention_present(low)
    # Падежи: «техобслуживанию» / «техобслуживанием» (17495) — не только номинатив.
    if marker == "техобслуживание":
        return bool(re.search(r"\bтехобслуживан\w*\b", low, re.I))
    if marker == "тех обслуживание":
        return bool(re.search(r"\bтех\s+обслуживан\w*\b", low, re.I))
    # «то-7» не внутри «то-75» (8826).
    if re.fullmatch(r"то-\d+", marker):
        m = re.search(rf"\b{re.escape(marker)}\b(?!\d)", low)
        if not m:
            return False
        return not _to_n_digit_marker_is_false_positive(low, m)
    if marker == "сделать то":
        return bool(
            re.search(rf"\bсделать\s+то\b{_SDELAT_TO_PARTICLE_FALSE_POSITIVE}", low, re.IGNORECASE)
        )
    # «то сделать» — не хвост «что-то сделать».
    if marker == "то сделать":
        return bool(re.search(r"(?<!что-)(?<!что\s)\bто\s+сделать\b", low, re.I))
    # «цена/стоимость то» — не подстрока «цена только» / «стоимость тогда» (15652).
    if marker in _REGULATORY_TO_PRICE_WORD_MARKERS:
        pat = r"\b" + re.escape(marker).replace(r"\ ", r"\s+") + r"(?!\w)"
        return bool(re.search(pat, low, re.I))
    if marker in ("то 0", "то-0"):
        # STT: «седьмое то 000» = 7 000 ₽, не маркер «ТО-0» (11996).
        pat = r"\bто\s*0\b" if marker == "то 0" else r"\bто\s*-\s*0\b"
        for m in re.finditer(pat, low):
            prefix = low[max(0, m.start() - 22) : m.start()]
            tail = low[m.end() : m.end() + 5].lstrip()
            if re.search(
                r"\b(?:пят|шест|седьм|восьм|девят|десят|перв|втор|трет|четвер|четвёрт)\w*\s*$",
                prefix,
                re.I,
            ):
                continue
            if re.match(r"0+\b", tail):
                continue
            return True
        return False
    return marker in low


# Упоминание уже пройденного / прошлого визита на ТО (не запись на новое ТО, если других маркеров нет).
_PAST_TO_REFERENCE_RE = re.compile(
    r"(?:"
    r"\b(?:заезз?жал|заезадал)[аи]?\s+на\s+техническ\w+|"
    r"\b(?:заезз?жал|заезадал)[аи]?\s+на\s+то\b|"
    r"\b(?:к\s+нам\s+)?(?:заезжал[аи]?|приезжал[аи]?|обслуживал[аи]?).{0,40}(?:да,?\s*)?делал[аи]?\s+техническ\w+|"
    r"\bделал[аи]?\s+техническ\w+\s+обслуживан\w+|"
    r"\bна\s+техническ\w+\s+обслуживан\w+\s+приезжал\w*|"
    r"\bбыли\s+на\s+то\b|"
    r"\bпроходил[аи]?(?:\s+на)?\s+то\b|"
    r"\bпройденн\w+\s+то\b|"
    r"\b(?:нулев\w+|перв\w+|втор\w+|трет\w+|треть\w+|четвер\w+|четвёрт\w+|пят\w+|шест\w+)\s+то\b"
    r"[^.!?]{0,90}?\b(?:проходил\w*|делал\w*|были|заезжал\w*)\b|"
    r"\b(?:проходил\w*|делал\w*|были|заезжал\w*)[^.!?]{0,90}?\b"
    r"(?:нулев\w+|перв\w+|втор\w+|трет\w+|треть\w+|четвер\w+|четвёрт\w+)\s+то\b|"
    r"\b(?:вы\s+)?то\s+проходил\w*\b"
    r")",
)

# Явная тема записи / цены / регламента ТО — не считать «только прошлый визит».
_STRONG_SCHEDULED_TO_SIGNAL_RES: Tuple[re.Pattern, ...] = (
    re.compile(r"\bзапис(?:аться|ь)\s+на\s+то\b"),
    # 9045: «записать машину на ТО» — не «записаться на то».
    re.compile(r"\bзапис(?:ать|аться|ыва\w*)\s+(?:машин\w+|автомобил\w+)?\s*на\s+то\b"),
    re.compile(r"\bзапись\s+на\s+то\b"),
    re.compile(r"\bзаписал[аи]?\s+вас\s+на\s+то\b"),
    re.compile(r"\bзапиш(?:у|ем)\s+на\s+то\b"),
    re.compile(r"\bзаписываю\s+на\s+то\b"),
    re.compile(r"\bхочу\s+записаться\s+на\s+то\b"),
    re.compile(r"\bнужно\s+записаться\s+на\s+то\b"),
    re.compile(r"\bможно\s+записаться\s+на\s+то\b"),
    re.compile(r"\bпредлагаю\s+записаться\s+на\s+то\b"),
    re.compile(r"\bстоимость\s+то\b"),
    re.compile(r"\bстоимость\s+на\s+то\b"),
    re.compile(r"\bсколько\s+стоит\s+то\b"),
    # STT 11291: «сколько будет стоить ТО80 000» после нормализации → «стоимость то 80 000».
    re.compile(
        r"\b(?:сколько|стоимост\w*|стоит).{0,50}?\bто\s+\d{2,3}\s+000\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bцен[ауы]\s+на\s+то\b"),
    re.compile(r"\bуточнить\s+цену\s+на\s+то\b"),
    re.compile(r"\bузнать\s+стоимость\s+то\b"),
    re.compile(r"\bузнать\s+стоимость\s+на\s+то\b"),
    re.compile(r"\bузнать\s+цену\s+на\s+то\b"),
    re.compile(r"\bразмер\s+то\b"),
    re.compile(r"\bнулевое\s+то\b"),
    re.compile(r"\bнулевом\s+то\b"),
    re.compile(r"\bна\s+нулевом\s+то\b"),
    # Пробег ТО: «ТО 60 000 км»; без «км/km» чаще шумовая цена «то 3 500 ₽» (9283, 11996, 15825).
    re.compile(r"\bто\s+\d{1,3}\s+\d{3}\s*(?:км|km)\b", re.IGNORECASE),
    # STT 9603: «Пиот 150 000 км» / «узнать … то … 150 000» после нормализации пиот→то.
    re.compile(r"\bто\s+150\s+000\s*(?:км|km)?\b", re.IGNORECASE),
    re.compile(
        r"\b(?:узнать|уточнить|хотел\w*)\b[^.!?]{0,120}?\bто\b[^.!?]{0,80}?150\s+000",
        re.IGNORECASE,
    ),
    re.compile(r"\bплановое\s+то\b"),
    re.compile(r"\bрегламентное\s+то\b"),
    re.compile(r"\bпериодическое\s+то\b"),
    re.compile(r"\bтехосмотр\b"),
    re.compile(r"\bплановое\s+обслуживание\b"),
    re.compile(r"\bпо\s+регламенту\s+то\b"),
    re.compile(r"\bинтервал\s+то\b"),
    re.compile(r"\bсрок\s+то\b"),
    re.compile(r"\bпо\s+сроку\s+то\b"),
    re.compile(r"\bна\s+то\s+записаться\b"),
    re.compile(r"\bна\s+то\s+записать\b"),
    # 10727 / 10930: «на ТО хотели/хотите записаться» — CRM-скрипт диспетчера.
    re.compile(r"\bна\s+то\s+хот\w*\s+запис", re.I),
    # 10930: «сколько займёт время ТО» (STT «Тго» → «то»).
    re.compile(r"\bсколько\s+[^.!?]{0,40}?\bвремя\s+то\b", re.I),
    # 12778: подтверждение слота на ТО — «ТО будет длиться …», «при ТО всё сделаем» (кампания/доработки).
    re.compile(r"\bто\s+будет\s+длиться\b", re.I),
    re.compile(r"\bпри\s+то\s+вс[её]\s+сдел", re.I),
    re.compile(r"\bна\s+то\s+заехать\b"),
    re.compile(r"\bзаехать\s+на\s+то\b"),
    re.compile(r"\bк\s+вам\s+на\s+то\b"),
    re.compile(r"\bприглас(?:ить|ите)\s+на\s+то\b"),
    re.compile(r"\bприглашаем\s+пройти\s+то\b"),
    re.compile(r"\bпройти\s+техническое\s+обслуживание\b"),
    re.compile(r"\bпройти\s+то\b"),
    # STT 10155: «нам надо ТО пройти» — перестановка слов.
    re.compile(r"\bто\s+пройти\b"),
    re.compile(r"\b(?:надо|нужно)\s+то\s+пройти\b"),
    # 10182: «планировал работы там ТО» (регламент у стороннего/дилера), не частица «то есть».
    re.compile(r"\bпланировал\w*\s+работ\w*\s+там\s+то\b", re.IGNORECASE),
    re.compile(
        r"\bпланировал\w*[^.!?]{0,120}?\bто\b(?!\s+есть\b)",
        re.IGNORECASE,
    ),
    # «ТО на … у вас можно провести?» — порядок слов не «на то»; симметрия с «пройти то».
    re.compile(r"\bпровести\s+то\b"),
    re.compile(r"\bпроведем\s+то\b"),
    re.compile(r"\bпроведём\s+то\b"),
    re.compile(r"\bто\b[^.!?\n]{0,120}\bможно\s+провести\b"),
    # «ТО на Ниссан/машину/…» без привязки к марке; отсекаем типичные «то на самом деле» и т.п.
    # (?<![а-яёa-z0-9-]) — не «почему-то на …» / «что-то на …» (8375).
    # Не «то на диспетчера/кого-то» — союз «то… то…» при переводах на линии (12658).
    re.compile(
        r"(?<![а-яёa-z0-9-])то\s+на\s+(?!самом\b|деле\b|этом\b|этот\b|этой\b|этому\b|этого\b|"
        r"сегодня\b|завтра\b|послезавтра\b|неделе\b|неделю\b|месяце\b|дне\b|"
        r"числ[ео]\b|утро\b|вечер[ае]?\b|времени\b|случай\b|раз\b|минут[еу]\b|"
        r"час[ауе]?\b|день\b|дня\b|днях\b|"
        r"диспетчер\w*|кого(?:-то)?|администратор\w*|оператор\w*|ассистент\w*)\b"
        r"[а-яёa-z0-9-]{3,}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bпроходите\s+то\b"),
    re.compile(r"\bпрохождение\s+то\b"),
    re.compile(rf"\bсделать\s+то\b{_SDELAT_TO_PARTICLE_FALSE_POSITIVE}", re.IGNORECASE),
    re.compile(r"\bто\s+уже\s+надо\s+делать\b", re.IGNORECASE),
    re.compile(r"\bполное\s+то\b"),
    re.compile(r"\bкомплексн\w+\s+то\b"),
    re.compile(r"\bчетверт\w+\s+то\b"),
    re.compile(r"\bчетвёрт\w+\s+то\b"),
    # STT 9163: «О4, сколько будет» = стоимость 4-го ТО (после _normalize_text → «то-4»).
    re.compile(
        r"\b(?:сколько|подскаж\w*|узнать|стоимост|цен\w*).{0,100}?\bто\s*[-]?\s*4\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:хотел[аи]|хочу)\s+то\s+сделать\b", re.IGNORECASE),
    re.compile(r"\bсначала\s+то\s+потом\s+диагноз", re.IGNORECASE),
    re.compile(r"\bто\s+проводится\b", re.IGNORECASE),
    re.compile(r"\b(?:пят|шест|седьм|восьм|девят|десят)\w*\s+то\b"),
    # Не «где-то 3 дня» / «где то 5» (STT срок поставки) — иначе ложный ТО-N (8524).
    # Не «то 3 500» (союз «то» + цена) — 9283.
    re.compile(r"(?<!где[-\s])\bто[-\s]([01234567])(?!\s*\d)"),
    re.compile(r"\bто\s+0\b"),
    re.compile(r"\bвторое\s+техническое\b"),
    re.compile(r"\bтретье\s+техническое\b"),
    re.compile(r"\bпервое\s+техническое\b"),
    re.compile(r"\bподходит\s+второе\b"),
    re.compile(r"\bподходит\s+третье\b"),
    re.compile(r"\bподходит\s+первое\b"),
    re.compile(r"\bприближается\s+второе\b"),
    re.compile(r"\bприближается\s+третье\b"),
    re.compile(r"\bпланируете\s+проходить\b"),
    re.compile(r"\bпланируете\s+пройти\b"),
    re.compile(r"\bбудете\s+проходить\b"),
    # STT 10330: «очередное ТО пройти»; «объёмное/необъёмное ТО» в регламенте.
    re.compile(r"\bочередн\w*\s+то\s+пройти\b", re.IGNORECASE),
    # STT 12462: «записаться на очередное ТО» (не «записи то на очередное», 9467).
    re.compile(r"\bзапис\w*\s+на\s+очередн\w*\s+то\b", re.IGNORECASE),
    re.compile(r"\b(?:не)?объемн\w*\s+то\b", re.IGNORECASE),
    # STT: порядковое ТО как «на 3- то …» / явная формулировка диспетчера (7958).
    re.compile(r"\bна\s+\d+\s*-\s*то\b"),
    re.compile(r"\bто\s+необходимо\s+записать\b"),
)


def past_to_reference_present(low: str) -> bool:
    """Есть ли в тексте формулировка про уже совершённый визит на ТО (см. _PAST_TO_REFERENCE_RE)."""
    return bool(_PAST_TO_REFERENCE_RE.search(low))


def _technical_service_mention_is_past_visit_reference(
    low: str, match_start: int, match_end: int
) -> bool:
    """
    «техническ… обслуживан…» в контексте уже совершённого визита (8025, 11708).
    Не путать с CRM-вопросом «ранее обслуживались?» + новая запись на ТО дальше по тексту.
    """
    pst = low[max(0, match_start - 120) : match_start]
    after = low[match_end : match_end + 32].lstrip()
    window = low[max(0, match_start - 150) : match_end + 80]
    if re.search(r"\bменял\w*\b", pst) and re.search(r"\b(?:был|это\s+был)\b", pst[-55:]):
        return True
    if re.search(r"\bистор\w+\s+загруз", window):
        return True
    # 16976: «именно техническое обслуживание» + «узнать … масла меняли» — уточнение прошлого визита.
    if re.search(r"\bименно\s+техническ", window, re.I) and re.search(
        r"\b(?:масл\w*\s+менял|менял\w*\s+масл|узнать|подскаж)\b",
        (low or "")[match_end : match_end + 140],
        re.I,
    ):
        return True
    # «сейчас историю открою» — CRM перед записью на ТО, не прошлый визит (15657).
    if re.search(r"\bистор\w+\s+откро", pst[-48:], re.I):
        after_sched = low[match_end : match_end + 100]
        if re.search(
            r"\b(?:получается|у\s+вас|будет|нужно|надо|запис|хотите|предлож|ближайш|то\s*[-]?\s*\d)\b",
            after_sched,
            re.I,
        ):
            return False
    if re.search(
        r"\b(?:истор\w+|ноябр|апрел|декабр|март|самар|раньше|2019|2020|2021|2022|2023|2024|2025)\b",
        window,
        re.I,
    ):
        return True
    if re.search(
        r"(?:заезжал[аи]?|зезжал[аи]?|приезжал[аи]?|проходил[аи]?)\s+на\s*$",
        pst[-90:],
    ) and after.startswith("да"):
        return True
    # 11708: «к нам заезжали, да, делали техническое обслуживание» — прошлый визит в истории.
    if re.search(
        r"(?:к\s+нам\s+)?(?:заезжал[аи]?|приезжал[аи]?|обслуживал[аи]?).{0,40}(?:да,?\s*)?делал[аи]?\s*$",
        pst[-120:],
        re.I,
    ):
        return True
    if re.search(r"\bделал[аи]?\s*$", pst[-25:], re.I):
        return True
    # 13781: «заезжали на техническое обслуживание 12-го» — прошлый визит, не новая запись на ТО.
    if re.search(r"(?:заезжал\w*|приезжал\w*|были)\s+на\s*$", pst[-45:], re.I):
        return True
    # 17539: «вы были, да, на техническое обслуживание» / «были недавно на …» — прошлый визит.
    if re.search(
        r"\b(?:были|был[аи]?|заезжал\w*|приезжал\w*|проходил\w*)\b"
        r"(?:[^.!?]{0,40}?)\bна\s*$",
        pst[-90:],
        re.I,
    ):
        return True
    # 14091: CRM «недавно проводили техническое обслуживание в анаавто» — история, не запись на ТО.
    if re.search(r"\bнедавно\s+проводил[аи]?\s*$", pst[-40:], re.I):
        return True
    after_ctx = low[match_end : match_end + 72]
    if re.search(r"\bпроводил[аи]?\s*$", pst[-24:], re.I) and re.search(
        r"\b(?:анаавто|анauto|не\s+у\s+(?:нас|вас)|сторонн|"
        r"друг\w+\s+(?:сервис|станц|дилер)|иного\s+дилер)\b",
        after_ctx,
        re.I,
    ):
        return True
    return False


def _past_to_visit_phrase_present(low: str) -> bool:
    """
    «были / были недавно / заезжали … на ТО|техническое обслуживание» —
    прошлый визит, не намерение новой записи (17539).
    """
    head = (low or "")[:4500]
    if not head.strip():
        return False
    if re.search(
        r"\b(?:заезжал[аи]?|приезжал[аи]?|проходил[аи]?|были|был[аи]?|проводил[аи]?)\b"
        r"[^.!?]{0,80}\bна\b[^.!?]{0,40}\bто\b",
        head,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:заезжал[аи]?|приезжал[аи]?|проходил[аи]?|были|был[аи]?)\b"
        r"[^.!?]{0,80}\bна\b[^.!?]{0,40}\bтехническ\w+\s+обслуживан",
        head,
        re.I,
    ):
        return True
    if re.search(r"\b(?:в\s+\w+\s+)?то\b[^.!?]{0,40}\bделал(?:и|а)?\b", head, re.I):
        return True
    if re.search(r"\bделал(?:и|а)?\b[^.!?]{0,40}\bто\b", head, re.I):
        return True
    if re.search(r"\bпосле\s+(?:то\b|техническ\w+\s+обслужив)", head, re.I):
        return True
    return False


def _has_new_regulatory_to_booking_or_price_intent(low: str) -> bool:
    """Явная новая запись или цена/состав регламентного ТО (не прошлый визит)."""
    if _is_inbound_opening_regulatory_to_booking(low):
        return True
    if _regulatory_to_price_or_composition_context(low):
        return True
    head = (low or "")[:2200]
    if re.search(
        r"\bзапис(?:аться|ать|ыва\w*)\s+(?:машин\w+|автомобил\w+)?\s*на\s+"
        r"(?:то\b|техническ|техобслуж|(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то)\b",
        head,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:хотел[аи]|хочу|нужно|надо)\b[^.!?]{0,80}\bзапис\w*\s+на\s+"
        r"(?:то\b|техническ|(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то)\b",
        head,
        re.I,
    ):
        return True
    # 21150: «хотел записать Ниссан НТО(на ТО)» — глагол «записать» без «-ся».
    if re.search(
        r"\b(?:хотел[аи]?|хочу)\b[^.!?]{0,80}\bзапис(?:ать|ыва\w*)\b"
        r"[^.!?]{0,120}\b(?:на\s+)?(?:то\b|техническ|техобслуж)\b",
        head,
        re.I,
    ):
        return True
    # 17962: «записаться хотел на ТО» — перестановка слов (не «хотел записаться»).
    if re.search(
        r"\bзапис(?:аться|ать|ыва\w*)[^.!?]{0,24}?\s+на\s+"
        r"(?:то\b|техническ|техобслуж|(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то)\b",
        head,
        re.I,
    ):
        return True
    # 18144: «на техобслуживание записать» / «машину на техобслуживание записать».
    if re.search(
        r"\bна\s+(?:техобслуж\w*|техническ\w+\s+обслуживан\w*|то)\b[^.!?]{0,50}?\bзапис",
        head,
        re.I,
    ):
        return True
    # 18185: «техобслуживание 23. Записать хотели бы автомобиль?»
    if re.search(
        r"\b(?:техобслуж\w*|техническ\w+\s+обслуживан\w*)\b[^.!?]{0,60}"
        r"\bзапис(?:ать|ыва\w*)?\s+хотел",
        head,
        re.I,
    ):
        return True
    if re.search(
        r"\bзапис(?:ать|ыва\w*)?\s+хотел\w*\s+(?:бы\s+)?автомобил",
        head,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:сколько|стоимост|стоит|сориентир\w*)\b[^.!?]{0,80}\b(?:то\b|техническ\w+\s+обслуживан)",
        head,
        re.I,
    ):
        return True
    return False


def _is_past_to_followup_without_new_to_booking(low: str) -> bool:
    """
    17539: прошлый визит на ТО без новой записи/цены — follow-up, не регламентное ТО.
    """
    if not _past_to_visit_phrase_present(low):
        return False
    if re.search(r"\bбыл[аи]?\s+записан[аы]?\s+на\s+(?:сервис|то)\b", low, re.I) and re.search(
        r"\b(?:можно\s+поменять|ошибочно\s+записал|поправл)\b",
        low,
        re.I,
    ):
        return False
    # 21160: даже при отсылке к прошлому ТО сохраняем новую запись,
    # если в этом же звонке согласовали новый слот (дата/время + «вас записали»).
    head = (low or "")[:5200]
    has_new_slot_now = bool(
        re.search(
            r"\b(?:записал[аи]\s+(?:вас\s+)?на|вас\s+записали|"
            r"записываем(?:ся|\s+вас)?|записал\w*сь|записались|"
            r"предварительно[^.!?]{0,80}\bзаписал\w*сь|"
            r"давайте(?:\s+в)?\s+\d{1,2}(?:[:.])\d{2}|будем\s+ожидать)\b",
            head,
            re.I,
        )
        and (
            re.search(
                r"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|сред[ау]|четверг|"
                r"пятниц|суббот|воскресень)\b",
                head,
                re.I,
            )
            or re.search(r"\b(?:в|на)\s+\d{1,2}(?:[:.])\d{2}\b", head, re.I)
            or re.search(
                r"\b(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\b",
                head,
                re.I,
            )
            or re.search(r"\b\d{1,2}\s+август\w*\b", head, re.I)
        )
    )
    if has_new_slot_now:
        return False
    # 22880: явная новая запись на диагностику (климат/симптомы) имеет приоритет
    # над контекстом «в марте делали N-е ТО».
    if _is_explicit_scheduled_diagnostics_intake(low):
        return False
    if _has_new_regulatory_to_booking_or_price_intent(low):
        return False
    return True


def _is_past_to_brake_wear_followup_not_narrow_to(low: str) -> bool:
    """
    Follow-up по ранее измеренному износу тормозов после прошлого ТО:
    «на ТО в марте», «остаточная толщина дисков/колодок».
    Это не новая запись на регламентное ТО.
    """
    head = (low or "")[:5000]
    if not head.strip():
        return False
    has_brake_wear = bool(
        re.search(r"\bостаточн\w*\s+толщин\w*", head, re.I)
        and re.search(
            r"\b(?:тормозн\w*\s+диск\w*|диск\w*\s+тормозн\w*|тормозн\w*\s+колод\w*|колод\w*)\b",
            head,
            re.I,
        )
    )
    if not has_brake_wear:
        return False
    has_past_to_context = bool(
        re.search(
            r"\b(?:на\s+(?:прошл\w*\s+)?то|после\s+то|на\s+техническ\w+\s+обслуживан\w*|"
            r"в\s+(?:марте|апреле|мае|июне|июле|августе|сентябре|октябре|ноябре|декабре))\b",
            head,
            re.I,
        )
        or re.search(r"\b(?:измерял\w*|измерили|был[аи]?\s+не\s+так\w+\s+больш\w*)\b", head, re.I)
    )
    if not has_past_to_context:
        return False
    # 21379: в звонке прямо считают очередное ТО (регламент + стоимость),
    # а контекст «остаточная толщина» — лишь справочное уточнение.
    if re.search(r"\bименно\s+техническ\w+\s+обслуживан\w*\b", head, re.I) and re.search(
        r"\b(?:стоимост\w*|сколько\b|что\s+входит\b|очередн\w*)\b",
        head,
        re.I,
    ):
        return False
    # Если в этом же звонке явно согласовали новый слот, не перехватываем.
    has_new_slot_now = bool(
        re.search(
            r"\b(?:запиш(?:ем|у|итесь|емся)|записал[аи]\s+(?:вас\s+)?на|"
            r"давайте\s+запиш|подходит|устраивает|договорились)\b",
            head,
            re.I,
        )
        and (
            re.search(
                r"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|сред[ау]|четверг|"
                r"пятниц|суббот|воскресень)\b",
                head,
                re.I,
            )
            or re.search(r"\b(?:в|на)\s+\d{1,2}(?:[:.]\d{2})?\b", head, re.I)
            or re.search(
                r"\b(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\b",
                head,
                re.I,
            )
        )
    )
    return not has_new_slot_now


def _is_immediate_repeat_continuation_without_new_slot(low: str) -> bool:
    """
    Продолжение только что оборванного разговора на линии сервиса:
    «только что говорили с вами» + уточнение уже обсуждённого ТО,
    без нового согласования слота в этом звонке.

    20168: не новая запись на ТО, а повторный контакт (НЕ_ТО).
    """
    head = (low or "")[:1400]
    if not head.strip():
        return False
    immediate_repeat = bool(
        re.search(r"\bтолько\s+что\s+говорил\w*\s+с\s+вами\b", head, re.I)
        or re.search(r"\bмы\s+только\s+что\s+говорил\w*\b", head, re.I)
        or re.search(r"\b(?:я\s+)?(?:вот\s+)?(?:во\s+)?сейчас\s+с\s+вами\s+(?:общал\w*|разговаривал\w*)\b", head, re.I)
        or re.search(r"\b(?:я\s+)?(?:вот\s+)?(?:во\s+)?сейчас\s+(?:общал\w*|разговаривал\w*)\s+с\s+вами\b", head, re.I)
        or re.search(r"\bсвяз\w*\s+прервал\w*[^.!?]{0,40}\bпродолж", head, re.I)
        or re.search(r"\bпродолж\w*[^.!?]{0,40}\b(?:предыдущ\w*|прошл\w*)\s+разговор", head, re.I)
    )
    if not immediate_repeat:
        return False

    # Нет нового выбора/подтверждения даты-времени слота в этом звонке.
    has_new_slot_now = bool(
        re.search(
            r"\b(?:запиш(?:ем|у|итесь|емся)|записал[аи]\s+(?:вас\s+)?на|"
            r"давайте\s+запиш|подходит|устраивает|договорились)\b",
            head,
            re.I,
        )
        and (
            re.search(
                r"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|сред[ау]|четверг|"
                r"пятниц|суббот|воскресень)\b",
                head,
                re.I,
            )
            or re.search(r"\b(?:в|на)\s+\d{1,2}(?:[:.]\d{2})?\b", head, re.I)
            or re.search(
                r"\b(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\b",
                head,
                re.I,
            )
        )
    )
    if has_new_slot_now:
        return False
    return True


def _client_regulatory_to_booking_request_present(low: str) -> bool:
    """
    Клиент явно просит записаться на регламентное ТО (не CRM-история и не смета в хвосте).
    18150: «мне бы на ТО записаться» при отказе дилера по марке — вид работ ТО.
    """
    if _is_inbound_opening_regulatory_to_booking(low):
        return True
    head = (low or "")[:2200]
    return bool(
        re.search(r"\bна\s+то\b[^.!?]{0,50}?\bзапис", head, re.I)
        or re.search(r"\bзапис\w*\s+на\s+то\b", head, re.I)
        or re.search(
            r"\bна\s+(?:техобслуж\w*|техническ\w+\s+обслуживан\w*)\b[^.!?]{0,50}?\bзапис",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:мне\s+бы|хотел[аи]?|хочу|надо|нужно)\b[^.!?]{0,80}\bна\s+то\b",
            head,
            re.I,
        )
    )


def _work_type_aligned_when_narrow_not_to(
    work_type: Optional[str],
    low: str,
    *,
    evidence: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Узкая рубрика НЕ_ТО не должна перетирать суть диалога:
    если work_type уже определен как «to», сохраняем «to».
    Доп. эвристики ниже используются только когда исходный work_type пустой/неопределенный.
    """
    ev = dict(evidence or {})
    wt = (work_type or "").strip().lower()
    if wt:
        if wt == "to" and not ev.get("work_intent"):
            ev["work_intent"] = "to_kept_despite_narrow_not_to"
        return wt, ev
    # Для явных не-ТО сигналов при пустом исходном виде работ подставляем профильную метку.
    if (
        _is_inbound_warranty_repair_booking_intake(low)
        or _is_inbound_warranty_defect_consultation_not_scheduled_to(low)
        or _is_warranty_decision_document_followup(low)
    ):
        ev["work_hit"] = ev.get("work_hit") or "warranty"
        ev["work_intent"] = "warranty_after_narrow_not_to"
        return "warranty", ev
    if "диагностик" in (low or "") or _is_primary_defect_diagnostic_service_intake(low):
        ev["work_hit"] = "diagnostics"
        ev["work_intent"] = "diagnostics_after_narrow_not_to"
        return "diagnostics", ev
    if _is_body_shop_service_intake(low) or _is_insurance_claim_body_inspection_intake(low):
        ev["work_hit"] = "body_shop"
        ev["work_intent"] = "body_shop_after_narrow_not_to"
        return "body_shop", ev
    ev["work_hit"] = ev.get("work_hit") or "narrow_not_to"
    ev["work_intent"] = "other_after_narrow_not_to"
    return "other_work", ev


def align_sto_work_type_with_narrow_rubric(
    work_type: Optional[str],
    rubric_type: Optional[str],
    low: str,
    *,
    evidence: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Согласование вида работ с узкой рубрикой:
    - СТО_ТО_вх/исх → work_type = to (17577);
    - НЕ_ТО не сбрасывает уже определенный вид работ (в т.ч. `to`).
    """
    rub = (rubric_type or "").strip().upper()
    ev = dict(evidence or {})
    if rub in ("STO_TO_IN", "STO_TO_OUT"):
        if (work_type or "").strip().lower() == "to":
            return "to", ev
        ev["work_hit"] = "to"
        ev["work_intent"] = "to_aligned_with_narrow_sto_to"
        return "to", ev
    if rubric_type is None:
        return _work_type_aligned_when_narrow_not_to(work_type, low, evidence=ev)
    return ((work_type or "").strip().lower() or "other_work"), ev


def _technical_service_mention_is_brand_capability_not_to_booking(
    low: str, match_start: int, match_end: int
) -> bool:
    """
    «техническое обслуживание проводим, масло меняем» — ответ «обслуживаем бренд», не запись на ТО (16758).
    """
    after = (low or "")[match_end : match_end + 90].lstrip()
    window = (low or "")[max(0, match_start - 120) : match_end + 140]
    # Явная запись/прохождение регламентного ТО рядом — не capability.
    if re.search(
        r"\b(?:запис\w*|хотел\w*|нужно|надо|пройти|пройд\w*)\s+[^.!?]{0,40}техническ",
        window,
        re.I,
    ):
        return False
    if re.search(
        r"\bна\s+техническ\w+\s+обслуживан\w*[^.!?]{0,40}\bзапис",
        window,
        re.I,
    ):
        return False
    if re.search(r"\bстоимост\w*[^.!?]{0,40}\bтехническ", window, re.I):
        return False
    # «ТО проводим» / «проводим, в принципе. масло меняем»
    if re.match(r"проводим\b", after, re.I):
        return True
    if re.match(r"делаем\b", after, re.I) and re.search(r"\bмасл\w*\s+меня", window, re.I):
        return True
    return False


def _technical_service_mention_is_power_of_attorney_template(
    low: str, match_start: int, match_end: int
) -> bool:
    """
    «…доверяет вас… провести техническое обслуживание…» — шаблон доверенности, не запись на ТО (15767).
    """
    pst = (low or "")[max(0, match_start - 220) : match_start]
    window = (low or "")[max(0, match_start - 80) : match_end + 40]
    if re.search(r"\bдоверен", pst + window, re.I):
        return True
    if re.search(r"прост\w*\s+письменн", pst, re.I):
        return True
    if re.search(r"от\s+собственник", pst, re.I) and re.search(r"\bдовер", window, re.I):
        return True
    return False


def all_technical_service_mentions_are_past_visit_confirmation(low: str) -> bool:
    """
    Все фрагменты «техническ… обслуживан…» — только уточнение уже пройденного визита:
    «…заезжали на» + «да…» (8025) или «…заезжали, да, делали…» (11708).
    Не путать с 8024: там после фразы идёт «было…», не подтверждение «да».
    """
    ms = list(re.finditer(r"\bтехническ\w+\s+обслуживан\w+", low))
    if not ms:
        return False
    for m in ms:
        if not _technical_service_mention_is_past_visit_reference(low, m.start(), m.end()):
            return False
    return True


def _explicit_new_ordinal_to_booking_intent_present(low: str) -> bool:
    """«хочу/записаться на третье то» — новая запись, не CRM-история прошлого ТО."""
    return bool(
        re.search(
            r"\b(?:хочу|хотел\w*|нужно|надо|можно)\s+[^.!?]{0,55}?\b(?:запис\w*\s+)?(?:на\s+)?"
            r"(?:нулев\w+|перв\w+|втор\w+|трет\w+|треть\w+|четвер\w+|четвёрт\w+)\s+то\b",
            low,
            re.I,
        )
        or re.search(
            r"\bзапис\w*\s+на\s+"
            r"(?:нулев\w+|перв\w+|втор\w+|трет\w+|треть\w+|четвер\w+|четвёрт\w+)\s+то\b",
            low,
            re.I,
        )
        or re.search(r"\b(?:надо|нужно|мне\s+надо)\s+то\s+проход", low, re.I)
    )


def _ordinal_zero_to_hit_is_past_service_history_only(low: str, m: re.Match) -> bool:
    """
    «нулевое то … проходила» в CRM — история обслуживания, не сигнал новой записи на регламентное ТО.
    Не отсекать явный запрос «хочу на третье то» / «записаться на нулевое то».
    """
    g = (m.group(0) or "").lower()
    if not re.search(
        r"\b(?:нулев\w+|перв\w+|втор\w+|трет\w+|треть\w+|четвер\w+|четвёрт\w+)\s+то\b"
        r"|\bна\s+нулевом\s+то\b"
        r"|\bнулевом\s+то\b",
        g,
    ):
        return False
    if _explicit_new_ordinal_to_booking_intent_present(low):
        return False
    narrow = low[max(0, m.start() - 45) : m.end() + 55]
    if re.search(
        r"\b(?:проходил\w*|делал\w*|были|заезжал\w*|обслуживал\w*)\b",
        narrow,
        re.I,
    ):
        return True
    if past_to_reference_present(low):
        return True
    return False


def _ordinal_to_marker_is_service_history_reference(low: str, marker: str) -> bool:
    """
    «шестое то» / «на то 40» в рассказе про прошлые визиты и гарантию — не запись на новое ТО (9727).
    """
    if marker not in _TO_ORDINAL_PHRASE_MARKERS:
        return False
    # 12788: в исходящем CRM-контуре могут одновременно звучать
    # «в прошлом году» (история) и новая фиксация слота «вас записываем».
    # В таком случае это не purely-history, не блокируем маркер ТО.
    if re.search(
        r"\b(?:записыва\w*|записал[аи]?\s+вас|можем\s+записаться|"
        r"предварительно\s+договорились|будем\s+ожидать)\b",
        low,
        re.I,
    ) and (
        re.search(
            r"\bна\s+\d{1,2}[\s-]*(?:е|го)?\s+числ\w*\b",
            low,
            re.I,
        )
        or re.search(r"\b\d{1,2}[:\s.]\d{2}\b", low, re.I)
    ):
        return False
    if _explicit_new_ordinal_to_booking_intent_present(low):
        return False
    first, second = marker.split(" ", 1)
    pat = re.compile(rf"\b{re.escape(first)}\s+{re.escape(second)}\b")
    for m in pat.finditer(low):
        window = low[max(0, m.start() - 95) : m.end() + 95]
        narrow = low[max(0, m.start() - 45) : m.end() + 55]
        if re.search(
            r"\b(?:проходил\w*|делал\w*|был[аи]?|были|заезжал\w*|обслуживал\w*|прохож\w*)\b",
            narrow,
            re.I,
        ):
            return True
        if any(
            x in window
            for x in (
                "истори",
                "открою",
                "ноябр",
                "апрел",
                "раньше",
                "было",
                "был ",
                "была",
                "были",
                "меняли",
                "прошл",
                "2024",
                "2025",
                "марте вы",
                "заезжаете на то",
                "на то 40",
                "на то 60",
                "на то 100",
                "значит это",
                "наряд",
                "допуск",
                "запросил",
                "заказнаряд",
                "заказ-наряд",
            )
        ):
            return True
    return False


def _ordinal_to_past_complaint_blocks_scheduled_to(low: str) -> bool:
    """«шестом то даже не сделали» — не сигнал записи на регламентное ТО (8853)."""
    if not re.search(r"\b(?:пят|шест|седьм|восьм|девят|десят)\w*\s+то\b", low):
        return False
    if re.search(r"\bзапис(?:аться|ь)\s+на\s+то\b", low):
        return False
    for m in re.finditer(
        r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят)\w*\s+то\b",
        low,
    ):
        window = low[max(0, m.start() - 35) : m.end() + 90]
        if re.search(
            r"(?:даже\s+)?не\s+(?:сделал|адаптир|провел|провёл|выполн|поменял|отрегулир)",
            window,
            re.I,
        ):
            return True
    return False


def _ordinal_before_to_stt_match_is_false_positive(low: str, m: re.Match) -> bool:
    """
    STT: «до половины седьмого, то есть» / «четверг, то есть» — не «N-е ТО» (10256, 10260).
    """
    tail = (low or "")[m.end() : m.end() + 10].lstrip()
    if tail.startswith("есть"):
        return True
    prefix = (low or "")[max(0, m.start() - 55) : m.start()]
    if re.search(r"половин\w*\s*$", prefix, re.I):
        return True
    if re.search(r"\bдо\s+половин\w*\s*$", prefix, re.I):
        return True
    # 13554: «останется только второй, то есть» — второй специалист, не «второе ТО».
    if re.search(
        r"\b(?:только|останется|остался|остается|один)\s+втор\w*\s*$"
        r"|\bвтор\w*\s+специалист",
        prefix,
        re.I,
    ):
        return True
    # STT: «седьмое то 000» = 7 000 ₽ за работу, не «7-е ТО» (11996).
    if re.search(
        r"\b(?:пят|шест|седьм|восьм|девят|десят)\w*\s+то\b",
        (low or "")[m.start() : m.end()],
        re.I,
    ):
        tail = (low or "")[m.end() : m.end() + 14].lstrip()
        if (
            re.match(r"0{2,}\b", tail)
            or re.match(r"\d{3,}\b", tail)
            or re.match(r"0\s+000\b", tail)
        ):
            return True
    # 15679: «планировали время, то есть ТО, плюс запчасти» — «то есть», не запись на ТО.
    prefix_to_est = (low or "")[max(0, m.start() - 20) : m.start()]
    if re.search(r"то\s+есть\s*$", prefix_to_est, re.I):
        return True
    return False


def _to_n_digit_marker_is_false_positive(low: str, m: re.Match) -> bool:
    """
    STT: «смотреть-то 5 минут» — частица + длительность, не «ТО-5» (10870).
    «то 3 500» — союз + цена (9283).
    «6 июня, то 5- июня» — частица «то» + дата, не «ТО-5» (12327).
    """
    g = (m.group(0) or "").lower()
    if not re.search(r"(?<!где[-\s])\bто[-\s]([01234567])(?!\s*\d)", g):
        return False
    start, end = m.start(), m.end()
    prefix = (low or "")[max(0, start - 45) : start]
    tail = (low or "")[end : min(len(low or ""), end + 32)]
    if re.search(
        r"(?:смотрет|посмотр|оцен|загон|протер|там|о\s+большом\s+счет)\w*[-\s]?то\s*$",
        prefix,
        re.I,
    ):
        return True
    if re.search(r"^\s*(?:минут|мин\b|час|сек|\d+\s*(?:минут|мин\b|час))", tail, re.I):
        return True
    if re.search(r"^\s+\d{3,}", tail):
        return True
    _month = (
        r"(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр|числ)"
    )
    if re.search(rf"^\s*(?:-|–)?\s*(?:\d{{1,2}}\s*)?{_month}", tail, re.I):
        return True
    if re.search(rf"\b\d{{1,2}}\s+{_month}\w*,?\s*$", prefix, re.I):
        return True
    return False


def _scheduled_to_n_digit_regex_hit_is_false_positive(low: str, m: re.Match) -> bool:
    """
    STT: «где-то 4,5» (пробег), «Tenet T4» — не стоимость/номер регламентного ТО (13517).
    """
    for sub in re.finditer(r"\bто\s*[-]?\s*([0-9])\b", m.group(0) or "", re.I):
        abs_start = m.start() + sub.start()
        abs_end = m.start() + sub.end()
        prefix = (low or "")[max(0, abs_start - 14) : abs_start]
        tail = (low or "")[abs_end : min(len(low or ""), abs_end + 16)]
        if re.search(
            r"(?:где|что|почему|как|это|ничего|чего|того|сего|него|любого|иного)[-\s]?$",
            prefix,
            re.I,
        ):
            return True
        if re.match(r"\s*(?:[,.]?\s*\d\b|тыс|км|km|лет|год|pro|бел|цвет)", tail, re.I):
            return True
        window = (low or "")[max(0, abs_start - 28) : abs_end + 4]
        if re.search(r"\b(?:tenet|tiggo|chery|черри|тенет)\s+t\s*$", window, re.I):
            return True
    return False


def _regulated_to_signal_match_is_negated_or_excluded(low: str, m: re.Match) -> bool:
    """
    STT: «без то на который я записался», «только про стекло» — не маркер регламентного ТО (10568).
    16946: «на ТО, на колодки» после снятия запятой → «на то на колодки» — не «ТО на …».
    """
    start, end = m.start(), m.end()
    prefix = (low or "")[max(0, start - 55) : start]
    window = (low or "")[max(0, start - 55) : min(len(low or ""), end + 55)]
    if re.search(r"\b(?:без|не\s+про|не\s+о|исключая)\s+(?:то\b|техническ)", prefix, re.I):
        return True
    if re.search(r"\bбез\s+то\s+на\b", window, re.I):
        return True
    if re.search(r"\bтолько\s+про\s+(?:стекл|замен|камер|запчаст|ремонт)\b", window, re.I):
        return True
    if re.search(r"\b(?:мы\s+)?(?:это\s+)?только\s+про\b", window, re.I):
        return True
    # «на то на колодки/работы…» — машина уже на ТО, уточнение работ, не «ТО на …».
    g0 = (m.group(0) or "").lower()
    if re.match(r"то\s+на\s+", g0) and re.search(r"\bна\s+$", prefix):
        return True
    # STT-склейка: «на какой срок … то есть» — не «срок ТО» (14807).
    tail = (low or "")[end : end + 12].lstrip()
    if tail.startswith("есть"):
        return True
    return False


def _strong_scheduled_to_regex_hit_valid(low: str, rx: re.Pattern) -> Optional[re.Match]:
    m = rx.search(low or "")
    if not m or _ordinal_to_past_complaint_blocks_scheduled_to(low):
        return None
    # 18588: после удаления пунктуации «если записаться, то на ближайшее»
    # выглядит как сильный маркер «ТО на ...», хотя «то» здесь условный союз.
    if re.match(r"\bто\s+на\s+ближайш", (m.group(0) or "").lower()):
        prefix = (low or "")[max(0, m.start() - 90) : m.start()]
        if re.search(
            r"\bесли\s+(?:запис\w*|подъех\w*|приех\w*)[^.!?]{0,50}$",
            prefix,
            re.I,
        ):
            return None
    if _regulated_to_signal_match_is_negated_or_excluded(low, m):
        return None
    # «…, то есть» / «срок то есть» — устойчивое «то есть», не регламентное ТО (10256, 14807).
    if _ordinal_before_to_stt_match_is_false_positive(low, m):
        return None
    g = (m.group(0) or "").lower()
    # 15679: «планировали … то есть ТО» — второе «то» после «то есть», не запись на ТО.
    if re.search(r"\bпланировал\w", g) and re.search(r"то\s+есть\s+то\b", g):
        return None
    if re.search(r"(?<!где[-\s])\bто[-\s]([01234567])(?!\s*\d)", g):
        if _to_n_digit_marker_is_false_positive(low, m):
            return None
    if re.search(r"\bто\s*[-]?\s*[0-9]\b", g):
        if _scheduled_to_n_digit_regex_hit_is_false_positive(low, m):
            return None
    if _ordinal_zero_to_hit_is_past_service_history_only(low, m):
        return None
    return m


def strong_scheduled_to_signal_present(low: str) -> bool:
    """
    Есть ли маркеры записи/стоимости/регламента ТО, не сводимые к одному только «прошлый визит».
    """
    if any(_strong_scheduled_to_regex_hit_valid(low, r) for r in _STRONG_SCHEDULED_TO_SIGNAL_RES):
        return True
    if _regulatory_technical_service_mention_present(low):
        return True
    if _mileage_to_interval_code_present(low):
        return True
    for om in _TO_ORDINAL_PHRASE_MARKERS:
        if explicit_to_marker_present(low, om):
            if _ordinal_to_past_complaint_blocks_scheduled_to(low):
                continue
            if _ordinal_to_marker_is_service_history_reference(low, om):
                continue
            return True
    return False


_ZERO_TO_TOPIC_MARKERS = ("нулевое то", "то нулевое", "то 0", "то-0")


def _zero_to_topic_mentioned(low: str) -> bool:
    if any(explicit_to_marker_present(low, m) for m in _ZERO_TO_TOPIC_MARKERS):
        return True
    return bool(
        re.search(r"\bнулев\w*\s+то\b", low, re.I)
        or re.search(r"\bто\s+нулев\w*\b", low, re.I)
        or re.search(r"\bна\s+нулев\w*\s+то\b", low, re.I)
        or re.search(r"\bнулевом\s+то\b", low, re.I)
    )


def _warranty_context_chery_tenet_default_brand(low: str) -> bool:
    """
    17077: «на гарантии» / «по гарантии» / «автомобиль на гарантии» — Chery/Tenet,
    если марка авто в разговоре не названа явно.
    """
    if not (low or "").strip():
        return False
    return bool(
        re.search(r"\bпо\s+гарантии\b", low, re.I)
        or re.search(r"\bна\s+гарантии\b", low, re.I)
        or re.search(r"\bавтомобил\w*\s+на\s+гарантии\b", low, re.I)
        or re.search(r"\b(?:авто|машин)\w*\s+на\s+гарантии\b", low, re.I)
    )


def _zero_to_booking_topic_present(low: str) -> bool:
    """
    Запись/перенос/цена нулевого ТО (ТО-0 / ТО0) — у дилера Chery/Tenet только его марки (16205).
    """
    if not _zero_to_topic_mentioned(low):
        return False
    return bool(
        any(m in low for m in _BOOKING_MARKERS)
        or strong_scheduled_to_signal_present(low)
        or re.search(
            r"\b(?:перенес|перенос\w*|переназнач|запис(?:аться|ать|ан|ыва)|слот)\b",
            low,
            re.I,
        )
        or re.search(r"\b(?:стоимост|сколько\s+стоит|цен\w*)\b", low, re.I)
        or re.search(r"\bто\s+необходимо\s+записать\b", low, re.I)
    )


_CRM_LEAD_RECEIVED_MARKERS = (
    "получили заявку",
    "получили вашу заявку",
    "заявку получили",
    "мы заявку получили",
    "мы вашу заявку получили",
    "мы получили заявку",
    "мы просто заявку получили",
    "просто заявку получили",
    "пришла заявка",
    "пришла заявк",
    "заявка пришла",
    "нам заявка пришла",
)

_CRM_OUTBOUND_EXPLICIT_TO_TOPIC_RES = (
    # 15770: «заявка пришла на ТО … автомобиль записать».
    re.compile(r"\bзаявк\w*\s+пришл\w*[^.!?]{0,40}?\bна\s+то\b", re.I),
    re.compile(
        r"\bзаявк\w*\s+пришл\w*[^.!?]{0,80}?\bавтомобил\w*\s+запис",
        re.I,
    ),
    # 13631 / 15657: «на ТО … записаться»; 15611: «на ТО необходимо вас записать».
    re.compile(r"\bна\s+то\b[^.!?]{0,80}?\bзапис", re.I),
    re.compile(r"\bна\s+то\b[^.!?]{0,40}?\bнеобходим\w*\s+[^.!?]{0,30}?\bзапис", re.I),
    # 15856: «на техническое обслуживание хотели бы записать автомобиль».
    re.compile(
        r"\bна\s+техническ\w+\s+обслужив\w*[^.!?]{0,80}?\b(?:хотел\w*|запис)\w*",
        re.I,
    ),
    # 15608: STT «Вы го» = «Вы ТО тоже будете делать?»
    re.compile(r"\bвы\s+го\b[^.!?]{0,60}?\bделать", re.I),
    re.compile(r"\bго\s+тоже\s+будете\s+делать", re.I),
    # 15940: «на второе ТО необходимо вас записать».
    re.compile(
        r"\b(?:на\s+)?(?:перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|нулев)\w*\s+то\b"
        r"[^.!?]{0,40}?\bнеобходим\w*\s+[^.!?]{0,30}?\bзапис",
        re.I,
    ),
    # 22213: «предыдущее ТО ... второе уже пройти» в исходящем перезвоне по заявке.
    re.compile(
        r"\bпредыдуще\w+\s+то\b[^.!?]{0,160}\b"
        r"(?:нулев\w+|перв\w+|втор\w+|трет\w+|четвер\w+|четвёрт\w+|пят\w+|шест\w+)\w*"
        r"[^.!?]{0,45}\bпройти\b",
        re.I,
    ),
)


def crm_outbound_explicit_to_topic_present(low: str, *, head_limit: int = 3200) -> bool:
    """Исх. CRM-перезвон: в заявке явная запись на регламентное ТО (15608, 15611)."""
    head = (low or "")[:head_limit]
    if not any(m in head for m in _CRM_LEAD_RECEIVED_MARKERS):
        return False
    return any(pat.search(head) for pat in _CRM_OUTBOUND_EXPLICIT_TO_TOPIC_RES)


def contains_any_to_marker_hit(low: str) -> Tuple[bool, str]:
    """
    Явное плановое ТО в тексте.

    Если встречаются только формулировки про уже пройденное ТО (заезжали/были/проходили/пройденное)
    и нет отдельных маркеров записи или иного регламентного ТО — не считаем явным ТО.
    """
    for rx in _STRONG_SCHEDULED_TO_SIGNAL_RES:
        m = _strong_scheduled_to_regex_hit_valid(low, rx)
        if m:
            return True, (m.group(0) or "").strip()[:48]
    hits = [m for m in _TO_MARKERS if explicit_to_marker_present(low, m)]
    if not hits:
        if _mileage_to_interval_code_present(low):
            return True, "mileage_to_interval"
        if crm_outbound_explicit_to_topic_present(low):
            return True, "crm_outbound_to_booking"
        # 16743: «на ТО машину записать» — «машину» между «на то» и «запис».
        if re.search(r"\bна\s+то\b[^.!?]{0,50}?\bзапис", low, re.I):
            return True, "на то запис"
        # 28645: STT «ТОО» вместо «ТО» в репликах про запись/состав визита.
        if re.search(r"\bкроме\s+тоо\b", low, re.I) and re.search(r"\bзапис\w*", low[:2000], re.I):
            return True, "кроме тоо"
        if re.search(r"\b(?:на\s+)?тоо\b[^.!?]{0,60}\b(?:запис|пробег|кроме)\w*", low, re.I):
            return True, "тоо"
        return False, ""
    if past_to_reference_present(low) and not strong_scheduled_to_signal_present(low):
        return False, ""
    for m in _TO_MARKERS:
        if explicit_to_marker_present(low, m):
            if m in _TO_ORDINAL_PHRASE_MARKERS and _ordinal_to_past_complaint_blocks_scheduled_to(low):
                continue
            if m in _TO_ORDINAL_PHRASE_MARKERS and _ordinal_to_marker_is_service_history_reference(low, m):
                continue
            return True, m
    return False, ""


# Замена масла / фильтров без явного планового ТО — не work_type «to» (аналитика / карточка).
_OIL_SERVICE_WITHOUT_SCHEDULED_TO_MARKERS = (
    "замена масла",
    "замену масла",
    "заменить масло",
    "масло поменять",
    "поменять масло",
    "моторного масла",
    "масло в двигателе",
    "масло в коробке",
    "масляного фильтра",
    "масляный фильтр",
    "масляный сервис",
    "замена масла в коробке",
    "замена масла в двигателе",
    "масло в вариатор",
    "замена масла в вариатор",
    "коробке передач",
    "только масло",
)

# Уточнение уже существующей записи — не плановое ТО на карточке.
_BOOKING_VERIFICATION_TOPIC_MARKERS = (
    "давайте проверим",
    "давай проверим",
    "проверим запись",
    "на какое всё-таки",
    "на какое все-таки",
    "на какое число",
    "на какую дату",
    "два числа",
    "две даты",
    "два даты",
    "сбой в программе",
    "две записи",
    "на то записывался",
    "всё в силе",
    "все в силе",
    "на какое время записан",
    "перенести запись",
    "перенос записи",
    "изменить время записи",
    "изменить время визита",
    "сдвинуть запись",
    "сдвинуть время",
    "другое время записи",
)


def _is_to_slot_availability_inquiry_not_booking_verify(low: str) -> bool:
    """
    Запрос ближайшего слота на ТО — не проверка уже существующей записи (12466).
    «на ТО ближайшая запись на какое число», «когда можно пройти ТО».
    """
    head = (low or "")[:1600]
    if re.search(
        r"\b(?:ближайш\w*\s+)?(?:запись|запис\w+)\s+на\s+как(?:ое|ую)\s+(?:число|дату|день|время)\b",
        head,
        re.I,
    ):
        return True
    if re.search(r"\bна\s+то\b[^.!?]{0,60}ближайш", head, re.I):
        return True
    if re.search(
        r"\bкогда\s+(?:можно|могу|могли\s+бы)\s+[^.!?]{0,50}\b(?:то|техническ\w+\s+обслуживан)\b",
        head,
        re.I,
    ):
        return True
    return False


_WARRANTY_MARKERS = (
    "гарантия",
    "гарантийный",
    "по гарантии",
    "гарантийн",
)

_BOOKING_MARKERS = (
    "запис",
    "подберем время",
    "подберем дату",
    "предлагаю время",
    "подтвердим запись",
    "запланируем визит",
    "на какое время",
    "на какую дату",
)

# Явный запрос диагностики / неисправности — не плановое ТО (даже если в речи есть частица «то»).
_DIAGNOSTICS_TOPIC_MARKERS = (
    "диагностик",  # также покрывает «диагностику», «диагностика»
    "осмотр перед ремонтом",
)
_DIAG_BOOKING_OR_SCHEDULE_HINTS = (
    "запис",
    "запиш",
    "назнач",
    "подберем",
    "подберём",
    "приём",
    "прием",
    "слот",
    "во сколько",
    "на понедельник",
    "на вторник",
    "на среду",
    "на четверг",
    "на пятниц",
    "на суббот",
    "на воскресенье",
)
_DIAG_SYMPTOM_MARKERS = (
    "проблем",
    "не работает",
    "поломк",
    "неисправн",
    "педаль",
    "газа",
    "газ ",
    "на ходу",
    "накатом",
    "на катом",
    "перестал",
    "перестает",
    "перестают",
    "откликаться",
    "откликается",
    "ошибк",
    "горит",
    "лампа",
    "чек еngine",
    "стук",
    "шум",
    "вибрац",
    "не едет",
    "едет просто",
    "check engine",
    "греется",
    "нагревается",
    "перегрев",
    "лампочк",
    "загорел",
)


def _is_planned_diagnostics_required(low: str) -> bool:
    """
    Линия фиксирует, что нужна/будет диагностика (9512: запланировать, потребуется).
    Вид работ — диагностика, даже при упоминании ТО вскользь.
    """
    return bool(
        re.search(
            r"\b(?:необходимо|нужно)\s+запланир\w*\s+диагностик",
            low,
            re.I,
        )
        or re.search(r"\bпотребуется\s+диагностик", low, re.I)
        or re.search(
            r"\b(?:надо|нужно)\s+(?:будет\s+)?(?:сделать|провести|назначить)\s+диагностик",
            low,
            re.I,
        )
    )


def _diagnostics_booking_intake_phrase_present(head: str) -> bool:
    """Запрос записи на диагностику (9283, 10547, 10615)."""
    h = (head or "")[:4000]
    if re.search(
        r"\bзапис\w*\s+на\s+диагностик|\bзапись\s+на\s+диагностик|"
        r"\bможно\s+запис\w*[^.!?]{0,45}?\s+на\s+диагностик|"
        r"\bмогу\s+ли[^.!?]{0,80}?\bна\s+диагностик\w*(?:[^.!?]{0,70})?\s+запис|"
        r"\bна\s+диагностик\w*(?:[^.!?]{0,70})?\s+запис|"
        r"\bна\s+диагностик\w*\s+можн\w*[^.!?]{0,30}?\s*запис",
        h,
        re.I,
    ):
        return True
    # 10615: «на диагностику автомобиля Nissan записать» — «запис» и «диагностик» в начале разговора.
    opening = h[:1200]
    return bool(
        re.search(r"\bна\s+диагностик\w*\b", opening, re.I)
        and re.search(r"\bзапис\w+", opening, re.I)
    )


def _is_explicit_scheduled_diagnostics_intake(low: str) -> bool:
    """
    Явная запись на диагностику (не регламентное ТО), в т.ч. когда диспетчер назначает слот (9283).
    """
    head = (low or "")[:4000]
    if not _diagnostics_booking_intake_phrase_present(head):
        return False
    return any(t in head for t in _DIAGNOSTICS_TOPIC_MARKERS) or any(
        p in head for p in _DIAG_SYMPTOM_MARKERS
    )


def _is_regulatory_to_package_with_gearbox_oil(head: str) -> bool:
    """
    14073: масло в вариаторе/КПП в составе объёмного N-го ТО — не отдельная услуга (11996).
    """
    if not re.search(r"\b(?:замен\w*\s+)?масл", head, re.I):
        return False
    if not any(
        re.search(rf"\b{re.escape(g)}\w*\b", head, re.I)
        for g in ("вариатор", "коробк", "трансмис", "кпп", "поддон")
    ):
        return False
    if re.search(
        r"\b(?:только\s+)?замен\w*\s+масл\w*\s+в\s+вариатор",
        head,
        re.I,
    ):
        return False
    if re.search(r"\bбольш\w*\s+то\b", head, re.I):
        return True
    if re.search(r"\bрегламент\w*[^.!?]{0,80}\bбольш\w*\s+то\b", head, re.I):
        return True
    if re.search(r"\bполност\w*\s+[^.!?]{0,40}\s+то\b", head, re.I) and re.search(
        r"\b\d{2}\s+000\b", head
    ):
        return True
    if re.search(r"\bтехническ\w+\s+обслужив\w+\s+(?:коробк|вариатор)", head, re.I):
        return False
    if re.search(
        r"\b(?:меня\s+интересует|стоимост\w*)\s+замен\w*\s+масл",
        head,
        re.I,
    ):
        return False
    price_to = bool(
        re.search(
            r"\b(?:сколько|стоимост|сориентир\w*|стоит).{0,40}?\b(?:то|техническ\w+\s+обслужив)\b",
            head[:1200],
            re.I,
        )
    )
    return bool(
        price_to
        and (
            re.search(r"\b(?:меня(?:ет|ются)|меняется)\s+масл", head, re.I)
            or re.search(r"\b(?:объемн|объёмн)\w*\b", head, re.I)
            or re.search(
                r"\b(?:пят|шест|седьм|восьм|девят|десят)\w*\b[^.!?]{0,40}\b(?:необходимо|нужно)\s+сделать",
                head,
                re.I,
            )
            or re.search(r"\b\d{2,3}\s+000\b", head)
        )
    )


def _gearbox_oil_service_price_context(low: str) -> bool:
    """Запрос/смета замены масла КПП — не регламентное ТО автомобиля (11996)."""
    head = (low or "")[:4500]
    if not re.search(r"\b(?:замен\w*\s+)?масл", head, re.I):
        return False
    # 16914: «коробка передач какая у вас?» — уточнение комплектации для сметы ТО, не замена масла КПП.
    if re.search(r"\bкоробк\w*\s+передач\s+как", head, re.I):
        return False
    if re.search(r"\bкак\w*\s+у\s+вас\s+коробк", head, re.I):
        return False
    gearbox = ("вариатор", "коробк", "трансмис", "кпп", "поддон")
    if not any(re.search(rf"\b{re.escape(g)}\w*\b", head, re.I) for g in gearbox):
        return False
    if _is_regulatory_to_package_with_gearbox_oil(head):
        return False
    return True



def _is_accessory_install_price_quote_intake(low: str) -> bool:
    """
    Установка доп.оборудования / аксессуаров — запрос цены, не регламентное ТО (15825).
    """
    if contains_any_to_marker_hit(low)[0] or strong_scheduled_to_signal_present(low):
        return False
    accessory = bool(
        re.search(r"\b(?:брызговик|брезовик|грузговик)", low, re.I)
        or re.search(r"\bфаркоп", low, re.I)
        or (
            re.search(r"\bустановк", low, re.I)
            and re.search(r"\bдополнительн\w*\s+оборудован", low, re.I)
        )
    )
    if not accessory:
        return False
    return bool(re.search(r"\b(?:сколько|стоимост|стоит|цена|цену)\b", low, re.I))


def _is_accessory_alarm_equipment_service_intake(low: str) -> bool:
    """
    16790: отключить доп. сигнализацию, отдел доп.оборудования / передача мастеру — не ТО.
    STT «записаться то чё-то» при теме сигнализации — не work_type «to».
    """
    if contains_any_to_marker_hit(low)[0] or strong_scheduled_to_signal_present(low):
        return False
    head = (low or "")[:3200]
    if not re.search(r"\bсигнализац", head, re.I):
        return False
    if re.search(r"\b(?:отключ|выключ|снять)\w*", head, re.I):
        return True
    if re.search(r"\bдополнительн\w*\s+сигнализац", head, re.I):
        return True
    if re.search(
        r"\b(?:отдел\w*\s+)?(?:дополнительн\w*\s+оборудован|допоборудован|по\s+сигнализац)",
        low,
        re.I,
    ):
        return True
    return False


def _is_existing_to_accessory_followup_not_booking(low: str) -> bool:
    """
    У клиента уже есть запись на ТО, а текущий разговор про доп.оборудование
    (видеорегистратор/сигнализация/проводка): это не новая запись на ТО.

    Кейсы типа 21729: «на ТО завтра записаны? да» + «вопрос по видеорегистратору».
    """
    head = (low or "")[:4200]
    if not head.strip():
        return False
    has_existing_slot = _has_existing_appointment_clarification(head) or bool(
        re.search(
            r"\bвы\s+на\s+(?:то\b|техническ\w+\s+обслуживан\w*)[^.!?]{0,60}\bзаписан[аы]?\b",
            head,
            re.I,
        )
        or re.search(r"\b(?:я|мы)\s+уже\s+записан[аы]?\b", head, re.I)
        or re.search(r"\bподтверждаем\s+вашу\s+запись\b", head, re.I)
    )
    if not has_existing_slot:
        return False

    accessory_issue = bool(
        re.search(r"\bвидеорегист\w*\b", head, re.I)
        or re.search(r"\bрегистратор\w*\b", head, re.I)
        or re.search(r"\bдоп(?:олнительн\w*\s+)?оборудован\w*\b", head, re.I)
        or (
            re.search(r"\bсигнализац\w*\b", head, re.I)
            and re.search(r"\b(?:не\s+работ|сломал|сломан|провод|подключ|отключ)\w*", head, re.I)
        )
    )
    if not accessory_issue:
        return False

    # Если явно создают новый слот на ТО (без признаков уже существующей записи), не перехватываем.
    if re.search(r"\b(?:запиш(?:у|ем|ите)|записать\s+вас)\s+на\s+то\b", head, re.I) and not has_existing_slot:
        return False
    return True


def _is_non_to_issue_with_incidental_to_reference_not_booking(low: str) -> bool:
    """
    Упоминание ТО носит справочный характер («до ТО ещё ...», «пора ТО»),
    но предмет звонка — отдельная неисправность/консультация без обсуждения записи на ТО.
    Возвращаем НЕ_ТО (other_work).
    """
    head = (low or "")[:3200]
    if not head.strip():
        return False
    # 22187: индикация ключа/ТО на приборке — обращение по сбросу/настройке,
    # а не новая запись на регламентное ТО.
    dashboard_indicator_issue = bool(
        re.search(
            r"\b(?:значок|индикац\w*|пиктограмм\w*|ключ(?:ик)?|ламп\w*)\b",
            head,
            re.I,
        )
        and re.search(
            r"\b(?:прибор\w*|панел\w*|комбинац\w+\s+прибор\w*|выскочил|загорел)\b",
            head,
            re.I,
        )
        and re.search(
            r"\b(?:убрал\w*|сброс\w*|настройк\w*|приехат\w*|подъехат\w*|"
            r"что(?:\s+\w+){0,3}\s+делать|как\s+убрат\w*|как\s+сброс\w*|"
            r"сможем\s+доехат\w*|можно\s+доехат\w*)\b",
            head,
            re.I,
        )
    )
    if dashboard_indicator_issue:
        # Если в звонке реально договариваются о новой записи на регламентное ТО,
        # не перехватываем этот кейс в НЕ_ТО.
        if re.search(
            r"\b(?:запис(?:ать|аться|ыва\w*)\s+на\s+то|"
            r"записал[аи]?\s+вас\s+на\s+то|стоимост\w*\s+то|"
            r"сколько\s+стоит\s+то|то\s*[-]?\s*\d)\b",
            head,
            re.I,
        ):
            return False
        return True
    has_incidental_to_reference = bool(
        re.search(
            r"\bдо\s+(?:техобслуживан\w*|техническ\w+\s+обслуживан\w*|то)\b[^.!?]{0,40}\b(?:ещ[её]|еще|остал\w*)\b",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:пора|требует(?:ся)?)\s+(?:пройти\s+)?(?:то|техобслуживан\w*|техническ\w+\s+обслуживан\w*)\b",
            head,
            re.I,
        )
    )
    if not has_incidental_to_reference:
        return False
    # Если есть явный интент записи/регламента, это уже не incidental-упоминание.
    if strong_scheduled_to_signal_present(head):
        return False
    if _regulatory_to_price_or_composition_context(head):
        return False
    if _is_inbound_opening_regulatory_to_booking(head):
        return False
    issue_or_handoff_markers = (
        "не реагиру",
        "неактив",
        "не работает",
        "неисправ",
        "ошибк",
        "загорел",
        "пиктограм",
        "ключик",
        "приложени",
        "сигнализац",
        "допоборуд",
        "специалист",
        "переключу",
        "переведу",
    )
    if not any(m in head for m in issue_or_handoff_markers):
        return False
    # Подстраховка: если всё же обсуждают слот/дату/время ТО, не срабатываем.
    if re.search(
        r"\b(?:на\s+\d{1,2}(?::\d{2})?|в\s+\d{1,2}(?::\d{2})?|"
        r"на\s+(?:понедельник|вторник|среду|четверг|пятниц|суббот|воскресен)|"
        r"на\s+(?:какое|какую)\s+(?:время|дату|число)|"
        r"когда\s+запис(?:аться|ать))\b",
        head,
        re.I,
    ):
        return False
    return True


def _is_existing_zero_to_certificate_price_consultation_not_booking(low: str) -> bool:
    """
    22188: клиент уже записан на ТО-0 и уточняет условия «подарка/сертификата/бесплатно».
    Это не новая запись на ТО, а уточнение по существующему визиту.
    """
    head = (low or "")[:4200]
    if not head.strip():
        return False
    has_existing_zero_to_slot = bool(
        re.search(
            r"\b(?:сегодня|на\s+сегодня)\b[^.!?]{0,80}\bзаписан[аы]?\b[^.!?]{0,80}\b(?:то\s*-\s*0|то0|нулев\w+\s+то)\b",
            head,
            re.I,
        )
        or re.search(
            r"\bзаписан[аы]?\b[^.!?]{0,80}\b(?:в|на)\s+\d{1,2}[:.]\d{2}\b[^.!?]{0,80}\b(?:то\s*-\s*0|то0|нулев\w+\s+то)\b",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:то\s*-\s*0|то0|нулев\w+\s+то)\b[^.!?]{0,100}\b(?:сегодня|в|на)\s+\d{1,2}[:.]\d{2}\b",
            head,
            re.I,
        )
    )
    if not has_existing_zero_to_slot:
        return False
    has_certificate_consult = bool(
        re.search(r"\b(?:сертификат|письм\w*|подар\w*|штамп\w*|отмет\w*)\b", head, re.I)
        and re.search(r"\b(?:бесплатн\w*|действу\w*|использ\w*|привез\w*)\b", head, re.I)
    )
    if not has_certificate_consult:
        return False
    # Если явно оформляют новый слот ТО, не перехватываем.
    if re.search(
        r"\b(?:запис(?:ать|аться|ыва\w*)\s+на\s+то|записал[аи]?\s+вас\s+на\s+то)\b",
        head,
        re.I,
    ):
        return False
    return True


def _is_recall_software_update_consultation_without_to_booking(low: str) -> bool:
    """
    Отзывная/сервисная акция на обновление ПО/прошивку:
    клиент не записывается на ТО сейчас, а переносит «потом вместе с ТО».
    Это НЕ_ТО (прочие работы), даже при фразе «в рамках ТО бесплатно».
    """
    head = (low or "")[:3800]
    if not head.strip():
        return False
    update_or_campaign = bool(
        re.search(
            r"\b(?:обновлен\w*|обновим\w*|прошив\w*|программн\w+\s+обеспечен\w*|"
            r"головн\w+\s+устройств\w*|голосов\w+\s+помощник\w*|акци\w*|уведомлени\w*|письм\w*)\b",
            head,
            re.I,
        )
    )
    if not update_or_campaign:
        return False
    # Типичный контекст: установка вне ТО платная, в рамках ТО бесплатная.
    has_to_scope_phrase = bool(
        re.search(
            r"\b(?:в\s+рамках|при)\s+(?:планов\w+\s+)?(?:то|техобслуживан\w*|техническ\w+\s+обслуживан\w*)\b",
            head,
            re.I,
        )
        or re.search(
            r"\bвне\s+(?:то|техобслуживан\w*|техническ\w+\s+обслуживан\w*)\b",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:до|к|когда)\s+перв(?:ом|ое)\s+то\b",
            head,
            re.I,
        )
    )
    if not has_to_scope_phrase:
        return False
    defer_to_future_to = bool(
        re.search(
            r"\b(?:потом|позже|как[-\s]?нибудь)\b[^.!?]{0,90}\b(?:вместе\s+с\s+то|когда\s+буду\s+делать\s+то|на\s+перв(?:ом|ое)\s+то)\b",
            head,
            re.I,
        )
        or re.search(
            r"\bпотом\b[^.!?]{0,100}\b(?:то|техническ\w+\s+обслуживан\w*|техобслуживан\w*)\b[^.!?]{0,60}\bбуд\w*\s+делат\w*\b",
            head,
            re.I,
        )
        or re.search(r"\b(?:(?:на|до|к|когда)\s+перв(?:ом|ое)\s+то)\b", head, re.I)
    )
    if not defer_to_future_to:
        return False
    explicit_new_to_booking_intent = bool(
        re.search(
            r"\b(?:хочу|хотел[аи]?|давайте|запишите|запиши|записаться)\b[^.!?]{0,120}"
            r"\b(?:на\s+)?(?:то|техобслуживан\w*|техническ\w+\s+обслуживан\w*)\b",
            head,
            re.I,
        )
        and re.search(
            r"\b(?:на\s+\d{1,2}(?::\d{2})?|в\s+\d{1,2}(?::\d{2})?|"
            r"на\s+(?:какое|какую)\s+(?:время|дату|число)|"
            r"на\s+(?:понедельник|вторник|среду|четверг|пятниц|суббот|воскресен)|"
            r"записал[аи]?\s+вас)\b",
            head,
            re.I,
        )
    )
    if explicit_new_to_booking_intent:
        return False
    if _is_inbound_opening_regulatory_to_booking(head):
        return False
    return True


def _is_recall_update_warranty_context(low: str) -> bool:
    """
    Контекст гарантийного (бесплатного) отзывного обновления:
    откладываем до ТО, но само обновление обещано бесплатно.
    """
    head = (low or "")[:3800]
    if not head.strip():
        return False
    has_update_subject = bool(
        re.search(
            r"\b(?:обновл\w*|обновим\w*|прошив\w*|программн\w+\s+обеспечен\w*)\b",
            head,
            re.I,
        )
    )
    if not has_update_subject:
        return False
    has_warranty_or_free = bool(
        re.search(r"\bгаранти\w*\b", head, re.I)
        or re.search(r"\bбесплатн\w*\b", head, re.I)
    )
    if not has_warranty_or_free:
        return False
    first_to_context = bool(
        re.search(r"\bперв(?:ом|ое)\s+то\b", head, re.I)
        and (
            re.search(r"\b10\s*000\b", head, re.I)
            or re.search(r"\b10\s*тыс", head, re.I)
        )
    )
    return first_to_context


def _is_service_campaign_hardware_recall_without_regulatory_to(low: str) -> bool:
    """
    Сервисная кампания/доработка (шторка, жгут сидения и т.п.) без явной темы регламентного ТО.
    Шумные обрывки STT вроде «на то» не считаем достаточным признаком ТО.
    """
    head = (low or "")[:4500]
    if not head.strip():
        return False
    has_campaign = bool(
        re.search(r"\b(?:сервисн\w*\s+кампан\w*|кампан\w+\s+месяц\w*|акци\w*)\b", head, re.I)
    )
    if not has_campaign:
        return False
    has_hardware_subject = bool(
        re.search(r"\bжгут\w*\b", head, re.I)
        or re.search(r"\bсидени\w*\b", head, re.I)
        or re.search(r"\bшторк\w*\b", head, re.I)
        or re.search(r"\bдоработк\w*\b", head, re.I)
        or re.search(r"\bпроверк\w*\b", head, re.I)
    )
    if not has_hardware_subject:
        return False
    # Явная тема регламентного ТО: такие кейсы сюда не относим.
    has_strong_reg_to = bool(
        re.search(r"\b(?:техническ\w+\s+обслуживан\w*|техобслуживан\w*)\b", head, re.I)
        or re.search(r"\b(?:при|в\s+рамках)\s+то\b", head, re.I)
        or re.search(r"\bто\b[^.!?]{0,25}\b(?:будет|длит\w*|занима\w*|займ[её]т)\b", head, re.I)
        or re.search(r"\bто\s*[-]?\s*\d{1,2}\b", head, re.I)
    )
    if has_strong_reg_to:
        return False
    return True


def _repair_work_price_quote_is_primary_not_regulatory_to(low: str) -> bool:
    """
    Запрос цены ремонтных работ (радиатор/вентилятор и т.п.), не смета регламентного ТО (14237, 11413).
    «на N-е на ТО записан» в хвосте — про совмещение с уже существующей записью.
    """
    head = (low or "")[:2200]
    if _is_accessory_install_price_quote_intake(low):
        return True
    repair_quote = bool(
        re.search(r"\bпо\s+стоимости\s+работ", head, re.I)
        or re.search(
            r"\b(?:замен\w*|ремонт\w*).{0,90}(?:радиатор|вентилятор|бампер|амортизатор|стойк)",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:радиатор|вентилятор|бампер).{0,90}(?:замен\w*|ремонт\w*)",
            head,
            re.I,
        )
    )
    if not repair_quote:
        return False
    if re.search(r"\bзапис(?:аться|ь)\s+на\s+(?:то|\d)", head[:1400], re.I):
        if not re.search(r"\bпо\s+стоимости\s+работ", head[:900], re.I):
            return False
    return True


_STT_TO_DIGIT_PRICE_QUOTE_RES = (
    # 15880: STT «сколько Т 3 будет» — «ТО-3» без «О».
    re.compile(
        r"\b(?:сколько|стоимост|стоит|узнать|хотел\s+узнать).{0,50}?\bт\s*[-]?\s*(?:0|[1-9])\b",
        re.I,
    ),
    # STT «ттретие т» / «третие т» — «третье ТО» с обрубком «О».
    re.compile(
        r"\bт{1,2}(?:рет|ерв|тор|четвер|четвёрт|ят|ест|едьм|осьм|евят|улев|олев)\w*\s+т\b",
        re.I,
    ),
    # 26378: STT «рассчитать стоимость до четвертого» (ошибка «до» вместо «ТО»)
    # + пробег/км в той же реплике => запрос цены N-го ТО.
    re.compile(
        r"\b(?:рассчитать|посчитать|узнать)\w*.{0,45}?\b(?:стоимост\w*|сколько|стоит)\b"
        r".{0,45}?\bдо\s+(?:перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят)\w*\b"
        r".{0,90}?\b(?:пробег|км|\d{2,3}\s*000)\b",
        re.I,
    ),
    # 26378: даже без явного "пробег/км" в той же фразе.
    re.compile(
        r"\b(?:рассчитать|посчитать|узнать)\w*.{0,55}?\b(?:стоимост\w*|сколько|стоит)\b"
        r".{0,45}?\bдо\s+(?:перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят)\w*\b",
        re.I,
    ),
)


def _stt_regulatory_to_price_quote_present(low: str) -> bool:
    """STT-обрубки «Т N» / «ттретие т» в запросе цены регламентного ТО (15880)."""
    head = (low or "")[:1600]
    return any(rx.search(head) for rx in _STT_TO_DIGIT_PRICE_QUOTE_RES)


def _price_question_with_service_to_token_present(masked_low: str) -> bool:
    """
    Защита от ложных срабатываний «...сколько стоит ... а то ...».
    Считаем bare «то» маркером ТО только если это не союз/частица.
    """
    head = (masked_low or "")[:1200]
    for m in re.finditer(r"\b(?:сколько|стоимост|сориентир\w*|стоит).{0,80}?\bто\b", head, re.I):
        token = re.search(r"\bто\b", m.group(0), re.I)
        if not token:
            continue
        token_abs_start = m.start() + token.start()
        token_abs_end = token_abs_start + len(token.group(0))
        left = head[max(0, token_abs_start - 8) : token_abs_start]
        right = head[token_abs_end : token_abs_end + 28]
        # Союзная конструкция: «... а то ...» не про регламентное ТО.
        if re.search(r"(?:^|\s)(?:а|но|и)\s*$", left, re.I):
            continue
        # Частица/союз после «то»: «то же/то ж/то вдруг/то приеду ...».
        if re.match(
            r"\s*(?:же|ж|вдруг|приед\w*|нам\b|мне\b|ему\b|ей\b|не\b|бы\b|чтобы\b|чтоб\b)",
            right,
            re.I,
        ):
            continue
        # «то 2 000 ₽» без явного регламентного контекста — чаще связка/оговорка, не тема ТО.
        if re.match(r"\s*\d{1,3}(?:\s+\d{3})?\b", right):
            local = head[max(0, token_abs_start - 80) : token_abs_end + 120]
            has_reg_context = bool(
                re.search(
                    r"\b(?:то\s*-\s*\d{1,3}|нулев\w+\s+то|перв\w+\s+то|втор\w+\s+то|"
                    r"трет\w+\s+то|четвер\w+\s+то|техническ\w+\s+обслуживан\w*|"
                    r"техобслуживан\w*|регламент\w*|пробег\w*|следующ\w+\s+то)\b",
                    local,
                    re.I,
                )
            )
            if not has_reg_context:
                continue
        return True
    return False


def _regulatory_to_price_or_composition_context(low: str) -> bool:
    """
    Явная тема регламентного ТО: номер/состав ТО (9506, 10545, 9163).
    Наличие цены само по себе не маркер ТО; «что-то»/«где-то» маскируются до проверок «то».
    """
    if _stt_regulatory_to_price_quote_present(low):
        return True
    if _gearbox_oil_service_price_context(low):
        return False
    if _is_accessory_install_price_quote_intake(low):
        return False
    if _repair_work_price_quote_is_primary_not_regulatory_to(low):
        return False
    if _is_arrived_interior_part_replacement_booking_intake(low):
        return False
    if _is_warranty_to_scope_consultation_not_regulatory_to_booking(low):
        return False
    masked = _mask_spurious_to_for_service(low)
    _ordinal_to = (
        r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят)\w*\s+то\b"
    )

    def _live_ordinal_to_present(chunk: str) -> bool:
        """Порядковое ТО не в истории «делали/после ТО» (16969)."""
        for m in re.finditer(_ordinal_to, chunk, re.I):
            narrow = (low or "")[max(0, m.start() - 55) : m.end() + 55]
            if re.search(
                r"\b(?:проходил\w*|делал\w*|были|заезжал\w*|обслуживал\w*)\b",
                narrow,
                re.I,
            ):
                continue
            if re.search(r"\bпосле\s+то\b", narrow, re.I):
                continue
            if re.search(r"\bобращени\w*\b", narrow, re.I):
                continue
            if re.search(r"\bв\s+рамках\b", narrow, re.I):
                continue
            return True
        return False

    return bool(
        re.search(r"\bнулев\w*\s+то\b", low, re.I)
        and not re.search(
            r"\b(?:проходил\w*|делал\w*)\b[^.!?]{0,40}\bнулев\w*\s+то\b",
            low,
            re.I,
        )
        or _live_ordinal_to_present(masked)
        or re.search(r"\bна\s+данном\s+то\b", masked, re.I)
        # «меняем масло» само по себе — масляный сервис, не смета ТО (16895).
        # Масло в составе ТО ловится ниже вместе с «на этом то» / «то большое» / порядковым ТО.
        or re.search(
            r"\b(?:пят|шест|седьм|восьм|девят|десят)\w*\b[^.!?]{0,40}\b(?:необходимо|нужно)\s+сделать",
            low,
            re.I,
        )
        or re.search(r"\bто\s+необходимо\s+сделать\b", masked, re.I)
        # 16368: CRM «посмотрите ТО» → следующее ТО по пробегу + запись на слот.
        or (
            re.search(r"\bследующ\w*\s+то\b", low, re.I)
            and (
                re.search(r"\b(?:запиш|записал)\w+", low, re.I)
                or re.search(r"\bпробег\b", low, re.I)
                or re.search(r"\bпредыдущ\w*\s+то\b", low, re.I)
            )
        )
        or (
            re.search(r"\b(?:меня(?:ет|ются|ем)|меняется)\s+масл", low, re.I)
            and re.search(r"\b(?:на\s+этом\s+то|по\s+работам\s+по\s+стоимости)\b", low, re.I)
        )
        # 16914: «надо ТО проходить», нулевое/первое прошла → вторая + цена.
        or (
            re.search(r"\b(?:надо|нужно|мне\s+надо)\s+то\s+проход", low, re.I)
            and re.search(r"\b(?:сколько|стоимост|стоит)\b", low, re.I)
            and re.search(r"\b(?:нулев\w+|перв\w+|втор\w+)\b", low, re.I)
        )
        or (
            re.search(r"\bпроходил\w*\s+нулев", low, re.I)
            and re.search(r"\bперв\w+", low, re.I)
            and re.search(r"\bвтор\w+", low, re.I)
            and re.search(r"\b(?:стоимост|сколько|₽|руб|\d[\d\s]{3,4})\b", low, re.I)
        )
        # 10155: базовое ТО + масло/фильтры + запись на слот.
        or (
            re.search(r"\b(?:то\s+пройти|пройти\s+то)\b", masked, re.I)
            and re.search(r"\bзаписал", low, re.I)
        )
        or (
            re.search(r"\bбазов\w*\b", low, re.I)
            and re.search(r"\b(?:замен\w*\s+)?масл", low, re.I)
            and re.search(r"\b(?:то\s+пройти|пройти\s+то)\b", masked, re.I)
        )
        # 10545, 9163: «ТО большое с заменой масла» в смете регламентного ТО.
        or (
            re.search(r"\bто\s+больш", masked, re.I)
            and re.search(
                r"\b(?:перв|втор|трет|четвер|четвёрт)\w*\s+то\b",
                masked,
                re.I,
            )
        )
        # 11291: запрос цены ТО на пробеге 80 000 + Tiggo 8 Pro Max.
        or re.search(
            r"\b(?:сколько|стоимост|стоит).{0,80}?\bто\s+\d{2,3}\s+000\b",
            low,
            re.I,
        )
        # 17212: «ТО-105» / «регламент ТО-75» — код регламентного ТО Nissan (шаг 5 тыс. км).
        or _mileage_to_interval_code_present(low)
        # 11394: «стоимость первого Т» (до нормализации «т») / после — «первое то».
        or re.search(
            r"\bстоимост\w*.{0,60}?\b(?:перв|втор|трет|четвер|четвёрт|нулев)\w*\s+то\b",
            low,
            re.I,
        )
        or re.search(
            r"\bстоимост\w*.{0,60}\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят)\w*\s+то\b",
            low,
            re.I,
        )
        or _price_question_with_service_to_token_present(masked)
        or _stt_regulatory_to_price_quote_present(low)
        # 11441: диспетчер подтверждает пакет регламентного ТО по заявке.
        or (
            re.search(r"\bто\s+подходит\b", masked, re.I)
            and re.search(r"\b(?:30\s+000|\d{2}\s+000)\b", low)
        )
        # 16743: смета регламентного ТО на пробеге 30 000 (масло/фильтры/свечи), не отдельная замена масла.
        or (
            re.search(r"\b(?:30\s+000|на\s+30\s*0+)\b", low, re.I)
            and re.search(r"\bзамен\w*\s+масл", low, re.I)
            and re.search(r"\b(?:фильтр|свеч)", low, re.I)
        )
        # 16148: «регламент большого ТО» / «полностью всё ТО … 47 стоит» — смета регламентного ТО.
        or re.search(r"\bрегламент\w*[^.!?]{0,80}\bбольш\w*\s+то\b", low, re.I)
        or (
            re.search(r"\bбольш\w*\s+то\b", low, re.I)
            and ("регламент" in low or re.search(r"\bполност\w*\s+[^.!?]{0,40}\s+то\b", low, re.I))
        )
        # 17212: «ТО-105» / «регламент ТО-75» — код регламентного ТО (шаг 5 тыс. км).
        or _mileage_to_interval_code_present(low)
    )


_CAR_ALREADY_AT_SERVICE_MARKERS = (
    "машина уже у нас",
    "автомобиль уже у нас",
    "находится в сервисе",
    "стоит в сервисе",
    "в работе у нас",
    "на сервисе сейчас",
    "выдача автомобиля",
    # Клиент: авто уже отдан на ТО/обслуживание у этого дилера (статус / когда забрать), не новая запись.
    # Подстрочно ловим «…на техобслуживании» и падежи «…техобслуживание» после нормализации.
    "у вас на техобслуживани",
    "там у вас на техобслуживани",
    "машина там у вас",
    "машинка там у вас",
    "авто там у вас",
    "автомобиль там у вас",
    # Сдал/передал на площадку — статус визита, перезвон мастеру, не новая запись на регламентное ТО (8768).
    "передал на станцию",
    "передали на станцию",
    "передавал на станцию",
    "передала на станцию",
    "сдал на сервис",
    "сдали на сервис",
    "вчера передал",
    "вчера сдал",
    "вчера сдали",
    # 16946: вчера оставляли автомобиль; статус готовности, не новая запись на ТО.
    "оставляли автомобиль",
    "оставлял автомобиль",
    "оставили автомобиль",
    "оставил автомобиль",
    "оставляли машин",
    "оставлял машин",
    "по готовности автомобиля",
    "по готовности авто",
    "узнать по готовности",
    "уже на сто",
    "автомобиль уже на сто",
    "принимает автомобиль, освободится",
    # 11908: координация выдачи — авто уже на сервисе, не новая запись на ТО.
    "подъеду за автомобил",
    "выдадим вам автомобиль",
)

# Мягкие маркеры: легко ловятся на CRM-поиск «там у вас машина Chery» / склейку фраз (17231).
_SOFT_TAM_U_VAS_CAR_AT_SERVICE_MARKERS = frozenset(
    (
        "машина там у вас",
        "машинка там у вас",
        "авто там у вас",
        "автомобиль там у вас",
    )
)

_STRONG_CAR_ALREADY_AT_SERVICE_MARKERS = tuple(
    m for m in _CAR_ALREADY_AT_SERVICE_MARKERS if m not in _SOFT_TAM_U_VAS_CAR_AT_SERVICE_MARKERS
)


def _crm_automobile_u_vas_script_only(low: str) -> bool:
    """«автомобиль у вас» в скрипте приёмки (госномер, пробег), не «машина сейчас на сервисе» (13620)."""
    if re.search(
        r"\b(?:машин\w+|автомобил\w+)\s+у\s+вас\s+(?:госномер|пробег)\b",
        low,
        re.I,
    ):
        return True
    if re.search(r"\bроботе\s+автомобил\w*\s+у\s+вас\b", low, re.I):
        return True
    if re.search(
        r"\bна\s+(?:каком\s+)?(?:пробег\w*|роботе)[^.!?]{0,35}автомобил\w*\s+у\s+вас\b",
        low,
        re.I,
    ):
        return True
    return False


def _crm_vehicle_lookup_false_tam_u_vas(low: str) -> bool:
    """
    17231: «там у вас машина Chery…» после вопроса про авто — поиск в CRM, не статус на сервисе.
    Склейка «она машина. Там у вас машина …» даёт ложное «машина там у вас».
    """
    if re.search(
        r"\b(?:машин\w+|автомобил\w+|авто)\s+там\s+у\s+вас\s+(?:машин\w+|автомобил\w+|авто)\b",
        low,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:для\s+какого\s+автомобил|какого\s+автомобил|машина\s+какая)\w*",
        low,
        re.I,
    ) and re.search(
        r"\bтам\s+у\s+вас\s+(?:машин\w+|автомобил\w+|авто)\b",
        low,
        re.I,
    ):
        return True
    return False


def _future_dropoff_to_slot_inquiry_not_car_at_service(low: str) -> bool:
    """17231: «загнать на техобслуживание» / слот на завтра — будущая сдача, не статус визита."""
    if not re.search(
        r"\bзагн\w*\s+на\s+(?:техобслуживани\w*|техническ\w+\s+обслуживани\w*|то)\b",
        low,
        re.I,
    ):
        return False
    if any(m in low for m in _STRONG_CAR_ALREADY_AT_SERVICE_MARKERS):
        return False
    if re.search(r"\bсегодня\s+машин\w*\s+к\s+нам\b", low, re.I):
        return False
    if re.search(r"\bмашин\w*\s+к\s+нам\b", low, re.I) and re.search(
        r"\b(?:сдал\w*|сдан\w*|уже\s+у\s+нас|на\s+сервисе)\b",
        low,
        re.I,
    ):
        return False
    return True


def _future_dropoff_pickup_booking_arrangement(low: str) -> bool:
    """Утром пригоню / ключи оставлю → заберёте: договорённость о визите, не статус на сервисе (13620)."""
    return bool(
        re.search(r"\bпригон\w*", low, re.I)
        and re.search(r"\bзабер\w*", low, re.I)
        and not re.search(r"\bмоя\s+машин\w*\s+у\s+вас\b", low, re.I)
    )


def _car_already_at_service_detected(low: str) -> bool:
    """Авто уже у дилера: статус визита / выдача, не новая запись на регламентное ТО (8768, 11908)."""
    # 17231: будущая сдача на ТО — не «авто уже на сервисе».
    if _future_dropoff_to_slot_inquiry_not_car_at_service(low):
        return False
    marker_hits = [m for m in _CAR_ALREADY_AT_SERVICE_MARKERS if m in low]
    if marker_hits:
        soft_only = all(m in _SOFT_TAM_U_VAS_CAR_AT_SERVICE_MARKERS for m in marker_hits)
        if soft_only and _crm_vehicle_lookup_false_tam_u_vas(low):
            pass
        else:
            return True
    if re.search(r"\bподъед\w*\s+за\s+автомобил", low):
        return True
    if re.search(r"\bвыдад\w*\s+вам\s+автомобил", low):
        return True
    if re.search(r"\bзабер\w*", low) and re.search(
        r"\b(?:моя\s+)?(?:машин\w+|автомобил\w+)\s+у\s+вас\b", low
    ):
        # 12749: «автомобиль у вас обслуживался» — история, не «машина сейчас у вас».
        if re.search(r"\b(?:машин\w+|автомобил\w+)\s+у\s+вас\s+обслужив", low, re.I):
            pass
        # «заберёте по готовности» после согласованного будущего ТО — не статус визита.
        elif re.search(r"\bзабер\w*\s+[^.!?]{0,60}\b(?:по\s+)?готовност", low, re.I):
            pass
        elif _crm_automobile_u_vas_script_only(low):
            pass
        elif _future_dropoff_pickup_booking_arrangement(low):
            pass
        else:
            return True
    if re.search(r"\bзабер\w*", low) and re.search(
        r"\b(?:машин\w+|автомобил\w+)\s+у\s+вас\s+сто", low
    ):
        return True
    # 22106: «в 9:30 сдали ... автомобиль ... когда будет готов» — статус текущего визита.
    if re.search(
        r"\bсдал[аи]?\b[^.!?]{0,90}\b(?:машин\w*|автомобил\w*)\b",
        low,
        re.I,
    ) and re.search(
        r"\b(?:когда|примерно)\b[^.!?]{0,50}\bготов\w*|\bпо\s+готовност\w*\b",
        low,
        re.I,
    ):
        return True
    # 21227: «сегодня машину ... на нулевой ТО загнал» + связь с мастером — это статус
    # уже текущего визита, а не новая запись.
    if re.search(
        r"\bсегодня\b[^.!?]{0,80}\bмашин\w*\b[^.!?]{0,140}"
        r"\b(?:на\s+)?(?:нулев\w+\s+)?то\b[^.!?]{0,80}\b(?:загнал\w*|сдал\w*)\b",
        low,
        re.I,
    ):
        return True
    if re.search(r"\bмашин\w*\s+готов\w*\b", low, re.I) and re.search(
        r"\b(?:мастер|приемщик|приёмщик)\b",
        low,
        re.I,
    ):
        return True
    # 15927: «сегодня машина к нам (загнала) … техническое обслуживание» — авто уже на сервисе.
    if re.search(r"\bсегодня\s+машин\w*\s+к\s+нам\b", low, re.I):
        return True
    if re.search(r"\bмашин\w*\s+к\s+нам\b", low, re.I) and re.search(
        r"\b(?:техническ\w+\s+обслужив|обращ\w*\s+техническ|загн\w+|сдал\w*|сдан\w*)\b",
        low,
        re.I,
    ):
        return True
    return False


_CHERY_TENET_EXACT = (
    "чери",
    "chery",
    "тенет",
    "tenet",
    "тигго",
    "tiggo",
    "тига",  # STT: «Тига» ≈ Tiggo (Chery)
    "tiga",
    "тигго 4",
    "тигго4",
    "tiggo 4",
    "tiggo4",
    # Часто STT даёт «Chery Tig 4 Pro» без второго «go».
    "tig 4 pro",
    "tig4pro",
    "tig 4pro",
    # STT-обломки марки/модели (см. _normalize_text: чh[рr], tиg).
    "чhr",
    "чhр",
    "tиg",
    "тиg",
    "4pr",
    "4 pro",
    "4pro",
    "тиг 4",
    "tig 4",
    "тиг 4 про",
    "тиг4про",
    "тиг 4про",
    "тига 4",
    "тига4",
    "черепга",  # STT: «Chery» / Tiggo
    # Модель Tenet T7 (STT: «т на т-7», «т-7», «t7») — после _normalize_text даёт «тенет t7».
    "t7",
    "т7",
    # STT-искажения Tenet T7: «тэнт-7» / «тэнет-7» / «тент 7» / «тен 7».
    "тэнт-7",
    "тэнт 7",
    "тэнет-7",
    "тэнет 7",
    "тент-7",
    "тент 7",
    "тен-7",
    "тен 7",
    # Модель Tenet T8 и STT-искажения.
    "t8",
    "т8",
    "тэнт-8",
    "тэнт 8",
    "тэнет-8",
    "тэнет 8",
    "тент-8",
    "тент 8",
    "тен-8",
    "тен 8",
    "тэн т-8",
    "тэн т 8",
    "тен т-8",
    "тен т 8",
    # Tenet T4 (STT: «Tenet Т4», «тенет t4», «Тэнет-4»).
    "t4",
    "т4",
    "тенет t4",
    "tenet t4",
    # Tenet T1 (STT 17640: «Чl Т1»).
    "тенет t1",
    "tenet t1",
    "chery tenet t1",
    "тэнт-4",
    "тэнт 4",
    "тэнет-4",
    "тэнет 4",
    "тент-4",
    "тент 4",
    "тен-4",
    "тен 4",
    # Tenet T4 Pro (STT 14320: «Tenet T4Pr» / «Тенет Т4Pr»).
    "tenet t4 pro",
    "тенет t4 pro",
    "t4 pro",
    "t4pro",
    "т4 pro",
    "т4pr",
    "т4пр",
    # Модельный ряд Tiggo (8 Pro Max и т.д.) — без отдельного «чери» в фразе STT.
    "промакс",
    "про макс",
    "pro max",
    "тигго 7",
    "tiggo 7",
    "тигго 9",
    "tiggo 9",
    "тигго 7 про",
    "tiggo 7 pro",
    "тигго 7 л",
    "tiggo 7 l",
    "7 про",
    "7про",
    "7 pro",
    "7pro",
    "тигго 8",
    "tiggo 8",
    "титго 8",
    # STT: «Чиритиг 7 Промакс» ≈ Chery Tiggo 7 Pro Max (8029).
    "чиритиг",
    "чирити",
    "черетиг",
    "черетиго",
    "черетига",  # STT: «Черетига 7» (9317)
    "черега",  # STT: «Черега» ≈ Chery (9317)
    "черечга",  # STT 26027: «ЧереЧга» ≈ Chery Tiggo
    "черегсеми",  # STT 26371: «череГСеми» ≈ Chery Tiggo 7 L
    "чере г семи",
    "черитиго",
    "промаакс",
    # Chery Arrizo (STT: «ориза», «Лариза», «ариза» — 8572; «Ареза восем» — 10033).
    "arrizo",
    "arrizo 8",
    "arrizo8",
    "арризо",
    "аризо",
)

_NISSAN_EXACT = (
    "ниссан",
    "ниссане",  # STT/падеж: «на Ниссане» (13954)
    "нисsан",  # STT: латинская s в «Ниссан» (8031)
    "нессан",  # STT 17882: «Нессan» — «и»→«е», двойное «с»
    "нессана",  # STT 17882: род. падеж «с Нессана»
    "nissan",
    # STT/ручной ввод: «Neissan», «Neissan Terrana» и близкие латинские варианты.
    "neissan",
    "neisan",
    "нисан",
    "x-trail",
    "xtrail",
    "икстрейл",
    "кашкай",
    "кашка",  # STT: обрезание «Кашкай» → «кашка» (Qashqai)
    "кашкае",  # STT: «на Кашкае», «Ниссан Кашкае» (9803)
    "кашкаи",
    "qashqai",
    "kashkai",  # STT латиницей «Nissan Kashkai»
    "kashka",  # STT «Kashka» ≈ Qashqai (8761)
    # STT к модели X-Trail (латиница/искажения)
    "extrelle",
    "extrill",
    "extrall",
    "extreyl",
    "extreйл",
    # X-Trail кириллицей до нормализации — дублируют замены в _normalize_text
    "экстрелл",
    "экстрел",
    "экстрейл",
    "almera",
    "альмера",
    "алмера",
    # STT 10648: Nissan Patrol — «Nesan Patroll».
    "patrol",
    "patroll",
    "pathfinder",
    "патфайндер",
    "подфайм",  # STT: «Подфайм» ≈ Pathfinder (13474)
    "потфаймер",  # STT: «Потфаймер» (13412)
    "пайдер",
    "файмер",
    "tiida",
    "тиида",
    "micra",
    "микра",
    "titan",
    "terrano",
    "террано",
    "juke",
    "джук",
    "жук",  # STT 26654: «по Жуку / Жук-012» = Nissan Juke без явного «Nissan»
    # Infiniti — в продукте группа Nissan (сервисная марка nissan, не other_brand).
    "finit",  # STT: обрезание «In» в Infiniti
    "infinit",
    "infinity",
    "infiniti",
    "инфинит",
    "инфинити",
)

# STT matrix: N/н + и/i + s/с/c (1–3) + a/а + n/н — смешанная латиница/кириллица (17340: «Nисsaн»).
_NISSAN_STT_MATRIX_RE = re.compile(
    r"\b[нn][иiі][sсc]{1,3}[aа][nн](?:[еeуy])?\b",
    re.IGNORECASE,
)


def _nissan_stt_matrix_hit(low: str) -> Tuple[bool, str]:
    m = _NISSAN_STT_MATRIX_RE.search(low or "")
    if m:
        return True, (m.group(0) or "").strip()
    return False, ""


_OTHER_BRAND_EXACT = (
    # Явно «чужие» бренды/модели в сервисной линии: должны перебивать «Чери» из приветствия.
    # Hyundai
    "hyundai",
    "хендай",
    "хундай",
    "elantra",
    "элантра",
    "алантра",
    # Exeed
    "exeed",
    "exid",
    "эксид",
    "эксайд",
    "оксид",
    "oxid",
    # Exlantix / Exlantixs: не Chery/Tenet в нашей сервисной матрице.
    "exlantix",
    "exlantixs",
    "экслантикс",
    "экслэнтикс",
    "экслантиксs",
    # Xcite (15790: STT «Xсаit»; 17497: «ИX саit XCross»)
    "xcite",
    "xca it",
    "xcross",
    # Omoda
    "omoda",
    "омода",
    # Jetour
    "jetour",
    "джетур",
    "джетоур",
    "жетур",
    # Toyota
    "toyota",
    "тойот",
    # Kia
    " kia",
    "kia ",
    "киа ",
    " киа",
    # Lada и популярные модели
    "lada",
    "лада ",
    " лада",
    "гранта",
    "веста",
    "калина",
    "приор",
    "ларгус",
    "нива",
    # BMW / Mercedes
    "bmw",
    "бмв",
    "mercedes",
    "мерседес",
    # Volkswagen / Skoda
    "volkswagen",
    "фольксваген",
    "шкода",
    "skoda",
    # Renault
    "renault",
    "рено",
    "fluence",
    "флюенс",
    "флюн",
    # Subaru (14919: Forester — не Чери/Tenet, даже при «Викинги Чhri» в приветствии).
    "subaru",
    "subar",
    "subaro",
    "субару",
    "forester",
    "forestr",
    # STT-варианты Skoda Octavia (10422): «Scod Actavia Gus».
    "scod",
    "octavia",
    "actavia",
    "октавия",
    "актавия",
    # Geely / Haval / Belgee / Changan
    "geely",
    "джили",
    "haval",
    "хавал",
    # Haval Jolion (STT: «джолион», «джолиун») — не Chery/Tenet.
    "jolion",
    "джолион",
    "джолиун",
    "belgee",
    "бэлдж",
    "белдж",
    "changan",
    "чанган",
    # Ford (16318: «у меня форд», «Форд СМакс»)
    "ford",
    "форд",
    "s-max",
    "smax",
    "с-макс",
    "смакс",
    "focus",
    "фокус",
    "mondeo",
    "монdeo",
    "kuga",
    "куга",
    "galaxy",
    "галакси",
    # LiXiang / Li Auto (18187 STT: «Лиссян», «Лисян», «полисяном»)
    "lixiang",
    "li xiang",
    "лиссян",
    "лисян",
    "лисянь",
    "полисян",
    "ли сян",
)

# Маркеры марки/модели в окне после «автомобил…» (первое вхождение без марки — часто мусор STT).
_VEHICLE_MENTION_WINDOW_BRAND_MARKERS: Tuple[str, ...] = tuple(
    dict.fromkeys(tuple(_NISSAN_EXACT) + tuple(_CHERY_TENET_EXACT) + tuple(_OTHER_BRAND_EXACT))
)


def _chery_token_is_brand(text: str) -> bool:
    """«чери две неделя» и т.п. — STT про дату/неделю, не марка Chery (8571)."""
    for m in re.finditer(r"\bчери\b", text):
        left = text[max(0, m.start() - 48) : m.start()]
        tail = text[m.end() : m.end() + 32]
        ctx = f"{left}чери{tail}"
        if re.search(r"мотор\s+$", left) or "мотор чери" in ctx:
            continue
        if re.match(r"\s+(?:получается|две|три|четыре|пять|1|2|3|4|5)?\s*недел", tail):
            continue
        # Приветствие/перевод на линию: «Викинги Чери», «дилерский центр Чери» — не авто клиента (10302).
        if re.search(
            r"(?:викинг\w*|дилерск\w*\s+центр|автосалон|салон|ассистент\s+сервис|диспетчер\s+сервис|"
            r"официальн\w*\s+дилер)\s*[^.!?]{0,24}чери\b",
            ctx,
            flags=re.IGNORECASE,
        ):
            continue
        if re.search(r"\bчери\s+викинг", ctx, flags=re.IGNORECASE):
            continue
        return True
    return False


def _contains_chery_tenet(text: str) -> Tuple[bool, str]:
    for v in _CHERY_TENET_EXACT:
        if v not in text:
            continue
        if v == "чери" and not _chery_token_is_brand(text):
            continue
        return True, v
    return False, ""


def _segment_client_maintenance_brand(low: str) -> Optional[str]:
    """
    Явная заявка клиента о марке для ТО в начале разговора.
    Приоритетнее позднего «автомобил…» + ложного «чери» из STT про даты (8571, 8738).
    """
    m = re.search(
        r"(?:мне\s+)?(?:нужно\s+)?(?:сделать\s+)?"
        r"(?:техническое\s+обслуживание|техобслуживание)\s+"
        r"(nissan|ниссан|нисан|chery|чери|tenet|тенет|тэнет)\b",
        low,
    )
    if m:
        start = max(0, m.start() - 24)
        return low[start : m.end() + 140].strip()
    # 8738: «на Ниссан на ТО записаться», «Nissan Entrell».
    m2 = re.search(
        r"на\s+(?:nissan|ниссан|нисан)\s+на\s+то\s+запис",
        low,
    )
    if m2:
        start = max(0, m2.start() - 24)
        return low[start : m2.end() + 160].strip()
    # 13954: «на Ниссане бы масло поменять» — замена масла, марка Nissan.
    m_oil = re.search(
        r"\bна\s+(?:nissan|ниссан|нисан)\w*\s+[^.!?]{0,80}?\bмасл",
        low[:2400],
        flags=re.IGNORECASE,
    )
    if m_oil:
        start = max(0, m_oil.start() - 24)
        return low[start : m_oil.end() + 120].strip()
    # 8761: «покупал Nissan Kashka» — марка авто клиента, не «Чери» из даты записи.
    m3 = re.search(
        r"(?:покупал|купил[аи]?|приобретал)\s+(?:nissan|ниссан|нисан)\b",
        low,
    )
    if m3:
        start = max(0, m3.start() - 32)
        return low[start : m3.end() + 120].strip()
    # 8776: «У меня Infinit …» — марка клиента, не «Чери» из приветствия / STT «мотор Чери».
    m4 = re.search(
        r"\bу\s+меня\s+[^.!?]{0,120}?(?:finit|infinit|infinity|инфинит|инфинити)\b",
        low[:1800],
        flags=re.IGNORECASE,
    )
    if m4:
        start = max(0, m4.start() - 8)
        return low[start : m4.end() + 220].strip()
    return None


def _vehicle_mention_window_has_brand(window: str) -> bool:
    if _contains_any(window, _NISSAN_EXACT)[0]:
        return True
    if _contains_any(window, _OTHER_BRAND_EXACT)[0]:
        return True
    return _contains_chery_tenet(window)[0]


def _short_chery_model_answer_after_car_question(window: str) -> Optional[str]:
    """Короткий ответ на вопрос об авто: «7 Л» / «семь Л» / «Тиг» = Chery Tiggo."""
    answer = (window or "")[:48]
    if re.search(r"\b(?:м\s+)?(?:7|семь)\s*[lл]\b", answer, re.I):
        return "chery tiggo 7 l"
    if re.search(r"\bтиг\b", answer, re.I):
        return "chery tiggo"
    return None


# Все известные STT-варианты бренда Jetour (узкий слой НЕ_ТО, отказ в ТО, сброс записи).
# Используется и в sto_to_rubric.py (Jetour в тексте).
_JETOUR_BRAND_FORMS = (
    "джетур",
    "джитур",
    "джтур",
    "джытур",
    "жетур",
    "житур",
    "jetour",
    "jetor",
    "jettour",
    "jaytur",
    "g-tour",
    "g tour",
    "жтур",
    "жту",
    # падежи / склейки STT
    "джитуру",
    "джитура",
    "джитуром",
    "джитуры",
    "джетуру",
    "джетура",
    "джетуром",
    "джи тур",
    "джи-тур",
    "джито",  # 19605: STT «ДжиТО» = Jetour
    "дже тур",
    "дже-тур",
    # STT: латиница/кириллица вперемешку (9674: ДЖтр2, ДJтур).
    "джтр",
    "djтур",
    "дjtур",
    "дjтур",
    # 18349: «на Ддзтуре», «дилерами Dижетуr»
    "ддзтур",
    "дижетур",
    "dижетур",
    "dижетуr",
)

# Маркеры явного отказа дилера в обслуживании чужого бренда («мы не дилер», «не принимаем», ...).
_BRAND_SERVICE_REFUSAL_MARKERS = (
    "не обслуживаем",
    "больше не обслуживаем",
    "не принимаем",
    "больше не принимаем",
    # 18187: «ещё не принимали такие автомобили» / «пока не принимали»
    "не принимали",
    "ещё не принимали",
    "еще не принимали",
    "пока не принимали",
    "не являемся официальным дилером",
    "не являемся дилером",
    "больше не являемся",
    "больше не дилер",
    "больше не дилером",
    # типичный порядок слов STT: «не являемся больше официальными дилерами …»
    "не являемся больше официальными",
    "не являемся официальными",
    "официальными дилерами",
    "официальным дилерам",
    "не работаем с маркой",
    "не работаем по",
    "снят с дилерства",
    "снят с гарантии",
    "не в дилерств",
    "не возьмем",
    "не возьмём",
    "не возьмем в работу",
    "не возьмём в работу",
    "лучше обратиться к официальному дилеру",
    "обратитесь к официальному дилеру",
    # отказ именно в прохождении ТО / ТО-услуге (часто рядом с Jetour)
    "не проводим техническое обслуживание",
    "не проводим техническое",
    "не проводим то ",
    "не проводим то.",
    "не проводим то,",
    "не оказываем услуги по",
    "не оказываем услуги",
    # 18297: «уже нет дилерства», «неофициальные дилеры мы теперь»
    "нет дилерства",
    "уже нет дилерства",
    "неофициальные дилеры",
    "неофициальный дилер",
)


def _sto_service_refusal_phrase_hit(low: str) -> bool:
    """
    Отказ дилера в сервисе / ТО: подстроки плюс ослабленные условия под перестановки STT.
    Для Jetour узкая рубрика СТО_ТО_* не применяется при совпадении вместе с маркой в тексте.
    """
    if any(m in low for m in _BRAND_SERVICE_REFUSAL_MARKERS):
        return True
    if "не являемся" in low and "больше" in low and "дилер" in low:
        return True
    if "не являемся" in low and "официальн" in low and "дилер" in low:
        return True
    if "не проводим" in low and ("техническое обслуживание" in low or "техобслуживание" in low):
        return True
    # 18297: «Не продлили с нами дилерство» / «не продлили … дилерское соглашение»
    if "не продлили" in low and "дилер" in low:
        return True
    # 18680: технически обслужить можем, но официальное ТО для гарантийного
    # автомобиля невозможно — не поставим отметку в базе/сервисной книжке,
    # поэтому направляем к действующему дилеру.
    no_official_to_record = bool(
        re.search(
            r"\bне\s+(?:сможем\s+)?(?:постав\w*|внес\w*|сдела\w*)"
            r"[^.!?]{0,45}\bотмет",
            low,
            re.I,
        )
        and re.search(
            r"\b(?:электронн\w*\s+баз\w*|сервисн\w*\s+книжк\w*|"
            r"един\w*\s+баз\w*|портал\w*)\b",
            low,
            re.I,
        )
    )
    official_dealer_referral = bool(
        re.search(
            r"\b(?:лучше\s+обраща\w*|нужно\s+(?:обратиться|ехать)|"
            r"представительств\w*\s+только|дилерск\w*\s+центр)\b",
            low,
            re.I,
        )
        and re.search(
            r"\b(?:самар\w*|друг\w*\s+дилер\w*|официальн\w*\s+дилер\w*)\b",
            low,
            re.I,
        )
    )
    if no_official_to_record and official_dealer_referral:
        return True
    return False


def _jetour_warranty_refusal_but_to_still_offered(low: str) -> bool:
    """
    25922: «не принимаем автомобили на гарантийный ремонт. ТО можем провести» —
    отказ только по гарантии, регламентное ТО всё ещё предлагают.
    """
    if not re.search(
        r"не\s+принимаем[^.!?]{0,100}гарантийн\w*\s+ремонт",
        low or "",
        re.I,
    ):
        return False
    return bool(
        re.search(
            r"(?:\bто\b|техническ\w+\s+обслуживан\w*)\s+можем\s+провест|"
            r"можем\s+провест[^.!?]{0,60}(?:\bто\b|техническ)",
            low or "",
            re.I,
        )
    )


def _jetour_full_service_refusal_present(low: str) -> bool:
    """
    Полный отказ в ТО — узкий НЕ_ТО (9674).
    Не путать с ограничением «не офиц. дилер, но ТО проведём» (12339).
    """
    if _jetour_warranty_refusal_but_to_still_offered(low):
        return False
    if re.search(
        r"не\s+принимаем[^.!?]{0,120}(?:автомобил|ни\s+на\s+техническ|техническ)",
        low,
        re.I,
    ):
        return True
    if re.search(r"не\s+принимаем\w*\s+автомоб", low, re.I):
        return True
    if any(
        p in low
        for p in (
            "не проводим техническое обслуживание",
            "не проводим техническое",
            "не оказываем услуги",
        )
    ):
        return True
    if "не принимаем" in low and re.search(r"техническ\w+\s+обслуживан", low, re.I):
        if not re.search(r"можем\s+провест", low, re.I):
            return True
    return False


def _jetour_limitation_but_regulatory_to_booking_agreed(low: str) -> bool:
    """
    Jetour: не офиц. дилер (штамп/портал), но регламентное ТО и слот согласован — СТО_ТО_вх (12339).
    """
    if not jetour_brand_mentioned(low):
        return False
    if _jetour_full_service_refusal_present(low):
        return False
    if not _sto_service_refusal_phrase_hit(low):
        return False
    can_do_to = bool(
        re.search(
            r"техническ\w+\s+обслуживан\w+[^.!?]{0,100}можем\s+провест",
            low,
            re.I,
        )
        or re.search(r"можем\s+провест[^.!?]{0,100}техническ", low, re.I)
        or ("можем провести" in low and "техническ" in low)
        or re.search(r"именно\s+по\s+то", low, re.I)
    )
    if not can_do_to:
        return False
    reg_to = bool(
        re.search(
            r"\b(?:нулев|перв|втор|трет|четвер|четвёрт|пят|шест)\w*\s+то\b",
            low,
            re.I,
        )
        or re.search(r"\b(?:запис|загон\w*).{0,80}\bто\b", low, re.I)
        or re.search(r"\b(?:сколько|стоимост|22\s+500).{0,80}(?:то\b|техническ)", low, re.I)
        or re.search(r"\bна\s+техосмотр", low, re.I)
    )
    slot_agreed = bool(
        re.search(r"удобно\?[^.!?]{0,120}\b(?:да|могу|хорошо)\b", low, re.I)
        or re.search(
            r"\bда,?\s+могу\s+(?:и\s+)?(?:в\s+)?(?:сред|вторник|понедельник|завтра|\d)",
            low,
            re.I,
        )
        or (
            re.search(r"\b\d{1,2}[.:]\d{2}\b", low)
            and re.search(r"\b(?:позвон|напомн|ожидаем|ждем|ждём|\d{1,2}\s*(?:го|ое)\s+июн)", low, re.I)
        )
        # 25922: «на 15 часов запишите» / «на 12 сентября время 3 часа дня. вас записали».
        or re.search(
            r"\bзапишите\b[^.!?]{0,50}\bпожалуйста\b|"
            r"\b(?:вас|ввас)\s+записал[аи]\b|"
            r"\bзаписал[аи]\b[^.!?]{0,80}\bнапомн",
            low,
            re.I,
        )
        or (
            re.search(
                r"\b(?:на\s+)?\d{1,2}(?:-го)?\s+"
                r"(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\b",
                low,
                re.I,
            )
            and re.search(r"\b(?:час(?:а|ов)?|записал)\w*\b", low, re.I)
        )
        or re.search(r"\b(?:на\s+)?\d{1,2}\s+час(?:а|ов)?\b", low, re.I)
    )
    return reg_to and slot_agreed


# Регулярки для склеек STT Jetour, не покрытых подстроками _JETOUR_BRAND_FORMS.
_JETOUR_STT_BRAND_RE = (
    re.compile(r"д[жj][тt]р", re.I),
    re.compile(r"д[jj][тt]?ур", re.I),
    re.compile(r"джи[tт]ur", re.I),
    re.compile(r"(?:^|[^\wа-яё])dj\s*тур", re.I),
    re.compile(r"дилер\w{0,40}\b(?:д\.?\s*)?[jt]?\s*тур\b", re.I),
    re.compile(r"getur\s*dash", re.I),
    # 18349: «Ддзтуре», «Dижетуr» (латиница D + кириллица)
    re.compile(r"ддзтур", re.I),
    re.compile(r"[dд]ижету[rр]", re.I),
)


def jetour_brand_mentioned(low: str) -> bool:
    """
    Jetour / Джитур / Джетур и типичные ошибки STT (ДЖтр, ДJтур, «…дилером … тур»).
    Используется в узком слое НЕ_ТО и отказе в сервисе чужого бренда.
    """
    t = (low or "").lower()
    if any(p in t for p in _JETOUR_BRAND_FORMS):
        return True
    if any(rx.search(t) for rx in _JETOUR_STT_BRAND_RE):
        return True
    # Отдельное «тур» только в связке с отказом дилера и маркой в той же фразе (9674).
    if re.search(r"\bтур\b", t) and _sto_service_refusal_phrase_hit(t):
        if re.search(r"\bдилер\w*.{0,50}\bтур\b", t) or re.search(r"\bтур\w*.{0,40}\bдилер", t):
            return True
    return False


def _jetour_brand_hit_token(low: str) -> str:
    """Первое совпадение Jetour / X70 для evidence brand_hit (15791)."""
    t = (low or "").lower()
    if re.search(r"x\s*70", t, re.I):
        return "jetour x70"
    if re.search(r"(?:jetour|getur|джетур|джитур|жетур).{0,24}dash", t, re.I):
        return "jetour dashing"
    for p in sorted(_JETOUR_BRAND_FORMS, key=len, reverse=True):
        if p in t:
            return p.strip()
    return "jetour"


_CHERY_TENET_FUZZY_BASE = (
    "чери",
    "тенет",
    "chery",
    "tenet",
    "tiggo",
    "тига",
    "промакс",
    "arrizo",
)

_NISSAN_FUZZY_BASE = (
    "ниссан",
    "нисан",
    "nissan",
    "infinit",
    "infinity",
    "finit",
    "qashqai",
    "xtrail",
)

# Вопросы, после которых клиент обычно называет марку/модель (раньше приветствия «Викинги Чери» не считаем за машину клиента).
# Длинные фразы важнее коротких при совпадении позиции (см. _segment_after_car_question).
_CAR_IDENTITY_QUESTION_MARKERS = (
    "какой у вас автомобиль",
    "какая у вас машина",
    # 19284: «С каким автомобилем? … Шкода» — иначе зона бренда уходит в CRM + ложный «промакс».
    "с каким автомобилем",
    "с какой машиной",
    "каким автомобилем",
    # «Автомобиль какой? … Tenet T7» — частый порядок слов в STT (иначе зона бренда не сдвигается на ответ).
    "автомобиль какой",
    "автомобиль у вас какой",
    "у вас какой автомобиль",
    "какой автомобиль",
    "каккой автомобиль",
    "для какого автомобиля",
    "какой автомобиль у вас",
    "какой автомобиль у вам",
    "что у вас за автомобиль",
    "какая марка автомобиля",
    "какая марка у вас",
    "какой у вас авто",
    "на каком автомобиле",
    "на какой машине ездите",
    "на какой машине",
    "что за автомобиль у вас",
    "какая модель автомобиля",
    "какая модель у вас",
    "какой именно форд",
    "какой именно ford",
)

# Начало сути разговора (не шапка «Викинги Чери … слушаю»), если вопроса про авто нет.
_BODY_AFTER_GREETING_MARKERS = (
    "подскажите",
    "скажите, пожалуйста",
    "скажите пожалуйста",
    "можно ли",
    "когда первое",
    "хочу записаться",
    "нужно записаться",
    "интересует стоимость",
    "сколько стоит",
    "мне нужен",
    "замена масла",
)


def _ordinal_stem_to_regulatory_to_phrase(stem: str) -> str:
    """STT: «третья» после «стoimosti» → «третье то» (12475)."""
    s = (stem or "").lower().replace("ё", "е")
    for prefix, phrase in (
        ("перв", "первое то"),
        ("втор", "второе то"),
        ("трет", "третье то"),
        ("четвер", "четвертое то"),
        ("четвёрт", "четвёртое то"),
        ("пят", "пятое то"),
        ("шест", "шестое то"),
    ):
        if s.startswith(prefix):
            return phrase
    return f"{s} то"


_STOIMOST_ORDINAL_GAP_VEHICLE_RE = re.compile(
    r"\b(?:tenet|тенет|тэнет|тент|chery|чери|tiggo|тигго|arrizo|арризо)\b",
    re.IGNORECASE,
)


def _stoimost_ordinal_gap_repl(m: re.Match) -> str:
    """
    STT 12475: «пo stoimosti tretiya» → «третье то».
    STT 14320: не сжимать «стoimosti TOO Tenet T4Pr chetvyortoe» — модель авто в промежутке.
    """
    if _STOIMOST_ORDINAL_GAP_VEHICLE_RE.search(m.group(0) or ""):
        return m.group(0)
    return f" {m.group(1)} {_ordinal_stem_to_regulatory_to_phrase(m.group(2))} "


def _chery_stt_mixed_script_model_repl(m: re.Match) -> str:
    """STT: CTee7L, СТее7Л → chery tiggo 7 l (10185)."""
    frag = (m.group(0) or "").lower()
    digit_m = re.search(r"[4789]", frag)
    if not digit_m:
        return m.group(0)
    d = digit_m.group(0)
    tail = frag[digit_m.end() :]
    if re.search(r"[lл]", tail) and d == "7":
        return " chery tiggo 7 l "
    if re.search(r"(?:pr|pro|пр|про|max|макс)", frag):
        if re.search(r"(?:max|макс)", frag):
            return f" chery tiggo {d} pro max "
        return f" chery tiggo {d} pro "
    return f" chery tiggo {d} "


_MILEAGE_TO_INTERVAL_CODE_MAX = 300  # до 300 тыс. км (ТО-300)


def _mileage_to_interval_code_km_valid(code: int) -> bool:
    """Код регламентного ТО по пробегу (Nissan и др.): 0, 5, 10, … шаг 5 тыс. км."""
    return 0 <= code <= _MILEAGE_TO_INTERVAL_CODE_MAX and code % 5 == 0


def _mileage_to_interval_code_match_is_false_positive(low: str, m: re.Match) -> bool:
    """
    «то 23 200» — цена; «было то 135 в прошлом» / «на то 40» в гарантии — история, не новое ТО.
    """
    code_s = m.group(1) if m.lastindex else ""
    if not code_s:
        return True
    try:
        code = int(code_s)
    except ValueError:
        return True
    if not _mileage_to_interval_code_km_valid(code):
        return True
    abs_start, abs_end = m.start(), m.end()
    tail = (low or "")[abs_end : min(len(low or ""), abs_end + 12)]
    # Нормализованный код ТО может быть в форме «то 120 000».
    # Дополнительные «000» здесь не признак цены и не должны отбрасывать маркер.
    # Но «... то 30-го/29-е число ...» после нормализации может выглядеть как «то 30 000 -го»:
    # это дата записи, а не код ТО-30000.
    if re.match(r"\s+000\s*[-–]?\s*(?:го|е|й|м|х)\b", tail, re.I):
        return True
    if re.match(r"\s+000\b", tail):
        pass
    elif re.match(r"\s+\d", tail):
        return True
    # 17932: «то 10:00» — союз + время слота, не регламент ТО-10.
    if re.match(r"\s*:\d{2}\b", tail):
        return True
    narrow = (low or "")[max(0, abs_start - 55) : abs_end + 55]
    # 18670: прошлое ТО и текущее ТО могут стоять рядом:
    # «прошлым летом делали ТО-60, сейчас ТО-75». Маркер прошлого относится
    # к первому интервалу, а «сейчас ТО-Y» обозначает новое обслуживание.
    prefix = (low or "")[max(0, abs_start - 120) : abs_start]
    if re.search(
        r"\b(?:прошл\w*|раньше)\b[^.!?]{0,80}\bто\s*[-]?\s*\d{2,3}\b"
        r"[^.!?]{0,35}\b(?:сейчас|теперь)\b[^.!?]{0,20}$",
        prefix,
        re.I,
    ):
        return False
    if re.search(r"\b(?:было|были|прошл\w*|раньше|обслуживал\w*|заезжал\w*)\b", narrow, re.I):
        if not re.search(
            r"\b(?:можно\s+сделать|запис|сделать|пройти|нужно|надо|хотел\w*\s+запис)\b",
            narrow,
            re.I,
        ):
            return True
    if re.search(rf"\bна\s+то\s+{re.escape(code_s)}\b", narrow, re.I):
        if any(
            x in narrow
            for x in ("гарант", "истори", "меняли", "заканчивается", "значит это")
        ):
            return True
    return False


def _mileage_to_interval_code_present(low: str) -> bool:
    """Явный код регламентного ТО по пробегу: «ТО-105», «регламент ТО-75», «ТО 60»."""
    for m in re.finditer(
        r"\b(?:регламент\w*\s+)?то\s*[-]?\s*(\d{2,3})\b",
        low or "",
        re.I,
    ):
        if not _mileage_to_interval_code_match_is_false_positive(low, m):
            return True
    return False


def _stt_expand_mileage_to_interval_code_sub(m: re.Match) -> str:
    code_s = m.group(1)
    try:
        code = int(code_s)
    except ValueError:
        return m.group(0)
    if _mileage_to_interval_code_km_valid(code):
        return f" то {code} 000 "
    return m.group(0)


def _normalize_text(text: str) -> str:
    t = (text or "").lower()
    # Частые унификации для STT/смешанной латиницы.
    t = t.replace("ё", "е")
    # STT 17574: «гос номер» → госномер (CRM-паспорт авто).
    t = re.sub(r"\bгос\s+номер\b", "госномер", t, flags=re.IGNORECASE)
    # STT: пропущена «а» / «в» вместо «а» в «автомобиль».
    t = re.sub(r"\bвтомобил", "автомобил", t, flags=re.IGNORECASE)
    t = re.sub(r"\bатомобил", "автомобил", t, flags=re.IGNORECASE)
    # 19845: смешанная латиница/кириллица «Chrри» = Chery.
    t = re.sub(
        r"\b(?:chrри|chрри|chри)\b",
        " chery ",
        t,
        flags=re.IGNORECASE,
    )
    # 19798: «по вашему автомобилю Ч4 проба» = Chery T4 Pro Max.
    # Только в контексте названного автомобиля, чтобы не расширять голое «ч4».
    t = re.sub(
        r"\b((?:по\s+)?(?:ваш\w+\s+)?(?:автомобил\w*|машин\w*)[\s\S]{0,40}?)"
        r"\bч\s*4\s*проб[аы]\b",
        r"\1 chery t4 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 26668: «автомобиль Ч7, госномер…» = Chery Tiggo 7.
    # Применяем только в авто-контексте (автомобиль/машина/госномер), чтобы не расширять
    # произвольные короткие коды вне темы авто.
    t = re.sub(
        r"\b((?:по\s+)?(?:ваш\w+\s+)?(?:автомобил\w*|машин\w*)[\s\S]{0,10}?)"
        r"\bч\s*([4789])\b",
        r"\1 chery tiggo \2 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bч\s*([4789])\s*(?:[,.;:]|\s)\s*госномер\b",
        r" chery tiggo \1 госномер ",
        t,
        flags=re.IGNORECASE,
    )
    # 19716: «C,S7мрка L» = Chery Tiggo 7 L.
    t = re.sub(
        r"(?<![a-zа-яё0-9])[cс]\s*[,.;:]?\s*[sс]\s*7\s*мрка\s*[lл]"
        r"(?![a-zа-яё0-9])",
        " chery tiggo 7 l ",
        t,
        flags=re.IGNORECASE,
    )
    # STT-обломки рано: «чhr»/«чhр» → чери, «tиg»/«тиg» → тигго — до голых «7 про» (без лишних пробелов: иначе «чери  7» ломает (?<!чери ) перед «7 про»).
    t = re.sub(r"\bчh[рr]\b", "чери", t)
    # STT 17640: «Чl Т1» / «Чл Т1» (лат. l) — Chery/Tenet T1 (не только greeting «Чери»).
    t = re.sub(
        r"\bч[lл]\s*т\s*[-]?\s*1\b",
        " chery tenet t1 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 14677: «Чртиг 7L» → Tiggo 7 L.
    t = re.sub(r"\bчртиг\b", " тигго ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчтиг\b", " тигго ", t, flags=re.IGNORECASE)
    # STT 14644: «4тыре-4ре» / «4тыре» → Tenet T4.
    t = re.sub(r"\b4-?4ре\b", " тенет t4 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b4тыре\b", " тенет t4 ", t, flags=re.IGNORECASE)
    # STT 14644: «Тэнет сергу» ≈ Tenet Tiggo; «наЦеН 107» ≈ Tenet T7.
    t = re.sub(r"\b(?:тэнет|тенет|tenet)\s+сергу\b", " тенет тигго ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bнацен\s*107\b", " тенет t7 ", t, flags=re.IGNORECASE)
    # STT 14744: «CherTeleperMax» — Chery Tiggo 8 Pro Max.
    t = re.sub(r"\bchertelepermax\b", " chery tiggo 8 pro max ", t, flags=re.IGNORECASE)
    # STT 13966: «ЧrG» / «chrg» — Chery (латиница + кириллическая «р»).
    t = re.sub(r"\bchrg\b", "chery", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчrg\b", "чери", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:t|т)иg\b", "тигго", t)
    # STT 9233: «Chрry Tиg 8» — латиница + кириллическая «р»/«и».
    t = re.sub(r"\bch[еe]?[рr]{1,2}y\b", "chery", t, flags=re.IGNORECASE)
    t = re.sub(
        r"\bch[еe]?[рr]{1,2}y\s+t[иi]g\s*([4789])\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bt[иi]g\s*([4789])\b", r" tiggo \1 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bилерский\s+центр\b", "дилерский центр", t)
    t = t.replace("т.о.", " то ").replace("т.о", " то ")
    t = t.replace("x trail", "xtrail").replace("x-trail", "xtrail")
    # STT: латинские «ss» вместо «сс» в «Ниссан» («Ниssан», «ниSSан», «Ниssaн»).
    t = re.sub(r"\bни[s]{2,}a?н\b", "ниссан", t, flags=re.IGNORECASE)
    t = re.sub(r"\bn[i]?[s]{2,}a?n\b", "nissan", t, flags=re.IGNORECASE)
    # STT matrix: «Nисsaн», «Нисsан», «nиссан» и др. — единая нормализация в nissan (17340).
    t = _NISSAN_STT_MATRIX_RE.sub(" nissan ", t)
    # STT 18548: смешанная форма с русским падежным окончанием
    # «Нисsана / Нисsану / Нисsане» → Nissan.
    t = re.sub(
        r"\bн[иi][сc]s[аa][нn](?:[аa]|у|е|ом|ы)?\b",
        " nissan ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 16331: «Ниssan» — кириллическое «ни» + латинское «ssan».
    t = re.sub(r"\bн[иi]ssan\b", " nissan ", t, flags=re.IGNORECASE)
    # STT 16247: «Nисsan» — латинское N + кириллическое «ис» + латинское «san».
    t = re.sub(r"\bn[иi][сc]ssan\b", " nissan ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bn[иi][сc]san\b", " nissan ", t, flags=re.IGNORECASE)
    # STT 17882: «Нессan» / «Нессан» — «и»→«е», двойное «с» (вне матрицы N+и+s+a+n).
    t = re.sub(r"\bнессan\b", " nissan ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bнессан\w*\b", " nissan ", t, flags=re.IGNORECASE)
    # 19540: полностью латинское «Nessan» само по себе = Nissan.
    t = re.sub(r"\bnessan\b", " nissan ", t, flags=re.IGNORECASE)
    # 28257: латинское «Neissan/Neissan/Neisan» (в т.ч. перед моделью Terrano/Terrana) = Nissan.
    t = re.sub(r"\bnei+s{1,2}a?n\w*\b", " nissan ", t, flags=re.IGNORECASE)
    # 28270: «исsан» / «исsan» отдельно (без модели) → nissan.
    t = re.sub(r"\bисs[аa][нn]\b", " nissan ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bисsan\b", " nissan ", t, flags=re.IGNORECASE)
    # STT: X-Trail кириллицей «Экстрелл», «Экстрел», «Экстрейл» и склейки «икстрелл».
    for _wrong in ("экстрелл", "экстрел", "экстрейл", "экстрели", "экстрелю"):
        t = t.replace(_wrong, "икстрейл")
    # STT 16074: «Cрри Tгo 7» / «срри тго 7» ≈ Chery Tiggo 7 (mixed Latin/Cyrillic).
    # До замены «тго»→«то», иначе «срри тго 7» превращается в «срри седьмое то».
    t = re.sub(
        r"\b(?:c|с)(?:r|р){2}(?:i|и)?\s+(?:t|т)(?:g|г)(?:o|о)\s*([4789])\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    # Типичные искажения STT (ТО / модели Nissan).
    t = re.sub(r"\bмнто\b", " то ", t)
    # 21150: STT «НТО» в клиентской фразе «хотел записать ... НТО» = «на ТО».
    t = re.sub(r"\bнто\b", " на то ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтго\b", " то ", t)
    # STT 13702: «на ТЛ» / «КНТО» = на ТО / ТО (запись).
    t = re.sub(r"\bна\s+тл\b", " на то ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bкнто\b", " на то ", t, flags=re.IGNORECASE)
    # STT 22457: «мне нужно ТЦеЛО/ТЛО пройти» = «мне нужно ТО пройти».
    t = re.sub(r"\b(?:тцело|тло)\b(?=\s+пройти\b)", " то ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bпройти\s+(?:тцело|тло)\b", " пройти то ", t, flags=re.IGNORECASE)
    # STT 16743: «завтраНет» = завтра нет; «30ать тысяч» = пробег 30 000 км (регламентное ТО).
    t = re.sub(r"\bзавтранет\b", " завтра нет ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b30ать\s+тысяч\b", " 30 000 ", t, flags=re.IGNORECASE)
    # STT 11354: «на киошку» = «на тэошку» / на ТО (жаргон); «тог» вместо «ТО» у диспетчера.
    t = re.sub(r"\bкиошк\w*\b", " то ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтог\b", " то ", t, flags=re.IGNORECASE)
    # STT 21023: «СЭО / ТЭО» вместо «ТО» (запись + пробег 50 000).
    t = re.sub(r"\bсэо\b", " то ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтэо\b", " то ", t, flags=re.IGNORECASE)
    # STT 10930: «нулевое ТОрем» — склейка «ТО» + «рем» в составе нулевого ТО.
    t = re.sub(r"\bнулев\w*\s+торем\b", " нулевое то ", t, flags=re.IGNORECASE)
    # STT 16634: «слух в машине появился» при трогании — стук (не «слух»).
    if re.search(r"\bслух\b", t, re.I) and re.search(
        r"\b(?:трога|машин|отвал|газ|подвеск|слыш|стук)\b",
        t,
        re.I,
    ):
        t = re.sub(r"\bслух\b", "стук", t, flags=re.I)
    # STT 10258: «ТтО» / «ТТО» вместо «ТО»; «МТО» вместо «ТО» («на МТО подъедет»).
    t = re.sub(r"\bт{2,}о\b", " то ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bмто\b", " то ", t, flags=re.IGNORECASE)
    # STT 10330: «ТтоО» / «ттоо» — объёмное/необъёмное ТО (двойное «о» в ASR).
    t = re.sub(
        r"\b(не)?объемн\w*\s+т{2,}о+\b",
        r"\1объемное то",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bт{2,}о+\s+необъемн\w*\b",
        " необъемное то ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bто\s+необъемн\w*\b",
        " необъемное то ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bт{2,}о+\b", " то ", t, flags=re.IGNORECASE)
    # STT 18921: вопрос диспетчера «на ТО хотела записаться?» распался на
    # «Дачего хотела записаться?» / «а кого хотела записаться?».
    t = re.sub(
        r"\b(?:дачего|а\s+кого)\s+(?:хотел\w*\s+)?записаться\b",
        " на то записаться ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 10330: «пройти его» вместо «пройти ТО» — только с глаголом «пройти».
    t = re.sub(r"\bпройти\s+его\b", " пройти то ", t, flags=re.IGNORECASE)
    # STT: «очередное пройти» / «пвоочередное пройти» без «ТО».
    t = re.sub(
        r"\b(?:пво)?очередн\w*\s+пройти\b",
        " очередное то пройти ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «необходимо ТО провести» — перестановка для маркера «провести то».
    t = re.sub(
        r"\bнеобходимо\s+то\s+провести\b",
        " необходимо провести то ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 9603: «Пиот» вместо «ТО» при запросе стоимости по пробегу.
    t = re.sub(r"\bпиот\b", " то ", t, flags=re.IGNORECASE)
    # STT 12475: «по стоимости стого/стового» — стoimosti TO.
    t = re.sub(r"\bстоимост\w*\s+стог\w*\b", " стоимость то ", t, flags=re.IGNORECASE)
    t = re.sub(
        r"\b(?:tenet|тенет|тэнет|тент)\s+[тt]\s*4\s*pr\b",
        " тенет t4 pro ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:tenet|тенет|тэнет|тент)\s+[тt]4pr\b",
        " тенет t4 pro ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 12475: «по стoimosti tretiya» — порядковое ТО без «то» после числительного.
    t = re.sub(
        r"\b(стоимост\w*)[^.!?]{0,40}\b(перв|втор|трет|четвер|четвёрт|пят|шест)(\w*)\b",
        _stoimost_ordinal_gap_repl,
        t,
        flags=re.IGNORECASE,
    )
    # STT 9651: «Т прохожу обслуживание» вместо «ТО прохожу».
    t = re.sub(r"\bт\s+прохожу\b", " то прохожу ", t, flags=re.IGNORECASE)
    # STT: «О провести 150 000» = регламентное ТО на пробеге 150 тыс. км.
    t = re.sub(
        r"\bо\s+провести\s+(\d{2,3})\s+(\d{3})\b",
        r" провести то \1 \2 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bпровести\s+(\d{2,3})\s+(\d{3})\s*(?:км|km)?\b",
        r" провести то \1 \2 км ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «ТО-7» / «то 7» на приборке (часто рядом с «SIM»); для узкого слоя важен сам маркер ТО, не расшифровка.
    t = re.sub(r"\bто\s*[-–]?\s*(?:7|７)\b", " седьмое то ", t, flags=re.IGNORECASE)
    # STT 9760: «седь-е ТО», «перв-е то» — порядковое N-е ТО.
    t = re.sub(r"\bседь\s*-\s*е\s+то\b", " седьмое то ", t, flags=re.IGNORECASE)
    t = re.sub(
        r"\b(нулев|нолев|перв|втор|трет|четвер|четвёрт|пят|шест|восьм|девят|десят)\s*-\s*е\s+то\b",
        r" \1ое то ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 21636: «нулевой-то записаться» — дефис между порядковым и «то».
    t = re.sub(
        r"\b((?:нулев|нолев|перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят)\w*)\s*-\s*то\b",
        r" \1 то ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «ТО «Сим»» / «то сим» (приборка, путают с SIM/«ТО-7») → плановое то — явный регламентный ТО (8152).
    t = re.sub(
        r"\bто\s*[\u00ab\u00bb«»\"']*\s*сим\b\s*[\u00ab\u00bb«»\"']?",
        " плановое то ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «на ТО» как «нато» / «натого»; склейка «записатьсянато» (8148, 11355).
    t = re.sub(r"\bнатого\b", " на то ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bнато\b", " на то ", t)
    # STT 11148: «на Т ТО» / «на т то» вместо «на ТО».
    t = re.sub(r"\bна\s+т\s+то\b", " на то ", t, flags=re.IGNORECASE)
    t = re.sub(
        r"(записаться|записать|запишусь|запись|записываюсь|записываемся)нат(?:о|ого)\b",
        r"\1 на то ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 13785: «на И ТУ» / «на ту» = на ТО (запись).
    t = re.sub(r"\bна\s+и\s+ту\s+запис", " на то запис", t, flags=re.IGNORECASE)
    t = re.sub(r"\bна\s+ту\s+запис", " на то запис", t, flags=re.IGNORECASE)
    # STT 17562: «на ПО сделать» / «ПО сделать» ≈ «на ТО сделать».
    t = re.sub(r"\bна\s+по\s+сделать\b", " на то сделать ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bпо\s+сделать\b", " то сделать ", t, flags=re.IGNORECASE)
    # STT 8116: «ТОО» / «ТООО» вместо «ТО» перед пробегом (60 000 км).
    t = re.sub(r"\bт(?:о){2,}\s+(?=\d)", " то ", t, flags=re.IGNORECASE)
    # Склейки ordinal+«то»: «второйто/третьето/первоето» -> «второе то/третье то/первое то».
    t = re.sub(r"\bвтор(?:ой|ое|ого|ому)\s*то\b", " второе то ", t)
    t = re.sub(r"\bтреть(?:е|ий|его|ему)\s*то\b", " третье то ", t)
    t = re.sub(r"\bперв(?:ый|ое|ого|ому)\s*то\b", " первое то ", t)
    # STT 16779: «на ТО Второе» / «ТО Второе» → «на второе то» (порядок слов в CRM-заявке).
    t = re.sub(
        r"\bна\s+то\s+(нулев|нолев|перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят)\w*\b",
        r" на \1ое то ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bто\s+(нулев|нолев|перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят)"
        r"(?:ое|ой|ий|ье|ый)?\b",
        r" \1ое то ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 12945: «первого/второго/… ТО» — родительный падеж порядкового.
    t = re.sub(
        r"\b(нулев|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят)(?:ого|ому|ему|ым)\s+то\b",
        r" \1ое то ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 11299: «Первое Т» / «на первое т» — обрезанное «ТО» (одна буква «т»).
    t = re.sub(
        r"\b(нулев|нолев|перв|втор|трет|четвер|четвёрт|пят|шест|седьм?\w*|восьм|девят|десят)"
        r"(?:ое|ой|ий|ье|ая|ого|ому|ему|ым)?\s+т\b",
        r" \1ое то ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 19988: «первое ттода / второе тода» -> «первое то / второе то».
    t = re.sub(
        r"\b(нулев|нолев|перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят)"
        r"(?:ое|ой|ий|ье|ая|ого|ому|ему|ым)?\s+т{1,3}од[аоыуе]?\b",
        r" \1ое то ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 9506: «нулевоето» — склейка «нулевое ТО».
    t = re.sub(r"\bнулев(?:ое|ой|ом)?то\b", " нулевое то ", t, flags=re.IGNORECASE)
    # 18298: «ТО один сделать» / «Тодин» = первое ТО (пробег ~10 000).
    t = re.sub(r"\bто\s*один\b", " первое то ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтодин\b", " первое то ", t, flags=re.IGNORECASE)
    # 18289: «нулевой это самое тело» ≈ «нулевое это самое ТО» (STT тело→ТО).
    t = re.sub(
        r"\b(?:нулев|нолев)(?:ой|ое|ом)\s+это\s+самое\s+тело\b",
        " нулевое то ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:нулев|нолев)(?:ой|ое|ом)\b([^.!?]{0,35}?)\bтело\b",
        r" нулевое то \1 ",
        t,
        flags=re.IGNORECASE,
    )
    # 18289: «Нулевой это … 14 300 ₽ по стоимости» — голое «нулевой» = нулевое ТО + смета.
    t = re.sub(
        r"\b(?:нулев|нолев)(?:ой|ое|ом)\s+это\b(?=[^.!?]{0,90}?(?:\d[\d\s]{1,10}\s*(?:₽|руб)|стоимост))",
        " нулевое то это ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 20895: краткий ответ клиента «Семь Про» = Chery/Tenet 7 Pro.
    t = re.sub(r"\bсемь\s+про\b(?!\s*ц)", " тигго 7 pro ", t, flags=re.IGNORECASE)
    # STT 10918: «ТО0» / «то 0» = нулевое ТО (запрос стоимости).
    t = re.sub(r"\bто\s*0\b", " нулевое то ", t, flags=re.IGNORECASE)
    # STT 11291: «стоить ТО80 000» / «ТО80 000» — стоимость регламентного ТО на пробеге (80 тыс.).
    t = re.sub(
        r"\b(?:стоит|стоимост\w*)\s+то(\d{2,3})\s*0{3}\b",
        r" стоимость то \1 000 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bто(\d{2,3})\s*0{3}\b",
        r" то \1 000 ",
        t,
        flags=re.IGNORECASE,
    )
    # «то-80» / «то-105» — код регламентного ТО по пробегу (шаг 5 тыс. км), не «то-8»+«0».
    t = re.sub(
        r"\bто-(\d{2,3})\b",
        _stt_expand_mileage_to_interval_code_sub,
        t,
        flags=re.IGNORECASE,
    )
    # 17932: «то 10:00» — союз + время, не код ТО-10 (10000 км).
    t = re.sub(
        r"\bто\s+(\d{2,3})\b(?!\s+\d)(?!\s*:\d{2})",
        _stt_expand_mileage_to_interval_code_sub,
        t,
        flags=re.IGNORECASE,
    )
    # STT 9163/10727: «N-е ИТО» / «итоo» = порядковое ТО (ASR «ТО» → «ито»).
    t = re.sub(
        r"\b(нулев|перв|втор|трет|четверт|четвёрт|пят|шест|седьм|восьм|девят|десят)\w*\s+ито+\b",
        r"\1ое то",
        t,
        flags=re.IGNORECASE,
    )
    # STT 9163: «О4, сколько будет» в начале разговора = 4-е ТО, не модель (рядом запрос цены).
    _lim_o4 = min(len(t), 2400)
    _head_o4, _tail_o4 = t[:_lim_o4], t[_lim_o4:]
    if re.search(r"\b(?:сколько|подскаж|стоимост|цен\w*|узнать|вообще)\b", _head_o4):
        _head_o4 = re.sub(
            r"\bо\s*4\b(?!\s*(?:pro|new|пром|prom|nw)\b)",
            " то-4 ",
            _head_o4,
            flags=re.IGNORECASE,
        )
    t = _head_o4 + _tail_o4
    # STT: «первоые ТВ» / «первые тв» вместо «первое ТО» при записи на сервис (8453).
    t = re.sub(r"\bперв\w*\s+тв\b", " первое то ", t, flags=re.IGNORECASE)
    # STT 8639: «на левой ТО» / «левое ТО» вместо «на первое ТО» (ASR «первое» → «левой»).
    t = re.sub(r"\bна\s+лев(?:ой|ое)\s+то\b", " на первое то ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bлев(?:ой|ое)\s+то\b", " первое то ", t, flags=re.IGNORECASE)
    # STT 7780: «Ноловой КО» / «нолевой ko» = «нулевое ТО» (лат. KO или «ко» вместо «то»).
    _stt_ko_as_to = r"[кk][оoо]"
    t = re.sub(
        rf"\bноло[вв](?:ой|ое|ом)\s+{_stt_ko_as_to}\b",
        " нулевое то ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        rf"\bнолев(?:ой|ое|ом)\s+{_stt_ko_as_to}\b",
        " нулевое то ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        rf"\bнулев(?:ой|ое|ом)\s+{_stt_ko_as_to}\b",
        " нулевое то ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 7780: «нулевом по» / «нулевое по» — «по» вместо «то» в контексте нулевого ТО.
    t = re.sub(r"\bнулевом\s+по\b", " нулевом то ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bнулевое\s+по\b", " нулевое то ", t, flags=re.IGNORECASE)
    # STT: «на кого/того записаться», «надо кого записаться» -> «на то записаться».
    t = re.sub(
        r"\b(?:на|надо)\s+(?:кого|того)\s+записа(?:ться|т[ьт])\b",
        " на то записаться ",
        t,
    )
    # STT 21568: «на О записаться» — потеря буквы «т» в «ТО».
    t = re.sub(
        r"\bна\s+о\s+записа(?:ться|т[ьт]|ть)\b",
        " на то записаться ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bxtrile\b", "xtrail", t)
    # 19540: сокращённое STT «Xtrl» само по себе = Nissan X-Trail.
    t = re.sub(r"\bxtrl\b", "xtrail", t, flags=re.IGNORECASE)
    t = re.sub(r"\bикстрел+\b", "икстрейл", t)
    # STT: «чериенсервис» / «черисервис» / «снсервис» — не марка авто, склейка «Чери + сервис» в приветствии дилера.
    t = t.replace("чериенсервис", " ")
    t = t.replace("черисервис", " ")
    # STT 13785: «Члюсти»/«Челсти» в приветствии линии без слова «сервис».
    t = re.sub(r"\bч(?:ел|лю)сти\b", " чери сервис ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:ен|итм|сн)сервис\b", " ассистент сервиса ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bextrel+le\b", "xtrail", t, flags=re.IGNORECASE)
    t = re.sub(r"\bextrill\b", "xtrail", t, flags=re.IGNORECASE)
    t = re.sub(r"\bextrall\b", "xtrail", t, flags=re.IGNORECASE)
    t = re.sub(r"\bextrele\b", "xtrail", t, flags=re.IGNORECASE)
    # STT 24184: «EXtrall» / «Extreйл» для Nissan X-Trail.
    t = re.sub(r"\bextre[йy][лl]\b", "xtrail", t, flags=re.IGNORECASE)
    # STT 8761: Nissan Qashqai — «Kashka», «Kashkai», «Casain», «Кашхай».
    t = re.sub(r"\bkashka\b", "kashkai", t, flags=re.IGNORECASE)
    t = re.sub(r"\bcasain\b", "qashqai", t, flags=re.IGNORECASE)
    t = re.sub(r"\bкашхай\b", "кашкай", t, flags=re.IGNORECASE)
    # STT 14517: «NissanPаski» / «Nissan Qashkai» — склейка марки и модели.
    t = re.sub(
        r"\bnissan(?:p[aа]sk\w*|\s+(?:qash|kash|pask)\w*)\b",
        " nissan qashqai ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 8025: Nissan X-Trail — «Extri», «Extraile», «Nиssan», «Нисsан».
    t = re.sub(r"\bn\u0438ssan\b", "nissan", t)
    t = re.sub(r"\bn\u0438ssa\u043d\b", "nissan", t)
    t = re.sub(r"\bнисsан\b", "ниссан", t)
    # STT 16485: «Нисsaн» — кириллическое «Нис» + латинское «saн».
    t = re.sub(r"\bн[иi][сc]s[аa][нn]\b", " nissan ", t, flags=re.IGNORECASE)
    # STT 16485: Nissan Juke — «Жук» кириллицей.
    t = re.sub(
        r"\b(?:nissan|ниссан|нисан)\s+жук\b",
        " nissan juke ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 16331: Nissan Micra — «Микроo», «Микро»; повтор диспетчера «Микро, госномер».
    t = re.sub(
        r"\b(?:nissan|нissan|ниссан|нисан)\s+микро\w*\b",
        " nissan micra ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bмикро\w*,?\s+госномер\b",
        " nissan micra госномер ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 16247: Nissan Titan — «Tиrn» (смешанная раскладка).
    t = re.sub(
        r"\b(?:nissan|нissan|ниссан|нисан)\s+t[иi]rn\b",
        " nissan titan ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bextri\b", "xtrail", t, flags=re.IGNORECASE)
    t = re.sub(r"\bextraile\b", "xtrail", t, flags=re.IGNORECASE)
    # STT: Nissan X-Trail — «Extral», «Extreil» (8811, 8843).
    t = re.sub(r"\bextral\b", "xtrail", t, flags=re.IGNORECASE)
    t = re.sub(r"\bextreil\b", "xtrail", t, flags=re.IGNORECASE)
    t = re.sub(r"\bextreyl\b", "xtrail", t, flags=re.IGNORECASE)
    # STT 8738: Nissan X-Trail — «Entrell», «Entrel».
    t = re.sub(r"\bentrell\b", "xtrail", t, flags=re.IGNORECASE)
    t = re.sub(r"\bentrele\b", "xtrail", t, flags=re.IGNORECASE)
    # STT 10648: «Nesan Patroll» / «Nesan Patrol» = Nissan Patrol.
    t = re.sub(r"\bnesan\s+patroll?\b", " nissan patrol ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bpatroll\b", "patrol", t, flags=re.IGNORECASE)
    # STT 14237 / 16709: «N Nessan Potfind» / «Nissan Potfider» = Nissan Pathfinder.
    t = re.sub(r"\b(?:n\s+)?nessan\s+potfid\w*\b", " nissan pathfinder ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bnissan\s+potfid\w*\b", " nissan pathfinder ", t, flags=re.IGNORECASE)
    # STT 27383: «Nissan Foltsfainer / Folkfiner / Potfainer» = Nissan Pathfinder.
    t = re.sub(r"\bnissan\s+folts?fain\w*\b", " nissan pathfinder ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bnissan\s+folkfin\w*\b", " nissan pathfinder ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bnissan\s+potfain\w*\b", " nissan pathfinder ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bсподфайнд\w*\b", " nissan pathfinder ", t, flags=re.IGNORECASE)
    # STT 13427: Nissan Tiida — «Ciдаa» / «Ниссанс-Зида» / «Ниsсan Ида».
    t = re.sub(r"\bн[иi]s[сc]an\b", " nissan ", t, flags=re.IGNORECASE)
    # STT 16395: «Nиссan» / «nиссан» — mixed Latin/Cyrillic Nissan.
    t = re.sub(r"\bn[иi][сc]{1,2}an\b", " nissan ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bn[иi][сc]{1,3}[aа][nн]\b", " nissan ", t, flags=re.IGNORECASE)
    # STT/падеж: «на Ниссане» / «на Nissanе» — предложный падеж марки.
    t = re.sub(
        r"\bна\s+(?:nissan|ниссан|нисан)(?:е|у|ом|ах)\b",
        " на nissan ",
        t,
        flags=re.IGNORECASE,
    )
    # Смешанная кириллица/латиница в Kia: «КIA», «Киa», «Kиа».
    t = re.sub(r"\b[кk][иi][аa]\b", " kia ", t, flags=re.IGNORECASE)
    # STT 13954: «на местали бы масло» ≈ «на Ниссане» (замена масла).
    t = re.sub(
        r"\bна\s+местали\b(?=[^.!?]{0,70}\bмасл)",
        " на nissan ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bnissan\s+c[iи]даa?\b", " nissan tiida ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bниссан[сc]?[-\s]*зида\b", " nissan tiida ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bnissan\s+ида\b", " nissan tiida ", t, flags=re.IGNORECASE)
    # STT 13474: Nissan Pathfinder — «Pдфаinдr» / «Подфайм» / «подфinr».
    t = re.sub(r"\bpдфаinдr\b", " nissan pathfinder ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bподфайм\b", " nissan pathfinder ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bподфinr\b", " nissan pathfinder ", t, flags=re.IGNORECASE)
    # STT 13412: Pathfinder — «Потфаймер» / «Пайдер» / «Файмер».
    t = re.sub(r"\bпотфайм\w*\b", " nissan pathfinder ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bпайдер\b", " nissan pathfinder ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bфаймер\b", " nissan pathfinder ", t, flags=re.IGNORECASE)
    # 28270: «Potфайнtr» отдельно (без марки) → pathfinder.
    t = re.sub(r"\bpotфайн\w*\b", " pathfinder ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b[pр]ot[фf]айн[tdтr]+\b", " pathfinder ", t, flags=re.IGNORECASE)
    # STT: «оксид» / «Oxid» — Exeed.
    t = re.sub(r"\bоксид\b", " exeed ", t, flags=re.IGNORECASE)
    t = re.sub(r"\boxid\b", " exeed ", t, flags=re.IGNORECASE)
    # STT 18187: LiXiang — «Лиссян» / «Лисян» / «полисяном».
    t = re.sub(r"\bполисян\w*\b", " lixiang ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bлиссян\w*\b", " lixiang ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bлисян[ьяе]?\w*\b", " lixiang ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bli\s*xiang\b", " lixiang ", t, flags=re.IGNORECASE)
    # STT: «разварасхождение» / «развалвхождение» → развал-схождение.
    t = re.sub(r"\bразвар?асхожден\w*\b", " развал схождение ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bразвалвхожден\w*\b", " развал схождение ", t, flags=re.IGNORECASE)
    # STT 15790: «Xсаit» / «Xca it» — Xcite (лат+кир).
    t = re.sub(r"\bx[сs][aа]\s*it\b", " xcite ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bx[сs][aа]it\b", " xcite ", t, flags=re.IGNORECASE)
    # STT 17497: «ИX саit» / «иx саit» — «и/i»+«x» отдельным токеном, затем «саit».
    t = re.sub(
        r"\b(?:и|i)\s*x\s+[сs][aа]\s*it\b",
        " xcite ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:и|i)x\s+[сs][aа]\s*it\b",
        " xcite ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 17525: «XitePr7» / «Xite Pr 7» — Xcite (не Nissan из «Чери Ниссан» у диспетчера).
    t = re.sub(r"\bxite\s*pr\s*\d*\b", " xcite ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bxite\b", " xcite ", t, flags=re.IGNORECASE)
    # Модель Xcite X-Cross (STT «XCross»).
    t = re.sub(r"\bx\s*-?\s*cross\b", " xcross ", t, flags=re.IGNORECASE)
    # STT 15791: «житурв» — Jetour.
    t = re.sub(r"\bжитурв\b", " джитур ", t, flags=re.IGNORECASE)
    # STT 16295: «джиtur» / «Джитур Т2» — Jetour T2 (mixed Latin/Cyrillic).
    t = re.sub(r"\bджи[tт]ur\b", " jetour ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:jetour|джитур|джетур)\s+т\s*[-]?\s*2\b", " jetour t2 ", t, flags=re.IGNORECASE)
    # STT 15562: «GeTurdashing» / «getur dashing» — Jetour Dashing.
    t = re.sub(r"\bgetur\s*dashing\b", " jetour dashing ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bgeturdashing\b", " jetour dashing ", t, flags=re.IGNORECASE)
    # STT: «Finit» / «finit» — обрезание «In» в Infiniti (группа Nissan).
    t = re.sub(r"\bfinit\b", " infiniti ", t, flags=re.IGNORECASE)
    # 28989: «Инfiнити» — mixed Cyrillic/Latin без конечной «y/й».
    t = re.sub(
        r"\b[иi][нn][фf][иi][нn][иi][тt][иi]\b",
        " infiniti ",
        t,
        flags=re.IGNORECASE,
    )
    # 28224: mixed-форма «Инфiniтиy» и близкие варианты -> Infiniti (группа Nissan).
    t = re.sub(
        r"\bинф(?:i|и|н|n){0,2}(?:n|н)?(?:i|и){0,2}т(?:i|и)y\b",
        " infiniti ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b[иi][нn][фf][иi][нn][иi][тt][иi][йy]\b",
        " infiniti ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\binfini(?:ti|ty)\b", " infiniti ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчерепга\b", "чери", t)
    # STT 8833: «Чериига 7» ≈ Chery Tiggo 7.
    t = re.sub(r"\bчериига\s*7\b", " чери тигго 7 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчериига\b", " чери тигго ", t, flags=re.IGNORECASE)
    # STT 12647: «Череига 8» ≈ Chery Tiggo 8 (вариант «Чериига»).
    t = re.sub(r"\bчереига\s*([4789])\b", r" чери тигго \1 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчереига\b", " чери тигго ", t, flags=re.IGNORECASE)
    # STT 16335: «Терига 8» ≈ Chery Tiggo 8 (без «ч» в начале).
    t = re.sub(r"\bтерига\s*([4789])\b", r" chery tiggo \1 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтерига\b", " chery tiggo ", t, flags=re.IGNORECASE)
    # 19727: «Чиряреза, 8емь» / «Чиряреза восемь» = Chery Arrizo 8.
    t = re.sub(
        r"\bчиряреза\s*[,.;:]?\s*(?:8\s*емь|8|восемь|восем|восьм\w*)\b",
        " chery arrizo 8 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «ар 8» / «ar8» — сокращение Chery Arrizo 8.
    t = re.sub(r"\b(?:ar|ар)\s*8\b", " arrizo 8 ", t, flags=re.IGNORECASE)
    # STT 12923: Chery Arrizo 8 — «Aриiзa 8», mixed Latin/Cyrillic.
    t = re.sub(
        r"\b[aа][rр][iи]{1,3}[zз][aа]\s*(?:8|８|восем|восьм\w*|восьмой)\b",
        " arrizo 8 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 23052: «Cherr АAриiзa 8» -> Chery Arrizo 8 (лишняя «A» перед r в mixed-форме).
    t = re.sub(
        r"\b[aа]{2}[rр][iи]{1,3}[zз][aа]\s*(?:8|８|восем|восьм\w*|восьмой)\b",
        " arrizo 8 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b[aа][rр][iи]{1,3}[zз][aа]\b",
        " arrizo ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\b[aа]{2}[rр][iи]{1,3}[zз][aа]\b", " arrizo ", t, flags=re.IGNORECASE)
    # STT 16159: «Рiза 8» ≈ Chery Arrizo 8 (лат. «i» вместо «и», кир. «за»).
    t = re.sub(
        r"\b[рr][iи][zз][aа]\s*(?:8|８|восем|восьм\w*|восьмой)\b",
        " arrizo 8 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\b[рr][iи][zз][aа]\b", " arrizo ", t, flags=re.IGNORECASE)
    # STT 26326 / CRM: «CeriaRiza 8» = Chery Arrizo 8.
    t = re.sub(
        r"\bceria\s*riza\s*(?:8|８|восем|восьм\w*|восьмой)\b",
        " chery arrizo 8 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bceria\s*riza\b", " chery arrizo ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bceria\b", " chery ", t, flags=re.IGNORECASE)
    # STT 8572: Chery Arrizo — «ориза», «Лариза», «ариза» (дилер: «Arrizo?»).
    t = re.sub(
        r"\b(?:лариза|ориза|ариза|арризо|arizzo|серияриз|ризо|риза|rizo|riza)\b",
        "arrizo",
        t,
        flags=re.IGNORECASE,
    )
    # STT 10033 / 10449 / 14639: Chery Arrizo 8 — «Ареза восем», «Риза 8», «Серияриз 8».
    t = re.sub(
        r"\b(?:аре?за|areza|arizzo|ризо|риза|rizo|riza|серияриз|серия\s*риз)\s*(?:восем|восьм|восьмой|8|８)\b",
        " arrizo 8 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\b(?:аре?за|areza|arizzo|ризо|риза|rizo|riza)\b", " arrizo ", t, flags=re.IGNORECASE)
    # Единый voice/MAX кейс: «Чhr 7 Проo Макс» -> Chery Tiggo 7 Pro Max.
    t = re.sub(
        r"\bчh?r+\s*([4789])\s*пр[оo]+\s*макс\b",
        r" chery tiggo \1 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    # Перенос правил Tenet из MAX-ветки в основной нормализатор.
    t = re.sub(r"\b(?:тенет|тэнет|тенат|тонат)\s*(?:[тt]\s*)?4\b", " tenet t4 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:тенет|тэнет|тенат|тонат)\s*(?:[тt]\s*)?7\b", " tenet t7 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:тенет|тэнет|тенат|тонат)\s*(?:[тt]\s*)?8\b", " tenet t8 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:тенет|тэнет|тенат|тонат)\s*(?:[тt]\s*)?9\b", " tenet t9 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:тенет|тэнет|тенат|тонат)\s+четыр\w*\b", " tenet t4 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:тенет|тэнет|тенат|тонат)\s+сем\w*\b", " tenet t7 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:тенет|тэнет|тенат|тонат)\s+восем\w*\b", " tenet t8 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:тенет|тэнет|тенат|тонат)\s+девят\w*\b", " tenet t9 ", t, flags=re.IGNORECASE)
    # STT 9803: «на Кашкае» / «Ниссан Кашкае» — Qashqai.
    t = re.sub(r"\bкашкае\b", " кашкай ", t)
    t = re.sub(r"\bкашкаи\b", " кашкай ", t)
    # STT: голое «кашка» — Nissan Qashqai (не «кашкае/кашкаи» — граница слова).
    t = re.sub(r"\bкашка\b", " кашкай ", t)
    # STT: «Cheri» (лишняя i), «TiVa» / «tiva» вместо Tiggo.
    t = re.sub(r"\bcheri\b", "chery", t, flags=re.IGNORECASE)
    # STT 23052: «Cherr» в CRM-лиде -> Chery.
    t = re.sub(r"\bcherr\b", "chery", t, flags=re.IGNORECASE)
    # STT 22563: «ТТиг-8» / «ttig-8» — Chery Tiggo 8.
    t = re.sub(
        r"\b[tт]{2}[iи][gг]\s*[- ]?\s*([4789])\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 18643: смешанная кириллица/латиница
    # «Чеrtga 7Pро Мк» ≈ Chery Tiggo 7 Pro Max.
    t = re.sub(
        r"\bче[rр]t[gг][aа]\s*7\s*[pр][rр][oо](?:\s+мк)?\b",
        " chery tiggo 7 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    # «7Pро» в названии модели: латинская P + кириллические «ро».
    t = re.sub(r"\b7\s*[pр][rр][oо]\b", " 7 pro ", t, flags=re.IGNORECASE)
    # STT 8684: «Chrry» / «Chrry Tig 4Pro» ≈ Chery Tiggo 4 Pro.
    t = re.sub(r"\bchrry\b", "chery", t, flags=re.IGNORECASE)
    t = re.sub(r"\bchrry\s+tig\s*4\s*pro\b", " chery tiggo 4 pro ", t, flags=re.IGNORECASE)
    # STT 8667: «Чhery» (кириллическая «ч» + латиница) ≈ Chery.
    t = re.sub(r"\bчhery\b", "chery", t, flags=re.IGNORECASE)
    t = re.sub(r"\btiva\b", "tiggo", t, flags=re.IGNORECASE)
    # STT: «Chery Tig 4» / «Чhery Tig 4» без «go» и без Pro — Tiggo 4.
    t = re.sub(
        r"\b(?:chery|чери|чhery)\s+tig\s*([4789])\b",
        r"chery tiggo \1",
        t,
        flags=re.IGNORECASE,
    )
    # STT 12749: «Чери Тиг 4 Нюгас» — Tiggo 4 New (кириллица + STT «Нюгас» ≈ New).
    t = re.sub(
        r"\b(?:chery|чери|чhery)\s+(?:tig|тиг)\s*([4789])\s*"
        r"(?:нюгас|нюгаз|n[yu]gas|new|нью|ню)\b",
        r"chery tiggo \1",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:chery|чери|чhery)\s+(?:tig|тиг)\s*([4789])\b",
        r"chery tiggo \1",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:tig|тиг)\s*([4789])\s*(?:нюгас|нюгаз|n[yu]gas|new|нью|ню)\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 25693: «ЧTg 7L» (смешанная кириллица/латиница) — Chery Tiggo 7 L.
    t = re.sub(
        r"\bч\s*tg\s*7\s*[lл]\b",
        " chery tiggo 7 l ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 8586: «Тг 4 Nw» / «Tg 4 New» — Tiggo 4 New (Chery).
    t = re.sub(r"\bтг\s*([4789])\s*(?:nw|new)\b", r"chery tiggo \1", t, flags=re.IGNORECASE)
    t = re.sub(r"\btg\s*([4789])\s*(?:nw|new)\b", r"chery tiggo \1", t, flags=re.IGNORECASE)
    # STT 11283: «ChrTg 4 New» / «ChrTeg 4 New» — Chery Tiggo 4 New.
    t = re.sub(
        r"\bchr(?:teg|tg|tiga)\s*([4789])\s*(?:nw|new)\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 23465: «CherTg 4 New» — Chery Tiggo 4 New.
    t = re.sub(
        r"\bcher(?:teg|tg|tiga)\s*([4789])\s*(?:nw|new)\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bchr(?:teg|tg|tiga)([4789])(?:nw|new)\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 12536 / CRM: «CrTg 4 прога» — Chery Tiggo 4 Pro (Cr=Chery, Tg=Tiggo, прога=Pro).
    t = re.sub(
        r"\bcr\s*tg\s*([4789])\s*(?:прога|proga|pro|pr|про)\b",
        r" chery tiggo \1 pro ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bcrtg\s*([4789])\s*(?:прога|proga|pro|pr|про)\b",
        r" chery tiggo \1 pro ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bchr(?:teg|tg|tiga)\s*([4789])\s*(?:прога|proga|pro|pr|про)\b",
        r" chery tiggo \1 pro ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bchr(?:teg|tg|tiga)([4789])(?:прога|proga|pro|pr|про)\b",
        r" chery tiggo \1 pro ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 14931: «ЧrTG 4 прога» — Chery Tiggo 4 Pro (кириллическая «Ч», не лат. Cr/Chr).
    t = re.sub(
        r"\bч\s*r?\s*tg\s*([4789])\s*(?:прога|proga|pro|pr|про)\b",
        r" chery tiggo \1 pro ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bчrtg\s*([4789])\s*(?:прога|proga|pro|pr|про)\b",
        r" chery tiggo \1 pro ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 15940: «44—4 прога» / «44 4 прога» — Chery Tiggo 4 Pro (STT «тигго» → «44»).
    t = re.sub(
        r"\b44[\s\-—]+4\s*(?:прога|proga|pro|pr|про)\b",
        " chery tiggo 4 pro ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 12536: «Nissan Tираangos» (лат+кир) при чтении CRM Chery Tiggo 4 Pro — не Terrano/Tirana.
    t = re.sub(
        r"\bnissan\s+t[иi][рr][aаa]+ng\w*\b",
        " chery tiggo 4 pro ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bnissan\s+тиранга\b", " chery tiggo 4 pro ", t, flags=re.IGNORECASE)
    # STT 14158: Nissan Terrano — «Тирана», «Ferrana», «Fiac» (не Chery «тиранга»).
    t = re.sub(
        r"\b(?:nissan|нissan|нисan|nessan|ниссан|нисan)\s+(?:тиран\w*|tiran\w*|ferran\w*|fiac)\b",
        " nissan terrano ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bнissan\s+тиран\w*\b", " nissan terrano ", t, flags=re.IGNORECASE)
    # STT 12579 / CRM: «Ч4 ТГ4r» — Chery Tiggo 4.
    t = re.sub(r"\bч\s*4\s*тг\s*4r?\b", " chery tiggo 4 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bч4\s*тг\s*4r?\b", " chery tiggo 4 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bч4\s*тг4r?\b", " chery tiggo 4 ", t, flags=re.IGNORECASE)
    # STT 14579 / 14683: «C4рогус» / «С4рогус» / «Г4рогус» — Chery Tiggo 4 Pro.
    t = re.sub(r"\b[cсgг]4рогус\b", " chery tiggo 4 pro ", t, flags=re.IGNORECASE)
    # 28418: «Ч 4ро» / «Ч4ро» — Chery Tiggo 4 Pro (обломок «pro» → «ро»).
    t = re.sub(r"\bч\s*4ро\b", " chery tiggo 4 pro ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bч\s*4\s*ро\b", " chery tiggo 4 pro ", t, flags=re.IGNORECASE)
    # STT 14801: «CT 4rogу» / «CT 4rog» — Chery Tiggo 4 Pro (латиница CT + обломок rogу).
    t = re.sub(r"\bct\s*4\s*rog[uуy]\b", " chery tiggo 4 pro ", t, flags=re.IGNORECASE)
    # STT 14547 / 15432: «Чг7 Эльгус» / «Че Г7 Элигус» — Chery Tiggo 7 L.
    _t7_eligus = r"(?:эль?гус|элигус)"
    t = re.sub(rf"\bчг\s*7\s*{_t7_eligus}\b", " chery tiggo 7 l ", t, flags=re.IGNORECASE)
    t = re.sub(rf"\bчг7\s*{_t7_eligus}\b", " chery tiggo 7 l ", t, flags=re.IGNORECASE)
    t = re.sub(rf"\bче\s*г\s*7\s*{_t7_eligus}\b", " chery tiggo 7 l ", t, flags=re.IGNORECASE)
    t = re.sub(rf"\bче\s*г7\s*{_t7_eligus}\b", " chery tiggo 7 l ", t, flags=re.IGNORECASE)
    # STT 18023: «Че7еромакс» / «Че4еромакс» — Че + цифра 4/7/8/9 + обломок + (про) макс.
    t = re.sub(
        r"\bче([4789])[a-zа-яё]{0,12}(?:макс|max)\b",
        r" chery tiggo \1 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bче([4789])[a-zа-яё]{0,12}(?:про|pro|пр)\b",
        r" chery tiggo \1 pro ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 16881: «Тиг семель» / «Семь мель» ≈ Tiggo 7L (семь + эль).
    t = re.sub(r"\bтиг\s+семель\b", " chery tiggo 7 l ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтигго\s+семель\b", " chery tiggo 7 l ", t, flags=re.IGNORECASE)
    # STT 21653: «чи семель» в ответе на «какой автомобиль?» = Chery 7 L.
    t = re.sub(r"\bчи\s+семел(?:ь|и)?\b", " chery tiggo 7 l ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bсемь\s+мель\b", " tiggo 7 l ", t, flags=re.IGNORECASE)
    # 19845: ответ на «какой автомобиль?» — «Семь Эль» = Chery Tiggo 7 L.
    t = re.sub(r"\bсемь\s+эль\b", " chery tiggo 7 l ", t, flags=re.IGNORECASE)
    # 11237: «Чери чига 7л» / «Чери ... 7 л» -> Chery Tiggo 7 L.
    t = re.sub(
        r"\b(?:chery|чери|черри)\s+чиг+а\s*7\s*(?:л|l|эль|ель)\b",
        " chery tiggo 7 l ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:chery|чери|черри)\s+чиг+а\s*([4789])\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:chery|чери|черри)\b(?:\s+[a-zа-яё0-9-]{1,8}){0,2}\s+7\s*(?:л|l|эль|ель)\b",
        " chery tiggo 7 l ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 14549: «Рно Флюн» — Renault Fluence.
    t = re.sub(r"\bрно\s*флюн\b", " renault fluence ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bрно\s*флюенс\b", " renault fluence ", t, flags=re.IGNORECASE)
    # STT 11706: «4NU» / «G4NU» / «черезG4NU» — Chery Tiggo 4 New (склейка «4N» + «U» из New).
    t = re.sub(r"(?<=[a-zа-я])g4nu\b", " chery tiggo 4 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:tg|tig|g)?4nu\b", " chery tiggo 4 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b4n\s*u\b", " chery tiggo 4 ", t, flags=re.IGNORECASE)
    # До catch-all «чер…→chery»: иначе «Черега/Черетига/ЧереЧга» теряют модель.
    # STT 26371: «череГСеми» ≈ Chery Tiggo 7 L (Chery + G/Tiggo + семи=7 + L-линейка).
    t = re.sub(r"\bчерегсеми\b", " chery tiggo 7 l ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчере\s*г\s*семи\b", " chery tiggo 7 l ", t, flags=re.IGNORECASE)
    # STT 26027: «ЧереЧга» ≈ Chery Tiggo; «чере» (кроме «через») ≈ Chery.
    t = re.sub(r"\bчеречга\b", " chery tiggo ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчерега\b", " chery tiggo ", t, flags=re.IGNORECASE)
    # 28611: «Чирегс Инромакс» ≈ Chery Tiggo ... Pro Max.
    t = re.sub(r"\bчирегс\b", " chery tiggo ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bинромакс\b", " pro max ", t, flags=re.IGNORECASE)
    t = re.sub(
        r"\bчеретига\s*([4789])\s*промаакс\b",
        r" chery tiggo \1 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bчеретига\s*([4789])\b", r" chery tiggo \1 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчеретига\b", " chery tiggo ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчере\b", " chery ", t, flags=re.IGNORECASE)
    # STT 14057: «Черезигус» — «через»+буквы (склейка, не предлог «через …») ≈ Chery.
    # Не «черного/черной цвета» (14463: «чёрного» → «черного» после ё→е).
    t = re.sub(
        r"\bчер(?!ез\b)(?!н(?:ый|ого|ой|ое|ые|ая)\b)[a-zа-яё]{2,}\w*\b",
        " chery ",
        t,
        flags=re.IGNORECASE,
    )
    # «Через 4/7/8/9», «Через N про [макс]» — Chery Tiggo (предлог «через месяц» не трогаем).
    t = re.sub(
        r"\bчерез\s+([4789])\s*(?:про|pro|пр)\s*(?:макс|max)\b",
        r" chery tiggo \1 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bчерез\s+([4789])\s*(?:про|pro|пр)\b",
        r" chery tiggo \1 pro ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bчерез\s+([4789])\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bчерез\s+(?:про|pro|пр)(?:\s*(?:макс|max))?\b",
        " chery ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 8068: «ЧhrTИga 4 Ug» (латиница + кириллица) ≈ Chery Tiggo 4 Pro; «ug» = pro.
    t = re.sub(r"\bчhrtиg[aoао]\s*4\s*ug\b", " chery tiggo 4 pro ", t)
    # STT: «Cheri TiVa 7Pro Max» → Chery Tiggo 7 Pro Max
    t = re.sub(r"\bchery\s+tiggo\s+([4789])pro\b", r"chery tiggo \1 pro", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчери\s+тигго\s+([4789])про\b", r"чери тигго \1 про", t, flags=re.IGNORECASE)
    # STT: «Тига 4», «Tiga4» → Tiggo 4 (Chery)
    # STT 13883: «47руга» / «7руга» / «7тига» — Chery Tiggo N (цифра + обломок «tiggo» в ASR).
    t = re.sub(
        r"\b4?([4789])(?:руг\w*|rгу\w*|рог\w*|тиг\w*|tig\w*|tiga)\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 13966: «ЧrG 4rгу» — Chery Tiggo N (латиница chrg + обломок rгу).
    t = re.sub(
        r"\b(?:ch|ч)r?g\s*([4789])\s*r?\s*(?:гу|rgu\w*|rug\w*)\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bтига\s*([0-9])\b", r"тигго \1", t)
    t = re.sub(r"\btiga\s*([0-9])\b", r"tiggo \1", t, flags=re.IGNORECASE)
    # 17574: «чери Тиг а-а» — обрезанное Tiggo после Chery.
    t = re.sub(r"\b(?:чери|chery)\s+тиг\b", " чери тигго ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:чери|chery)\s+tig\b", " chery tiggo ", t, flags=re.IGNORECASE)
    # STT 27787: «ере Тг 8» — обрезанный Chery + Tg (Tiggo) + модель.
    t = re.sub(
        r"\b(?:чере|ере)\s*тг\s*([4789])\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 27884: «Chertig 7L» — склейка Chery + Tiggo + 7L.
    t = re.sub(
        r"\bchert(?:ig|g|tg)\s*7\s*(?:l|л|эль|ель)\b",
        " chery tiggo 7 l ",
        t,
        flags=re.IGNORECASE,
    )
    # 19105: «ТЧере ИГ4 прога» / «Чере ИГ4 прога» ≈ Chery Tiggo 4 Pro.
    t = re.sub(
        r"\bт?чере\s+и?г\s*4\s+прога\b",
        " chery tiggo 4 pro ",
        t,
        flags=re.IGNORECASE,
    )
    # Короткое «Чере» — Chery; граница слова не затрагивает предлог «через».
    t = re.sub(r"\bчере\b", " chery ", t, flags=re.IGNORECASE)
    # STT-форма «4 прога» = «4 Pro».
    t = re.sub(r"\b4\s*прога\b", " chery tiggo 4 pro ", t, flags=re.IGNORECASE)
    # STT/ASR: «tig 4pro» / «tig 4 pro» / «тиг 4 про» -> Tiggo 4 Pro
    t = re.sub(r"\btig\s*([0-9])\s*pro\b", r"tiggo \1 pro", t, flags=re.IGNORECASE)
    t = re.sub(r"\btig([0-9])pro\b", r"tiggo \1 pro", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтиг\s*([0-9])\s*про\b", r"тигго \1 про", t)
    t = re.sub(r"\bтиг([0-9])про\b", r"тигго \1 про", t)
    # Продуктовая договорённость: 4Pr = 4 Pro = Chery (Tiggo 4 Pro).
    # Сначала явные «Chery/Чери + 4…», затем голое «4Pr» (без двойного chery после лат. chery).
    t = re.sub(
        r"\b(?:chery|чери)\s*4(?:\s*)?(?:pr|pro|[pр]ро|про)\b",
        " chery tiggo 4 pro ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"(?<!chery )(?<!чери )(?<!tiggo )(?<!тигго )\b4(?:\s*)?(?:pr|pro|[pр]ро|про)\b",
        " chery4protiggo ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bchery4protiggo\b", " chery tiggo 4 pro ", t, flags=re.IGNORECASE)
    # STT: «чер/черп/чере + 4/7/8/9 + про» -> Chery <n> Pro.
    t = re.sub(r"\bчер(?:п|е)?\s*([4789])\s*про\b", r"chery \1 pro", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчер(?:п|е)?([4789])про\b", r"chery \1 pro", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчер(?:п|е)?\s*([4789])\s*pro\b", r"chery \1 pro", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчер(?:п|е)?([4789])pro\b", r"chery \1 pro", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтига\b", "тигго", t)
    t = re.sub(r"\btiga\b", "tiggo", t, flags=re.IGNORECASE)
    # Склейка «tiggo7pro» / «тигго7про» после замены tiva/тига.
    t = re.sub(r"\btiggo\s*([4789])pro\b", r"tiggo \1 pro", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтигго\s*([4789])про\b", r"тигго \1 про", t, flags=re.IGNORECASE)
    # STT: голое «7 про / 7про / 7 pro / 7pro» без приставки Chery/Tiggo → Tiggo 7 Pro (не дублировать «тигgo 7 про»).
    t = re.sub(
        r"(?<!тигго )(?<!tiggo )(?<!chery )(?<!чери )\b7\s*про\b",
        " чери тигго 7 про ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"(?<!тигго )(?<!tiggo )(?<!chery )(?<!чери )\b7про\b",
        " чери тигго 7 про ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"(?<!тигго )(?<!tiggo )(?<!chery )(?<!чери )\b7\s*pro\b",
        " chery tiggo 7 pro ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"(?<!тигго )(?<!tiggo )(?<!chery )(?<!чери )\b7pro\b",
        " chery tiggo 7 pro ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 9752: «Че4 про» — цифра вместо «ри» в Chery Tiggo 4 Pro.
    t = re.sub(r"\bче\s*4\s*про\b", " chery tiggo 4 pro ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bче4\s*про\b", " chery tiggo 4 pro ", t, flags=re.IGNORECASE)
    # STT 10438: «че 4 Nюg N» / «че 4 new» — Chery Tiggo 4 New (обрезание «Чери» + хвост new/nюg).
    t = re.sub(
        r"(?<!chery )(?<!чери )(?<!tiggo )(?<!тигго )\bче\s*([4789])\s*"
        r"(?:n[юyu]w?g?\s*n\w*|new|nw|нью|нюг|ню)\b",
        r" chery tiggo \1 pro ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 13400: «ЧеrG 4рога» ≈ Chery Tiggo 4 Pro (4+pro go в ASR).
    t = re.sub(
        r"\bч[eе][a-zа-я]{0,4}\s*([4789])\s*рога\b",
        r" chery tiggo \1 pro ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 13631: «ЧереГ4ро» — Chery Tiggo 4 Pro (склейка «Чер» + G4 + ро).
    t = re.sub(r"\bчере?г4ро\b", " chery tiggo 4 pro ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчере?г\s*4\s*ро\b", " chery tiggo 4 pro ", t, flags=re.IGNORECASE)
    # STT: «че 4» / «че 7» / «че4» без «ри» — Chery Tiggo N.
    # Не преобразовывать разговорное «а чё, 9:30 есть?» в Chery Tiggo 9 (18668).
    t = re.sub(
        r"(?<!chery )(?<!чери )(?<!tiggo )(?<!тигго )\bче\s*([4789])\b"
        r"(?!\s*:\s*\d{1,2}\b)",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"(?<!chery )(?<!чери )(?<!tiggo )(?<!тигго )\bче([4789])\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 9783: «Чеrtk 9» / «Чертк 9» ≈ Chery Tiggo 9; «планово Чи9» — ответ на «какой автомобиль».
    t = re.sub(r"\bче\s*rtk\s*9\b", " chery tiggo 9 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчертк\s*9\b", " chery tiggo 9 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bпланов\w*\s+чи\s*9\b", " плановое chery tiggo 9 ", t, flags=re.IGNORECASE)
    # STT 18191: «CherritiX 9» ≈ Chery Tiggo 9 (достаточно префикса Cher/Cherr…).
    t = re.sub(
        r"\bcherr[a-zа-яё]*\s*([4789])\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bcherritix\b", " chery tiggo ", t, flags=re.IGNORECASE)
    # STT 15486: «Чили Te9» — Chery Tiggo 9.
    t = re.sub(r"\bчили\s*te9\b", " chery tiggo 9 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчили\s*te\s+9\b", " chery tiggo 9 ", t, flags=re.IGNORECASE)
    # STT 27368: «Чли Пиg 9» / «Чли Пиг 9» — Chery Tiggo 9.
    t = re.sub(
        r"\bчл[иi]\s+п[иi][gг]\s*([4789])\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bчл[иi]\s*п[иi][gг]9\b", " chery tiggo 9 ", t, flags=re.IGNORECASE)
    # STT 10282: «Tenet Tenet T7» — двойное ASR-маркетинговое название.
    t = re.sub(
        r"\b(tenet|тенет|тэнет)\s+\1\s+t\s*([4789])\b",
        r" \1 t\2 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 10195 / 10930: «Чər 9», «Чer 9», «Чertik 9» — расширение общего «Че+N» (лат. хвост rtik…).
    t = re.sub(
        r"\bч[eеəɛ][rр](?:[a-z]{1,8}\s+|\s*)([4789])\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bchery\s+([4789])\b(?!\s*(?:pro|пр|про|tiggo|тигго))",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 10013: «Чr Tiг 8 Pro» — латиница/кириллица Chery Tiggo 8 Pro.
    t = re.sub(
        r"\bч[rр]\s*t[иi][gг]\s*8\s*(?:pr|pro|пр|про)\b",
        " chery tiggo 8 pro ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bч[rр]t[иi][gг]\s*8\s*(?:pr|pro|пр|про)\b",
        " chery tiggo 8 pro ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «8Pro»/«8Pро»/«8pRo» — приводим к единой форме 8 pro.
    t = re.sub(r"\b8p[рr][oо]\b", " 8 pro ", t, flags=re.IGNORECASE)
    # Модель без явного бренда: «8 pro» в сервисном контексте = Tiggo 8 Pro.
    t = re.sub(r"\b8\s*pro\b", " chery tiggo 8 pro ", t, flags=re.IGNORECASE)
    # STT 10675: «на-8 ТМакс» / «8 ТМакс» — Chery Tiggo 8 Pro Max (8 + Макс в STT).
    t = re.sub(
        r"\bто\s+на\s*[-]?\s*8\s*тмакс\w*\b",
        " то chery tiggo 8 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:на\s*[-]?\s*)?8\s*тмакс\w*\b",
        " chery tiggo 8 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 16127: «CheriTga 7PrMax» ≈ Chery Tiggo 7 Pro Max (Cheri без y, Tga=Tiggo).
    t = re.sub(
        r"\bcheritga\s*7\s*(?:pr(?:o)?max|promax)\b",
        " chery tiggo 7 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bcheritga7(?:pr(?:o)?max|promax)\b",
        " chery tiggo 7 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 16720: «CheriTg 4Prгу» ≈ Chery Tiggo 4 Pro (Cheri без y, Tg=Tiggo, Prгу=Pro).
    t = re.sub(
        r"\bcheritg\s*([4789])\s*(?:pr\w*|пр\w*|proga|прога)\b",
        r" chery tiggo \1 pro ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bcheritg([4789])(?:pr\w*|пр\w*|proga|прога)\b",
        r" chery tiggo \1 pro ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 16199: «Chr Tg 7PrMaрк» ≈ Chery Tiggo 7 Pro Max (STT «PrMaрк» ≈ Pro Max).
    t = re.sub(
        r"\bchr\s+t[gг]\s*7\s*(?:pr(?:o)?)?\s*(?:max|макс|ma[рr][кk])\b",
        " chery tiggo 7 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 23311: «Chr Te7PMак» / mixed-script -> Chery Tiggo 7 Pro Max.
    t = re.sub(
        r"\bchr\s*te\s*7\s*p(?:r|р)?\s*[mм]\s*[aа]\s*[kк]\w*\b",
        " chery tiggo 7 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 8926: «ChrTeg 7Pr» / «ChrTg 7 Pro» / «ChrTiga7Pro» ≈ Chery Tiggo 7 Pro.
    t = re.sub(
        r"\bchr(?:teg|tg|tiga)\s*7\s*(?:pr|pro|пр|про)\b",
        " chery tiggo 7 pro ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bchr(?:teg|tg|tiga)7(?:pr|pro)\b",
        " chery tiggo 7 pro ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bchr(?:teg|tg|tiga)\s*7\s*промакс\b",
        " chery tiggo 7 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    # Смешанная кириллица/латиница в модели Chery Tiggo 7L:
    # «ЧеrTeg7Л», «cherteg7l», «teg7л» и похожие формы.
    t = re.sub(r"\bчеrteg\s*7л\b", " чери тигго 7 л ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bcherteg\s*7l\b", " chery tiggo 7 l ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bteg\s*7л\b", " tiggo 7 l ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bteg\s*7l\b", " tiggo 7 l ", t, flags=re.IGNORECASE)
    # STT без 'e' посередине: «ЧеrTg7L», «ЧеrТг7Л», «черТг7Л» (смешанная кир/латиница: «р/r», «т/t», «g/г»).
    t = re.sub(
        r"\bче(?:[рr][тt]?[gг]|[тt][gг]|[gг])\s*7\s*[lл]\b",
        " чери тигго 7 л ",
        t,
        flags=re.IGNORECASE,
    )
    # Латиница: «cherTg7L», «chertg7l», «cher tg 7 l» (с 'r' и опц. 't').
    t = re.sub(
        r"\bch[ae]?r(?:[тt]?[gг])?\s*7\s*[lл]\b",
        " chery tiggo 7 l ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 10185: обломки лат/кир, [C|Ч|T|Т] + буквы + 4|7|8|9 (+ L) → Chery Tiggo (не голый «т7» = Tenet).
    _chery_stt_mixed_letters = r"[a-zа-яёeеəɛ]"
    t = re.sub(
        rf"\b[cсч]{_chery_stt_mixed_letters}{{2,14}}?[4789](?:\s*[lл])?\b",
        _chery_stt_mixed_script_model_repl,
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        rf"\b[cсч]{_chery_stt_mixed_letters}{{2,14}}?[4789][lл]\b",
        _chery_stt_mixed_script_model_repl,
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        rf"\b[tт](?:[gг]|[eе]{{2}}|[iи][gг]){_chery_stt_mixed_letters}{{0,10}}?[4789](?:\s*[lл])?\b",
        _chery_stt_mixed_script_model_repl,
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        rf"\b[tт](?:[gг]|[eе]{{2}}|[iи][gг]){_chery_stt_mixed_letters}{{0,10}}?[4789][lл]\b",
        _chery_stt_mixed_script_model_repl,
        t,
        flags=re.IGNORECASE,
    )
    # STT: «Чеrg7», «чerg 7», «черg7», «чег7» — «че/cher» + (опц.) «r/р» + «g/г» + 7 (Chery Tiggo 7), тикет 7766.
    # В «cher» буква e часто латинская (0x65), «ч» — кириллица.
    t = re.sub(r"\bч(?:е|e)(?:[рr][gг]|[gг])\s*7pro\b", " chery tiggo 7 pro ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bч(?:е|e)(?:[рr][gг]|[gг])\s*7про\b", " чери тигго 7 про ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bч(?:е|e)(?:[рr][gг]|[gг])\s*7\s*pro\b", " chery tiggo 7 pro ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bч(?:е|e)(?:[рr][gг]|[gг])\s*7\s*про\b", " чери тигго 7 про ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bч(?:е|e)(?:[рr][gг]|[gг])\s*7\b", " chery tiggo 7 ", t, flags=re.IGNORECASE)
    # 17560: «ПТО Чег 4 Ню» / «Чег 4» — STT Chery Tiggo; голое «Чег» = Chery Tiggo.
    t = re.sub(
        r"\bчег\s*([4789])\s*(?:ню|new|nw|н)\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bчег\s*([4789])\b", r" chery tiggo \1 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчег\b", " chery tiggo ", t, flags=re.IGNORECASE)
    # STT: «Чери г4» / «чери г 4» — обрыв «Тигго» (голосовой бот).
    t = re.sub(
        r"\b(?:chery|чери|черри)\s+г\s*([4789])\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:chery|чери|черри)\s+g\s*([4789])\b",
        r" chery tiggo \1 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT/смешанная кир/лат: «Чеr 4», «чер 4», «Cher 4», «cher4» -> «chery N»
    # (только для номерных моделей Chery 4/7/8/9, чтобы не задевать «черный/черт/чери ...»).
    t = re.sub(r"\bче[rр]\s*([4789])\b", r" chery \1 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bch[aeəɛ]?r\s*([4789])\b", r" chery \1 ", t, flags=re.IGNORECASE)
    # STT 13620: «Черецг 4N» / «Чер TG 4N» — Chery Tiggo 4 New; «Чер»/«Чеr» + модель.
    t = re.sub(r"\bчерецг\s*4\s*n\b", " chery tiggo 4 ", t, flags=re.IGNORECASE)
    t = re.sub(
        r"\bчер[еёe]?(?:цг|[cс]г|[tт][gг]|тиг)\s*4\s*n\b",
        " chery tiggo 4 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «Чер 7L» / «Чер 7Л» — Chery Tiggo 7 L (до голого «чер N»).
    t = re.sub(r"\bчер\s*([4789])\s*[lл]\b", r" chery tiggo \1 l ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчер\s*([4789])\b", r" chery \1 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bч[еёe]r\s*([4789])\b", r" chery \1 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b4\s*га\b", " chery tiggo 4 ", t, flags=re.IGNORECASE)
    # STT 9308: «Чре 4» ≈ Chery Tiggo 4 (кириллическое обрезание «Чери»).
    # STT 13780: «Чре 7L» — Chery Tiggo 7 L (CRM-заявка; суффикс L до голого «чре N»).
    t = re.sub(r"\bчре\s*([4789])\s*[lл]\b", r" chery tiggo \1 l ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчре\s*([4789])\b", r" chery tiggo \1 ", t, flags=re.IGNORECASE)
    t = re.sub(
        r"\bчре\s*([4789])\s*(?:про|pro|nw|new|про)\b",
        r" chery tiggo \1 pro ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: голое «Чре» = «Чери» (обрезание ASR; после «чре N» / «чре N L» / «чре N про»).
    t = re.sub(r"\bчре\b", " чери ", t, flags=re.IGNORECASE)
    # STT: голое «Чер» = «Чери» (обрезание ASR; после «чер N» / «чер N L»; не «черный»; «через» — отдельно выше).
    t = re.sub(r"\bчер\b", " чери ", t, flags=re.IGNORECASE)
    # STT 27762: «чере7макс» / «чере 7 макс» = Chery Tiggo 7 Pro Max.
    t = re.sub(
        r"\bчере\s*([4789])\s*(?:макс|max)\b",
        r" chery tiggo \1 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bчере([4789])(?:макс|max)?\b",
        r" chery tiggo \1 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    # Короткие формы «чер + 4/7/8/9» и «чер + макс» в авто-контексте трактуем как Chery.
    t = re.sub(
        r"\bчер\s*([4789])\s*(?:макс|max)?\b",
        r" chery tiggo \1 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bчер\s*(?:макс|max)\b", " chery ", t, flags=re.IGNORECASE)
    # STT 12550 / 10258: «ЧreTIG 4 пробы», «ЧеретиГ 4 пробы» — Chery Tiggo 4 Pro (лат+кир; «пробы»=Pro).
    t = re.sub(
        r"\b[cсч](?:hery|hr|hre|re|ре|ере|er)?(?:t[еe]?g|t[иi]g|tg|тиг)\s*([4789])\s*(?:пробы|прога|proga)\b",
        r" chery tiggo \1 pro ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «Tenet Т4» / «tenet т4» (латиница + кириллическая «т»).
    t = re.sub(r"\btenet\s+т\s*([4789])\b", r" тенет t\1 ", t, flags=re.IGNORECASE)
    # STT 14320: «Tenet T4Pr» / «Тенет Т4Pr» — Tenet T4 Pro.
    t = re.sub(
        r"\b(?:tenet|тенет|тэнет|тент)\s+[тt]\s*4\s*pr\b",
        " тенет t4 pro ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:tenet|тенет|тэнет|тент)\s+[тt]4pr\b",
        " тенет t4 pro ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 12611 / CRM: «ТН34» / «TN34» — Tenet T4 (ТN=Tenet, 34=T4).
    t = re.sub(r"\b[тt][нn]34\b", " тенет t4 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b[тt][нn]\s*3\s*4\b", " тенет t4 ", t, flags=re.IGNORECASE)
    # STT 14140 / CRM: «Т04» / «T04» / «t04» — Tenet T4 (0 вместо буквы O или лат. T).
    t = re.sub(r"\b[тt]0([4789])\b", r" тенет t\1 ", t, flags=re.IGNORECASE)
    # STT 13828 / CRM: «ТМ4» / «TM4» / «ТМ 4» / «TM 4» / «TN 4» — Tenet T4 (ТM/TN=Tenet).
    t = re.sub(r"\b[тt][мm]4\b", " тенет t4 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b[тt][мm]\s*4\b", " тенет t4 ", t, flags=re.IGNORECASE)
    # 19749: смешанная кириллица/латиница «ТN8» / «TN8» = Tenet T8.
    t = re.sub(
        r"\b[тt][нn]\s*([4789])\b",
        r" тенет t\1 ",
        t,
        flags=re.IGNORECASE,
    )
    # Самостоятельные «tg 7l» / «тг 7л» — Tiggo 7L (без префикса Chery в этом куске STT).
    t = re.sub(r"\b[тt][gг]\s*7\s*[lл]\b", " тигго 7 л ", t, flags=re.IGNORECASE)
    # STT-склейки Tenet T4/T7/T8/T9: «тт7», «т т-7», «t-т7», «т т8» и т.п.
    t = re.sub(
        r"\b[тt]{2}\s*([4789])\b",
        lambda m: f" тенет t{m.group(1)} ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b[тt][-\s]+[тt][-\s]*([4789])\b",
        lambda m: f" тенет t{m.group(1)} ",
        t,
        flags=re.IGNORECASE,
    )
    # Варианты с числительным: «т т семь», «т-т семь» -> Tenet T7.
    t = re.sub(
        r"\b[тt][-\s]+[тt][-\s]*семь\b",
        " тенет t7 ",
        t,
        flags=re.IGNORECASE,
    )
    # Латиница: «t t seven», «t-t seven» -> Tenet T7.
    t = re.sub(
        r"\b[тt][-\s]+[тt][-\s]*seven\b",
        " тенет t7 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT-склейки Tenet T4/T7/T8: «тэнт4», «тент7», «тэн8» и т.п.
    t = re.sub(
        r"\bт(?:э|е)?нт?\s*([478])\b",
        lambda m: f" тенет t{m.group(1)} ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «Тенот» / «Танет» вместо Tenet/Тенет (ответ на «какой автомобиль?», 8358).
    t = re.sub(r"\bтенот\b", " тенет ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bтанет\b", " тенет ", t, flags=re.IGNORECASE)
    # STT 15502/20889: «тэнет» (э) вместо «тенет»; «ТНNT-4/8» / «tnnt-4/8» — Tenet T4/T8.
    t = re.sub(r"\bтэнет\b", " тенет ", t, flags=re.IGNORECASE)
    t = re.sub(
        r"\b[тt][нn]{2,}[тt][-\s]*([4789])\b",
        lambda m: f" тенет t{m.group(1)} ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 15570: «ТЦНТ 4Л» / «ЦНТ 4Л» — Tenet T4L.
    t = re.sub(r"\bтцнт\s*4\s*[lл]\b", " тенет t4 l ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bцнт\s*4\s*[lл]\b", " тенет t4 l ", t, flags=re.IGNORECASE)
    # STT: «или мотор Чери сдох» при Infinit/i — «или моторчик» (8776).
    if re.search(r"\binfinit", t, flags=re.IGNORECASE):
        t = re.sub(r"\bили\s+мотор\s+чери\b", " или моторчик ", t, flags=re.IGNORECASE)
    # STT 13985: «Tenet, сеем семёрка» / «Tenet семерка» — Tenet T7.
    t = re.sub(
        r"\b(?:tenet|тенет|тэнет)(?:\s*,?\s*(?:е|t))?\s*,?\s*"
        r"(?:tenet|тенет|тэнет)\s*,?\s*(?:сеем\s+)?(?:семерк\w*|семер\w*)\b",
        " тенет t7 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:tenet|тенет|тэнет)\s+(?:сеем\s+)?(?:семерк\w*|семер\w*)\b",
        " тенет t7 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «7емь» / «7ёмь» / «т7емь» — Tenet T7 (краткий ответ на «какой автомобиль?», 8852).
    # Не «7емьсот» (сумма) — отдельное слово по границе \b.
    t = re.sub(r"\b[тt]?\s*7[её]мь\b", " тенет t7 ", t, flags=re.IGNORECASE)
    # STT 22832/27241: «восьмерка гибрид» / «гибрид восьмерку» — Tiggo 8 Hybrid.
    t = re.sub(
        r"\bвосьм[её]рк\w*\s+гибрид\w*\b",
        " chery tiggo 8 hybrid ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bгибрид\w*\s+восьм[её]рк\w*\b",
        " chery tiggo 8 hybrid ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 22833: «чирри восьмерка» / «чирри 8» -> Chery Tiggo 8.
    t = re.sub(
        r"\bчир+ри\s+(?:восьм[её]рк\w*|8)\b",
        " chery tiggo 8 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 28872: «Chirri Г8 ПроMакс» -> Chery Tiggo 8 Pro Max (mixed-script tokenization).
    t = re.sub(
        r"\bchir+ri\s+[гg]\s*8\s*про\s*[mм]\s*акс\b",
        " chery tiggo 8 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 22841: «Чечерег 8еь» / «Четыре г восемь» -> Chery Tiggo 8.
    t = re.sub(
        r"\bчечер[еа]г\s*8\w*\b",
        " chery tiggo 8 ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bчетыр[её]\s+г\s+восем\w*\b",
        " chery tiggo 8 ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 10071: «Черема» ≈ Chery Tiggo Pro Max (цифра в STT не озвучена → 7 Pro Max).
    t = re.sub(r"\bчерема\b", " chery tiggo 7 pro max ", t, flags=re.IGNORECASE)
    # 28386: «CеlTg» / «CelTg» отдельно → chery tiggo; «7Бмаaк» отдельно → 7 pro max.
    t = re.sub(r"\bc[еe]l\s*t[gг]\b", " chery tiggo ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b7\s*бм[аa]+к\b", " 7 pro max ", t, flags=re.IGNORECASE)
    # STT 14511: «че Сероммакс» / «Сероммакс» — Chery Tiggo 7 Pro Max.
    t = re.sub(r"\bче\s*сероммакс\b", " chery tiggo 7 pro max ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bсероммакс\b", " chery tiggo 7 pro max ", t, flags=re.IGNORECASE)
    # STT 27654: «чре серомак» / «серомак» (одна «к») — Chery Tiggo 7 Pro Max.
    t = re.sub(r"\bчре\s*серомак\b", " chery tiggo 7 pro max ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bсеромак\b", " chery tiggo 7 pro max ", t, flags=re.IGNORECASE)
    # STT 25950: «Чирокс? ильтромакс» ≈ Chery Tiggo 7 Pro Max.
    t = re.sub(
        r"\bчирокс\b[?.!,;:\s]*ильтромакс\b",
        " chery tiggo 7 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bильтромакс\b", " chery tiggo 7 pro max ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчирокс\b", " chery ", t, flags=re.IGNORECASE)
    # STT 9317: «Черега» / «Черетига …» — см. ранние правила до catch-all «чер…».
    t = re.sub(r"\bс\s*([4789])\s*промаакс\b", r" чери тигго \1 промакс ", t, flags=re.IGNORECASE)
    # STT: «Черетика семь» / «Чери тик семьромакс» → Tiggo 7 / 7 Pro Max (8439).
    t = re.sub(r"\bчеретик\w*\s+семь\b", " чери тигго 7 ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчери\s+тик\s+семь\s*ромакс\b", " чери тигго 7 промакс ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчери\s+тик\s+семьромакс\b", " чери тигго 7 промакс ", t, flags=re.IGNORECASE)
    # 21405: «8 Про Ммаакс» (двойная «м») — Chery Tiggo 8 Pro Max.
    t = re.sub(
        r"\b([4789])\s*(?:про|pro|пр)\s*м{2,}а+к+с+\b",
        r" chery tiggo \1 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «сем макс» / «семь макс» — Chery Tiggo 7 Pro Max (цифра не озвучена явно).
    t = re.sub(r"\bсем(?:ь)?\s*макс\w*\b", " chery tiggo 7 pro max ", t, flags=re.IGNORECASE)
    # STT: «7 макс» / «8 макс» — Chery Tiggo N Pro Max.
    t = re.sub(
        r"(?<!chery )(?<!чери )(?<!tiggo )(?<!тигго )\b([4789])\s*макс\w*\b",
        r" chery tiggo \1 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 14057: «инфромакс» ≈ Pro Max; «чиг 7ромокс» / «7ромокс» — Tiggo 7 Pro Max.
    t = re.sub(r"\bинфромакс\b", " промакс ", t, flags=re.IGNORECASE)
    t = re.sub(
        r"\bчиг\s*([4789])\s*ромокс\b",
        r" chery tiggo \1 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b([4789])ромокс\b",
        r" chery tiggo \1 pro max ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: «ромакс/ромокс» без номера модели = Pro Max; в контексте ТО
    # этого достаточно для определения модельного ряда Chery.
    t = re.sub(r"\bр[оа]м[ао]кс\b", " промакс ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bпро\s*макс\b", " промакс ", t)
    t = re.sub(r"\bpro\s*max\b", " промакс ", t, flags=re.IGNORECASE)
    # 19284: «скинуть … на макс/промакс» = мессенджер MAX, не Chery Pro Max.
    # Срезаем только в контексте отправки/пересылки.
    t = re.sub(
        r"\b(?:скинуть|отправить|переслать|направить|писать|написать)\b[^.!?]{0,35}\b(?:на|в|по|через)\s+макс\b",
        " ",
        t,
        flags=re.IGNORECASE,
    )
    # STT: голое «макс» / «МАКС» — Chery Pro Max (не «про макс» и не «максимально»).
    t = re.sub(r"(?<!про )(?<!pro )\bмакс\b", " промакс ", t, flags=re.IGNORECASE)
    # Мессенджер после склейки «про макс» / уже сказанного STT «промакс».
    # Срезаем только в явном контексте отправки/пересылки в MAX, чтобы
    # «ТО ... на промакс» не теряло модель автомобиля (romaks/max кейсы).
    t = re.sub(
        r"\b(?:скинуть|отправить|переслать|направить|писать|написать)\b[^.!?]{0,35}\b(?:на|в|по|через)\s+промакс\b",
        " ",
        t,
        flags=re.IGNORECASE,
    )
    # STT/латиница: Chritent / Charritent / CheryTenet -> Chery Tenet.
    t = re.sub(r"\bchri+tent\b", " chery tenet ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bcharri+tent\b", " chery tenet ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bchery\s*tenet\b", " chery tenet ", t, flags=re.IGNORECASE)
    # STT перед «Tenet»: «Пет», «Pet», «т.е.» (обрубок) — убрать перед маркой.
    t = re.sub(r"\b(?:пет|pet|т\.?\s*е\.?)\s+tenet\b", " tenet ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(?:пет|pet|т\.?\s*е\.?)\s+тенет\b", " тенет ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчертиг\s*4\s*про\b", " chery tiggo 4 pro ", t, flags=re.IGNORECASE)
    # Бренд дилера: «Чери Викинги Татарстан» -> «Чери Викинги на Заставной».
    t = re.sub(r"\bчар+ри\s+викинг[аи]?\s+татарстан\b", " чери викинги на заставной ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bчери\s+викинг[аи]?\s+татарстан\b", " чери викинги на заставной ", t, flags=re.IGNORECASE)
    # STT 8115: «Черевики» / «Дая» — как канон приветствия СТО (узкий/широкий слой видят тот же текст).
    t = re.sub(
        r"\bздравствуйте,?\s+черевики\.?\s+дая\b",
        # См. text_normalization._normalize_chereviki_daya_stt: точка перед «стажер» обходит strip канона.
        " здравствуйте чери викинги на заставной. стажер дарья ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\bчеревики\b", " чери викинги на заставной ", t, flags=re.IGNORECASE)
    t = re.sub(r"\bдая\b", " стажер дарья ", t, flags=re.IGNORECASE)
    # STT: «Чребикинкий» / «Черебикинки»; в начале «диспетчер ри» → Дарья (см. text_normalization).
    t = re.sub(
        r"\bчр[еэ]б(?:икинк+|егин|гин)\w*\b",
        " чери викинги на заставной ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bвикинговый\s*нзутант\w*\b",
        " чери викинги на заставной ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bчер\s*викингинзстан\w*\b",
        " чери викинги на заставной ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\bчревикинг\w*стан\w*\b",
        " чери викинги на заставной ",
        t,
        flags=re.IGNORECASE,
    )
    head100, tail100 = t[:100], t[100:]
    head100 = re.sub(
        r"(?:(?:сервис[-\s]*)?консультант\s*,?\s*)?диспетчер\s+ри\b",
        " диспетчер дарья ",
        head100,
        flags=re.IGNORECASE,
    )
    head100 = re.sub(r",\s*ри\b(?=[\s,.!?;:]|$)", ", дарья", head100, flags=re.IGNORECASE)
    t = head100 + tail100
    # Модель Tenet T4/T7/T8: STT «тэнет-4», «тэнт-7/8», «т-7» и т.п. → «тенет tN».
    def _tenet_stt_model_sub(m: re.Match) -> str:
        digit = re.search(r"[4789]", m.group(0))
        return f" тенет t{digit.group(0)} " if digit else " тенет t7 "

    # STT 11394: «Те наТ8» / «те нат8» = Tenet T8.
    t = re.sub(
        r"\bте\s+нат\s*([4789])\b",
        lambda m: f" тенет t{m.group(1)} ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(
        r"\b(?:"
        r"т\s+на\s+т[-\s]*[4789]|"
        r"т\s+нат[-\s]*[4789]|"
        r"т(?:э|е)?н(?:е)?т[-\s]*[4789]|"
        r"тен[-\s]*[4789]|"
        r"т[-\s]*[4789]|"
        r"(?<!тенет\s)(?<!tenet\s)t[-\s]*[4789]|"
        r"(?<!тенет\s)(?<!tenet\s)t[4789]|"
        r"т[4789]"
        r")\b",
        _tenet_stt_model_sub,
        t,
    )
    # STT: «чери четыре года» — регламент «раз в N лет», не марка Chery (14158).
    t = re.sub(
        r"\bчери\s+(?:"
        r"один|два|три|четыре|пять|шесть|семь|восемь|девять|"
        r"1|2|3|4|5|6|7|8|9"
        r")\s+(?:год|года|лет)\b",
        " каждые n года ",
        t,
        flags=re.IGNORECASE,
    )
    # STT 14140: «раньше здесь был Nissan» — про локацию салона, не авто клиента.
    t = re.sub(
        r"\b(?:раньше\s+здесь|здесь\s+раньше)\s+был[аи]?\s+"
        r"(?:nissan|нissan|нисan|chery|чери|tenet|тенет|тэнет)\b",
        " раньше здесь был другой салон ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"[^0-9a-zа-я+\s-]", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _mask_spurious_to_for_service(low: str) -> str:
    """
    Убирает из текста конструкции, где «то» не про регламентное ТО (частицы, местоимения),
    чтобы эвристика bare_particle_to не ловила «то» в «то, что» / «что-то» и т.п.
    """
    s = low
    # Связка «то, что» / «то что» (в т.ч. после нормализации пунктуации).
    s = re.sub(r"(?:^|\s)то\s+что(?:\s|$)", "   ", s)
    # Местоимения и разговорные формы (в т.ч. после STT).
    for pat in (
        r"что-то",
        r"кое-что",
        r"где-то",
        r"(?:^|\s)что\s+то(?:\s|$)",
        r"(?:^|\s)кое\s+что(?:\s|$)",
        r"(?:^|\s)кое\s+то(?:\s|$)",
        r"(?:^|\s)где\s+то(?:\s|$)",
    ):
        s = re.sub(pat, "     ", s)
    # Указательное «не то», но не начало «не то что …».
    s = re.sub(r"(?:^|\s)не\s+то(?!\s+что)(?=\s|$)", "    ", s)
    # STT: «это-то», «часть-то», «чё-то» — частица, не регламентное ТО (9117).
    s = re.sub(r"\b\w+-то\b", "     ", s)
    s = re.sub(r"(?:^|\s)это\s+то(?:\s|$)", "     ", s)
    s = re.sub(r"(?:^|\s)то\s+это(?:\s|$)", "     ", s)
    # Связка «то есть» / «то есть,» — не регламентное ТО (9371, 13400).
    s = re.sub(r"(?:^|\s)то\s+есть(?:[,\s]|$)", "     ", s)
    # STT 16676: «то да, это» — частица, не регламентное ТО.
    s = re.sub(r"(?:^|\s)то\s+да(?:[,\s]|$)", "     ", s)
    # STT-склейка: «на какой срок … то есть» — не «срок ТО» (14807).
    s = re.sub(r"\bсрок\s+то\s+есть\b", "           ", s)
    # Частица «так-то» / «так то» — не регламентное ТО (12202).
    s = re.sub(r"\b(?:так-то|так\s+то)\b", "        ", s, flags=re.I)
    # Союз «то… то…» при переводах: «попадаю, то на диспетчера, то на кого-то» (12658).
    s = re.sub(
        r"\b(?:попада\w*[^.!?]{0,40})?то\s+на\s+(?:диспетчер\w*|кого(?:-то)?)\b",
        "            ",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"(?:^|\s)то\s+(?:или|и)\s+(?:ещё|еще)\s+на\b",
        "                ",
        s,
        flags=re.I,
    )
    # STT: «то чё/то че» — частица, не регламентное ТО (12619).
    s = re.sub(r"(?:^|\s)то\s+ч[её](?:\s|$)", "     ", s)
    # Диспетчер про очередь: «ТО проводят в основном» — не тема клиента (12619).
    s = re.sub(r"\bто\s+провод\w+\s+в\s+основн\w*\b", "                  ", s, flags=re.I)
    # STT: «после обеда то можно на …» — частица «то», не регламентное ТО (13647).
    s = re.sub(r"после обеда\s+то\s+можно", "                ", s)
    s = re.sub(r"(?:^|\s)то\s+можно\s+на\b", "         на", s)
    # STT: «а то нам …» — союз/частица, не регламентное ТО (14087).
    s = re.sub(r"(?:^|\s)а\s+то\s+", "     ", s)
    # Союз «то ли …, то ли …» — не регламентное ТО (21281).
    s = re.sub(r"(?:^|\s)то\s+ли(?:\s|$)", "     ", s, flags=re.I)
    # Частица «то ж/то же» — не регламентное ТО.
    s = re.sub(r"(?:^|\s)то\s+ж(?:\s|$)", "     ", s, flags=re.I)
    s = re.sub(r"(?:^|\s)то\s+же(?:\s|$)", "      ", s, flags=re.I)
    # «то туда, то сюда» / «то в гараже» — частицы, не регламентное ТО (16323).
    s = re.sub(
        r"\bто\s+(?:[tт])?уда\s*,?\s*то\s+(?:[sс])?юда\b",
        "        ",
        s,
        flags=re.I,
    )
    s = re.sub(r"(?:^|\s)то\s+в\s+гараж\w*", "          ", s, flags=re.I)
    # «одно и то же» / «то же самое» — частицы, не регламентное ТО (16295).
    s = re.sub(r"\bодно\s+и\s+то\s+же\b", "           ", s, flags=re.I)
    s = re.sub(r"\bто\s+же\s+сам(?:ое|o)\b", "           ", s, flags=re.I)
    # «если из четырех то 1 440» — союз «то», не регламентное ТО (15825).
    s = re.sub(
        r"\bиз\s+(?:одного|двух|трех|четырех|пяти)\s+то\b",
        "            ",
        s,
        flags=re.I,
    )
    # «подъехать/приехать то показать» — частица, не регламентное ТО (21844).
    s = re.sub(
        r"\b(?:подъех\w*|приех\w*|заех\w*)\s+то\s+(?:показ\w*|посмотр\w*|глян\w*|провер\w*)\b",
        "                      ",
        s,
        flags=re.I,
    )
    # «если …, то …» / «выходные, то …» — союз, не регламентное ТО (16320).
    s = re.sub(r"\b(?:если|когда)\s+[^,!?]{0,80},\s+то\s+", "              ", s, flags=re.I)
    # После общей нормализации запятая может исчезнуть:
    # «если записаться то на ближайшее [число/день]» — условный союз, не ТО (18588).
    s = re.sub(
        r"\bесли\s+(?:запис\w*|подъех\w*|приех\w*)[^.!?]{0,50}\bто\s+"
        r"(?=(?:на\s+)?(?:ближайш\w*|сегодня|завтра|послезавтра|"
        r"понедельник|вторник|сред[ау]|четверг|пятниц[ау]?|суббот[ау]?|"
        r"воскресень\w*|\d{1,2}))",
        " ",
        s,
        flags=re.I,
    )
    # Консультация по отдельной работе: «если да, то сколько стоит?»,
    # «если потребуется замена, то там уже ...» — условный союз, не ТО (18734).
    s = re.sub(
        r"\bесли\s+(?:да|потреб\w*\s+замен\w*|нужно\s+будет\s+менять)"
        r"\s+то\s+(?=(?:сколько|там|тогда|здесь|можно)\b)",
        " ",
        s,
        flags=re.I,
    )
    s = re.sub(r"\b(?:в\s+)?выходн\w*,\s+то\s+", "            ", s, flags=re.I)
    # 17265: «то дело 25 360» — номер дела МФЦ, не регламентное ТО.
    s = re.sub(r"(?:^|\s)то\s+дело(?:\s+\d[\d\s]*)?", "          ", s, flags=re.I)
    # STT: «то я …» — разговорная частица, не регламентное ТО.
    s = re.sub(r"(?:^|\s)то\s+я\s+", "     я ", s, flags=re.I)
    return s


def _is_inbound_online_technical_service_application_intake(low: str) -> bool:
    """12476: онлайн-заявка на техническое обслуживание — тема регламентного ТО, не чистая диагностика."""
    head = (low or "")[:1600]
    return bool(
        re.search(r"\b(?:оставлял|оставляла|оставил|оставила)\s+заявк", head, re.I)
        and re.search(r"\b(?:на\s+)?техническ\w+\b", head, re.I)
    )


def _is_prefilled_online_to_application_confirmation_not_narrow_to(low: str) -> bool:
    """
    Звонок только для проверки/подтверждения уже заполненной онлайн-заявки на ТО.

    Дата и время взяты из заявки; диспетчер лишь подтверждает свободный слот.
    Если в разговоре выбирают другой слот, это обычная запись по чек-листу.
    """
    head = (low or "")[:3200]
    if not head.strip():
        return False
    prior_application = bool(
        re.search(r"\bвы\s+заявк\w*\s+(?:нам\s+)?(?:ещ[её]\s+)?отправлял", head, re.I)
        or re.search(r"\bзаявк\w*\s+(?:нам\s+)?(?:ещ[её]\s+)?отправлял", head, re.I)
        or re.search(r"\bмы\s+заявк\w*\s+получил", head, re.I)
    )
    prefilled_slot = bool(
        (
            re.search(r"\bв\s+заявк\w*\s+указан", head, re.I)
            or (
                prior_application
                and re.search(r"\bжелаем\w*\s+дат\w*\s+указал", head, re.I)
            )
        )
        and re.search(r"\b(?:желаем\w*\s+дат|дат\w*)", head, re.I)
        and re.search(r"\bврем[яеи]\b", head, re.I)
    )
    confirmation = bool(
        re.search(r"\bданн\w*\s+врем\w*\s+.{0,30}\bсвобод", head, re.I)
        or re.search(r"\bмы\s+вас\s+записал", head, re.I)
        or re.search(r"\bзапись\s+(?:подтвержд|оформлен)", head, re.I)
        or re.search(r"\bзапис\w*[^.!?]{0,35}\bподтвержда", head, re.I)
    )
    if not (prior_application and prefilled_slot and confirmation):
        return False
    active_slot_selection = bool(
        re.search(
            r"\b(?:могу\s+предложить|какое\s+время\s+(?:вам\s+)?удобно|"
            r"выбирайте\s+время|давайте\s+на\s+\d|перенес[её]м\s+на)\b",
            head,
            re.I,
        )
    )
    return not active_slot_selection


def _is_deferred_to_scheduling_callback_without_booking(low: str) -> bool:
    """
    ТО обсуждается, но запись отложена до уточнения графика и повторного звонка.

    Нет согласованного слота и прохождения чек-листа записи (18830).
    """
    head = (low or "")[:3200]
    if not head.strip() or not contains_any_to_marker_hit(head)[0]:
        return False
    uncertain_schedule = bool(
        re.search(r"\bграфик\w*[^.!?]{0,45}\bне\s+зна", head, re.I)
        or re.search(r"\bне\s+зна\w*[^.!?]{0,45}\bграфик", head, re.I)
    )
    deferred_contact = bool(
        re.search(r"\b(?:перезвонит|перезвоню|позвоню|звоните\s+тогда)\b", head, re.I)
        and re.search(r"\b(?:договорим|как\s+узна|ближе\s+к|пятого|шестого)\b", head, re.I)
    )
    if not (uncertain_schedule and deferred_contact):
        return False
    agreed_slot = bool(
        re.search(r"\b(?:записал\w*|запишем|внес\w*\s+в\s+запис|будем\s+(?:ждать|ожидать))\b", head, re.I)
        or re.search(r"\b(?:на|в)\s*(?:[01]?\d|2[0-3])\s*[:.]\s*\d{2}\b", head, re.I)
    )
    checklist_or_quote = bool(
        re.search(
            r"\b(?:фамили\w*\s+собственник|госномер|пробег|стоимост|цен[аеуы]|рубл|"
            r"замен\w*\s+масл|маслян\w*\s+фильтр|салонн\w*\s+фильтр|свеч\w*)\b",
            head,
            re.I,
        )
    )
    return not agreed_slot and not checklist_or_quote


def _is_to_price_quote_deferred_by_client_without_slot(low: str, evidence: Dict[str, Any]) -> bool:
    """
    Клиент получил консультацию по ТО/цене и откладывает запись:
    «чуть позже перезвоню, по датам сориентируете», без подтверждённого слота.
    """
    if (evidence.get("work_intent") or "") != "to_price_quote":
        return False
    head = (low or "")[:3600]
    if not head.strip():
        return False
    has_defer_phrase = bool(
        re.search(r"\b(?:чуть\s+)?позже\b[^.!?]{0,40}\b(?:перезвон|позвон)\w*", head, re.I)
        or re.search(r"\b(?:перезвон|позвон)\w*\b[^.!?]{0,80}\b(?:по\s+дат\w*|по\s+времен\w*|сориентиру\w*)", head, re.I)
        or re.search(r"\bпо\s+дат\w*\b[^.!?]{0,80}\b(?:сориентиру\w*|перезвон\w*|позвон\w*)", head, re.I)
    )
    if not has_defer_phrase:
        return False
    has_booking_confirmation = _has_to_price_quote_booking_confirmation(head)
    return not has_booking_confirmation


def _has_to_price_quote_booking_confirmation(text: str) -> bool:
    """
    Подтверждение, что по звонку о стоимости ТО запись всё-таки согласована.

    Базовые формулировки: «записали», «запишем», «будем ожидать».
    Дополнительно поддерживаем живые варианты:
    - «запишете?» + в разговоре уже есть конкретика по слоту;
    - «вас будет ожидать» + конкретика по слоту.
    """
    low = (text or "")
    if not low.strip():
        return False
    strong_confirm = bool(
        re.search(
            r"\b(?:вас\s+)?записал[аи]\w*\b|"
            r"\b(?:давайте\s+запиш\w*|запишем|внес\w*\s+в\s+запис)\b|"
            r"\b(?:будем\s+ожидать|подъезжайте|накануне\s+(?:позвон\w*|напомн\w*))\b",
            low,
            re.I,
        )
    )
    if strong_confirm:
        return True

    has_slot_specifics = bool(
        re.search(
            r"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|сред[ау]|четверг|"
            r"пятниц\w*|суббот\w*|воскресень\w*)\b",
            low,
            re.I,
        )
        or re.search(r"\b(?:в|на|к)\s*(?:[01]?\d|2[0-3])\s*[:.]\s*\d{2}\b", low, re.I)
        or re.search(r"\b(?:в|к)\s+районе\s+(?:[01]?\d|2[0-3])\b", low, re.I)
    )
    if not has_slot_specifics:
        return False

    soft_confirm = bool(
        re.search(r"\bзапиш(?:ете|ите)\b", low, re.I)
        or re.search(r"\bвас\s+буд(?:ем|ет)\s+ожидать\b", low, re.I)
        or re.search(r"\bожида(?:ем|ть)\s+вас\b", low, re.I)
    )
    return soft_confirm


def _is_primary_defect_diagnostic_service_intake(low: str) -> bool:
    """
    Рамочное правило: визит из‑за неисправности/диагностики, а не новая запись на регламентное ТО.
    Маркеры ТО в прошедшем времени / CRM («нулевое ТО проходила», «вы ТО проходили») не дают СТО_ТО_*.
    Намерение регламентного ТО в том же звонке — приоритет у ТО (18298).
    Запись на гарантийный ремонт (15878) — не эта ветка (см. warranty).
    """
    if _is_inbound_warranty_repair_booking_intake(low):
        opening_warranty = (low or "")[:3200]
        # 21938: гарантийное обращение может быть именно диагностикой
        # (свист/ролики/двигатель + «подъехать посмотреть»), а не готовым ремонтом.
        warranty_mechanical_diag_override = bool(
            re.search(
                r"\b(?:свист\w*|шум\w*|скрежет\w*|ролик\w*|помп\w*|натяжн\w*)\b",
                opening_warranty,
                re.I,
            )
            and re.search(
                r"\b(?:двигател\w*|капот\w*|газовк\w*|оборот\w*)\b",
                opening_warranty,
                re.I,
            )
            and re.search(
                r"\b(?:провер\w*|посмотр\w*|диагност\w*|подъех\w*|приех\w*)\b",
                opening_warranty,
                re.I,
            )
        )
        if not warranty_mechanical_diag_override:
            return False
    # Явный запрос сход-развала должен идти как other_work/wheel_alignment.
    if _is_wheel_alignment_service_intake(low):
        return False
    if _regulatory_to_intent_takes_priority(low):
        return False
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    if _is_inbound_online_technical_service_application_intake(low):
        return False
    if strong_scheduled_to_signal_present(low):
        return False
    opening = (low or "")[:3200]
    has_past_to_context = _past_to_visit_phrase_present(opening)
    if _explicit_new_ordinal_to_booking_intent_present(opening[:1800]):
        return False
    if contains_any_to_marker_hit(low)[0]:
        # 21825: «в мае ТО проходил» + текущая жалоба/проверка — это диагностика, не новая запись на ТО.
        # Сохраняем блокировку только когда есть признаки нового регламентного ТО в этом же звонке.
        if has_past_to_context and not _has_new_regulatory_to_booking_or_price_intent(low):
            pass
        else:
            return False
    if _is_brake_pad_replacement_booking_intake(low):
        return False
    has_defect = any(
        p in opening
        for p in (
            "не работает",
            "нее работает",
            "перестал",
            "перестала",
            "перестали",
            "не горит",
            "не закрыва",
            "не открыва",
            "не включа",
            "сломал",
            "неисправн",
            "поломк",
            "стук",
            "стуч",
            "хруст",
            "шумит",
            "свист",
            "скрежет",
            "брязг",
            "бренч",
            "теч",
            "ошибк",
        )
    )
    if not has_defect:
        # Описательный симптом без слова «стук»: посторонний звук/отбой
        # у колеса или стойки на кочке/неровности (18588).
        has_defect = bool(
            re.search(r"\bзвук\w*\b", opening, re.I)
            and any(
                p in opening
                for p in (
                    "колес",
                    "стойк",
                    "кочк",
                    "неровност",
                    "лежач",
                    "отбива",
                )
            )
        )
    if not has_defect:
        # 21844: вода/конденсат в фонаре — дефект для осмотра/диагностики, не регламентное ТО.
        has_defect = bool(
            re.search(r"\b(?:вода|влаг\w*|конденсат\w*|протека\w*|подтек\w*)\b", opening, re.I)
            and re.search(r"\b(?:фонар\w*|фар\w*|багажник\w*|люк\w*|салон\w*)\b", opening, re.I)
        )
    if has_defect and _employee_availability_not_vehicle_defect(opening):
        has_defect = False
    diag_route = any(
        p in opening
        for p in (
            "диагност-электрик",
            "диагност электрик",
            "к диагност",
        )
    ) or bool(re.search(r"\bдиагност\w*\b", opening, re.I))
    # 22213: в скрипте ТО может звучать «диагност проверяет/подключает прибор» как часть
    # перечня работ, это не отдельная запись на диагностику.
    if (
        not has_defect
        and diag_route
        and re.search(r"\bто\b", opening, re.I)
        and re.search(
            r"\b(?:предыдуще\w+|втор\w+\s+уже\s+пройти|то\s+только\s+выполня\w*|"
            r"пройти\s+то|то\s+проход\w*)\b",
            opening,
            re.I,
        )
    ):
        return False
    if not has_defect and not diag_route:
        return False
    return any(
        p in opening
        for p in (
            "запис",
            "подъехать",
            "приехать",
            "запиш",
            "глянут",
            "посмотр",
            "провер",
        )
    ) or diag_route


def _sunroof_or_electrical_trim_defect_service_intake(low: str) -> bool:
    """Люк/панорама + дефект + осмотр — диагностика, не плановое ТО (13400)."""
    if _has_clear_regulatory_to_context_for_non_to_override(low):
        return False
    opening = (low or "")[:2000]
    has_unit = any(p in opening for p in ("люк", "люком", "панорам"))
    has_symptom = any(
        p in opening
        for p in (
            "перестал",
            "перестают",
            "не работает",
            "не закрыва",
            "не открыва",
            "кнопк",
            "промежуток",
        )
    )
    if not (has_unit and has_symptom):
        return False
    return any(
        p in opening for p in ("глянут", "посмотр", "провер", "диагност", "запис")
    )


def _chassis_steering_symptom_service_intake(low: str) -> bool:
    """Стук/увод/кривой руль — диагностика, не плановое ТО (12619, 16323)."""
    opening = (low or "")[:2000]
    # Явный запрос сход-развала классифицируем как wheel_alignment, не diagnostics.
    if _is_wheel_alignment_service_intake(low):
        return False
    if _has_clear_regulatory_to_context_for_non_to_override(low):
        early = opening[:900]
        # Если в начале звонка уже явно обсуждают увод/руль/сход-развал,
        # считаем это основной темой, даже при шумовом «то + пробег» дальше.
        if not any(p in early for p in ("развал", "схожден", "вхожден", "тянет", "увод", "криво", "руль")):
            return False
    has_symptom = (
        any(p in opening for p in ("стук", "скрежет", "хруст"))
        or ("шум" in opening and "звук" in opening)
        or ("криво" in opening and "руль" in opening)
        or ("тянет" in opening and "руль" in opening)
        or bool(
            re.search(
                r"\b(?:машин\w*|автомобил\w*|руль\w*)\s+[^.!?]{0,50}?\bтянет\b",
                opening,
                re.I,
            )
        )
        or bool(
            re.search(
                r"\bтянет\s+[^.!?]{0,40}?\b(?:влево|вправо|налево|направо|слево|справо)\b",
                opening,
                re.I,
            )
        )
        or any(p in opening for p in ("развал", "схожден", "вхожден"))
    )
    if not has_symptom:
        return False
    return any(
        p in opening
        for p in (
            "поворот",
            " руль",
            "руль ",
            "рулевой",
            "рулевую",
            "рулев",
            "колонк",
            "налево",
            "направо",
            "влево",
            "вправо",
            "выкручива",
            "криво",
            "спиц",
            "опасн",
        )
    )


def _is_wheel_alignment_service_intake(low: str) -> bool:
    """Развал-схождение / хождение машины (увод) — прочие работы, не регламентное ТО (16676)."""
    opening = (low or "")[:3200]
    # Если явный запрос «записаться на диагностику» прозвучал раньше,
    # а развал/схождение обсуждаются позже как возможная причина, не считаем это
    # самостоятельным запросом на wheel_alignment.
    diag_m = re.search(r"\bдиагностик\w*", opening, re.I)
    align_m = re.search(r"\b(?:развал|схожден|вхожден|сход[-\s]?развал)\w*", opening, re.I)
    if diag_m and _diagnostics_booking_intake_phrase_present(opening):
        if not align_m or diag_m.start() <= align_m.start():
            return False
    if _has_clear_regulatory_to_context_for_non_to_override(low):
        early = opening[:1000]
        # При явном «записаться на ТО» и т.п. оставляем ТО-контекст.
        # Но если в начале разговора тема сразу про развал/схождение,
        # не даём ложному «то 170 000» переопределить работу.
        if not any(p in early for p in ("развал", "схожден", "вхожден", "стенд развал")):
            return False
    return bool(
        any(
            p in opening
            for p in (
                "развал",
                "развал вхожд",
                "схожден",
                "вхожден",
                "стенд развал",
                "стенде развал",
            )
        )
        or bool(re.search(r"\bразвал\s+вхожд", opening, re.I))
        or bool(re.search(r"\bстенд\w*\s+развал", opening, re.I))
        or (
            # Симптомы сами по себе относим к diagnostics; для wheel_alignment
            # нужен явный запрос/назначение по сход-развалу.
            any(p in opening for p in ("тянет", "увод", "криво"))
            and any(p in opening for p in ("руль", "машин", "автомобил"))
            and any(
                p in opening
                for p in (
                    "развал",
                    "схожден",
                    "вхожден",
                    "сход-развал",
                    "сход развал",
                    "стенд развал",
                )
            )
        )
    )


def _has_clear_regulatory_to_context_for_non_to_override(low: str) -> bool:
    """
    Явный контекст регламентного ТО (без шумовых «то + число/пробег»),
    который должен блокировать auto-классификацию в non-ТО по симптомам.
    """
    opening = (low or "")[:2600]
    if not opening.strip():
        return False
    if _is_inbound_opening_regulatory_to_booking(low):
        return True
    if re.search(r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят|нулев)\w*\s+то\b", opening, re.I):
        return True
    if re.search(r"\bто[-\s]?\d+\b", opening, re.I):
        return True
    if re.search(r"\b(?:техническ\w+\s+обслуживан\w*|техобслуживан\w*)\b", opening, re.I):
        return True
    if re.search(r"\b(?:стоимост\w*|сколько\s+стоит|цен\w*)\b[^.!?]{0,40}\b(?:на\s+)?то\b", opening, re.I):
        return True
    return False


def _is_wheel_balancing_service_intake(low: str) -> bool:
    """Балансировка колёс — прочие работы, даже при посторонней внутренней речи о диагностике."""
    opening = (low or "")[:3200]
    # Если в начале клиент явно просит запись на диагностику, а «балансировка»
    # звучит позже как версия диспетчера, не считаем это отдельным запросом на балансировку.
    diag_m = re.search(r"\bдиагностик\w*", opening, re.I)
    bal_m = re.search(r"\bбалансир\w*", opening, re.I)
    if diag_m and _diagnostics_booking_intake_phrase_present(opening):
        if not bal_m or diag_m.start() <= bal_m.start():
            return False
    if not re.search(r"\bбалансир\w*", opening, re.I):
        return False
    return bool(
        re.search(r"\bкол[её]с\w*", opening, re.I)
        or any(p in opening for p in ("запис", "стоимост", "по деньгам", "радиус"))
    )


def _is_insurance_claim_body_inspection_intake(low: str) -> bool:
    """
    16320: страховой случай / направление / Европротокол — осмотр кузовных повреждений, не ТО.
    """
    head = (low or "")[:3200]
    insurance = any(
        p in head
        for p in (
            "страховой случ",
            "по страховому",
            "направлен",
            "направление",
            "европротокол",
            "извещени",
            "дтп",
            "сстрахование",
            "страхован",
        )
    )
    if not insurance:
        return False
    has_body_damage_topic = any(
        p in head
        for p in (
            "кузов",
            "вмятин",
            "царапин",
            "лкп",
            "поврежден",
            "бампер",
            "крыл",
            "двер",
            "капот",
            "зеркал",
            "молдинг",
            "стекл",
            "фар",
        )
    )
    if not has_body_damage_topic:
        return False
    if not any(p in head for p in ("осмотр", "оценк", "ремонт", "направлен", "направление")):
        return False
    if contains_any_to_marker_hit(low)[0] or strong_scheduled_to_signal_present(low):
        return False
    return True


def _is_body_shop_service_intake(low: str) -> bool:
    """
    Кузовной цех: линия/мастер кузовного, вмятины, ЛКП, покраска — не регламентное ТО.
    Ниже ТО и гарантии по приоритету вида работ (гарантия отсекается выше по цепочке).
    """
    low = low or ""
    if not low.strip():
        return False
    head = low[:3500]
    body_line = bool(
        re.search(r"\bкузовн\w*", head, re.I)
        or re.search(r"\bкузоно\w*", head, re.I)
        or re.search(r"\bвикинги\s+кузов", head, re.I)
        or "цех кузовного" in head
        or "кузовной ремонт" in head
    )
    body_work = any(
        p in head
        for p in (
            "вмятин",
            "царапин",
            "покраск",
            "лкп",
            "лакокрас",
            "без покраски",
            "полировк",
            "риктовк",
            "рихтовк",
            "бампер",
            "крыло",
            "капот",
        )
    ) and any(
        p in head
        for p in ("осмотр", "ремонт", "запис", "направлен", "мастер", "приёмщик", "приемщик")
    )
    # 18773: клиент ошибочно попал в кузовной, откуда его перевели диспетчеру сервиса.
    # Само название прежней линии не является темой работ.
    wrong_body_line_transfer = bool(
        re.search(
            r"\bвы\s+в\s+кузовн\w*\s+попал\w*[^.!?]{0,100}\b"
            r"(?:на\s+)?диспетчер\w*[^.!?]{0,60}\b(?:соедин|перевед)",
            head,
            re.I,
        )
    )
    if wrong_body_line_transfer and not body_work:
        return False
    if not body_line and not body_work and not _is_insurance_claim_body_inspection_intake(low):
        return False
    # 19059: «после ремонта цепи шум был под капотом» — слово «капот» не делает
    # явную запись на диагностику двигателя обращением в кузовной цех.
    explicit_mechanical_diagnostics = bool(
        re.search(r"\b(?:запис\w*[^.!?]{0,70}\bна\s+)?диагностик\w*", head, re.I)
        and re.search(
            r"\b(?:двигател\w*|цеп\w*|грм|оборот\w*|мощност\w*|педал\w*|газ\w*|"
            r"не\s+едет|плохо\s+едет|под\s+капот\w*)\b",
            head,
            re.I,
        )
    )
    strong_body_damage = any(
        p in head
        for p in (
            "вмятин",
            "царапин",
            "покраск",
            "лкп",
            "лакокрас",
            "без покраски",
            "полировк",
            "риктовк",
            "рихтовк",
            "бампер",
            "крыло",
        )
    )
    # 21938: «свист из-под двигателя / из-под капота» + гарантийная проверка —
    # механическая диагностика, не кузовной цех.
    mechanical_noise_diagnostics = bool(
        re.search(
            r"\b(?:свист\w*|шум\w*|скрежет\w*|ролик\w*|помп\w*|натяжн\w*)\b",
            head,
            re.I,
        )
        and re.search(
            r"\b(?:двигател\w*|капот\w*|газовк\w*|оборот\w*)\b",
            head,
            re.I,
        )
        and re.search(r"\b(?:провер\w*|посмотр\w*|диагност\w*|гаранти\w*)\b", head, re.I)
    )
    if not body_line and mechanical_noise_diagnostics and not strong_body_damage:
        return False
    if not body_line and explicit_mechanical_diagnostics and not strong_body_damage:
        return False
    # Явная смета/запись регламентного ТО — не кузовной.
    if contains_any_to_marker_hit(low)[0] and _regulatory_to_price_or_composition_context(low):
        return False
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    return True


def _is_post_visit_body_panel_fit_complaint(low: str) -> bool:
    """
    Пост-визитная претензия по подгонке кузовных элементов:
    «крыло/бампер не защёлкнуты», «зазор волной» и т.п.
    Это кузовная тема (body_shop), даже если в звонке нет новой записи.
    """
    head = (low or "")[:4200]
    if not head.strip():
        return False
    has_body_part = bool(re.search(r"\b(?:крыл\w*|бампер\w*|капот\w*|зазор\w*)\b", head, re.I))
    if not has_body_part:
        return False
    fit_issue = bool(
        re.search(r"\bне\s+(?:защ[её]лк\w*|щ[её]лк\w*|закреп\w*|плотно\s+стоит)\b", head, re.I)
        or re.search(r"\bзазор\w*[^.!?]{0,24}\b(?:волной|крив\w*|неровн\w*|больш\w*)\b", head, re.I)
        or re.search(r"\bотщ[её]лк\w*\b", head, re.I)
    )
    if not fit_issue:
        return False
    has_post_visit_context = bool(
        re.search(
            r"\b(?:вчера|после\s+ремонт\w*|забирал\w*|забирала|после\s+визита|на\s+ремонте)\b",
            head,
            re.I,
        )
    )
    return has_post_visit_context


def _is_brake_pad_replacement_booking_intake(low: str) -> bool:
    """16387: запись на замену тормозных колодок — прочие работы, не диагностика/ТО."""
    head = (low or "")[:2400]
    brake_work = bool(
        re.search(
            r"\bзамен\w*[^.!?]{0,48}?\b(?:тормозн\w*\s+)?колод",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:тормозн\w*\s+)?колод\w*[^.!?]{0,48}?\b(?:замен|меня)\w*",
            head,
            re.I,
        )
        or re.search(r"\bколод\w*\s+меня\w*", head, re.I)
        or re.search(
            r"\b(?:помен\w*|меня\w*)[^.!?]{0,48}?\b(?:тормозн\w*\s+)?колод\w*\b",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:тормозн\w*\s+)?колод\w*[^.!?]{0,48}?\bпомен\w*",
            head,
            re.I,
        )
    )
    if not brake_work:
        return False
    has_to_markers = contains_any_to_marker_hit(low)[0] or strong_scheduled_to_signal_present(low)
    if has_to_markers:
        opening = (low or "")[:1100]
        tail = (low or "")[1100:5200]
        # 27443: клиент может открыться фразой «записаться на техобслуживание»,
        # но фактическая работа и запись далее — именно замена колодок.
        opening_generic_to_request = bool(
            re.search(
                r"\b(?:запис(?:аться|ать)\s+на\s+|на\s+)?(?:техобслуживан\w*|"
                r"техническ\w+\s+обслуживан\w*|\bто\b)\b",
                opening,
                re.I,
            )
        )
        has_strong_regulatory_to_in_tail = bool(
            re.search(
                r"\b(?:перв\w+\s+то|втор\w+\s+то|трет\w+\s+то|четверт\w+\s+то|"
                r"пят\w+\s+то|шест\w+\s+то|седьм\w+\s+то|восьм\w+\s+то|"
                r"девят\w+\s+то|десят\w+\s+то|объемн\w+\s+то|объ[её]мн\w+\s+то|"
                r"регламент\w*|то\s*[-]?\s*\d{1,3}|что\s+входит\s+в\s+то|"
                r"стоимост\w*[^.!?]{0,40}\bто\b)\b",
                tail,
                re.I,
            )
        )
        if not (opening_generic_to_request and not has_strong_regulatory_to_in_tail):
            return False
    return True


def _employee_availability_not_vehicle_defect(opening: str) -> bool:
    """16387: «Влад … работает сегодня? — нет, не работает» — не неисправность авто."""
    head = (opening or "")[:3200]
    # Не путать с узлом авто: «диспетчер … прикуриватель не работает» (нормализация без точек).
    if re.search(
        r"\b(?:прикуриватель|люк|панорам\w*|фар\w*|фонар\w*|двигател\w*|коробк\w*|кпп|"
        r"кондиционер\w*|печк\w*|стеклоподъ[её]м\w*|дворник\w*|зеркал\w*|"
        r"сигнализац\w*|магнитол\w*|камер\w*|датчик\w*|аккумулятор\w*|генератор\w*|"
        r"стартер\w*|тормоз\w*|подвеск\w*|руль\w*|машин\w*|автомобил\w*)\s+не\s+работает\b",
        head,
        re.I,
    ):
        return False
    return bool(
        re.search(
            r"\b(?:работает\s+сегодня|сегодня\s+работает|будет\s+(?:на\s+)?работ)\b"
            r"[^.!?]{0,60}?\bне\s+работает\b",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:боровых|сотрудник|мастер|слесар|приемщик|приёмщик|диспетчер)\w*"
            r"[^.!?]{0,70}?\bне\s+работает\b",
            head,
            re.I,
        )
    )


def _is_spare_parts_department_inquiry_intake(low: str) -> bool:
    """16295: отдел ЗЧ + подбор детали по VIN/артикулу — прочие работы, не ТО."""
    if not re.search(r"\bотдел\s+запасн", low, re.I):
        return False
    if not any(
        p in low
        for p in (
            "наконечник",
            "артикул",
            "артик",
            "вин-код",
            "vin-код",
            "вин код",
            "vin код",
            "подобра",
            "наличи",
            "запчаст",
        )
    ):
        return False
    if contains_any_to_marker_hit(low)[0] or strong_scheduled_to_signal_present(low):
        return False
    return True


def _contains_nissan_for_client_brand(low: str, brand_low: str) -> Tuple[bool, str]:
    """
    Nissan как марка авто клиента; не «на Nissan Tirana у нас есть…» в отделе ЗЧ (16295).
    """
    has_ns, hit = _contains_any(brand_low, _NISSAN_EXACT)
    if not has_ns:
        has_ns, hit = _nissan_stt_matrix_hit(brand_low)
    if not has_ns:
        return False, ""
    if jetour_brand_mentioned((low or "")[:1200]):
        if re.search(
            r"\b(?:у\s+нас|в\s+каталог\w*|есть\s+на)\s+[^.!?]{0,50}?\bnissan\b",
            brand_low,
            re.I,
        ) or re.search(
            r"\bnissan\w*.{0,80}?\b(?:у\s+нас|стабилизатор|втулк|вместе\s+со)\b",
            brand_low,
            re.I,
        ):
            return False, ""
    return has_ns, hit


def _repair_parts_status_blocks_bare_particle_to(low: str) -> bool:
    """Статус ремонта/поставки запчастей — «то» в STT не плановое ТО (9117, 14087)."""
    if not any(p in low for p in ("ремонт", "запчаст", "боковин", "кузовн", "поставк")):
        return False
    return any(
        p in low
        for p in (
            "запчаст",
            "поставк",
            "пришл",
            "прийт",
            "детал",
            "на каком этапе",
            "силовой",
            "задн",
            "боковин",
            "после аварии",
            "кузовн",
            "когда забрать",
            "мастер-прием",
            "мастер-приём",
            "приемщик",
            "приёмщик",
            "переведу",
            "долго ждать",
            "не звонят",
        )
    )


def _is_inbound_opening_regulatory_to_booking(low: str) -> bool:
    """
    Входящий: с начала «пройти/сделать ТО» или «подошло N-е ТО», тема поддержана ценой/регламентом/слотом —
    новая запись на регламентное ТО (9409), не перенос и не доп. к визиту (9476).
    """
    opening = (low or "")[:1800]
    if not opening.strip():
        return False
    # 13945: уточнение по уже сделанной записи («хотел уточнить… записывался на нулевое ТО») — не новая СТО_ТО_вх.
    opening_early = opening[:900]
    has_fresh_to_booking_intent = bool(
        re.search(
            r"\b(?:хочу|хотел[аи]?|нужно|надо)\b[^.!?]{0,120}"
            r"\b(?:пригн\w*\s+(?:машин\w+|автомобил\w+)\s+на\s+"
            r"(?:техобслуж\w*|техническ\w+\s+обслуживан\w*)"
            r"|(?:на\s+)?то\b[^.!?]{0,60}\bзапис)\b",
            opening_early,
            re.I,
        )
        or re.search(
            r"\bможем\b[^.!?]{0,40}\b(?:сейчас\s+)?(?:с\s+вами\s+)?\bзапис(?:ать|аться)\b",
            opening_early,
            re.I,
        )
    )
    if re.search(r"\bхотел\w*\s+уточн", opening_early, re.I) and re.search(
        r"\bзаписывал", opening_early, re.I
    ):
        if not has_fresh_to_booking_intent:
            return False
    if re.search(r"\bуточн", opening_early, re.I) and re.search(
        r"\bзаписывал(?:ся|ись|и)\b", opening_early, re.I
    ):
        if not has_fresh_to_booking_intent:
            return False
    # 9741: «записался сегодня на 10:00, не получается приехать» — перенос слота, не новая СТО_ТО_вх.
    head_open = opening[:700]
    if (
        re.search(r"\bзаписал(?:ся|ись)\s+сегодня\b", head_open, re.I)
        and re.search(r"\bна\s+\d{1,2}\s*[.:]?\s*\d{2}\b", head_open)
        and any(
            p in head_open
            for p in (
                "не получается приехать",
                "не получается подъехать",
                "не получится",
                "не смогу приехать",
                "не получилось приехать",
            )
        )
    ):
        return False
    if re.search(
        r"\b(?:уже\s+записан|я\s+записан|мы\s+записан|записан[аы]?\s+на\s+то)\b",
        opening[:900],
        re.I,
    ):
        if not re.search(r"\b(?:пройти\s+то|то\s+пройти)\b", opening[:700], re.I) and not re.search(
            r"\b(?:сделать|хотел[аи]|хочу)\s+(?:на\s+)?то\b", opening[:700], re.I
        ) and not re.search(
            r"\bподошл[ао]\s+(?:втор|трет|четвер|перв|нулев)\w*\s+то\b", opening[:700], re.I
        ):
            return False
    opening_to = bool(
        re.search(r"\bпройти\s+то\b", opening, re.I)
        or re.search(r"\bто\s+пройти\b", opening, re.I)
        or re.search(r"\b(?:надо|нужно)\s+то\s+пройти\b", opening, re.I)
        or re.search(r"\bпройти\s+техническ", opening, re.I)
        # 20065: «хочу пригнать машину на техобслуживание» — явное намерение записи на регламентное ТО.
        or re.search(
            r"\b(?:хочу|хотел[аи]?|нужно|надо)\b[^.!?]{0,80}\bпригн\w*\s+"
            r"(?:машин\w+|автомобил\w+)\s+на\s+(?:техобслуж\w*|техническ\w+\s+обслуживан\w*)",
            opening[:900],
            re.I,
        )
        or re.search(r"\bсделать\s+то\b", opening, re.I)
        or re.search(
            r"\b(?:нулев|перв|втор|трет|четвер|четвёрт|пят|шест)\w*\s+то\s+сделать\b",
            opening,
            re.I,
        )
        or re.search(r"\bто\s+один\s+сделать\b", opening, re.I)
        or re.search(r"\b(?:хотел[аи]|хочу)\s+(?:на\s+)?то\s+(?:сделать|запис)", opening, re.I)
        or re.search(r"\b(?:хотел[аи]|хочу)\s+то\s+сделать\b", opening, re.I)
        or re.search(
            r"\bподошл[ао]\s+(?:втор|трет|четвер|перв|нулев)\w*\s+то\b", opening, re.I
        )
        or re.search(r"\bпошл[ао]\s+(?:втор|трет|четвер|перв)\w*\s+то\b", opening, re.I)
        or re.search(r"\bподскаж\w+.{0,50}\bто\b", opening[:700], re.I)
        or re.search(r"\bсколько\s+стоит\s+то\b", opening, re.I)
        # 9506: «узнать стоимость нулевого ТО» — запрос регламентного ТО с начала разговора.
        or re.search(r"\b(?:узнать|стоимост)\w*\s+нулев\w*\s+то\b", opening, re.I)
        or re.search(r"\bнулев\w*\s+то\b", opening, re.I)
        # STT: «записи МТО на очередное» (9467); после нормализации мто→то.
        or re.search(r"\bзапис\w*\s+(?:мто|то)\s+на\s+очеред", opening, re.I)
        # STT 12462: «записаться на очередное ТО» — другой порядок слов.
        or re.search(r"\bзапис\w*\s+на\s+очередн\w*\s+то\b", opening, re.I)
        or re.search(r"\bнасчет\s+запис\w*\s+(?:мто|то)\b", opening, re.I)
        or (
            re.search(r"\bмто\b", opening, re.I)
            and re.search(r"\bзапис", opening[:500], re.I)
        )
        # 9651: «нужно записаться на третье», «каждый год … ТО прохожу обслуживание».
        or (
            re.search(r"\b(?:нужно|надо|хотел[аи]?)\s+записаться", opening[:900], re.I)
            and re.search(r"\b(?:перв|втор|трет|четвер|нулев)\w*\b", opening[:900], re.I)
        )
        or re.search(r"\bкаждый\s+год.{0,120}\bто\s+прохожу", opening, re.I)
        or re.search(r"\bпрохожу\s+обслуживан", opening[:900], re.I)
        # 11383: STT «Мжно второе ТО записаться» — новая запись с начала, не уточнение слота.
        or re.search(
            r"\b(?:можно|мжно|нужно|надо|хотел[аи]?|хочу)\s+"
            r"(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то\s+запис",
            opening[:900],
            re.I,
        )
        or re.search(
            r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то\s+запис",
            opening[:900],
            re.I,
        )
        # 11581: «хотела на нулевое ТОО записаться» (STT тоо / перестановка слов).
        or (
            re.search(r"\bхотел[аи]?\s+на\s+нулев\w*\s+то", opening[:900], re.I)
            and re.search(r"\bзапис", opening[:900], re.I)
        )
        or re.search(r"\bнулев\w*\s+то+\s+запис", opening[:900], re.I)
        # 14017: «хотела записаться на ТО» / «на техобслуживание записаться» с начала разговора.
        or re.search(
            r"\bзапис(?:аться|ать|ите)\s+на\s+(?:то|техническ|техобслуж)",
            opening[:700],
            re.I,
        )
        or re.search(
            r"\bзапиш(?:ите|ем|у)\b[^.!?]{0,60}\bна\s+(?:то|техническ|техобслуж)\b",
            opening[:700],
            re.I,
        )
        or re.search(
            r"\b(?:хотел[аи]|хочу)[^.!?]{0,100}\bзапис\w*\s+на\s+(?:то|техническ|техобслуж)",
            opening[:700],
            re.I,
        )
        # 14019: «записаться на пятое ТО» / «можно записаться на пятое» (N-е между «на» и «то»).
        or re.search(
            r"\bзапис(?:аться|ать)\s+на\s+(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то\b",
            opening[:700],
            re.I,
        )
        or re.search(
            r"\b(?:можно|мжно|надо|нужно|хотел[аи]?|хочу)\b[^.!?]{0,50}\bзапис\w*\s+на\s+"
            r"(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то\b",
            opening[:700],
            re.I,
        )
        # 15843: «по пробегу подходит второе ТО» + «хотелось бы записаться».
        or re.search(
            r"\bпо\s+пробег\w*\s+подход\w*\s+(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то\b",
            opening[:900],
            re.I,
        )
        # 15883: «время подошло по километражу на ТО-3».
        or re.search(
            r"\b(?:время\s+)?подошл\w*[^.!?]{0,80}?\b(?:на\s+)?то\s*-\s*[0-9]\b",
            opening[:900],
            re.I,
        )
        or re.search(
            r"\bпо\s+(?:километраж|пробег)\w*[^.!?]{0,50}?\b(?:на\s+)?то\s*-\s*[0-9]\b",
            opening[:900],
            re.I,
        )
        or re.search(
            r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то\b"
            r"[^.!?]{0,100}?\bхотел\w*\s+[^.!?]{0,30}?\bзапис",
            opening[:900],
            re.I,
        )
        # 16146: «Первое ТО, сколько у вас будет стоить?» — запрос цены регламентного ТО с начала.
        or re.search(
            r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то\b"
            r"[^.!?]{0,100}?\b(?:сколько|стоимост)",
            opening[:900],
            re.I,
        )
        # 14496 / 16743: «на ТО записаться» / «на ТО машину записать» (слова между «на то» и «запис»).
        or re.search(r"\bна\s+то\b[^.!?]{0,50}?\bзапис", opening[:700], re.I)
        # 18144: «надо машину на техобслуживание записать» / «на техобслуживание записаться».
        or re.search(
            r"\bна\s+(?:техобслуж\w*|техническ\w+\s+обслуживан\w*)\b[^.!?]{0,50}?\bзапис",
            opening[:900],
            re.I,
        )
        or re.search(
            r"\b(?:надо|нужно|хотел[аи]?|хочу|подскаж\w*)\b[^.!?]{0,100}"
            r"\b(?:машин\w+|автомобил\w+)\s+на\s+(?:техобслуж\w*|техническ\w+\s+обслуживан\w*|то)\b"
            r"[^.!?]{0,40}?\bзапис",
            opening[:900],
            re.I,
        )
        # 18185: «техобслуживание 23. Записать хотели бы автомобиль?»
        or re.search(
            r"\b(?:техобслуж\w*|техническ\w+\s+обслуживан\w*)\b[^.!?]{0,60}"
            r"\bзапис(?:ать|ыва\w*)?\s+хотел",
            opening[:900],
            re.I,
        )
        or re.search(
            r"\bзапис(?:ать|ыва\w*)?\s+хотел\w*\s+(?:бы\s+)?автомобил",
            opening[:900],
            re.I,
        )
        # 16368: CRM «посмотрите ТО» — уточнение регламента перед записью.
        or re.search(r"\bпосмотр(?:ите|им)\s+то\b", opening[:900], re.I)
    )
    if not opening_to:
        return False
    return bool(
        re.search(r"\b(?:стоимост\w*|сколько\s+стоит|выходит\s+\d|₽|\bруб)\b", low, re.I)
        or re.search(r"\bполучается\s+\d", low, re.I)
        or re.search(r"\bна\s+данном\s+то\b", low, re.I)
        or re.search(r"\b(?:меня(?:ет|ются|ем)|меняется)\s+масл", low, re.I)
        or re.search(r"\bследующ\w*\s+то\b", low, re.I)
        or re.search(r"\bпредыдущ\w*\s+то\b", low, re.I)
        or re.search(r"\b(?:запиш(?:емся|ем)|давайте\s+запиш)\w*\b", low, re.I)
        or re.search(
            r"\bможем\b[^.!?]{0,40}\b(?:сейчас\s+)?(?:с\s+вами\s+)?\bзапис(?:ать|аться)\b",
            low,
            re.I,
        )
        or re.search(r"\bзамен\w*\s+масл", low, re.I)
        # 18185: скрипт «кроме ТО потребуется / вопросы дополнительные».
        or re.search(r"\bкроме\s+то\b[^.!?]{0,50}\bпотребу", low, re.I)
        or re.search(r"\bвопросы\s+дополнительн", low, re.I)
        or re.search(r"\bзаписал[аи]\s+на\s+\d", low, re.I)
        or re.search(
            r"\b(?:нулев\w+\s+то|перв\w+\s+то).{0,160}(?:втор|трет)\w*\s+то\b", low, re.I
        )
        or re.search(r"\b(?:втор|трет|четвер|перв)\w*\s+то\s+сделал", low, re.I)
        or re.search(
            r"\bзапис\w+.{0,40}(?:июн|июл|мая|август|сент|октяб|ноябр|декабр|январ|феврал|март|апрел)",
            low,
            re.I,
        )
        or re.search(r"\bпо\s+времени\s+\d", low, re.I)
        # 15883: срок/пробег подошёл на ТО-N в начале — новая запись на регламентное ТО.
        or re.search(
            r"\b(?:время\s+)?подошл\w*[^.!?]{0,80}?\b(?:на\s+)?то\s*-\s*[0-9]\b",
            opening[:900],
            re.I,
        )
        or re.search(
            r"\bпо\s+(?:километраж|пробег)\w*[^.!?]{0,50}?\b(?:на\s+)?то\s*-\s*[0-9]\b",
            opening[:900],
            re.I,
        )
    )


def _is_inbound_to_late_arrival_notice(low: str) -> bool:
    """
    10441: уже записаны на регламентное ТО (слот сегодня) — перезвон предупредить об опоздании, не новая СТО_ТО_вх.
    """
    head = (low or "")[:1400]
    if not head.strip():
        return False
    existing_to = bool(
        re.search(r"\bзаписывал(?:ся|ись|и)\s+на\s+то\b", head, re.I)
        or re.search(r"\bзаписан[аы]?\s+на\s+то\b", head, re.I)
        or re.search(r"\bна\s+то\s+записан", head, re.I)
        or re.search(r"\b(?:я|мы)\s+.{0,40}на\s+то\s+записывал", head, re.I)
    )
    if not existing_to:
        return False
    return any(
        p in head
        for p in (
            "задерживаем",
            "задерживаю",
            "задержим",
            "задержал",
            "опаздыва",
            "опозда",
            "опоздан",
            "не успеем",
            "не успею",
            "не успева",
            "пробка",
            "в пробке",
            "застряли",
            "застрял",
            "предупредил",
            "предупрежда",
            "едем с опоздан",
        )
    )


def _is_inbound_existing_to_service_visit_cancel_or_reschedule(low: str) -> bool:
    """
    12687: отмена/перенос уже существующей записи на ТО/техобслуживание — не новая запись.
    «по поводу отмены на техобслуживание», «записывался на 10», «вашу запись перенесли на …».
    """
    from call_analytics.sto_to_rubric import (
        _dispatcher_third_party_slot_cancel_mention_not_client_intent,
        _inbound_diagnostic_only_booking_with_first_to_intake,
        _inbound_first_to_regulatory_booking_intake,
    )

    head = (low or "")[:4000]
    if not head.strip():
        return False
    explicit_reschedule_request = bool(
        re.search(
            r"\b(?:можно\s+ли\s+)?перенест\w*\s+(?:запис\w*|время|дат\w*|день)\b",
            head,
            re.I,
        )
        or re.search(r"\bпереносим\b", head, re.I)
        or re.search(r"\bперезапис\w*\b", head, re.I)
        or re.search(r"\bна\s+друг\w+\s+день\b", head, re.I)
    )
    third_party_slot_release_hypothesis = bool(
        re.search(
            r"\bкто[-\s]*то\b[^.!?]{0,80}\b(?:перенес\w*|отмен\w*)\b",
            head,
            re.I,
        )
    )
    # 20857: исходящий CRM-перезвон на новую запись.
    # «кто-то перенесет/отменит» здесь про возможное освобождение слота, а не про отмену текущей записи клиента.
    if (
        not explicit_reschedule_request
        and third_party_slot_release_hypothesis
        and _crm_outbound_new_to_application_opening(low)
    ):
        return False
    has_cancel_ctx = bool(
        re.search(r"\bпо\s+поводу\s+отмен\w*\b", head, re.I)
        or re.search(r"\b(?:вашу|ваш\w*)\s+запис\w*\s+перенес", head, re.I)
        or re.search(r"\bперезапис\w*\b", head, re.I)
    )
    if _inbound_first_to_regulatory_booking_intake(low) and not explicit_reschedule_request and not has_cancel_ctx:
        return False
    if _inbound_diagnostic_only_booking_with_first_to_intake(low) and not explicit_reschedule_request and not has_cancel_ctx:
        return False
    has_to_topic = bool(
        re.search(r"\b(?:техобслуживан|техническ\w+\s+обслуживан)\w*\b", head, re.I)
        or contains_any_to_marker_hit(head)[0]
    )
    if not has_to_topic:
        return False
    conditional_cancel = bool(
        re.search(r"\bесли\b[^.!?]{0,90}\bотмен\w*\b", head, re.I)
        or re.search(r"\bесли\s+поближ\w*[^.!?]{0,60}\bотмен\w*\b", head, re.I)
    )
    has_new_slot_confirmation = bool(
        re.search(
            r"\b(?:записыва\w*|записал[аи]?\s+вас|записал(?:ись|ся)|"
            r"предварительно\s+договорились|будем\s+ожидать)\b",
            head,
            re.I,
        )
        and (
            re.search(r"\bна\s+\d{1,2}[\s-]*(?:е|го)?\s+числ\w*\b", head, re.I)
            or re.search(r"\b\d{1,2}[:\s.]\d{2}\b", head, re.I)
        )
    )
    if has_new_slot_confirmation and not re.search(
        r"\b(?:по\s+поводу\s+отмен|перенест\w*|перезапис\w*|"
        r"вашу\s+запис\w*\s+перенес|был[аи]?\s+записан)\b",
        head,
        re.I,
    ) and (
        conditional_cancel
        or not explicit_reschedule_request
        or re.search(
            r"\b(?:мы\s+с\s+вами\s+записались|накануне[^.!?]{0,30}напомним|благодар\w+)\b",
            head,
            re.I,
        )
    ):
        return False
    existing_booking = bool(
        re.search(r"\bзаписывал(?:ся|ась|ись)\b", head, re.I)
        or re.search(r"\bзаписал(?:ся|ась|ись)\b", head, re.I)
        or re.search(r"\bменя\s+записал[аи]\b", head, re.I)
        or re.search(r"\b(?:вашу|ваш\w*)\s+запис\w*\s+перенес", head, re.I)
        or re.search(r"\bпо\s+поводу\s+отмен\w*\s+на\s+техобслужив", head, re.I)
        # 21175: «запись на 11 августа ... отменяем», «пока отбой сделайте».
        or re.search(
            r"\bзапис[ьяи]\w*\s+на\b[^.!?]{0,90}\b(?:отмен\w*|отбой)\b",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:отбой\s+сдела\w*|сдела\w*\s+отбой)\b[^.!?]{0,90}\bзапис\w*\b",
            head,
            re.I,
        )
        # 19445: «в понедельник на техосмотр, в 12:30 записали меня».
        or re.search(
            r"\b(?:понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|"
            r"воскресень\w*)\b[^.!?]{0,65}\b(?:техосмотр|то)\b"
            r"[^.!?]{0,90}\bзаписал[аи]\s+меня\b",
            head,
            re.I,
        )
        # 18161: «в записи сегодня автомобиль».
        or re.search(r"\bв\s+записи\s+сегодня\b", head, re.I)
        # 17482: «был записан на сервис» + ошибочно записали ТО / можно поменять.
        or (
            re.search(r"\bбыл[аи]?\s+записан[аы]?\s+на\s+(?:сервис|то)\b", head, re.I)
            and (
                re.search(r"\b(?:можно\s+поменять|ошибочно\s+записал)", head, re.I)
                or re.search(r"\bна\s+\d{1,2}[\s-]*(?:е|го)?\s+числ\w*\s+записан", head, re.I)
            )
        )
    )
    if not existing_booking:
        return False
    if explicit_reschedule_request:
        return True
    if re.search(r"\bпо\s+поводу\s+отмен", head, re.I):
        return True
    if re.search(r"\b(?:вашу|ваш\w*)\s+запис\w*\s+перенес", head, re.I):
        return True
    # 19445: STT «вытеркните» ≈ «вычеркните»; новую дату клиент выберет потом.
    if re.search(r"\b(?:вычеркните|вытеркните)\b", head, re.I):
        return True
    if re.search(r"\bпотом\s+перепиш\w*\b", head, re.I):
        return True
    # 18161: отказ приехать на уже назначенное ТО.
    if re.search(r"\bне\s+одобрил\w*\s+приезд\s+на\s+то\b", head, re.I):
        return True
    if re.search(r"\bне\s+приеду\b", head, re.I) and (
        re.search(r"\bна\s+то\b", head, re.I)
        or re.search(r"\bв\s+записи\s+сегодня\b", head, re.I)
    ):
        return True
    # 17482: коррекция вида ТО в уже сделанной записи («можно поменять», «я сейчас поправлю»).
    if re.search(r"\bбыл[аи]?\s+записан[аы]?\s+на\s+(?:сервис|то)\b", head, re.I) and (
        re.search(r"\bможно\s+поменять\b", head, re.I)
        or re.search(r"\bошибочно\s+записал", head, re.I)
        or re.search(r"\bпоправл", head, re.I)
    ):
        return True
    if "отмен" in head and "запис" in head:
        if re.search(
            r"клиент\w*\s+.{0,45}(?:запис\w+.{0,30}отмен|отмен\w+.{0,30}запис)",
            head,
            re.I,
        ):
            return False
        if _dispatcher_third_party_slot_cancel_mention_not_client_intent(low):
            return False
        return True
    return bool(re.search(r"\b(?:запис\w*\s+)?перенес(?:ли|ли)\b", head, re.I))


def _past_to_or_technical_service_visit_context(early: str) -> bool:
    """Прошедший визит на регламентное ТО / техобслуживание (10769, 13781)."""
    return bool(
        re.search(
            r"\b(?:заезжал\w*|приезжал\w*|были)\b[^.!?]{0,80}\b(?:на\s+)?техническ\w+\s+обслуживан",
            early,
            re.I,
        )
        or re.search(r"\bна\s+техническ\w+\s+обслуживан\w+\s+приезжал", early, re.I)
        or re.search(
            r"\b(?:на\s+то|на\s+техническ\w+\s+обслуживан\w+)\b[^.!?]{0,100}"
            r"(?:не\s+(?:было|оказал\w*|менял\w*)|не\s+поменял)",
            early,
            re.I,
        )
        or re.search(r"\bв\s+прошлый\s+раз\s+не\s+(?:поменял|сделал|замен)", early, re.I)
    )


def _is_warranty_to_scope_consultation_not_regulatory_to_booking(low: str) -> bool:
    """
    17038: ТО как регламент/история в гарантийном споре (AC, заправка), не запись на новое ТО.
    «какие работы на четвёртом ТО», «в рамках ТО», «на четвёртом ТО обращался» при споре о кондиционере.
    """
    seat_cushion_followup = bool(
        re.search(r"\bподушк\w*[^.!?]{0,25}\bсиден\w*\b", low, re.I)
        and re.search(r"\bзапчаст\w*\b", low, re.I)
        and re.search(r"\b(?:гарант|кондиционер|заправк|фреон)\w*\b", low, re.I)
    )
    if _is_inbound_opening_regulatory_to_booking(low) and not seat_cushion_followup:
        return False
    if _is_inbound_crm_next_regulatory_to_booking_intake(low):
        if not seat_cushion_followup:
            return False
    head = (low or "")[:5000]
    if not any(p in head for p in ("гарант",)):
        return False
    if not any(p in head for p in ("кондиционер", "заправк", "фреон", "охлаждающ")):
        return False
    to_reference = bool(
        re.search(r"\b(?:перв|втор|трет|четвер|четвёрт)\w*\s+то\b", head, re.I)
        or re.search(r"\bв\s+рамках\s+[^.!?]{0,40}\bто\b", head, re.I)
        or re.search(r"\bпро\s+то\b", head, re.I)
    )
    consult_not_booking = bool(
        re.search(r"\bкакие\s+работ", head, re.I)
        or re.search(r"\bрегламент", head, re.I)
        or re.search(r"\bобращени\w*", head, re.I)
        or re.search(r"\bне\s+является", head, re.I)
        or re.search(r"\bне\s+могу\s+сказать", head, re.I)
        or re.search(r"\bне\s+проверя", head, re.I)
    )
    return to_reference and consult_not_booking


def _is_oil_change_only_request(low: str) -> bool:
    """
    Клиент просит «только/просто масло поменять» как минимальный объём работ.
    Это отдельная сервисная работа (прочие), а не регламентное ТО.
    """
    head = (low or "")[:3200]
    has_oil_change = bool(
        re.search(
            r"\b(?:только|просто|по\s+минимуму)\b[^.!?]{0,40}\b"
            r"(?:замен\w*|помен\w*)[^.!?]{0,30}\bмасл\w*|"
            r"\bмасл\w*[^.!?]{0,30}\b(?:замен\w*|помен\w*)[^.!?]{0,30}\b"
            r"(?:только|просто)\b",
            head,
            re.I,
        )
    )
    if not has_oil_change:
        return False
    if _is_body_shop_service_intake(head) or _is_insurance_claim_body_inspection_intake(head):
        return False
    return True


def _is_regulatory_to_timing_consultation_without_booking(low: str) -> bool:
    """
    Консультация по сроку/пробегу регламентного ТО без фактического согласования слота.
    """
    head = (low or "")[:3200]
    has_to_topic = bool(
        contains_any_to_marker_hit(head)[0]
        or re.search(
            r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят|нулев)\w*"
            r"[^.!?]{0,20}\bто\b",
            head,
            re.I,
        )
    )
    if not has_to_topic:
        return False
    has_timing_question = bool(
        re.search(
        r"\b(?:во\s+сколько|на\s+пробег\w*|пробег\w*|год\s+с\s+момента|"
        r"через\s+сколько|за\s+сколько\s+запис)\b",
        head,
        re.I,
        )
    )
    if not has_timing_question:
        return False
    # Обсуждение сервисной книжки/штампов по прошлым ТО без вопроса о сроке нового ТО
    # — это не консультация по новому регламентному циклу.
    if (
        re.search(r"\b(?:сервисн\w*\s+книжк\w*|книжк\w*|штамп\w*|отметк\w*|восстанов\w*)\b", head, re.I)
        and not re.search(r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то\b", head, re.I)
    ):
        return False
    if re.search(
        r"\b(?:вас\s+)?записал[аи]\w*\b|"
        r"\b(?:давайте\s+запиш\w*|запишем|внес\w*\s+в\s+запис)\b",
        head,
        re.I,
    ):
        return False
    return True


def _is_arrived_interior_part_replacement_booking_intake(low: str) -> bool:
    """
    CRM перезвон: запчасти по дефекту/гарантии (сиденье, фонарь, сирена…) —
    согласование визита, не регламентное ТО (13647, 15824, 17038).
    Маркеры ТО в хвосте (состав N-го ТО, гарантийный спор) не отменяют визит по запчастям.
    """
    early = (low or "")[:2800]
    if not early.strip():
        return False
    # 19988: явное намерение регламентного ТО и запись на слот приоритетнее
    # сопутствующих жалоб/дефектов в хвосте.
    if _regulatory_to_intent_takes_priority(low):
        return False
    # Новая CRM-заявка именно на ТО имеет приоритет над дополнительной жалобой:
    # сиденье/обшивку осмотрят вместе с регламентным обслуживанием (18574).
    if _crm_outbound_new_to_application_opening(low):
        return False
    # 21379: обсуждение очередного ТО (регламент + стоимость) в исходящем перезвоне
    # не должно уводиться в "запчасти по дефекту" из-за упоминания заказ-наряда/наличия.
    if re.search(r"\bименно\s+техническ\w+\s+обслуживан\w*\b", early, re.I) and re.search(
        r"\b(?:стоимост\w*|сколько\b|что\s+входит\b|очередн\w*)\b",
        early,
        re.I,
    ):
        return False
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    if _is_inbound_crm_next_regulatory_to_booking_intake(low):
        return False
    # 22933: упоминание «заказ-наряд» в консультации по ТО/своим расходникам
    # не означает CRM-перезвон по дефектной запчасти.
    if re.search(r"\bзаказ-?\s*наряд\b", early, re.I) and not re.search(
        r"\b(?:подушк|сиден|фонар|сирен|дефект|неисправност|гаранти)\b",
        early,
        re.I,
    ):
        return False
    opening = early[:900]
    if re.search(r"\bзапис(?:аться|ать)\s+на\s+(?:то|техническ|техобслуж)", opening, re.I):
        if not re.search(
            r"\b(?:подушк|сиден|фонар|запчаст|сирен|сигнализац|дефект|неисправност)",
            opening,
            re.I,
        ):
            return False
    part_ctx = bool(
        re.search(r"\bподушк", early, re.I)
        or re.search(r"\bсиден", early, re.I)
        or re.search(r"\bфонар", early, re.I)
        or (
            re.search(r"\bзапчаст", early, re.I)
            and re.search(
                r"\b(?:поступ|ожида|заказан|закаж\w*|придут|конец\s+недел|пришл)",
                early,
                re.I,
            )
        )
        or re.search(r"\bсирен", early, re.I)
    )
    if not part_ctx:
        return False
    visit_sched = bool(
        re.search(r"\b(?:спланируем|планируем)\s+вам\s+визит", early, re.I)
        or re.search(r"\bсогласовать\s+день", early, re.I)
        or re.search(r"\bприглаш", early, re.I)
        or re.search(r"\bпоступил", low, re.I)
        or re.search(r"\bзапчаст\w*[^.!?]{0,50}\bпоступ", low[:4500], re.I)
        or re.search(r"\b(?:будем|ждём|ожида)\w*[^.!?]{0,40}\bвас\b", low, re.I)
        or re.search(r"\bзаписал\w*\s+(?:вас\s+)?на\b", low, re.I)
    )
    defect_claim = bool(
        re.search(r"\bобращал\w*", early, re.I)
        or re.search(r"\bзаказ\w*[^.!?]{0,40}\b(?:запчаст|детал)\w*", early, re.I)
        or re.search(r"\bодобрен\w*\s+замен", early, re.I)
        or re.search(
            r"\bзамен\w*[^.!?]{0,60}\b(?:подушк|сиден|фонар|сирен|запчаст|детал)\w*",
            early,
            re.I,
        )
    )
    return visit_sched or defect_claim


def _is_inbound_crm_next_regulatory_to_booking_intake(low: str) -> bool:
    """
    14017: «записаться на ТО» + CRM-история прошлого визита + следующее N-е / ТО-60 —
    новая регламентная запись, не отложенная замена после прошлого ТО (10769).
    """
    opening = (low or "")[:900]
    if not (
        re.search(r"\bзапис(?:аться|ать)\s+на\s+(?:то|техническ|техобслуж)", opening, re.I)
        or re.search(
            r"\bзапис(?:аться|ать)\s+на\s+(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то\b",
            opening,
            re.I,
        )
        or re.search(
            r"\b(?:хотел[аи]|хочу)[^.!?]{0,100}\bзапис\w*\s+на\s+(?:то|техническ|техобслуж)",
            opening,
            re.I,
        )
        or re.search(
            r"\b(?:хотел[аи]|хочу)[^.!?]{0,100}\bзапис\w*\s+на\s+"
            r"(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то\b",
            opening,
            re.I,
        )
    ):
        return False
    head = (low or "")[:4500]
    next_to = bool(
        re.search(
            r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев|шестидесят\w*)\w*\s+(?:то|ту)\b",
            head,
            re.I,
        )
        or re.search(r"\bто[-\s]*(?:60|120|20|40|80|105)\b", head, re.I)
        or re.search(
            r"\b(?:следующ\w*|будет|получается|ежегодн\w*)\b[^.!?]{0,50}\b(?:то|ту|техническ\w+\s+обслужив)",
            head,
            re.I,
        )
    )
    reg_ctx = bool(
        re.search(r"\b(?:меня(?:ет|ются)|меняется)\s+масл", head, re.I)
        or re.search(r"\b(?:стоимост|сколько\s+стоит|₽|руб|\d[\d\s]{3,5})\b", head, re.I)
        or re.search(r"\b(?:фильтр|тормозн\w+\s+жидкост)", head, re.I)
    )
    return next_to and reg_ctx


def _is_past_to_work_order_document_request_not_narrow_to(low: str) -> bool:
    """
    15058: запрос копии/скана заказ-наряда прошлого регламентного ТО у дилера (для другого СТО),
    без новой записи на ТО у нас — узкий НЕ_ТО.
    """
    if not (low or "").strip():
        return False
    if crm_outbound_explicit_to_topic_present(low):
        return False
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    if _is_inbound_crm_next_regulatory_to_booking_intake(low):
        return False
    if re.search(r"\bзаписал[аи]\w*\s+(?:вас\s+)?на\b", low):
        return False

    doc_request = bool(
        re.search(
            r"\b(?:можно|могу|нужен|нужна|нужно|хотел\w*|надо|пришлит\w*|"
            r"отправ\w*|скин\w*)\b[^.!?]{0,100}"
            r"\b(?:заказ-?\s*наряд|наряд\s*допуск|скан-?\s*коп\w*|копи\w*|документ\w*)\b",
            low,
            re.I,
        )
        or re.search(
            r"\b(?:заказ-?\s*наряд|наряд\s*допуск|скан-?\s*коп\w*|копи\w*|документ\w*)\b"
            r"[^.!?]{0,120}\b(?:на\s+почт\w*|в\s+почт\w*|отправ\w*|пришлит\w*|скин\w*)\b",
            low,
            re.I,
        )
    )
    if not doc_request:
        return False

    past_at_dealer = bool(
        re.search(
            r"\b(?:делал|делали|заезжал\w*)\b[^.!?]{0,100}?\b(?:у\s+вас|к\s+вам|дилерск|викинг)",
            low,
            re.I,
        )
        or re.search(
            r"\b(?:у\s+вас|к\s+вам|дилерск\w*)\b[^.!?]{0,100}?\b(?:делал|заезжал|техническ\w+\s+обслужив)",
            low,
            re.I,
        )
        or (
            past_to_reference_present(low)
            and re.search(r"\b(?:делал|заезжал|2025|2024)\b", low, re.I)
        )
    )
    if not past_at_dealer:
        return False

    external_to = bool(
        re.search(r"\bпрохож\w*\s+(?:сейчас\s+)?(?:перв|втор|трет|четвер)\w*\s+то\b", low, re.I)
        and re.search(r"\b(?:самар|в\s+друг\w+\s+(?:город|дилер))\b", low, re.I)
    )
    first_to_proof = bool(
        re.search(r"\b(?:на\s+)?перв\w*\s+то\b", low, re.I)
        and re.search(r"\b(?:наряд|допуск|регламент|работ\w*)\w*", low, re.I)
    )
    return external_to or first_to_proof


def _has_new_service_booking_confirmed_after_history(low: str) -> bool:
    """Новая запись на слот после обсуждения истории обслуживания/сервисной книжки."""
    if not (low or "").strip():
        return False
    booking_request = bool(
        re.search(
            r"\b(?:можно|хочу|хотел\w*|нужно|надо)\b[^.!?]{0,45}\bзапис\w*",
            low,
            re.I,
        )
        or re.search(r"\bзаписаться\b[^.!?]{0,45}\b(?:на|в)\b", low, re.I)
    )
    slot_selected = bool(
        re.search(
            r"\bдавайте\b[^.!?]{0,90}\b(?:понедельник|вторник|сред\w*|четверг|"
            r"пятниц\w*|суббот\w*|воскресень\w*|\d{1,2}(?:-го)?)\b"
            r"[^.!?]{0,45}\b(?:\d{1,2}[.:]\d{2}|\d{1,2}\s+\d{2})\b",
            low,
            re.I,
        )
    )
    final_confirmation = bool(
        re.search(
            r"\bзаписал[аи]\s+вас\b[^.!?]{0,100}"
            r"\b(?:понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|"
            r"воскресень\w*|\d{1,2}(?:-го)?)\b[^.!?]{0,50}"
            r"\b(?:\d{1,2}[.:]\d{2}|\d{1,2}\s+\d{2})\b",
            low,
            re.I,
        )
    )
    return booking_request and slot_selected and final_confirmation


def _is_past_to_service_record_correction_not_narrow_to(low: str) -> bool:
    """
    14948: отметки в сервисной книжке/CRM по уже пройденным ТО не отображаются —
    коррекция записи, не новая запись на регламентное ТО.
    """
    if not (low or "").strip():
        return False
    if crm_outbound_explicit_to_topic_present(low):
        return False
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    if _is_inbound_crm_next_regulatory_to_booking_intake(low):
        return False
    if re.search(r"\bзаписал[аи]\w*\s+(?:вас\s+)?на\b", low):
        return False
    if re.search(
        r"\b(?:запис\w*\s+на\s+(?:то|техобслуж|техническ|сервис|\d{1,2}\s+(?:июн|июл|август|"
        r"мая|март|апрел|феврал|январ|сентябр|октябр|ноябр|декабр)))",
        low,
        re.I,
    ):
        return False
    # 19618: обсуждение отметки в электронной книжке было лишь частью консультации;
    # затем клиент выбрал новый слот, и диспетчер явно подтвердил запись.
    if _has_new_service_booking_confirmed_after_history(low):
        return False
    opening_early = (low or "")[:1200]
    # 17071: новая запись на нулевое ТО + вопрос про подарок/сертификат — не коррекция CRM-отметок.
    if re.search(
        r"\bзапис(?:аться|ать)\s+на\s+нулев\w*\s+то\b",
        opening_early,
        re.I,
    ) or (
        re.search(r"\b(?:нужно|надо|мне\s+нужно)\s+запис", opening_early[:900], re.I)
        and re.search(r"\bнулев\w*\s+то\b", opening_early, re.I)
    ):
        return False

    past_to_done = bool(
        re.search(
            r"\bпрош(?:ел|ёл|ла|ли)\b[^.!?]{0,60}?\b(?:нулев\w*|перв\w*|втор\w*|трет\w*)\s+то\b",
            low,
            re.I,
        )
        or re.search(
            r"\bпрош(?:ел|ёл|ла|ли)\b\s+[^.!?]{0,50}?\bтехническ\w+\s+обслужив",
            low,
            re.I,
        )
        or (
            re.search(r"\bпрош(?:ел|ёл|ла|ли)\b", low, re.I)
            and re.search(r"\b(?:нулев\w*|перв\w*|втор\w*)\w*\b", low, re.I)
            and re.search(r"\bтехническ\w+\s+обслужив", low, re.I)
        )
        or (
            re.search(
                r"\b(?:нулев\w*|перв\w*|втор\w*)\s+то\b[^.!?]{0,80}?\b(?:прош|проходил|отдал|оплат|9000|отмет)",
                low,
                re.I,
            )
            and not re.search(
                r"\b(?:нулев\w*|перв\w*|втор\w*)\s+то\b[^.!?]{0,120}?\bв\s+подарок\b",
                low,
                re.I,
            )
        )
        or (
            re.search(r"\b(?:проходил\w*|прош(?:ел|ёл|ла|ли))\b", low, re.I)
            and re.search(r"\b(?:нулев\w*|перв\w*|втор\w*)\s+то\b", low, re.I)
        )
        # История обслуживания может описываться не «прошёл ТО», а «мы делали ТО:
        # первое, второе, третье». Это факт прошлых работ, если выше не найдено
        # намерение записаться на новое ТО.
        or (
            re.search(
                r"\b(?:делал[аи]?|сделал[аи]?|проводил[аи]?|проходил[аи]?)\b"
                r"[^.!?]{0,80}\bто\b",
                low,
                re.I,
            )
            and re.search(
                r"\b(?:нулев\w*|перв\w*|втор\w*|трет\w*|четвер\w*|четвёрт\w*)\b",
                low,
                re.I,
            )
        )
    )
    if not past_to_done:
        return False

    record_fix = bool(
        re.search(r"\b(?:не\s+)?(?:отмет\w*|отобраз\w*)\b", low, re.I)
        or (
            re.search(r"\b(?:поправ\w*|исправ\w*|обнов\w*)\b", low, re.I)
            and re.search(r"\b(?:клиентск\w*\s+служб|crm|истори|книжк|портал)\w*", low, re.I)
        )
        # Смысловой блок бумажной истории обслуживания: книжку восстановили,
        # требуется проставить в ней прошлые ТО или отсутствующие отметки.
        or (
            re.search(
                r"(?:\bсервисн\w*\s+книжк|\bкнижк\w*\s+сервисн)",
                low,
                re.I,
            )
            and re.search(
                r"\b(?:простав\w*|восстанов\w*|отмет\w*|"
                r"книжк\w*\s+(?:нет|нету|не\s+был))",
                low,
                re.I,
            )
        )
    )
    return record_fix


def _is_employment_recruitment_inquiry_not_narrow_to(low: str) -> bool:
    """
    16123: звонок по вакансии (объявление, HeadHunter, резюме, собеседование).
    «ТО-1/ТО-2» в рассказе об опыте на прошлом СТО — не запись на регламентное ТО.
    """
    head = (low or "")[:5000]
    if not head.strip():
        return False
    strong_employment = any(
        p in head
        for p in (
            "хэдхантер",
            "хедхантер",
            "headhunter",
            "hh.ru",
            "резюме",
            "ваканс",
            "трудоустрой",
            "собеседован",
            "отдел кадров",
            "технический директор",
        )
    )
    listing_job = any(
        p in head for p in ("по объявлению", "по вакансии", "на вакансию")
    ) and any(
        p in head
        for p in (
            "мастер-приёмщик",
            "мастер приёмщик",
            "мастер-приемщик",
            "мастер приемщик",
            "приёмщик",
            "приемщик",
            "кладовщик",
            "механик",
            "водител",
            "работу искал",
            "опыт работы",
        )
    )
    hh_otklik = ("отклик" in head) and any(
        p in head
        for p in ("хэдхантер", "хедхантер", "headhunter", "hh.ru", "объявлен")
    )
    interview_scheduling = (
        strong_employment or listing_job or "резюме" in head
    ) and any(
        p in head
        for p in (
            "подойти",
            "приходите",
            "приезжайте",
            "встреч",
            "пообщаемся",
            "понедельник",
            "вторник",
            "на следующей неделе",
            "утра",
            "10:00",
            "10.00",
        )
    ) and any(
        p in head
        for p in (
            "компани",
            "опыт работ",
            "работал",
            "работу",
            "мастер",
            "механик",
            "директор",
            "рузанов",
        )
    )
    if strong_employment and listing_job:
        return True
    if hh_otklik:
        return True
    if listing_job and ("отклик" in head or "резюме" in head or "опыт работ" in head):
        return True
    if interview_scheduling:
        return True
    return False


def _is_past_to_admin_followup_not_narrow_to(low: str) -> bool:
    """15058, 14948: пост-ТО документы или отметки в CRM — не запись на новое ТО."""
    return (
        _is_past_to_work_order_document_request_not_narrow_to(low)
        or _is_past_to_service_record_correction_not_narrow_to(low)
    )


def _is_own_parts_to_eligibility_consultation_not_narrow_to(low: str) -> bool:
    """
    Консультация об условиях ТО со своими материалами без расчёта и записи.

    Смысл задаётся сочетанием: свои расходники/запчасти + вопрос о допустимости.
    Реальное согласование слота или расчёт стоимости остаются узкой темой ТО.
    """
    head = (low or "")[:5000]
    if not head.strip():
        return False
    own_materials = bool(
        re.search(
            r"\bсо\s+сво(?:им|ими|ей)\s+"
            r"(?:расходник\w*|запчаст\w*|материал\w*|масл\w*|фильтр\w*)",
            head,
            re.I,
        )
        or re.search(
            r"\bсво[иёе]\s+(?:расходник\w*|запчаст\w*|материал\w*|масл\w*|фильтр\w*)",
            head,
            re.I,
        )
        # 19234: «можно ли привезти масло своё» — порядок «масло + своё».
        or re.search(
            r"\b(?:привезт\w*|привезу|привезите)\b[^.!?]{0,40}\bмасл\w*[^.!?]{0,20}\bсво[еёи]\b",
            head,
            re.I,
        )
        or re.search(r"\bмасл\w*\s+сво[еёи]\b", head, re.I)
    )
    if not own_materials:
        return False
    eligibility = bool(
        re.search(
            r"\b(?:можно|нельзя|разреш\w*|допуска\w*|получится|не\s+получится|"
            r"возможно|услови\w*)\b",
            head,
            re.I,
        )
    )
    if not eligibility:
        return False
    # 26378: если уже идет расчет/состав регламентного ТО, вопрос про свои
    # расходники — это часть TO-консультации, а не отдельный "прочий" сценарий.
    if _regulatory_to_price_or_composition_context(head):
        return False
    # Цена самого ТО / расчёт записи; сравнение цены масла у дилеров не делает звонок записью.
    price_discussion = bool(
        re.search(
            r"\b(?:стоимост\w*\s+(?:(?:прохожд|проведен|проведени)\w*\s+|"
            r"(?:перв|втор|трет|треть|четверт|пят|шест|седьм|восьм|девят|десят)\w*\s+)?то|"
            r"сколько\s+сто\w*\s+(?:то|техобслуж)|цен[аеуы]\s+то|"
            r"обойд[её]тся|рассчита\w*)\b",
            head,
            re.I,
        )
    )
    if price_discussion:
        return False
    # Новое согласование слота в этом звонке; «я уже записался 28-го» — не новая запись.
    new_slot_now = bool(
        re.search(
            r"\b(?:запишите|записывайте|записал[аи]\s+(?:вас\s+)?на|"
            r"записать\b|записываем(?:ся|\s+вас)?|записали\s+(?:нас|вас)|"
            r"давайте\s+запиш|давайте\s+тогда\s+на|давайте\s+на|подходит|устраивает|договорились|ожидаем)\b",
            head,
            re.I,
        )
        and (
            re.search(r"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|сред[ау]|четверг|пятниц|суббот|воскресень)\b", head)
            or re.search(r"\b(?:в|на)\s+\d{1,2}(?:(?:[:.]\d{2})|\s+\d{2})?\b", head)
            or re.search(
                r"\b(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\b",
                head,
                re.I,
            )
        )
    )
    # 25877: менеджер предлагает окна, клиент выбирает «попозже, 11:30», затем
    # подтверждение «накануне свяжемся / СМС направлю» — это фактическая запись.
    if not new_slot_now:
        has_slot_offer = bool(
            re.search(
                r"\b(?:могу\s+предложить|есть\s+время|пораньше|попозже)\b",
                head,
                re.I,
            )
        )
        has_selected_time = bool(
            re.search(
                r"\b(?:давайте|попозже|пораньше)\b[^.!?]{0,30}\b(?:\d{1,2}(?:[:.]\d{2})?)\b",
                head,
                re.I,
            )
            or re.search(r"\bк\s+\d{1,2}(?::\d{2})?\b", head, re.I)
        )
        has_booking_followup = bool(
            re.search(
                r"\b(?:накануне[^.!?]{0,40}свяж\w*|смс[^.!?]{0,40}(?:направл|отправл)|напомн\w*\s+о\s+визит)\b",
                head,
                re.I,
            )
        )
        new_slot_now = has_slot_offer and has_selected_time and has_booking_followup
    return not new_slot_now


def _is_to_official_dealer_eligibility_consultation_not_narrow_to(low: str) -> bool:
    """
    19255: справочный вопрос — можно ли пройти ТО у вас или только у официального дилера.

    Без обсуждения стоимости и даты/времени прохождения ТО → НЕ_ТО / other_work.
    """
    head = (low or "")[:4500]
    if not head.strip():
        return False
    eligibility_question = bool(
        re.search(
            r"\b(?:можно|нужно|надо|стоит\s+ли)\b[^.!?]{0,80}\b"
            r"(?:проходить|пройти|делать|сделать)\b[^.!?]{0,60}\bто\b",
            head,
            re.I,
        )
        or re.search(
            r"\bто\b[^.!?]{0,50}\b(?:можно|нужно|надо)\b[^.!?]{0,60}\b"
            r"(?:проходить|пройти|делать|сделать)\b",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:проходить|пройти)\s+то\b[^.!?]{0,80}\b"
            r"(?:официальн\w*\s+дилер|к\s+официальн)",
            head,
            re.I,
        )
        # 19605: «я не могу у вас ТО пройти?» / «у вас сейчас
        # не обслуживается Jetour?» — вопрос о допустимости обслуживания.
        or re.search(
            r"\b(?:я\s+)?(?:не\s+)?могу\s+(?:ли\s+)?(?:я\s+)?"
            r"у\s+вас\s+то\s+пройти\b",
            head,
            re.I,
        )
        or re.search(
            r"\bу\s+вас\b[^.!?]{0,55}\bне\s+обслужива\w*\b",
            head,
            re.I,
        )
    )
    if not eligibility_question:
        return False
    official_dealer_contrast = bool(
        re.search(
            r"\b(?:официальн\w*\s+дилер\w*|к\s+официальн\w*|у\s+официальн\w*)\b",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:у\s+вас|к\s+вам)\b[^.!?]{0,80}\b(?:или|либо)\b[^.!?]{0,60}\bдилер",
            head,
            re.I,
        )
    )
    if not official_dealer_contrast:
        return False
    price_discussion = bool(
        re.search(
            r"\b(?:стоимост\w*|сколько\s+сто\w*|цен[аеуы]|обойд[её]тся|рассчита\w*)\b",
            head,
            re.I,
        )
    )
    if price_discussion:
        return False
    booking_record_marker = False
    for m in re.finditer(r"\bзапис\w*\b", head, re.I):
        context = head[max(0, m.start() - 100) : m.end() + 100]
        # 19605: «телефоны могу продиктовать, запишите» — клиент записывает
        # номер дилера, это не запись автомобиля на ТО.
        if re.search(r"\b(?:телефон|номер|код\s+город)\w*\b", context, re.I) and re.search(
            r"\b(?:продикт\w*|запис\w*)\b",
            context,
            re.I,
        ):
            continue
        booking_record_marker = True
        break
    schedule_discussion = bool(
        booking_record_marker
        or re.search(
            r"\b(?:давайте\s+запиш|на\s+какую\s+дату|"
            r"на\s+какое\s+время|во\s+сколько)\b",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|сред[ау]|четверг|"
            r"пятниц|суббот|воскресень)\b",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:\d{1,2}[:.]\d{2}|\d{1,2}\s*часов?|\d{1,2}-го)\b",
            head,
            re.I,
        )
    )
    return not schedule_discussion


def _is_to_eligibility_or_guarantee_consultation_without_schedule(low: str) -> bool:
    """
    Консультация по допустимости/условиям прохождения ТО (гарантия, допоборудование),
    когда день и время визита не согласованы в этом звонке.
    """
    head = (low or "")[:7000]
    if not head.strip():
        return False
    if not (_regulatory_to_price_or_composition_context(head) or contains_any_to_marker_hit(head)[0]):
        return False
    eligibility = bool(
        re.search(
            r"\b(?:какие\s+мои\s+действия|как\s+поступить|можно\s+его\s+проходить|"
            r"можно\s+то\s+проходить|можно\s+проходить|не\s+сняли\s+с\s+гарантии|"
            r"снят\w*\s+с\s+гаранти|гарант\w*)\b",
            head,
            re.I,
        )
    )
    if not eligibility:
        return False
    # Явное согласование нового визита.
    has_schedule_or_booking = bool(
        re.search(r"\bзапис(?:али|ал[аи]?|ать|аться|ыва\w*|ываем)\b", head, re.I)
        or re.search(
            r"\b(?:понедельник|вторник|сред[ау]|четверг|пятниц[ау]?|суббот[ау]?|воскресень\w*|"
            r"сегодня|завтра|послезавтра)\b",
            head,
            re.I,
        )
        or re.search(r"\b(?:в|на)\s+\d{1,2}(?:[:.]\d{2})\b", head, re.I)
        or re.search(r"\b(?:приедете|подъедете|ожидаем|подходит\s+время)\b", head, re.I)
    )
    return not has_schedule_or_booking


def _is_fluids_and_repair_specs_consultation_without_booking(low: str) -> bool:
    """
    Консультация по отдельным работам и техжидкостям без оформления визита.

    «Я/можно я запишу» рядом с маркой масла, допуском или антифризом означает
    запись информации клиентом, а не запись автомобиля на сервис.
    """
    head = (low or "")[:7000]
    if not head.strip():
        return False
    # 22301: явный старт «хотел записаться на ТО» не должен переопределяться
    # в консультацию по жидкостям/отдельным работам.
    opening = head[:1800]
    if _is_inbound_opening_regulatory_to_booking(head) or re.search(
        r"\b(?:хотел[аи]?|хочу|нужно)\b[^.!?]{0,80}\bзапис(?:аться|ать)\b[^.!?]{0,40}\bна\s+то\b",
        opening,
        re.I,
    ):
        return False
    topics = sum(
        1
        for present in (
            "катализатор" in head,
            "антифриз" in head or "антрифриз" in head,
            "масло" in head,
        )
        if present
    )
    gearbox_context = bool(
        re.search(
            r"\b(?:вариатор|коробк\w*|трансмис\w*|кпп|акпп|cvt|сvт|поддон)\b",
            head,
            re.I,
        )
    )
    gearbox_specs = bool(
        re.search(
            r"\b(?:частич\w*|полн\w*\s+замен\w*|слив\w*|залив\w*|фильтр\w*|"
            r"проклад\w*|кольц\w*|литр\w*|допуск\w*|вязкост\w*|регламент\w*)\b",
            head,
            re.I,
        )
    )
    if topics < 2 and not (gearbox_context and gearbox_specs):
        return False
    consultation = bool(
        (
            re.search(
                r"\b(?:провер\w*|сколько\s+сто\w*|стоимост\w*|како[ей]\s+масл|"
                r"как\s+лучше|рекоменд\w*|надо\s+ли|не\s+надо)\b",
                head,
                re.I,
            )
            and re.search(
                r"\b(?:допуск|марка|вязкост|цвет|g\s*11|5\s*w\s*40|замен\w*|"
                r"фильтр\w*|проклад\w*|частич\w*|полн\w*)\b",
                head,
                re.I,
            )
        )
        or (
            gearbox_context
            and gearbox_specs
            and bool(
                re.search(
                    r"\b(?:по\s+регламент\w*|в\s+регламент\w*\s+не\s+входит|"
                    r"частич\w*|полн\w*|консульт\w*|как\s+лучше|надо\s+ли)\b",
                    head,
                    re.I,
                )
            )
        )
    )
    if not consultation:
        return False
    actual_slot = bool(
        re.search(
            r"\b(?:записали\s+вас|запишите\s+(?:меня|автомобил)|давайте\s+запиш|"
            r"на\s+какую\s+дату|на\s+какое\s+время)\b",
            head,
            re.I,
        )
        or (
            re.search(
                r"\b(?:понедельник|вторник|сред[ау]|четверг|пятниц[ау]?|"
                r"суббот[ау]?|воскресень\w*|завтра|послезавтра)\b",
                head,
                re.I,
            )
            and re.search(r"\b(?:запис|подходит|удобно|приед)\w*", head, re.I)
        )
    )
    # «Предварительная запись есть / заранее позвоню» — консультация по условиям записи,
    # а не оформление визита в этом звонке.
    deferred_planning_only = bool(
        re.search(
            r"\b(?:предварительн\w*\s+запис\w*|заранее\s+позвон\w*|лучше\s+дней?\s+за\s+\d+\s+"
            r"позвон\w*\s+запис\w*|потом\s+позвон\w*\s+запис\w*)\b",
            head,
            re.I,
        )
    )
    if deferred_planning_only and not actual_slot:
        return True
    return not actual_slot


def _is_component_presence_regulation_consultation_without_booking(low: str) -> bool:
    """Справка о наличии/регламентной замене отдельного узла без записи на обслуживание."""
    head = (low or "")[:5000]
    component_question = bool(
        re.search(r"\bесть\s+ли\b[^.!?]{0,90}\bтопливн\w*\s+фильтр", head, re.I)
        or re.search(r"\bтопливн\w*\s+фильтр\b[^.!?]{0,90}\b(?:есть|нет|меня)", head, re.I)
    )
    specification_lookup = bool(
        re.search(r"\bпо\s+комплектац", head, re.I)
        or re.search(r"\bпо\s+регламент\w*[^.!?]{0,100}\bне\s+меня", head, re.I)
        or re.search(r"\b(?:физически\s+нет|нет\s+выносн\w*\s+фильтр)", head, re.I)
    )
    if not (component_question and specification_lookup):
        return False
    actual_booking_or_quote = bool(
        re.search(
            r"\b(?:записал\w*\s+(?:вас|автомобил)|запишите\s+(?:меня|автомобил)|"
            r"давайте\s+запиш|стоимост\w*|цен[аеуы]|рубл)\b",
            head,
            re.I,
        )
        or (
            re.search(
                r"\b(?:понедельник|вторник|сред[ау]|четверг|пятниц[ау]?|"
                r"суббот[ау]?|воскресень\w*|завтра|послезавтра)\b",
                head,
                re.I,
            )
            and re.search(r"\b(?:запис|подходит|удобно|приед)\w*", head, re.I)
        )
    )
    return not actual_booking_or_quote


def _admin_salon_no_service_reception_callback(low: str) -> bool:
    """Админ без фактической приёмки СТО (заявка/перезвон) — lazy, без цикличного импорта."""
    try:
        from call_analytics.classify_by_transcript import (
            _is_sto_no_service_assistant_connection_low,
        )

        return bool(_is_sto_no_service_assistant_connection_low(low))
    except Exception:
        return False


def _is_inbound_to_history_crm_consultation_not_narrow_to(low: str) -> bool:
    """
    14346: вх. CRM-история регламентных ТО (сколько делали, какое по счёту, по базе)
    без записи на слот у дилера — узкий НЕ_ТО / work_type other_work.
    16976: «когда последний раз ТО», «что меняли», «на каком пробеге» — справка по прошлым работам.
    """
    if not (low or "").strip():
        return False
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    if _is_inbound_crm_next_regulatory_to_booking_intake(low):
        return False
    if re.search(r"\bзаписал[аи]\w*\s+(?:вас\s+)?на\b", low):
        return False

    opening = (low or "")[:2400]
    crm_lookup = bool(
        re.search(r"\bпо\s+баз[еи]\s+посмотр", opening, re.I)
        or re.search(r"\bсколько\s+(?:нам\s+)?то\s+делал", opening, re.I)
        or re.search(r"\bкакое\s+(?:оно\s+)?по\s+сч", opening, re.I)
        or re.search(
            r"\bкакое\s+.{0,50}?\b(?:по\s+количеств|по\s+номеру\s+то)\b",
            opening,
            re.I,
        )
        or re.search(
            r"\b(?:узнать|подскаж).{0,50}?\b(?:сколько|какое).{0,40}?\bто\b",
            opening,
            re.I,
        )
        # 16976: «хотела узнать … последний раз ТО», «что … меняли», «на каком пробеге».
        or re.search(r"\bпоследн\w*\s+раз\s+то\b", opening, re.I)
        or re.search(
            r"\b(?:узнать|подскаж|хотел\w*\s+узнать).{0,80}?\bпоследн\w*\s+раз\b.{0,40}\bто\b",
            opening,
            re.I,
        )
        or (
            re.search(r"\bна\s+каком\s+пробег", opening, re.I)
            and re.search(r"\bто\b", opening[:1200], re.I)
        )
        or (
            re.search(r"\b(?:что|какие).{0,40}?\bменял", opening, re.I)
            and re.search(r"\b(?:то\b|последн\w*\s+раз)", opening[:1200], re.I)
        )
        or (
            re.search(r"\bмасла?\s+менял", opening, re.I)
            and re.search(r"\b(?:то\b|узнать|подскаж)", opening[:1400], re.I)
        )
    )
    if not crm_lookup:
        return False

    history_ctx = past_to_reference_present(low) or bool(
        re.search(r"\b(?:заезжал\w*|обслуживал\w*)\b", low)
        and re.search(
            r"\b(?:седьм|восьм|девят|следующ|последн)\w*\s+то\b",
            low,
            re.I,
        )
        or re.search(r"\bпоследн\w*\s+раз\s+то\b", low, re.I)
        or re.search(r"\bкрайний\s+раз\b", low, re.I)
        or (
            re.search(r"\bгод\s+назад\b", low, re.I)
            and re.search(r"\bзамен\w*\s+масл", low, re.I)
        )
        or re.search(r"\bв\s+прошлом\s+месяц", low, re.I)
        or re.search(r"\bбыл[аи]?\s+буквально\b", low, re.I)
    )
    if not history_ctx:
        return False

    has_owner_identification_step = bool(
        re.search(r"\bна\s+кого\s+оформлен\w*\b", low, re.I)
        or re.search(r"\bфамили\w+\s+(?:собственник\w*|владел\w*)\b", low, re.I)
    )
    has_slot_choice_dialog = bool(
        re.search(r"\b(?:если\s+на\s+\d{1,2}|на\s+\d{1,2}(?:-?ое)?\s+числ\w*)\b", low, re.I)
        or re.search(r"\b(?:утренн\w*|ближе\s+к\s+обеду|время\s+утренн\w*)\b", low, re.I)
        or re.search(r"\b(?:8[:.\s]30|9[:.\s]00|10[:.\s]00|11[:.\s]00)\b", low, re.I)
        or re.search(r"\b(?:в\s+десять|на\s+девятнадцат\w*)\b", low, re.I)
    )
    if contains_any_to_marker_hit(low)[0] and has_owner_identification_step and has_slot_choice_dialog:
        return False

    # 22457: в разговоре могут обсуждать историю ТО, но при этом согласовывать
    # новый слот (день/время + финальное подтверждение записи).
    has_active_slot_negotiation = bool(
        re.search(r"\b(?:ведет(?:ся)?|идет)\s+запис\w*\b", low, re.I)
        and (
            re.search(
                r"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|сред[ау]|четверг|"
                r"пятниц|суббот|воскресень|август|сентябр|октябр|ноябр|декабр)\b",
                low,
                re.I,
            )
            or re.search(r"\b(?:на|в)\s+\d{1,2}(?:[:.]\d{2}|\s+\d{2})\b", low, re.I)
            or re.search(r"\b(?:утренн\w*|ближе\s+к\s+обеду|крайнее\s+время)\b", low, re.I)
        )
        and re.search(
            r"\b(?:записал[аи]?\s+вас|(?:мы\s+с\s+вами\s+)?записал(?:ись|ся|ась)|"
            r"накануне[^.!?]{0,40}(?:позвон|напомн))\b",
            low,
            re.I,
        )
    )
    if has_active_slot_negotiation:
        return False

    tail = (low or "")[-1400:]
    if re.search(r"\bперезвон\w*\b", tail):
        return True
    if re.search(
        r"\bзапис\w*\s+(?:в\s+|на\s+)(?:самар|друг\w+\s+(?:город|дилер)|там|сторон)",
        opening,
        re.I,
    ):
        return True
    return not bool(
        re.search(
            r"\b(?:запис\w*\s+на|на\s+(?:завтра|понедельник|вторник|среду|"
            r"четверг|пятниц|суббот|воскресенье|\d{1,2}\s+(?:июн|июл|август|"
            r"мая|март|апрел|феврал|январ|сентябр|октябр|ноябр|декабр)))",
            low,
            re.I,
        )
    )


def _is_deferred_repair_after_past_to_visit_intake(low: str) -> bool:
    """
    Прошедшее ТО/техобслуживание; запись на доустановку/замену по дефекту или ожидающим запчастям —
    не новое регламентное ТО (10769: свечи; 13781: сирена сигнализации).
    """
    early = (low or "")[:2500]
    if not early.strip():
        return False
    oil_deferred_explicit = bool(
        re.search(
            r"\b(?:на\s+техническ\w+\s+обслужив\w*|на\s+то)\b[^.!?]{0,120}\b(?:не\s+смогл\w*\s+замен|"
            r"не\s+было\b[^.!?]{0,40}\bмасл\w*)",
            low[:4500],
            re.I,
        )
        and re.search(
            r"\bмасл\w*[^.!?]{0,50}\b(?:поступил\w*|появил\w*|в\s+наличии)\b",
            low[:4500],
            re.I,
        )
        and re.search(
            r"\b(?:приглас\w*|готов\w*\s+вас\s+приглас\w*|на\s+данн\w+\s+работ\w*)\b",
            low[:4500],
            re.I,
        )
    )
    if _regulatory_to_intent_takes_priority(low):
        has_slot_confirmation = bool(
            re.search(
                r"\b(?:записывать\s+вас|запиш(?:у|ем)\s+вас|подъедете|приедете)\b",
                low,
                re.I,
            )
            or re.search(
                r"\b(?:на\s+\d{1,2}(?::\d{2})|"
                r"(?:понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|воскресень\w*))\b",
                low,
                re.I,
            )
        )
        if has_slot_confirmation and not oil_deferred_explicit:
            return False
    if _crm_outbound_new_to_application_opening(low):
        has_new_slot_negotiation = bool(
            re.search(
                r"\b(?:записывать\s+вас|записываю\s+вас|запиш(?:у|ем)\s+вас|"
                r"на\s+\d{1,2}(?::\d{2})|приедете|подъедете)\b",
                low,
                re.I,
            )
            or re.search(
                r"\b(?:понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|воскресень\w*)\b",
                early,
                re.I,
            )
        )
        if has_new_slot_negotiation:
            return False
    if _is_inbound_crm_next_regulatory_to_booking_intake(low):
        return False
    past_to_ctx = _past_to_or_technical_service_visit_context(early)
    parts_arrived = bool(
        re.search(r"\bсвеч\w*", early, re.I)
        and any(
            p in early
            for p in (
                "поступил",
                "пришл",
                "не было",
                "на тот момент",
            )
        )
    ) or ("свеч" in low and "поступил" in low)
    spark_deferred_explicit = bool(
        "на замену свеч" in low
        or re.search(
            r"\bсвеч\w*.{0,50}\b(?:замен\w*|поступил|приглаш)",
            low[:4000],
            re.I,
        )
        or re.search(
            r"\b(?:не\s+было|на\s+тот\s+момент)[^.!?]{0,50}\bсвеч",
            low[:4000],
            re.I,
        )
        or re.search(
            r"\bсвеч\w*[^.!?]{0,50}\b(?:не\s+было|на\s+тот\s+момент)",
            low[:4000],
            re.I,
        )
    )
    spark_deferred = bool(
        parts_arrived
        and (
            spark_deferred_explicit
            or re.search(
                r"\b(?:замен\w*|ожида\w*|приглаш\w*|запис\w*).{0,45}\bсвеч",
                low[:4000],
                re.I,
            )
        )
    ) or bool(past_to_ctx and spark_deferred_explicit and parts_arrived)
    defect_parts_deferred = bool(
        past_to_ctx
        and re.search(r"\bне\s+работает\b", early, re.I)
        and (
            re.search(r"\bодобрен\w*\s+замен", early, re.I)
            or re.search(r"\bсигнализац", early, re.I)
        )
        and (
            re.search(r"\bожида\w*\s+сирен", low[:4500], re.I)
            or re.search(r"\bзапчаст\w*[^.!?]{0,50}поступ", low[:4500], re.I)
            or re.search(r"\bсирен", low[:4500], re.I)
        )
    )
    if not (spark_deferred or defect_parts_deferred or oil_deferred_explicit):
        return False
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    new_to_intake = bool(
        re.search(
            r"\bзапис(?:аться|ать)\s+на\s+(?:то|техническ|техобслуж)",
            early[:900],
            re.I,
        )
        or re.search(
            r"\b(?:хотел[аи]|хочу)[^.!?]{0,100}\bзапис\w*\s+на\s+(?:то|техническ|техобслуж)",
            early[:900],
            re.I,
        )
    ) and (
        not re.search(r"\b(?:замен|свеч|запчаст|сирен|сигнализац)", early[:900], re.I)
        or _is_inbound_crm_next_regulatory_to_booking_intake(low)
    )
    return not new_to_intake


def _is_quality_check_visit_intake(low: str) -> bool:
    """
    15513: вызов/перенос слота проверки качества (письмо дилера, ПК) — не регламентное ТО.
    16969: претензия к качеству уже выполненного ТО (запах/фильтры) + повторный осмотр.
    """
    head = (low or "")[:5000]
    if not head.strip():
        return False
    if _is_post_completed_to_quality_complaint_intake(low):
        return True
    has_qc_phrase = bool(re.search(r"\bпроверк\w*\s+качеств", head, re.I))
    if not has_qc_phrase:
        has_qc_phrase = bool(
            re.search(r"\bпроверк\w*\b", head, re.I)
            and re.search(r"\bкачеств\w*\b", head, re.I)
            and any(
                p in head
                for p in (
                    "письм",
                    "переназнач",
                    "безвозмездн",
                    "устранен",
                    "недостатк",
                    "явиться",
                    "клиентск",
                    "телеграм",
                )
            )
        )
    if not has_qc_phrase:
        return False
    # Внутренний QC по заказ-нарядам (14202) — не клиентский визит ПК.
    if any(
        p in head
        for p in (
            "заказнаряд",
            "заказ наряд",
            "моторному отсеку",
            "коррозия по всему",
        )
    ) and not re.search(r"\bписьм", head):
        return False
    return True


def _is_post_completed_to_quality_complaint_intake(low: str) -> bool:
    """
    16969: «делали ТО первое» + «после ТО» + некачественные расходники/вонь в салоне —
    претензия по уже выполненному ТО, не новая запись на регламент.
    """
    head = (low or "")[:4500]
    if not head.strip():
        return False
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    past_done = bool(
        re.search(
            r"\bделал[аи]?\s+(?:то\b|(?:перв|втор|трет|четвер|четвёрт|нулев)\w*\s+то\b)",
            head[:2200],
            re.I,
        )
        or re.search(r"\bпосле\s+то\b", head[:2200], re.I)
        or (
            re.search(r"\bделал[аи]?\b", head[:1800], re.I)
            and re.search(
                r"\b(?:перв|втор|трет|четвер|четвёрт|нулев)\w*\s+то\b",
                head[:1800],
                re.I,
            )
        )
    )
    if not past_done:
        return False
    return any(
        p in head
        for p in (
            "некачеств",
            "не устраивает",
            "вонь",
            "запах",
            "газы",
        )
    )


def _is_inbound_warranty_defect_consultation_not_scheduled_to(low: str) -> bool:
    """
    12922: после ТО нашли дефект; консультация — гарантийный случай или нет; слот на замену уже есть.
    «У меня ТО было, нашли неисправности» + «гарантийный случай»; «проходила третье ТО» — история.
    """
    head = (low or "")[:4500]
    if not head.strip() or not any(p in head for p in ("гарант",)):
        return False
    if _regulatory_to_intent_takes_priority(low):
        return False
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    defect = any(
        p in head
        for p in (
            "неисправност",
            "люфт",
            "дефект",
            "подкапыва",
            "подтек",
            "не работает",
            "стук",
            "качать колес",
            "качать колёс",
        )
    )
    past_to = bool(
        re.search(r"\b(?:у меня|мне)\s+то\s+было\b", head[:1800], re.I)
        or re.search(r"\bто\s+было\b[^.!?]{0,80}\bнеисправност", head[:1800], re.I)
        or re.search(
            r"\b(?:проходил[аи]?|были|делал[аи]?)\b[^.!?]{0,80}\b(?:перв|втор|трет|четвер|четвёрт)\w*\s+то\b",
            head,
            re.I,
        )
    )
    warranty_question = any(
        p in head
        for p in (
            "гарантийный случай",
            "гарантийным случа",
            "гарантийный ремонт",
            "гарантийному ремонту",
            "гарантийного ремонта",
            "по гарантии",
            "на гарантии",
            "не входит в гарант",
            "входит в гарант",
        )
    )
    existing_repair_slot = bool(
        re.search(r"\bзаписан[аы]?\s+на\s+[^.!?]{0,70}\bзамен", head, re.I)
    )
    if past_to and defect and warranty_question:
        return True
    if existing_repair_slot and warranty_question and defect:
        return True
    return False


def _is_existing_to_warranty_repair_routing_not_new_booking(low: str) -> bool:
    """
    23200: клиент уже записан на ТО и уточняет маршрутизацию/логистику по гарантийному ремонту.
    Это НЕ новая запись на ТО.
    """
    head = (low or "")[:4200]
    if not head.strip():
        return False
    has_existing_slot = bool(
        re.search(
            r"\b(?:я\s+к\s+вам\s+)?записан[аы]?\b[^.!?]{0,140}\b(?:на|в)\s+"
            r"(?:понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|воскресень\w*|\d{1,2}(?::\d{2})?)\b",
            head,
            re.I,
        )
        or re.search(r"\b(?:запись|запис\w*)\b[^.!?]{0,70}\b(?:уже\s+есть|подтверд\w*)\b", head, re.I)
    )
    if not has_existing_slot:
        return False
    has_warranty_topic = bool(
        re.search(
            r"\b(?:гарантийн\w+\s+ремонт\w*|по\s+гаранти\w*|инженер[ау]?\s+(?:по\s+)?гаранти\w*)\b",
            head,
            re.I,
        )
    )
    if not has_warranty_topic:
        return False
    has_routing_or_dropoff_question = bool(
        re.search(r"\b(?:как\s+связат\w*|перевед\w*\s+на\s+диспетчер\w*)\b", head, re.I)
        or re.search(
            r"\b(?:пригнать|оставить)\b[^.!?]{0,50}\b(?:машин\w*|автомобил\w*|ключ\w*)\b",
            head,
            re.I,
        )
    )
    if not has_routing_or_dropoff_question:
        return False
    has_new_reg_to_intent = bool(
        re.search(
            r"\b(?:хочу|хотел[аи]?|нужно|надо)\b[^.!?]{0,80}\bзапис(?:аться|ать)\b[^.!?]{0,40}\b(?:на\s+то|то\b)\b",
            head,
            re.I,
        )
    )
    return not has_new_reg_to_intent


def _is_warranty_decision_document_followup(low: str) -> bool:
    """
    Получение решения/письменного отказа по уже рассмотренному гарантийному случаю.

    Внутреннее упоминание кузовного отдела не меняет основную тему на body_shop.
    """
    head = (low or "")[:5000]
    if not head.strip():
        return False
    prior_warranty_case = bool(
        re.search(r"\b(?:по\s+поводу\s+)?гарантийн\w*\s+случа", head, re.I)
        or re.search(r"\bприезжал[аи]?\b[^.!?]{0,100}\bгарант", head, re.I)
    )
    refusal_decision = bool(
        re.search(r"\b(?:решени\w*\s+об\s+отказ|отказал\w*|отказ\w*\s+от\s+гарант)", head, re.I)
    )
    document_request = bool(
        re.search(
            r"\b(?:забрать|получить|выдать|бумажн\w*\s+подтверждени|"
            r"письменн\w*\s+(?:решени|отказ)|причин\w*\s+отказ)",
            head,
            re.I,
        )
    )
    return prior_warranty_case and refusal_decision and document_request


def _is_warranty_parts_arrived_replacement_coordination(low: str) -> bool:
    """Гарантийная запчасть (поступила/ожидается) и координация по ней — гарантийные работы."""
    head = (low or "")[:4500]
    parts_arrived_under_warranty = bool(
        re.search(
            r"\bзапчаст\w*[^.!?]{0,80}\b(?:поступ\w*|пришл\w*|зашл\w*)"
            r"[^.!?]{0,60}\bпо\s+гаранти",
            head,
            re.I,
        )
        or re.search(
            r"\bпо\s+гаранти\w*[^.!?]{0,80}\bзапчаст\w*"
            r"[^.!?]{0,60}\b(?:поступ\w*|пришл\w*|зашл\w*)",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:поступ\w*|пришл\w*|зашл\w*)[^.!?]{0,50}\bзапчаст\w*"
            r"[^.!?]{0,60}\bпо\s+гаранти",
            head,
            re.I,
        )
    )
    parts_expected_under_warranty = bool(
        re.search(
            r"\bзапчаст\w*[^.!?]{0,90}\b(?:должн\w*\s+прийти|заказан\w*|срок\w*\s+поставк\w*|"
            r"когда\s+прид\w*|не\s+пришл\w*)",
            head,
            re.I,
        )
        and re.search(r"\bпо\s+гаранти\w*\b", head, re.I)
    )
    replacement_coordination = bool(
        re.search(r"\b(?:замен\w*|согласова\w*\s+день|согласова\w*\s+врем)\b", head, re.I)
    )
    delivery_status_inquiry = bool(
        re.search(
            r"\b(?:пришла[-\s]?не\s+пришла|пришла\s+или\s+нет|можно\s+узнать|"
            r"когда\s+прид\w*|по\s+срок\w*|срок\w*\s+поставк\w*)\b",
            head,
            re.I,
        )
    )
    has_warranty_parts_supply_context = parts_arrived_under_warranty or parts_expected_under_warranty
    return has_warranty_parts_supply_context and (replacement_coordination or delivery_status_inquiry)


def _is_inbound_post_to_visit_signal_horn_defect_intake(low: str) -> bool:
    """
    16199: после недавнего ТО сигнал/колокол/сигнализация не работает — прочие работы, не warranty.
    """
    head = (low or "")[:4500]
    if not head.strip():
        return False
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    past_to = bool(
        re.search(
            r"\b(?:проводил[аи]?|проходил[аи]?|делал[аи]?|были|заезжал[аи]?)\b"
            r"[^.!?]{0,100}\b(?:то\s*-\s*[0-9]|(?:перв|втор|трет|четвер|четвёрт)\w*\s+то)\b",
            head[:1800],
            re.I,
        )
        or re.search(
            r"\b(?:на\s+)?(?:этой|той)\s+недел\w*[^.!?]{0,80}\b(?:то|проводил|проходил)",
            head[:1800],
            re.I,
        )
    )
    signal_defect = bool(
        re.search(r"\bсигнал\w*[^.!?]{0,40}\bне\s+работает\b", head, re.I)
        or re.search(r"\b(?:звуков\w*|штатн\w*)\s+сигнал", head, re.I)
        or re.search(r"\bколокол", head, re.I)
        or re.search(r"\bсигнализац\w*[^.!?]{0,40}\bне\s+сигнал", head, re.I)
        or re.search(r"\bне\s+сигнализир", head, re.I)
    )
    return past_to and signal_defect


def _is_inbound_post_to_visit_battery_check_diagnostic_intake(low: str) -> bool:
    """
    28889: после недавнего ТО клиент обращается с дефектом запуска/АКБ/чека
    и просит проверить причину — это диагностика, не регламентное ТО.
    """
    head = (low or "")[:5000]
    if not head.strip():
        return False
    past_to = bool(
        re.search(
            r"\b(?:недавно|после|сегодня|вчера)\b[^.!?]{0,140}\b(?:нулев|перв|втор|трет|четвер|четвёрт|пят|шест)\w*\s+то\b",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:проводил[аи]?|проходил[аи]?|делал[аи]?|сделал[аи]?|заезжал[аи]?)\b"
            r"[^.!?]{0,120}\b(?:то\s*-\s*[0-9]|(?:нулев|перв|втор|трет|четвер|четвёрт|пят|шест)\w*\s+то)\b",
            head,
            re.I,
        )
    )
    # Если нет явного контекста недавнего ТО — не перебиваем штатную ветку регламентного ТО.
    if _is_inbound_opening_regulatory_to_booking(low) and not past_to:
        return False
    if not past_to:
        return False
    defect = bool(
        re.search(r"\b(?:не\s+стал\w*\s+завод\w*|не\s+завод\w*|не\s+зав[её]л\w*)\b", head, re.I)
        or re.search(r"\bаккумулятор\w*[^.!?]{0,60}\b(?:разряд\w*|сел\w*|севш\w*)\b", head, re.I)
        or re.search(r"\b(?:разряд\w*|сел\w*)\b[^.!?]{0,40}\bаккумулятор\w*\b", head, re.I)
        or re.search(r"\b(?:лампочк\w*[^.!?]{0,30}\b)?чек(?:\s*engine|\s*ин|\s*in)?\b", head, re.I)
    )
    if not defect:
        return False
    needs_diagnostic = bool(
        re.search(r"\b(?:провер\w*|посмотр\w*|диагност\w*|почему)\b", head, re.I)
        and re.search(r"\bзапис\w*|запиш\w*|запись\b", head, re.I)
    )
    return needs_diagnostic


def _regulatory_to_intent_takes_priority(low: str) -> bool:
    """
    Намерение регламентного ТО (N-е ТО сделать/пройти/цена/запись) —
    приоритетнее гарантии, диагностики и прочих сопутствующих тем в том же звонке (18298).
    Не считать прошлый визит («нулевое то проходила») живым намерением ТО (13517).
    """
    low = low or ""
    if not low.strip():
        return False
    if _is_inbound_opening_regulatory_to_booking(low):
        return True
    head = low[:2800]
    if re.search(
        r"\b(?:нулев|перв|втор|трет|четвер|четвёрт|пят|шест)\w*\s+то\s+"
        r"(?:запис|надо|нужно|пройти|сделать|сдела)\b",
        head,
        re.I,
    ):
        return True
    if re.search(r"\bто\s+один\s+(?:сделать|пройти|запис)\b", head, re.I):
        return True
    if re.search(
        r"\bзапис(?:аться|ь)\s+на\s+(?:нулев|перв|втор|трет|четвер|четвёрт|пят|шест)\w*\s+то\b",
        head,
        re.I,
    ):
        return True
    if re.search(r"\bна\s+то\s+запиш(?:у|ем|ите)\b", head, re.I):
        return True
    # 21150: «хотел записать ... на ТО» (строго глагол «записать», без «записан»).
    if re.search(
        r"\b(?:хотел[аи]?|хочу)\b[^.!?]{0,80}\bзаписать\b"
        r"[^.!?]{0,100}\b(?:на\s+)?(?:то\b|техническ\w+\s+обслуживан\w*|техобслуж\w*)\b",
        head,
        re.I,
    ):
        return True
    # 20032: «проводили ТО-105, сейчас будет ТО 120» + выбор даты/времени.
    if _mileage_to_interval_code_present(head) and re.search(
        r"\b(?:сейчас|теперь|далее)\b[^.!?]{0,50}\b(?:будет|подход\w*)\b"
        r"[^.!?]{0,50}\bто\s*[-]?\s*\d{2,3}(?:\s+000)?\b",
        head,
        re.I,
    ):
        if re.search(
            r"\b(?:запис(?:ались|ать|аться|ыва)|запишу|запись|время|на\s+\d{1,2}[:.]\d{2}|"
            r"(?:июл|август|сентябр|октябр|ноябр|декабр|январ|феврал|март|апрел|ма[йя]))\b",
            head,
            re.I,
        ):
            return True
    # Расчёт/стоимость N-го ТО — только если это не история «N-е то проходил(а)».
    if re.search(r"\b(?:расч[её]т|стоимост|узнать\s+расч|по\s+стоимости|сколько\s+стоит)\b", head, re.I) and (
        re.search(
            r"\b(?:нулев|перв|втор|трет|четвер|четвёрт)\w*\s+то\b",
            head,
            re.I,
        )
        or re.search(r"\bто\s+один\b|\bтодин\b", head, re.I)
    ):
        if re.search(
            r"\b(?:нулев|перв|втор|трет|четвер|четвёрт)\w*\s+то\b[^.!?]{0,50}\bпроходил",
            head,
            re.I,
        ) or re.search(
            r"\bпроходил\w*[^.!?]{0,50}\b(?:нулев|перв|втор|трет|четвер|четвёрт)\w*\s+то\b",
            head,
            re.I,
        ):
            # Есть и живой запрос цены на другое N-е ТО?
            if not re.search(
                r"\b(?:сколько|стоимост|расч[её]т).{0,60}\b(?:перв|втор|трет|четвер|нулев)\w*\s+то\b",
                head,
                re.I,
            ) and not re.search(r"\bто\s+один\b|\bтодин\b", head, re.I):
                return False
        return True
    return False


def _is_inbound_warranty_repair_booking_intake(low: str) -> bool:
    """
    15878: «записаться на гарантийный ремонт» + дефект — warranty, не диагностика.
    Не перекрывать явное намерение регламентного ТО (18298: первое ТО + «по гарантии» в STT).
    """
    head = (low or "")[:4500]
    if not head.strip() or not any(p in head for p in ("гарант",)):
        return False
    warranty_body_defect_override = bool(
        re.search(r"\b(?:гарантийн\w+\s+машин\w*|по\s+гаранти\w*)\b", head, re.I)
        and re.search(r"\bинженер[ау]?\s+(?:по\s+)?гаранти\w*\b", head, re.I)
        and re.search(
            r"\b(?:вздул\w*|вдулас\w*|пленк\w*|стойк\w*|двер\w*)\b",
            head,
            re.I,
        )
        and re.search(r"\bзапис(?:аться|ать|ыва\w*)\b", head, re.I)
    )
    if _regulatory_to_intent_takes_priority(low) and not warranty_body_defect_override:
        return False
    if _is_inbound_opening_regulatory_to_booking(low) and not warranty_body_defect_override:
        return False
    warranty_intent = any(
        p in head
        for p in (
            "гарантийный ремонт",
            "гарантийному ремонту",
            "гарантийного ремонта",
            "гарантийной машине",
            "гарантийная машина",
            "записаться на гарантий",
            "записаться по гарант",
            "записаться у вас по гарант",
            "инженер по гарантии",
            "инженера гарантии",
            "гарантийным вопрос",
            "гарантийный случай",
            "по гарантии",
        )
    )
    if not warranty_intent:
        return False
    if re.search(r"\bзапис(?:аться|ать|ыва\w*)\b", head, re.I):
        return True
    # 21212: «нужен инженер по гарантии» + согласование даты/времени визита
    # (даже если слово «запись» звучит в конце диалога).
    has_warranty_engineer = bool(
        re.search(r"\bинженер\s+по\s+гаранти\w*\b", head, re.I)
        or re.search(r"\bинженер[ау]?\s+гаранти\w*\b", head, re.I)
    )
    has_visit_scheduling = bool(
        re.search(r"\bможем\s+вам\s+предложить\b", head, re.I)
        and re.search(r"\bприехать\b", head, re.I)
        and (
            re.search(
                r"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|сред[ау]|четверг|"
                r"пятниц|суббот|воскресень)\b",
                head,
                re.I,
            )
            or re.search(r"\b(?:в|на)\s+\d{1,2}(?:[:.]\d{2})?\b", head, re.I)
            or re.search(
                r"\b(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\b",
                head,
                re.I,
            )
        )
    )
    if has_warranty_engineer and has_visit_scheduling:
        return True
    return any(
        p in head
        for p in (
            "стук",
            "шум",
            "люфт",
            "неисправност",
            "дефект",
            "вздул",
            "вдулас",
            "пленк",
            "подкапыва",
            "подтек",
            "не работает",
        )
    )


def _crm_outbound_new_to_application_opening(low: str) -> bool:
    """
    17167: CRM-исходящий «пришла заявка … на запись на техническое обслуживание» —
    новая запись на ТО, не подтверждение уже существующего слота.
    """
    head = (low or "")[:1400]
    if not any(
        p in head
        for p in (
            "пришла заявка",
            "пришла заявк",
            "поступила заявка",
            "заявка поступила",
            "получили заявку",
            "заявку получили",
        )
    ):
        return False
    return (
        "техническое обслуживание" in head
        or "техобслуживание" in head
        or "запись на техническ" in head
        or "на запись на техническ" in head
        or bool(re.search(r"\bна\s+то\b", head))
    )


def _has_existing_appointment_clarification(low: str) -> bool:
    """
    Слот уже назначен; звонок про подтверждение или дополнение к записи — не новая запись на регламентное ТО.
    """
    if _is_inbound_to_late_arrival_notice(low):
        return True
    # 9409: «записались на 2 июня» в конце новой записи — не «слот уже был».
    _END_BOOKING_OUTCOME_PATTERN = (
        r"\bзаписал[аиоы]?\w*\s+(?:на\s+)?(?:трет|четвер|перв|втор|\d)"
    )
    patterns = (
        r"записан[аы]?\s+на\s+завтра",
        r"на\s+завтра\s+записан[аы]?",
        r"на\s+завтра\s+записаны",
        r"записаны\s+на\s+завтра",
        # STT: «завтра записаны к нам на 15:30 на ТО» (9597).
        r"\bзавтра\s+записан[аы]?\s+к\s+нам\b",
        r"\bзаписан[аы]?\s+к\s+нам\s+на\b",
        # 12032: голое «вы записаны» в STT про каско/адрес — не «слот уже есть».
        r"\bвы\s+записаны\s+(?:к\s+нам|на\s+(?:то\b|завтра|техническ|\d)|\d)",
        r"(?:^|\s)вы\s+записан(?:\s|$)",
        r"на\s+\d{1,2}[\s:.]*\d{2}\s+вы\s+записаны",
        r"на\s+\d{1,2}[\s:.]*\d{2}\s+записаны",
        # прошедшее от линии: «вы записывали на завтра…» — напоминание перед визитом
        r"\bвы\s+записывали\b",
        r"\bзаписывали\s+автомобиль\b",
        # Клиент перезванивает: уже есть слот на регламентное ТО («я к вам записана на ТО»),
        # дальше — перенос / уточнение / отмена, не новая запись на ТО в узком слое (НЕ_ТО).
        r"\bзаписан[аы]?\s+на\s+то\b",
        # STT: «в 11:30 записано ТО» (8924).
        r"\bзаписан[аоы]?\s+то\b",
        # 19234: «я на ТО записался 28-го в 15:00» — совершенный вид, слот уже есть.
        r"\b(?:я|мы)\s+на\s+то\s+записал(?:ся|ись)\b",
        r"\bна\s+то\s+записал(?:ся|ись)\b",
        r"\bзаписал(?:ся|ись)\s+на\s+то\b",
        # 19303: STT «записавался» ≈ «записывался»; «только сейчас записавался на ТО».
        r"\bзаписавал(?:ся|ись)\s+на\s+то\b",
        r"\bзаписавал(?:ся|ись)\b[^.!?]{0,100}\bна\s+то\b",
        r"\bтолько\s+(?:сейчас|что)\s+запис(?:ал|ывал|авал)(?:ся|ись)\b[^.!?]{0,80}\b(?:на\s+)?то\b",
        r"\bтолько\s+(?:сейчас|что)\s+запис(?:ал|ывал|авал)(?:ся|ись)\b[^.!?]{0,80}\bтехническ\w*\s+обслуживан",
        # STT: «я завтра на ТО записана» (порядок слов, 8567).
        r"\b(?:я|мы)\s+завтра\s+на\s+то\s+записан[аы]?\b",
        # STT: «я записан завтра на ТО» (10549) — порядок слов, не «завтра на то записан».
        r"\b(?:я|мы)\s+записан[аы]?\s+завтра\s+на\s+то\b",
        r"\bна\s+то\s+записан[аы]?\b",
        r"\bперезапис(?:аться|ать|ыва|ывал[аи]?)?\b",
        r"\b(?:я|мы)\s+к\s+вам\s+записан",
        r"\bк\s+вам\s+записан[аы]?\s+на\s+то\b",
        # «Алло… я завтра записан» — слот уже есть, часто перенос/смена времени (7843).
        r"\b(?:я|мы)\s+завтра\s+записан[аы]?\b",
        r"\b(?:я|мы)\s+послезавтра\s+записан[аы]?\b",
        r"\b(?:я|мы)\s+сегодня\s+записан[аы]?\b",
        r"\b(?:я|мы)\s+уже\s+записан[аы]?\b",
        # Уже есть слот: «я записан на 18-е в кузовной», уточнение времени (8375).
        r"\b(?:я|мы)\s+записан[аы]?\s+на\s+\d",
        r"\bзаписан[аы]?\s+на\s+\d{1,2}",
        r"\bслетел[аи]?\s+запис",
        r"\bслетели\s+запис",
        r"\bуточняю\s+просто\s+время",
        r"\bуточнял\s+время",
        r"\bпо\s+памяти\s+.{0,40}\s+запис",
        # 10441: «записывались на ТО на 2 ч» — слот уже есть, опоздание/задержка.
        r"\bзаписывал(?:ся|ись|и)\s+на\s+то\b",
        # 10880: STT «записывался на тридцатого, на тридцатое число на ТО» — дата между глаголом и «на ТО».
        r"\bзаписывал(?:ся|ись|и)\b[^.!?]{0,100}\bна\s+то\b",
        # 8963: «я на ТО записывался» + перезвон после звонка жене.
        r"\b(?:я|мы)\s+.{0,55}(?:на\s+)?то\s+записывал",
        # 9008: «записана на техническое обслуживание на среду» — слот уже есть, уточнение/доп. работы.
        r"\bзаписан[аы]?\s+на\s+техническ",
        r"\bзаписывал[аио]?\s+на\s+техническ",
        # 13945: STT «наТ нулевое ТО», «на двадцатое записывался», «к нам вы записаны».
        r"\bзаписывал(?:ся|ись|и)\b[^.!?]{0,90}(?:нат\s+)?(?:нулев|нолев)\w*\s+то\b",
        r"\bк\s+нам\s+вы\s+записан",
        r"\bна\s+(?:двадцат|двадц\w*)[^.!?]{0,60}записывал",
    )
    # 17167: CRM «пришла заявка … на техническое» + «перезаписаться» (условный перенос
    # слота, который только создают) — не «слот уже был».
    if _crm_outbound_new_to_application_opening(low):
        patterns = tuple(p for p in patterns if "перезапис" not in p)
    # Итог записи в конце приёмки («записали на 31 мая») — не «слот уже был» (9409, 9651).
    return any(
        re.search(p, low)
        for p in patterns
        if p != _END_BOOKING_OUTCOME_PATTERN
    )


def _is_existing_to_duration_consultation_not_booking(low: str) -> bool:
    """
    22275: запись на ТО уже есть, клиент уточняет только длительность работ.
    Это не создание нового слота.
    """
    head = (low or "")[:3200]
    if not head.strip():
        return False
    has_existing_slot = _has_existing_appointment_clarification(head) or bool(
        re.search(
            r"\bзаписан\w*\s+на\s+(?:нулев|нолев|перв|втор|трет|четвер|четв[её]рт|"
            r"пят|шест|седьм|восьм|девят|десят)\w*\s+то\b",
            head,
            re.I,
        )
    )
    if not has_existing_slot:
        return False
    asks_duration_only = bool(
        re.search(r"\bсколько\b[^.!?]{0,35}\bпо\s+времени\b", head, re.I)
        or re.search(r"\bпо\s+времени\b[^.!?]{0,50}\b(?:займ[её]т|будет|проход\w*)\b", head, re.I)
        or re.search(r"\b(?:займ[её]т|длительн\w*|проход\w*)\b[^.!?]{0,35}\b(?:час|часа|часов)\b", head, re.I)
    )
    if not asks_duration_only:
        return False
    if _explicit_new_ordinal_to_booking_intent_present(head):
        return False
    if re.search(
        r"\b(?:записал[аи]?\s+вас|записыва\w*\s+вас|давайте\s+тогда\s+на|"
        r"ожидаем\s+вас|подбер[её]м\s+(?:дату|время)|на\s+какое\s+время\s+записать)\b",
        head,
        re.I,
    ):
        return False
    return True


def _pre_visit_blocked_by_new_regulatory_to_booking(low: str) -> bool:
    """
    11148: в звонке подтверждают старый слот (другая работа), но основная тема — новая запись на ТО.
    Такой разговор не «pre_visit_confirmation».
    """
    if re.search(r"\bна\s+то\s+сейчас\s+запись\b", low, re.I):
        return True
    if re.search(r"\bна\s+(?:т\s+)?то\s+запис", low, re.I):
        return True
    has_ordinal_to = bool(
        re.search(
            r"\b(?:нулев|нолев|перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят)\w*\s+то\b",
            low,
            re.I,
        )
    )
    if has_ordinal_to and re.search(r"\b(?:записали|записала|записываю)\b", low):
        return True
    # 12032: новая запись на N-е ТО, не напоминание о визите.
    head = (low or "")[:1600]
    if re.search(r"\bмне\s+то\s+надо\b", head, re.I) and re.search(
        r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+проход\w+\b",
        head,
        re.I,
    ):
        return True
    return False


def _has_pre_visit_booking_confirmation(low: str) -> bool:
    """
    Исходящее напоминание накануне визита: линия звонит «по уже существующей записи» и подтверждает визит.
    Формулировки «техническое обслуживание» / время здесь — напоминание о том, на что записаны, а не новый разговор о ТО.
    """
    head = (low or "")[:1200]
    # 22301: «хотел записаться на ТО» + «давайте запишем» — старт новой записи,
    # а не подтверждение уже существующего визита.
    if re.search(
        r"\b(?:хотел[аи]?|хочу|нужно)\b[^.!?]{0,90}\bзапис(?:аться|ать)\b[^.!?]{0,45}\bна\s+то\b",
        head,
        re.I,
    ) and re.search(r"\bдавайте\s+запиш\w*\b", head, re.I):
        return False
    # 20857: CRM-перезвон по новой заявке «хотели бы записаться на ТО» — это старт новой записи,
    # а не подтверждение уже назначенного слота.
    if _crm_outbound_new_to_application_opening(low):
        return False
    existing_dated_booking_opening = bool(
        re.search(
            r"\bпо\s+(?:сегодняшн|завтрашн|послезавтрашн)\w*\s+запис",
            head,
            re.I,
        )
    )
    existing_ordinal_to_slot_opening = bool(
        re.search(
            r"\bу\s+вас\s+(?:нулев|перв|втор|трет|четвер|четвёрт|пят|шест|седьм|"
            r"восьм|девят|десят)\w*\s+то\b[^.!?]{0,45}"
            r"\b(?:сегодня|завтра|послезавтра)\b[^.!?]{0,35}"
            r"\b(?:в|на|к)\s+\d{1,2}(?:[\s:.]\d{2})?\b",
            head,
            re.I,
        )
    )
    existing_first_person_to_slot_opening = bool(
        re.search(
            r"\b(?:я|мне)\b[^.!?]{0,40}\b(?:сегодня|завтра|послезавтра)\b"
            r"[^.!?]{0,80}\b(?:на\s+)?т[оуа]-?\s*\d\b[^.!?]{0,80}\bзаписывал",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:я|мне)\b[^.!?]{0,40}\b(?:на\s+)?т[оуа]-?\s*\d\b[^.!?]{0,80}"
            r"\b(?:сегодня|завтра|послезавтра)\b[^.!?]{0,80}\bзаписывал",
            head,
            re.I,
        )
    )
    existing_first_person_today_to_time_opening = bool(
        re.search(
            r"\b(?:я|мы)?\s*\bсегодня\b[^.!?]{0,90}\bзаписан[аы]?\b"
            r"[^.!?]{0,90}\bна\s+(?:\d{1,2}|(?:один|два|три|четыре|пять|шесть|семь|восемь|девять|"
            r"десять|одиннадцать|двенадцать))\s+(?:час|часа|чс)\b",
            head,
            re.I,
        )
        and re.search(
            r"\b(?:на\s+)?(?:то|техническ\w+\s+обслуживан\w*|техобслуживан\w*)\b",
            head,
            re.I,
        )
    )
    reminder_opening = bool(
        re.search(r"(?:звоню|звоним)\s+по\s+записи", low)
        # STT: «я по записи звоню на завтра…» (7987), не «звоню по записи».
        or re.search(r"\bпо\s+записи\s+звоню\b", low)
        # 18096 STT: «я по записи уточнить звоню».
        or re.search(r"\bпо\s+записи[^.!?]{0,40}?звоню\b", low)
        or re.search(r"\bпо\s+записи\s+уточн", low)
        # 18714: «по завтрашней записи» — уже назначенный визит; обсуждают счёт и доплату.
        or existing_dated_booking_opening
        or "по записи на завтра" in low
        or "по записи на послезавтра" in low
        or re.search(
            r"\bвы\s+записаны\s+(?:к\s+нам|на\s+(?:то\b|завтра|техническ|\d)|\d)",
            low,
        )
        or re.search(r"\bвы\s+записан\s+(?:к\s+нам|на\s+(?:то\b|завтра|техническ|\d)|\d)", low)
        or re.search(r"\bвы\s+записывали\b", low)
        or re.search(r"\bзаписывали\s+автомобиль\b", low)
        # 18107 STT: «записывали на завтра, автомобиль … на техническое обслуживание».
        or re.search(r"\bзаписывали\s+на\s+завтра\b", low)
        or re.search(
            r"\bзаписывали\b[^.!?]{0,40}\bна\s+завтра\b[^.!?]{0,60}автомобил",
            low,
            re.I,
        )
        # 18153: «вы сегодня к нам записаны / записывались».
        or re.search(
            r"\bвы\s+(?:сегодня|завтра)\s+к\s+нам\s+записан[аы]?\b",
            low,
        )
        or re.search(
            r"\bвы\s+(?:сегодня|завтра)\s+к\s+нам\s+записывал",
            low,
        )
        or re.search(r"\bзавтра\s+записан[аы]?\s+к\s+нам\b", head)
        or re.search(r"\bзаписан[аы]?\s+к\s+нам\s+на\b", head)
        # 19385: слот уже назначен, но слово «записан» в первой фразе опущено:
        # «у вас нулевое ТО завтра в 11:00»; далее уточняют раннюю сдачу авто.
        or existing_ordinal_to_slot_opening
        # 20022: «завтра мне на ТО-2 ... записывался на 14:30» + подтверждение визита.
        or existing_first_person_to_slot_opening
        # 20919: «сегодня записана на 2 часа ... на ТО» — уточнение по уже существующей записи.
        or existing_first_person_today_to_time_opening
        or re.search(
            r"\bнапомните\b[^.!?]{0,45}\b(?:во\s+сколько|на\s+какое\s+время)\b"
            r"[^.!?]{0,45}\bзаписан\w*\b",
            head,
            re.I,
        )
        or (
            "хотели уточнить" in head
            and re.search(r"\bзаписан[аы]?", head)
            and (
                re.search(r"\bна\s+то\b", head)
                or "техническое обслуживание" in head
                or "техобслуживание" in head
            )
        )
        or (
            re.search(r"\bзаписан[аы]?\s+на\s+автомобил", head)
            and (
                "техническое обслуживание" in head
                or "техобслуживание" in head
                or re.search(r"\bна\s+то\b", head)
            )
        )
        # 12879: «на завтра … запланирован ваш автомобиль» — напоминание о слоте, не новая запись на ТО.
        or re.search(
            r"\b(?:на\s+)?завтра\b[^.!?]{0,80}\bзапланирован\w*[^.!?]{0,50}(?:ваш\s+)?автомобил",
            head,
            re.I,
        )
        # 18096: «на завтра в 7:50 планировали автомобиль запись?»
        or re.search(
            r"\b(?:на\s+)?завтра\b[^.!?]{0,80}\bпланировали\b[^.!?]{0,60}автомобил",
            head,
            re.I,
        )
        # 13350: «завтра на 15:30 записан на техническое обслуживание» (без «к нам» / «вы»).
        or re.search(
            r"\bзавтра\b[^.!?]{0,60}\bзаписан[аы]?\b[^.!?]{0,90}"
            r"(?:техническ\w+\s+обслуживан\w+|техобслуживан\w+|\bна\s+то\b)",
            head,
            re.I,
        )
        # STT: «компания … беспокоит» + слот на завтра (короткое напоминание перед визитом).
        or (
            re.search(r"\b(?:беспокоит|звоним)\b", head)
            and re.search(
                r"\bзавтра\b[^.!?]{0,60}\bзаписан[аы]?\b",
                head,
                re.I,
            )
            and (
                "техническое обслуживание" in head
                or "техобслуживание" in head
                or re.search(r"\bна\s+то\b", head)
            )
        )
        # 15930: STT «записываем автомобиль на завтра … всё ли актуально, приедете?»
        or re.search(
            r"\bзаписыва\w*\s+автомобил\w*\s+на\s+завтра\b",
            head,
            re.I,
        )
        or (
            re.search(r"\bзавтра\b", head)
            and re.search(r"\bтехническ\w+\s+обслуживан", head, re.I)
            and re.search(r"\bактуальн", head, re.I)
        )
    )
    if not reminder_opening:
        return False
    if _pre_visit_blocked_by_new_regulatory_to_booking(low):
        return False
    if (
        existing_dated_booking_opening
        or existing_ordinal_to_slot_opening
        or existing_first_person_to_slot_opening
        or existing_first_person_today_to_time_opening
    ):
        return True
    if "хотели уточнить" in head and re.search(r"\bподъед\w*\b", head, re.I):
        return True
    if any(
        p in low
        for p in (
            "подъедете",
            "подъедите",
            "подъедет",
            "подъедёт",
            "приедете",
            "приедите",
            "в силе",
            "ожидаем",
            "ожидаем вас",
            "ждем вас",
            "ждём вас",
            "вас ждем",
            "вас ждём",
            "вас ожидаем",
            "завтра вас",
            "я буду",
            "приеду",
            # 18153: «Вы будете?» — подтверждение прихода.
            "вы будете",
            "будете?",
        )
    ):
        return True
    # 18153: «вы будете» без вопросительного знака после нормализации.
    if re.search(r"\bвы\s+будете\b", low, re.I):
        return True
    # 13350: клиент «да, буду» + «спасибо, ждём» без «ждём вас».
    if re.search(r"\b(?:да|угу)[^.!?]{0,50}\b(?:я\s+)?буду\b", low, re.I):
        return True
    if re.search(r"\bжд[её]м\b", low) and re.search(r"\b(?:спасибо|хорошо|угу)\b", low):
        return True
    return False


def _car_question_marker_valid(low: str, pos: int, marker: str) -> bool:
    """Отсекает ложные вхождения маркеров (9317: «…года автомобиль какой пробег…»)."""
    end = pos + len(marker)
    tail = low[end : end + 28]
    if marker == "автомобиль какой" and re.match(r"^\s+пробег\b", tail, flags=re.IGNORECASE):
        return False
    return True


def _segment_after_car_question(low: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Текст после вопроса про автомобиль клиента (ответ с маркой/моделью).
    Предпочитает вхождение, после которого в ближайшем окне есть марка; иначе — самое раннее валидное.
    """
    candidates: list[Tuple[int, int, str]] = []
    for m in _CAR_IDENTITY_QUESTION_MARKERS:
        pos = 0
        while True:
            pos = low.find(m, pos)
            if pos < 0:
                break
            if _car_question_marker_valid(low, pos, m):
                candidates.append((pos, pos + len(m), m))
            pos += max(1, len(m))
    if not candidates:
        return None, None
    for _pos, end, m in sorted(candidates, key=lambda x: x[0]):
        window = low[end : end + 360]
        short_chery = _short_chery_model_answer_after_car_question(window)
        if short_chery:
            return f"{short_chery} {window}".strip(), m
        if _vehicle_mention_window_has_brand(window):
            return window.strip(), m
    _pos, end, m = sorted(candidates, key=lambda x: x[0])[0]
    return low[end:].strip(), m


_CLIENT_OWNERSHIP_BRAND_RE = re.compile(
    r"(?:"
    r"\bу\s+меня\b[^.!?]{0,180}?\b(?:ford|форд)\b"
    r"|"
    # 18187: «сейчас у меня Лиссян» / LiXiang
    r"\bу\s+меня\b[^.!?]{0,80}?\b(?:lixiang|лиссян|лисян|лисянь|полисян|li\s+xiang)\b"
    r"|"
    r"\bу\s+меня\b[^.!?]{0,180}?"
    r"\b(?:nissan|ниссан|нисан|tenet|тенет|тэнет|tiggo|тигго|"
    r"xtrail|икстрейл|extral|extreil|extri\w+|qashqai|кашкай|кашкае|кашкаи|kashka|kashkai|"
    r"pathfinder|патфайндер|pдфайндер|подфайм|подфinr|потфайм\w*|пайдер|файмер|tiida|тиида|murano|мурано|arrizo|арризо|infinit\w*|инфинит\w*)\b"
    r"|"
    r"\bу\s+меня\b[^.!?]{0,140}?\b(?:chery|чери)\s+(?:tiggo|тигго|[4789]|[48]\s*pro)\b"
    r"|"
    r"\bу\s+меня\b[^.!?]{0,120}?\bто\b[^.!?]{0,80}?\b(?:chery|чери)\b"
    r"|"
    r"\b(?:покупал|купил[аи]?|приобретал)\s+(?:nissan|ниссан|нисан|chery|чери|tenet|тенет)\b"
    r"|"
    r"\bсвой\s+(?:nissan|ниссан|нисан|chery|чери|tenet|тенет|тэнет)\b"
    r"|"
    r"\b(?:nissan|ниссан|нисан)\s+обслужива\w*"
    r"|"
    r"\bобслужива\w*[^.!?]{0,40}?\b(?:nissan|ниссан|нисан|pathfinder|патфайндер|pдфайндер|подфайм|потфайм\w*|пайдер|tiida|тиида)\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)

# Зоны, где марка — из речи клиента (не из приветствия дилера).
_CLIENT_BRAND_PRIORITY_ZONES = frozenset(
    {
        "client_ownership",
        "after_car_question",
        "after_car_question_short_answer",
        "client_maintenance_request",
        "early_client_vehicle",
        "crm_vehicle_passport",
        "after_booking_confirmation",
        "full_transcript_vehicle",
        # 17574: «заявку получили по автомобилю … чери Тиг» — марка клиента, не шапка дилера.
        "after_vehicle_mention",
    }
)


def _ownership_segment_is_past_vehicle_reference(low: str, seg_start: int) -> bool:
    """
    13985: «до этого у нас была Nissan … обслуживались» — прошлый автомобиль, не текущий.
    """
    prefix = (low or "")[max(0, seg_start - 140) : seg_start]
    return bool(
        re.search(
            r"\b(?:"
            r"до\s+этого|"
            r"а\s+до\s+этого|"
            r"в\s+прошлом|"
            r"(?:у\s+нас\s+)?(?:была|был|были)\s+(?:nissan|нissan|нисan|ниссан|нисан|chery|чери|tenet|тенет|тэнет)"
            r")\b",
            prefix,
            re.I,
        )
    )


def _ownership_segment_is_dealer_service_history_question(low: str, seg_start: int) -> bool:
    """
    16318: «у вас раньше Ниссан обслуживали форды?» — вопрос про историю ДЦ, не марка клиента.
    """
    prefix = (low or "")[max(0, seg_start - 100) : seg_start]
    suffix = (low or "")[seg_start : seg_start + 140]
    if re.search(r"\b(?:раньше|у\s+вас|вы\s+ведь|на\s+викинг\w*)\b", prefix, re.I):
        if re.search(r"\bобслужива\w*", suffix, re.I):
            return True
    if re.search(r"\bобслужива\w*[^.!?]{0,50}?\b(?:ford|форд)\w*\b", suffix, re.I):
        return True
    return False


def _segment_client_ownership_brand(low: str) -> Optional[str]:
    """
    Клиент называет своё авто: «у меня … Ниссан», «свой Ниссан обслуживаю» (10302).
    Приоритетнее приветствия «Викинги Чери» и «дилерский центр Чери» при переводе.
    """
    head = low[:3200]
    m = _CLIENT_OWNERSHIP_BRAND_RE.search(head)
    if not m:
        return None
    if _ownership_segment_is_past_vehicle_reference(head, m.start()):
        return None
    if _ownership_segment_is_dealer_service_history_question(head, m.start()):
        return None
    # Только от совпадения — не захватывать «Викинги Чери» из приветствия слева (10302).
    return head[m.start() : m.end() + 100].strip()


def _segment_early_client_vehicle_brand(low: str) -> Optional[str]:
    """
    Клиент называет марку/модель в начале без вопроса «какой автомобиль?»
    (8811: «Nissan Extral, необходимо на осмотр»; 9308: «сколько ТО стоит на Чре 4»).
    """
    head = low[:2400]
    # 16395: «звонил по поводу Nissan Кашкай» — до перевода на мастера, не терять в after_greeting_body.
    m_about_vehicle = re.search(
        r"\b(?:звонил[аи]?|обращал\w*|звоню|обраща\w*)\s+[^.!?]{0,80}?\bпо\s+поводу\s+"
        r"(?:nissan|нissan|нисan|ниссan|нисan|chery|чери|tenet|тенет|тэнет)\b"
        r"[^.!?]{0,60}?\b(?:"
        r"xtrail|икстрейл|extral|extreil|extri\w+|qashqai|кашкай|кашка|кашкае|кашкаи|"
        r"kashkai|kashka|pathfinder|патфайндер|подфайм|потфайм\w*|tiggo|тигго|arrizo|арризо"
        r")?\b",
        head,
        flags=re.IGNORECASE,
    )
    if m_about_vehicle:
        start = max(0, m_about_vehicle.start() - 24)
        return head[start : m_about_vehicle.end() + 100].strip()
    # 28647: «Владелец автомобиля Кашкай» без явного Nissan в том же фрагменте.
    m_owner_vehicle_nissan_model = re.search(
        r"\bвладел\w*\s+автомобил[ья]\s+"
        r"(?:qashqai|кашкай|кашка|кашкае|кашкаи|"
        r"xtrail|икстрейл|extral|extreil|extri\w+|"
        r"murano|мурано|almera|альмера|teana|тиана|juke|джук|жук|"
        r"pathfinder|патфайндер|подфайм|потфайм\w*|potfid\w*|"
        r"tiida|тиида|patrol|patroll|terrano|террано)\b",
        head,
        flags=re.IGNORECASE,
    )
    if m_owner_vehicle_nissan_model:
        start = max(0, m_owner_vehicle_nissan_model.start() - 24)
        return head[start : m_owner_vehicle_nissan_model.end() + 100].strip()
    m_vehicle_tenet = re.search(
        r"\b(?:машин[аы]|автомобил[ьа])\b[^.!?]{0,120}?\b(?:tenet|тенет|тэнет)\b",
        head,
        flags=re.IGNORECASE,
    )
    if m_vehicle_tenet:
        start = max(0, m_vehicle_tenet.start() - 16)
        return head[start : m_vehicle_tenet.end() + 80].strip()
    m_to_price = re.search(
        r"(?:сколько|узнать|стоимост).{0,120}?\bто\b.{0,100}?"
        r"на\s+(?:chery|чери|tiggo|тигго|тенет|tenet|чре)\s*(?:tiggo|тигго\s*)?([4789])\b",
        head,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not m_to_price:
        m_to_price = re.search(
            r"(?:подскаж\w*|сколько|узнать|стоимост).{0,100}?\bто\s*[-]?\s*([4789])\b",
            head,
            flags=re.IGNORECASE | re.DOTALL,
        )
    if m_to_price:
        start = max(0, m_to_price.start() - 24)
        return head[start : m_to_price.end() + 80].strip()
    m_to_on_tenet = re.search(
        r"\bто\s*[-]?\s*\d\b[^.!?]{0,48}?\b(?:на\s+)?(?:tenet|тенет|тэнет)\b",
        head,
        flags=re.IGNORECASE,
    )
    if m_to_on_tenet:
        start = max(0, m_to_on_tenet.start() - 24)
        return head[start : m_to_on_tenet.end() + 80].strip()
    m_tmax = re.search(
        r"запис\w+[^.!?]{0,120}?\bто\b[^.!?]{0,60}?"
        r"(?:на\s*[-]?\s*)?([4789])\s*тмакс",
        head,
        flags=re.IGNORECASE,
    )
    if m_tmax:
        start = max(0, m_tmax.start() - 48)
        return head[start : m_tmax.end() + 100].strip()
    m_chery_model = re.search(
        r"\b(?:chery|чери)\s+tiggo\s+([4789])(?:\s+pro(?:\s+max)?)?\b",
        head,
        flags=re.IGNORECASE,
    )
    if m_chery_model:
        start = max(0, m_chery_model.start() - 40)
        return head[start : m_chery_model.end() + 60].strip()
    m = re.search(
        r"\b(?:nissan|ниссан|нисан|nesan)\s+"
        r"(?:xtrail|икстрейл|extral|extreil|extri\w+|qashqai|кашкай|кашкае|кашкаи|murano|мурано|"
        r"almera|альмера|teana|тиана|juke|джук|жук|pathfinder|патфайндер|подфайм|потфайм\w*|"
        r"potfid\w*|пайдер|файмер|"
        r"tiida|тиида|"
        r"patrol|patroll|terrano|террано)\b",
        head,
        flags=re.IGNORECASE,
    )
    if not m:
        m = re.search(
            r"\bна\s+ремонт\s+(?:nissan|ниссан|нисан|nesan)\s+patroll?\b",
            head,
            flags=re.IGNORECASE,
        )
    if m:
        start = max(0, m.start() - 32)
        return head[start : m.end() + 100].strip()
    # 16709: «я у вас был в субботу Nissan …» — марка в начале до перевода на мастера.
    m_visit_ns = re.search(
        r"\bя\s+у\s+вас\s+был[аи]?\b[^.!?]{0,120}?\b(?:nissan|ниссан|нисан|nesan)\b",
        head,
        flags=re.IGNORECASE,
    )
    if m_visit_ns:
        start = max(0, m_visit_ns.start() - 16)
        return head[start : m_visit_ns.end() + 100].strip()
    # 13412: «вы на Nissan … делаете» — марка в начале без «у меня».
    m_ns_service = re.search(
        r"\b(?:вы\s+)?на\s+(?:nissan|ниссан|нисан)\b[^.!?]{0,100}?\b(?:делаете|обслужива\w*|ремонт\w*|перевод)\b",
        head,
        flags=re.IGNORECASE,
    )
    if m_ns_service:
        start = max(0, m_ns_service.start() - 24)
        return head[start : m_ns_service.end() + 120].strip()
    m_pf = re.search(
        r"\b(?:pathfinder|патфайндер|подфайм|потфайм\w*|potfid\w*|сподфайнд\w*|пайдер|файмер)\b",
        head,
        flags=re.IGNORECASE,
    )
    if m_pf:
        start = max(0, m_pf.start() - 40)
        return head[start : m_pf.end() + 100].strip()
    m_kashkai = re.search(
        r"\b(?:приеду\s+)?на\s+кашка(?:й|е|и)\b",
        head,
        flags=re.IGNORECASE,
    )
    if m_kashkai:
        start = max(0, m_kashkai.start() - 48)
        return head[start : m_kashkai.end() + 120].strip()
    return None


def _segment_crm_vehicle_passport(low: str) -> Optional[str]:
    """
    CRM после уточнения карточки: «Чеr 9 … госномер … пробег» (10195).
    Не отрезать марку поздним якорем «подскажите» в хвосте разговора.
    """
    head = low[:3200]
    for m in re.finditer(r"\bгосномер\b", head, flags=re.IGNORECASE):
        window = head[max(0, m.start() - 140) : m.end() + 90]
        if _vehicle_mention_window_has_brand(window):
            return window.strip()
    m = re.search(
        r"\b(?:chery\s+tiggo\s+[4789]|chery\s+[4789]|тенет\s+t[4789]|tenet\s+t[4789]|"
        r"tiggo\s+[4789]|тигго\s+[4789])\b[^.!?]{0,160}?\b(?:госномер|черн\w+\s+цвет)\b",
        head,
        flags=re.IGNORECASE,
    )
    if m:
        start = max(0, m.start() - 24)
        return head[start : m.end() + 100].strip()
    return None


def _segment_after_vehicle_mention(low: str) -> Optional[str]:
    """
    Фрагмент сразу после формулировок вида «по автомобилю ...» / «автомобиль ...».
    Для бренда это приоритетнее общего фона (приветствие «Викинги Чери»).

    Перебираем все вхождения: первое «автомобил…» часто — эхо STT («…на автомобиле на
    напомните…») без марки; берём первое вхождение, после которого в окне есть марка/модель.
    """
    pat = re.compile(r"\b(?:по\s+)?автомобил[ьюея]\s+", re.IGNORECASE)
    for m in pat.finditer(low):
        before = low[max(0, m.start() - 36) : m.start()]
        # «половину автомобиля разобрать» — не вопрос про марку (10302).
        if re.search(r"(?:половин\w*|част\w*|разобра\w*)\s+автомобил", before, flags=re.IGNORECASE):
            continue
        frag = low[m.end() :].strip()
        if len(frag) < 4:
            continue
        window = frag[:220]
        if re.search(r"\bдилерск\w*\s+центр\b", window[:120], flags=re.IGNORECASE):
            continue
        if _vehicle_mention_window_has_brand(window):
            return window
    return None


def _body_anchor_marker_variants(marker: str) -> Tuple[str, ...]:
    m = (marker or "").strip()
    if not m:
        return ()
    out = [m]
    if ", " in m:
        out.append(m.replace(", ", " "))
    if "," in m:
        out.append(m.replace(",", ""))
    return tuple(dict.fromkeys(out))


def _opening_client_brand_before_anchor(low: str, anchor_pos: int) -> bool:
    """13412: марка в реплике клиента до якоря «замена масла» — не отрезать по якорю."""
    prefix = (low or "")[:anchor_pos]
    listen_end = _dealer_listen_opener_end_pos(prefix)
    if listen_end > 0:
        after_greeting = prefix[listen_end:]
    else:
        after_greeting = prefix
        for m in ("слушаю вас", "слуша вас", "слушаю."):
            pos = prefix.find(m)
            if pos >= 0:
                after_greeting = prefix[pos + len(m) :]
                break
    if _segment_has_client_assignable_chery_tenet(after_greeting):
        return True
    if _contains_any(after_greeting, _NISSAN_EXACT)[0]:
        return True
    if re.search(r"\b(?:вы\s+)?на\s+(?:nissan|ниссан|нисан)\b", after_greeting, re.I):
        return True
    return False


def _dealer_listen_opener_end_pos(low: str) -> int:
    """Конец шаблона «… слушаю» в начале звонка (не марка из приветствия)."""
    head = (low or "")[:280]
    for m in ("слушаю вас", "слуша вас", "слушаю."):
        pos = head.find(m)
        if pos >= 0:
            return pos + len(m)
    m = re.search(
        r"(?:ассистент|администратор|диспетчер|сервис)[^.!?]{0,90}?\bслушаю\b",
        head,
        re.I,
    )
    if m:
        return m.end()
    m = re.search(r"^[а-яё]{3,14}\s+слушаю\b", head)
    if m:
        return m.end()
    return 0


def _segment_has_client_assignable_chery_tenet(seg: str) -> bool:
    """Конкретная модель Chery/Tenet в речи клиента (не голое «чери» из шапки дилера)."""
    if not (seg or "").strip():
        return False
    for pat in _EXPLICIT_CHERY_TENET_VEHICLE_ANYWHERE_RES:
        if pat.search(seg):
            return True
    if any(s in seg for s in _STRONG_CHERY_VEHICLE_MARKERS if len((s or "").strip()) >= 3):
        return True
    # 17574: CRM «по автомобилю … чери» / «заявку получили … чери» — марка клиента без модели.
    if re.search(
        r"\b(?:по\s+автомобил\w*|заявк\w*\s+получен\w*|автомобил\w*)\b"
        r"[^.!?]{0,80}\b(?:чери|chery|тигго|tiggo|тенет|tenet)\b",
        seg,
        re.I,
    ):
        return True
    if _contains_chery_tenet(seg)[0] and re.search(r"\b(?:госномер|пробег)\b", seg, re.I):
        return True
    return False


def _dealer_greeting_end_pos(low: str) -> int:
    """
    Индекс начала тела разговора после приветствия дилера («… слушаю вас»).
    Марка авто клиента никогда не берётся из префикса [0:pos).
    """
    opening_anchor_limit = 3200
    best_pos: Optional[int] = None
    crm_brand_pos: Optional[int] = None
    crm_seg = _segment_crm_vehicle_passport(low)
    if crm_seg:
        idx = low.find(crm_seg[: min(40, len(crm_seg))])
        if idx >= 0:
            crm_brand_pos = idx
    for m in _BODY_AFTER_GREETING_MARKERS:
        for variant in _body_anchor_marker_variants(m):
            pos = low.find(variant)
            if 60 < pos < opening_anchor_limit and (best_pos is None or pos < best_pos):
                best_pos = pos
    if best_pos is not None and crm_brand_pos is not None and crm_brand_pos < best_pos:
        best_pos = None
    if best_pos is not None and _opening_client_brand_before_anchor(low, best_pos):
        best_pos = None
    if best_pos is not None:
        return best_pos
    for m in ("слушаю вас", "слуша вас", "слушаю."):
        pos = low.find(m)
        if pos >= 0:
            return pos + len(m)
    listen_end = _dealer_listen_opener_end_pos(low)
    if listen_end > 0:
        opening_body = low[listen_end : min(len(low), listen_end + 700)]
        if _segment_has_client_assignable_chery_tenet(opening_body) or _contains_any(
            opening_body, _NISSAN_EXACT
        )[0]:
            return listen_end
    if len(low) > _GREETING_ONLY_BRAND_WINDOW:
        return _GREETING_ONLY_BRAND_WINDOW
    return 0


def _body_low_after_dealer_greeting(low: str) -> str:
    """
    Тело разговора без приветствия дилера: Чери/Тенет не ищем в «Викинги Чери … слушаю».
    """
    return low[_dealer_greeting_end_pos(low) :].lstrip(" .,!?-—")


def _segment_after_booking_confirmation_answer(low: str) -> Optional[str]:
    """
    Рамка: диспетчер уточняет заявку/ТО («нулевое ТО, правильно?»), клиент коротко называет авто
    («Да. 7емь.» → Tenet T7) без отдельного вопроса «какой автомобиль?» (8852 и аналоги).
    """
    patterns = (
        r"нулевое\s+то[^.!?]{0,55}(?:правильно|верно)",
        r"на\s+(?:нулевое\s+)?то[^.!?]{0,55}(?:правильно|верно)",
        r"по\s+заявке[^.!?]{0,90}нулевое\s+то[^.!?]{0,35}(?:правильно|верно)",
        r"звоню\s+на\s+запись[^.!?]{0,60}нулевое\s+то[^.!?]{0,35}(?:правильно|верно)",
    )
    for pat in patterns:
        m = re.search(pat, low, flags=re.IGNORECASE)
        if not m:
            continue
        frag = low[m.end() : m.end() + 160].strip()
        if len(frag) < 2:
            continue
        head = frag[:100]
        if _contains_chery_tenet(head)[0]:
            return frag
        if re.search(r"\b(?:да|угу|ага)\b", head[:24]) and (
            _contains_chery_tenet(frag)[0]
            or re.search(r"\b(?:t[4789]|т[4789]|\d)\b", head)
        ):
            return frag
    return None


def _brand_focus_low(low: str, evidence: Dict[str, Any]) -> str:
    """
    Фрагмент для марки авто клиента: приоритет — ответ на «какой автомобиль/какая машина»
    и «у меня …»; не приветствие дилера «Викинги Чери … слушаю».
    """
    frag, marker = _segment_after_car_question(low)
    early_veh = _segment_early_client_vehicle_brand(low)
    if frag is not None and len(frag) >= 4:
        # 28647: если после «какой у вас авто?» нет марки, но в раннем ответе клиента
        # есть явная модель (напр. «владелец автомобиля Кашкай»), берём ранний сегмент.
        if early_veh and not _vehicle_mention_window_has_brand(frag[:420]) and _vehicle_mention_window_has_brand(early_veh):
            evidence["brand_zone"] = "early_client_vehicle"
            evidence["brand_question_marker"] = marker or ""
            return early_veh
        evidence["brand_zone"] = "after_car_question"
        evidence["brand_question_marker"] = marker or ""
        return frag
    if frag is not None and len(frag) < 4:
        evidence["brand_zone"] = "after_car_question_short_answer"
        evidence["brand_question_marker"] = marker or ""
        return _body_low_after_dealer_greeting(low)
    ownership = _segment_client_ownership_brand(low)
    if ownership:
        evidence["brand_zone"] = "client_ownership"
        evidence["brand_question_marker"] = ""
        return ownership
    maint = _segment_client_maintenance_brand(low)
    if maint:
        evidence["brand_zone"] = "client_maintenance_request"
        evidence["brand_question_marker"] = ""
        return maint
    if early_veh:
        evidence["brand_zone"] = "early_client_vehicle"
        evidence["brand_question_marker"] = ""
        return early_veh
    crm_passport = _segment_crm_vehicle_passport(low)
    if crm_passport:
        evidence["brand_zone"] = "crm_vehicle_passport"
        evidence["brand_question_marker"] = ""
        return crm_passport
    confirm = _segment_after_booking_confirmation_answer(low)
    if confirm:
        evidence["brand_zone"] = "after_booking_confirmation"
        evidence["brand_question_marker"] = ""
        return confirm
    veh = _segment_after_vehicle_mention(low)
    if veh:
        evidence["brand_zone"] = "after_vehicle_mention"
        evidence["brand_question_marker"] = ""
        return veh
    evidence["brand_zone"] = "after_greeting_body"
    evidence["brand_question_marker"] = ""
    return _body_low_after_dealer_greeting(low)


def _variant_present_in_text(text: str, variant: str) -> bool:
    """Короткие марки — по границе слова (9233: «нива» в «заклинивает»; 10675: «лада» в «вклада»)."""
    v = variant or ""
    core = v.strip()
    if not core:
        return False
    if len(core) <= 6:
        return bool(
            re.search(
                rf"(?<![а-яёa-z0-9]){re.escape(core)}(?![а-яёa-z0-9])",
                text,
                re.IGNORECASE,
            )
        )
    return v in text


def _contains_any(text: str, variants: Iterable[str]) -> Tuple[bool, str]:
    for v in variants:
        if _variant_present_in_text(text, v):
            return True, v
    return False, ""


def _last_variant_pos(text: str, variants: Iterable[str]) -> int:
    """Позиция последнего вхождения любого маркера (для разрешения конфликта «Чери в приветствии» vs марка авто клиента ниже по тексту)."""
    best = -1
    for v in variants:
        if not v or v not in text:
            continue
        pos = text.rfind(v)
        if pos > best:
            best = pos
    return best


def _explicit_brand_discussion_score(text: str, variants: Iterable[str]) -> int:
    """
    Насколько содержательно в тексте обсуждается марка (а не фон из приветствия).
    Сигналы: модель/кузов/год/VIN/запчасти/ТО рядом с названием марки.
    """
    low = text or ""
    if not low.strip():
        return 0
    score = 0
    for v in variants:
        vv = (v or "").strip()
        if not vv:
            continue
        pat = re.compile(
            rf"(?<![а-яёa-z0-9]){re.escape(vv)}(?![а-яёa-z0-9])",
            flags=re.IGNORECASE,
        )
        for m in pat.finditer(low):
            left = low[max(0, m.start() - 40) : m.start()]
            right = low[m.end() : m.end() + 140]
            window = left + low[m.start() : m.end()] + right
            if re.search(
                r"\b(?:модель|кузов|год|года|госномер|vin|вин|пробег|"
                r"запчаст\w*|брызговик\w*|диск\w*|колод\w*|фильтр\w*|"
                r"вариатор\w*|двигател\w*|свеч\w*|масл\w*|подшипник\w*|"
                r"pathfinder|патфайндер|r[\s-]*\d{2}|то\s*[-]?\s*\d{1,3})\b",
                window,
                re.I,
            ):
                score += 2
            if re.search(r"\b(?:у\s+меня|мой|моего|автомобил\w*|машин\w*|по)\s*$", left, re.I):
                score += 1
            if re.search(rf"^{re.escape(vv)}\s+[a-zа-яё0-9-]{{2,18}}", low[m.start() : m.start() + 40], re.I):
                score += 1
    return score


# Марка авто не названа: «чери»/лишние токены только в приветствии дилера (~«Викинги Чери …»),
# не отрезать речь клиента с маркой/моделью на ~100–200-м символе (8811: Nissan Extral).
_GREETING_ONLY_BRAND_WINDOW = 50


_STRONG_CHERY_VEHICLE_MARKERS = (
    "тигго",
    "tiggo",
    "тига ",
    " тигго",
    "чери тигго",
    "тенет ",
    "tenet ",
    "промакс",
    "про макс",
    "т-7",
    " т7",
    " t7",
    "т-8",
    " т8",
    " t8",
    "тенет t7",
    "тенет t8",
    " tenet t7",
    " tenet t8",
    "tenet t4 pro",
    "тенет t4 pro",
    "t4 pro",
    "t4pro",
    "т4 pro",
    "тигго 4",
    "тиг 4",
    "tig 4",
    "тигго 7",
    "тигго 8",
    "тигго 9",
    "tiggo 4",
    "tiggo 4 pro",
    "chery tiggo 4 pro",
    # STT-обломки (см. _normalize_text: чh[рr], tиg).
    "чhr",
    "чhр",
    "tиg",
    "тиg",
    "tiggo 7",
    "tiggo 7 pro",
    "7 про",
    "7про",
    "7 pro",
    "7pro",
    "tiggo 8",
    "tiggo 8 pro max",
    "chery tiggo 8 pro max",
    "tiggo 9",
    # «Номерные» модели Chery / Tenet (после нормализации STT-варианты «Чеr 4 / cher 4 / chery 4»
    # склеиваются в «chery N»; дилер также маркетинг-называет ту же модель «Tenet N»).
    "chery 4",
    "chery 7",
    "chery 8",
    "chery 9",
    "чери 4",
    "чери 7",
    "чери 8",
    "чери 9",
    "тенет 4",
    "тенет 7",
    "тенет 8",
    "тенет 9",
    "тэнет 4",
    "тэнет 7",
    "тэнет 8",
    "тэнет 9",
    "тэнет-7",
    "тэнет-8",
    "тэнет-4",
    "tenet 4",
    "tenet 7",
    "tenet 8",
    "tenet 9",
    # STT «Чиритиг…» / «Черетиг…» без отдельного «тигго» в той же фразе.
    "чиритиг",
    "черетиг",
    "промаакс",
    "арризо",
    "arrizo",
    "arrizo 8",
    "arrizo8",
    # STT «Тео-1 / ТУ-1» — цена первого регламентного ТО на линии Чери (8009).
    "тео-1",
    "тэо-1",
    "ту-1",
    "teo-1",
    # Регламент первого ТО: «Чери год со дня покупки, либо на 10 000» (8359).
    "чери год",
)

# Короткие подстроки из _STRONG_CHERY_VEHICLE_MARKERS — не для поиска по всему тексту (ложные « t7»).
_STRONG_CHERY_VEHICLE_MARKERS_FULLTEXT_SKIP = frozenset(
    {
        " t7",
        " t8",
        " t4",
        "т-7",
        "т-8",
        "т-4",
        "t7",
        "t8",
        "t4",
        "т7",
        "т8",
        "т4",
    }
)

_EXPLICIT_CHERY_TENET_VEHICLE_ANYWHERE_RES = (
    re.compile(
        r"\b(?:tenet|тенет|тэнет)(?:\s+(?:tenet|тенет|тэнет))?\s*t\s*([4789])(?:\s+pro(?:\s+max)?)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:chery|чери)\s+tiggo\s+([4789])(?:\s+pro(?:\s+max)?)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:tenet|тенет|тэнет)\s+([4789])(?:\s+pro(?:\s+max)?)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bто\s*[-]?\s*([4789])\b.{0,100}?\b(?:на\s+)?(?:tenet|тенет|тэнет)\b",
        re.IGNORECASE | re.DOTALL,
    ),
)


def _explicit_chery_tenet_vehicle_anywhere(low: str) -> Tuple[bool, str]:
    """
    Явная модель Chery/Tenet вне приветствия дилера (10282: «ТО-1 на Tenet T7»
    до позднего «подскажите» в CRM-хвосте).
    """
    search = low[_dealer_greeting_end_pos(low) :]
    for pat in _EXPLICIT_CHERY_TENET_VEHICLE_ANYWHERE_RES:
        m = pat.search(search)
        if m:
            return True, (m.group(0) or "").strip()
    for s in _STRONG_CHERY_VEHICLE_MARKERS:
        if s in _STRONG_CHERY_VEHICLE_MARKERS_FULLTEXT_SKIP:
            continue
        if len((s or "").strip()) < 5:
            continue
        if s in search:
            return True, s.strip()
    return False, ""


def _service_brand_assignable_outside_greeting(
    low: str,
    brand: str,
    evidence: Dict[str, Any],
) -> bool:
    """
    Марка никогда не берётся только из приветствия дилера («Викинги Чери … слушаю»).
    """
    if (evidence.get("brand_method") or "") == "zero_to_booking_chery_tenet_default":
        return True
    if (evidence.get("brand_method") or "") == "warranty_context_chery_tenet_default":
        return True
    if evidence.get("brand_zone") in _CLIENT_BRAND_PRIORITY_ZONES:
        return True
    body = low[_dealer_greeting_end_pos(low) :]
    if not (body or "").strip():
        return False
    if brand == "chery_tenet":
        full_hit = evidence.get("brand_full_text_vehicle_hit") or ""
        if full_hit and full_hit in body:
            return True
        if any(s in body for s in _STRONG_CHERY_VEHICLE_MARKERS):
            return True
        if _contains_chery_tenet(body)[0]:
            return True
        for pat in _EXPLICIT_CHERY_TENET_VEHICLE_ANYWHERE_RES:
            if pat.search(body):
                return True
        return False
    if brand == "nissan":
        if _contains_any(body, _NISSAN_EXACT)[0]:
            return True
        # 10302: «я у вас свой Ниссан обслуживаю» может остаться в префиксе
        # до технической границы greeting_end, но это клиентская марка.
        if re.search(r"\b(?:свой\s+nissan|свой\s+ниссан|у\s+меня\s+nissan|у\s+меня\s+ниссан)\b", low, re.I):
            return True
        return False
    return True


def _suppress_chery_if_only_greeting_dealer_noise(low: str, evidence: Dict[str, Any]) -> bool:
    """
    Не присваивать Чери/Тенет, если маркеры только в префиксе приветствия дилера,
    а в основной части разговора нет конкретной модели Чери/Tiggo/Tenet.

    Усиление: если в транскрипте явно звучит чужой бренд (Exeed/Hyundai/Toyota/…)
    и при этом нет конкретной модели Чери/Tiggo/Tenet — подавляем независимо от позиций
    «чери» и от brand_zone (включая after_car_question, потому что ответ клиента —
    другая марка, и фрагмент после вопроса всё равно не должен закрепить Chery).
    """
    has_other_brand_anywhere = _contains_any(low, _OTHER_BRAND_EXACT)[0]
    has_strong_chery_model = any(s in low for s in _STRONG_CHERY_VEHICLE_MARKERS)
    if has_other_brand_anywhere and not has_strong_chery_model:
        return True
    # STT: «Тео-1 / ТУ-1» вместо «ТО-1» / «первое ТО» в начале разговора — до «тигго/chery 7» в тексте;
    # без этого марка гасится в «прочие» при длинном транскрипте (8009).
    if any(s in low for s in ("тео-1", "тэо-1", "ту-1", "teo-1")):
        return False
    if evidence.get("brand_zone") in _CLIENT_BRAND_PRIORITY_ZONES or evidence.get("brand_zone") in (
        "after_vehicle_mention",
        "after_greeting_body",
    ):
        # Марка берётся из ответа клиента / тела разговора, не из приветствия дилера.
        return False
    pos_last = _last_variant_pos(low, _CHERY_TENET_EXACT)
    if pos_last >= _GREETING_ONLY_BRAND_WINDOW:
        return False
    anchor = 0
    for mark in ("удобно говорить", "удобно разговаривать"):
        i = low.find(mark)
        if i >= 0:
            anchor = max(anchor, i + len(mark) + 8)
    # Короче окна приветствия — смотрим весь текст: иначе low[240:] пустой и «tiggo 4 pro»
    # после нормализации «4Pро» не попадает в tail → ложное «марка не указана».
    if anchor > 0:
        tail = low[anchor:]
    elif len(low) <= _GREETING_ONLY_BRAND_WINDOW:
        tail = low
    else:
        tail = low[_GREETING_ONLY_BRAND_WINDOW // 2 :]
    # Перенос визита после срыва ТО («планируем … ваш визит») — маркеры Чери в приветствии оставляем.
    if "планируем" in tail and "визит" in tail:
        return False
    tail_strong_vehicle = (
        "ниссан",
        "nissan",
        "нисан",
        "кашкай",
        "qashqai",
        "kashkai",
        "xtrail",
        "икстрейл",
    ) + _STRONG_CHERY_VEHICLE_MARKERS
    if any(s in tail for s in tail_strong_vehicle):
        return False
    # Консультация по срокам первого ТО Chery: «чери год» / 10 000 км, без названия модели (8359).
    if "чери" in low and any(
        p in low for p in ("первое то", "первое т о", "нулевое", "нулевой", "чери год")
    ) and any(
        p in low
        for p in (
            "10 000",
            "10000",
            "10.000",
            "десяти тысяч",
            "10 тысяч",
            "год со дня",
            "либо год",
        )
    ):
        return False
    return True


_VIKINGI_LADA_SISTER_PHONE_RE = re.compile(
    r"63[\s.:,\-nоль]*0+[\s.:,\-nоль]*(?:7+\s*7|5+\s*0|5+\b)",
    re.I,
)


def _xcite_brand_mentioned(low: str) -> bool:
    """Xcite / STT «Xсаit», «ИX саit», «XitePr7», модель X-Cross (лат+кир)."""
    return bool(
        re.search(
            r"\bxcite\b|\bxite\b|\bxitepr\d*\b|\bxcross\b|"
            r"\bx[сs][aа]\s*it\b|\bx[сs][aа]it\b|"
            r"\b(?:и|i)\s*x\s+[сs][aа]\s*it\b|\b(?:и|i)x\s+[сs][aа]\s*it\b",
            low or "",
            re.I,
        )
    )


def _lada_or_xcite_sister_dealer_brand(low: str) -> bool:
    return bool(
        _xcite_brand_mentioned(low)
        or re.search(r"\bлада\b", low or "")
        or re.search(r"\b(?:vesta|грант\w*|ларгус|нива|хрей)\b", low or "", re.I)
    )


def _is_vikingi_komsomol_sister_redirect_not_service(low: str) -> bool:
    """
    15790: Xcite / Лада — ТО только в другом салоне Викинги (Комсомольский район),
    на линии Чери/Tenet (Заставная) не обслуживают; запись не состоялась.
    """
    if not re.search(r"викинг", low or ""):
        return False
    redirect = bool(
        re.search(r"комсомольск\w*\s+район", low or "", re.I)
        or re.search(r"викинг\w*[^.!?]{0,70}комсомольск", low or "", re.I)
        or (
            re.search(r"только\s+[^.!?]{0,55}викинг", low or "", re.I)
            and re.search(r"комсомольск", low or "", re.I)
        )
    )
    if not redirect:
        return False
    wrong_line = bool(
        re.search(r"заставн", low or "", re.I)
        or re.search(r"здесь\s+нельзя", low or "", re.I)
        or re.search(
            r"не\s+(?:выполня\w*|обслужива\w*)[^.!?]{0,25}(?:тут|здесь)",
            low or "",
            re.I,
        )
        or re.search(r"звоните\s+[^.!?]{0,50}викинг", low or "", re.I)
    )
    if not wrong_line:
        return False
    return _lada_or_xcite_sister_dealer_brand(low)


def _is_vikingi_gromovaya_xcite_sister_redirect_not_service(low: str) -> bool:
    """
    17497: Xcite — на линии Чери/Tenet не обслуживают; отправляют в Викинги на Громовой
    («ТО пройти нельзя», «Викинги другие»).
    «на Громовой» — адрес ДЦ; не путать с фамилией «Громов».
    """
    if not _xcite_brand_mentioned(low or ""):
        return False
    if not re.search(r"викинг", low or "", re.I):
        return False
    redirect = bool(
        re.search(r"\bна\s+громов", low or "", re.I)
        or re.search(r"громов\w*\s+улиц", low or "", re.I)
        or re.search(r"отправля\w+[^.!?]{0,80}викинг", low or "", re.I)
        or re.search(r"викинг\w*[^.!?]{0,50}друг", low or "", re.I)
        or re.search(r"обрат\w+[^.!?]{0,55}викинг\w*[^.!?]{0,40}друг", low or "", re.I)
    )
    if not redirect:
        return False
    refusal = bool(
        re.search(r"к\s+сожал[её]ни\w*\s*,?\s*нет", low or "", re.I)
        or re.search(r"(?:то|техобслуживани\w*)\s+пройти\s+нельзя", low or "", re.I)
        or re.search(r"пройти\s+[^.!?]{0,25}нельзя", low or "", re.I)
        or re.search(r"нельзя[^.!?]{0,40}(?:то|пройт)", low or "", re.I)
        or re.search(r"не\s+официальн\w*\s+дилер", low or "", re.I)
        or re.search(r"здесь\s+нельзя", low or "", re.I)
        or re.search(
            r"не\s+(?:выполня\w*|обслужива\w*|принима\w*)[^.!?]{0,25}(?:тут|здесь)",
            low or "",
            re.I,
        )
    )
    return refusal


def _is_wrong_department_vikingi_lada_redirect_not_service(low: str) -> bool:
    """
    Клиент ошибся номером: линия Чери/Tenet на Заставной → другой салон Викинги
    (Лада 63 0077 / Комсомольский район / Громовая для Xcite). Запись на ТО не состоялась
    (14222, 14967, 15790, 17497).
    """
    if _is_vikingi_komsomol_sister_redirect_not_service(low):
        return True
    if _is_vikingi_gromovaya_xcite_sister_redirect_not_service(low):
        return True
    if not re.search(r"викинг\w*\s+лада", low):
        return False
    redirect = bool(
        _VIKINGI_LADA_SISTER_PHONE_RE.search(low)
        or re.search(
            r"(?:вам|вас)\s+нужн\w+[^.!?]{0,45}викинг\w*\s+лада",
            low,
        )
        or re.search(r"викинг\w*\s+лада[^.!?]{0,55}другой\s+номер", low)
        or re.search(r"другой\s+номер[^.!?]{0,55}викинг\w*\s+лада", low)
        or re.search(r"перезвон\w*[^.!?]{0,40}викинг\w*\s+лада", low)
        or re.search(r"по-другому\s+телефон", low)
        or re.search(r"громов\w*\s+улиц", low)
        or re.search(r"\bна\s+громов", low)
        or re.search(r"не\s+на\s+заставн", low)
    )
    if not redirect:
        return False
    wrong_line = bool(
        re.search(r"позвонил\w*[^.!?]{0,110}(?:заставн|чери|тенет|тоннет|тэнет)", low)
        or (
            re.search(r"обслужива\w*", low)
            and re.search(r"(?:чери|тенет|тоннет|тэнет)", low)
            and not re.search(r"обслужива\w*[^.!?]{0,35}лада", low)
        )
        or re.search(r"не\s+(?:принима\w*|обслужива\w*)[^.!?]{0,80}лада", low)
        or re.search(
            r"(?:нива\w*|автомобил\w*)[^.!?]{0,55}не\s+(?:принима\w*|обслужива\w*)",
            low,
        )
    )
    if not wrong_line:
        return False
    return bool(
        re.search(r"\bлада\b", low)
        or re.search(r"\b(?:веста|грант\w*|ларгус|нива|хрей)\b", low)
        or _xcite_brand_mentioned(low)
    )


def _is_brand_service_refusal(low: str) -> bool:
    """
    Чужой бренд (Jetour / Hyundai / Exeed / ...) + явный отказ дилера в обслуживании
    («не обслуживаем», «больше не дилер», «не принимаем» и т.п.) → НЕ ТО, НЕ запись.
    Слова «техническое обслуживание» в речи диспетчера здесь — про отказ, не про запись.
    """
    has_other_brand = jetour_brand_mentioned(low) or _contains_any(low, _OTHER_BRAND_EXACT)[0]
    if not has_other_brand:
        return False
    if not _sto_service_refusal_phrase_hit(low):
        return False
    if jetour_brand_mentioned(low) and _jetour_limitation_but_regulatory_to_booking_agreed(low):
        return False
    return True


def _current_kia_over_past_nissan_history(low: str) -> bool:
    """19102: текущая Kia важнее Nissan из истории прежнего обслуживания."""
    head = (low or "")[:2600]
    current_kia = bool(
        re.search(
            r"\bна\s+kia\b[^.!?]{0,100}\b(?:помен\w*|замен\w*|обслуж\w*|ремонт\w*)|"
            r"\b(?:сейчас|теперь)\b[^.!?]{0,100}\bпересел\w*\b[^.!?]{0,45}\bkia\b",
            head,
            re.IGNORECASE,
        )
    )
    past_nissan = bool(
        re.search(
            r"\b(?:раньше|ранее|до\s+этого)\b[^.!?]{0,120}\bnissan\b|"
            r"\bна\s+nissan\b[^.!?]{0,100}\b(?:был|обслужив\w*|заезжал\w*)",
            head,
            re.IGNORECASE,
        )
    )
    return current_kia and past_nissan


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[0-9a-zа-я+-]{3,}", text, flags=re.IGNORECASE)


def _best_fuzzy(token_list: List[str], targets: Iterable[str]) -> Tuple[float, str, str]:
    best_ratio = 0.0
    best_token = ""
    best_target = ""
    for tok in token_list:
        for tgt in targets:
            ratio = SequenceMatcher(None, tok, tgt).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_token = tok
                best_target = tgt
    return best_ratio, best_token, best_target


def _is_civilian_documents_mfc_inquiry_not_auto_service(low: str) -> bool:
    """
    17265: МФЦ / «Мои документы» / статус паспорта — не автосервис;
    «то дело №», «то есть» в речи оператора — не регламентное ТО.
    """
    head = (low or "")[:4500]
    if not any(
        p in head
        for p in (
            "мои документ",
            "мои доокумент",
            "мфц",
            "оказания услуг",
            "ходе рассмотрения ваших документов",
            "контакт-центра мфц",
            "предварительно записаться на прием документ",
            "предварительно записаться на приём документ",
        )
    ):
        return False
    if any(
        p in low
        for p in (
            "диспетчер сервиса",
            "викинги",
            "автомобил",
            "машин",
            "ниссан",
            "чери",
            "записаться на то",
            "запись на то",
        )
    ):
        return False
    if re.search(r"\bтехническ\w+\s+обслуживан", low, re.I):
        return False
    if re.search(r"\bобслуживан\w*\s+автомобил", low, re.I):
        return False
    return True


def _to_intent_retracted_non_reg_work(low: str) -> Optional[Tuple[str, str, str]]:
    """
    Клиент стартует с намерения «на ТО», но далее явно переключается на другой
    тип работ в этом же звонке: диагностика/кузовной/прочие.
    """
    head = (low or "")[:5200]
    m = re.search(
        r"\b(?:хотел\w*|собирал\w*|планировал\w*|думал\w*)\b[^.!?]{0,120}\bна\s+то\b"
        r"[^.!?]{0,120}\b(?:но|однако|а\s+потом|потом|все[-\s]?таки|всё[-\s]?таки|"
        r"решил\w*|передумал\w*)\b",
        head,
        re.I,
    )
    if not m:
        return None

    tail = head[m.end() : m.end() + 1200]
    if not tail.strip():
        return None

    if (
        _is_body_shop_service_intake(tail)
        or _is_insurance_claim_body_inspection_intake(tail)
        or re.search(r"\b(?:кузов\w*|окрас\w*|покрас\w*|бампер\w*|крыл\w*|двер\w*)\b", tail, re.I)
    ):
        return ("body_shop", "body_shop", "to_retracted_to_body_shop")

    if (
        _is_explicit_scheduled_diagnostics_intake(tail)
        or _is_primary_defect_diagnostic_service_intake(tail)
        or _is_planned_diagnostics_required(tail)
        or _diagnostics_booking_intake_phrase_present(tail)
        or re.search(r"\b(?:диагност\w*|посмотр\w*|провер\w*|стук\w*|шум\w*)\b", tail, re.I)
    ):
        return ("diagnostics", "diagnostics", "to_retracted_to_diagnostics")

    if (
        _is_wheel_alignment_service_intake(tail)
        or _is_wheel_balancing_service_intake(tail)
        or _is_accessory_install_price_quote_intake(tail)
        or _is_accessory_alarm_equipment_service_intake(tail)
        or _is_arrived_interior_part_replacement_booking_intake(tail)
        or _is_brake_pad_replacement_booking_intake(tail)
        or any(marker in tail for marker in _OIL_SERVICE_WITHOUT_SCHEDULED_TO_MARKERS)
        or re.search(
            r"\b(?:масл\w*\s+замен\w*|замен\w*\s+масл\w*|ремонт\w*|колодк\w*|"
            r"фильтр\w*|сход[-\s]?развал|балансир\w*|зеркал\w*)\b",
            tail,
            re.I,
        )
    ):
        return ("other_work", "other_work", "to_retracted_to_other_work")
    return None


def _declined_to_price_quote_without_booking(low: str, evidence: Dict[str, Any]) -> bool:
    """
    Клиент запросил цену ТО, но отказался («мне не подходит/дороговато»),
    а подтверждения записи на слот в звонке нет.
    """
    if (evidence.get("work_intent") or "") != "to_price_quote":
        return False
    head = (low or "")[:2600]
    has_decline = bool(
        re.search(
            r"\b(?:мне|нам)\s+не\s+(?:подход\w*|устраива\w*)\b|"
            r"\bне\s+подходит\b|\bне\s+устраивает\b|\bдороговат\w*\b",
            head,
            re.I,
        )
    )
    if not has_decline:
        return False
    has_booking_confirmation = bool(
        re.search(
            r"\b(?:вас\s+)?записал[аи]\w*\b|"
            r"\b(?:давайте\s+запиш\w*|запишем|внес\w*\s+в\s+запис)\b|"
            r"\b(?:будем\s+ожидать|подъезжайте|накануне\s+(?:позвон\w*|напомн\w*))\b",
            head,
            re.I,
        )
    )
    return not has_booking_confirmation


def infer_sto_booking_dimensions(transcript_text: str) -> Dict[str, Any]:
    """
    Возвращает словарь:
      service_brand: chery_tenet | nissan | other_brand
      work_type: to | warranty | diagnostics | body_shop | quality_check | other_work
      is_booking: bool
      confidence: high | medium | low
      evidence: dict
    """
    low = _normalize_text(transcript_text or "")
    evidence: Dict[str, Any] = {}

    # 1) Вид работ (приоритет): to > warranty > diagnostics > body_shop > quality_check > other_work.
    # Если линия диспетчера назначает визит («запис…», день недели, время) — это запись на сервис/ТО,
    # даже при теме «диагностика» (не переводим вид работ в «диагностика»).
    # Отдельное короткое «то» в речи часто не про ТО; при симптомах + «посмотреть» это диагностика.
    has_explicit_to, exp_to_hit = contains_any_to_marker_hit(low)
    low_for_bare = _mask_spurious_to_for_service(low)
    bare_particle_to = (not has_explicit_to) and bool(
        re.search(r"(?:^| )то(?: |$)", low_for_bare)
    )
    _diag_inspect_verbs = (
        "посмотреть",
        "посмотрите",
        "посмотрели",
        "осмотр",
        "осмотреть",
        "осмотрели",
        "диагностик",
        "проверить",
        "разобраться",
        "разберемся",
        "послушать",
        "глянуть",
        "глянули",
        "посмотрим",
    )
    # «Ходовую / подвеску посмотреть» — осмотр подвески, в карточке = диагностика (не плановое ТО).
    diag_suspension_walkthrough = (
        any(x in low for x in ("ходов", "подвеск"))
        and any(p in low for p in _diag_inspect_verbs)
        and not _regulatory_to_price_or_composition_context(low)
    )
    diag_steering_chassis_symptom = _chassis_steering_symptom_service_intake(low)
    diag_sunroof_electrical = _sunroof_or_electrical_trim_defect_service_intake(low)
    diag_strong = (
        diag_suspension_walkthrough
        or diag_steering_chassis_symptom
        or diag_sunroof_electrical
        or (
            (
                any(
                    p in low
                    for p in (
                        "подвеск",
                        "посторонние звук",
                        "посторонние звуки",
                        "посторонний шум",
                        "посторонние шум",
                        "стук",
                        "шум в подвеск",
                        "шумы в подвеск",
                    )
                )
                or ("шум" in low and "подвеск" in low)
            )
            and any(p in low for p in _diag_inspect_verbs)
        )
    )

    has_warranty, warranty_hit = _contains_any(low, _WARRANTY_MARKERS)

    has_oil_without_scheduled_to = (not has_explicit_to) and any(
        p in low for p in _OIL_SERVICE_WITHOUT_SCHEDULED_TO_MARKERS
    )
    # Смета регламентного ТО у диспетчера («ТО большое», «замена масла» в составе ТО) — не отдельная замена (9163).
    if has_oil_without_scheduled_to and (
        strong_scheduled_to_signal_present(low)
        or _regulatory_to_price_or_composition_context(low)
        or _is_inbound_opening_regulatory_to_booking(low)
        or re.search(r"\bто\s+больш", low)
        or re.search(r"\bбольш\w*\s+то\b", low)
        or re.search(r"\bрегламент\w*[^.!?]{0,80}\bбольш\w*\s+то\b", low)
        or re.search(r"\bчетверт\w+\s+то\b", low)
        or re.search(r"\bчетвёрт\w+\s+то\b", low)
        or re.search(r"\bпланировал\w*\s+работ\w*\s+там\s+то\b", low, re.I)
    ):
        has_oil_without_scheduled_to = False

    has_opening_reg_to_booking = _is_inbound_opening_regulatory_to_booking(low)
    has_existing_slot_clarification = _has_existing_appointment_clarification(low)
    has_booking_verify = (
        any(p in low for p in _BOOKING_VERIFICATION_TOPIC_MARKERS)
        or bool(re.search(r"записан[аы]?\s+на\s+то\s+на\s+как", low))
        or has_existing_slot_clarification
        or _has_pre_visit_booking_confirmation(low)
    )
    if has_opening_reg_to_booking:
        has_booking_verify = bool(
            re.search(r"записан[аы]?\s+на\s+то\s+на\s+как", low)
            or _has_pre_visit_booking_confirmation(low)
        )

    has_diag_topic = any(t in low for t in _DIAGNOSTICS_TOPIC_MARKERS)
    _has_weekday_or_slot = bool(
        re.search(
            r"(понедельник|вторник|среду|четверг|пятниц|суббот|воскресенье|"
            r"на\s+9[\s:\.]30|на\s+10[\s:\.]00|во\s+сколько|на\s+десять|на\s+девять)",
            low,
        )
    )
    has_dealer_service_booking = (
        ("диспетчер сервиса" in low or "ассистент сервиса" in low)
        and (
            any(p in low for p in _DIAG_BOOKING_OR_SCHEDULE_HINTS)
            or _has_weekday_or_slot
        )
    )
    wants_explicit_diagnostics = (
        has_diag_topic
        and not has_explicit_to
        and not has_dealer_service_booking
        and (
            any(p in low for p in _DIAG_BOOKING_OR_SCHEDULE_HINTS)
            or any(p in low for p in _DIAG_SYMPTOM_MARKERS)
        )
    )

    if _is_warranty_parts_arrived_replacement_coordination(low):
        work_type = "warranty"
        has_to = False
        evidence["work_hit"] = "warranty"
        evidence["work_intent"] = "warranty_parts_replacement"
    elif _is_warranty_decision_document_followup(low):
        work_type = "warranty"
        has_to = False
        evidence["work_hit"] = "warranty"
        evidence["work_intent"] = "warranty_decision_document"
    elif _is_inbound_warranty_defect_consultation_not_scheduled_to(low):
        work_type = "warranty"
        has_to = False
        evidence["work_hit"] = "warranty"
        evidence["work_intent"] = "warranty_consultation"
    elif _is_inbound_post_to_visit_signal_horn_defect_intake(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "repair"
        evidence["work_intent"] = "post_to_visit_signal_repair"
    elif _is_inbound_post_to_visit_battery_check_diagnostic_intake(low):
        work_type = "diagnostics"
        has_to = False
        evidence["work_hit"] = "diagnostics"
        evidence["work_intent"] = "post_to_visit_battery_check_diagnostics"
    elif _is_existing_to_warranty_repair_routing_not_new_booking(low):
        work_type = "warranty"
        has_to = False
        evidence["work_hit"] = "warranty"
        evidence["work_intent"] = "warranty_existing_visit_clarification"
    elif _is_inbound_warranty_repair_booking_intake(low):
        work_type = "warranty"
        has_to = False
        evidence["work_hit"] = "warranty"
        evidence["work_intent"] = "warranty_repair_booking"
    elif _is_inbound_existing_to_service_visit_cancel_or_reschedule(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "booking_cancellation_or_reschedule"
        evidence["work_intent"] = "existing_visit_clarification"
    elif _is_employment_recruitment_inquiry_not_narrow_to(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "employment_inquiry"
        evidence["work_intent"] = "employment_recruitment"
    elif _is_own_parts_to_eligibility_consultation_not_narrow_to(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "own_parts_to_eligibility"
        evidence["work_intent"] = "own_parts_to_eligibility_consultation"
    elif _is_to_official_dealer_eligibility_consultation_not_narrow_to(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "to_dealer_eligibility"
        evidence["work_intent"] = "to_official_dealer_eligibility_consultation"
    elif _is_post_visit_body_panel_fit_complaint(low):
        work_type = "body_shop"
        has_to = False
        evidence["work_hit"] = "body_shop"
        evidence["work_intent"] = "post_visit_body_panel_fit_complaint"
    elif _is_past_to_followup_without_new_to_booking(low):
        # 17539: «были недавно на ТО» + follow-up без новой записи — Прочие, не ТО/не warranty из «инженер по гарантии».
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "past_to_followup"
        evidence["work_intent"] = "past_to_visit_followup"
    elif _is_past_to_brake_wear_followup_not_narrow_to(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "past_to_brake_wear_followup"
        evidence["work_intent"] = "past_to_visit_followup"
    elif _is_immediate_repeat_continuation_without_new_slot(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "immediate_repeat_continuation"
        evidence["work_intent"] = "existing_visit_clarification"
    elif _is_civilian_documents_mfc_inquiry_not_auto_service(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "civilian_documents"
        evidence["work_intent"] = "documents_status_inquiry"
    elif _is_existing_to_duration_consultation_not_booking(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "booking_verification"
        evidence["work_intent"] = "existing_visit_clarification"
    elif _has_pre_visit_booking_confirmation(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "booking_verification"
        evidence["work_intent"] = "pre_visit_confirmation"
    elif _is_existing_zero_to_certificate_price_consultation_not_booking(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "booking_verification"
        evidence["work_intent"] = "existing_visit_clarification"
    elif _is_deferred_repair_after_past_to_visit_intake(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "repair"
        evidence["work_intent"] = "deferred_repair_after_to_visit"
    elif _is_existing_to_accessory_followup_not_booking(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "booking_verification"
        evidence["work_intent"] = "existing_visit_clarification"
    elif _is_oil_change_only_request(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "oil_change"
        evidence["work_intent"] = "oil_change"
    elif _is_body_shop_service_intake(low) or _is_insurance_claim_body_inspection_intake(low):
        work_type = "body_shop"
        has_to = False
        evidence["work_hit"] = "body_shop"
        evidence["work_intent"] = (
            "insurance_claim_inspection"
            if _is_insurance_claim_body_inspection_intake(low)
            else "body_shop"
        )
    elif _is_brake_pad_replacement_booking_intake(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "brake_pads"
        evidence["work_intent"] = "brake_pad_replacement"
    elif _is_arrived_interior_part_replacement_booking_intake(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "replacement"
        evidence["work_intent"] = "parts_replacement"
    elif _is_accessory_install_price_quote_intake(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "accessory_install"
        evidence["work_intent"] = "accessory_install_quote"
    elif _is_accessory_alarm_equipment_service_intake(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "alarm_accessory"
        evidence["work_intent"] = "accessory_alarm_service"
    elif _is_warranty_to_scope_consultation_not_regulatory_to_booking(low):
        work_type = "other_work"
        has_to = False
        if re.search(r"\bподушк\w*[^.!?]{0,25}\bсиден\w*\b", low, re.I):
            evidence["work_hit"] = "replacement"
            evidence["work_intent"] = "parts_replacement"
        else:
            evidence["work_hit"] = "warranty_to_scope_consultation"
            evidence["work_intent"] = "warranty_to_scope_consultation"
    elif _is_recall_software_update_consultation_without_to_booking(low):
        work_type = "warranty" if _is_recall_update_warranty_context(low) else "other_work"
        has_to = False
        evidence["work_hit"] = "recall_software_update_consultation"
        evidence["work_intent"] = "recall_update_consultation_without_to_booking"
    elif _is_service_campaign_hardware_recall_without_regulatory_to(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "service_campaign_recall"
        evidence["work_intent"] = "service_campaign_recall_without_regulatory_to"
    elif _is_non_to_issue_with_incidental_to_reference_not_booking(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "incidental_to_reference_non_booking_issue"
        evidence["work_intent"] = "non_to_issue_with_incidental_to_reference"
    elif _is_spare_parts_department_inquiry_intake(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "parts_lookup"
        evidence["work_intent"] = "spare_parts_inquiry"
    elif _repair_work_price_quote_is_primary_not_regulatory_to(low) and not re.search(
        r"\b(?:хочу|хотел[аи]?|нужно|надо)\b[^.!?]{0,80}\b(?:запис\w*|сделать)\b[^.!?]{0,25}\bто\b",
        low,
        re.I,
    ):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "repair_quote_primary"
        evidence["work_intent"] = "repair_quote_primary"
    elif _is_inbound_to_history_crm_consultation_not_narrow_to(low):
        # 16976/14346: до сметы ТО — иначе «техническое обслуживание»/«первое то» в истории даёт work_type to.
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "crm_to_history"
        evidence["work_intent"] = "crm_to_history_consultation"
    elif _is_wheel_balancing_service_intake(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "wheel_balancing"
        evidence["work_intent"] = "wheel_balancing"
    elif _is_primary_defect_diagnostic_service_intake(low):
        work_type = "diagnostics"
        has_to = False
        evidence["work_hit"] = "diagnostics"
        evidence["work_intent"] = "diagnostics"
    elif _is_wheel_alignment_service_intake(low):
        # 16676/18962: сход-развал — прочие работы, не диагностика и не ложное ТО.
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "wheel_alignment"
        evidence["work_intent"] = "wheel_alignment"
    elif _admin_salon_no_service_reception_callback(low) and not (
        _regulatory_to_price_or_composition_context(low)
        and _is_inbound_opening_regulatory_to_booking(low)
    ):
        # 17418: админ без приёмки — заявка/перезвон; вид работ не ТО (диагностика выше по цепочке).
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "admin_callback_no_reception"
        evidence["work_intent"] = "admin_callback_no_reception"
    elif _is_component_presence_regulation_consultation_without_booking(low):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "component_specification"
        evidence["work_intent"] = "component_regulation_consultation"
    elif has_booking_verify and has_existing_slot_clarification and (
        re.search(r"\bзаписывал(?:и|а|о|ся|ись)?\b", low[:1500], re.I)
        or re.search(r"\bзаписал(?:ся|ись)\b", low[:1500], re.I)
        or re.search(r"\bзаписавал(?:ся|ись)\b", low[:1500], re.I)
        or re.search(r"\bтолько\s+(?:сейчас|что)\s+запис", low[:1500], re.I)
        or any(
            p in low[:1500]
            for p in (
                "в силе",
                "всё в силе",
                "все в силе",
                "да, вижу",
                "да вижу",
                "телефон",
                "поправили",
                "не тот назвал",
            )
        )
        or re.search(r"\bу меня\s+вопрос\b", low[:1500], re.I)
        or re.search(r"\bхотел\w*\s+уточнить\b", low[:1500], re.I)
        or re.search(
            r"\bна\s+(?:какое|какую)\s+(?:время|дату|день|число)\b",
            low[:1500],
            re.I,
        )
    ):
        # Слот уже есть (10880/13945/19234/19303) — важнее цены/состава ТО в хвосте; не новая запись.
        # «вы записаны» только в конце приёмки (10545) — не считаем (см. opening-only маркеры).
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "booking_verification"
        evidence["work_intent"] = "existing_visit_clarification"
    elif _to_intent_retracted_non_reg_work(low):
        wt, wh, wi = _to_intent_retracted_non_reg_work(low) or (
            "other_work",
            "other_work",
            "to_retracted_to_other_work",
        )
        work_type = wt
        has_to = False
        evidence["work_hit"] = wh
        evidence["work_intent"] = wi
    elif _regulatory_to_price_or_composition_context(low):
        work_type = "to"
        has_to = True
        evidence["work_hit"] = exp_to_hit or "regulatory_to_price_quote"
        evidence["work_intent"] = "to_price_quote"
    elif _is_past_to_admin_followup_not_narrow_to(low):
        if _is_past_to_service_record_correction_not_narrow_to(low) and _is_regulatory_to_timing_consultation_without_booking(low):
            # 27738: вопрос «когда делать первое ТО» — вид работ ТО, но без факта записи.
            work_type = "to"
            has_to = True
            evidence["work_hit"] = "past_to_service_record_correction"
            evidence["work_intent"] = "to_consultation_without_booking"
        else:
            work_type = "other_work"
            has_to = False
            if _is_past_to_service_record_correction_not_narrow_to(low):
                evidence["work_hit"] = "past_to_service_record_correction"
                evidence["work_intent"] = "past_to_service_record_correction"
            else:
                evidence["work_hit"] = "past_to_work_order_document"
                evidence["work_intent"] = "past_to_document_request"
    elif has_explicit_to and has_opening_reg_to_booking:
        work_type = "to"
        has_to = True
        evidence["work_hit"] = exp_to_hit
        evidence["work_intent"] = "to_booking"
    elif has_explicit_to and _regulatory_to_price_or_composition_context(low):
        # Цена/состав N-го ТО + запись на слот; «вы записаны» на кузовной — не other_work (10545).
        work_type = "to"
        has_to = True
        evidence["work_hit"] = exp_to_hit
        evidence["work_intent"] = "to_booking"
    elif has_booking_verify and not (
        has_explicit_to
        and (
            _is_to_slot_availability_inquiry_not_booking_verify(low)
            or _regulatory_to_price_or_composition_context(low)
            or _is_inbound_opening_regulatory_to_booking(low)
            or re.search(r"\bзаписал\w*\s", low, re.I)
        )
    ):
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "booking_verification"
        evidence["work_intent"] = (
            "existing_visit_clarification"
            if has_existing_slot_clarification
            else "booking_verification"
        )
    elif has_oil_without_scheduled_to:
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "oil_change"
        evidence["work_intent"] = "oil_change"
    elif _is_explicit_scheduled_diagnostics_intake(low):
        # 9283/15767: запись на диагностику — до has_explicit_to (ложное «техобслуживание» в доверенности).
        work_type = "diagnostics"
        evidence["work_hit"] = "diagnostics"
        evidence["work_intent"] = "diagnostics"
        has_to = False
    elif has_explicit_to:
        work_type = "to"
        has_to = True
        evidence["work_hit"] = exp_to_hit
    elif _is_planned_diagnostics_required(low):
        # 9512: запланировать / потребуется диагностика — только без явного ТО в тексте.
        work_type = "diagnostics"
        evidence["work_hit"] = "diagnostics"
        evidence["work_intent"] = "diagnostics"
        has_to = False
    elif wants_explicit_diagnostics:
        work_type = "diagnostics"
        evidence["work_hit"] = "diagnostics"
        evidence["work_intent"] = "diagnostics"
        has_to = False
    elif diag_strong and not has_explicit_to:
        work_type = "diagnostics"
        evidence["work_hit"] = "diagnostics"
        evidence["work_intent"] = "diagnostics"
        has_to = False
    elif _is_quality_check_visit_intake(low):
        work_type = "quality_check"
        has_to = False
        evidence["work_hit"] = "quality_check"
        evidence["work_intent"] = "quality_check_visit"
    elif has_warranty:
        work_type = "warranty"
        has_to = False
        evidence["work_hit"] = warranty_hit
    elif (
        bare_particle_to
        and not diag_strong
        and not _repair_parts_status_blocks_bare_particle_to(low)
        and not _diagnostics_booking_intake_phrase_present(low[:1500])
        and not _is_spare_parts_department_inquiry_intake(low)
        and not _is_accessory_alarm_equipment_service_intake(low)
        and not (
            re.search(r"\bбыл[аи]?\s+записан[аы]?\s+на\s+(?:сервис|то)\b", low, re.I)
            and re.search(r"\b(?:можно\s+поменять|ошибочно\s+записал|поправл|перенес)\b", low, re.I)
        )
    ):
        work_type = "to"
        has_to = True
        evidence["work_hit"] = "то"
    else:
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = ""

    # 2) Признак записи.
    is_booking = any(m in low for m in _BOOKING_MARKERS)
    # Важно: само наличие ТО-темы (цена/состав/регламент) не означает факт записи.
    # Признак записи выставляем только по явным маркерам/сигналам согласования слота.
    car_already_at_service = _car_already_at_service_detected(low)
    if car_already_at_service:
        is_booking = False
        work_type = "other_work"
        has_to = False
        evidence["work_hit"] = "car_at_service"
        evidence["work_intent"] = "car_pickup_status"
    elif (evidence.get("work_intent") or "") in (
        "existing_visit_clarification",
        "warranty_existing_visit_clarification",
    ):
        # 19303: «только сейчас записавался» / правка телефона — не новая запись в этом звонке.
        is_booking = False
    elif (evidence.get("work_intent") or "") == "recall_update_consultation_without_to_booking":
        is_booking = False
    elif (evidence.get("work_intent") or "") == "service_campaign_recall_without_regulatory_to":
        is_booking = False
    elif _has_pre_visit_booking_confirmation(low):
        is_booking = False
    elif _is_quality_check_visit_intake(low):
        is_booking = False
    elif _is_spare_parts_department_inquiry_intake(low):
        is_booking = False
        evidence["booking_intent"] = "spare_parts_lookup"
    elif _is_civilian_documents_mfc_inquiry_not_auto_service(low):
        is_booking = False
    elif _is_past_to_followup_without_new_to_booking(low):
        is_booking = False
    elif _is_past_to_brake_wear_followup_not_narrow_to(low):
        is_booking = False
    elif _is_deferred_to_scheduling_callback_without_booking(low):
        is_booking = False
        evidence["booking_intent"] = "deferred_until_schedule_known"
    elif _is_to_price_quote_deferred_by_client_without_slot(low, evidence):
        is_booking = False
        evidence["booking_intent"] = "deferred_until_schedule_known"
    elif _is_to_eligibility_or_guarantee_consultation_without_schedule(low):
        is_booking = False
        evidence["booking_intent"] = "to_eligibility_consultation_without_schedule"
    elif (evidence.get("work_intent") or "") == "to_consultation_without_booking":
        is_booking = False
        evidence["booking_intent"] = "to_consultation_without_booking"
    elif _declined_to_price_quote_without_booking(low, evidence):
        is_booking = False
        evidence["booking_intent"] = "price_quote_declined_no_booking"
    elif (evidence.get("work_intent") or "") == "to_price_quote":
        has_booking_confirmation = _has_to_price_quote_booking_confirmation(low)
        if not has_booking_confirmation:
            is_booking = False
            evidence["booking_intent"] = "to_consultation_without_booking"
    evidence["car_already_at_service"] = car_already_at_service
    # Отмена записи: подстрока «запис» из _BOOKING_MARKERS давала ложное «Да» на «отменяем запись».
    _appointment_cancel_signals = (
        any(
            p in low
            for p in (
                "отменяем запись",
                "отменить запись",
                "отменяю запись",
                "отмена записи",
                "отмену записи",
                "выписаться",
                "хотела выписаться",
                "тогда отменяем запись",
            )
        )
        or (
            "отмен" in low
            and "запис" in low
            # без «воскресенье»: дата вида «10 мая воскресенье» давала ложное совпадение с «отменится»
            and any(x in low for x in ("диагностик", "антифриз", "антрифриз"))
            # 9467/9409: диспетчер — «клиент запись отменил» (освободилось окно), не отмена визита звонящего.
            and not re.search(
                r"клиент\w*\s+.{0,45}(?:запис\w+.{0,30}отмен|отмен\w+.{0,30}запис)",
                low,
                re.I,
            )
        )
        or (
            re.search(r"\bотказаться\b", low)
            and ("запис" in low or re.search(r"\bто\b", low))
        )
        or "хотели бы отказаться" in low
        or ("отменяйте" in low and ("запис" in low or re.search(r"\bто\b", low)))
        or re.search(r"\bпо\s+поводу\s+отмен\w*\s+на\s+техобслужив", low, re.I)
        or _is_inbound_existing_to_service_visit_cancel_or_reschedule(low)
        or _is_inbound_warranty_defect_consultation_not_scheduled_to(low)
    )
    if _appointment_cancel_signals:
        from call_analytics.sto_to_rubric import (
            _dispatcher_third_party_slot_cancel_mention_not_client_intent,
            _inbound_first_to_regulatory_booking_intake,
        )

        if (
            _dispatcher_third_party_slot_cancel_mention_not_client_intent(low)
            or _inbound_first_to_regulatory_booking_intake(low)
        ):
            _appointment_cancel_signals = False
    # Условное «если что-то изменится, отменю» после согласования нового слота
    # — не фактическая отмена записи в текущем звонке (27519).
    if _appointment_cancel_signals:
        conditional_future_cancel = bool(
            re.search(r"\bесли\b[^.!?]{0,120}\bотмен\w*\b", low, re.I)
            and re.search(r"\b(?:измен\w*|не\s+получ\w*|не\s+смож\w*)\b", low, re.I)
        )
        confirmed_new_slot_now = bool(
            re.search(
                r"\b(?:давайте\s+запиш\w*|записываю|запишем|запишемся|"
                r"предварительно[^.!?]{0,90}\bзаписал\w*сь)\b",
                low,
                re.I,
            )
            and (
                re.search(
                    r"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|сред[ау]|четверг|"
                    r"пятниц\w*|суббот\w*|воскресень\w*)\b",
                    low,
                    re.I,
                )
                or re.search(r"\b\d{1,2}[-е]?\s*(?:числ\w*)\b", low, re.I)
                or re.search(r"\b(?:в|на)\s+\d{1,2}(?::\d{2})\b", low, re.I)
            )
        )
        if conditional_future_cancel and confirmed_new_slot_now:
            _appointment_cancel_signals = False
    if _appointment_cancel_signals:
        is_booking = False
        if _is_inbound_warranty_repair_booking_intake(low):
            evidence["booking_intent"] = "warranty_repair_booking"
        elif _is_inbound_warranty_defect_consultation_not_scheduled_to(low):
            evidence["booking_intent"] = "warranty_consultation"
        else:
            evidence["booking_intent"] = "cancellation"
    if (evidence.get("booking_intent") or "") == "cancellation":
        work_type = "other_work"
        evidence["work_intent"] = "existing_visit_clarification"
    if re.search(r"\bбыл[аи]?\s+записан[аы]?\s+на\s+(?:сервис|то)\b", low, re.I) and re.search(
        r"\b(?:можно\s+поменять|ошибочно\s+записал|поправл)\b",
        low,
        re.I,
    ):
        work_type = "other_work"
        is_booking = False
        evidence["work_hit"] = "booking_verification"
        evidence["work_intent"] = "existing_visit_clarification"

    # 3) Марка: Чери/Тенет — только после вопроса про авто или в теле разговора (не в приветствии дилера).
    # Раннее окно — только для чужих марок, явно названных в начале.
    early_brand_low = low[:700]
    early_has_other_exact, early_other_hit = _contains_any(early_brand_low, _OTHER_BRAND_EXACT)
    if jetour_brand_mentioned(early_brand_low):
        early_has_other_exact = True
        if not early_other_hit:
            early_other_hit = _jetour_brand_hit_token(early_brand_low)

    brand_low = _brand_focus_low(low, evidence)
    skip_early_window_exact = evidence.get("brand_zone") in (
        *_CLIENT_BRAND_PRIORITY_ZONES,
        "after_vehicle_mention",
        "after_greeting_body",
    )

    has_chery_exact, chery_hit = _contains_chery_tenet(brand_low)
    ownership_seg = _segment_client_ownership_brand(low)
    if not has_chery_exact:
        full_vehicle, full_vehicle_hit = _explicit_chery_tenet_vehicle_anywhere(low)
        ownership_blocks_full_chery = bool(
            ownership_seg
            and not _contains_chery_tenet(ownership_seg)[0]
            and (
                _contains_any(ownership_seg, _NISSAN_EXACT)[0]
                or _contains_any(ownership_seg, _OTHER_BRAND_EXACT)[0]
            )
        )
        if full_vehicle and not ownership_blocks_full_chery:
            has_chery_exact, chery_hit = True, full_vehicle_hit
            evidence["brand_full_text_vehicle_hit"] = full_vehicle_hit
            if evidence.get("brand_zone") in (None, "", "after_greeting_body"):
                evidence["brand_zone"] = "full_transcript_vehicle"
    has_nissan_exact, nissan_hit = _contains_nissan_for_client_brand(low, brand_low)
    if ownership_seg:
        own_ns, own_ns_hit = _contains_nissan_for_client_brand(low, ownership_seg)
        own_ch, own_ch_hit = _contains_chery_tenet(ownership_seg)
        own_other, own_other_hit = _contains_any(ownership_seg, _OTHER_BRAND_EXACT)
        if own_other and not own_ch:
            # 18187: «сейчас у меня Лиссян» — чужой бренд клиента важнее Chery/Nissan из истории ДЦ.
            has_other_exact_pre = True
            other_hit_pre = own_other_hit
            has_chery_exact, chery_hit = False, ""
            has_nissan_exact, nissan_hit = False, ""
        elif own_ns and not own_ch:
            has_nissan_exact, nissan_hit = True, own_ns_hit
            has_chery_exact, chery_hit = False, ""
            has_other_exact_pre = False
            other_hit_pre = ""
        elif own_ns and own_ch:
            # В ownership-сегменте берём явно обсуждаемую марку, а не просто «последнее вхождение».
            ns_disc = _explicit_brand_discussion_score(ownership_seg, _NISSAN_EXACT)
            ch_disc = _explicit_brand_discussion_score(ownership_seg, _CHERY_TENET_EXACT)
            if ns_disc >= ch_disc + 2:
                has_nissan_exact, nissan_hit = True, own_ns_hit
                has_chery_exact, chery_hit = False, ""
            elif ch_disc >= ns_disc + 2:
                has_chery_exact, chery_hit = True, own_ch_hit
                has_nissan_exact, nissan_hit = False, ""
            elif _last_variant_pos(ownership_seg, _NISSAN_EXACT) >= _last_variant_pos(
                ownership_seg, _CHERY_TENET_EXACT
            ):
                has_nissan_exact, nissan_hit = True, own_ns_hit
                has_chery_exact, chery_hit = False, ""
            else:
                has_chery_exact, chery_hit = True, own_ch_hit
                has_nissan_exact, nissan_hit = False, ""
            has_other_exact_pre = False
            other_hit_pre = ""
        else:
            has_other_exact_pre = False
            other_hit_pre = ""
    else:
        has_other_exact_pre = False
        other_hit_pre = ""
    has_other_exact, other_hit = _contains_any(brand_low, _OTHER_BRAND_EXACT)
    if has_other_exact_pre:
        has_other_exact, other_hit = True, other_hit_pre
    if jetour_brand_mentioned(low):
        jetour_in_brand_focus = jetour_brand_mentioned(brand_low)
        chery_in_client_zone = bool(
            has_chery_exact
            and evidence.get("brand_zone")
            in (
                *_CLIENT_BRAND_PRIORITY_ZONES,
                "after_vehicle_mention",
                "after_greeting_body",
                "crm_vehicle_passport",
            )
        )
        # 20987: в зоне ответа клиента «8 Про Макс» важнее шумового «Жтур» в другом фрагменте.
        if not (chery_in_client_zone and not jetour_in_brand_focus):
            has_other_exact = True
            if not other_hit:
                other_hit = _jetour_brand_hit_token(low)

    tokens = _tokenize(brand_low)
    ch_ratio, ch_tok, ch_tgt = _best_fuzzy(tokens, _CHERY_TENET_FUZZY_BASE)
    ns_ratio, ns_tok, ns_tgt = _best_fuzzy(tokens, _NISSAN_FUZZY_BASE)

    brand = "other_brand"
    confidence = "low"
    in_client_zone = evidence.get("brand_zone") in _CLIENT_BRAND_PRIORITY_ZONES or evidence.get(
        "brand_zone"
    ) in (
        "after_vehicle_mention",
        "after_greeting_body",
    )
    if in_client_zone and has_other_exact and not has_nissan_exact:
        # Явная модель Чери/Tenet в заявке (10675: «8 ТМакс») важнее ложного «лада» в «вклада».
        if has_chery_exact and (
            evidence.get("brand_full_text_vehicle_hit")
            or evidence.get("brand_zone") == "early_client_vehicle"
        ):
            brand = "chery_tenet"
            confidence = "high"
            evidence["brand_hit"] = chery_hit
            evidence["brand_method"] = "client_zone_chery_vehicle_over_false_other"
        else:
            # Клиент в ответ на вопрос «какой автомобиль?» назвал другую марку — она перебивает
            # «Чери» из приветствия дилера и любые упоминания моделей Чери в речи диспетчера.
            brand = "other_brand"
            confidence = "high"
            evidence["brand_hit"] = other_hit
            evidence["brand_method"] = "client_zone_exact_other_brand"
    elif (
        _contains_any(low, _OTHER_BRAND_EXACT)[0]
        and not any(s in low for s in _STRONG_CHERY_VEHICLE_MARKERS)
        and not any(v in low for v in _NISSAN_EXACT)
        and not has_chery_exact
    ):
        # Чужой бренд в разговоре (напр. «ДЦ Лада» при сравнении салонов) не перебивает
        # марку авто клиента в зоне ответа на «какой автомобиль?» (8439: Черетика 7).
        _, full_other_hit = _contains_any(low, _OTHER_BRAND_EXACT)
        brand = "other_brand"
        confidence = "high"
        evidence["brand_hit"] = full_other_hit
        evidence["brand_method"] = "full_text_exact_other_brand_dominates"
        evidence["brand_zone"] = "full_transcript"
    elif not skip_early_window_exact and early_has_other_exact:
        brand = "other_brand"
        confidence = "high"
        evidence["brand_hit"] = early_other_hit
        evidence["brand_method"] = "early_window_exact_other_brand"
        evidence["brand_zone"] = "early_window"
    elif has_other_exact and not has_chery_exact and not has_nissan_exact:
        brand = "other_brand"
        confidence = "high"
        evidence["brand_hit"] = other_hit
        evidence["brand_method"] = "exact_other_brand"
    elif (
        evidence.get("brand_zone") == "after_vehicle_mention"
        and has_chery_exact
        and not has_nissan_exact
        and not has_other_exact
    ):
        # 15767: «автомобиль 4га» — Chery в зоне авто важнее «Шкода» из CRM-уточнения.
        # 15562: Jetour Dashing в речи клиента — не Chery из приветствия дилера.
        brand = "chery_tenet"
        confidence = "high"
        evidence["brand_hit"] = chery_hit
        evidence["brand_method"] = "vehicle_mention_chery_over_crm_noise"
    elif has_other_exact and has_chery_exact and evidence.get("brand_zone") in (
        *_CLIENT_BRAND_PRIORITY_ZONES,
        "after_vehicle_mention",
        "after_greeting_body",
    ):
        # При конфликте брендов в клиентской зоне приоритет у содержательно обсуждаемой марки.
        other_disc = _explicit_brand_discussion_score(brand_low, _OTHER_BRAND_EXACT)
        ch_disc = _explicit_brand_discussion_score(brand_low, _CHERY_TENET_EXACT)
        if ch_disc >= other_disc + 2:
            brand = "chery_tenet"
            confidence = "high"
            evidence["brand_hit"] = chery_hit
            evidence["brand_method"] = "explicit_brand_discussion_priority_chery"
        else:
            # «по автомобилю Hyundai ...» / «у меня Лиссян» важнее дилерского «Викинги Чери»
            # и прошлого Nissan в истории обслуживания (18187).
            brand = "other_brand"
            confidence = "medium"
            evidence["brand_hit"] = other_hit
            evidence["brand_method"] = "exact_other_brand_over_chery_context"
    elif has_chery_exact and not has_nissan_exact:
        brand = "chery_tenet"
        confidence = "high"
        evidence["brand_hit"] = chery_hit
        evidence["brand_method"] = "exact"
    elif has_nissan_exact and not has_chery_exact:
        brand = "nissan"
        confidence = "high"
        evidence["brand_hit"] = nissan_hit
        evidence["brand_method"] = "exact"
    elif has_chery_exact and has_nissan_exact:
        full_vehicle_hit = (evidence.get("brand_full_text_vehicle_hit") or "").lower()
        # 28989: Infiniti = группа Nissan; не проигрывать позднему «здание Чери» / приветствию дилера.
        if re.search(r"\b(?:infiniti|infinity|инфинити|инфинит)\b", low, re.I) and not any(
            s in low for s in _STRONG_CHERY_VEHICLE_MARKERS
        ):
            brand = "nissan"
            evidence["brand_hit"] = nissan_hit or "infiniti"
            confidence = "high"
            evidence["brand_method"] = "infiniti_nissan_group_over_dealer_chery"
        elif any(k in full_vehicle_hit for k in ("тенет", "tenet", "чери", "chery", "tiggo")):
            # Явная модель Tenet/Chery в тексте клиента важнее раннего «кашкай»/Nissan-шума.
            brand = "chery_tenet"
            evidence["brand_hit"] = chery_hit or full_vehicle_hit
            confidence = "high"
            evidence["brand_method"] = "full_text_vehicle_hit_over_nissan_noise"
            evidence["brand_zone"] = "full_transcript_vehicle"
        else:
            # Универсальный приоритет: если одна марка явно обсуждается содержательно
            # (модель/кузов/год/VIN/запчасти), она важнее фонового упоминания другой.
            ch_disc = _explicit_brand_discussion_score(brand_low, _CHERY_TENET_EXACT)
            ns_disc = _explicit_brand_discussion_score(brand_low, _NISSAN_EXACT)
            if ns_disc >= ch_disc + 2:
                brand = "nissan"
                evidence["brand_hit"] = nissan_hit
                confidence = "high"
                evidence["brand_method"] = "explicit_brand_discussion_priority_nissan"
            elif ch_disc >= ns_disc + 2:
                brand = "chery_tenet"
                evidence["brand_hit"] = chery_hit
                confidence = "high"
                evidence["brand_method"] = "explicit_brand_discussion_priority_chery"
            else:
                # Конфликт: «Чери» в приветствии дилера + марка авто клиента (часто Nissan).
                # Счётчики подстрок завышают Чери (много синонимов в списке); надёжнее — последнее вхождение по группе.
                pos_ch = _last_variant_pos(brand_low, _CHERY_TENET_EXACT)
                pos_ns = _last_variant_pos(brand_low, _NISSAN_EXACT)
                if pos_ns > pos_ch:
                    brand = "nissan"
                    evidence["brand_hit"] = nissan_hit
                elif pos_ch > pos_ns:
                    brand = "chery_tenet"
                    evidence["brand_hit"] = chery_hit
                else:
                    ch_count = sum(1 for v in _CHERY_TENET_EXACT if v in brand_low)
                    ns_count = sum(1 for v in _NISSAN_EXACT if v in brand_low)
                    if ns_count > ch_count:
                        brand = "nissan"
                        evidence["brand_hit"] = nissan_hit
                    elif ch_count > ns_count:
                        brand = "chery_tenet"
                        evidence["brand_hit"] = chery_hit
                    else:
                        brand = "nissan"
                        evidence["brand_hit"] = nissan_hit
                confidence = "medium"
                evidence["brand_method"] = "exact_conflict_last_pos"
    else:
        # Fuzzy fallback.
        if ch_ratio >= 0.84 and ch_ratio > ns_ratio + 0.03:
            brand = "chery_tenet"
            confidence = "medium"
            evidence["brand_hit"] = ch_tok
            evidence["brand_method"] = "fuzzy"
            evidence["brand_target"] = ch_tgt
            evidence["brand_ratio"] = round(ch_ratio, 3)
        elif ns_ratio >= 0.84 and ns_ratio > ch_ratio + 0.03:
            brand = "nissan"
            confidence = "medium"
            evidence["brand_hit"] = ns_tok
            evidence["brand_method"] = "fuzzy"
            evidence["brand_target"] = ns_tgt
            evidence["brand_ratio"] = round(ns_ratio, 3)
        else:
            evidence["brand_method"] = "fallback_other"
            evidence["brand_ratio_chery"] = round(ch_ratio, 3)
            evidence["brand_ratio_nissan"] = round(ns_ratio, 3)

    if _current_kia_over_past_nissan_history(low):
        brand = "other_brand"
        confidence = "high"
        evidence["brand_hit"] = "kia"
        evidence["brand_method"] = "current_kia_over_past_nissan_history"
        evidence["brand_zone"] = "current_vehicle"

    # 10302: «свой Ниссан обслуживаю» — клиентская марка Nissan, не шум приветствия.
    if re.search(
        r"\b(?:свой\s+nissan|свой\s+ниссан|у\s+меня\s+nissan|у\s+меня\s+ниссан)\b",
        low,
        re.I,
    ):
        brand = "nissan"
        confidence = "high"
        evidence["brand_hit"] = "nissan"
        evidence["brand_method"] = "client_nissan_ownership_phrase"
        evidence["brand_zone"] = "client_ownership"

    # Марка авто в диалоге не названа — только «Чери» из приветствия/мусора STT → прочие.
    if brand in ("chery_tenet", "nissan") and not _service_brand_assignable_outside_greeting(
        low, brand, evidence
    ):
        brand = "other_brand"
        confidence = "low"
        evidence["brand_method"] = "suppress_greeting_only_dealer_intro"
    elif brand == "chery_tenet" and _suppress_chery_if_only_greeting_dealer_noise(low, evidence):
        brand = "other_brand"
        confidence = "low"
        evidence["brand_method"] = "suppress_greeting_only_no_vehicle_named"

    # Если нет явной записи и уверенность по бренду низкая — оставляем conservative fallback.
    if not is_booking and confidence == "low":
        brand = "other_brand"

    # Нулевое ТО / ТО-0 при записи или переносе — марка Chery/Tenet (модель в разговоре не обязательна).
    if (
        work_type == "to"
        and _zero_to_booking_topic_present(low)
        and not _is_wrong_department_vikingi_lada_redirect_not_service(low)
        and not _is_brand_service_refusal(low)
    ):
        brand = "chery_tenet"
        confidence = "high"
        evidence["brand_method"] = "zero_to_booking_chery_tenet_default"
        evidence["brand_hit"] = evidence.get("brand_hit") or "нулевое то"

    # 17077: гарантийный контекст без явной марки авто — Chery/Tenet (как у дилера).
    if (
        _warranty_context_chery_tenet_default_brand(low)
        and not _is_wrong_department_vikingi_lada_redirect_not_service(low)
        and not _is_brand_service_refusal(low)
        and not has_nissan_exact
        and not has_other_exact
        and not (has_chery_exact and confidence == "high")
    ):
        brand = "chery_tenet"
        confidence = "medium"
        evidence["brand_method"] = "warranty_context_chery_tenet_default"
        evidence["brand_hit"] = evidence.get("brand_hit") or "на гарантии"

    # Чужой бренд + явный отказ дилера: бренд — other_brand; запись в этом звонке не состоялась.
    # Вид работ от бренда не зависит и здесь не перезаписывается (25936: кузовной остаётся кузовным).
    # 18150/20940: если клиент просил ТО — только помечаем work_intent для узкого слоя.
    if _is_brand_service_refusal(low):
        is_booking = False
        brand = "other_brand"
        if _client_regulatory_to_booking_request_present(low) and (
            (work_type or "").strip().lower() == "to"
        ):
            evidence["work_intent"] = "to_despite_brand_service_refusal"
            evidence["work_hit"] = evidence.get("work_hit") or "to"
        evidence["brand_method"] = (
            evidence.get("brand_method") or ""
        ) + ("|" if evidence.get("brand_method") else "") + "brand_service_refusal"

    if _is_wrong_department_vikingi_lada_redirect_not_service(low):
        work_type = "other_work"
        is_booking = False
        brand = "other_brand"
        evidence["work_intent"] = "wrong_department_lada_redirect"
        evidence["brand_method"] = (
            evidence.get("brand_method") or ""
        ) + ("|" if evidence.get("brand_method") else "") + "wrong_department_lada_redirect"

    if _is_inbound_to_history_crm_consultation_not_narrow_to(low):
        work_type = "other_work"
        is_booking = False
        evidence["work_intent"] = "crm_to_history_consultation"

    if _is_own_parts_to_eligibility_consultation_not_narrow_to(low):
        work_type = "other_work"
        is_booking = False
        evidence["work_hit"] = "own_parts_to_eligibility"
        evidence["work_intent"] = "own_parts_to_eligibility_consultation"

    if (
        (evidence.get("work_intent") or "") == "existing_visit_clarification"
        and (evidence.get("work_hit") or "") != "warranty"
    ):
        work_type = "other_work"
        is_booking = False

    if _is_to_official_dealer_eligibility_consultation_not_narrow_to(low):
        work_type = "other_work"
        is_booking = False
        evidence["work_hit"] = "to_dealer_eligibility"
        evidence["work_intent"] = "to_official_dealer_eligibility_consultation"

    if _is_fluids_and_repair_specs_consultation_without_booking(low):
        work_type = "other_work"
        is_booking = False
        evidence["work_hit"] = "fluids_and_repair_specs_consultation"
        evidence["work_intent"] = "service_specs_consultation"

    if _is_warranty_decision_document_followup(low):
        work_type = "warranty"
        is_booking = False
        evidence["work_hit"] = "warranty"
        evidence["work_intent"] = "warranty_decision_document"

    if _is_past_to_admin_followup_not_narrow_to(low):
        if _is_past_to_service_record_correction_not_narrow_to(low) and _is_regulatory_to_timing_consultation_without_booking(low):
            work_type = "to"
            is_booking = False
            evidence["work_intent"] = "to_consultation_without_booking"
            evidence["work_hit"] = evidence.get("work_hit") or "past_to_service_record_correction"
        else:
            work_type = "other_work"
            is_booking = False
            if _is_past_to_service_record_correction_not_narrow_to(low):
                evidence["work_intent"] = "past_to_service_record_correction"
            else:
                evidence["work_intent"] = "past_to_document_request"

    from call_analytics.classify_by_transcript import _is_sto_no_service_assistant_connection_low
    from call_analytics.sto_to_rubric import (
        _inbound_regulatory_to_quote_and_booking_intake,
        _no_actual_reception_contact,
    )

    if _is_sto_no_service_assistant_connection_low(low) and not _inbound_regulatory_to_quote_and_booking_intake(low):
        if (
            work_type != "diagnostics"
            and evidence.get("work_hit") != "diagnostics"
            and evidence.get("work_intent") != "diagnostics"
        ):
            work_type = "other_work"
        is_booking = False
        if evidence.get("work_intent") != "diagnostics":
            evidence["work_intent"] = "admin_no_dispatcher_callback"
    elif _no_actual_reception_contact(low):
        is_booking = False
        if _is_primary_defect_diagnostic_service_intake(low):
            work_type = "diagnostics"
            evidence["work_hit"] = "diagnostics"
            evidence["work_intent"] = "diagnostics"

    if (
        _is_quality_check_visit_intake(low)
        and (evidence.get("work_intent") in ("past_to_visit_followup", "existing_visit_clarification", None))
    ):
        work_type = "quality_check"
        evidence["work_hit"] = "quality_check"
        evidence["work_intent"] = "quality_check_visit"

    return {
        "service_brand": brand,
        "work_type": work_type,
        "is_booking": bool(is_booking),
        "confidence": confidence,
        "evidence": evidence,
    }

