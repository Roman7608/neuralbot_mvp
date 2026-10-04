"""
HTTP-клиент к platform-api.max.ru (токен в заголовке Authorization).
Документация: https://dev.max.ru/docs-api
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional

import aiohttp

from max_bot.config import MAX_API_BASE, MAX_BOT_TOKEN

logger = logging.getLogger(__name__)


def _file_upload_content_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "application/pdf"
    if ext in (".docx", ".doc"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "application/octet-stream"


class MaxApiError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"MAX API HTTP {status}: {body[:500]}")


class MaxApiClient:
    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        self._token = (token or MAX_BOT_TOKEN or "").strip()
        self._base = (base_url or MAX_API_BASE).rstrip("/")
        if not self._token:
            raise ValueError("MAX_BOT_TOKEN пуст — задайте в .env")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": self._token, "Content-Type": "application/json"}

    async def get_me(self, session: aiohttp.ClientSession) -> dict[str, Any]:
        url = f"{self._base}/me"
        async with session.get(url, headers=self._headers()) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise MaxApiError(resp.status, text)
            import json

            return json.loads(text) if text else {}

    async def get_updates(
        self,
        session: aiohttp.ClientSession,
        *,
        marker: Optional[int] = None,
        limit: int = 100,
        timeout: int = 30,
        types: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        params: list[tuple[str, str]] = [
            ("limit", str(limit)),
            ("timeout", str(timeout)),
        ]
        if marker is not None:
            params.append(("marker", str(marker)))
        if types:
            for t in types:
                params.append(("types", t))

        url = f"{self._base}/updates"
        async with session.get(url, headers=self._headers(), params=params) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise MaxApiError(resp.status, text)
            import json

            return json.loads(text) if text else {}

    async def send_message(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
        text: str,
        attachments: Optional[list[dict[str, Any]]] = None,
        text_format: Optional[str] = None,
    ) -> dict[str, Any]:
        import json

        params: list[tuple[str, str]] = []
        if chat_id is not None:
            params.append(("chat_id", str(chat_id)))
        if user_id is not None:
            params.append(("user_id", str(user_id)))

        body: dict[str, Any] = {"text": text}
        if attachments:
            body["attachments"] = attachments
        if text_format:
            body["format"] = text_format

        url = f"{self._base}/messages"
        raw = json.dumps(body, ensure_ascii=False)
        headers = {**self._headers(), "Content-Type": "application/json; charset=utf-8"}
        async with session.post(url, headers=headers, params=params, data=raw.encode("utf-8")) as resp:
            text_out = await resp.text()
            if resp.status >= 400:
                raise MaxApiError(resp.status, text_out)
            return json.loads(text_out) if text_out else {}

    async def upload_file_get_token(self, session: aiohttp.ClientSession, file_path: Path) -> str:
        """
        POST /uploads?type=file → загрузка multipart на выданный url → token для вложения type=file.
        См. https://dev.max.ru/docs-api/methods/POST/uploads
        """
        path = file_path.resolve()
        if not path.is_file():
            raise FileNotFoundError(str(path))

        slot_url = f"{self._base}/uploads"
        async with session.post(
            slot_url,
            headers={"Authorization": self._token},
            params={"type": "file"},
        ) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise MaxApiError(resp.status, text)
            slot = json.loads(text) if text else {}
        upload_url = slot.get("url")
        if not upload_url or not isinstance(upload_url, str):
            raise MaxApiError(0, f"uploads: нет url в ответе: {slot!r}")

        data_bytes = path.read_bytes()
        form = aiohttp.FormData()
        form.add_field(
            "data",
            data_bytes,
            filename=path.name,
            content_type=_file_upload_content_type(path),
        )
        async with session.post(upload_url, data=form, headers={"Authorization": self._token}) as resp:
            text = await resp.text()
            if resp.status >= 400:
                raise MaxApiError(resp.status, text)
            body: Any = {}
            if text:
                try:
                    body = json.loads(text)
                except json.JSONDecodeError:
                    body = {}
            token = None
            if isinstance(body, dict):
                token = body.get("token") or body.get("retval")
                if not token and isinstance(body.get("payload"), dict):
                    token = body["payload"].get("token")
            if not token and isinstance(body, str) and body.strip():
                token = body.strip()
            if not token:
                raise MaxApiError(0, f"upload: нет token в ответе: {text[:800]!r}")
            return str(token)

    async def send_message_with_file(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
        text: str,
        file_path: Path,
        extra_attachments: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Сообщение с вложением file; повтор при attachment.not.ready после обработки на стороне MAX."""
        token = await self.upload_file_get_token(session, file_path)
        await asyncio.sleep(1.5)
        attachments: list[dict[str, Any]] = [{"type": "file", "payload": {"token": token}}]
        if extra_attachments:
            attachments.extend(extra_attachments)
        # Пустая подпись к файлу: в MAX поле text часто обязательно — невидимый символ.
        caption = (text or "").strip()
        if not caption:
            caption = "\u200b"
        last_exc: Optional[MaxApiError] = None
        for attempt in range(6):
            try:
                return await self.send_message(
                    session,
                    chat_id=chat_id,
                    user_id=user_id,
                    text=caption,
                    attachments=attachments,
                )
            except MaxApiError as e:
                last_exc = e
                low = (e.body or "").lower()
                if "not.ready" in low or "not.processed" in low or "attachment.file.not.processed" in low:
                    await asyncio.sleep(1.0 * (attempt + 1))
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    async def send_message_text(
        self,
        session: aiohttp.ClientSession,
        *,
        chat_id: Optional[int] = None,
        user_id: Optional[int] = None,
        text: str,
    ) -> dict[str, Any]:
        return await self.send_message(
            session, chat_id=chat_id, user_id=user_id, text=text, attachments=None
        )

    async def answer_callback(
        self,
        session: aiohttp.ClientSession,
        callback_id: str,
        *,
        notification: Optional[str] = None,
        message: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """POST /answers — подтвердить нажатие callback-кнопки (снять «ожидание» в клиенте)."""
        import json

        params = [("callback_id", callback_id)]
        body: dict[str, Any] = {}
        if notification is not None:
            body["notification"] = notification
        if message is not None:
            body["message"] = message

        url = f"{self._base}/answers"
        raw = json.dumps(body, ensure_ascii=False) if body else "{}"
        headers = {**self._headers(), "Content-Type": "application/json; charset=utf-8"}
        async with session.post(url, headers=headers, params=params, data=raw.encode("utf-8")) as resp:
            text_out = await resp.text()
            if resp.status >= 400:
                raise MaxApiError(resp.status, text_out)
            return json.loads(text_out) if text_out else {}
