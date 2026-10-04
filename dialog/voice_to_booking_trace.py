"""
JSONL-трассировка ветки «запись на ТО» (голосовой бот ↔ 1С / сценарий).

Включение только через окружение (без обязательной правки compose):
  export VOICE_TO_TRACE_PATH=/var/log/vikingi/voice_to_booking.jsonl

Каждая строка файла — один JSON-объект: ts (местное время server7 / Europe/Samara), call_uuid, event, поля-снимки.
Персональные данные: телефон — только хвост (последние 4 цифры), ФИО — инициалы по словам.
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

_ENV_PATH = "VOICE_TO_TRACE_PATH"

_call_uuid_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "voice_to_booking_call_uuid",
    default=None,
)


def set_voice_to_booking_call_uuid(call_uuid: Optional[str]) -> contextvars.Token:
    return _call_uuid_var.set(call_uuid)


def reset_voice_to_booking_call_uuid(token: contextvars.Token) -> None:
    _call_uuid_var.reset(token)


def get_voice_to_booking_call_uuid() -> Optional[str]:
    return _call_uuid_var.get()


def _phone_tail(phone: Optional[str]) -> str:
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) < 4:
        return "****"
    return f"…{digits[-4:]}"


def _fio_hint(fio: Optional[str]) -> str:
    if not fio:
        return ""
    parts = str(fio).split()
    bits = []
    for p in parts[:4]:
        bits.append(p[0] if p else "?")
    tail = "…" if len(parts) > 4 else ""
    return ".".join(bits) + tail


def stt_preview_for_trace(text: Optional[str], *, max_len: int = 80) -> str:
    """Краткий превью STT для jsonl (без отдельного поля — только диагностика)."""
    s = (text or "").strip()
    if not s:
        return ""
    if len(s) <= max_len:
        return s
    return s[:max_len] + "…"


def summarize_service_data(
    sd_data: Optional[Mapping[str, Any]],
    *,
    caller_phone: Optional[str] = None,
) -> dict[str, Any]:
    """Краткий снимок service_data без полного ФИО/телефона."""
    if not sd_data:
        return {}
    d = dict(sd_data)
    out: dict[str, Any] = {
        "phone_tail": _phone_tail(d.get("phone")),
        "fio_hint": _fio_hint(d.get("fio")),
        "has_mileage": bool(d.get("mileage")),
        "has_work_list": bool(d.get("work_list")),
        "car_confirmed": bool(d.get("car_confirmed")),
        "mileage_work_confirmed": bool(d.get("mileage_work_confirmed")),
        "date_time_confirmed": bool(d.get("date_time_confirmed")),
        "client_found_in_db": d.get("client_found_in_db"),
        "desired_date": d.get("desired_date"),
        "desired_time": d.get("desired_time"),
        "operation_unknown": bool(d.get("operation_unknown")),
        "car_brand": d.get("car_brand"),
        "car_model": (str(d.get("car_model") or "")[:80] or None),
        "car_year": d.get("car_year"),
        "car_attempts": d.get("car_attempts"),
    }
    if caller_phone is not None:
        out["caller_phone_tail"] = _phone_tail(caller_phone)
    return {k: v for k, v in out.items() if v is not None and v != ""}


def voice_to_booking_trace(event: str, **fields: Any) -> None:
    path = (os.environ.get(_ENV_PATH) or "").strip()
    if not path:
        return
    uid = get_voice_to_booking_call_uuid() or ""
    from dialog.dealer_time import dealer_local_now_iso

    record: dict[str, Any] = {
        "ts": dealer_local_now_iso(),
        "call_uuid": uid,
        "event": event,
    }
    for k, v in fields.items():
        if v is not None:
            record[k] = v
    line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
    parent = os.path.dirname(path)
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            logger.debug("voice_to_booking_trace makedirs %s: %s", parent, exc)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as exc:
        logger.debug("voice_to_booking_trace write %s: %s", path, exc)
