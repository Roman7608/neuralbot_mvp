"""
Сохранение оценок в call_quality_scores по типу звонка.

- OP_IN / OP_OUT — чек-лист ОП (analyze_call_quality).
- СТО по скрипту записи на ТО — только при sto_to_rubric_type in (STO_TO_IN, STO_TO_OUT);
  те же критерии 7–29 (analyze_sto_quality), что раньше для STO_IN/STO_OUT.
- Широкие STO_IN/STO_OUT без узкой рубрики — оценки не пишутся (аналитика по объёмам на закладке «Аналитика»).
- OTHER («Прочие») — оценки не пишутся.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

__all__ = ["save_quality_scores_for_call_type"]

_VALID_STO_TO = frozenset({"STO_TO_IN", "STO_TO_OUT"})
_OP_SCORE_KEYS: Tuple[str, ...] = (
    "p3_intro",
    "p4_ask_name_form",
    "p5_name_usage_3plus",
    "p6_car_interest",
    "p7_familiar_with_car",
    "p8_for_whom",
    "p9_purchase_timing",
    "p10_payment_form",
    "p11_current_car",
    "p12_invite_to_dc",
    "p13_test_drive",
    "p14_ask_contacts",
    "p15_send_contacts",
    "p16_thanks",
)


def _binarize_op_scores(scores: Dict[str, Any]) -> Dict[str, Any]:
    """
    Бинаризация оценок ОП: только полное выполнение критерия = 1, иначе 0.
    Итог total_score — сумма выполненных пунктов (0–14, целое).
    Старые значения 0/0.25/0.5/0.75 остаются в rollback-режиме (VIKINGI_OP_EVAL_BINARY=0).
    """
    out: Dict[str, Any] = dict(scores or {})
    total_sum = 0.0
    for key in _OP_SCORE_KEYS:
        try:
            v = float(out.get(key, 0.0) or 0.0)
        except (TypeError, ValueError):
            v = 0.0
        b = 1.0 if v >= 0.999 else 0.0
        out[key] = b
        total_sum += b
    out["total_score"] = int(total_sum)
    return out


def save_quality_scores_for_call_type(
    *,
    call_id: int,
    transcription_id: Optional[int],
    normalized: str,
    call_type: str,
    sto_to_rubric_type: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[float], str]:
    """
    Вычисляет оценки по правилам, соответствующим call_type и узкой рубрике СТО.

    sto_to_rubric_type: STO_TO_IN | STO_TO_OUT | None — для широких STO_* обязателен непустой
    для записи чек-листа сервиса.
    """
    ct = (call_type or "").strip().upper()
    text = (normalized or "").strip()
    strt = (sto_to_rubric_type or "").strip().upper() or None

    if ct in ("OTHER", "OP_MISC") or not text:
        return None, None, "none"

    from database.postgresql_manager import CallAnalyticsDB

    if ct in ("STO_IN", "STO_OUT"):
        if strt not in _VALID_STO_TO:
            return None, None, "none"
        from analyze_sto_quality import evaluate_sto_by_rules

        sto_scores = evaluate_sto_by_rules(normalized, call_type=ct, sto_to_rubric_type=strt)
        sto_total = sum(sto_scores.get(i, 0.0) for i in range(7, 30))
        overall = round(sto_total * 5.0 / 23.0, 2)
        detailed: Dict[str, Any] = {f"sto_{n}": v for n, v in sto_scores.items()}
        CallAnalyticsDB.save_quality_scores(
            call_id=call_id,
            transcription_id=transcription_id,
            greeting_score=float(sto_scores.get(7, 0.0)),
            professionalism_score=float(sto_scores.get(8, 0.0)),
            clarity_score=float(sto_scores.get(9, 0.0)),
            listening_score=float(sto_scores.get(10, 0.0)),
            problem_solving_score=float(sto_scores.get(25, 0.0)),
            closing_score=float(sto_scores.get(27, 0.0)),
            overall_score=overall,
            detailed_evaluation=detailed,
        )
        return detailed, overall, "sto"

    if ct in ("OP_IN", "OP_OUT"):
        from analyze_call_quality import evaluate_call_auto
        from config import VIKINGI_OP_EVAL_BINARY

        scores = evaluate_call_auto(normalized, call_type=ct)
        eval_kind = "op_soft"
        if VIKINGI_OP_EVAL_BINARY:
            scores = _binarize_op_scores(scores)
            eval_kind = "op_binary"
        overall = float(scores.get("total_score", 0.0))
        CallAnalyticsDB.save_quality_scores(
            call_id=call_id,
            transcription_id=transcription_id,
            greeting_score=float(scores.get("p3_intro", 0.0)),
            professionalism_score=float(scores.get("p4_ask_name_form", 0.0)),
            clarity_score=float(scores.get("p5_name_usage_3plus", 0.0)),
            listening_score=float(scores.get("p6_car_interest", 0.0)),
            problem_solving_score=float(scores.get("p12_invite_to_dc", 0.0)),
            closing_score=float(scores.get("p16_thanks", 0.0)),
            overall_score=overall,
            detailed_evaluation=scores,
        )
        return scores, overall, eval_kind

    return None, None, "none"
