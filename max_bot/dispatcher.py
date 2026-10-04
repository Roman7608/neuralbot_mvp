"""
Разбор входящих Update от MAX: bot_started, message_created, message_callback.
Сценарии лидов и записи на ТО — max_bot.lead_flow (логика как в telegram_bot).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, TYPE_CHECKING

from telegram_bot.utils.phone_normalizer import normalize_phone

from max_bot.config import policy_local_file_path
from max_bot.keyboards import (
    CLOSE_SESSION_PAYLOAD,
    GREETING,
    MENU_REPLIES,
    POLICY_PAYLOAD,
    attachments_for_menu_payload,
    back_to_menu_attachments,
    main_menu_attachments,
)
from max_bot.lead_flow import handle_max_lead_message
from max_bot.lead_session import clear_session, update_data

if TYPE_CHECKING:
    from max_bot.api_client import MaxApiClient

logger = logging.getLogger(__name__)


def _dig(d: dict, *keys: str, default=None):
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
    return cur if cur is not None else default


def extract_reply_target(update: dict) -> tuple[Optional[int], Optional[int]]:
    """(chat_id, user_id) для POST /messages."""
    chat_id = update.get("chat_id")
    user_id = update.get("user_id")
    if chat_id is None:
        chat_id = _dig(update, "message", "recipient", "chat_id")
    if chat_id is None:
        chat_id = _dig(update, "message", "chat_id")
    if user_id is None:
        u = update.get("user") or _dig(update, "message", "sender") or _dig(update, "message", "from")
        if isinstance(u, dict):
            user_id = u.get("user_id") or u.get("id")
    return chat_id, user_id


def extract_reply_target_callback(update: dict) -> tuple[Optional[int], Optional[int]]:
    """chat_id / user_id из апдейта message_callback."""
    cb = update.get("callback")
    if isinstance(cb, dict):
        msg = cb.get("message")
        if isinstance(msg, dict):
            chat_id = _dig(msg, "recipient", "chat_id") or msg.get("chat_id")
            snd = msg.get("sender") or msg.get("from")
            uid = None
            if isinstance(snd, dict):
                uid = snd.get("user_id") or snd.get("id")
            if chat_id is not None or uid is not None:
                return chat_id, uid
        user = cb.get("user")
        if isinstance(user, dict):
            uid = user.get("user_id") or user.get("id")
            return None, uid
    return extract_reply_target(update)


def _scalar_phone_candidate(v: Any) -> Optional[str]:
    if isinstance(v, int):
        v = str(v)
    if isinstance(v, str) and sum(c.isdigit() for c in v) >= 10:
        return v
    return None


def _phone_from_nested(obj: Any) -> Optional[str]:
    p = _scalar_phone_candidate(obj)
    if p:
        return p
    if not isinstance(obj, dict):
        return None
    for key in ("phone_number", "phone", "mobile", "tel", "vcf_phone"):
        p = _scalar_phone_candidate(obj.get(key))
        if p:
            return p
    for v in obj.values():
        found = _phone_from_nested(v)
        if found:
            return found
    return None


def extract_contact_phone(update: dict) -> Optional[str]:
    """
    Номер из сообщения после нажатия request_contact (структура тела может отличаться — перебираем вложения).
    """
    msg = update.get("message")
    if not isinstance(msg, dict):
        return None
    candidates: list[str] = []
    # Часть клиентов MAX кладёт контакт в message.contact (как в Bot API), не в body.
    contact = msg.get("contact")
    if isinstance(contact, dict):
        for key in ("phone_number", "phone", "mobile", "tel"):
            p = _scalar_phone_candidate(contact.get(key))
            if p:
                candidates.append(p)
        p = _phone_from_nested(contact)
        if p and p not in candidates:
            candidates.append(p)
    body = msg.get("body")
    if not isinstance(body, dict):
        body = {}
    for key in ("phone", "phone_number", "contact_phone", "tel"):
        p = _scalar_phone_candidate(body.get(key))
        if p:
            candidates.append(p)
    # Контакт как vCard в тексте тела (MAX)
    for key in ("text", "plain", "message"):
        v = body.get(key)
        if isinstance(v, str) and "vcard" in v.lower():
            n = normalize_phone(v)
            if n:
                candidates.insert(0, n)
    for att in body.get("attachments") or []:
        if not isinstance(att, dict):
            continue
        pl = att.get("payload")
        if isinstance(pl, dict):
            p = _phone_from_nested(pl)
            if p:
                candidates.append(p)
        p2 = _phone_from_nested(att)
        if p2 and p2 not in candidates:
            candidates.append(p2)
    for raw in candidates:
        n = normalize_phone(raw)
        if n:
            return n

        # MAX иногда присылает vCard/текст вместо "чистого" номера.
        # Берём только цифровой фрагмент длиной 10-11 (RU), не весь "сырой" текст.
        for chunk in re.findall(r"\d{10,11}", raw or ""):
            n2 = normalize_phone(chunk)
            if n2:
                return n2
    return None


def extract_incoming_text(update: dict) -> Optional[str]:
    ut = update.get("update_type") or update.get("type") or ""
    msg = update.get("message")
    if isinstance(msg, dict):
        body = msg.get("body")
        if isinstance(body, dict):
            t = body.get("text") or body.get("message") or body.get("plain")
            if t:
                return str(t).strip()
        if isinstance(msg.get("text"), str):
            return msg["text"].strip()
    if ut == "message_created" and isinstance(update.get("text"), str):
        return update["text"].strip()
    return None


def extract_callback(update: dict) -> tuple[Optional[str], Optional[str]]:
    """(callback_id, payload) для message_callback."""
    cb = update.get("callback")
    if not isinstance(cb, dict):
        return None, None
    cid = cb.get("callback_id") or cb.get("id")
    if cid is None:
        return None, None
    pl = cb.get("payload") or cb.get("callback_data")
    if pl is not None:
        pl = str(pl)
    return str(cid), pl


def _is_start_command(text: str) -> bool:
    """MAX не рисует кнопку /start — пользователь пишет в поле сообщения."""
    s = text.strip().lower()
    if not s:
        return False
    if s in ("/start", "/старт", "старт", "start", "начать", "меню"):
        return True
    if s.startswith("/start") or s.startswith("/старт"):
        return True
    return False


async def _send_greeting_with_menu(
    client: "MaxApiClient",
    session,
    *,
    chat_id: Optional[int],
    user_id: Optional[int],
) -> None:
    await client.send_message(
        session,
        chat_id=chat_id,
        user_id=None if chat_id is not None else user_id,
        text=GREETING,
        attachments=main_menu_attachments(user_id),
    )


async def _send_policy_callback_reply(
    client: "MaxApiClient",
    session,
    *,
    chat_id: Optional[int],
    user_id: Optional[int],
) -> None:
    if user_id is not None:
        update_data(user_id, policy_menu_dismissed=True)
    reply = MENU_REPLIES.get(POLICY_PAYLOAD, "")
    local = policy_local_file_path()
    if local is not None:
        try:
            await client.send_message_with_file(
                session,
                chat_id=chat_id,
                user_id=None if chat_id is not None else user_id,
                text="",
                file_path=local,
                extra_attachments=attachments_for_menu_payload("show_policy", user_id),
            )
            return
        except Exception:
            logger.exception("MAX: отправка локального файла политики не удалась, остаётся текст")
    await client.send_message(
        session,
        chat_id=chat_id,
        user_id=None if chat_id is not None else user_id,
        text=reply,
        attachments=attachments_for_menu_payload("show_policy", user_id),
    )


async def handle_update(client: "MaxApiClient", session, update: dict) -> None:
    ut = update.get("update_type") or update.get("type") or "unknown"
    logger.info("MAX update: type=%s keys=%s", ut, list(update.keys())[:24])

    if ut == "bot_started":
        chat_id, user_id = extract_reply_target(update)
        if user_id is not None:
            clear_session(user_id)
        await _send_greeting_with_menu(client, session, chat_id=chat_id, user_id=user_id)
        return

    if ut == "message_callback":
        cid, payload = extract_callback(update)
        if not cid or not payload:
            logger.warning("message_callback без callback_id/payload: %s", update)
            return
        try:
            await client.answer_callback(session, cid, notification=" ")
        except Exception:
            logger.exception("answer_callback failed callback_id=%s", cid)
        chat_id, user_id = extract_reply_target_callback(update)
        if user_id is None:
            logger.warning("message_callback: нет user_id, update=%s", update)
            return
        handled = await handle_max_lead_message(
            client, session, chat_id=chat_id, user_id=user_id, text_in=None, callback_payload=payload
        )
        if handled:
            return
        if payload == POLICY_PAYLOAD:
            await _send_policy_callback_reply(client, session, chat_id=chat_id, user_id=user_id)
            return
        if payload == "start":
            clear_session(user_id)
            await _send_greeting_with_menu(client, session, chat_id=chat_id, user_id=user_id)
            return
        if payload == CLOSE_SESSION_PAYLOAD:
            clear_session(user_id)
            await client.send_message(
                session,
                chat_id=chat_id,
                user_id=None if chat_id is not None else user_id,
                text=(
                    "Сессия закрыта. Данные сценария сброшены. "
                    "Чтобы начать снова — выберите раздел в меню или напишите /start."
                ),
                attachments=main_menu_attachments(user_id),
            )
            return
        return

    chat_id, user_id = extract_reply_target(update)
    text_in = (extract_incoming_text(update) or "").strip()
    contact_phone = extract_contact_phone(update)

    if user_id is None:
        logger.warning("Нет user_id в update, полный объект: %s", update)
        return

    if contact_phone:
        if await handle_max_lead_message(
            client,
            session,
            chat_id=chat_id,
            user_id=user_id,
            text_in=text_in,
            callback_payload=None,
            contact_phone=contact_phone,
        ):
            return

    if text_in:
        if _is_start_command(text_in):
            clear_session(user_id)
            await _send_greeting_with_menu(client, session, chat_id=chat_id, user_id=user_id)
            return
        if await handle_max_lead_message(
            client,
            session,
            chat_id=chat_id,
            user_id=user_id,
            text_in=text_in,
            callback_payload=None,
            contact_phone=None,
        ):
            return
        await client.send_message(
            session,
            chat_id=chat_id,
            user_id=None if chat_id is not None else user_id,
            text=(
                f"Принято: {text_in[:500]}\n\n"
                "Продолжайте сообщение при необходимости. "
                "Чтобы снова открыть список разделов — кнопка «Главное меню»."
            ),
            attachments=back_to_menu_attachments(),
        )
        return

    # Контакт/вложения есть, номер не извлечён — иначе пользователь видит карточку, бот молчит.
    if ut == "message_created" and isinstance(update.get("message"), dict):
        m = update["message"]
        b = m.get("body") if isinstance(m.get("body"), dict) else {}
        atts = b.get("attachments") or []
        types = [a.get("type") for a in atts if isinstance(a, dict)]
        contact_like = isinstance(m.get("contact"), dict) or "contact" in types
        if contact_like:
            logger.warning(
                "MAX message_created: контакт не распознан (нет номера). "
                "body_keys=%s att_types=%s msg_keys=%s text_in=%r",
                list(b.keys()) if b else [],
                types,
                list(m.keys())[:20],
                (text_in or "")[:80],
            )
    logger.debug("Update без исходящего ответа: %s", update)
