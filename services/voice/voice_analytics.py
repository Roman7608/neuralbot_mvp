"""
Журналирование сессий голосового бота в PostgreSQL (таблицы voice_bot_*).
Отключается: VOICE_BOT_DB_LOG=0 или ошибка импорта/подключения.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)


class VoiceSessionAnalytics:
    def __init__(self, call_uuid: str, greeting_type: str, asterisk_channel_id: Optional[str]):
        self.call_uuid = call_uuid
        self._greeting_type = greeting_type
        self._channel_id = asterisk_channel_id
        self._session_id: Optional[int] = None
        self._seq = 0
        self._broken = False
        self._enabled = os.environ.get("VOICE_BOT_DB_LOG", "1").lower() in ("1", "true", "yes")

    def get_session_id(self) -> Optional[int]:
        """ID строки voice_bot_sessions (для связи telegram_leads.voice_bot_session_id)."""
        return self._ensure_session_id()

    def _ensure_session_id(self) -> Optional[int]:
        if not self._enabled or self._broken:
            return None
        if self._session_id is not None:
            return self._session_id
        try:
            from database.postgresql_manager import VoiceBotAnalyticsDB

            sid = VoiceBotAnalyticsDB.ensure_session(
                self.call_uuid, self._greeting_type, self._channel_id
            )
            if sid is None:
                self._broken = True
                return None
            self._session_id = sid
            return sid
        except Exception as e:
            logger.warning("[%s] Voice bot DB log unavailable: %s", self.call_uuid, e)
            self._broken = True
            return None

    async def log_client_text(self, text: str) -> None:
        sid = self._ensure_session_id()
        if not sid:
            return
        self._seq += 1
        seq = self._seq
        t = text or ""

        def _write() -> None:
            from database.postgresql_manager import VoiceBotAnalyticsDB

            VoiceBotAnalyticsDB.add_transcript_turn(sid, seq, "client", t, None)

        await asyncio.to_thread(_write)

    async def log_bot_utterance(self, wav_file: Optional[str], tts_text: Optional[str]) -> None:
        sid = self._ensure_session_id()
        if not sid:
            return
        self._seq += 1
        seq = self._seq
        wf = wav_file
        tt = tts_text
        meta = {"wav_file": wf} if wf else None

        def _write() -> None:
            from database.postgresql_manager import VoiceBotAnalyticsDB
            from dialog.rag_system_states import WAV_FILE_TEXTS

            body = (tt or "").strip()
            if wf and not body:
                body = WAV_FILE_TEXTS.get(wf, "") or ""
            VoiceBotAnalyticsDB.add_transcript_turn(sid, seq, "bot", body, meta)

        await asyncio.to_thread(_write)

    async def on_transfer_decision(
        self,
        wav_file: Optional[str],
        need: Any,
        voice_admin_reason: Optional[str],
    ) -> None:
        sid = self._ensure_session_id()
        if not sid:
            return
        need_s = None
        if need is not None and hasattr(need, "value"):
            need_s = str(need.value)

        def _write() -> None:
            from database.postgresql_manager import VoiceBotAnalyticsDB
            from asterisk_transfer import get_exten_for_wav

            ar = voice_admin_reason if wav_file == "03_transfer_admin.wav" else None
            ext = get_exten_for_wav(wav_file)
            VoiceBotAnalyticsDB.add_transfer_event(
                sid,
                playback_wav=wav_file,
                admin_reason=ar,
                exten=ext,
                client_need=need_s,
            )

        await asyncio.to_thread(_write)

    async def patch_latest_transfer_ami(self, ami_result: str) -> None:
        """Обновить результат AMI у последнего записанного события перевода (после Redirect)."""
        if not self._enabled or self._broken or not ami_result:
            return
        sid = self._session_id or self._ensure_session_id()
        if not sid:
            return

        def _write() -> None:
            from database.postgresql_manager import VoiceBotAnalyticsDB

            VoiceBotAnalyticsDB.update_latest_transfer_ami_result(sid, ami_result)

        await asyncio.to_thread(_write)

    async def finalize(
        self,
        state_final: Optional[str],
        caller_phone: Optional[str] = None,
        client_spoken_name: Optional[str] = None,
    ) -> None:
        if not self._enabled or self._broken:
            return
        if self._session_id is None and self._ensure_session_id() is None:
            return

        def _write() -> None:
            from database.postgresql_manager import VoiceBotAnalyticsDB

            if caller_phone:
                VoiceBotAnalyticsDB.set_caller_phone(self.call_uuid, caller_phone)
            VoiceBotAnalyticsDB.finalize_session(
                self.call_uuid, state_final, client_spoken_name=client_spoken_name
            )

        await asyncio.to_thread(_write)
