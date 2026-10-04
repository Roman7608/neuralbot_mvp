"""Внутренний тестовый номер: скрытие в админке и исключение из агрегатов «без перевода»."""
from __future__ import annotations

from typing import Any, Optional

INTERNAL_TEST_PHONE_LAST10 = "9023730808"
ADMIN1_ROLE_ID = 8


def admin1_sees_internal_test_phone(role_id: Optional[int]) -> bool:
    """admin1 (role_id=8) видит все звонки, включая внутренний тестовый номер."""
    return role_id == ADMIN1_ROLE_ID


def phone_last10_digits(phone: Optional[Any]) -> Optional[str]:
    if phone is None:
        return None
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if len(digits) < 10:
        return None
    return digits[-10:]


def is_internal_test_phone(phone: Optional[Any]) -> bool:
    return phone_last10_digits(phone) == INTERNAL_TEST_PHONE_LAST10


def sql_voice_session_exclude_internal_test(session_alias: str = "s") -> str:
    """Фрагмент AND … для voice_bot_sessions: исключить тестовый номер."""
    last10 = INTERNAL_TEST_PHONE_LAST10
    s = session_alias
    return f"""(
        right(regexp_replace(BTRIM(COALESCE({s}.caller_phone, '')), '[^0-9]', '', 'g'), 10)
            IS DISTINCT FROM '{last10}'
        AND NOT EXISTS (
            SELECT 1 FROM telegram_leads tl_it
            WHERE tl_it.voice_bot_session_id = {s}.id
              AND right(
                  regexp_replace(BTRIM(COALESCE(tl_it.client_phone, '')), '[^0-9]', '', 'g'),
                  10
              ) = '{last10}'
        )
    )"""


def sql_lead_exclude_internal_test(lead_alias: str = "tl") -> str:
    """Фрагмент AND … для telegram_leads по client_phone."""
    last10 = INTERNAL_TEST_PHONE_LAST10
    la = lead_alias
    return f"""(
        right(regexp_replace(BTRIM(COALESCE({la}.client_phone, '')), '[^0-9]', '', 'g'), 10)
            IS DISTINCT FROM '{last10}'
    )"""


def sql_lead_exclude_internal_test_for_role(
    lead_alias: str = "tl", *, role_id: Optional[int] = None
) -> str:
    if admin1_sees_internal_test_phone(role_id):
        return "TRUE"
    return sql_lead_exclude_internal_test(lead_alias)


def sql_voice_session_exclude_internal_test_for_role(
    session_alias: str = "s", *, role_id: Optional[int] = None
) -> str:
    if admin1_sees_internal_test_phone(role_id):
        return "TRUE"
    return sql_voice_session_exclude_internal_test(session_alias)


def filter_rows_hide_internal_test_phone(
    rows: list[dict],
    *,
    role_id: int,
    phone_key: str = "client_phone",
) -> list[dict]:
    """Скрыть строки с тестовым номером для всех ролей, кроме admin1 (role_id=8)."""
    if role_id == 8:
        return rows
    return [r for r in rows if not is_internal_test_phone(r.get(phone_key))]
