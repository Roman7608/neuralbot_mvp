"""
Авторизация админки: сессия в cookie, проверка ролей.
Роли: 1=Менеджер ОП, 2=Ассистент СТО, 3=Хостес, 4=Руководитель ОП, 5=Руководитель СТО,
      6=Полный доступ (директор), 8=Суперадмин (admin1): как 6 плюс импорт SPRecord, журнал переводов бота.
      Роли 2 и 5: в «Звонках» — СТО и категория «Прочие» (OTHER), см. calls_allowed_departments_for_list / can_access_call_department_row.
      Номер 7 в БД не используется (см. миграция 017_retire_admin_role7.sql).
"""

import os
import hashlib
import hmac
import base64
import json
import logging
from typing import FrozenSet, List, Optional, Tuple
from dataclasses import dataclass

from fastapi import Request, HTTPException, status

logger = logging.getLogger(__name__)

# Секрет для подписи сессии (из env или фиксированный для dev)
ADMIN_SESSION_SECRET = os.environ.get("ADMIN_SESSION_SECRET", "vikingi-admin-secret-change-in-prod")
SESSION_COOKIE_NAME = "admin_session"
SESSION_MAX_AGE = 86400 * 30  # 30 дней


@dataclass
class AdminUser:
    id: int
    login: str
    role_id: int


def _sign(payload: str) -> str:
    sig = hmac.new(
        ADMIN_SESSION_SECRET.encode(),
        payload.encode(),
        hashlib.sha256,
    ).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}.{sig}".encode()).decode()


def _verify(token: str) -> Optional[dict]:
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        payload, sig = raw.rsplit(".", 1)
        expected = hmac.new(
            ADMIN_SESSION_SECRET.encode(),
            payload.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        return json.loads(payload)
    except Exception:
        return None


def create_session(user_id: int, login: str, role_id: int) -> str:
    payload = json.dumps({"user_id": user_id, "login": login, "role_id": role_id})
    return _sign(payload)


def parse_session(token: Optional[str]) -> Optional[AdminUser]:
    if not token:
        return None
    data = _verify(token)
    if not data:
        return None
    return AdminUser(
        id=data["user_id"],
        login=data["login"],
        role_id=int(data["role_id"]),
    )


async def get_current_user(request: Request) -> AdminUser:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    user = parse_session(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход")
    return user


# Матрица прав
def is_full_access_role(role_id: int) -> bool:
    """Директор (6) и суперадмин (8): все отделы и те же лимиты API, что у директора."""
    return role_id in (6, 8)


def can_access_leads(role_id: int) -> bool:
    return True


# Каналы лидов (telegram_leads.need_type): ОП vs сервис — для ролей 1/4 и 2/5
# new_cars_chery_tenet / other / accounting — голосовой бот (dialog.ClientNeed), иначе список «Лиды» пуст при «Все».
# parts — голос (ClientNeed.PARTS); spares — ТГ-бот (NeedDetector); оба в фильтре СТО.
LEADS_NEED_TYPES_OP: FrozenSet[str] = frozenset(
    {"chery_tenet", "secretary", "new_cars_chery_tenet", "other", "accounting"}
)
LEADS_NEED_TYPES_STO: FrozenSet[str] = frozenset(
    {"service", "used_cars", "body_repair", "spares", "parts", "service_cost"}
)


def leads_need_types_allowlist(role_id: int) -> Optional[FrozenSet[str]]:
    """
    None — на странице «Лиды» не режем выборку по need_type (все phone/max за период).
    Сужение — только выпадающий «Канал» и прочие фильтры в API.

    Раньше для ОП/СТО подставлялся IN(chery_tenet, secretary, …) / IN(service, …):
    голос пишет new_cars_chery_tenet, service, parts и т.д. — список был пуст при непустой
    сводке сессий голосового бота.
    """
    return None

def leads_date_range(role_id: int) -> str:
    """yesterday_today | any"""
    return "any"

def can_access_calls(role_id: int) -> bool:
    return role_id in (1, 2, 4, 5, 6, 8)  # 3=хостес нет

def calls_department(role_id: int) -> Optional[str]:
    """None=все, OP, STO"""
    if is_full_access_role(role_id):
        return None
    if role_id in (1, 4):
        return "OP"
    if role_id in (2, 5):
        return "STO"
    return None  # не должен попасть


def calls_allowed_departments_for_list(
    role_id: int, requested_department: Optional[str]
) -> Tuple[Optional[str], Optional[List[str]]]:
    """
    Для list_calls: (один отдел | None, список отделов IN | None).
    Категория OTHER доступна только role_id=8 (admin1).
    Роли 2/5: только СТО (по умолчанию и по запросу).
    Роли 1/4: всегда только OP (как раньше — query department не расширяет доступ).
    """
    restricted = calls_department(role_id)
    req = (requested_department or "").strip().upper()
    if req == "OTHER" and role_id != 8:
        req = ""
    if role_id == 6:
        if req in ("OP", "STO"):
            return req, None
        return None, ["OP", "STO"]
    if restricted is None:
        return (req if req else None), None
    if restricted == "OP" and role_id in (1, 4):
        return "OP", None
    if restricted == "STO" and role_id in (2, 5):
        return "STO", None
    # ОП и прочие ограниченные роли: не доверяем query department вне своего отдела
    return restricted, None


def can_access_call_department_row(role_id: int, call_department: Optional[str]) -> bool:
    """Доступ к записи звонка по полю calls.department (в т.ч. карточка и аудио)."""
    if not can_access_calls(role_id):
        return False
    restricted = calls_department(role_id)
    if role_id == 6:
        cd = (call_department or "").strip().upper()
        return cd in ("", "OP", "STO")
    if restricted is None:
        return True
    cd = (call_department or "").strip().upper()
    if not cd:
        return True
    if cd == "OTHER" and role_id != 8:
        return False
    if restricted == "STO" and role_id in (2, 5) and cd in ("STO", "OTHER"):
        return True
    return cd == restricted


def can_listen_audio(role_id: int) -> bool:
    return role_id in (1, 2, 4, 5, 6, 8)

def can_see_transcription(role_id: int) -> bool:
    return role_id in (4, 5, 6, 8)  # только руководители и полный

def can_upload(role_id: int) -> bool:
    return role_id in (4, 5, 6, 8)


def can_upload_from_sprecord(role_id: int) -> bool:
    """Импорт отдельных файлов с шары SPRecord — только суперадмин (роль 8, учётная запись admin1)."""
    return role_id == 8


def upload_department(role_id: int) -> Optional[str]:
    """None=все, OP, STO"""
    if is_full_access_role(role_id):
        return None
    if role_id == 4:
        return "OP"
    if role_id == 5:
        return "STO"
    return None

def can_access_analytics(role_id: int) -> bool:
    return role_id in (4, 5, 6, 8)

def analytics_department(role_id: int) -> Optional[str]:
    """None=все отделы, OP, STO"""
    if is_full_access_role(role_id):
        return None
    if role_id == 4:
        return "OP"
    if role_id == 5:
        return "STO"
    return None

def can_exclude_calls(role_id: int) -> bool:
    return role_id in (4, 5, 6, 8)


def can_edit_manager_name(role_id: int) -> bool:
    return role_id in (4, 5, 6, 8)


def can_access_voice_transfers(role_id: int) -> bool:
    """Журнал переводов голосового бота и API /api/voice-bot/* — только суперадмин (роль 8, admin1)."""
    return role_id == 8
