"""
Узкая классификация STO_TO_IN / STO_TO_OUT для реестра «Звонки» и оценки по скрипту записи на ТО.

Широкие типы STO_IN / STO_OUT задаёт classify_auto. Узкая рубрика СТО_ТО_* — при явной теме регламентного ТО
(запись на ТО, N-е ТО, стоимость ТО, сильные маркеры из sto_booking_dimensions и т.п.).
Если в том же звонке обсуждаются диагностика, гарантия, замена колодок и др. — при наличии маркеров записи/ТО
это остаётся СТО_ТО_*, а не «НЕ_ТО» (отсутствие узкой рубрики) из-за сопутствующих тем.
Приоритет: намерение регламентного ТО важнее гарантии, диагностики и прочих работ в одном звонке (18298).

Для исходящих — исключение «холодного» реактивного прозвона (outbound_cold_to), без узкой СТО_ТО-рубрики.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from call_analytics.sto_booking_dimensions import (
    _TO_MARKERS,
    _TO_ORDINAL_PHRASE_MARKERS,
    jetour_brand_mentioned,
    _sto_service_refusal_phrase_hit,
    _is_brand_service_refusal,
    _is_wrong_department_vikingi_lada_redirect_not_service,
    _repair_work_price_quote_is_primary_not_regulatory_to,
    _jetour_limitation_but_regulatory_to_booking_agreed,
    _has_pre_visit_booking_confirmation,
    _has_existing_appointment_clarification,
    _is_inbound_to_late_arrival_notice,
    _is_inbound_opening_regulatory_to_booking,
    _regulatory_to_price_or_composition_context,
    _stt_regulatory_to_price_quote_present,
    _mileage_to_interval_code_present,
    _is_inbound_warranty_repair_booking_intake,
    _is_prefilled_online_to_application_confirmation_not_narrow_to,
    _is_deferred_to_scheduling_callback_without_booking,
    _normalize_text,
    _mask_spurious_to_for_service,
    contains_any_to_marker_hit,
    crm_outbound_explicit_to_topic_present,
    _ordinal_before_to_stt_match_is_false_positive,
    explicit_to_marker_present,
    _ordinal_to_marker_is_service_history_reference,
    all_technical_service_mentions_are_past_visit_confirmation,
    past_to_reference_present,
    strong_scheduled_to_signal_present,
    _technical_service_mention_is_past_visit_reference,
    _technical_service_mention_is_gearbox_service_nomenclature,
    _technical_service_mention_is_third_party_referral,
    _technical_service_mention_is_brand_capability_not_to_booking,
    _is_primary_defect_diagnostic_service_intake,
    _is_inbound_online_technical_service_application_intake,
    _is_deferred_repair_after_past_to_visit_intake,
    _is_arrived_interior_part_replacement_booking_intake,
    _is_accessory_install_price_quote_intake,
    _is_accessory_alarm_equipment_service_intake,
    _is_inbound_to_history_crm_consultation_not_narrow_to,
    _is_past_to_work_order_document_request_not_narrow_to,
    _is_past_to_service_record_correction_not_narrow_to,
    _is_past_to_admin_followup_not_narrow_to,
    _is_own_parts_to_eligibility_consultation_not_narrow_to,
    _is_to_official_dealer_eligibility_consultation_not_narrow_to,
    _is_component_presence_regulation_consultation_without_booking,
    _is_employment_recruitment_inquiry_not_narrow_to,
    _is_past_to_followup_without_new_to_booking,
    _is_past_to_brake_wear_followup_not_narrow_to,
    _is_existing_to_warranty_repair_routing_not_new_booking,
    _work_type_aligned_when_narrow_not_to,
)

__all__ = [
    "infer_sto_to_rubric_type",
    "infer_sto_appointment_agreed",
    "explicit_narrow_sto_to_topic_hit",
]

# Дополнительно к _TO_MARKERS: стоимость, запись формулировками, прохождение ТО.
_NARROW_STO_TO_EXTRA_MARKERS = (
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
    "размер то",
    "пройти то",
    "провести то",
    "проходите то",
    "проходили то",
    "прошли то",
    "прохождение то",
    "сделать то",
    "на то сделать",
    "то сделать",
    "полное то",
    "комплексное то",
    "четвертое то",
    "четвёртое то",
    "записали на то",
    "записали вас на то",
    "записываю на то",
    "запишем на то",
    "запишу на то",
    "хочу записаться на то",
    "нужно записаться на то",
    "можно записаться на то",
    "предлагаю записаться на то",
    "на то записаться",
    "на то записать",
    "на то заехать",
    "заехать на то",
    "к вам на то",
    "хотел бы на то",
    "пригласить на то",
    "приглашаем пройти то",
    "пройти техническое обслуживание",
    "по регламенту то",
    "интервал то",
    "срок то",
    "по сроку то",
    # Нулевое ТО (частый сценарий первого сервисного визита).
    "нулевое то",
    "нулевое-то",
    "нолевое то",
    "нулевом то",
    "на нулевом то",
    "то-0",
    "то 0",
    # Исходящие напоминания о регламенте («второе ТО», без подряд «второе то» в STT).
    "второе техническое обслуживание",
    "третье техническое обслуживание",
    "первое техническое обслуживание",
    "второе техническое",
    "третье техническое",
    "первое техническое",
    "подходит второе",
    "подходит третье",
    "подходит первое",
    "приближается второе",
    "приближается третье",
    "планируете проходить",
    "планируете пройти",
    "будете проходить",
)

_NARROW_EXPLICIT_STO_TO_MARKERS: Tuple[str, ...] = tuple(
    dict.fromkeys(list(_TO_MARKERS) + list(_NARROW_STO_TO_EXTRA_MARKERS))
)

# В узком слое: «проходили/прошли то» без иных маркеров ТО — только воспоминание о прошлом визите.
_NARROW_PAST_COMPLETION_TO_MARKERS_ONLY = frozenset({"проходили то", "прошли то"})

# Исходящий «холодный» прозвон: если в том же тексте явный регламент ТО — узкую СТО_ТО_исх оставляем.
_COLD_OUTBOUND_NARROW_EXCEPTION = (
    "второе техническое обслуживание",
    "третье техническое обслуживание",
    "второе техническое",
    "третье техническое",
    "подходит второе",
    "подходит третье",
    "планируете проходить",
    "планируете пройти",
    "будете проходить",
    "приближается второе",
    "приближается третье",
)

# --- Исходящие: реактивация / «давно не были» (узкая рубрика СТО_ТО — не холодный реактив)
_COLD_MARKERS_STRICT = (
    "год назад",
    "месяцев назад",
    "месяца назад",
    "11 месяцев",
    "полгода назад",
    "давно не были",
    "давно не был",
    "давно у нас не",
    "планируете в этом году",
    "были у нас на то",
    "проходили то",
    "проходили у нас то",
    "обслуживались у нас",
    "проходили техническое",
)

_SERVICE_ANCHOR_FOR_COLD = (
    " то ",
    "то ",
    "техобслуживание",
    "техническое обслуживание",
    "на то",
    "сервис",
)


def _technical_service_mention_is_past_visit_not_new_booking(
    low: str, match_start: int, match_end: int
) -> bool:
    """
    Уже были на регламентном ТО (сегодня/вчера) — жалоба или доп. к записи, не новая СТО_ТО_вх (9476).
    """
    if _technical_service_mention_is_past_visit_reference(low, match_start, match_end):
        return True
    prefix = low[max(0, match_start - 95) : match_start]
    if re.search(r"\bпрош(?:ел|ёл|ла|ли)\b", prefix[-75:]):
        return True
    window = low[max(0, match_start - 150) : match_end + 80]
    if re.search(r"\b(?:была|был|были)\s+на\s*$", prefix[-55:]):
        return True
    if re.search(
        r"\b(?:истор\w+|открою|ноябр|апрел|декабр|март|самар|перед новым годом|раньше|заезжаете\s+на\s+то|на\s+то\s+\d{2,3})\b",
        window,
        re.I,
    ):
        return True
    if re.search(r"\b(?:были|был[аи]?)\s+.+\s+на\s*$", prefix[-85:]):
        return True
    head = low[: match_end + 8]
    after = low[match_end : match_end + 48].lstrip()
    # 10769: «на техническом обслуживании приезжал автомобиль» — прошлый визит, не новая запись.
    if re.match(r"приезжал", after, re.I):
        return True
    return bool(
        re.search(
            r"\b(?:сегодня|вчера)\s+(?:была|был|были)\s+на\s+техническ",
            head,
            re.I,
        )
    )


def _positive_regulatory_to_discussion_present(low: str, *, head_limit: Optional[int] = None) -> bool:
    """
    В звонке есть обсуждение прохождения/записи на регламентное ТО (не отрицается).
    """
    if _is_employment_recruitment_inquiry_not_narrow_to(low):
        return False
    chunk = (low or "") if head_limit is None else (low or "")[:head_limit]
    if not chunk.strip():
        return False
    if re.search(r"\bна\s+то\s+хот\w*\s+[^.!?]{0,50}запис", chunk, re.I):
        return True
    if re.search(r"\bна\s+то\b[^.!?]{0,80}?\bзапис", chunk, re.I):
        return True
    if explicit_narrow_sto_to_topic_hit(chunk)[0]:
        return True
    if strong_scheduled_to_signal_present(chunk) or contains_any_to_marker_hit(chunk)[0]:
        return True
    return False


def _regulated_to_discussion_explicitly_excluded(low: str) -> bool:
    """Клиент/диспетчер явно отсекают тему регламентного ТО (10568: «только про стекло»)."""
    head = (low or "")[:4500]
    return bool(
        re.search(r"\bбез\s+то\s+на\s+котор", head, re.I)
        or re.search(r"\bтолько\s+про\s+стекл", head, re.I)
        or re.search(
            r"\b(?:мы\s+)?(?:это\s+)?только\s+про\s+(?:стекл|замен|камер|запчаст|ремонт)\b",
            head,
            re.I,
        )
        or re.search(r"\bне\s+про\s+то\b", head, re.I)
        or re.search(r"\bговорим\s+только\s+про\b", head, re.I)
    )


_ADJACENT_SERVICE_DEPT_MARKERS = (
    "отдел допоборуд",
    "отдел доп оборуд",
    "допоборудован",
    "отдел запчаст",
    "отдел зч",
    "отдел запасных",
    "кузовной цех",
    "кузовной отдел",
    "мастер кузовн",
    "приёмщик кузовн",
    "приемщик кузовн",
    "отдел трейд",
    "отдел продаж трейд",
    "трейд-ин",
    "трейд ин",
    "trade-in",
    "trade in",
)

_CUSTOMER_SERVICE_DEPT_MARKERS = (
    "отдел по работе с клиентами",
    "отделом по работе с клиентами",
    "отдел по работе с клиентом",
    "отделом по работе с клиентом",
    "клиентская служба",
    "клиентскую службу",
    "клиентской службы",
    "клиентск службу",
)


def _customer_service_dept_opening_present(head: str) -> bool:
    """Открытие линии клиентской службы / отдела по работе с клиентом (17225)."""
    h = (head or "")[:2000]
    return any(p in h for p in _CUSTOMER_SERVICE_DEPT_MARKERS)


def _customer_service_legal_commercial_topic_present(low: str) -> bool:
    """Юридические / B2B аспекты: договор, юрист, коммерческое, ТЗ, НДС, норма-час."""
    h = (low or "")[:5000]
    legal_hits = 0
    for p in (
        "договор",
        "юрист",
        "коммерческ",
        "техзадани",
        "техническое задание",
        "норма час",
        "нормы часа",
        "нормочас",
        "без ндс",
        "с ндс",
        "отсрочка платежа",
        "служб безопасности",
        "служба безопасности",
    ):
        if p in h:
            legal_hits += 1
    if re.search(r"\bтз\b", h):
        legal_hits += 1
    return legal_hits >= 2


def _has_real_sto_dispatcher_assistant_booking_line_rubric(low: str) -> bool:
    """Реальная линия диспетчера/ассистента сервиса (не «диспетчеры пересылают»)."""
    return bool(
        re.search(r"\bдиспетчер\s+сервис", low or "")
        or re.search(r"\bассистент\s+сервис", low or "")
        or re.search(r"\bассистент-сервис", low or "")
    )


def _is_customer_service_legal_commercial_not_narrow_to(low: str) -> bool:
    """
    17225: клиентская служба / отдел по работе с клиентом — договор, ТЗ, коммерческое;
    нет перевода на диспетчера/ассистента для реальной записи — узкий НЕ_ТО.
    """
    if not (low or "").strip():
        return False
    if not _customer_service_dept_opening_present(low):
        return False
    if not _customer_service_legal_commercial_topic_present(low):
        return False
    if _has_real_sto_dispatcher_assistant_booking_line_rubric(low):
        return False
    return True


def _adjacent_department_opening_present(head: str) -> bool:
    """Сотрудник смежного отдела (не линия диспетчера сервиса) в начале разговора."""
    h = (head or "")[:2400]
    if not h.strip():
        return False
    if any(p in h for p in _ADJACENT_SERVICE_DEPT_MARKERS):
        return True
    return bool(
        re.search(r"\bкузовн\w*\s+(?:цех|отдел|мастер|приём|прием)", h, re.I)
        or re.search(r"\b(?:трейд|trade)[\s-]?ин\w*\s+(?:отдел|менеджер)", h, re.I)
    )


def _to_prebooked_by_dispatcher_before_adjacent_call(low: str) -> bool:
    """Регламентное ТО уже записано диспетчером; смежный отдел координирует свою работу."""
    head = (low or "")[:2600]
    keep_existing_to_slot = any(
        p in low
        for p in (
            "оставляйте по нулевому то",
            "оставьте по нулевому то",
            "всё и оставляйте по нулевому",
            "все и оставляйте по нулевому",
            "как и оставляйте по нулевому",
        )
    )
    if keep_existing_to_slot:
        return True
    dispatcher_handoff = bool(
        re.search(r"\bдиспетчер\s+сервис\w*\s+мне\s+передал", head, re.I)
        or re.search(r"\bдиспетчер\s+сервис\w*\s+передал", head, re.I)
        or re.search(r"\bмне\s+передал[аи]?\s+диспетчер\s+сервис", head, re.I)
    )
    to_already_booked = bool(
        re.search(r"\bзаписал\w*\s+на\s+то\b", head, re.I)
        or re.search(r"\b(?:вы|вас)\s+записал\w*\s+на\s+то\b", head, re.I)
        or re.search(r"\b(?:вы|вас)?\s*записал(?:ись|ся|ась)\s+на\s+то\b", head, re.I)
        or re.search(r"\bна\s+то\s+нулев\w*\s+запис", head, re.I)
        or re.search(r"\bзаписал(?:ись|ся|ась)?\s+на\s+нулев\w*\s+то\b", head, re.I)
        or re.search(r"\bзаписал(?:ись|ся|ась)?\s+на\s+(?:перв|втор|трет|четвер|четв[её]рт)\w*\s+то\b", head, re.I)
    )
    return dispatcher_handoff and to_already_booked


def _is_adjacent_dept_existing_to_coordination_not_narrow_to(low: str) -> bool:
    """
    Смежный отдел (допоборудование, запчасти, кузовной, трейд-ин…) звонит по своей теме;
    регламентное ТО уже записано диспетчером — координация допработ, не СТО_ТО_* (13398).
    """
    if not (low or "").strip():
        return False
    head = (low or "")[:2400]
    if not _adjacent_department_opening_present(head):
        return False
    return _to_prebooked_by_dispatcher_before_adjacent_call(low)


def _no_regulatory_to_discussion_not_narrow_to(low: str) -> bool:
    """
    Узкая СТО_ТО_* не применяется: нет обсуждения прохождения регламентного ТО.
    Перезвон по CRM-заявке на нерегламентную работу или явное «не про ТО» (10568, 9746).
    """
    if _positive_regulatory_to_discussion_present(low):
        return False
    if _regulated_to_discussion_explicitly_excluded(low):
        return True
    head = (low or "")[:2800]
    if any(m in head for m in _CRM_LEAD_CALLBACK_MARKERS):
        if not _positive_regulatory_to_discussion_present(low, head_limit=2800):
            return True
    if _non_reg_service_topic(head):
        return True
    return False


def _is_pure_to_price_inquiry_without_booking_not_narrow_to(low: str) -> bool:
    """
    «Сколько стоит ТО» без развития в запись:
    нет слотов/даты-времени, поиска клиента/авто, состава работ и оформления записи.
    Такой звонок — узкий НЕ_ТО (кейс 21643).
    """
    head = (low or "")[:4200]
    if not head.strip():
        return False
    has_price_to = bool(
        re.search(
            r"\b(?:сколько\s+стоит|стоимость|цена|цену|расценк\w*|прайс)\b[^.!?\n]{0,70}\b(?:на\s+)?то\b",
            head,
            re.I,
        )
    )
    if not has_price_to:
        return False
    # Конкретный регламент (ТО-0/ТО-1/ТО-60 000) не отсекаем.
    if _mileage_to_interval_code_present(head) or re.search(
        r"\b(?:то\s*[-]?\s*0|нулев\w+\s+то|"
        r"(?:перв|втор|трет|четвер|четв[её]рт|пят|шест|седьм|восьм|девят|десят)\w*\s+то)\b",
        head,
        re.I,
    ):
        return False
    has_booking_flow = bool(
        re.search(r"\bзапис(?:аться|ать|ыва\w*|ал[аиоы]?)\b", head, re.I)
        or re.search(
            r"\b(?:завтра|послезавтра|сегодня|понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|воскресень\w*)\b",
            head,
            re.I,
        )
        or re.search(r"\b(?:на|в)\s+\d{1,2}(?::|\.)\d{2}\b", head, re.I)
        or re.search(r"\b(?:приедете|подъедете|приезжайте|ожидаем)\b", head, re.I)
    )
    if has_booking_flow:
        return False
    has_owner_lookup = bool(
        re.search(
            r"\b(?:фамили\w*|по\s+фамили\w*|vin|вин\b|госномер|гос\s+номер|номер\s+автомобил\w*)\b",
            head,
            re.I,
        )
    )
    if has_owner_lookup:
        return False
    has_composition = bool(
        re.search(
            r"\b(?:что\s+входит|какие\s+работ\w*|перечень\s+работ|состав\s+работ|"
            r"регламент|объ[её]мн\w*\s+то|масл\w*|фильтр\w*|свеч\w*|жидкост\w*|колодк\w*)\b",
            head,
            re.I,
        )
    )
    if has_composition:
        return False
    return True


def _is_direct_to_cost_question_without_booking_not_narrow_to(low: str) -> bool:
    """
    Прямой вопрос «мне узнать стоимость» по ТО без оформления записи в этом звонке.
    21921: «шестое ТО. Мне узнать стоимость?» + консультация по регламенту, без слота/времени/оформления.
    """
    head = (low or "")[:4200]
    if not head.strip():
        return False
    has_direct_cost_question = bool(
        re.search(r"\bмне\s+(?:бы\s+)?узнать\s+стоимост\w*\b", head, re.I)
        or re.search(r"\b(?:можно|хотел[аи]?|скажите)[^.!?\n]{0,40}\bузнать\s+стоимост\w*\b", head, re.I)
    )
    if not has_direct_cost_question:
        return False
    # 9506: нулевое ТО — не относить к price-only НЕ_ТО.
    if re.search(r"\b(?:то\s*[-]?\s*0|нулев\w+\s+то)\b", head, re.I):
        return False
    # 19720: вопрос о своих расходниках после расчета ТО — сохраняем узкую СТО_ТО.
    if re.search(
        r"\b(?:со\s+сво\w+\s+(?:расходник\w*|запчаст\w*|фильтр\w*|материал\w*)|"
        r"сво\w+\s+(?:расходник\w*|запчаст\w*|фильтр\w*|материал\w*))\b",
        head,
        re.I,
    ):
        return False
    has_to_topic = bool(contains_any_to_marker_hit(head)[0] or _regulatory_to_price_or_composition_context(head))
    if not has_to_topic:
        return False
    has_booking_flow = bool(
        re.search(r"\bзапис(?:аться|ать|ыва\w*|ал[аиоы]?)\b", head, re.I)
        or re.search(
            r"\b(?:завтра|послезавтра|сегодня|понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|воскресень\w*)\b",
            head,
            re.I,
        )
        or re.search(r"\b(?:на|в)\s+\d{1,2}(?::|\.)\d{2}\b", head, re.I)
        or re.search(r"\b(?:приедете|подъедете|приезжайте|ожидаем)\b", head, re.I)
    )
    if has_booking_flow:
        return False
    has_owner_lookup = bool(
        re.search(
            r"\b(?:фамили\w*|по\s+фамили\w*|vin|вин\b|госномер|гос\s+номер|номер\s+автомобил\w*)\b",
            head,
            re.I,
        )
    )
    if has_owner_lookup:
        return False
    return True


def _is_preparatory_to_price_quote_without_booking_not_narrow_to(low: str, work_intent: str) -> bool:
    """
    Подготовительный запрос цены ТО без оформления записи в текущем звонке.
    22155: «заранее, чтобы подготовиться» + «буду готовиться», без слотов/оформления.
    """
    if (work_intent or "").strip().lower() != "to_price_quote":
        return False
    head = (low or "")[:4200]
    if not head.strip():
        return False
    has_prep_context = bool(
        re.search(r"\bзаранее\b", head, re.I)
        or re.search(r"\bчтобы\s+подготов", head, re.I)
        or re.search(r"\bбуду\s+готовить(?:ся)?\b", head, re.I)
        or re.search(r"\bеще\s+\d{1,3}\s*000\s*км\b", head, re.I)
    )
    if not has_prep_context:
        return False
    has_booking_flow = bool(
        re.search(r"\bзапис(?:аться|ать|ыва\w*|ал[аиоы]?)\b", head, re.I)
        or re.search(
            r"\b(?:завтра|послезавтра|сегодня|понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|воскресень\w*)\b",
            head,
            re.I,
        )
        or re.search(r"\b(?:на|в)\s+\d{1,2}(?::|\.)\d{2}\b", head, re.I)
        or re.search(r"\b(?:приедете|подъедете|приезжайте|ожидаем)\b", head, re.I)
    )
    if has_booking_flow:
        return False
    has_owner_lookup = bool(
        re.search(
            r"\b(?:фамили\w*|по\s+фамили\w*|vin|вин\b|госномер|гос\s+номер|номер\s+автомобил\w*)\b",
            head,
            re.I,
        )
    )
    return not has_owner_lookup


def _has_minimum_sto_to_markers_for_narrow(
    low: str,
    sto_dims: Dict[str, Any],
    *,
    has_to_interest: bool,
    min_markers: int = 3,
    allow_without_progress_marker: bool = False,
) -> bool:
    """
    Продуктовое правило: СТО_ТО_* только при >= 3 маркерах сценария ТО.
    Дополнительно нужен хотя бы один «прогресс-маркер»:
    - идентификация владельца
    - обсуждение дня/времени визита
    - явная запись на ТО
    """
    ev = sto_dims.get("evidence") if isinstance(sto_dims.get("evidence"), dict) else {}
    brand_zone = (ev.get("brand_zone") or "").strip().lower()
    brand_model_marker = brand_zone in (
        "client_ownership",
        "after_car_question",
        "after_vehicle_mention",
        "early_client_vehicle",
        "crm_vehicle_passport",
        "client_maintenance_request",
    )
    owner_marker = bool(
        re.search(
            r"\b(?:на\s+кого\s+оформлен\w*|фамили\w+\s+владел\w*|"
            r"владел\w*[^.!?]{0,35}(?:автомобил\w*|машин\w*)|"
            r"оформлен\w*[^.!?]{0,35}(?:на\s+кого|на\s+вас)|"
            r"фамили\w+\s+собственник\w*)\b",
            low,
            re.I,
        )
    )
    price_marker = bool(
        re.search(
            r"\b(?:сколько\s+стоит|стоимост\w*|цен[ауые]?)\b"
            r"[^.!?]{0,100}\b(?:то|техническ\w+\s+обслуживан\w*|техобслуж)\b",
            low,
            re.I,
        )
        or re.search(
            r"\b(?:то|техническ\w+\s+обслуживан\w*|техобслуж)\b"
            r"[^.!?]{0,100}\b(?:сколько\s+стоит|стоимост\w*|цен[ауые]?)\b",
            low,
            re.I,
        )
    )
    composition_marker = bool(
        re.search(
            r"\b(?:включа\w*|состав\w*|переч\w+\s+работ|"
            r"замен\w+\s+масл\w*|масл\w*[^.!?]{0,20}меня\w*|"
            r"фильтр\w+[^.!?]{0,40}(?:салон\w+|воздуш\w+|маслян\w*)|"
            r"общ\w*\s+осмотр\w*)\b",
            low,
            re.I,
        )
    )
    _date_num_token = r"(?:[12]?\d|3[01])(?:\s*-\s*(?:е|го)|(?:-?(?:е|го)))?"
    _date_word_token = (
        r"(?:перв(?:ое|ого)|втор(?:ое|ого)|треть(?:е|его)|четверт(?:ое|ого)|четвёрт(?:ое|ого)|"
        r"пят(?:ое|ого)|шест(?:ое|ого)|седьм(?:ое|ого)|восьм(?:ое|ого)|девят(?:ое|ого)|"
        r"десят(?:ое|ого)|одиннадцат(?:ое|ого)|двенадцат(?:ое|ого)|тринадцат(?:ое|ого)|"
        r"четырнадцат(?:ое|ого)|пятнадцат(?:ое|ого)|шестнадцат(?:ое|ого)|семнадцат(?:ое|ого)|"
        r"восемнадцат(?:ое|ого)|девятнадцат(?:ое|ого)|двадцат(?:ое|ого)|"
        r"двадцать\s+перв(?:ое|ого)|двадцать\s+втор(?:ое|ого)|двадцать\s+треть(?:е|его)|"
        r"двадцать\s+четверт(?:ое|ого)|двадцать\s+четвёрт(?:ое|ого)|двадцать\s+пят(?:ое|ого)|"
        r"двадцать\s+шест(?:ое|ого)|двадцать\s+седьм(?:ое|ого)|двадцать\s+восьм(?:ое|ого)|"
        r"двадцать\s+девят(?:ое|ого)|тридцат(?:ое|ого)|тридцать\s+перв(?:ое|ого))"
    )
    schedule_marker = bool(
        re.search(
            r"\b(?:на\s+какое\s+время|на\s+какую\s+дату|во\s+сколько|когда\s+можно\s+пройти)\b",
            low,
            re.I,
        )
        or re.search(r"\bк\s+\d{1,2}\s+час(?:ам|а)?\b", low, re.I)
        or re.search(
            r"\b(?:на|в)\s+\d{1,2}[а-яё]{1,4}\b[^.!?]{0,40}\b(?:запис\w*|время|окно|подойдет|подойд[её]т)\b",
            low,
            re.I,
        )
        or re.search(
            r"\b(?:запис\w*|время|окно|подойдет|подойд[её]т)\b[^.!?]{0,40}\b(?:на|в)\s+\d{1,2}[а-яё]{1,4}\b",
            low,
            re.I,
        )
        or (
            re.search(
                r"\b(?:понедельник|вторник|сред[ау]|четверг|пятниц\w*|суббот\w*|воскресень\w*|"
                r"сегодня|завтра|послезавтра)\b",
                low,
                re.I,
            )
            and re.search(r"\b(?:на|в)\s+\d{1,2}(?:[:.]\d{2}|\s+\d{2})\b", low, re.I)
        )
        # Дата слота числом + время: «23-е число ... 11:30», «22-го ... в 18:00».
        or re.search(
            rf"\b(?:на|в)?\s*{_date_num_token}\s*(?:числ\w*)?\b[^.!?]{{0,120}}"
            r"\b(?:в|на)?\s*\d{1,2}(?:[:.]\d{2})\b",
            low,
            re.I,
        )
        # Дата слота словами + время: «первого числа в 9:30», «тридцатого на 11:00».
        or re.search(
            rf"\b(?:на|в)?\s*{_date_word_token}\s*(?:числ\w*)?\b[^.!?]{{0,120}}"
            r"\b(?:в|на)?\s*\d{1,2}(?:[:.]\d{2})\b",
            low,
            re.I,
        )
        # «Ближайшая запись на понедельник» без конкретного времени.
        or re.search(
            r"\bближайш\w*\s+запис\w*\b[^.!?]{0,100}\b"
            r"(?:понедельник|вторник|сред[ау]|четверг|пятниц\w*|суббот\w*|воскресень\w*|"
            r"сегодня|завтра|послезавтра)\b",
            low,
            re.I,
        )
    )
    booking_marker = bool(
        re.search(
            r"\b(?:хотел[аи]?\s+запис(?:аться|ать)|"
            r"хотел[аи]?\s+бы[^.!?]{0,30}\s+запис(?:аться|ать)|"
            r"запис(?:ать|аться|ыва\w*)\s+на\s+то|"
            r"записал[аи]?\s+вас\s+на|вас\s+записал[аи]?\w*|"
            r"давайте\s+запиш\w*|можем\s+записать\s+вас|"
            r"запиш(?:ите|и)\s*,?\s*(?:пожалуйста)?|"
            r"могл\w*\s+бы\s+вас\s+приглас\w*|"
            r"можем\s+вас\s+приглас\w*|"
            r"приглас\w*\s+вас)\b",
            low,
            re.I,
        )
    )
    markers_count = sum(
        (
            bool(has_to_interest),
            brand_model_marker,
            owner_marker,
            price_marker,
            composition_marker,
            schedule_marker,
            booking_marker,
        )
    )
    has_progress_marker = owner_marker or schedule_marker or booking_marker
    if markers_count < min_markers:
        return False
    if has_progress_marker:
        return True
    return bool(allow_without_progress_marker)


def _has_confirmed_to_slot_from_dims_signal(low: str, sto_dims: Dict[str, Any]) -> bool:
    """
    Подстраховка для шумного STT: если размерности уже уверенно распознали
    новую запись на ТО и в речи есть согласование даты/времени слота,
    сохраняем узкую рубрику STO_TO_IN.
    """
    if (sto_dims.get("work_type") or "").strip().lower() != "to":
        return False
    if not bool(sto_dims.get("is_booking")):
        return False
    head = (low or "")[:5200]
    has_slot_time = bool(
        re.search(
            r"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|воскресень\w*)\b",
            head,
            re.I,
        )
        and re.search(r"\b(?:(?:в|на)\s+)?(?:время\s+)?\d{1,2}(?:[:.]|\s)\d{2}\b", head, re.I)
    ) or bool(
        re.search(r"\b(?:\d{1,2}\s+август\w*|август\w*\s+\d{1,2})\b", head, re.I)
        and re.search(r"\b(?:время\s+)?\d{1,2}(?:[:.]|\s)\d{2}\b", head, re.I)
    )
    if not has_slot_time:
        return False
    slot_confirm = bool(
        re.search(r"\b(?:записал[аи]\w*|записыва\w*|записываем\w*|записываемся)\b", head, re.I)
        or re.search(r"\b(?:будем(?:\s+\w+){0,2}\s+ожидать|подъезжайте|подъедете)\b", head, re.I)
    )
    return slot_confirm


def _has_confirmed_outbound_to_booking_slot(low: str) -> bool:
    """
    Исходящий CRM-звонок: тема регламентного ТО + согласованный новый слот.
    Нужен как приоритет над сопутствующим диагностическим контекстом.
    """
    head = (low or "")[:6000]
    has_crm_lead = bool(
        re.search(r"\b(?:получили|получал\w*|оставлял\w*)\s+заявк\w*\b", head, re.I)
        or "по записи вашего автомобиля" in head
    )
    has_slot_date = bool(
        re.search(
            r"\b(?:\d{1,2}(?:-е|-го)?\s+числ\w*|"
            r"понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|воскресень\w*|"
            r"сегодня|завтра|послезавтра)\b",
            head,
            re.I,
        )
    )
    has_slot_time = bool(re.search(r"\b(?:время\s+)?\d{1,2}(?:[:.]\d{2}|\s+\d{2})\b", head, re.I))
    has_booking_confirm = bool(
        re.search(r"\b(?:записал[аи]\w*|запиш\w*\s+вас|вас\s+записыва\w*)\b", head, re.I)
    )
    return has_crm_lead and has_slot_date and has_slot_time and has_booking_confirm


def _has_confirmed_service_booking_slot(low: str) -> bool:
    """
    Подтвержденный слот визита: дата/день + время + фиксация «вас записали».
    Используется для приоритета ТО над сопутствующей заменой детали.
    """
    head = (low or "")[:7000]
    has_slot_date = bool(
        re.search(
            r"\b(?:\d{1,2}(?:-е|-го)?\s+(?:числ\w*|январ\w*|феврал\w*|март\w*|апрел\w*|ма[йя]\w*|"
            r"июн\w*|июл\w*|август\w*|сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)|"
            r"понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|воскресень\w*|"
            r"сегодня|завтра|послезавтра)\b",
            head,
            re.I,
        )
    )
    has_slot_time = bool(
        re.search(r"\b(?:время\s+)?\d{1,2}(?:[:.]\d{2}|\s+\d{2})\b", head, re.I)
    )
    has_booking_confirm = bool(
        re.search(
            r"\b(?:записал[аи]\w*|вас\s+запис\w*|запиш\w*\s+вас|подтвержда\w*\s+запис\w*)\b",
            head,
            re.I,
        )
    )
    return has_slot_date and has_slot_time and has_booking_confirm


def _is_inbound_parts_crm_history_consultation_not_narrow_to(low: str) -> bool:
    """
    12958: вх. консультация — CRM-история (когда меняли масло) + диски/колодки;
    «второе ТО» из прошлого, запись на ТО не оформлена.
    """
    if not (low or "").strip():
        return False
    crm_hist = any(
        p in low
        for p in (
            "история загрузилась",
            "историю откро",
            "по истории",
            "заказ наряд",
        )
    )
    parts_topic = (
        _brake_disk_topic_present(low) or "колодк" in low or "тормоз" in low
    ) and any(
        p in low for p in ("налич", "стоим", "замен", "4900", "9800", "4 900", "9 800")
    )
    consult = any(
        p in low[:2200]
        for p in (
            "проконсультир",
            "соедин",
            "когда последний раз",
            "можете соедин",
            "мастер",
        )
    )
    booking_done = bool(re.search(r"\bзаписал[аи]\w*\s+(?:вас\s+)?на\b", low))
    deferred = bool(re.search(r"\b(?:сориентир|подумаю)\w*\b", low[-900:]))
    if crm_hist and parts_topic and consult and not booking_done and deferred:
        return True
    return False


def _is_inbound_existing_to_visit_parts_question_not_narrow_to(low: str) -> bool:
    """
    Входящий: клиент уже записан на регламентное ТО; звонок — запчасти/детали к визиту (10549).
    Не новая СТО_ТО_вх., даже если в речи есть «техническое обслуживание» про завтрашний визит.
    """
    head = (low or "")[:3200]
    if not head.strip():
        return False
    existing_to = bool(
        re.search(r"\b(?:я|мы)\s+записан[аы]?\s+завтра\s+на\s+то\b", head, re.I)
        or re.search(r"\b(?:я|мы)\s+записан[аы]?\s+на\s+то\b", head, re.I)
        or re.search(r"\bна\s+то\s+записан", head, re.I)
        # 19234: «я на ТО записался 28-го».
        or re.search(r"\b(?:я|мы)\s+на\s+то\s+записал(?:ся|ись)\b", head, re.I)
        or re.search(r"\bна\s+то\s+записал(?:ся|ись)\b", head, re.I)
        or _inbound_existing_to_booking_fact_present(low, head_limit=3200)
    )
    if not existing_to:
        return False
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    if (
        _inbound_client_new_regulatory_to_booking_intake(low)
        or _regulatory_to_with_side_work_booking(low)
        or _inbound_regulatory_to_quote_and_booking_intake(low)
    ):
        return False
    # 10549: наконечники / отдел запчастей; не «рулевое управление» в составе регламентного ТО (11581).
    # 19234: вопрос про своё масло/расходники к уже существующему визиту.
    parts_ctx = any(
        p in low
        for p in (
            "наконечник",
            "запчаст",
            "отдел запчаст",
            "в наличии",
            "остатки",
            "остаток",
            "wildberries",
        )
    ) or bool(re.search(r"\bрулев\w*\s+наконечник", low, re.I)) or bool(
        re.search(
            r"\b(?:масл\w*\s+сво[еёи]|сво[еёи]\s+масл|привезт\w*[^.!?]{0,40}масл|"
            r"сво[иёе]\s+(?:расходник\w*|запчаст\w*|материал\w*))\b",
            low,
            re.I,
        )
    )
    return parts_ctx


def _is_inbound_existing_slot_addendum_not_narrow_to(low: str) -> bool:
    """
    Входящий: слот на дату уже есть; просят дописать/добавить работу к записи — НЕ_ТО (9476).
    """
    has_slot = bool(
        re.search(
            r"\bзаписал[аиоы]?\w*\s+(?:на\s+)?(?:\d|трет|четвер|перв|втор|июн|июл|август|сент)",
            low,
            re.I,
        )
        or re.search(r"\b(?:на\s+)?треть(?:е|ье|его)\s+числ", low, re.I)
    )
    if not has_slot:
        return False
    if any(
        p in low
        for p in (
            "добавлю",
            "допишем",
            "добавить",
            "заодно",
            "дополнить",
            "этот свист",
            "этот вопрос",
        )
    ):
        return True
    return bool(
        re.search(r"\b(?:сегодня|вчера)\s+была\s+на\s+техническ\w+\s+обслуживан", low, re.I)
        and re.search(r"\b(?:допишем|добавлю)\b", low)
    )


def _technical_service_mention_is_department_routing_not_to_booking(
    low: str, match_start: int, match_end: int
) -> bool:
    """
    «отдел … технического обслуживания» при переводе к мастеру / «сдал машину» — наименование линии, не запись на ТО (12199).
    """
    window = low[max(0, match_start - 120) : match_end + 90]
    prefix = low[max(0, match_start - 95) : match_start]
    if "отдел" not in prefix and "отдел" not in window[:90]:
        return False
    return any(
        p in window
        for p in (
            "сдал машин",
            "сдали машин",
            "соедин",
            "подключ",
            "мастер",
            "кузовной цех",
            "слесарн",
        )
    )


def _ordinal_to_match_is_service_history_reference(low: str, match: re.Match) -> bool:
    """
    «первое то было», «на гарантии … первое то» — прошлый визит, не запись на регламентное ТО (12202).
    16673: «две недели назад было на то» / «всё хорошо … на первом то» — история, не смета.
    """
    window = low[max(0, match.start() - 95) : match.end() + 95]
    wide = low[max(0, match.start() - 220) : match.end() + 80]
    if re.search(r"\bзапис(?:аться|ь)\s+на\s+(?:то|\d)", window, re.I):
        return False
    if re.search(
        r"\b(?:перв|втор|трет|четвер|четвёрт)\w*\s+то\s+(?:запис|надо|нужно|пройти|сделать)\b",
        window,
        re.I,
    ):
        return False
    if re.search(
        r"\b(?:недел\w*\s+назад|назад\s+было|было\s+на\s+то)\b",
        wide,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:вс[её]\s+хорошо|дали\s+заключен|заключен\w*)\b",
        window,
        re.I,
    ):
        return True
    return any(
        x in window
        for x in (
            "истори",
            "открою",
            "ноябр",
            "апрел",
            "декабр",
            "март",
            "раньше",
            "было",
            "была",
            "были",
            "был ",
            "делал",
            "делали",
            "меняли",
            "прошл",
            "обращал",
            "заезжал",
            "проходил",
            "прохожу",
            "запросил",
            "наряд",
            "2024",
            "2025",
            "гарант",
        )
    )


_ORDINAL_TO_MATCH_RE = re.compile(
    r"\b(?:нулев|нолев|перв|втор|трет|четвер|четвёрт|пят|шест|седьм?\w*|восьм|девят|десят)\w*(?:-\s*е)?\s+то\b",
    re.I,
)


def _mask_past_ordinal_to_history_mentions(low: str) -> str:
    """Глушит «N-е то» в контексте прошлого визита, чтобы не давать смету/состав ТО (16673)."""

    def _repl(m: re.Match) -> str:
        if _ordinal_to_match_is_service_history_reference(low, m):
            return " " * (m.end() - m.start())
        return m.group(0)

    return _ORDINAL_TO_MATCH_RE.sub(_repl, low or "")


def explicit_narrow_sto_to_topic_hit(transcript: str) -> Tuple[bool, str]:
    """
    Есть ли в тексте явная тема регламентного ТО (для узкой рубрики СТО_ТО_*).
    Использует ту же нормализацию, что и размерности СТО.
    """
    low = _normalize_text(transcript or "")
    if not low:
        return False, ""
    if _is_employment_recruitment_inquiry_not_narrow_to(low):
        return False, ""
    head_app = low[:1600]
    if _inbound_online_technical_service_application_intake(low):
        return True, "online_application_technical_service"
    # Морфология «техническое обслуживание»: ловим падежи/формы
    # (например, «к техническому обслуживанию» из STT).
    # Не считать узкой темой ТО фразу про будущий визит при текущем ремонте:
    # «до следующего технического обслуживания», «перед следующим техобслуживанием».
    for m in re.finditer(r"\bтехническ\w+\s+обслуживан\w+\b", low):
        prefix = low[max(0, m.start() - 95) : m.start()]
        pst = prefix.strip()
        if re.search(r"до\s+следующ\w+\s*$", pst) or re.search(
            r"перед\s+следующ\w+\s*$", pst
        ):
            continue
        if _technical_service_mention_is_third_party_referral(low, m.start(), m.end()):
            continue
        if _technical_service_mention_is_gearbox_service_nomenclature(low, m.start(), m.end()):
            continue
        if _technical_service_mention_is_past_visit_not_new_booking(low, m.start(), m.end()):
            continue
        if _technical_service_mention_is_department_routing_not_to_booking(low, m.start(), m.end()):
            continue
        if _technical_service_mention_is_brand_capability_not_to_booking(low, m.start(), m.end()):
            continue
        return True, "техническ* обслуживан*"
    # STT: «седь-е ТО», «перв-е то» (9760) и «N-е ТО» до слова «то».
    for m in _ORDINAL_TO_MATCH_RE.finditer(low):
        if _ordinal_to_match_is_service_history_reference(low, m):
            continue
        if _ordinal_before_to_is_phone_contact_context(low, m):
            continue
        if not _ordinal_before_to_stt_match_is_false_positive(low, m):
            return True, "ordinal_before_to"
    # 14073: «стоимость ТО» + «восьмой … необходимо сделать» / объёмное ТО с составом.
    head = low[:1600]
    if re.search(r"\b(?:сколько|стоимост|сориентир\w*|стоит).{0,80}?\bто\b", head, re.I):
        if re.search(
            r"\b(?:пят|шест|седьм|восьм|девят|десят)\w*\b[^.!?]{0,40}\b(?:необходимо|нужно)\s+сделать",
            low,
            re.I,
        ):
            return True, "to_price_ordinal_required"
        if re.search(r"\b(?:сколько|стоимост|стоит).{0,80}?\bто\s+\d{2,3}\s+000\b", low, re.I):
            return True, "to_price_mileage"
        if re.search(r"\b(?:меня(?:ет|ются)|меняется)\s+масл", low, re.I) and re.search(
            r"\b(?:объемн|объёмн)\w*\b", low, re.I
        ):
            return True, "volume_to_composition"
    # 16148: «регламент большого ТО» / «полностью всё ТО … 47 стоит» — смета регламентного ТО.
    if re.search(r"\bрегламент\w*[^.!?]{0,80}\bбольш\w*\s+то\b", head, re.I):
        return True, "regulatory_big_to_quote"
    if re.search(r"\bбольш\w*\s+то\b", head, re.I) and (
        "регламент" in head or re.search(r"\bполност\w*\s+[^.!?]{0,40}\s+то\b", head, re.I)
    ):
        return True, "big_regulatory_to_composition"
    if _stt_regulatory_to_price_quote_present(low):
        return True, "stt_to_digit_price_quote"
    # STT-перестановка: «на ТО третье/второе/первое» (вместо «третье ТО»).
    if re.search(r"\bто\s+(?:перв(?:ое|ый)|втор(?:ое|ой)|треть(?:е|ий))\b", low):
        return True, "то + ordinal"
    # STT: «мне ТО надо уже третий проходить» — не «третье ТО» (12032).
    head = low[:1600]
    if re.search(r"\bмне\s+то\s+надо\b", head, re.I) and re.search(
        r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+проход\w+\b",
        head,
        re.I,
    ):
        return True, "ordinal_prokhodit_to"
    # 18289: «Нулевой» + смета/защита двигателя в сервисном диалоге (после STT тело→то).
    if re.search(r"\bнулев\w*\s+то\b", head, re.I) and (
        "защита двигателя" in head
        or re.search(r"по\s+стоимости\s+(?:вас\s+)?сориентир", head, re.I)
        or re.search(r"\bнулев\w*\s+то\b[^.!?]{0,60}\d[\d\s]{1,}\s*(?:₽|руб)", head, re.I)
        or re.search(r"\bузнать\s+насч[её]т\s+сервис", head, re.I)
    ):
        return True, "zero_to_service_price_inquiry"
    # STT: «на 3- то необходимо записать» — цифра и дефис вместо слова «третье» (7958).
    if re.search(r"\bна\s+\d+\s*-\s*то\b", low):
        return True, "stt_digit_hyphen_ord_to"
    # «это необходимо записать» (12025) — не «ТО необходимо записать»; граница слова как в booking_dims.
    if re.search(r"\bто\s+необходимо\s+записать\b", low):
        return True, "to_need_register_vehicle"
    # 11441: заявка Автопартнёра на регламентное ТО + запись на слот.
    if re.search(r"\bавтопартн", low) and re.search(r"\bна\s+то\b", low) and re.search(
        r"\b(?:запис|то\s+подходит)\b", low, re.I
    ):
        return True, "autopartner_regulatory_to_booking"
    if re.search(r"\bто\s+подходит\b", low, re.I) and re.search(
        r"\b(?:30\s+000|\d{2}\s+000)\b", low
    ):
        return True, "to_package_price_confirmed"
    # 16743: «на ТО машину записать» + смета регламентного ТО на 30 000 км.
    if re.search(r"\bна\s+то\b[^.!?]{0,50}?\bзапис", head, re.I) and re.search(
        r"\bзапис", low, re.I
    ):
        return True, "na_to_booking_request"
    if (
        re.search(r"\b(?:30\s+000|на\s+30\s*0+)\b", low, re.I)
        and re.search(r"\bзамен\w*\s+масл", low, re.I)
        and re.search(r"\bзапис", low, re.I)
    ):
        return True, "mileage_to_package_booking"
    # 17212: «ТО-105» / «регламент ТО-75» — код регламентного ТО по пробегу (шаг 5 тыс. км).
    if _mileage_to_interval_code_present(low):
        return True, "mileage_to_interval_code"
    for m in _NARROW_EXPLICIT_STO_TO_MARKERS:
        if not explicit_to_marker_present(low, m):
            continue
        if m == "сделать то" and not _context_ok_for_sdelat_to(low, m):
            continue
        if m in ("техническое обслуживание", "техобслуживание", "тех обслуживание"):
            if all_technical_service_mentions_are_past_visit_confirmation(low):
                continue
            tech_ms = list(re.finditer(r"\bтехническ\w+\s+обслуживан\w+\b", low))
            if tech_ms and all(
                _technical_service_mention_is_department_routing_not_to_booking(
                    low, tm.start(), tm.end()
                )
                or _technical_service_mention_is_brand_capability_not_to_booking(
                    low, tm.start(), tm.end()
                )
                for tm in tech_ms
            ):
                continue
        if m in _NARROW_PAST_COMPLETION_TO_MARKERS_ONLY:
            if past_to_reference_present(low) and not strong_scheduled_to_signal_present(low):
                continue
        if m in _TO_ORDINAL_PHRASE_MARKERS and _ordinal_to_in_past_complaint_context(low):
            continue
        if m in _TO_ORDINAL_PHRASE_MARKERS and _ordinal_to_marker_is_service_history_reference(low, m):
            continue
        return True, m
    # 16673: прошлые «на первом то» не должны давать regulatory_to_price_or_composition.
    if _regulatory_to_price_or_composition_context(low):
        low_wo_past_ord = _mask_past_ordinal_to_history_mentions(low)
        if low_wo_past_ord == low or _regulatory_to_price_or_composition_context(
            low_wo_past_ord
        ):
            return True, "regulatory_to_price_or_composition"
    if re.search(r"\bто\s+будет\s+длиться\b", low, re.I):
        return True, "to_duration_at_visit"
    if re.search(r"\bпри\s+то\s+вс[её]\s+сдел", low, re.I):
        return True, "work_at_regulatory_to_visit"
    if re.search(r"\bто\s+необходимо\s+сделать\b", low):
        return True, "to_required"
    return False, ""


def _ordinal_before_to_is_phone_contact_context(low: str, m: re.Match) -> bool:
    """
    Шум STT при диктовке контактов: «телефон ... шесть девять семь ... то пятьдесят семь».
    Не считать это явной темой регламентного ТО.
    """
    start = max(0, m.start() - 140)
    end = min(len(low), m.end() + 140)
    window = low[start:end]
    has_contact_context = bool(
        re.search(
            r"\b(?:телефон|номер|прямой|позвоню|перезвоню|перевести|переведите)\b",
            window,
            re.I,
        )
    )
    if not has_contact_context:
        return False
    has_regulatory_to_context = bool(
        re.search(
            r"\b(?:запис\w*|стоим\w*|цен\w*|регламент\w*|пробег\w*|"
            r"техобслуж\w*|обслуживан\w*|работ\w*|масл\w*|фильтр\w*|свеч\w*)\b",
            window,
            re.I,
        )
    )
    return not has_regulatory_to_context


def _inbound_warranty_defect_signal(low: str) -> bool:
    """Дефект/жалоба для гарантийной ветки; «луж» не внутри «обслуживались» (13430)."""
    if re.search(r"\b(?:подкапыва|подтек)\w*", low, re.I):
        return True
    if re.search(r"\b(?:луж[аиеуюей]|луж\b)", low, re.I):
        return True
    if re.search(r"\bтеч(?:ет|ёт|ь)\b", low, re.I):
        return True
    return any(
        p in low
        for p in (
            "не работает",
            "дефект",
            "проблем",
            "посмотреть",
            "диагност",
            "фильтр крив",
            "панель прибор",
            "панельк",
            "люфт",
            "стук",
            "шум",
            "качать колес",
            "качать колёс",
            "неисправност",
        )
    )


def _is_inbound_warranty_complaint_booking_not_narrow_to(low: str) -> bool:
    """
    Вх.: гарантия / диагностика из‑за дефекта (подтекает масло, панель не работает и т.п.),
    без обсуждения прохождения регламентного ТО — узкий НЕ_ТО (12137, 12202, 12327, 12922).
    Запись на N-е ТО со сметой/слотом при обсуждении гарантийной отметки — СТО_ТО_вх (13380).
    Запрос цены N-го ТО при обсуждении отметки в книжке — СТО_ТО_вх (13430).
    Намерение ТО + гарантия/диагностика/прочее в одном звонке — приоритет у ТО (18298).
    """
    from call_analytics.sto_booking_dimensions import _regulatory_to_intent_takes_priority

    head = (low or "")[:4500]
    if not any(p in head for p in ("гарант",)):
        return False
    if _regulatory_to_intent_takes_priority(low):
        return False
    if _is_inbound_opening_regulatory_to_booking(low) or _inbound_client_new_regulatory_to_booking_intake(
        low
    ):
        return False
    # 25686: запись на регламентное ТО (слот/пробег/смета) в гарантийный период
    # остаётся СТО_ТО_вх; гарантийные претензии здесь сопутствующая тема.
    if _regulatory_to_with_side_work_booking(low):
        return False
    if _is_inbound_warranty_repair_booking_intake(low):
        return True
    warranty_line = any(
        p in low
        for p in (
            "инженер по гарантии",
            "гарантийным вопрос",
            "гарантийный случай",
            "гарантийный ремонт",
            "гарантийному ремонту",
            "гарантийного ремонта",
            "по гарантии",
            "на гарантия",
            "на гарантии",
            "записаться у вас по гарант",
            "записаться по гарант",
            "заканчивается гарант",
            "окончания гарант",
            "гарантия на машину",
            "не входит в гарант",
            "входит в гарант",
        )
    )
    defect = _inbound_warranty_defect_signal(low)
    # 13380: «записаться на ТО» / N-е ТО + смета — до гарантийного хвоста (подвеска, инженер).
    if re.search(
        r"\b(?:запис(?:аться|ь)\s+на\s+то|(?:перв|втор|трет|четвер|четвёрт|пят|шест)\w*\s+то\s+(?:запис|надо|нужно|пройти|сделать))\b",
        head[:2200],
        re.I,
    ):
        return False
    if re.search(
        r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест)\w*\s+то\b"
        r"[^.!?]{0,100}?\bхотел\w*\s+[^.!?]{0,30}?\bзапис",
        head[:2200],
        re.I,
    ):
        return False
    if _inbound_regulatory_to_quote_and_booking_intake(low) and re.search(
        r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест)\w*\s+техническ\w+\s+обслуживан",
        head[:2500],
        re.I,
    ):
        return False
    # 13430: смета/состав N-го ТО — не чистая гарантийная консультация.
    if _regulatory_to_price_or_composition_context(low) and (
        re.search(
            r"\b(?:сориентир\w*|стоимост\w*\s+то|по\s+стоимости\s+\d)",
            head[:1400],
            re.I,
        )
        or (
            re.search(
                r"\b(?:перв|втор|трет|четвер|четвёрт|нулев)\w*\s+то\b",
                head[:2800],
                re.I,
            )
            and not re.search(
                r"\b(?:перв|втор|трет|четвер|четвёрт|нулев)\w*\s+то\s+был",
                head[:2800],
                re.I,
            )
            and re.search(
                r"\b(?:подошл\w*|подход\w*|проход\w*|меняется|по\s+стоимости)\b",
                head[:2800],
                re.I,
            )
        )
    ):
        return False
    if warranty_line and defect:
        return True
    if strong_scheduled_to_signal_present(head[:2200]) and _regulatory_to_in_scheduled_combo_not_particle(
        head[:2200]
    ):
        return False
    if warranty_line and re.search(r"\b(?:инженер|диагност)[^.!?]{0,80}гарант", low):
        return True
    if any(
        p in head for p in ("заканчивается гарант", "окончания гарант", "крайний срок обращ")
    ) and defect and re.search(r"\b(?:диагност|люфт|качать)\w*", head, re.I):
        return True
    if re.search(r"\b(?:у меня|мне)\s+то\s+было\b", head[:1800], re.I) and defect and warranty_line:
        return True
    return False


def _regulatory_to_in_scheduled_combo_not_particle(head: str) -> bool:
    """
    «запис… то» в окне — регламентное ТО, не частица «-то» в «сейчасче-то» (12922).
    """
    masked = _mask_spurious_to_for_service(head or "")
    return bool(
        re.search(
            r"\b(?:запис(?:аться|ь)?\s+на\s+то|(?:перв|втор|трет|четвер|четвёрт|пят|шест)\w*\s+то\b|"
            r"стоимость\s+то|сколько[^.!?]{0,50}\bто\b)",
            masked,
            re.I,
        )
    )


def _is_inbound_master_car_at_service_followup_not_narrow_to(low: str) -> bool:
    """
    Вх.: перевод к мастеру, машина уже сдана; контрольный выезд / время приезда — не запись на регламентное ТО (12199).
    """
    head = (low or "")[:3200]
    if not head.strip():
        return False
    if _mileage_to_interval_code_present(low):
        return False
    if _is_inbound_opening_regulatory_to_booking(low) or _inbound_client_new_regulatory_to_booking_intake(
        low
    ):
        return False
    if re.search(
        r"\b(?:запис(?:аться|ь)\s+на\s+то|(?:перв|втор|трет|четвер|четвёрт)\w*\s+то\s+(?:запис|надо|нужно|пройти))\b",
        head[:2200],
        re.I,
    ):
        return False
    # 16146: «Первое ТО, сколько будет стоить?» — запрос цены регламентного ТО, не follow-up мастера.
    if re.search(
        r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то\b"
        r"[^.!?]{0,100}?\b(?:сколько|стоимост)",
        head[:2200],
        re.I,
    ):
        return False
    # 16798: «сколько стоит ТО-2» / «ТО-2 на 20 тысячах» — новая запись на регламентное ТО.
    if re.search(
        r"\b(?:сколько|стоимост|стоит).{0,100}?\bто\s*-\s*[0-9]\b",
        head[:2200],
        re.I,
    ) or re.search(
        r"\bто\s*-\s*[0-9]\b[^.!?]{0,120}?\b(?:сколько|стоимост|пробег|тысяч)",
        head[:2200],
        re.I,
    ):
        return False
    if (
        _regulatory_to_price_or_composition_context(low)
        and re.search(r"\bзапис", low, re.I)
        and (
            contains_any_to_marker_hit(low)[0]
            or re.search(r"\bто\s*-\s*[0-9]\b", low, re.I)
        )
    ):
        return False
    at_service = any(
        p in low
        for p in (
            "сдал машин",
            "сдали машин",
            "оставлял машин",
            "оставляли машин",
            "оставлял автомобиль",
            "оставляли автомобиль",
            "оставил автомобиль",
            "оставили автомобиль",
            "машина у нас",
            "авто у нас",
            "на ремонт",
            "по готовности автомобиля",
            "по готовности авто",
        )
    )
    if not at_service and "на сервисе" in low:
        # 16798: «был автомобиля на сервисе? — первый раз» — история, не статус визита.
        if re.search(r"\bпервый\s+раз\s+обращ", low, re.I):
            pass
        elif re.search(r"\bбыл\w*\s+автомобил", low, re.I):
            pass
        elif re.search(r"\bкак\s+пройти\s+на\s+сервис", low, re.I):
            pass
        elif re.search(r"\b(?:ранее|раньше)\b[^.!?]{0,50}на\s+сервис", low, re.I):
            pass
        elif re.search(r"\bбыл\w*\s+на\s+сервис", low, re.I):
            pass
        else:
            at_service = True
    master_handoff = any(
        p in head[:1800]
        for p in (
            "соедин",
            "подключ",
            "мастер",
            "кузовной цех",
            "слесарн",
            "отдел там техническ",
        )
    )
    followup = any(
        p in low
        for p in (
            "прокатил",
            "при приёмке",
            "при приемке",
            "ничего не проявил",
            "во сколько",
            "ожидать",
            "жду звон",
            "ждал звон",
            "клиентскую службу",
            "по работе с клиент",
            "запчаст",
            "подменн",
            "по готовност",
            "узнать по готов",
            "заканчивает работу",
            "наберёт",
            "наберет",
        )
    )
    # 16798: «контрольно-смотровые работы» в смете ТО — не контрольный выезд после ремонта.
    if not followup and "контрольн" in low:
        if not re.search(r"\bконтрольно[-\s]*смотров", low, re.I):
            followup = True
    if at_service and (master_handoff or followup):
        return True
    if master_handoff and followup and re.search(
        r"\bотдел\b[^.!?]{0,50}техническ\w+\s+обслужив",
        head[:1600],
    ):
        return True
    return False


def _is_inbound_dashcam_or_accessory_programming_not_narrow_to(low: str) -> bool:
    """
    13554: прописать видеорегистратор в Chery App / подключить регистратор — доп. работа, не СТО_ТО_вх.
    15825: установка брызговиков — отдел доп.оборудования, запрос цены, не СТО_ТО_вх.
    """
    if _is_accessory_install_price_quote_intake(low):
        return True
    if _is_accessory_alarm_equipment_service_intake(low):
        return True
    head = (low or "")[:4500]
    if not any(p in head for p in ("видеорегистр", "видео регистр", "регистратор")):
        return False
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    if _positive_regulatory_to_discussion_present(low):
        return False
    return any(
        p in head
        for p in (
            "пропис",
            "подключ",
            "аппаратур",
            "багажник",
            "ozon",
            "озон",
            "оригинальн",
            "чери апп",
            "cheri app",
        )
    ) or bool(re.search(r"\bч[еe]р\w*\s+девят", head, re.I))


def _is_inbound_chassis_noise_diagnostic_not_narrow_to(low: str) -> bool:
    """
    Вх.: стук колёс/ходовая, гарантия истекла, запись чтобы узнать причину — диагностика, не СТО_ТО_вх (9727).
    """
    head = (low or "")[:4500]
    complaint = any(
        p in head
        for p in (
            "стуч",
            "стук ",
            "стучит",
            "стучать",
            "стойк стабилиз",
            "стойку стабилиз",
            "колес начали",
            "колёса начали",
            "неровност",
        )
    )
    if not complaint:
        return False
    if _is_inbound_opening_regulatory_to_booking(low) or _inbound_client_new_regulatory_to_booking_intake(
        low
    ):
        return False
    if _regulatory_to_with_side_work_booking(low):
        return False
    if re.search(
        r"\b(?:запис(?:аться|ь)\s+на\s+то|(?:перв|втор|трет|четвер|четвёрт)\w*\s+то\s+запис)",
        head[:2200],
        re.I,
    ):
        return False
    if any(
        p in head
        for p in (
            "в чём причин",
            "в чем причин",
            "узнать",
            "спросить на сервис",
            "гарант",
            "стойк стабилиз",
        )
    ) and not _is_inbound_opening_regulatory_to_booking(low):
        return True
    return bool(complaint and "записаться" in head and "причин" in head)


def _is_inbound_primary_diagnostic_with_incidental_to_slot_not_narrow_to(low: str) -> bool:
    """
    Вх.: первичная жалоба/диагностика (панель, мультимедиа, стук, «непонятный звук»),
    а фраза про «техническое обслуживание» прозвучала как служебный вопрос диспетчера
    при подборе слота. Это НЕ_ТО в узком слое (22486).
    """
    head = (low or "")[:7000]
    opening = head[:2400]
    if _is_inbound_opening_regulatory_to_booking(low) or _inbound_client_new_regulatory_to_booking_intake(low):
        return False
    if re.search(
        r"\b(?:запис(?:аться|ать)\s+на\s+то|(?:перв|втор|трет|четвер|четвёрт)\w*\s+то\s+запис)",
        opening,
        re.I,
    ):
        return False
    primary_issue = bool(
        re.search(
            r"\b(?:на\s+панел\w*|мультимеди\w*|приборк\w*|непонятн\w*\s+звук|стук\w*|"
            r"кочк\w*|время\s+сбива\w*|время\s+отста\w*|разное\s+время)\b",
            head,
            re.I,
        )
    )
    if not primary_issue:
        return False
    diagnostic_flow = bool(
        re.search(
            r"\b(?:диагност\w*|подключить\s+прибор|найти\s+причин\w*|"
            r"посмотр(?:им|еть)\s+настро\w*|будем\s+искать)\b",
            head,
            re.I,
        )
    )
    if not diagnostic_flow:
        return False
    incidental_to_prompt = bool(
        re.search(
            r"\b(?:планиру\w*\s+техническ\w*\s+обслуживан\w*|заезд\s+на\s+сервис\w*)\b",
            head,
            re.I,
        )
    )
    has_slot_booking = bool(
        re.search(
            r"\b(?:записал\w*\s+вас|записыва\w*\s+вас)\b",
            head,
            re.I,
        )
        and re.search(
            r"\b(?:понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|воскресень\w*|\d{1,2}[- ]?е)\b",
            head,
            re.I,
        )
    )
    return incidental_to_prompt and has_slot_booking


def _is_inbound_oil_change_booking_with_incidental_to_reference_not_narrow_to(low: str) -> bool:
    """
    Вх.: первичный запрос на замену масла/масляного фильтра с записью на слот.
    Иногда в хвосте STT появляется чужой фрагмент про «техническое обслуживание»,
    который не должен переводить кейс в узкую СТО_ТО_вх (22536).
    """
    head = (low or "")[:2400]
    if not re.search(
        r"\b(?:запис(?:аться|ать)\s+на\s+замен\w*\s+масл\w*|"
        r"замен\w*\s+масл\w*[^.!?]{0,60}\b(?:в\s+двигател\w*|маслян\w*\s+фильтр))\b",
        head,
        re.I,
    ):
        return False
    if re.search(
        r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят|нулев)\w*\s+то\b",
        head,
        re.I,
    ):
        return False
    if re.search(r"\b(?:на\s+то\b|то\s+пройти|пройти\s+то)\b", head, re.I):
        return False
    has_oil_scope = bool(
        re.search(
            r"\b(?:только\s+масл\w*|маслян\w*\s+фильтр|в\s+двигател\w*\s+поменять)\b",
            low,
            re.I,
        )
    )
    has_slot_booking = bool(
        re.search(r"\b(?:девятнадцат\w*|восемнадцат\w*|семнадцат\w*|сред\w*)\b", low, re.I)
        and re.search(r"\b(?:11[:.\s]30|11[:.\s]00|на\s+11)\b", low, re.I)
        and re.search(r"\b(?:записал\w*\s+вас|поставлю|давайте\s+на)\b", low, re.I)
    )
    has_late_incidental_to = bool(
        re.search(
            r"\bзаписал\w*\s+чери\s+голосов\w+\s+помощник\w*[^.!?]{0,90}\bтехническ\w+\s+обслуживан\w+\b",
            low,
            re.I,
        )
    )
    return has_oil_scope and has_slot_booking and has_late_incidental_to


def _is_past_to_service_context(head: str) -> bool:
    """
    Прошлый визит на регламентное ТО — не «после 26-го … каie-то» (дата + STT «какие-то»).
    «были/были недавно/заезжали … на ТО» — признак прошлого, не намерение новой записи (17539).
    """
    if not (head or "").strip():
        return False
    from call_analytics.sto_booking_dimensions import _past_to_visit_phrase_present

    if _past_to_visit_phrase_present(head):
        return True
    if re.search(r"\b(?:у меня|мне)\s+то\s+было\b", head[:1800], re.I):
        return True
    if re.search(
        r"\bпроводил[аи]?\b[^.!?]{0,80}\b(?:в\s+)?то\s*-\s*[0-9]\b",
        head[:1800],
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:проходил[аи]?|были)\b[^.!?]{0,80}\b(?:перв|втор|трет|четвер|четвёрт)\w*\s+то\b",
        head,
        re.I,
    ):
        return True
    # 26481: «недавно на ТО записывалась», «по предыдущему заезду» — жалоба по прошлому визиту.
    if re.search(
        r"\bнедавн\w*[^.!?]{0,60}\bна\s+то\b[^.!?]{0,40}\bзаписывал[асьсяеи]*\b",
        head,
        re.I,
    ):
        return True
    if re.search(
        r"\bпо\s+предыдущ\w+\s+заезд\w*\b|\bпредыдущ\w+\s+заезд\w*\b",
        head,
        re.I,
    ):
        return True
    if re.search(
        r"\bпосле\b[^.!?]{0,50}\b(?:последн|прошл|этого|сделан|пройден)\w*\s+то\b",
        head,
        re.I,
    ):
        return True
    m = re.search(r"\bпосле\b[^.!?]{0,40}\bто\b", head, re.I)
    if not m:
        return False
    frag = (m.group(0) or "").lower()
    if re.search(r"\d", frag) or any(x in frag for x in ("двадц", "тридц", "числ", "-го")):
        return False
    if re.search(r"(?:ка|как)[a-zа-я]*-?\s*то\b", frag, re.I):
        return False
    return True


def _is_past_to_followup_without_new_to_booking_not_narrow_to(low: str) -> bool:
    """Обёртка: прошлый ТО без новой записи → узкий НЕ_ТО (17539)."""
    return _is_past_to_followup_without_new_to_booking(low)


# «ТО» звучит как история визитов из CRM, а не как предмет новой записи.
_HISTORICAL_TO_CONTEXT_MARKERS = (
    "предыдущ",
    "только что сделали",
    "только что делали",
    "уже сделали",
    "когда мы делали",
    "когда делали",
    "делали у вас",
    "в прошлый раз",
    "прошлый раз",
    "прошлом",
    "проходил",
    "было сделано",
)

_HISTORICAL_TO_MENTION_RE = re.compile(
    r"\bто\s*[-–]?\s*\d{1,2}\b"
    r"|\b(?:нулев|перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят)\w*\s+то\b"
    r"|\b(?:техническ\w+\s+обслуживан\w*|техобслуживан\w*)\b",
    re.IGNORECASE,
)


def _to_mention_is_historical_reference(low: str, start: int, end: int) -> bool:
    """Упоминание ТО попадает в окно рассказа о прошлых визитах."""
    window = (low or "")[max(0, start - 130) : end + 90]
    return any(p in window for p in _HISTORICAL_TO_CONTEXT_MARKERS)


def _is_historical_to_context_without_new_booking_not_narrow_to(low: str) -> bool:
    """
    25572: клиент записывается на ремонт заявленных дефектов, а «ТО»/«ТО-N» звучит
    только как история визитов из CRM («на предыдущем ТО», «только что сделали ТО»,
    «на ТО-2, когда мы делали»). Новой записи на регламентное ТО в звонке нет.
    """
    text = low or ""
    if not text.strip():
        return False
    # Явное намерение записи/сметы регламентного ТО снимает правило.
    if _is_inbound_opening_regulatory_to_booking(text):
        return False
    if _regulatory_to_price_or_composition_context(text):
        return False
    # «посмотрю по записи… на предыдущем ТО» — не намерение записи, поэтому
    # ловим только явные глагольные формы записи на ТО.
    if re.search(
        r"\bзапис(?:аться|ать|ыва\w*|ываюсь|ываемся)\b[^.!?]{0,30}\bна\b[^.!?]{0,15}\bто\b",
        text,
        re.I,
    ):
        return False
    if re.search(r"\bзапись\s+на\s+то\b|\bна\s+то\b[^.!?]{0,25}\bзапис", text, re.I):
        return False

    mentions = list(_HISTORICAL_TO_MENTION_RE.finditer(text))
    if not mentions:
        return False
    if not all(
        _to_mention_is_historical_reference(text, m.start(), m.end()) for m in mentions
    ):
        return False

    opening = text[:900]
    if not re.search(r"\bзапис\w*\b", opening, re.I):
        return False
    return bool(
        re.search(
            r"\bна\s+(?:ремонт\w*|диагностик\w*|осмотр\w*)\b",
            opening,
            re.I,
        )
        or re.search(
            r"\b(?:не\s+работает|не\s+срабатывает|неисправн\w*|дефект\w*|"
            r"поломк\w*|сломал\w*)\b",
            opening,
            re.I,
        )
    )


def _has_post_to_visit_complaint_signal(head: str) -> bool:
    """Жалоба/дефект после визита; «ничего нового не появилось» — не жалоба (14019)."""
    if not (head or "").strip():
        return False
    # Нейтральная регламентная формулировка («проверим рулевое управление»)
    # не должна считаться жалобой по маркеру «рулев».
    complaint_scope = re.sub(
        r"\bрулев\w*\s+управлен\w+\b",
        " ",
        head,
        flags=re.IGNORECASE,
    )
    if not re.search(r"\b(?:не\s+)?(?:ничего\s+(?:нов\w+\s+)?)?не\s+появил\w*\b", head, re.I):
        if re.search(r"\b(?:появил(?:ся|ась|ось|ись)|появил[аи]?)\b", head, re.I):
            return True
    # 19375: после выполненного ТО не вернули/не установили снятую деталь под капотом.
    if re.search(r"\bне\s+(?:доставил|положил|поставил)\w*\b", head, re.I):
        return True
    if re.search(r"\bобратно\b[^.!?]{0,25}\bне\s+поставил\w*\b", head, re.I):
        return True
    if re.search(r"\bзащит\w*\b[^.!?]{0,35}\b(?:нет|нету|отсутств\w*)\b", head, re.I):
        return True
    if re.search(r"\bкуда\s+дел(?:ся|ась|ось|ись)\b", head, re.I):
        return True
    markers = (
        "руль влево",
        "руль вправо",
        "тянет вправо",
        "тянет влево",
        "машину тянет",
        "развал вхож",
        "развал-схож",
        "развал схож",
        "чек горит",
        "стук",
        "неисправност",
        "люфт",
        "наконечник",
        "сигнал не работает",
        "не работает сигнал",
        "колокол",
        "не сигнализир",
        "сигнализац",
        # 26481: явная претензия к качеству прошлого визита.
        "отзыв",
        "свист",
        "скрип",
        "кондер",
        "кондиц",
        # 16969: претензия к качеству уже сделанного ТО (фильтры/запах/газы).
        "некачеств",
        "не устраивает",
        "вонь",
        "запах",
        "газы",
        "вся вонь",
    )
    # 20165: «ООО Рулевой» — название клиента, не жалоба на рулевое управление.
    if re.search(r"\bооо\s+рулев\w+\b", complaint_scope, re.I):
        pass
    # «Рулевое управление осмотрит механик» в регламентном перечислении не является жалобой.
    # Считаем жалобой только при явном дефектном контексте рядом с «рулев*».
    if re.search(
        r"\bрулев\w*(?:\s+управлен\w+)?\b[^.!?]{0,45}\b(?:тянет|увод|люфт|стук|бь[её]т|закусыв|неисправ)\w*",
        complaint_scope,
        re.I,
    ) or re.search(
        r"\b(?:тянет|увод|люфт|стук|бь[её]т|закусыв|неисправ)\w*\b[^.!?]{0,45}\bрулев\w*",
        complaint_scope,
        re.I,
    ):
        return True
    return any(p in complaint_scope for p in markers)


def _is_inbound_post_to_visit_complaint_not_narrow_to(low: str) -> bool:
    """
    Входящий после уже выполненного ТО: клиент описывает претензию/дефект
    («в пятницу ТО делал», «руль тянет», «развал-схождение») — это НЕ новая
    запись на регламентное ТО в узком слое.
    """
    head = (low or "")[:4500]
    if not head.strip():
        return False
    past_to = _is_past_to_service_context(head)
    complaint = _has_post_to_visit_complaint_signal(head)
    # 26487: открытие может содержать «на нулевое ТО», но дальше идёт
    # явная претензия после визита — это НЕ_ТО, а не новая запись на регламент.
    if _is_inbound_opening_regulatory_to_booking(low) and not (past_to and complaint):
        return False
    opening = (low or "")[:900]
    if re.search(
        r"\bзапис(?:аться|ать)\s+на\s+(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то\b",
        opening,
        re.I,
    ):
        return False
    # 15883: новая запись на N-е ТО по пробегу/времени — не жалоба после прошлого визита.
    if re.search(
        r"\b(?:время\s+)?подошл\w*[^.!?]{0,80}?\b(?:на\s+)?то\s*-\s*[0-9]\b",
        opening,
        re.I,
    ):
        return False
    if re.search(
        r"\bпо\s+(?:километраж|пробег)\w*[^.!?]{0,50}?\b(?:на\s+)?то\s*-\s*[0-9]\b",
        opening,
        re.I,
    ):
        return False
    if not past_to:
        return False
    gearbox_complaint = bool(
        (
            "короб" in head
            and any(
                p in head
                for p in (
                    "пинк",
                    "пина",
                    "подпин",
                    "толч",
                    "дерга",
                    "дёрга",
                    "задней передач",
                    "трогаешь",
                    "трогается",
                )
            )
        )
        or re.search(r"\bнадо\s+короб\w*\s+смотр", head, re.I)
    )
    warranty_ctx = any(p in head for p in ("гарантий", "по гарантии", "на гарантии"))
    if past_to and complaint and warranty_ctx:
        return True
    return complaint or gearbox_complaint


def _is_primary_new_to_then_tail_post_to_complaint(low: str) -> bool:
    """
    26507: сначала обсуждают новое регламентное ТО (цена/состав),
    затем в хвосте добавляют претензию по прошлому визиту.
    Такие звонки остаются в СТО_ТО.
    """
    head = (low or "")[:4500]
    if not head.strip():
        return False
    # Нужен явный текущий TO-контекст в начале разговора.
    opening = head[:1700]
    has_opening_new_to_scope = bool(
        re.search(
            r"\b(?:сколько|стоимост\w*|стоит)\b[^.!?]{0,120}\b(?:перв|втор|трет|четвер|четвёрт|"
            r"пят|шест|седьм|восьм|девят|десят|нулев)\w*\s+то\b",
            opening,
            re.I,
        )
        or re.search(
            r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят|нулев)\w*\s+то\b"
            r"[^.!?]{0,120}\b(?:сколько|стоимост\w*|стоит)\b",
            opening,
            re.I,
        )
        or re.search(
            r"\bто\b[^.!?]{0,30}\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят)\w*\b"
            r"[^.!?]{0,120}\b(?:сколько|стоимост\w*|стоит)\b",
            opening,
            re.I,
        )
        or _regulatory_to_price_or_composition_context(opening)
    )
    if not has_opening_new_to_scope:
        return False
    if not _is_past_to_service_context(head):
        return False
    # Претензия должна появляться позже основной TO-консультации.
    tail = head[1400:]
    if not tail.strip():
        return False
    if _has_post_to_visit_complaint_signal(tail):
        return True
    return bool(
        re.search(
            r"\b(?:стуч\w*|свист\w*|скрип\w*|грохот\w*|вибрац\w*|шум\w*|пина\w*|толч\w*|"
            r"не\s+работает|не\s+срабатывает|неисправн\w*|гидрокомпенс\w*)\b",
            tail,
            re.I,
        )
    )


def _is_opening_explicit_need_to_do_to(low: str) -> bool:
    """
    Явное намерение пройти регламентное ТО в opening
    («мне надо/нужно/хочу ... ТО сделать»), даже если дальше
    обсуждаются сопутствующие вопросы/претензии.
    """
    opening = (low or "")[:1700]
    if not opening.strip():
        return False
    return bool(
        re.search(
            r"\b(?:мне\s+)?(?:надо|нужно|хочу|необходимо)\s+(?:будет\s+)?то\s+сдела\w*\b",
            opening,
            re.I,
        )
        or re.search(
            r"\bто\s+сдела\w*\b[^.!?]{0,80}\b(?:надо|нужно|хочу|необходимо)\b",
            opening,
            re.I,
        )
    )


def _context_ok_for_sdelat_to(low: str, marker: str) -> bool:
    """
    Фильтр шума STT для маркера «сделать то».
    Разрешаем только при сервисном контексте; отсеиваем связки вида «не делаем ... то».
    """
    idx = low.find(marker)
    if idx < 0:
        return False
    left = low[max(0, idx - 28) : idx]
    right = low[idx + len(marker) : min(len(low), idx + len(marker) + 48)]
    # Шумовой паттерн из разговоров о запчастях: «не делаем ... то ...».
    if "не делаем" in left or re.search(r"\bне\s+делаем\b", left):
        return False
    if re.match(r"^\s*есть\b", right):
        return False
    ctx = (
        "техническое обслуживание",
        "запис",
        "стоимост",
        "цена",
        "первое",
        "второе",
        "третье",
        "регламент",
        "пройти то",
        "на то",
    )
    window = f"{left} {marker} {right}"
    return any(x in window for x in ctx)


def _norm(lo: str) -> str:
    t = (lo or "").lower()
    t = t.replace("ё", "е")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _outbound_cold_to_outreach(low: str) -> bool:
    if not any(p in low for p in _COLD_MARKERS_STRICT):
        return False
    return any(a in low for a in _SERVICE_ANCHOR_FOR_COLD)


def _sto_reception_line_answered_after_handoff(low: str) -> bool:
    """Линия диспетчера/ассистента сервиса фактически ответила после перевода (10330, 12687)."""
    if re.search(
        r"(?:диспетчер|ассистент)\s+сервиса[^.!?]{0,80}(?:слушаю|здравствуйте|добрый)",
        low,
        re.I,
    ):
        return True
    if re.search(
        r"(?:викинг|чери|дилерск).{0,50}(?:диспетчер|ассистент|сервис).{0,60}(?:слушаю|здравствуйте)",
        low,
        re.I,
    ):
        return True
    if re.search(
        r"(?:андреева|плаксина|гринкина|гранкина).{0,40}(?:юлия|дарья|александра).{0,40}(?:слушаю|здравствуйте)",
        low,
        re.I,
    ):
        return True
    if "слушаю вас" in low and ("диспетчер" in low or "ассистент" in low or "викинг" in low or "чери" in low):
        return True
    # 12658/12687: линия представилась без «слушаю».
    if re.search(r"диспетчер\s+сервиса\s+(?:юлия|дарья|александра|андреева)", low, re.I):
        return True
    return False


def _service_line_handoff_promised_but_not_connected(low: str) -> bool:
    """
    Пообещали перевод на диспетчера/ассистента сервиса по ТО, в записи нет ответа линии приёмки (13787).
    """
    low = (low or "").lower()
    if not re.search(r"(?:переключ|перевед|перевож|соедин)[а-яё]{0,20}", low, re.I):
        return False
    if not re.search(r"\b(?:диспетчер\w*|ассистент\w*)\b", low, re.I):
        return False
    if _sto_reception_line_answered_after_handoff(low):
        return False
    if not (
        re.search(r"\bто\b", low)
        or "запись на то" in low
        or "техобслуж" in low
        or "техническ" in low
    ):
        return False
    return bool(
        re.search(
            r"(?:переключ|перевед|перевож)[а-яё\s,.]{0,120}?(?:диспетчер\w*|ассистент\w*)",
            low,
            re.I,
        )
        or re.search(
            r"(?:на\s+)?(?:диспетчер\w*|ассистент\w*)[а-яё\s,.]{0,120}?(?:переключ|перевед|перевож)",
            low,
            re.I,
        )
    )


def _no_actual_reception_contact(low: str) -> bool:
    """Нет фактического контакта с приёмкой СТО: линия занята, только номер/перезвон."""
    from call_analytics.classify_by_transcript import (
        _dispatcher_off_hours_or_unavailable_schedule,
        _is_salon_front_desk_opening,
        _is_sto_no_service_assistant_connection_low,
        _sto_admin_callback_or_number_offer,
        _sto_regulatory_to_booking_service_intent_present,
    )
    # 21254: даже при «перезвоните» в финале мог состояться полноценный диалог
    # с приёмкой (регламент ТО + стоимость + подбор даты/окна). Это не no-contact.
    if _inbound_regulatory_to_quote_and_booking_intake(low):
        return False
    # В 28042 линия приёмки фактически ответила после перевода
    # («диспетчер сервиса ... слушаю вас»), поэтому это не no-contact.
    if _is_sto_no_service_assistant_connection_low(low) and not _sto_reception_line_answered_after_handoff(low):
        return True
    if _service_line_handoff_promised_but_not_connected(low):
        return True
    busy = (
        bool(re.search(r"линия[^.!?\n]{0,60}занят", low))
        or bool(re.search(r"линия[^.!?\n]{0,40}диспетчер[^.!?\n]{0,30}занят", low))
        or "линия диспетчера занята" in low
        or "линия у диспетчера занята" in low
        or "диспетчер занят" in low
        or bool(
            re.search(
                r"диспетчер[^.!?]{0,60}не\s+мож(?:ет|ь)[^.!?]{0,50}разговарив",
                low,
                re.I,
            )
        )
        or bool(re.search(r"диспетчер[^.!?]{0,40}(?:сейчас|сйчас|занят)\w*", low, re.I))
    )
    callback_only = _sto_admin_callback_or_number_offer(low)
    # 18193/15493: «линия занята» + номер у админа, но позже диспетчер ответил и записал — контакт был.
    answered_after = _sto_reception_line_answered_after_handoff(low)
    if busy and callback_only and not answered_after:
        return True
    # 11662/13576: диспетчер уже не на линии (график), только перезвон/номер у админа.
    if (
        _dispatcher_off_hours_or_unavailable_schedule(low)
        and callback_only
        and not answered_after
    ):
        return True
    # 17418: админ, сервис не работает (сегодня/завтра), заявка — перезвонят в понедельник и запишут.
    front_desk = "администратор" in (low or "")[:900] or _is_salon_front_desk_opening(low)
    if (
        front_desk
        and callback_only
        and not _sto_reception_line_answered_after_handoff(low)
        and (
            re.search(r"\b(?:сегодня|завтра|сегодня-завтра).{0,50}не\s+работа", low, re.I)
            or re.search(r"\bзапис\w*.{0,60}только\s+в\s+понедельник", low, re.I)
            or (
                re.search(r"\bзаявоч", low, re.I)
                and re.search(r"\bпонедельник\b", low, re.I)
                and re.search(r"\bперезвон", low, re.I)
            )
        )
    ):
        return True
    # 18454/18455: Полина/стойка — сервис не работает в выходные, запись только в пн, слота нет.
    if (
        front_desk
        and _sto_regulatory_to_booking_service_intent_present(low)
        and not answered_after
        and re.search(r"сервис\s+не\s+работа", low, re.I)
        and re.search(r"(?:суббот|воскресень)", low, re.I)
        and re.search(r"понедельник", low, re.I)
        and not (
            re.search(r"\bзаписал[аи]\w*\s+(?:вас\s+)?на\s+(?:то\b|техническ|\d)", low)
            or re.search(r"\bзаписал[аи]\w*\s+вас\s+на\b", low)
            or "подтверждаю запись" in low
            or "ждем вас" in low
            or "ждём вас" in low
        )
    ):
        return True
    return False


def _inbound_regulatory_to_quote_and_booking_intake(low: str) -> bool:
    """
    Входящий: смета/цена регламентного ТО (N-е ТО, «ТО большое») и запись на слот в том же звонке (10545, 9163).
    «вы записаны» на кузовной/осмотр — не отменяет СТО_ТО_вх при новой записи на ТО.
    """
    if not (
        _regulatory_to_price_or_composition_context(low)
        or (
            re.search(r"\bто\s+больш", low, re.I)
            and re.search(
                r"\b(?:перв|втор|трет|четвер|четвёрт)\w*\s+то\w*\b",
                low,
                re.I,
            )
        )
    ):
        return False
    return bool(
        re.search(r"\bзапиш\w+", low, re.I)
        or re.search(r"\bзаписал[аи]?\s+вас\b", low, re.I)
        or re.search(r"\b(?:мы\s+)?с\s+вами\s+записал", low, re.I)
        or re.search(r"\bзаписал(?:ись|ись|и)\b", low, re.I)
        or "записали" in (low or "")
        or (
            re.search(r"\bпо\s+стоимости\s+получается\b", low, re.I)
            and _regulatory_to_price_or_composition_context(low)
        )
        or re.search(r"\bпланиру\w*\b", low, re.I)
    )


def _inbound_client_new_regulatory_to_booking_intake(low: str) -> bool:
    """
    Входящий: клиент с начала просит записаться на N-е ТО / регулярно проходит ТО — новая СТО_ТО_вх (9651).
    Не путать с уточнением уже существующего слота.
    """
    head = (low or "")[:1400]
    if re.search(r"\b(?:нужно|надо|хочу)\s+записаться", head, re.I) and re.search(
        r"\b(?:перв|втор|трет|четвер|нулев)\w*\b", head, re.I
    ):
        return True
    if re.search(r"\bподошл[ао]\s+(?:втор|трет|четвер|перв|нулев)\w*\s+то\b", head, re.I):
        return True
    if re.search(r"\bкаждый\s+год.{0,140}\bто\s+прохожу", head, re.I):
        return True
    # 11383: «второе ТО записаться» / STT «Мжно … записаться» в начале звонка.
    if re.search(
        r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+то\s+запис",
        head,
        re.I,
    ):
        return True
    # 11581: STT «хотела на нулевое ТОО записаться» — слова между «хотела» и «записаться».
    if re.search(r"\bхотел[аи]?\s+на\s+нулев\w*\s+то", head, re.I) and re.search(
        r"\bзапис", head, re.I
    ):
        return True
    if re.search(r"\bнулев\w*\s+то+\s+запис", head, re.I):
        return True
    # 12032: «мне ТО надо … третий проходить» — новая запись на N-е ТО.
    if re.search(r"\bмне\s+то\s+надо\b", head, re.I) and re.search(
        r"\b(?:перв|втор|трет|четвер|четвёрт|пят|шест|нулев)\w*\s+проход\w+\b",
        head,
        re.I,
    ):
        return True
    # 14496: STT «мне бы вот на ТО записаться» (порядок слов).
    # Не ловить «на ТО записана/записан» — уже существующая запись (8567).
    # Императив «запиши/запишите на ТО» — отдельно ниже (14714, глагол перед «на ТО»).
    if re.search(
        r"\bна\s+то+\s+запис(?:ать(?:ся)?|ывать(?:ся)?|и(?:те)?)\b",
        head,
        re.I,
    ):
        return True
    # 14714: «запишите, пожалуйста, на ТО» — новая запись (глагол перед «на ТО»).
    if re.search(r"\bзапиш\w+[^.!?]{0,50}на\s+то\b", head, re.I):
        return True
    return False


def _inbound_already_booked_to_before_call(low: str) -> bool:
    """Клиент уже был записан на ТО до этого звонка (10549), не новая запись в разговоре."""
    head = (low or "")[:1400]
    return bool(
        re.search(r"\b(?:я|мы)\s+записан[аы]?\s+завтра\s+на\s+то\b", head, re.I)
        or re.search(r"\b(?:я|мы)\s+записан[аы]?\s+на\s+то\b", head, re.I)
        or re.search(r"\b(?:я|мы)\s+уже\s+записан[аы]?\s+на\s+то\b", head, re.I)
        or re.search(r"\b(?:я|мы)\s+уже\s+записал\w*\s+на\s+то\b", head, re.I)
        or re.search(r"\b(?:я|мы)\s+записал\w*\s+на\s+то\b", head, re.I)
        or re.search(r"\bна\s+то\s+записан", head[:600], re.I)
    )


def _inbound_new_to_slot_confirmed_in_call(low: str) -> bool:
    """В этом звонке назначен/подтверждён слот на регламентное ТО."""
    head = (low or "")[:3200]
    return bool(
        re.search(r"\bзаписал[аиоы]?\w*\s+вас\s+на\b", low, re.I)
        or re.search(r"\bзаявк\w*[^.!?]{0,50}получил", head, re.I)
        or re.search(r"\b(?:мы\s+)?с\s+вами\s+записал", low, re.I)
        or re.search(r"\bзаписал(?:ись|ись|и)\b", low, re.I)
        or "записали" in (low or "")
        or re.search(r"\bзапиш\w+", low, re.I)
    )


def _ordinal_regulatory_to_signal_in_head(low: str, head: str) -> bool:
    """N-е ТО в начале звонка с фильтром ложных STT («половина второго, то есть» — 13400)."""
    pat = (
        r"\b(?:нулев|нолев|перв|втор|трет|четвер|четвёрт|пят|шест|седьм?\w*|восьм|"
        r"девят|десят)\w*(?:-\s*е)?\s+то\b"
    )
    for m in re.finditer(pat, head, re.I):
        if _ordinal_before_to_stt_match_is_false_positive(low, m):
            continue
        if _ordinal_to_match_is_service_history_reference(low, m):
            continue
        return True
    return False


def _regulatory_to_with_side_work_booking(low: str) -> bool:
    """
    Регламентное ТО + любые доп. работы при новой записи в том же звонке — узкая СТО_ТО_*.
    Продукт: ТО + работы при новой записи = СТО_ТО, не НЕ_ТО (9603, 9760, 12574).
    """
    if _is_inbound_dashcam_or_accessory_programming_not_narrow_to(low):
        return False
    head = (low or "")[:3200]
    has_reg_to = bool(
        _ordinal_regulatory_to_signal_in_head(low, head)
        or re.search(
            r"\bто\s+(?:перв|втор|трет|четвер|пят|шест|седьм|восьм|девят|нулев)\w*\b",
            head,
            re.I,
        )
        or re.search(r"\bсамо\s+то\b", head, re.I)
        or contains_any_to_marker_hit(low)[0]
        or strong_scheduled_to_signal_present(low)
    )
    if not has_reg_to:
        return False
    if _is_adjacent_dept_existing_to_coordination_not_narrow_to(low):
        return False
    if not _inbound_new_to_slot_confirmed_in_call(low):
        return False
    if _inbound_already_booked_to_before_call(low):
        return False
    return True


def _brake_disk_topic_present(low: str) -> bool:
    """Тормозные диски — не подстрока «диск» в «дисконт/дисконта» (12475)."""
    return bool(
        re.search(r"\b(?:тормозн\w+\s+)?диск(?:и|ов|а)?\b", low or "", re.I)
    )


def _inbound_online_technical_service_application_intake(low: str) -> bool:
    return _is_inbound_online_technical_service_application_intake(low)


def _non_reg_service_topic(low: str) -> bool:
    """Нерегламентная сервисная тема: диагностика/гарантия/замены и т.п. -> НЕ_ТО."""
    head = (low or "")[:4500]
    if _is_primary_defect_diagnostic_service_intake(low):
        return True
    chassis_noise = any(
        p in head for p in ("стуч", "стойк стабилиз", "колес начали", "колёса начали", "неровност")
    )
    if chassis_noise and any(p in head for p in ("причин", "узнать", "гарант")):
        if not re.search(r"\bзапис(?:аться|ь)\s+на\s+то\b", head[:2200]):
            return True
    # Балансировка + совет «на любой станции ТО» — не регламентное ТО у дилера (9371).
    if "балансир" in low and any(
        p in low
        for p in (
            "шиномонтаж",
            "вибрац",
            "ролик",
            "необязательно",
            "не обязательно",
            "любой станц",
            "в любом шиномонтаж",
        )
    ):
        return True
    # Запись/стоимость регламентного ТО + доп. работы (масло КПП, развал, лампы) — узкая СТО_ТО_* (9603, 9760).
    if strong_scheduled_to_signal_present(low) or contains_any_to_marker_hit(low)[0]:
        return False
    if _inbound_online_technical_service_application_intake(low):
        return False
    if _regulatory_to_with_side_work_booking(low):
        return False
    if _regulatory_to_price_or_composition_context(low):
        return False
    # «осмотр» с границей слова — не подстрока в «посмотрели» (9045).
    # 9506: «общий осмотр» в составе нулевого/регламентного ТО — не отдельная диагностика.
    if re.search(r"\bосмотр\w*\b", low):
        if _regulatory_to_price_or_composition_context(low) and re.search(
            r"\bобщий\s+осмотр\b|\bходовая\s+часть\s+осматр",
            low,
            re.I,
        ):
            pass
        else:
            return True
    # 10155: состав регламентного ТО (масло/фильтры) при «ТО пройти» + запись на слот — не «только замена масла».
    if re.search(r"\b(?:то\s+пройти|пройти\s+то)\b", low, re.I) and re.search(
        r"\bзаписал", low, re.I
    ):
        return False
    if _brake_disk_topic_present(low):
        return True
    return any(
        p in low
        for p in (
            "диагностик",
            "гаранти",
            "замена",
            "колодк",
            "масло",
            "шумит",
            "не работает",
            "ремонт",
            "омывател",
            "стеклоомыв",
            "щетк",
            "дворник",
            "сцеплен",
            "цеплен",
            "регулиров",
            "адаптац",
            "балансир",
            "шиномонтаж",
            "развал",
            "свист",
            "стуч",
            "стук ",
            "стучит",
            "стойк стабилиз",
            "камер",
            "не показывает",
            "неисправ",
            "поломк",
            "дефект",
            "видеорегистр",
            "регистратор",
        )
    )


def _ordinal_to_in_past_complaint_context(low: str) -> bool:
    """«на шестом то даже не сделали» — жалоба на прошлый визит, не запись на регламентное ТО (8853)."""
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


def _is_clutch_adaptation_not_narrow_to(low: str) -> bool:
    """Запись на адаптацию/регулировку сцепления (STT «цепление») — не узкая СТО_ТО_* (8853)."""
    head = (low or "")[:4000]
    clutch = bool(
        re.search(r"\bадаптац\w*\s+цеплен", head)
        or re.search(r"\bадаптац\w*\s+сцеплен", head)
        or re.search(r"\bрегулиров\w*\s+сцеплен", head)
        or re.search(r"запис\w+\s+на\s+(?:адаптац|регулиров)", head)
        or ("сцеплен" in head and ("регулиров" in head or "адаптац" in head))
    )
    if not clutch:
        return False
    return not bool(
        re.search(r"\bзапис(?:аться|ь)\s+на\s+то\b", head)
        or re.search(r"\bзапись\s+на\s+то\b", head)
    )


def _is_jetour_topic(low: str) -> bool:
    """Jetour/Джитур (варианты STT) в сервисном звонке — см. jetour_brand_mentioned."""
    return jetour_brand_mentioned(low)


def _is_noisy_jetour_fragment_overridden_by_client_chery(sto_dims: Dict[str, Any]) -> bool:
    """
    20987: шумовой STT-фрагмент Jetour не должен задавать причину узкой рубрики,
    если марка клиента уже уверенно определена как Chery/Tenet в клиентской зоне.
    """
    if (sto_dims.get("service_brand") or "").strip().lower() != "chery_tenet":
        return False
    ev = sto_dims.get("evidence") if isinstance(sto_dims.get("evidence"), dict) else {}
    zone = (ev.get("brand_zone") or "").strip().lower()
    if zone not in (
        "crm_vehicle_passport",
        "after_vehicle_mention",
        "after_greeting_body",
        "client_ownership_phrase",
        "client_vehicle_question_window",
        "client_service_booking_phrase",
        "early_client_vehicle",
    ):
        return False
    hit = (ev.get("brand_hit") or "").strip().lower()
    return any(k in hit for k in ("промакс", "pro max", "tiggo", "чери", "тенет", "tenet"))


def _is_jetour_service_refusal(low: str) -> bool:
    """
    Jetour + явный отказ дилера в обслуживании / ТО. Узкая рубрика СТО_ТО_* не применяется,
    даже если в тексте есть явные маркеры регламентного ТО (цена третьего ТО и т.п.).
    Исключение: не офиц. дилер, но ТО проводят и слот согласован — СТО_ТО_вх (12339).
    Маркеры отказа общие с sto_booking_dimensions._sto_service_refusal_phrase_hit.
    """
    if not _is_jetour_topic(low):
        return False
    if not _sto_service_refusal_phrase_hit(low):
        return False
    if _jetour_limitation_but_regulatory_to_booking_agreed(low):
        return False
    return True


def _is_jetour_limitation_with_confirmed_new_to_booking(
    low: str, ct: str, sto_dims: Dict[str, Any]
) -> bool:
    """
    Jetour: в разговоре озвучили ограничение по официальной гарантии, но затем
    согласовали новый слот на ТО в этом же звонке (20940) — сохраняем СТО_ТО_вх.
    """
    if (ct or "").strip().upper() != "STO_IN":
        return False
    if not _is_jetour_topic(low):
        return False
    if not _sto_service_refusal_phrase_hit(low):
        return False
    if _jetour_limitation_but_regulatory_to_booking_agreed(low):
        return True
    ev = sto_dims.get("evidence") if isinstance(sto_dims.get("evidence"), dict) else {}
    wi = (ev.get("work_intent") or "").strip().lower()
    if wi != "to_despite_brand_service_refusal":
        return False
    has_booking_verb = bool(
        re.search(r"\b(?:запиш\w+|записал[аиоы]?\w*|записываем)\b", low, re.I)
    )
    has_slot_time = bool(
        re.search(r"\b\d{1,2}(?::|\.| )\d{2}\b", low)
        or re.search(r"\b(?:на|в)\s+\d{1,2}\s+\d{2}\b", low, re.I)
        # 25922: STT без двоеточия — «на 15 часов», «время 3 часа дня», «12 сентября».
        or re.search(r"\b(?:на\s+)?\d{1,2}\s+час(?:а|ов)?\b", low, re.I)
        or re.search(r"\bвремя\s+\d{1,2}\s+час", low, re.I)
        or re.search(
            r"\b(?:на\s+)?\d{1,2}(?:-го)?\s+"
            r"(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\b",
            low,
            re.I,
        )
    )
    has_slot_confirm = bool(
        re.search(r"\b(?:подъедете|приедете)\b[^.!?]{0,40}\b(?:да|угу|конечно)\b", low, re.I)
        or re.search(
            r"\b(?:давайте|записываем)\b[^.!?]{0,70}\b(?:на\s+)?(?:\d{1,2}|"
            r"понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|воскресень\w*)\b",
            low,
            re.I,
        )
        or re.search(
            r"\bзапишите\b[^.!?]{0,50}\bпожалуйста\b|"
            r"\b(?:вас|ввас)\s+записал[аи]\b|"
            r"\bзаписал[аи]\b[^.!?]{0,80}\bнапомн",
            low,
            re.I,
        )
    )
    return has_booking_verb and has_slot_time and has_slot_confirm


# Порядковые для ТО и для STT «N-й вариант … N-е то провести» (11583).
_ORDINAL_WORD_FOR_TO = (
    r"(?:нулев|нолев|перв|втор|трет|четвер|четвёрт|пят|шест|седьм?\w*|восьм|девят|десят)"
)


def _is_sto_option_wording_ordinal_to_not_regulatory_booking(
    low: str, *, head_limit: int = 2800
) -> bool:
    """
    STT: «первый/второй/нулевой … вариант» + «… то провести у них» — когда делать ТО, не N-е ТО по счёту.
    """
    head = (low or "")[:head_limit]
    if not head.strip():
        return False
    ord_to = _ORDINAL_WORD_FOR_TO
    return bool(
        re.search(
            rf"\bвариант[^.!?]{{0,100}}?\b{ord_to}\w*\s+то\s+провести",
            head,
            re.I,
        )
        or re.search(
            rf"\b{ord_to}\w*\s+вариант[^.!?]{{0,100}}?\bто\s+провести",
            head,
            re.I,
        )
        or re.search(
            rf"\b{ord_to}\w*\s+то\s+провести\s+у\s+них",
            head,
            re.I,
        )
    )


def _is_non_regulatory_to_appointment_booking_marker(low: str, *, head_limit: int = 2800) -> bool:
    """Запись не на регламентное ТО: замена прав и т.п. (11583)."""
    head = (low or "")[:head_limit]
    return bool(
        re.search(r"\bзаписал\w*\s+на\s+(?:замену\s+прав|права\b|гибдд)", head, re.I)
    )


def _is_to_timing_consultation_ranye_pozzhe_not_reschedule(head: str) -> bool:
    """«сделать раньше перед поездкой», варианты когда делать ТО — не перенос слота (11583)."""
    if not (head or "").strip():
        return False
    return bool(
        re.search(r"(?:сделать|сдела\w*)\s+раньше|раньше\s+(?:сделать|перед)", head, re.I)
        or re.search(r"что\s+мне\s+сделать\s+с\s+то\b", head, re.I)
        or (
            "вариант" in head
            and re.search(r"\bто\b", head)
            and re.search(r"\b(?:раньше|позже)\b", head, re.I)
        )
    )


def _is_other_dealer_earlier_slot_suggestion_not_reschedule(head: str) -> bool:
    """14496: совет обратиться в другой ДЦ («у них раньше есть время») — не перенос слота."""
    if not (head or "").strip():
        return False
    return bool(
        re.search(r"\bраньше\b", head, re.I)
        and re.search(
            r"\b(?:обратиться|другой|дилерск|ас-авто|асавто|айсавто|у\s+них|"
            r"солнечн|обводн)\b",
            head,
            re.I,
        )
    )


def _is_early_dropoff_same_day_not_reschedule(head: str) -> bool:
    """
    14714: после новой записи на ТО — «приехать/привезти раньше, оставить автомобиль»,
    не перенос слота на другой день.
    """
    if not (head or "").strip():
        return False
    return bool(
        re.search(
            r"\b(?:приехать|привезти|подъехать)\s+раньше\b",
            head,
            re.I,
        )
        or re.search(
            r"\bраньше[^.!?]{0,50}(?:оставить|привезти|приехать)\b",
            head,
            re.I,
        )
        or (
            re.search(r"\bпораньше\b", head, re.I)
            and re.search(r"\b(?:оставить|привезти)\s+автомоб", head, re.I)
        )
    )


def _client_past_no_show_not_active_to_booking(head: str) -> bool:
    """14496: «записывался, но не обслуживал» — срыв визита, не активная запись на ТО."""
    return bool(
        re.search(
            r"\bзаписывал(?:ся|ись)\b[^.!?]{0,100}\bне\s+обслуживал",
            head or "",
            re.I,
        )
    )


def _existing_to_booking_recording_verb(head: str) -> bool:
    """Глаголы про запись на ТО; без ложного «записывался, но не обслуживал» (14496)."""
    h = head or ""
    if _client_past_no_show_not_active_to_booking(h):
        return bool(
            re.search(r"\bзаписывал(?:ся|ись)\b[^.!?]{0,100}\bна\s+то\b", h, re.I)
            or re.search(r"\b(?:уже\s+)?записан[аы]?\s+на\s+то\b", h, re.I)
            or re.search(r"\b(?:с\s+вами|мы\s+с\s+вами)\s+записал", h, re.I)
        )
    return bool(
        re.search(
            r"\b(?:записывал(?:ся|ись)|уже\s+записан|с\s+вами\s+записал|мы\s+с\s+вами\s+записал)",
            h,
            re.I,
        )
    )


def _inbound_existing_to_booking_fact_present(low: str, *, head_limit: int = 4000) -> bool:
    """
    В тексте зафиксирована уже существующая запись на регламентное ТО (не новая заявка).
    В т.ч. прошедшее «записывался на ТО», подтверждение слота диспетчером («на 30-е записывали»).
    """
    head = (low or "")[:head_limit]
    if not head.strip():
        return False
    if _inbound_diagnostic_only_booking_with_first_to_intake(low):
        return False
    return bool(
        re.search(r"\bзаписывал(?:ся|ась|ись)\b[^.!?]{0,100}\bна\s+то\b", head, re.I)
        or re.search(r"\bзаписывал(?:ся|ась|ись)\s+на\s+то\b", head, re.I)
        # 19234: «я на ТО записался 28-го в 15:00».
        or re.search(r"\b(?:я|мы)\s+на\s+то\s+записал(?:ся|ась|ись)\b", head, re.I)
        or re.search(r"\bна\s+то\s+записал(?:ся|ась|ись)\b", head, re.I)
        or re.search(r"\bзаписал(?:ся|ась|ись)\s+на\s+то\b", head, re.I)
        # 19303: «только сейчас записавался на ТО» / STT «записавался».
        or re.search(r"\bзаписавал(?:ся|ась|ись)\s+на\s+то\b", head, re.I)
        or re.search(r"\bзаписавал(?:ся|ась|ись)\b[^.!?]{0,100}\bна\s+то\b", head, re.I)
        or re.search(
            r"\bтолько\s+(?:сейчас|что)\s+запис(?:ал|ывал|авал)(?:ся|ась|ись)\b"
            r"[^.!?]{0,80}\b(?:на\s+)?то\b",
            head,
            re.I,
        )
        or re.search(
            r"\bтолько\s+(?:сейчас|что)\s+запис(?:ал|ывал|авал)(?:ся|ась|ись)\b"
            r"[^.!?]{0,80}\bтехническ\w*\s+обслуживан",
            head,
            re.I,
        )
        or re.search(r"\bзаписан[аоы]?\s+то\b", head, re.I)
        # 28673: STT «ТОО» (два «о») в «на завтра на ТОО записан».
        or re.search(r"\bзаписан[аы]?\s+на\s+тоо\b", head, re.I)
        or re.search(r"\bна\s+то\s+записан", head, re.I)
        or re.search(r"\bна\s+тоо\s+записан", head, re.I)
        or re.search(r"\bзаписан[аы]?\s+на\s+то\b", head, re.I)
        or re.search(r"\b(?:я|мы)\s+на\s+завтра\s+на\s+тоо\s+записан[аы]?\b", head, re.I)
        or re.search(r"\b(?:я|мы)\s+завтра\s+на\s+тоо\s+записан[аы]?\b", head, re.I)
        # 17482: «был записан на сервис» + ТО в той же реплике (ошибочно записали ТО / поменять).
        or (
            re.search(
                r"\bбыл[аи]?\s+записан[аы]?\s+на\s+(?:сервис|то)\b",
                head[:1800],
                re.I,
            )
            and re.search(r"\bто\b", head[:1800], re.I)
        )
        or re.search(r"\bошибочно\s+записал[аи]?\s+то\b", head[:1800], re.I)
        or (
            re.search(
                r"\bна\s+\d{1,2}[\s-]*(?:е|го)?\s+числ\w*\s+записан",
                head[:1800],
                re.I,
            )
            and re.search(r"\bто\b", head[:1800], re.I)
        )
        or re.search(r"\b(?:я|мы)\s+завтра\s+на\s+то\s+записан[аы]?\b", head, re.I)
        or re.search(r"\b(?:я|мы)\s+записан[аы]?\s+завтра\s+на\s+то\b", head, re.I)
        or re.search(r"\b(?:я|мы)\s+уже\s+записан[аы]?\s+на\s+то\b", head, re.I)
        or re.search(r"\b(?:я|мы)\s+завтра\s+записан[аы]?\b", head, re.I)
        or re.search(r"\bпо\s+поводу\s+то\s+на\b", head, re.I)
        # 19445: существующий слот назван в порядке
        # «в понедельник на техосмотр, в 12:30 записали меня».
        or re.search(
            r"\b(?:понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|"
            r"воскресень\w*)\b[^.!?]{0,65}\b(?:техосмотр|то)\b"
            r"[^.!?]{0,90}\bзаписал[аи]\s+меня\b",
            head,
            re.I,
        )
        or (
            re.search(r"\b(?:мы|я)\s+с\s+вами\s+записал", head, re.I)
            and not _is_inbound_opening_regulatory_to_booking(low)
            and not _inbound_client_new_regulatory_to_booking_intake(low)
            and not _inbound_regulatory_to_quote_and_booking_intake(low)
            and not _regulatory_to_price_or_composition_context(low)
        )
        or (
            re.search(r"\bс\s+вами\s+записал", head, re.I)
            and not _is_inbound_opening_regulatory_to_booking(low)
            and not _inbound_client_new_regulatory_to_booking_intake(low)
            and not _inbound_regulatory_to_quote_and_booking_intake(low)
            and not _regulatory_to_price_or_composition_context(low)
        )
        or re.search(
            r"\bна\s+\d{1,2}[\s-]*(?:е|го)?\s+числ\w*\s+записывал",
            head,
            re.I,
        )
        # 10304: «записывался на ТО-2» / «на то-1».
        or re.search(
            r"\bзаписывал(?:ся|ась|ись)\s+на\s+то\s*[-–]?\s*[0-9]\b",
            head,
            re.I,
        )
        # 10304: «записывался на четвёртое / 4-е число» (STT).
        or re.search(
            r"\bзаписывал(?:ся|ась|ись)\s+на\s+"
            r"(?:перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят)\w*\s+числ",
            head,
            re.I,
        )
        or re.search(
            r"\bзаписывал(?:ся|ась|ись)\s+на\s+\d{1,2}[\s-]*(?:е|го)?\s+числ",
            head,
            re.I,
        )
        or re.search(
            r"\bна\s+"
            r"(?:перв|втор|трет|четвер|четвёрт|пят|шест|седьм|восьм|девят|десят)\w*\s+числ\w*\s+записывал",
            head,
            re.I,
        )
        or re.search(
            r"\bна\s+\d{1,2}[\s-]*(?:е|го)?\s+числ\w*\s+записывал",
            head,
            re.I,
        )
        # 12687: «записывался на 10» (дата без «число») при теме техобслуживания/отмены.
        or (
            re.search(r"\bзаписывал(?:ся|ась|ись)\s+на\s+\d{1,2}\b", head, re.I)
            and re.search(
                r"\b(?:техобслуживан|техническ\w+\s+обслуживан|отмен)\w*",
                head,
                re.I,
            )
        )
        or re.search(r"\b(?:вашу|ваш\w*)\s+запис\w*\s+перенес", head, re.I)
        or re.search(r"\bпо\s+поводу\s+отмен\w*\s+на\s+техобслужив", head, re.I)
        or (
            re.search(r"\d{1,2}[\s-]*(?:е|го)?\s+числ", head)
            and re.search(r"запис", head)
            and re.search(r"\bто\b", head)
            and _existing_to_booking_recording_verb(head)
        )
        or (
            re.search(r"\d{1,2}[\s-]*(?:е|го)?\s+числ", head)
            and re.search(r"\d{1,2}[.:]\d{2}", head)
            and re.search(r"запис", head)
            and re.search(r"\bто\b", head)
            and _existing_to_booking_recording_verb(head)
        )
        or (
            re.search(
                rf"\b{_ORDINAL_WORD_FOR_TO}\w*\s+(?:то\b|техобслужив)",
                head,
                re.I,
            )
            and re.search(
                r"\b(?:записал(?:ся|ась|ись|а|и)?|записывал(?:ся|ась|ись)|уже\s+записан|с\s+вами\s+записал|мы\s+.{0,20}записал)\w*",
                head,
                re.I,
            )
            and not re.search(r"\bзаписаться\b", head[:1200], re.I)
            and not _is_inbound_opening_regulatory_to_booking(low)
            and not _is_sto_option_wording_ordinal_to_not_regulatory_booking(
                low, head_limit=len(head)
            )
            and not _is_non_regulatory_to_appointment_booking_marker(low, head_limit=len(head))
        )
        or (
            re.search(r"\bзаписал(?:ся|ась|ись)\s+сегодня\b", head, re.I)
            and re.search(r"\bна\s+\d{1,2}\s*[.:]?\s*\d{2}\b", head)
        )
        or re.search(r"\bна\s+\d{1,2}\s*[.:]?\s*\d{2}\s+записан[аы]?", head, re.I)
        or re.search(r"\bзаписывал(?:ся|ась|ись)\s+на\s+завтра\b", head, re.I)
        or (
            re.search(r"\bзатра\s+на\s+завтра\b", head, re.I)
            and re.search(r"\bзаписывал", head[:500], re.I)
        )
        or (
            re.search(r"\bна\s+завтра\b", head)
            and re.search(r"\bзаписывал", head[:500], re.I)
            and re.search(r"\b\d{1,2}\s*[.:]?\s*\d{2}\b", head)
        )
        # Подтверждение диспетчера о существующей записи (часто без «записывался» в реплике клиента):
        # «вы записаны к нам на ...», «записаны к нам ...».
        or re.search(r"\bвы\s+записан(?:ы|а|)\s+к\s+нам\b", head, re.I)
        or re.search(r"\bзаписан(?:ы|а|)\s+к\s+нам\b", head, re.I)
        # 13945: STT «наТ нулевое ТО», «на двадцатое записывался», «к нам вы записаны».
        or re.search(
            r"\bзаписывал(?:ся|ись|и)\b[^.!?]{0,90}(?:нат\s+)?(?:нулев|нолев)\w*\s+то\b",
            head,
            re.I,
        )
        or re.search(
            r"\bна\s+(?:двадцат|двадц\w*)[^.!?]{0,60}записывал",
            head,
            re.I,
        )
        or re.search(r"\bк\s+нам\s+вы\s+записан", head, re.I)
        # 18161: «в записи сегодня автомобиль» + тема ТО / отказ приехать.
        or (
            re.search(r"\bв\s+записи\s+сегодня\b", head[:2000], re.I)
            and (
                re.search(r"\bна\s+то\b", head[:2500], re.I)
                or re.search(r"\bне\s+приеду\b", head[:2500], re.I)
                or re.search(r"\bприезд\s+на\s+то\b", head[:2500], re.I)
            )
        )
    )


def _inbound_to_booking_reschedule_intent_present(low: str) -> bool:
    if _inbound_same_day_slot_unable_to_attend_reschedule(low):
        return True
    # 15437: «хотел отменить» + «не получится» — отмена, не перенос слота.
    if _inbound_to_booking_cancellation_intent_present(low):
        head_cancel = (low or "")[:2800]
        if re.search(r"\b(?:хотел\w+|хочу)\s+отмен", head_cancel, re.I):
            return False
        if any(
            p in head_cancel
            for p in (
                "отменить",
                "отменяйте",
                "отменили запись",
                "отменяем запись",
                "отказаться",
            )
        ) and not any(
            p in head_cancel
            for p in (
                "перенести",
                "перенос",
                "перезапис",
                "другое время",
                "другой день",
                "другое число",
            )
        ):
            return False
    # 9409: отказ от окна при новой записи на ТО («не получится» на 29 мая) — не перенос слота.
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    head = (low or "")[:2800]
    if not head.strip():
        return False
    _reschedule_markers = (
        "перезапис",
        "перенести",
        "перенос записи",
        "перенесу вас",
        "перенесу ",
        "другой день",
        "другое время",
        "другое число",
        "позже есть",
        "не получилось приехать",
        "не получается приехать",
        "не получается подъехать",
        "не получается",
        "не смогу приехать",
        "не смогу подъехать",
        "не получится",
        "перенесем",
        "переносим",
        "перенесли",
        "запись перенес",
        "отменим",
        "пораньше",
        "попозже",
        "раньше",
        "позже",
        "пораньше принять",
        "попозже принять",
        "принять пораньше",
        "принять попозже",
        "возможности пораньше",
        "нет возможности пораньше",
        "может быть пораньше",
        "немножко пораньше",
        "часа на три",
        "на три часа",
        # 17482: коррекция уже сделанной записи (ТО-60 ↔ ТО-70).
        "можно поменять",
        "ошибочно записали",
        "ошибочно записала",
        "я сейчас поправлю",
    )
    for p in _reschedule_markers:
        if p not in head:
            continue
        if p in ("раньше", "позже") and (
            _is_to_timing_consultation_ranye_pozzhe_not_reschedule(head)
            or _is_other_dealer_earlier_slot_suggestion_not_reschedule(head)
            or _is_early_dropoff_same_day_not_reschedule(head)
        ):
            continue
        if p == "пораньше" and _is_early_dropoff_same_day_not_reschedule(head):
            continue
        return True
    # «Я записан(а) ... можно/нет возможности пораньше/попозже»
    if (
        re.search(r"\bзаписан(?:а|ы)?\b", head, re.I)
        and re.search(r"\b(?:пораньше|попозже|раньше|позже)\b", head, re.I)
        and re.search(r"\b(?:можно|возможност|принять|перенести)\b", head, re.I)
    ):
        return True
    # 27786: уже записан на ТО, просит более раннее «окошко» сегодня/после обеда.
    if _inbound_existing_to_booking_fact_present(low, head_limit=2200) and (
        re.search(r"\b(?:окошечк\w*|свободн\w+\s+времен\w*)\b", head, re.I)
        and re.search(r"\b(?:сегодня|после\s+обед\w*|в\s+обед)\b", head, re.I)
    ):
        return True
    # «попозже» — предложение слота диспетчером при новой записи на ТО, не перенос (9233).
    if "попозже" in head or "на попозже" in head:
        if re.search(r"\b(?:хотел[аи]|хочу)\s+то\s+сделать\b", head[:1200], re.I):
            return False
        if any(
            p in head
            for p in (
                "другой день",
                "другое время",
                "не получится",
                "перенес",
                "перенести",
            )
        ):
            return True
    if "вы мне звонили" in head or "вы звонили" in head or "пропущен" in head:
        return any(
            p in head
            for p in (
                "другой день",
                "другое время",
                "не получится",
                "не получается",
                "попозже",
                "позъех",
                "подъех",
                "записывал",
                "на завтра",
                "отмен",
            )
        )
    return False


def _inbound_hypothetical_to_cancel_superseded_by_reschedule_booking(low: str) -> bool:
    """
    12447: «если найду поближе — тогда отменю запись», но в том же звонке слот согласован
    («давайте записывайте», дата/время) — перенос/уточнение ТО, не отмена.
    """
    text = low or ""
    if not re.search(r"\bотмен", text, re.I) or not re.search(r"\bзапис", text, re.I):
        return False
    hypo = re.search(r"\bесли\b[^.!?]{0,180}\bотмен", text, re.I) or re.search(
        r"\bтогда\b[^.!?]{0,60}\bотмен\w*[^.!?]{0,50}\bзапис",
        text,
        re.I,
    )
    if not hypo:
        return False
    return bool(
        re.search(r"\b(?:давайте\s+)?запис(?:ывайте|ать|ываем|али|ала)\b", text, re.I)
        or re.search(
            r"\b(?:предварительно|записал\w*)\b[^.!?]{0,90}"
            r"(?:\d{1,2}\s*(?:"
            r"январ|феврал|март|апрел|мая|июн|июл|авг|"
            r"сентябр|октябр|ноябр|декабр)"
            r"|\d{1,2}[\s-]*(?:е|го)?\s+числ)",
            text,
            re.I,
        )
        or re.search(
            r"\b(?:на\s+)?\d{1,2}[\s-]*(?:е|го)?\s+числ\w*"
            r"[^.!?]{0,70}(?:\d{1,2}[\s:\.]?\d{0,2}|\d{1,2}\s+час)",
            text,
            re.I,
        )
    )


def _dispatcher_third_party_slot_cancel_mention_not_client_intent(low: str) -> bool:
    """
    12640: «может перенесёт запись либо отменится» — диспетчер про очередь, не отмена визита клиента.
    """
    head = (low or "")[:3500]
    if not head.strip():
        return False
    if re.search(
        r"\b(?:может\s+быть,?\s+)?(?:кто-то\s+)?перенес\w*\s+запись\s+либо\s+отменится\b",
        head,
        re.I,
    ):
        return True
    if re.search(r"\bотменится\b", head, re.I) and not re.search(
        r"\b(?:отмен(?:ить|яю|ите|им|я)|хотел\w+\s+отказ|отменяйте|"
        r"отменить\s+запись|отменяем\s+запись|отмена\s+записи|по\s+поводу\s+отмен)\b",
        head,
        re.I,
    ):
        return True
    return False


def _inbound_first_to_regulatory_booking_intake(low: str) -> bool:
    """
    12640: первое плановое ТО + смета/«первое ТО подходит» — новая СТО_ТО_вх, не отмена слота.
    """
    text = low or ""
    if not re.search(r"\bперв\w*\s+то\b", text, re.I):
        return False
    if re.search(r"\bперв\w*\s+то\s+подходит\b", text, re.I):
        return True
    if re.search(r"\bперв\w*\s+то\s+планов", text, re.I):
        return True
    if _inbound_regulatory_to_quote_and_booking_intake(text):
        return True
    head = text[:2200]
    return bool(
        re.search(r"\b(?:записаться|уточнить)\s+[^.!?]{0,120}\bперв\w*\s+то\b", head, re.I)
        or (
            re.search(r"\bперв\w*\s+то\b", head, re.I)
            and re.search(r"\b(?:10\s+000|10000|9\s+800|9800|9900|пробег)\b", head, re.I)
        )
    )


def _inbound_diagnostic_only_booking_with_first_to_intake(low: str) -> bool:
    """12640: уже записана на диагностику, в звонке — intake первого ТО (не существующая запись на ТО)."""
    head = (low or "")[:2800]
    has_diag_booking = bool(
        re.search(r"\b(?:записан\w*|запис\w*)\s+[^.!?]{0,100}\bдиагност", head, re.I)
        or re.search(r"\b(?:записан\w*\s+)?к\s+диагност", head, re.I)
        or re.search(
            r"\bна\s+диагност\w*\b[^.!?]{0,60}\b(?:запис|\d{1,2}[.:]\d{2})\b",
            head,
            re.I,
        )
    )
    if not has_diag_booking:
        return False
    if re.search(
        r"\b(?:записывал(?:ся|ись)\s+на\s+то|записан\w*\s+на\s+то|на\s+то\s+записан)\b",
        head,
        re.I,
    ):
        return False
    return _inbound_first_to_regulatory_booking_intake(low)


def _inbound_to_booking_cancellation_intent_present(low: str) -> bool:
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    head = (low or "")[:4000]
    if not head.strip():
        return False
    if any(
        p in head
        for p in (
            "отказаться",
            "хотели бы отказаться",
            "хотела бы отказаться",
            "отменяйте",
            "отменить запись",
            "отменяем запись",
            "отмена записи",
            "по поводу отмен",
        )
    ):
        return True
    if "отмен" in head and "запис" in head:
        # 9409: диспетчер — «клиенты запись отменяют», не отмена визита звонящего.
        if re.search(
            r"клиент\w*\s+.{0,45}(?:запис\w+.{0,30}отмен|отмен\w+.{0,30}запис)",
            head,
            re.I,
        ):
            return False
        if _dispatcher_third_party_slot_cancel_mention_not_client_intent(low):
            return False
        if _inbound_hypothetical_to_cancel_superseded_by_reschedule_booking(low):
            return False
        return True
    # 19445: STT «вытеркните» ≈ «вычеркните»; «потом перепишусь» —
    # отмена текущего слота без выбора новой даты в этом звонке.
    if re.search(r"\b(?:вычеркните|вытеркните)\b", head, re.I):
        return True
    if re.search(r"\bпотом\s+перепиш\w*\b", head, re.I):
        return True
    # 10304: «снимите» (STT) при уже названной записи у дилера.
    if (
        re.search(r"\bсним\w*\b", head, re.I)
        and re.search(r"\bзапис", head, re.I)
        and re.search(r"\bзаписывал", head[:1200], re.I)
    ):
        return True
    # Перезапись в другой ДЦ + отмена у нас.
    if (
        re.search(r"\bзаписывал", head[:1500], re.I)
        and re.search(r"\bтуда\s+записал", head, re.I)
        and re.search(r"\b(?:сним\w*|отмен)", head, re.I)
    ):
        return True
    # 18161: «менеджер не одобрил приезд на ТО, поэтому не приеду» — отказ от визита.
    if re.search(r"\bне\s+одобрил\w*\s+приезд\s+на\s+то\b", head, re.I):
        return True
    if re.search(r"\bприезд\s+на\s+то\b", head, re.I) and re.search(
        r"\bне\s+приеду\b", head, re.I
    ):
        return True
    if re.search(r"\bна\s+то\b[^.!?]{0,48}\bне\s+приеду\b", head, re.I):
        return True
    if re.search(r"\bне\s+приеду\b", head, re.I) and re.search(
        r"\bв\s+записи\s+сегодня\b", head, re.I
    ):
        return True
    return False


def _inbound_tomorrow_booking_unable_cancel_not_narrow_to(low: str) -> bool:
    """
    9985: перезвон по пропущенному — записывался на завтра (слот 8:30), не получается; отмена/перенос.
    """
    head = (low or "")[:2400]
    has_tomorrow_booking = bool(
        re.search(r"\bзаписывал(?:ся|ась|ись|и)\s+на\s+завтра\b", head, re.I)
        or (
            re.search(r"\bзатра\s+на\s+завтра\b", head, re.I)
            and re.search(r"\bзаписывал", head[:600], re.I)
        )
        or (
            re.search(r"\bна\s+завтра\b", head)
            and re.search(r"\bзаписывал", head[:600], re.I)
            and re.search(r"\b\d{1,2}\s*[.:]?\s*\d{2}\b", head)
        )
    )
    if not has_tomorrow_booking:
        return False
    unable = any(
        p in head
        for p in (
            "не получается",
            "не получится",
            "не смогу приехать",
            "не получилось",
        )
    )
    cancel = "отмен" in low[:4000] and re.search(r"\bзавтра\b", low[:4000])
    callback = any(
        p in head
        for p in ("вы звонили", "пропущен", "перезвонили", "вы мне звонили")
    )
    return (unable or cancel) and (callback or unable)


def _inbound_same_day_slot_unable_to_attend_reschedule(low: str) -> bool:
    """9741: записался сегодня на HH:MM, не получается приехать — перенос, не новая СТО_ТО_вх."""
    head = (low or "")[:900]
    if not re.search(r"\bзаписал(?:ся|ась|ись)\s+сегодня\b", head, re.I):
        return False
    if not re.search(r"\bна\s+\d{1,2}\s*[.:]?\s*\d{2}\b", head):
        return False
    if not any(
        p in head
        for p in (
            "не получается приехать",
            "не получается подъехать",
            "не получится",
            "не смогу приехать",
            "не получилось приехать",
        )
    ):
        return False
    return bool(re.search(r"\bперенес", low[:4000]))


def _is_inbound_existing_to_reschedule_not_narrow_to(low: str) -> bool:
    """
    Входящий: клиент уже записан на регламентное ТО и просит перенос/перезапись слота (8567, 8666).
    Уточнение диспетчера «первое/второе ТО» при переносе — не новая запись в узком СТО_ТО_вх.
    """
    if _inbound_same_day_slot_unable_to_attend_reschedule(low):
        return True
    if _inbound_tomorrow_booking_unable_cancel_not_narrow_to(low):
        return True
    # 9233: новая заявка «хотели ТО сделать» + «сначала ТО, потом диагноз» — не перенос слота.
    if re.search(r"\b(?:хотел[аи]|хочу)\s+то\s+сделать\b", low[:1200], re.I) and re.search(
        r"\bсначала\s+то\s+потом\s+диагноз", low, re.I
    ):
        return False
    # 9409: с начала «пройти ТО», цена и слот — новая СТО_ТО_вх, не перенос.
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    # 11583: консультация + цена/слот нулевого ТО в этом звонке — новая запись, не перенос.
    if _inbound_regulatory_to_quote_and_booking_intake(low):
        return False
    if _inbound_client_new_regulatory_to_booking_intake(low):
        return False
    if not _inbound_existing_to_booking_fact_present(low, head_limit=2800):
        return False
    return _inbound_to_booking_reschedule_intent_present(low)


def _is_inbound_existing_to_cancellation_not_narrow_to(low: str) -> bool:
    """
    Входящий: отмена уже существующей записи на регламентное ТО (8924, 9206).
    Факт записи в прошлом + отмена — узкий НЕ_ТО, даже при «записывался на ТО» в речи.
    """
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    if _inbound_first_to_regulatory_booking_intake(low):
        return False
    # 18921: в этом же звонке полностью рассчитали новое ТО и согласовали слот;
    # случайное STT «вы отмените запись проверочный звонок» не является отменой.
    if _inbound_regulatory_to_quote_and_booking_intake(low):
        return False
    if not _inbound_existing_to_booking_fact_present(low):
        return False
    return _inbound_to_booking_cancellation_intent_present(low)


def _is_inbound_existing_to_slot_confirmation_not_narrow_to(low: str) -> bool:
    """
    10880: клиент уже записан — «записывался на 30-е на ТО», «всё в силе», уточнение времени;
    диспетчер подтверждает слот. Не новая СТО_ТО_вх., даже при обсуждении состава/доп. услуг к визиту.
    19303: «только сейчас записавался на ТО» + правка телефона в записи.
    """
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    if _inbound_regulatory_to_quote_and_booking_intake(low):
        return False
    if re.search(
        r"\b(?:записыва\w*|предварительно\s+договорились|будем\s+ожидать)\b",
        low,
        re.I,
    ) and (
        re.search(r"\bна\s+\d{1,2}[\s-]*(?:е|го)?\s+числ\w*\b", low, re.I)
        or re.search(r"\b\d{1,2}[:\s.]\d{2}\b", low, re.I)
    ):
        return False
    head = (low or "")[:2000]
    if not head.strip():
        return False
    existing_fact = bool(
        re.search(r"\bзаписывал(?:ся|ись|и)\b[^.!?]{0,100}\bна\s+то\b", head, re.I)
        or re.search(r"\bзаписывал(?:ся|ись|и)\s+на\s+то\b", head, re.I)
        or re.search(r"\bзаписавал(?:ся|ись)\b[^.!?]{0,100}\bна\s+то\b", head, re.I)
        or re.search(
            r"\bтолько\s+(?:сейчас|что)\s+запис(?:ал|ывал|авал)(?:ся|ись)\b",
            head,
            re.I,
        )
        # 23473: «у нас сегодня запись на ТО», далее уточнение времени/смс.
        or re.search(
            r"\b(?:у\s+нас\s+)?(?:сегодня|завтра)\s+запис(?:ь|ан[аоы]?)\s+на\s+то\b",
            head,
            re.I,
        )
        or _inbound_existing_to_booking_fact_present(low, head_limit=800)
    )
    if not existing_fact:
        return False
    # 25969: «я вот записался ... на ТО первое» + дальнейшее уточнение цены.
    # Это уточнение уже существующей записи, не новый цикл записи на ТО.
    if (
        re.search(r"\b(?:я|мы)\s+(?:вот\s+)?записал(?:ся|ась|ись)\b", head, re.I)
        and re.search(
            r"\b(?:на\s+то\b|на\s+\d{1,2}(?:[-\s]*(?:е|го))?\s+числ\w*|"
            r"на\s+(?:перв|втор|трет|четвер|четв[её]рт|пят|шест|седьм|восьм|девят|десят)\w*\s+то)\b",
            head,
            re.I,
        )
    ):
        return True
    return any(
        p in head
        for p in (
            "в силе",
            "всё в силе",
            "все в силе",
            "записаны",
            "записывали",
            "уточнить",
            "хотел уточнить",
            "просто уточн",
            "к нам вы записаны",
            "на какое время",
            "на какое ещё раз",
            "всё верно",
            "все верно",
            "телефон",
            "продиктуйте",
            "поправили",
            "не тот назвал",
        )
    ) or bool(
        re.search(
            r"\bна\s+(?:какое|какую)\s+(?:время|дату|день|число)\b",
            head,
            re.I,
        )
    ) or bool(
        re.search(r"\bтолько\s+(?:сейчас|что)\s+запис(?:ал|ывал|авал)(?:ся|ись)\b", head, re.I)
    )


def _is_inbound_existing_to_in_service_status_not_narrow_to(low: str) -> bool:
    """
    26334: авто уже оставили на ТО, звонок про статус текущего визита
    («нет звонка», «кто мастер», «что с машиной», «когда забрать»).
    Это сопровождение существующего визита, не новая запись на ТО.
    """
    head = (low or "")[:4200]
    if not head.strip():
        return False
    if _is_inbound_opening_regulatory_to_booking(low):
        return False
    already_at_service = bool(
        re.search(
            r"\b(?:машин\w*|автомобил\w*)[^.!?]{0,80}"
            r"(?:оставил(?:а|и)?|оставлен\w*|пригнал(?:а|и)?)"
            r"[^.!?]{0,40}\b(?:на\s+то|в\s+сервис)\b",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:на\s+то|в\s+сервис)\b[^.!?]{0,80}"
            r"(?:оставил(?:а|и)?|пригнал(?:а|и)?|с\s+\d{1,2}[:.]\d{2})",
            head,
            re.I,
        )
    )
    if not already_at_service:
        return False
    status_followup = bool(
        re.search(
            r"\b(?:до\s+сих\s+пор|ни\s+звонка|нет\s+звонка|"
            r"кто\s+мастер(?:-?при[её]мщик)?|что\s+с\s+(?:ней|машин\w*)|"
            r"пытал(?:ись|ся)\s+дозвон|не\s+могли\s+дозвон|"
            r"когда\s+забрат\w*|забрат\w*\s+автомобил\w*)\b",
            head,
            re.I,
        )
    )
    return status_followup


def _is_inbound_employee_contact_routing_not_narrow_to(low: str) -> bool:
    """
    Входящий: связаться с конкретным сотрудником, график/переводы — не СТО_ТО_вх (12658).
    «попадаю, то на диспетчера, то или ещё на кого-то» — союз «то… то…», не ТО на авто.
    """
    head = (low or "")[:3500]
    if not head.strip():
        return False
    if re.search(
        r"\b(?:попада\w*[^.!?]{0,50}\b)?то\s+на\s+(?:диспетчер\w*|кого(?:-то)?)"
        r"[^.!?]{0,45},\s*то\s+(?:или|и)\b",
        head,
        re.I,
    ):
        return True
    if not re.search(r"\bто\s+на\s+(?:диспетчер\w*|кого(?:-то)?)\b", head, re.I):
        return False
    employee_contact = bool(
        re.search(
            r"\b(?:надо|нужно|хочу)\s+(?:позвонить|связаться|поговорить)\b",
            head,
            re.I,
        )
        or re.search(
            r"\bкак\s+(?:мне|можно)\s+[^.!?]{0,55}\bпоговор",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:именно\s+)?с\s+(?:ним|ней)\s+(?:надо|нужно)?\s*поговор",
            head,
            re.I,
        )
        or re.search(r"\bнадо\s+позвонить\b", head, re.I)
    )
    if not employee_contact:
        return False
    routing_ctx = any(
        p in head
        for p in (
            "переключ",
            "перевед",
            "не работает",
            "рабочая смена",
            "на диспетчера",
            "диспетчер серvиса",
            "диспетчер сервиса",
        )
    )
    if not routing_ctx:
        return False
    if re.search(
        r"\b(?:запис\w*\s+на\s+то|перв\w+\s+то|пройти\s+то|"
        r"техническ\w+\s+обслуживан|нулев\w+\s+то)\b",
        head,
        re.I,
    ):
        return False
    return True


def _is_inbound_client_missed_dealer_call_inquiry_not_narrow_to(low: str) -> bool:
    """
    Входящий: клиент перезванивает — не успел(а) взять трубку, спрашивает зачем/почему звонили (8568).
    Диспетчер подтверждает уже существующую запись/заявку — не новая СТО_ТО_вх., даже при «техосмотр» в речи.
    """
    head = (low or "")[:4000]
    if not head.strip():
        return False
    if not any(
        p in head
        for p in (
            "диспетчер сервиса",
            "ассистент сервиса",
            "слушаю вас",
        )
    ):
        return False
    client_inquiry = any(
        p in head
        for p in (
            "зачем перезваниваете",
            "зачем вы звон",
            "зачем звон",
            "почему вы звон",
            "почему звон",
            "вы звоните",
            "вы звонили",
            "некогда было взять трубку",
            "некогда взять трубку",
            "не успел взять трубку",
            "не успела взять трубку",
            "что случилось",
            "звонок был от вас",
            "пропущен",
        )
    ) or re.search(r"\bчто\s+случил", head, re.I)
    if not client_inquiry:
        return False
    existing_booking_ctx = any(
        p in head
        for p in (
            "подтвердить",
            "увидели вашу запись",
            "увидели заявку",
            "увидели вашу заявку",
            "записались на",
            "записался на",
            "записывался на",
            "записана на",
            "на завтра",
            "вчера мы вам звонили",
            "перезваниваем и подтверждаем",
            "ждём вас",
            "ждем вас",
            "дозвонил",
            "мастерприемщик звонил",
            "мастер приемщик звонил",
            "мастерприёмщик звонил",
        )
    ) or re.search(r"\bзаписал[аи]?\s+на\s+(?:то|техосмотр)\b", head, re.I)
    # «по готовности» только у звонка мастера (15927), не «по готовности сообщат» при сдаче (17498).
    if re.search(
        r"(?:мастер.?при[её]?мщик|мастерприемщик|мастер\s+при[её]?мщик)\w*"
        r"[^.!?]{0,55}по\s+готовност",
        head,
        re.I,
    ) or re.search(r"звонил\w*[^.!?]{0,55}по\s+готовност", head, re.I):
        existing_booking_ctx = True
    # 17498: после «пропущенный» — приёмка/коррекция слота на нулевое ТО + смета → не этот guard.
    if re.search(r"\bнулев\w*\s+то\b", low or "", re.I) and (
        _regulatory_to_price_or_composition_context(low or "")
        or re.search(r"\bпо\s+стоимост\w*[^.!?]{0,60}\bто\b", low or "", re.I)
    ):
        return False
    if existing_booking_ctx:
        return True
    # 15927: перезвон по пропущенному от мастера; машина уже сдана на ТО сегодня.
    if re.search(r"\bсегодня\s+машин\w*\s+к\s+нам\b", head, re.I):
        return True
    if re.search(r"\bмашин\w*\s+к\s+нам\b", head, re.I) and re.search(
        r"\b(?:мастерприем\w*\s+звонил|по\s+готовност|дозвонил)\w*",
        head,
        re.I,
    ):
        return True
    return False


def _is_outbound_site_lead_redirect_other_phone_not_narrow_to(low: str) -> bool:
    """
    Исходящий по заявке с сайта: клиент просит набрать на другой номер / согласовать с другим
    контактом — разговор о записи на ТО не состоялся (8675). Маркеры «второе ТО» в скрипте дилера
    не дают узкую СТО_ТО_*.
    """
    head = (low or "")[:4000]
    site_lead = bool(
        ("по вашей заявке" in head or "по заявке" in head)
        and ("звоню" in head or "звоним" in head)
        and (
            "оставляли на сайте" in head
            or "оставили на сайте" in head
            or "оставляли заявку" in head
            or "запись на техническое обслуживание" in head
        )
    )
    if not site_lead:
        return False
    return any(
        p in head
        for p in (
            "по другому номеру",
            "другому номеру",
            "другой номер",
            "наберите на друг",
            "набрать на друг",
            "согласовать",
        )
    )


def _is_wrong_number_redirect_other_dealer_not_narrow_to(low: str) -> bool:
    """
    Входящий «не туда попали»: клиент просит запись для другого дилера/бренда,
    ассистент объясняет, что обслуживает только свои бренды, и дает чужой телефон.
    Это НЕ_ТО для узкой рубрики (22071).
    """
    head = (low or "")[:4200]
    if not head.strip():
        return False
    has_wrong_number = bool(
        re.search(
            r"\b(?:не\s+туда\s+попал|не\s+туда\s+позвонил|ошиб(?:ся|лись)\s+номером)\b",
            head,
            re.I,
        )
        or (
            re.search(r"\bвы\s+позвонили\b", head, re.I)
            and re.search(r"\bмы\s+обслужива\w*\b", head, re.I)
        )
    )
    if not has_wrong_number:
        return False
    has_redirect = bool(
        re.search(
            r"\b(?:у\s+них\s+телефон|их\s+телефон|они\s+находятся|"
            r"обратитесь|позвоните\s+(?:им|туда)|номер\s+у\s+них)\b",
            head,
            re.I,
        )
    )
    if not has_redirect:
        return False
    has_foreign_brand_request = bool(
        re.search(
            r"\b(?:toyota|то[йи]от\w*|лада|vesta|веста|kia|hyundai|mazda|"
            r"skoda|renault|volkswagen|vw)\b",
            head,
            re.I,
        )
    )
    has_new_booking_progress = bool(
        re.search(
            r"\b(?:записал[аи]?\s+(?:вас|нас)|записыва(?:ю|ем)\s+(?:вас|на)|"
            r"ожидаем\s+вас|предлага(?:ю|ем)\s+(?:дат\w*|врем\w*|\d{1,2}(?::\d{2})?)|"
            r"\bслот\w*\b)\b",
            head,
            re.I,
        )
    )
    return has_foreign_brand_request and not has_new_booking_progress


def _is_outbound_prior_booking_recall_not_new_to_intake(low: str) -> bool:
    """
    Исходящий: диспетчер отсылается к УЖЕ созданной записи («мы вчера/сегодня с вами записывали
    автомобиль на … на техническое обслуживание») и дальше предлагает слот / перенос — не приём
    новой заявки на ТО в узком слое (7873, 10915). Явные маркеры «техническое обслуживание» здесь —
    напоминание контекста записи, а не тема новой записи на регламентное ТО.
    """
    if not any(
        p in low
        for p in (
            "диспетчер сервиса",
            "ассистент сервиса",
            "администратор сервиса",
        )
    ):
        return False
    prior_booking = bool(
        re.search(
            r"\bмы\s+(?:сегодня|вчера|позавчера)\s+с\s+вами\s+записывал",
            low,
        )
        or re.search(
            r"\b(?:сегодня|вчера|позавчера)\s+с\s+вами\s+записывал",
            low,
        )
        or re.search(
            r"\bзаписывал[аи]?\s+(?:вас|вам)\s+(?:сегодня|вчера|позавчера)",
            low,
        )
    )
    if prior_booking:
        return True
    # 10915: корректировка слота уже созданной записи (праздник и т.п.).
    return bool(
        re.search(r"\bзаписывал[аи]?\s+автомобил", low)
        and re.search(r"\bкорректиров", low)
    )


def _is_outbound_sto_dispatcher_context(low: str, *, head_limit: int = 1400) -> bool:
    head = (low or "")[:head_limit]
    return any(
        p in head
        for p in (
            "диспетчер сервиса",
            "ассистент сервиса",
            "администратор сервиса",
        )
    ) or ("диспетчер" in head and "викинг" in head)


def _is_outbound_existing_to_reschedule_not_narrow_to(low: str) -> bool:
    """
    Исходящий: диспетчер по уже созданной записи на ТО предлагает перенос слота
    («мы с вами … записались на … ТО», «время освободилось», «перенести») — узкий НЕ_ТО (13750).
    Смета «N-е ТО» в хвосте — контекст визита, не новая СТО_ТО_исх.
    """
    if not _is_outbound_sto_dispatcher_context(low):
        return False
    head = (low or "")[:2200]
    # 21455: CRM-исходящий по заявке на нулевое/регламентное ТО с валидацией
    # «всё ли верно/актуально» — это не перенос существующей записи в узком слое.
    if any(m in head for m in _CRM_LEAD_CALLBACK_MARKERS) and (
        re.search(r"\bвс[её]\s+ли\s+верно\b", head, re.I)
        or re.search(r"\bактуальн\w*\b", head, re.I)
    ) and (
        re.search(r"\bнулев\w*\s+то\b", head, re.I)
        or crm_outbound_explicit_to_topic_present(head, head_limit=len(head))
    ):
        return False
    if _is_inbound_opening_regulatory_to_booking(head):
        return False
    if _inbound_client_new_regulatory_to_booking_intake(head):
        return False
    explicit_reschedule_of_existing_slot = bool(
        re.search(
            r"\b(?:можем|можно)\s+перенест\w*[^.!?]{0,80}\b(?:с|со)\s+\d{1,2}(?:-?го)?\b[^.!?]{0,60}\bна\s+\d{1,2}(?:-?е|-?го)?\b",
            head,
            re.I,
        )
        or re.search(
            r"\b(?:время|окно)\s+освободил\w*[^.!?]{0,80}\bперенест\w*\b",
            head,
            re.I,
        )
        or re.search(
            r"\bперенест\w*[^.!?]{0,80}\b(?:ваше|ваш)\s+то\b",
            head,
            re.I,
        )
    )
    if not _inbound_existing_to_booking_fact_present(low, head_limit=len(head)) and not explicit_reschedule_of_existing_slot:
        return False
    return any(
        p in head
        for p in (
            "перенести",
            "перенесём",
            "перенесем",
            "переносим",
            "перенос записи",
            "освободилось",
            "другое время",
            "другой день",
        )
    ) or bool(re.search(r"\bвремя\s+освободил", head, re.I))


def _is_outbound_appointment_reminder(low: str) -> bool:
    """
    Исходящее напоминание/подтверждение УЖЕ существующей записи на сервис:
    диспетчер сервиса звонит клиенту перед приездом — «по записи звоню на завтра»,
    «звоню напомнить/подтвердить запись», «вы подъедете, актуально?».
    Применяется ТОЛЬКО к исходящим (broad_call_type == STO_OUT) для исключения
    из узкой рубрики STO_TO_*: это операционная проверка, не приём заявки на ТО.
    """
    head = (low or "")[:1200]
    triggers = (
        re.search(r"по\s+записи\s+звоню", head),
        # 18096 STT: «я по записи уточнить звоню» — между «по записи» и «звоню» вставка.
        re.search(r"по\s+записи[^.!?]{0,40}?звоню", head),
        re.search(r"по\s+записи\s+уточн", head),
        re.search(r"звоню\s+по\s+записи", head),
        re.search(r"звоню\s+по\s+поводу\s+(?:вашей\s+)?записи", head),
        re.search(r"звоню\s+(?:вам\s+|вас\s+)?напомнить", head),
        re.search(r"звоню\s+(?:вам\s+|вас\s+)?напомина", head),
        re.search(r"звоню\s+(?:вам\s+|вас\s+)?подтвердить", head),
        re.search(r"звоню\s+(?:вам\s+|вас\s+)?подтверд\w*\s+(?:запис|приезд)", head),
        re.search(r"\bзавтра\s+записан[аы]?\s+к\s+нам\b", head),
        re.search(r"\bзаписан[аы]?\s+к\s+нам\s+на\b", head),
        (
            "хотели уточнить" in head
            and re.search(r"\bзаписан[аы]?", head)
            and re.search(r"\bна\s+то\b", head)
        ),
        # 18096: «на завтра … планировали автомобиль запись?» — подтверждение слота.
        re.search(
            r"\b(?:на\s+)?завтра\b[^.!?]{0,80}\bпланировали\b[^.!?]{0,60}автомобил",
            head,
            re.I,
        ),
        # 18107: «записывали на завтра, автомобиль … на техническое обслуживание».
        re.search(r"\bзаписывали\s+на\s+завтра\b", head),
        re.search(
            r"\bзаписывали\b[^.!?]{0,40}\bна\s+завтра\b[^.!?]{0,60}автомобил",
            head,
            re.I,
        ),
        # 18153: «вы сегодня к нам записаны / записывались».
        re.search(r"\bвы\s+(?:сегодня|завтра)\s+к\s+нам\s+записан[аы]?\b", head),
        re.search(r"\bвы\s+(?:сегодня|завтра)\s+к\s+нам\s+записывал", head),
    )
    if not any(triggers):
        return False
    if any(
        p in head
        for p in (
            "подъедете",
            "подъедите",
            "ожидаем вас",
            "ожидаем",
            "ждём вас",
            "ждем вас",
            "приедете",
            "в силе",
            "вы будете",
            "вас ожидаем",
        )
    ) and (
        re.search(r"\bзавтра\s+записан[аы]?\s+к\s+нам\b", head)
        or re.search(r"\bзаписан[аы]?\s+к\s+нам\s+на\b", head)
        or re.search(r"\bзаписывали\s+на\s+завтра\b", head)
        or re.search(r"\bвы\s+(?:сегодня|завтра)\s+к\s+нам\s+записан", head)
    ):
        return True
    sto_intro = any(
        p in low
        for p in (
            "диспетчер сервиса",
            "администратор сервиса",
            "ассистент сервиса",
            "ассистент-сервис",
            "сервис-консультант",
            "сервисный консультант",
            "сервис консультант",
            "сервис-менеджер",
            "сервисный менеджер",
        )
    ) or ("диспетчер" in head and "викинг" in head) or (
        # 18153 STT: «Чери Центр … Владимир беспокоит» без роли «диспетчер сервиса».
        "беспокоит" in head
        and ("чери" in head or "викинг" in head or "заставн" in head)
    )
    sto_topic = any(
        p in low
        for p in (
            "техническое обслуживание",
            "техобслуживание",
            "записаны на",
            "записаны к нам",
            "записывали на",
            "на сервис",
            "на ремонт",
            "на диагностик",
        )
    ) or bool(re.search(r"\bна\s+то\b", head)) or bool(
        re.search(r"\bпланировали\b[^.!?]{0,40}(?:автомобил\w*\s+)?запис", head, re.I)
    )
    return sto_intro or sto_topic


def _is_outbound_to_quote_parts_continuation_not_narrow_to(low: str) -> bool:
    """
    Исходящий перезвон-продолжение разговора: наличие запчастей и смета на ТО, без приёма новой
    заявки «записаться на ТО» (8575, 15798, 15679). «Техническое обслуживание» здесь — контекст сметы, не узкий скрипт.
    """
    head = (low or "")[:4000]
    early = (low or "")[:1400]
    opening_early = (low or "")[:900]
    # 16923: вх. клиент с начала «сколько будет стоить ТО» — не перезвон-продолжение сметы.
    if re.search(r"\bсколько\s+(?:будет\s+)?стоит\w*\s+то\b", opening_early, re.I):
        return False
    if re.search(r"\b(?:рассчитать|посчитать)[^.!?]{0,50}\bто\b", opening_early, re.I):
        return False
    continuation = bool(
        re.search(r"ещ[её]\s+раз\s+добрый", early)
        or re.search(r"ещ[её]\s+раз\s+здравствуйте", early)
        or re.search(r"ещ[её]\s+раз\s*:", early)
        # После _normalize_text «ещё раз: Викинги» → «еще раз викинги» (15679).
        or re.search(r"ещ[её]\s+раз\s+(?:викинг|диспетчер|ассистент)", early)
        or re.search(r"снова\s+добрый", early)
        or "всё подобрали" in early
        or "все подобрали" in early
    )
    parts_to_quote = (
        "запчаст" in head
        and "в наличии" in head
        and ("техническое обслуживание" in head or "техобслуживание" in head or "стоимость" in head)
    ) or (
        "подобрали" in early
        and "в наличии" in head
        and (
            "техническое обслуживание" in head
            or "техобслуживание" in head
            or "стоимость" in head
            or "общая сумма" in head
            or ("регламент" in head and re.search(r"\bто\b", head))
            or (re.search(r"\bто\b", head) and ("пробег" in head or "тысяч" in head))
        )
    ) or (
        continuation
        and re.search(r"по\s+стоимост\w*\s+звон", early)
        and (
            "посчитали" in early
            or re.search(r"по\s+стоимост\w*\s+получ", early)
        )
        and (
            re.search(r"\bзарезервиров", head)
            or re.search(r"\bстоимост\w*\s+то\b", head)
        )
    ) or (
        continuation
        and "запчаст" in head
        and (
            re.search(r"\bпришл", head)
            or re.search(r"\bпоступил", head)
            or re.search(r"\bпланируем\s+в\s+работ", head)
            or re.search(r"\bв\s+работ\w*\s+взя", head)
        )
    )
    if not continuation or not parts_to_quote:
        return False
    new_to_intake = any(
        p in early
        for p in (
            "записаться на то",
            "запись на то",
            "хотели бы",
            "оставляли на сайте",
            "получили заявку",
            "получили вашу заявку",
            "записываю вас на",
        )
    ) or bool(
        re.search(r"\b(?:сможете|можете)\s+записать\b", low, re.I)
        or re.search(r"\bзапис\w+\s+меня\b", low, re.I)
        or re.search(r"\bзапиш\w+\s+меня\b", low, re.I)
    )
    return not new_to_intake


def _is_inbound_repeat_existing_non_to_application_not_narrow_to(low: str) -> bool:
    """
    Повторный входящий контакт по уже созданной заявке/заказу работ
    (вчера созванивались, заявка уже есть), где «ТО» звучит как ссылка на прошлый
    контекст, а текущая тема — отдельные работы/запчасти.
    """
    head = (low or "")[:4500]
    if not head.strip():
        return False
    repeat_contact = bool(
        re.search(
            r"\b(?:вчера|сегодня)\b[^.!?]{0,80}\b(?:созванив\w*|разговарив\w*|общал\w*)\b",
            head,
            re.I,
        )
        or re.search(r"\bвы\s+звонил\w*\b", head, re.I)
    )
    if not repeat_contact:
        return False
    existing_application = bool(
        re.search(r"\bзаявк\w*[^.!?]{0,50}\b(?:нашл\w*|есть|вижу)\b", head, re.I)
        or re.search(r"\b(?:нашл\w*|вижу)\b[^.!?]{0,40}\bзаявк\w*\b", head, re.I)
    )
    if not existing_application:
        return False
    non_reg_work_topic = bool(
        re.search(
            r"\b(?:диск\w*|колодк\w*|тормозн\w*\s+диск\w*|замен\w+\s+передн\w+\s+диск\w*)\b",
            head,
            re.I,
        )
    )
    if not non_reg_work_topic:
        return False
    to_reference_opening = bool(
        re.search(
            r"\bпо\s+поводу\s+запис[^\n.!?]{0,60}\b(?:то|техническ\w+\s+обслуживан\w*)\b",
            head[:900],
            re.I,
        )
    )
    if not to_reference_opening:
        return False
    strong_new_reg_to_now = bool(
        re.search(
            r"\b(?:запис(?:ать|аться|ыва\w*)\s+на\s+то|записал[аи]?\s+вас\s+на\s+то|"
            r"на\s+(?:перв|втор|трет|четвер|пят|шест)\w*\s+то)\b",
            head,
            re.I,
        )
    )
    return not strong_new_reg_to_now


def _is_deferred_parts_after_completed_to_visit_not_narrow_to(low: str) -> bool:
    """10769/13781: после ТО — замена по дефекту/запчастям, не новая СТО_ТО_*."""
    return _is_deferred_repair_after_past_to_visit_intake(low)


def _is_outbound_during_to_additional_work_not_narrow_to(low: str) -> bool:
    """
    14039: исходящий от мастера во время текущего ТО — согласование допработ
    (лампа, дефект), не запись на регламентное ТО в узком СТО_ТО_исх.
    «Техническое обслуживание» здесь — контекст визита в работе, не CRM-запись.
    """
    head = (low or "")[:4500]
    if not head.strip():
        return False
    if any(
        p in head
        for p in (
            "записаться на то",
            "запись на то",
            "запишу вас на",
            "записали вас на",
            "пригласить записаться",
            "хотим вам пригласить",
            "хотите сделать техническое обслуживание",
            "заявку получили",
            "оставляли заявку",
            "мне передали",
        )
    ):
        return False
    if _is_sto_outbound_lead_request_to_callback_for_narrow(head):
        return False
    during_to = bool(
        re.search(r"\bв\s+процессе\s+техническ\w+\s+обслужив", head, re.I)
        or re.search(r"\b(?:во\s+)?время\s+техническ\w+\s+обслужив", head, re.I)
        or re.search(
            r"\b(?:на|при)\s+техническ\w+\s+обслужив\w*[^.!?]{0,40}\bобнаруж",
            head,
            re.I,
        )
    )
    if not during_to:
        return False
    return bool(
        re.search(r"\bобнаруж", head, re.I)
        or re.search(r"\bвыяв", head, re.I)
        or re.search(r"\bрекоменду\w*\s+(?:поменя|замен|произвести|выполн)", head, re.I)
        or re.search(r"\bдополнительн\w*\s+работ", head, re.I)
    )


def _is_outbound_ac_refrigerant_service_not_narrow_to(low: str) -> bool:
    """
    Исходящий: заправка/диагностика кондиционера, эвакуация — не узкое СТО_ТО (10267).
    «Техническое обслуживание» в речи — зачёт оплаты на будущее ТО, не приём новой заявки.
    Не путать с CRM-перезвоном «мне передали… на ТО» + неисправность (9088).
    """
    head = (low or "")[:5000]
    if any(
        p in head[:2200]
        for p in (
            "мне передали",
            "передали то, что",
            "оставляли заявку",
            "заявку получили",
            "получили заявку",
            "хотите сделать техническое обслуживание",
            "записаться на техническое",
        )
    ):
        return False
    has_direct_ac_markers = any(
        p in head for p in ("фреон", "эвакуац", "красител", "ультрафиолет")
    )
    has_refuel_word = "заправк" in head
    has_ac_context = bool(re.search(r"\b(?:кондиц\w*|климат\w*)\b", head, re.I))
    # «заправка/заправляюсь» без контекста кондиционера не считаем AC-сервисом:
    # это может быть обсуждение топлива в обычной записи на ТО.
    if not (has_direct_ac_markers or (has_refuel_word and has_ac_context)):
        return False
    if any(
        p in head
        for p in (
            "записаться на то",
            "запись на то",
            "на то запис",
            "запишу вас на",
            "записали вас на",
        )
    ):
        return False
    return True


def _is_outbound_recall_before_scheduled_to_visit(low: str) -> bool:
    """
    Исходящий «перезвон перед визитом на ТО/сервис»: напоминание о записи, а не приём новой заявки
    на регламентное ТО в узком слое. В тексте часто есть «техническое обслуживание» как контекст
    уже существующей записи — без этой ветки explicit_narrow даёт СТО_ТО_исх (call_id 8093).
    """
    before_visit = (
        "перед визитом" in low
        or "перед вашим визитом" in low
        or "перед приездом" in low
        or bool(re.search(r"накануне[\s,.]{0,18}(?:визита|вашего\s+визита)", low))
    )
    if not before_visit:
        return False
    recall = bool(
        re.search(r"\bперезвон", low)
        or re.search(r"\bперезванива", low)
        or re.search(r"\bотзвон", low)
        or re.search(r"\bзвон(?:ю|им)\s+.{0,40}напомн", low)
        or "напоминаем" in low
    )
    if not recall:
        return False
    booking_or_to = bool(
        re.search(r"\bна\s+то\b", low)
        or "техническое обслуживание" in low
        or "техобслуживание" in low
        or re.search(r"\bпо\s+записи\b", low)
        or "записаны на" in low
        or re.search(r"\bвы\s+записан", low)
        or re.search(r"\bзаписывали\s+автомобиль\b", low)
    )
    if not booking_or_to:
        return False
    # Новая запись на визит в этом же звонке — оставляем узкую СТО_ТО_исх.
    if re.search(r"\bзаписал[аи]?\s+вас\s+на\b", low) or "записываю вас на" in low:
        return False
    if _is_sto_outbound_lead_request_to_callback_for_narrow(low):
        return False
    return True


_CRM_LEAD_CALLBACK_MARKERS = (
    "вы оставляли заявку",
    "вы оставили заявку",
    "оставляли заявку",
    "заявку оставляли",
    "вы заявку оставляли",
    "получили заявку",
    "получили вашу заявку",
    "заявку получили",
    "мы заявку получили",
    "мы вашу заявку получили",
    "мы просто заявку получили",
    "просто заявку получили",
    "пришла заявка",
    "пришла заявк",
    "заявка пришла",
    "нам заявка пришла",
    "по вашей заявке",
    "по заявке",
    "звоню по вашей заявке",
    "звоню по заявке",
)


def _crm_lead_explicit_to_booking_in_opening(head: str) -> bool:
    """Тема регламентного ТО в начале перезвона по CRM-заявке (позитивное обсуждение, не «без то…»)."""
    if crm_outbound_explicit_to_topic_present(head, head_limit=len(head or "")):
        return True
    if re.search(r"\bна\s+то\s+хотел\w*\s+запис", head, re.I):
        return True
    # 13631: «на ТО хотите к нам записаться?»; 15657: «на ТО, да, к нам хотите записаться?»
    if re.search(r"\bна\s+то\b[^.!?]{0,80}?\bзапис", head, re.I):
        return True
    if re.search(r"пришла\s+заявк\w*[^.!?]{0,120}на\s+то", head, re.I):
        return True
    return _positive_regulatory_to_discussion_present(head)


def _is_sto_outbound_lead_request_to_callback_for_narrow(low: str) -> bool:
    """
    Исходящий СТО: перезвон по лиду/CRM на запись на регламентное ТО.
    Голое «заявку получили» без темы ТО (камера, ремонт) — узкий НЕ_ТО (9746).
    """
    head = (low or "")[:2800]
    if any(m in head for m in _CRM_LEAD_CALLBACK_MARKERS):
        return _crm_lead_explicit_to_booking_in_opening(head)
    # 9255: «передали ваш номер, записаться на сервис» + ТО/диагностика в том же звонке.
    phone_handoff = any(
        m in head
        for m in (
            "мне передали",
            "передали ваш номер",
            "передали ваш телефон",
            "ваш номер передали",
            "ваш телефон передали",
        )
    )
    service_booking = (
        "записаться на сервис" in head
        or "запись на сервис" in head
        or ("хотели бы" in head and "запис" in head and "сервис" in head)
    )
    to_or_service = bool(
        re.search(r"\bто\s+уже\s+надо\s+делать\b", head, re.I)
        or re.search(r"\bтехническ\w+\s+обслуживан", head, re.I)
        or "техобслуж" in head
        or (
            re.search(r"\bто\b", head)
            and ("надо делать" in head or "какие работы" in head)
        )
    )
    return phone_handoff and service_booking and to_or_service


def _is_outbound_crm_lead_client_already_booked_not_narrow_to(low: str) -> bool:
    """
    15502: исх. перезвон по CRM-заявке на ТО; клиент уже записан (в т.ч. у другого ДЦ) —
    новая запись в этом звонке не состоялась → узкий НЕ_ТО.
    """
    head = (low or "")[:3200]
    outbound_ctx = _is_outbound_sto_dispatcher_context(low) or bool(
        "викинг" in head
        and re.search(r"\bменя\s+зовут\b", head)
        and (
            "удобно разговаривать" in head
            or "удобно говорить" in head
            or any(m in head for m in _CRM_LEAD_CALLBACK_MARKERS)
        )
    )
    if not outbound_ctx:
        return False
    crm_lead = any(m in head for m in _CRM_LEAD_CALLBACK_MARKERS) or (
        "заявк" in head and "автомобил" in head
    )
    if not crm_lead:
        return False
    already_booked = bool(
        re.search(r"\b(?:я|мы)\s+уже\s+записал\w*\s+на\s+то\b", head, re.I)
        or re.search(r"\b(?:я|мы)\s+уже\s+записан[аы]?\s+на\s+то\b", head, re.I)
        or re.search(r"\bпоэтому\s+я\s+уже\s+записал\w*\b", head, re.I)
    )
    if not already_booked:
        return False
    if re.search(r"\bзаписал[аи]?\s+вас\s+на\b", low, re.I) or "вас записали" in (low or ""):
        return False
    return True


def _is_outbound_sto_service_repeat_context(head: str) -> bool:
    """
    Контекст исходящего сервиса для звонка-продолжения.
    Шире, чем только «диспетчер»: 28564 — «компания Викинги, Юлия» без роли.
    """
    if _is_outbound_sto_dispatcher_context(head):
        return True
    if "викинг" not in (head or ""):
        return False
    companyish = bool(
        "компания викинг" in head
        or re.search(r"\bвикинг\w*[^.!?]{0,60}\b(?:юлия|ольга|анна|мария|елена|ирина)\b", head, re.I)
        or re.search(r"\bкомпания\s+викинг\w*\s*,?\s*[а-яё]{2,20}\b", head, re.I)
    )
    to_topic = bool(
        re.search(r"\b(?:^|[^\w])то(?:[^\w]|$)", head, re.I)
        or "техническ" in head
        or "техобслуж" in head
        or ("масл" in head and ("фильтр" in head or "объём" in head or "объем" in head))
        or "заявленн" in head
    )
    return companyish and to_topic


def _is_outbound_sto_repeat_greeting(head: str) -> bool:
    """Повторное приветствие исходящего СТО («ещё раз добрый день / здравствуйте»)."""
    return bool(
        re.search(
            r"\b[а-яё]{2,25}\s*(?:,\s*|\s+)ещ[её]\s+раз\s+добрый\s+(?:день|вечер)\b",
            head,
            re.I,
        )
        or re.search(
            r"\b[а-яё]{2,25}\s*(?:,\s*|\s+)добрый\s+(?:день|вечер)\s+ещ[её]\s+раз\b",
            head,
            re.I,
        )
        or re.search(
            r"\b[а-яё]{2,25}\s*(?:,\s*|\s+)ещ[её]\s+раз\s+здравствуйте\b",
            head,
            re.I,
        )
        or re.search(
            r"\bалло[^.!?]{0,100}\bещ[её]\s+раз\s+(?:добрый|здравствуйте)\b",
            head,
            re.I,
        )
    )


def _has_immediate_repeat_reconnect_marker(head: str) -> bool:
    """
    Признак немедленного продолжения того же разговора в этом же тексте
    (28660: «звонила только что ... порвался звонок»).
    """
    return bool(
        re.search(r"\b(?:я\s+вас\s+)?звонил\w*\s+только\s+что\b", head, re.I)
        or re.search(r"\bтолько\s+что\s+звонил\w*\b", head, re.I)
        or re.search(r"\b(?:звонок|связ\w*)\s+(?:порвал\w*|оборвал\w*|прервал\w*|пропал\w*)\b", head, re.I)
        or re.search(r"\b(?:порвал\w*|оборвал\w*|прервал\w*|пропал\w*)\s+(?:звонок|связ\w*)\b", head, re.I)
    )


def _has_outbound_sto_continuation_marker(head: str) -> bool:
    """
    Признаки продолжения прерванного/прошлого разговора (группы A/B/C/D).
    Голого «вас записали» недостаточно — иначе ломается 16148 (самостоятельная смета+запись).
    """
    # A. Явная отсылка к прошлому контакту
    if any(
        p in head
        for p in (
            "как я вам уже говорила",
            "как я вам уже говорил",
            "как я уже говорила",
            "как я уже говорил",
            "как мы уже говорили",
            "как мы с вами говорили",
            "не договорили",
            "не договорились",
            "продолжим разговор",
            "продолжим наш разговор",
            "вернуться к разговору",
            "вернулись к разговору",
        )
    ):
        return True
    if re.search(
        r"\b(?:мы|я)\s+с\s+(?:вами|там)\s+(?:сегодня\s+|вчера\s+|ранее\s+)?(?:общал\w*|разговаривал\w*|созванивал\w*)\b",
        head,
        re.I,
    ):
        return True
    if re.search(
        r"\b(?:сегодня|вчера|ранее)\s+(?:с\s+вами\s+)?(?:общал\w*|разговаривал\w*|созванивал\w*)\b",
        head,
        re.I,
    ):
        return True
    if re.search(r"\bтолько\s+что\s+(?:с\s+вами\s+)?(?:говорил\w*|общал\w*|разговаривал\w*)\b", head, re.I):
        return True
    if re.search(r"\b(?:я\s+вас\s+)?звонил\w*\s+только\s+что\b", head, re.I):
        return True
    if re.search(r"\bтолько\s+что\s+звонил\w*\b", head, re.I):
        return True
    if re.search(r"\b(?:я\s+)?(?:вот\s+)?сейчас\s+(?:с\s+вами\s+)?(?:общал\w*|разговаривал\w*)\b", head, re.I):
        return True
    if re.search(r"\bсвяз\w*\s+(?:прервал\w*|оборвал\w*|пропал\w*)", head, re.I):
        return True
    if re.search(r"\b(?:прервал\w*|оборвал\w*)\s+[^.!?]{0,40}\b(?:разговор|связь)\b", head, re.I):
        return True
    if re.search(r"\b(?:звонок|связ\w*)\s+(?:порвал\w*|оборвал\w*|прервал\w*|пропал\w*)\b", head, re.I):
        return True
    if re.search(r"\b(?:порвал\w*|оборвал\w*|прервал\w*|пропал\w*)\s+(?:звонок|связ\w*)\b", head, re.I):
        return True
    if re.search(r"\bпродолж\w*[^.!?]{0,40}\b(?:предыдущ\w*|прошл\w*)\s+разговор", head, re.I):
        return True

    # B. Исправление / доуточнение уже обсуждённого
    if any(
        p in head
        for p in (
            "нашли ошибку",
            "нашла ошибку",
            "нашёл ошибку",
            "нашел ошибку",
            "неверно посчитали",
            "неправильно посчитали",
            "пересчитали",
            "пересчитали стоимость",
            "скорректировали смету",
            "скорректировали стоимость",
            "уточнили стоимость",
            "на сайте актуальная",
            "на сайте актуальна",
        )
    ):
        return True
    if re.search(r"\bневерн\w*[^.!?]{0,40}\b(?:количеств\w*|объ[её]м\w*|стоимост\w*|масл\w*)\b", head, re.I):
        return True
    if re.search(r"\bобъ[её]м\w*\s+заявленн\w*", head, re.I):
        return True
    if re.search(
        r"\bпо\s+поводу\s+того[^.!?]{0,40}\b(?:говорил\w*|обсужд\w*|общал\w*)\b",
        head,
        re.I,
    ):
        return True
    if re.search(r"\bпо\s+вашему\s+то\b", head, re.I) and re.search(
        r"\b(?:уточн|ошибк|пересчит|скорректир)\w*",
        head,
        re.I,
    ):
        return True

    # C. Узкое продолжение слота (не голое «вас записали»)
    if re.search(r"\bдавайте\s+вс[её]\s*-?\s*таки\s+запиш", head, re.I):
        return True
    if re.search(r"\bтогда\s+получается[^.!?]{0,60}\b(?:записал|запишем|запись)\w*", head, re.I):
        return True
    if re.search(r"\bдоговор\w*\s+с\s+вами\s+(?:ранее|раньше|сегодня|вчера)\b", head, re.I):
        return True

    # D. Обратный дозвон по тому же диалогу
    if any(
        p in head
        for p in (
            "вы звонили",
            "вы мне звонили",
            "не дозвонились",
            "не дозвонился",
            "не дозвонилась",
            "перезваниваю по нашему разговору",
            "перезваниваю по разговору",
            "зову по нашему разговору",
        )
    ):
        return True
    if re.search(r"\bперезвон\w*[^.!?]{0,50}\b(?:наш(?:ему)?\s+)?разговор", head, re.I):
        return True
    return False


def _is_outbound_sto_repeat_followup_not_narrow_to(low: str) -> bool:
    """
    Исходящий звонок-продолжение сервиса (не новый самостоятельный цикл СТО_ТО_*).

    Ворота: контекст сервиса + (повторное приветствие ИЛИ явный маркер «только что созванивались/оборвался звонок»).
    Плюс ≥1 признак продолжения (A/B/C/D) — см. `_has_outbound_sto_continuation_marker`.
    Кейсы: 21564, 28058, 28564, 28660.
    """
    head = (low or "")[:2600]
    if not head.strip():
        return False
    if not _is_outbound_sto_service_repeat_context(head):
        return False
    has_repeat_greeting = _is_outbound_sto_repeat_greeting(head)
    has_reconnect = _has_immediate_repeat_reconnect_marker(head)
    if not has_repeat_greeting and not has_reconnect:
        return False
    if not _has_outbound_sto_continuation_marker(head):
        return False
    # Если это явный первичный лид-звонок по новой заявке — не перехватываем.
    if any(m in head for m in _CRM_LEAD_CALLBACK_MARKERS):
        return False
    return True


def _is_outbound_existing_visit_with_confirmed_new_to_booking(low: str) -> bool:
    """
    Исходящий звонок может стартовать как уточнение, но завершиться новой записью на ТО.
    В этом случае не применяем auto-НЕ_ТО по existing_visit_clarification (22085).
    """
    head = (low or "")[:5600]
    if not head.strip():
        return False
    opening = head[:2200]
    if not (
        any(m in opening for m in _CRM_LEAD_CALLBACK_MARKERS)
        or "звонок с сайта" in opening
    ):
        return False
    has_to_topic = bool(
        explicit_narrow_sto_to_topic_hit(head)[0]
        or re.search(
            r"\b(?:техническ\w+\s+обслуживан\w*|техобслуж\w*|то|нулев\w+\s+то)\b",
            head,
            re.I,
        )
    )
    if not has_to_topic:
        return False
    has_strong_new_to_booking = bool(
        re.search(
            r"\b(?:я\s+вас\s+записал[аи]?[^.!?]{0,50}техническ\w+\s+обслуживан\w*|"
            r"вас\s+записал[аи]?[^.!?]{0,50}техническ\w+\s+обслуживан\w*)\b",
            head,
            re.I,
        )
    )
    has_new_slot_confirmation = has_strong_new_to_booking and bool(
        re.search(
            r"\b(?:я\s+вас\s+записал[аи]?|вас\s+записали|записал[аи]\s+вас\s+на|"
            r"накануне[^.!?]{0,40}(?:позвон\w*|напомн\w*)|"
            r"смс[^.!?]{0,40}(?:направл\w*|отправл\w*)|ожидаем(?:\s+вас)?|подъедете)\b",
            head,
            re.I,
        )
        and (
            re.search(r"\b(?:в|на)\s+\d{1,2}(?::|\.)\d{2}\b", head, re.I)
            or re.search(
                r"\b(?:сегодня|завтра|послезавтра|понедельник|вторник|сред[ау]|четверг|пятниц\w*|суббот\w*|воскресень\w*)\b",
                head,
                re.I,
            )
            or re.search(
                r"\b(?:январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\b",
                head,
                re.I,
            )
        )
    )
    # 25877: CRM-обзвон по заявке, в диалоге подбирают окно
    # («пораньше/попозже», выбор 11:30) + подтверждение СМС/напоминания.
    if not has_new_slot_confirmation:
        has_slot_offer_dialog = bool(
            re.search(
                r"\b(?:могу\s+предложить|есть\s+время|пораньше|попозже)\b",
                head,
                re.I,
            )
            and re.search(
                r"\b(?:давайте|подойд[её]т|удобн\w*|попозже|пораньше)\b"
                r"[^.!?]{0,35}\b\d{1,2}(?::\d{2})?\b",
                head,
                re.I,
            )
            and re.search(
                r"\b(?:накануне[^.!?]{0,40}(?:свяж\w*|позвон\w*|напомн\w*)|"
                r"смс[^.!?]{0,40}(?:направл\w*|отправл\w*)|напомн\w*\s+о\s+визит)\b",
                head,
                re.I,
            )
        )
        has_new_slot_confirmation = has_slot_offer_dialog
    return has_new_slot_confirmation


def _inbound_assistant_transfer_failed_excludes_narrow_sto_to(low: str) -> bool:
    """
    Входящий: перевод на ассистента/диспетчера сервиса по теме ТО не состоялся (7767).
    В узкой рубрике — НЕ_ТО, даже если в тексте уже есть явные маркеры регламентного ТО
    (меню/бот, фоновые фразы) без фактического скрипта записи с линией приёмки.
    """
    failed = any(
        p in low
        for p in (
            "не получился перевод",
            "не получиля перевод",
            "не получился переключ",
            "не получилось перевести",
            "не получилось переключить",
            "перевод не получился",
            "перевести не получилось",
            "переключить не получилось",
            "не удалось перевести",
            "не удалось переключить",
            "не смогли перевести",
            "не смог перевести",
            "соединить не удалось",
            "не удалось соединить",
        )
    )
    if not failed:
        return False
    service_ctx = any(
        p in low
        for p in (
            "ассистент сервис",
            "диспетчер сервис",
            "на сервис",
            "записаться на то",
            "запись на то",
            "на то записаться",
            "техническое обслуживание",
            "техобслуживание",
        )
    )
    if not service_ctx:
        return False
    # Дальше по тексту всё же зафиксирована запись на визит — не отменяем узкую СТО_ТО.
    if re.search(r"записал[аи]?\s+вас\s+на", low) or "подтверждаю запись" in low:
        return False
    if "ждем вас" in low or "ждём вас" in low:
        return False
    return True


def _tail_after_service_line_handoff(low: str) -> Tuple[int, str]:
    """
    (индекс начала хвоста после перевода, хвост). (-1, "") — якорь не найден.
    """
    if not low:
        return -1, ""
    bridge_ends: list[int] = []
    for b in (
        "переключу на ассистента сервиса",
        "переключаю вас на ассистента сервиса",
        "переключаю на ассистента сервиса",
        "переведу на ассистента сервиса",
        "перевожу на ассистента сервиса",
        "переведу вас на ассистента сервиса",
        "переключу на диспетчера сервиса",
        "переключаю на диспетчера сервиса",
        "переведу на диспетчера сервиса",
    ):
        i = low.find(b)
        if i >= 0:
            bridge_ends.append(i + len(b))
    pos = max(bridge_ends) if bridge_ends else -1
    if pos < 0:
        m = re.search(
            r"(?:переключ|перевед)[а-яё\s,.]{8,100}?(?:ассистент|диспетчер)\s+сервиса",
            low,
        )
        if m:
            pos = m.end()
    if pos < 0:
        return -1, ""
    tail = low[pos:].strip()
    return pos, tail


def _inbound_parts_topic_after_service_handoff_excludes_narrow_sto_to(low: str) -> bool:
    """
    Входящий: сначала вопрос про техобслуживание/ТО, после перевода на ассистента — суть разговора
    про запчасти/замену деталей без записи на регламентное ТО (7793) → узкая рубрика НЕ_ТО.
    """
    pos, tail = _tail_after_service_line_handoff(low)
    if pos < 0 or len(tail) < 35:
        return False
    parts_focus = (
        "запчаст" in tail
        or "отдел запчаст" in tail
        or ("детал" in tail and "замен" in tail)
        or ("детал" in tail and "подбор" in tail)
        or ("колод" in tail and "запчаст" in tail)
        or ("колод" in tail and "детал" in tail)
    )
    if not parts_focus:
        return False
    tail_explicit, _ = explicit_narrow_sto_to_topic_hit(tail)
    if tail_explicit:
        return False
    if re.search(r"записал[аи]?\s+вас\s+на", tail) or "подтверждаю запись" in tail:
        return False
    if "ждем вас" in tail or "ждём вас" in tail:
        return False
    head = low[:pos]
    if not any(
        p in head
        for p in (
            "техобслуживание",
            "техническое обслуживание",
            "записаться на то",
            "запись на то",
            "на то записаться",
            "ассистент сервис",
            "диспетчер сервис",
        )
    ):
        return False
    return True


def _is_body_shop_crm_to_bait_not_narrow_to(low: str) -> bool:
    """
    В CRM указано «заявка на техобслуживание», разговор — кузовной/гарантийный осмотр (8491).
    Узкая СТО_ТО_* не применяется, несмотря на «техобслуживание» в шапке.
    """
    head = (low or "")[:1000]
    bait = bool(
        re.search(r"передал\w*[^.!?]{0,100}заяв\w*[^.!?]{0,60}техобслуживан", head)
        or re.search(r"заяв\w*\s+на\s+техобслуживан", head)
    )
    if not bait:
        return False
    if "кузовн" not in head:
        return False
    if not any(m in head for m in ("мастер-приёмщик", "мастер-приемщик", "мастер приемщик")):
        return False
    return any(
        p in low
        for p in (
            "ржавчин",
            "корроз",
            "молдинг",
            "багажник",
            "инженер по гарантии",
            "гарантийн",
            "перекраш",
            "на осмотр",
        )
    )


def _is_body_shop_booking_not_narrow_to(low: str) -> bool:
    """
    Запись/перезвон кузовного цеха: вмятины, покраска, ЛКП — не СТО_ТО_* (10870).
    При явном регламентном ТО в том же звонке не отключаем (10545).
    """
    head = (low or "")[:1200]
    if "кузовн" not in head:
        return False
    if _regulatory_to_price_or_composition_context(low):
        return False
    if re.search(
        r"\b(?:нулев|перв|втор|трет|четвер|четвёрт)\w*\s+то\b",
        low,
        re.I,
    ):
        return False
    return any(
        p in low
        for p in (
            "вмятин",
            "покраск",
            "перекраш",
            "лкп",
            "царапин",
            "бампер",
            "двер",
            "направлен",
            "осмотр автомобил",
            "без покраски",
            "характер вмятин",
            "поврежден",
        )
    )


def _is_outbound_service_campaign_hardware_recall_not_narrow_to(low: str) -> bool:
    """
    Исходящий звонок по сервисной кампании (жгут/шторка/доработка), без явной темы регламентного ТО.
    Шумный STT-фрагмент «на то» не считаем достаточным подтверждением темы ТО.
    """
    head = (low or "")[:5200]
    if not head.strip():
        return False
    has_campaign = bool(
        re.search(r"\b(?:сервисн\w*\s+кампан\w*|кампан\w+\s+месяц\w*|отзывн\w+\s+кампан\w*)\b", head, re.I)
    )
    if not has_campaign:
        return False
    has_hardware_subject = bool(
        re.search(r"\bжгут\w*\b", head, re.I)
        or re.search(r"\bсидени\w*\b", head, re.I)
        or re.search(r"\bшторк\w*\b", head, re.I)
        or re.search(r"\bдоработк\w*\b", head, re.I)
    )
    if not has_hardware_subject:
        return False
    has_strong_reg_to = bool(
        re.search(r"\b(?:техническ\w+\s+обслуживан\w*|техобслуживан\w*)\b", head, re.I)
        or re.search(r"\b(?:при|в\s+рамках)\s+то\b", head, re.I)
        or re.search(r"\bто\b[^.!?]{0,25}\b(?:будет|длит\w*|занима\w*|займ[её]т)\b", head, re.I)
        or re.search(r"\bто\s*[-]?\s*\d{1,2}\b", head, re.I)
    )
    if has_strong_reg_to:
        return False
    return True


def _is_false_ordinal_to_with_primary_defect_not_narrow_to(
    low: str,
    sto_dims: Dict[str, Any],
) -> bool:
    """
    25632: STT склеивает дату/время («седьмого, восьмого... то») в ложный маркер «N-е ТО»,
    но звонок изначально про дефект/диагностику (аккумулятор, не заводится, КПП, скорость).
    Если нет других сильных маркеров регламентного ТО — это НЕ_ТО.
    """
    ev = sto_dims.get("evidence") if isinstance(sto_dims.get("evidence"), dict) else {}
    work_hit = (ev.get("work_hit") or "").strip().lower()
    work_type = (sto_dims.get("work_type") or "").strip().lower()
    work_intent = (ev.get("work_intent") or "").strip().lower()
    # Интересуют только случаи, когда dimensions выставил work_type=to из-за ordinal-ТО.
    if work_type != "to":
        return False
    if work_intent not in ("to_price_quote", "to_booking"):
        return False
    # work_hit должен выглядеть как искажённый ordinal + то (STT артефакты типа «седьмогоое то»).
    # Чистые формы «седьмое то» / «седьмом то» из _TO_ORDINAL_PHRASE_MARKERS — не считаем ложными.
    if work_hit in _TO_ORDINAL_PHRASE_MARKERS:
        return False
    if not re.search(
        r"\b(?:седьм|восьм|девят|пят|шест|четверт|четвёрт|трет|втор|перв)"
        r"(?:ого|ему|ому|ом)(?:о+е?)?\s*то\b",
        work_hit,
        re.I,
    ):
        return False

    opening = (low or "")[:2200]
    has_defect = any(
        p in opening
        for p in (
            "аккумулятор",
            "не заводится",
            "плохо включается",
            "задняя скорость",
            "трещит",
            "передача",
            "кпп",
            "коробка",
            "неисправность",
            "дефект",
            "поломка",
            "стук",
            "стуч",
        )
    )
    if not has_defect:
        return False

    # Исключаем явные регламентные ТО-маркеры (ordinal-формы не считаем —
    # они могут быть ложными, см. work_hit выше).
    strong_to = any(
        p in low
        for p in (
            "записаться на то",
            "записываю на то",
            "запишем на то",
            "запишу на то",
            "плановое то",
            "регламентное то",
            "периодическое то",
            "пройти то",
            "то пройти",
            "стоимость то",
            "цена то",
            "техническое обслуживание",
            "техобслуживание",
        )
    )
    if strong_to:
        return False

    # Убедимся, что есть запись на визит/диагностику.
    return any(
        p in low
        for p in (
            "запис",
            "подъедите",
            "приезжайте",
            "подъехать",
            "приехать",
            "диагност",
            "посмотр",
            "провер",
        )
    )


def _is_brake_pad_replacement_opening_to_alias_not_narrow_to(
    low: str, sto_dims: Dict[str, Any]
) -> bool:
    """
    27443: «записаться на техобслуживание» в открытии, но фактическая запись на замену колодок.
    Это НЕ узкое ТО: work_type=прочие, а не СТО_ТО_*.
    """
    ev = sto_dims.get("evidence") if isinstance(sto_dims.get("evidence"), dict) else {}
    if (ev.get("work_intent") or "").strip().lower() != "brake_pad_replacement":
        return False
    opening = (low or "")[:1200]
    if not re.search(
        r"\b(?:запис(?:аться|ать)\s+на\s+|на\s+)?(?:техобслуживан\w*|"
        r"техническ\w+\s+обслуживан\w*|\bто\b)\b",
        opening,
        re.I,
    ):
        return False
    if re.search(
        r"\b(?:перв\w+\s+то|втор\w+\s+то|трет\w+\s+то|четверт\w+\s+то|пят\w+\s+то|"
        r"шест\w+\s+то|седьм\w+\s+то|восьм\w+\s+то|девят\w+\s+то|десят\w+\s+то|"
        r"объемн\w+\s+то|объ[её]мн\w+\s+то|регламент\w*|что\s+входит\s+в\s+то)\b",
        low,
        re.I,
    ):
        return False
    return True


def _focused_transcript_for_sto_to_narrow(transcript: str) -> str:
    """
    Сужает зону анализа для узкой рубрики:
    - отрезает явный «пост-разговор» после прощания;
    - ограничивает длину до «доверенной» части, чтобы хвост не добавлял ложные ТО-маркеры.
    """
    raw = re.sub(r"\s+", " ", (transcript or "")).strip()
    if not raw:
        return ""

    trimmed = raw
    if len(raw) >= 2500:
        farewell_re = re.compile(
            r"\b(?:до\s+свидан\w*|всего\s+добр\w*|хорош(?:его|ей)\s+дн\w*|до\s+встречи)\b",
            re.IGNORECASE,
        )
        service_ctx_re = re.compile(
            r"\b(?:запис\w*|техобслуж\w*|то\b|ремонт\w*|диагност\w*|"
            r"дата\w*|время\w*|стоимост\w*|цен\w*|подъед\w*|приез\w*|"
            r"гаранти\w*|автомобил\w*|машин\w*|заявк\w*)\b",
            re.IGNORECASE,
        )
        for m in farewell_re.finditer(raw):
            tail = raw[m.end() :]
            if len(tail) < 1800:
                continue
            head = raw[: m.start()]
            head_service = len(service_ctx_re.findall(head))
            tail_service = len(service_ctx_re.findall(tail))
            # После «до свидания» идёт длинный малорелевантный хвост.
            if tail_service <= max(2, head_service // 4) or len(tail) > 5000:
                trimmed = raw[: m.end()]
                break

    # Доверенная зона для узкого ТО-слоя: длинные хвосты обычно дают шумные ложные срабатывания.
    return trimmed[:5200]


def infer_sto_to_rubric_type(
    transcript: str,
    broad_call_type: str,
    sto_dims: Dict[str, Any],
    *,
    department: str,
) -> Tuple[Optional[str], str]:
    """
    Возвращает (STO_TO_IN | STO_TO_OUT | None, reason).

    Узкая рубрика СТО_ТО_* при явной теме регламентного ТО (см. explicit_narrow_sto_to_topic_hit и усиление
    маркерами записи/ТО из sto_booking_dimensions). Запись на ТО одновременно с диагностикой, гарантией,
    заменой колодок и т.п. — по-прежнему СТО_ТО_*, а не отсутствие узкой рубрики из-за «нерегламентных» слов.
    Поле sto_dims['work_type'] на результат не влияет — оно относится к слою аналитики/карточки.
    """
    dept = (department or "").strip().upper()
    ct = (broad_call_type or "").strip().upper()
    if ct not in ("STO_IN", "STO_OUT"):
        return None, "not_sto_call_type"
    if dept != "STO":
        return None, "department_not_sto"

    focused_transcript = _focused_transcript_for_sto_to_narrow(transcript or "")
    low = _norm(_normalize_text(focused_transcript))
    ev = sto_dims.get("evidence") if isinstance(sto_dims.get("evidence"), dict) else {}
    wi = (ev.get("work_intent") or "").strip().lower()
    if wi in (
        "to_retracted_to_diagnostics",
        "to_retracted_to_body_shop",
        "to_retracted_to_other_work",
    ):
        return None, "to_intent_retracted_non_reg_work"
    if wi == "admin_callback_no_reception":
        return None, "no_actual_reception_contact"
    jetour_booking_override = _is_jetour_limitation_with_confirmed_new_to_booking(
        low, ct, sto_dims
    )
    # Jetour + явный отказ дилера: «техническое обслуживание» в речи диспетчера относится к отказу.
    # Выводим из узкой рубрики STO_TO_* до проверки explicit_ok.
    if _is_jetour_service_refusal(low) and not jetour_booking_override:
        return None, "jetour_service_refusal"
    # 18187: чужой бренд (LiXiang и т.п.) + «не принимали» — не узкая СТО_ТО_*.
    if _is_brand_service_refusal(low) and not jetour_booking_override:
        return None, "brand_service_refusal_not_narrow_to"
    if _is_wrong_department_vikingi_lada_redirect_not_service(low):
        return None, "wrong_department_lada_redirect_not_narrow_to"
    if _is_wrong_number_redirect_other_dealer_not_narrow_to(low):
        return None, "wrong_number_redirect_other_dealer_not_narrow_to"
    if _is_prefilled_online_to_application_confirmation_not_narrow_to(low):
        return None, "prefilled_online_application_confirmation_not_narrow_to"
    if _is_deferred_to_scheduling_callback_without_booking(low):
        return None, "deferred_to_scheduling_callback_without_booking"
    if _is_employment_recruitment_inquiry_not_narrow_to(low):
        return None, "employment_recruitment_not_narrow_to"
    if _is_customer_service_legal_commercial_not_narrow_to(low):
        return None, "customer_service_legal_commercial_not_narrow_to"
    if _repair_work_price_quote_is_primary_not_regulatory_to(low) and not _regulatory_to_with_side_work_booking(low):
        return None, "repair_work_price_quote_not_narrow_to"
    if _is_body_shop_crm_to_bait_not_narrow_to(low):
        return None, "body_shop_crm_to_bait_not_narrow_to"
    if _is_body_shop_booking_not_narrow_to(low):
        return None, "body_shop_booking_not_narrow_to"
    if _is_past_to_admin_followup_not_narrow_to(low):
        if _is_past_to_service_record_correction_not_narrow_to(low):
            return None, "past_to_service_record_correction_not_narrow_to"
        return None, "past_to_work_order_document_request_not_narrow_to"
    if _is_own_parts_to_eligibility_consultation_not_narrow_to(low):
        return None, "own_parts_to_eligibility_consultation_not_narrow_to"
    if _is_to_official_dealer_eligibility_consultation_not_narrow_to(low):
        return None, "to_official_dealer_eligibility_consultation_not_narrow_to"
    if _is_component_presence_regulation_consultation_without_booking(low):
        return None, "component_regulation_consultation_not_narrow_to"
    # Исходящий лид с сайта: звонить на другой номер — согласование не состоялось (8675).
    if _is_outbound_site_lead_redirect_other_phone_not_narrow_to(low):
        return None, "outbound_site_lead_redirect_other_phone_not_narrow_to"
    if _is_adjacent_dept_existing_to_coordination_not_narrow_to(low):
        return None, "adjacent_dept_existing_to_coordination_not_narrow_to"
    # Исходящий: «вчера с вами записывали автомобиль…» — уточнение/перенос существующей записи (7873).
    if ct == "STO_OUT" and _is_outbound_prior_booking_recall_not_new_to_intake(low):
        return None, "outbound_prior_booking_recall_not_narrow_to"
    if ct == "STO_OUT" and _is_outbound_existing_to_reschedule_not_narrow_to(low):
        return None, "outbound_existing_to_reschedule_not_narrow_to"
    if ct in ("STO_OUT", "STO_IN") and _is_outbound_sto_repeat_followup_not_narrow_to(low):
        return None, "outbound_repeat_followup_not_narrow_to"
    if ct == "STO_OUT" and _is_outbound_service_campaign_hardware_recall_not_narrow_to(low):
        return None, "service_campaign_recall_not_narrow_to"
    if ct == "STO_IN" and _is_inbound_repeat_existing_non_to_application_not_narrow_to(low):
        return None, "inbound_repeat_existing_non_to_application_not_narrow_to"
    # 21175: исходящий перезвон по уже созданной записи, клиент просит отмену/отбой.
    # Это операционная работа с существующим слотом, не новая запись на ТО.
    if ct == "STO_OUT" and wi == "existing_visit_clarification":
        if not _is_outbound_existing_visit_with_confirmed_new_to_booking(low):
            if (ev.get("work_hit") or "").strip().lower() == "booking_cancellation_or_reschedule":
                return None, "outbound_existing_to_reschedule_not_narrow_to"
            return None, "outbound_existing_visit_clarification_not_narrow_to"
    # 15502: CRM-лид на ТО, клиент уже записан (в т.ч. на Солнечной) — узкий НЕ_ТО.
    if ct == "STO_OUT" and _is_outbound_crm_lead_client_already_booked_not_narrow_to(low):
        return None, "outbound_crm_lead_client_already_booked_not_narrow_to"
    # Исходящее напоминание/подтверждение уже сделанной записи: операционный звонок диспетчера
    # перед приездом клиента — не запись новой заявки на ТО, в узкой рубрике НЕ_ТО.
    # Если в речи явная тема регламентного ТО (7855: напоминание + «первое то» / ТО-лексика),
    # оставляем сужение до STO_TO_OUT ниже.
    if ct == "STO_OUT" and _is_outbound_appointment_reminder(low):
        explicit_reminder_to, _ = explicit_narrow_sto_to_topic_hit(focused_transcript)
        if not explicit_reminder_to:
            return None, "outbound_appointment_reminder"
    if ct in ("STO_IN", "STO_OUT") and _is_outbound_to_quote_parts_continuation_not_narrow_to(low):
        return None, "outbound_to_quote_continuation_not_narrow_to"
    if _is_pure_to_price_inquiry_without_booking_not_narrow_to(low):
        return None, "pure_to_price_inquiry_not_narrow_to"
    if _is_direct_to_cost_question_without_booking_not_narrow_to(low):
        return None, "direct_to_cost_question_without_booking_not_narrow_to"
    if _is_preparatory_to_price_quote_without_booking_not_narrow_to(low, wi):
        return None, "preparatory_to_price_quote_without_booking_not_narrow_to"
    if ct == "STO_IN" and _is_inbound_oil_change_booking_with_incidental_to_reference_not_narrow_to(low):
        return None, "inbound_oil_change_with_incidental_to_reference_not_narrow_to"
    if ct == "STO_IN" and _is_existing_to_warranty_repair_routing_not_new_booking(low):
        return None, "existing_to_warranty_repair_routing_not_narrow_to"
    # 25632: ложный ordinal+ТО из даты/времени на фоне дефектной диагностики — не СТО_ТО.
    if ct in ("STO_IN", "STO_OUT") and _is_false_ordinal_to_with_primary_defect_not_narrow_to(
        low, sto_dims
    ):
        return None, "false_ordinal_to_with_primary_defect_not_narrow_to"
    if ct == "STO_IN" and _is_brake_pad_replacement_opening_to_alias_not_narrow_to(
        low, sto_dims
    ):
        return None, "brake_pad_replacement_not_narrow_to"
    # 25572: «ТО» только как история визитов из CRM + запись на ремонт заявленных дефектов.
    if ct in ("STO_IN", "STO_OUT") and _is_historical_to_context_without_new_booking_not_narrow_to(
        low
    ):
        return None, "historical_to_context_without_new_booking_not_narrow_to"
    explicit_ok, _hit = explicit_narrow_sto_to_topic_hit(focused_transcript)
    if (
        ct == "STO_IN"
        and not explicit_ok
        and (
            (sto_dims.get("work_type") or "").strip().lower() == "diagnostics"
            or (ev.get("work_intent") or "").strip().lower() == "diagnostics"
        )
    ):
        return None, "diagnostics_without_explicit_to_not_narrow_to"
    if wi == "oil_change" and not explicit_ok:
        return None, "oil_change_without_regulatory_to_not_narrow_to"
    if (
        not explicit_ok
        and ct == "STO_IN"
        and _has_confirmed_to_slot_from_dims_signal(low, sto_dims)
    ):
        explicit_ok, _hit = True, "confirmed_to_slot_from_dims"
    # Перезвон диспетчера по лиду («вы оставляли заявку») — явная тема записи на ТО в продукте.
    if ct == "STO_OUT" and not explicit_ok and _is_sto_outbound_lead_request_to_callback_for_narrow(low):
        explicit_ok, _hit = True, "sto_outbound_lead_service_callback"
    if not explicit_ok and _regulatory_to_with_side_work_booking(low):
        explicit_ok, _hit = True, "regulatory_to_with_side_work_booking"
    # Запись на ТО / маркеры регламента (общий слой) + диагностика, гарантия, колодки и т.д. → узкая СТО_ТО_*,
    # не «НЕ_ТО» из-за сопутствующих тем (см. _non_reg_service_topic и booking_flow ниже).
    existing_slot_clarification = _has_existing_appointment_clarification(low)
    strict_explicit_to = explicit_ok
    if not explicit_ok and not existing_slot_clarification:
        if strong_scheduled_to_signal_present(low) or contains_any_to_marker_hit(low)[0]:
            # Запись на слот без темы регламентного ТО (омыватель, диагностика и т.д.) — узкий НЕ_ТО (8776).
            if not (_non_reg_service_topic(low) and not strict_explicit_to):
                explicit_ok, _hit = True, "scheduled_to_or_to_marker_with_side_topics"
    if wi == "recall_update_consultation_without_to_booking":
        return None, "recall_update_consultation_without_to_booking_not_narrow_to"
    if wi == "service_campaign_recall_without_regulatory_to":
        return None, "service_campaign_recall_without_regulatory_to_not_narrow_to"
    if wi == "non_to_issue_with_incidental_to_reference":
        return None, "incidental_to_reference_non_booking_issue_not_narrow_to"
    service_specs_consultation = wi == "service_specs_consultation"
    if wi in ("wheel_alignment", "wheel_balancing"):
        return None, "wheel_alignment_not_narrow_to"
    opening_reg_to_booking = ct == "STO_IN" and _is_inbound_opening_regulatory_to_booking(low)
    # Входящий: до диспетчера/ассистента сервиса не дошли (линия занята, только сняли номер и пообещали перезвон).
    # Это операционный коллбэк, а не фактическая приёмка записи на ТО.
    if ct == "STO_IN" and _no_actual_reception_contact(low):
        return None, "no_actual_reception_contact"
    # 26334: авто уже оставлено на ТО; звонок про статус текущего визита.
    if ct == "STO_IN" and _is_inbound_existing_to_in_service_status_not_narrow_to(low):
        return None, "existing_to_in_service_status_not_narrow_to"
    # Отмена/перенос существующего слота точнее общего
    # existing_visit_clarification и должны сохранять свою причину.
    if ct == "STO_IN" and _is_inbound_existing_to_cancellation_not_narrow_to(low):
        return None, "existing_to_cancellation_not_narrow_to"
    if ct == "STO_IN" and _is_inbound_existing_to_reschedule_not_narrow_to(low):
        return None, "existing_to_reschedule_not_narrow_to"
    # Входящий: клиент уже записывался — уточнение/подтверждение слота, не новая СТО_ТО_вх (8963).
    if ct == "STO_IN" and wi == "existing_visit_clarification":
        if (ev.get("work_hit") or "").strip().lower() == "booking_cancellation_or_reschedule":
            return None, "existing_to_reschedule_not_narrow_to"
        already_booked_slot_repeat = bool(
            re.search(
                r"\b(?:я\s+)?(?:вот\s+)?(?:только\s+что\s+|сейчас\s+)?"
                r"(?:с\s+вами\s+)?(?:общал\w*|разговаривал\w*)[^.!?]{0,120}"
                r"\bзаписал(?:ся|ась)\s+на\b",
                low,
                re.I,
            )
            or re.search(r"\bзаписал(?:ся|ась)\s+на\s+\d{1,2}\b", low, re.I)
        )
        if already_booked_slot_repeat:
            return None, "existing_visit_clarification_not_narrow_to"
        keep_narrow_to_in = explicit_ok and (
            opening_reg_to_booking
            or _inbound_client_new_regulatory_to_booking_intake(low)
            or _inbound_regulatory_to_quote_and_booking_intake(low)
        )
        if not keep_narrow_to_in:
            return None, "existing_visit_clarification_not_narrow_to"
    # Входящие: слот уже есть / уточнение записи — без узкой СТО_ТО_вх., если нет явных маркеров ТО.
    if ct == "STO_IN" and not explicit_narrow_sto_to_topic_hit(focused_transcript)[0]:
        if wi in ("existing_visit_clarification", "booking_verification") or existing_slot_clarification:
            return None, "booking_flow_not_narrow_to"
    # Продуктовый приоритет: явные маркеры ТО важнее сопутствующих тем
    # (ремонт/гарантия/замена/диагностика). Если ТО явно есть — остаёмся в СТО_ТО_*.
    # 10769/13781: прошлое ТО + замена по запчастям/дефекту — до общего non_reg.
    has_regulatory_to_topic_now = bool(
        explicit_ok or _positive_regulatory_to_discussion_present(low)
    )
    keep_narrow_on_confirmed_to_slot = bool(
        has_regulatory_to_topic_now
        and (
            _has_confirmed_to_slot_from_dims_signal(low, sto_dims)
            or _has_confirmed_service_booking_slot(low)
            or (ct == "STO_OUT" and _has_confirmed_outbound_to_booking_slot(low))
        )
    )
    if ct in ("STO_IN", "STO_OUT") and _is_deferred_parts_after_completed_to_visit_not_narrow_to(low):
        if not keep_narrow_on_confirmed_to_slot:
            return None, "deferred_parts_after_to_visit_not_narrow_to"
    if ct in ("STO_IN", "STO_OUT") and _is_arrived_interior_part_replacement_booking_intake(low):
        if not keep_narrow_on_confirmed_to_slot:
            return None, "arrived_part_replacement_not_narrow_to"
    # Перезвон-продолжение: запчасти/смета без «записаться на ТО» — до no_explicit_to_topic (15679).
    if ct in ("STO_IN", "STO_OUT") and _is_outbound_to_quote_parts_continuation_not_narrow_to(low):
        return None, "outbound_to_quote_continuation_not_narrow_to"
    # Неисправность/диагностика без записи/цены регламентного ТО — НЕ_ТО (вх. и исх.; 13517, 16673).
    # До early no_explicit/non_reg, иначе прошлое «первом то» уходит в non_reg с менее точной причиной.
    keep_outbound_to = bool(
        ct == "STO_OUT"
        and _has_confirmed_outbound_to_booking_slot(low)
        and (
            bool(explicit_ok)
            or bool(re.search(r"\b(?:техническ\w+\s+обслуживан\w*|техобслуж)\b", low, re.I))
            or bool(re.search(r"\bто\s*[-]?\s*\d{1,2}\b", low, re.I))
            or bool(re.search(r"\bсрок\w*[^.!?]{0,40}\bто\b", low, re.I))
        )
    )
    if keep_outbound_to and not explicit_ok:
        explicit_ok, _hit = True, "outbound_confirmed_to_booking_slot"
    if ct in ("STO_IN", "STO_OUT") and _is_primary_defect_diagnostic_service_intake(low):
        if not keep_outbound_to:
            return None, "primary_defect_diagnostic_not_narrow_to"
    if ct == "STO_IN" and _is_inbound_primary_diagnostic_with_incidental_to_slot_not_narrow_to(low):
        return None, "inbound_primary_diagnostic_with_incidental_to_slot_not_narrow_to"
    keep_narrow_on_confirmed_new_to_booking = bool(
        ct == "STO_IN"
        and explicit_ok
        and (
            _inbound_client_new_regulatory_to_booking_intake(low)
            or _inbound_regulatory_to_quote_and_booking_intake(low)
            or _regulatory_to_with_side_work_booking(low)
            or (
                (sto_dims.get("work_type") or "").strip().lower() == "to"
                and bool(sto_dims.get("is_booking"))
                and bool(
                    re.search(
                        r"\b(?:завтра|послезавтра|понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|воскресень\w*)\b"
                        r"[^.!?]{0,140}\b(?:на|в)\s+\d{1,2}(?:[:.]\d{2}|\s+\d{2})?\b",
                        low,
                        re.I,
                    )
                    or re.search(r"\b(?:на|в)\s+\d{1,2}(?:[:.]\d{2}|\s+\d{2})?\b", low, re.I)
                )
                and bool(
                    re.search(
                        r"\b(?:приедете|подъедете)\b[^.!?]{0,40}\b(?:да|угу|приеду|подъеду)\b",
                        low,
                        re.I,
                    )
                    or re.search(
                        r"\b(?:да|угу)\b[^.!?]{0,40}\b(?:приеду|подъеду)\b",
                        low,
                        re.I,
                    )
                )
            )
        )
    )
    keep_narrow_on_primary_new_to_with_tail_complaint = bool(
        ct == "STO_IN" and explicit_ok and _is_primary_new_to_then_tail_post_to_complaint(low)
    )
    keep_narrow_on_opening_need_to_do_to = bool(
        ct == "STO_IN" and explicit_ok and _is_opening_explicit_need_to_do_to(low)
    )
    keep_narrow_on_inbound_to_price_consultation = bool(
        ct == "STO_IN"
        and explicit_ok
        and _sto_reception_line_answered_after_handoff(low)
        and _regulatory_to_price_or_composition_context(low)
    )
    # 16969: претензия после уже выполненного ТО — до early no_explicit.
    if ct == "STO_IN" and _is_inbound_post_to_visit_complaint_not_narrow_to(low):
        if not (
            keep_narrow_on_confirmed_new_to_booking
            or keep_narrow_on_primary_new_to_with_tail_complaint
            or keep_narrow_on_opening_need_to_do_to
        ):
            return None, "post_to_visit_complaint_not_narrow_to"
    # 17539: «были недавно на ТО» без новой записи — follow-up, не СТО_ТО_* (вх. и исх.).
    if ct in ("STO_IN", "STO_OUT") and _is_past_to_followup_without_new_to_booking_not_narrow_to(
        low
    ):
        if not (keep_outbound_to or keep_narrow_on_opening_need_to_do_to):
            return None, "past_to_followup_without_new_to_booking"
    if ct in ("STO_IN", "STO_OUT") and _is_past_to_brake_wear_followup_not_narrow_to(low):
        return None, "past_to_brake_wear_followup_not_narrow_to"
    # 16976/14346: справка по прошлым ТО/работам — до early no_explicit.
    if ct == "STO_IN" and _is_inbound_to_history_crm_consultation_not_narrow_to(low):
        return None, "to_history_crm_consultation_not_narrow_to"
    if not explicit_ok:
        if _is_jetour_topic(low) and not _is_noisy_jetour_fragment_overridden_by_client_chery(
            sto_dims
        ):
            return None, "jetour_not_narrow_to"
        if _is_inbound_warranty_complaint_booking_not_narrow_to(low):
            return None, "inbound_warranty_complaint_booking_not_narrow_to"
        if _non_reg_service_topic(low):
            return None, "non_reg_service_topic"
        return None, "no_explicit_to_topic"

    # Входящий: сбой перевода на линию ассистента/диспетчера сервиса — нет полноценного СТО_ТО_вх (7767).
    if ct == "STO_IN" and _inbound_assistant_transfer_failed_excludes_narrow_sto_to(low):
        return None, "assistant_transfer_failed"

    # Входящий: ТО уже выполнено, после визита возникла претензия/дефект
    # (руль/развал/чек и т.п.) — узкий слой НЕ_ТО.
    if ct == "STO_IN" and _is_inbound_post_to_visit_complaint_not_narrow_to(low):
        if not (
            keep_narrow_on_confirmed_new_to_booking
            or keep_narrow_on_primary_new_to_with_tail_complaint
            or keep_narrow_on_opening_need_to_do_to
        ):
            return None, "post_to_visit_complaint_not_narrow_to"

    # Входящий: после перевода на ассистента — запчасти/детали, без узкой темы ТО в хвосте (7793).
    if ct == "STO_IN" and _inbound_parts_topic_after_service_handoff_excludes_narrow_sto_to(low):
        return None, "parts_after_assistant_not_narrow_to"

    # Входящий: CRM-история + консультация по дискам/маслу без записи на ТО (12958).
    if ct == "STO_IN" and _is_inbound_parts_crm_history_consultation_not_narrow_to(low):
        return None, "parts_crm_history_consultation_not_narrow_to"

    # Входящий: CRM-история регламентных ТО (сколько делали, по базе) без записи на слот (14346).
    if ct == "STO_IN" and _is_inbound_to_history_crm_consultation_not_narrow_to(low):
        return None, "to_history_crm_consultation_not_narrow_to"

    # Входящий: уже записан на ТО + вопрос по запчастям к визиту — НЕ_ТО (10549).
    if ct == "STO_IN" and _is_inbound_existing_to_visit_parts_question_not_narrow_to(low):
        return None, "existing_to_visit_parts_not_narrow_to"

    # Входящий: факт уже существующей записи на ТО + перенос/отмена слота — узкий НЕ_ТО
    # (8567, 8666, 8924, 9206, 15437), даже при «записывался на ТО» / уточнении «первое/второе ТО».
    if ct == "STO_IN" and _is_inbound_existing_to_cancellation_not_narrow_to(low):
        return None, "existing_to_cancellation_not_narrow_to"

    if ct == "STO_IN" and _is_inbound_existing_to_reschedule_not_narrow_to(low):
        return None, "existing_to_reschedule_not_narrow_to"

    # 10880: подтверждение уже существующей записи на ТО («всё в силе», «на какое время записаны»).
    if ct == "STO_IN" and _is_inbound_existing_to_slot_confirmation_not_narrow_to(low):
        return None, "existing_to_slot_confirmation_not_narrow_to"

    # 10441: «записывались на ТО на 2 ч» + «задерживаемся, пробка» — предупреждение об опоздании, не СТО_ТО_вх.
    if ct == "STO_IN" and _is_inbound_to_late_arrival_notice(low):
        return None, "existing_to_late_arrival_not_narrow_to"

    # Входящий: слот уже назначен — дописать жалобу/работу к визиту (9476).
    if ct == "STO_IN" and _is_inbound_existing_slot_addendum_not_narrow_to(low):
        return None, "existing_slot_addendum_not_narrow_to"
    if (
        ct == "STO_IN"
        and ev.get("booking_intent") == "cancellation"
        and not opening_reg_to_booking
    ):
        return None, "appointment_cancellation_not_narrow_to"

    # Входящий: клиент перезванивает — зачем звонили / не взял трубку; подтверждение записи (8568).
    if ct == "STO_IN" and _is_inbound_client_missed_dealer_call_inquiry_not_narrow_to(low):
        return None, "missed_dealer_call_inquiry_not_narrow_to"

    # Входящий: связь с конкретным сотрудником / «то на диспетчера, то на кого-то» (12658).
    if ct == "STO_IN" and _is_inbound_employee_contact_routing_not_narrow_to(low):
        return None, "employee_contact_routing_not_narrow_to"

    # 10769: ТО пройдено, свечи/запчасти поступили позже — замена, не СТО_ТО_*.
    if ct in ("STO_IN", "STO_OUT") and _is_deferred_parts_after_completed_to_visit_not_narrow_to(low):
        return None, "deferred_parts_after_to_visit_not_narrow_to"
    # Нет обсуждения прохождения регламентного ТО (лид на стекло/камеру и т.п.; 10568, 9746).
    if ct in ("STO_IN", "STO_OUT") and _no_regulatory_to_discussion_not_narrow_to(low):
        if not keep_outbound_to:
            return None, "no_regulatory_to_discussion_not_narrow_to"
    if not _has_minimum_sto_to_markers_for_narrow(
        low,
        sto_dims,
        has_to_interest=explicit_ok,
        allow_without_progress_marker=(
            service_specs_consultation
            or keep_narrow_on_primary_new_to_with_tail_complaint
            or keep_narrow_on_opening_need_to_do_to
            or keep_narrow_on_inbound_to_price_consultation
        ),
    ):
        if service_specs_consultation:
            return None, "service_specs_consultation_not_narrow_to"
        if keep_narrow_on_opening_need_to_do_to or keep_narrow_on_inbound_to_price_consultation:
            pass
        else:
            return None, "insufficient_sto_to_markers_not_narrow_to"
    if ct == "STO_OUT":
        # Перезвон перед визитом на ТО: в речи есть маркеры регламента, но узкий слой — НЕ_ТО (8093).
        if _is_outbound_recall_before_scheduled_to_visit(low):
            return None, "recall_before_scheduled_service_visit"
        # Перезвон накануне визита нужен для широкого STO_OUT, но в узком слое
        # это «подтверждение уже существующей записи», а не отдельная рубрика СТО_ТО_исх.
        if _has_pre_visit_booking_confirmation(low):
            return None, "pre_visit_confirmation_outbound"
        if _is_outbound_ac_refrigerant_service_not_narrow_to(low):
            return None, "ac_refrigerant_service_not_narrow_to"
        if _is_outbound_during_to_additional_work_not_narrow_to(low):
            return None, "outbound_during_to_additional_work_not_narrow_to"
    # Входящий/исходящий: «завтра записаны к нам… подъедете?» + смета N-го ТО в хвосте — НЕ_ТО (9597).
    if _has_pre_visit_booking_confirmation(low):
        return None, "pre_visit_confirmation"

    # Адаптация/регулировка сцепления (STT «цепление»); «шестом то» — жалоба на прошлый визит (8853).
    if _is_clutch_adaptation_not_narrow_to(low):
        return None, "clutch_adaptation_not_narrow_to"

    # Авто уже в сервисе у дилера («машинка там у вас на техобслуживании»): узкая СТО_ТО — про запись/цену регламентного ТО, не статус визита.
    if ct == "STO_IN":
        if _is_inbound_warranty_complaint_booking_not_narrow_to(low):
            return None, "inbound_warranty_complaint_booking_not_narrow_to"
        if _is_inbound_master_car_at_service_followup_not_narrow_to(low):
            return None, "inbound_master_car_at_service_followup_not_narrow_to"
        if _is_inbound_chassis_noise_diagnostic_not_narrow_to(low):
            return None, "inbound_chassis_noise_diagnostic_not_narrow_to"
        if _is_inbound_dashcam_or_accessory_programming_not_narrow_to(low):
            return None, "inbound_dashcam_programming_not_narrow_to"
        if ev.get("car_already_at_service"):
            return None, "car_at_service_status_not_narrow_to"
        return "STO_TO_IN", "ok"
    if not _positive_regulatory_to_discussion_present(low):
        if not keep_outbound_to:
            return None, "no_regulatory_to_discussion_not_narrow_to"
    return "STO_TO_OUT", "ok"


_AGREEMENT_MARKERS = (
    "записали вас на",
    "записали на",
    "записан на",
    "записана на",
    "записываю вас на",
    "официально записал",
    "подтверждаю запись",
    "ждем вас ",
    "ждём вас ",
    "до встречи ",
    "ждем ",
)


def infer_sto_appointment_agreed(transcript: str, sto_dims: Dict[str, Any]) -> bool:
    """Эвристика согласования даты/времени визита."""
    focused_transcript = _focused_transcript_for_sto_to_narrow(transcript or "")
    low = _norm(_normalize_text(focused_transcript))
    if not low:
        return False
    ev = sto_dims.get("evidence") if isinstance(sto_dims.get("evidence"), dict) else {}
    if ev.get("car_already_at_service"):
        return False
    if ev.get("work_intent") in (
        "warranty_consultation",
        "existing_visit_clarification",
        "employment_recruitment",
    ):
        return False
    if ev.get("booking_intent") == "warranty_consultation":
        return False
    if any(m in low for m in _AGREEMENT_MARKERS):
        return True
    # Дата/день недели + время (STT: «15 00», «на 3», «пятница» без «пятницу»).
    if re.search(
        r"\b(?:понедельник|вторник|сред\w*|четверг|пятниц\w*|суббот\w*|воскресень\w*|завтра|послезавтра)\b",
        low,
    ) and re.search(
        r"\b(?:в\s*)?\d{1,2}[.:\s]\d{2}\b|\bна\s+\d{1,2}\b|\bв\s+\d{1,2}\s+час",
        low,
    ):
        return True
    explicit_to, _ = explicit_narrow_sto_to_topic_hit(focused_transcript)
    if ev.get("work_intent") == "warranty_repair_booking" and sto_dims.get("is_booking"):
        return True
    return bool(sto_dims.get("is_booking")) and (
        sto_dims.get("work_type") == "to" or explicit_to
    )
