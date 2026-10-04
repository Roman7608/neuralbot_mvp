"""
Оценка входящих звонков ОП (OP_IN) по рубрикатору docs/OP_CALL_EVALUATION_CRITERIA_RUBRIC.md.
Шкала по каждому из 14 критериев: 0, 0.25, 0.5, 0.75, 1.0 — детерминированные эвристики по тексту (без LLM).

Нумерация в документе §1–§14 соответствует ключам p3–p16 в EVALUATION_CRITERIA.
"""

from __future__ import annotations

import re
from typing import Dict, Tuple

# RE_HANDOVER — едино с analyze_call_quality (маркер «после перевода на ОП»)


def _handover_and_greeting(full_transcript: str) -> Tuple[str, str, str]:
    from analyze_call_quality import RE_HANDOVER_TO_MANAGER

    low = (full_transcript or "").lower().strip()
    handover = RE_HANDOVER_TO_MANAGER.search(low)
    rest = low[handover.end() :] if handover else low
    greeting_block = rest[:650]
    return low, rest, greeting_block


def _normalize_for_phrase_match(text: str) -> str:
    low = (text or "").lower().replace("ё", "е")
    low = re.sub(r"[^а-яa-z0-9]+", " ", low)
    return re.sub(r"\s+", " ", low).strip()


def _has_any_phrase(text: str, phrases: Tuple[str, ...] | list[str]) -> bool:
    low = (text or "").lower()
    norm = _normalize_for_phrase_match(text)
    for phrase in phrases:
        p = (phrase or "").strip().lower()
        if not p:
            continue
        if p in low:
            return True
        if _normalize_for_phrase_match(p) in norm:
            return True
    return False


def _score_p3_intro_rubric(greeting_block: str, m) -> float:
    """§1 Представление ПК: имя и фамилия менеджера из справочника."""
    g = (greeting_block or "").lower()
    if m._has_manager_name_in_greeting(g):
        return 1.0
    if re.search(r"\b(?:меня\s+зовут|это|менеджер)\s+[а-яе]{2,20}\s+[а-яе]{2,30}\b", _normalize_for_phrase_match(g)):
        return 1.0
    return 0.0


def _score_p4_ask_name_rubric(
    low: str,
    rest: str,
    customer_name: str | None,
    name_count: int,
    m,
    *,
    manager_name: str | None = None,
) -> float:
    """§2 Как обращаться по имени: спросил/уточнил, клиент сам представился или обращение по имени."""
    scope = rest or low
    if m.op_p4_ask_name_phrase_match(scope):
        return 1.0
    handover = m.RE_HANDOVER_TO_MANAGER.search(low)
    if handover and customer_name:
        pre = low[: handover.start()]
        if m.op_p4_ask_name_phrase_match(pre):
            return 1.0
    if m.client_self_intro_in_text(scope, manager_name):
        return 1.0
    if customer_name:
        c = customer_name.lower().replace("ё", "е")
        if re.search(rf"\b{re.escape(c)}\b[\s,!.?-]*(?:верно|да|здравствуйте)?", _normalize_for_phrase_match(scope)):
            return 1.0
    if name_count >= 1:
        return 1.0
    return 0.0


def _score_p5_name_count(name_count: int) -> float:
    """§3 Обращение по имени: достаточно 1+ обращения."""
    return 1.0 if name_count >= 1 else 0.0


def _score_p6_car_rubric(low: str, m) -> float:
    """§4 Какой автомобиль интересует."""
    return 1.0 if m.op_p6_car_interest_match(low) else 0.0


def _score_p7_familiar_rubric(low: str, m) -> float:
    """§5 Знакомство с авто / ожидания."""
    return 1.0 if m.op_p7_familiar_binary_match(low) else 0.0


def _score_p8_for_whom_rubric(low: str, m) -> float:
    """§6 Для кого авто."""
    return 1.0 if m.op_p8_for_whom_match(low) else 0.0


def _score_p9_timing_rubric(low: str, m) -> float:
    """§7 Сроки покупки."""
    return 1.0 if m.op_p9_purchase_timing_match(low) else 0.0


def _score_p10_payment_rubric(low: str, m) -> float:
    """§8 Формы оплаты."""
    return 1.0 if m.op_p10_payment_form_match(low) else 0.0


def _score_p11_current_car_rubric(low: str, m) -> float:
    """§9 Текущий автомобиль клиента."""
    return 1.0 if m.op_p11_current_car_full_match(low) else 0.0


def _score_p12_invite_rubric(low: str, m) -> float:
    """§10 Приглашение в ДЦ / на тест."""
    return 1.0 if m.op_p12_invite_strong_match(low) else 0.0


def _score_p13_test_drive_rubric(low: str, m) -> float:
    """§11 Согласование времени тест-драйва."""
    return 1.0 if m.op_p13_test_drive_match(low) else 0.0


def _score_p14_contacts_rubric(low: str, m) -> float:
    """§12 Запрос контактов клиента."""
    return 1.0 if m.op_p14_ask_contacts_match(low) else 0.0


def _score_p15_send_rubric(low: str, m) -> float:
    """§13 Отправка своих контактов."""
    return 1.0 if m.op_p15_send_contacts_match(low) else 0.0


def _score_p16_thanks_rubric(low: str, m) -> float:
    """§14 Благодарность."""
    return 1.0 if m.op_p16_thanks_match(low) else 0.0


def evaluate_op_in_rubric(full_transcript: str) -> Dict[str, float]:
    import analyze_call_quality as m

    low, rest, greeting_block = _handover_and_greeting(full_transcript or "")
    if not low:
        out = {k: 0.0 for k in m.CRITERIA_KEYS}
        out["total_score"] = 0.0
        return out

    manager_name = m.extract_manager_name_from_full_transcript(full_transcript)
    customer_name = m.extract_customer_name_from_full_transcript(full_transcript, manager_name)
    name_count = m.count_customer_name_mentions_after_handover(full_transcript, customer_name)

    scores: Dict[str, float] = {}
    scores["p3_intro"] = _score_p3_intro_rubric(greeting_block, m)
    scores["p4_ask_name_form"] = _score_p4_ask_name_rubric(
        low, rest, customer_name, name_count, m, manager_name=manager_name
    )
    scores["p5_name_usage_3plus"] = _score_p5_name_count(name_count)
    scores["p6_car_interest"] = _score_p6_car_rubric(low, m)
    scores["p7_familiar_with_car"] = _score_p7_familiar_rubric(low, m)
    scores["p8_for_whom"] = _score_p8_for_whom_rubric(low, m)
    scores["p9_purchase_timing"] = _score_p9_timing_rubric(low, m)
    scores["p10_payment_form"] = _score_p10_payment_rubric(low, m)
    scores["p11_current_car"] = _score_p11_current_car_rubric(low, m)
    scores["p12_invite_to_dc"] = _score_p12_invite_rubric(low, m)
    scores["p13_test_drive"] = _score_p13_test_drive_rubric(low, m)
    scores["p14_ask_contacts"] = _score_p14_contacts_rubric(low, m)
    scores["p15_send_contacts"] = _score_p15_send_rubric(low, m)
    scores["p16_thanks"] = _score_p16_thanks_rubric(low, m)

    total = 0.0
    for key, meta in m.EVALUATION_CRITERIA.items():
        total += scores.get(key, 0.0) * meta["weight"] * 5.0
    scores["total_score"] = max(0.0, min(5.0, total))
    return scores
