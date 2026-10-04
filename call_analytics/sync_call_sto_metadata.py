"""
Единая точка обновления STO-признаков: марка/вид работ/запись + узкая рубрика STO_TO_* + appointment_agreed.
"""

from __future__ import annotations

from typing import Any, Dict

__all__ = ["sync_call_sto_metadata"]


def _persist_sto_dimensions_for_other_department(normalized: str) -> bool:
    """
    Для department/call_type = Прочие всё же сохраняем марку/вид работ в карточке,
    если это диалог диспетчера сервиса с записью/сутью сервиса (без широкого СТО вх. в реестре звонков).
    """
    low = (normalized or "").lower()
    if "диспетчер сервиса" not in low and "ассистент сервиса" not in low:
        return False
    if not any(
        n in low
        for n in (
            "юлия",
            "юлию",
            "андреева",
            "плаксина",
            "гринкина",
            "гранкина",
            "александра",
        )
    ):
        return False
    return any(
        x in low
        for x in (
            "запис",
            "вторник",
            "среду",
            "четверг",
            "пятниц",
            "понедельник",
            "на десять",
            "на 10",
            "на 16",
            "время",
            "чери",
            "черепга",
            "черри",
            "титго",
            "тигго",
            "тига",
            "tiggo",
            "tiga",
            "chery",
            "ниссан",
            "диагностик",
            "подвеск",
            "посмотреть",
            "промакс",
            "про макс",
        )
    )


def sync_call_sto_metadata(
    call_id: int,
    normalized: str,
    department: str,
    call_type: str,
) -> Dict[str, Any]:
    """
    Обновляет calls: sto_* размерности и sto_to_rubric_*.

    Returns:
        dict с ключами sto_to_rubric_type, sto_to_rubric_reason (для передачи в оценку).
    """
    from database.postgresql_manager import CallAnalyticsDB

    from call_analytics.sto_booking_dimensions import infer_sto_booking_dimensions
    from call_analytics.sto_to_rubric import infer_sto_appointment_agreed, infer_sto_to_rubric_type

    ct = (call_type or "").strip().upper()
    dept = (department or "").strip().upper()

    out: Dict[str, Any] = {
        "sto_to_rubric_type": None,
        "sto_to_rubric_reason": None,
    }

    if ct not in ("STO_IN", "STO_OUT"):
        if dept == "OTHER" and ct == "OTHER" and _persist_sto_dimensions_for_other_department(normalized or ""):
            dims = infer_sto_booking_dimensions(normalized or "")
            # Карточка «Прочие»: звонок о диагностике у диспетчера — вид работ не «ТО».
            try:
                from call_analytics.classify_by_transcript import (
                    _is_service_dispatcher_diagnostics_not_scheduled_to,
                )

                if _is_service_dispatcher_diagnostics_not_scheduled_to(normalized or ""):
                    ev = dict(dims.get("evidence") or {})
                    ev["work_intent"] = "diagnostics"
                    ev["work_hit"] = "diagnostics_registry_other"
                    dims = {**dims, "work_type": "other_work", "evidence": ev}
            except Exception:
                pass
            CallAnalyticsDB.update_call_sto_dimensions(
                call_id,
                service_brand=dims.get("service_brand"),
                work_type=dims.get("work_type"),
                is_booking=bool(dims.get("is_booking")),
                confidence=dims.get("confidence"),
                evidence=dims.get("evidence") if isinstance(dims.get("evidence"), dict) else {},
            )
            agreed = infer_sto_appointment_agreed(normalized or "", dims)
            CallAnalyticsDB.update_call_sto_rubric(
                call_id,
                rubric_type=None,
                reason="department_other_service_card",
                appointment_agreed=agreed,
            )
            out["sto_to_rubric_type"] = None
            out["sto_to_rubric_reason"] = "department_other"
            return out
        CallAnalyticsDB.update_call_sto_dimensions(
            call_id,
            service_brand=None,
            work_type=None,
            is_booking=None,
            confidence=None,
            evidence=None,
        )
        CallAnalyticsDB.update_call_sto_rubric(
            call_id,
            rubric_type=None,
            reason=None,
            appointment_agreed=None,
        )
        return out

    dims = infer_sto_booking_dimensions(normalized or "")
    rubric_type, reason = infer_sto_to_rubric_type(
        normalized or "",
        ct,
        dims,
        department=dept,
    )
    # Согласование вида работ с узкой рубрикой (СТО_ТО_* → ТО; НЕ_ТО → не ТО).
    from call_analytics.sto_booking_dimensions import align_sto_work_type_with_narrow_rubric

    aligned_wt, aligned_ev = align_sto_work_type_with_narrow_rubric(
        dims.get("work_type"),
        rubric_type,
        normalized or "",
        evidence=dims.get("evidence") if isinstance(dims.get("evidence"), dict) else {},
    )
    if aligned_wt != (dims.get("work_type") or ""):
        dims = {**dims, "work_type": aligned_wt, "evidence": aligned_ev}

    CallAnalyticsDB.update_call_sto_dimensions(
        call_id,
        service_brand=dims.get("service_brand"),
        work_type=dims.get("work_type"),
        is_booking=bool(dims.get("is_booking")),
        confidence=dims.get("confidence"),
        evidence=dims.get("evidence") if isinstance(dims.get("evidence"), dict) else {},
    )

    agreed = infer_sto_appointment_agreed(normalized or "", dims)

    CallAnalyticsDB.update_call_sto_rubric(
        call_id,
        rubric_type=rubric_type,
        reason=reason,
        appointment_agreed=agreed,
    )
    out["sto_to_rubric_type"] = rubric_type
    out["sto_to_rubric_reason"] = reason
    return out
