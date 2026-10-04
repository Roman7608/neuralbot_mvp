"""
CallSession — обработка одного телефонного звонка через AudioSocket.

Поток: AudioSocket audio → VAD → STT (HTTP) → dialog → TTS (HTTP) → AudioSocket audio
"""

import asyncio
import io
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

from services.voice.audiosocket import (
    AudioSocketFrame, FrameType, read_frame, build_audio_frame, build_hangup_frame, parse_uuid,
)
from services.voice.vad import VADCollector
from dialog.bot_logic import BotDialogMixin, replace_numbers_for_tts
from dialog.conversation_state import ConversationStateMachine, ConversationState, ClientNeed
from dialog.name_extractor import NameExtractor
from dialog.date_parser import DateParser
from dialog.car_brand_extractor import CarBrandExtractor
from dialog.database_manager import DatabaseManager
from dialog.leads_manager import LeadsManager
from dialog.rag_system_states import RAGSystemStates, WAV_FILE_TEXTS
from dialog.department_stt_normalize import is_meaningless_voice_stt
from asterisk_transfer import is_transfer_to_admin_wav, get_exten_for_wav, request_transfer_to_admin
from services.voice.voice_phrases import (
    GREETING_CHERY_TENET,
    GREETING_V2_CHERY_TENET,
    DEPARTMENT_CHOICE_CHERY_TENET,
    DEPARTMENT_CLARIFY_V2_CHERY_TENET,
    get_voice_scenario,
)
from services.voice.voice_profile import VOICE_SAMPLE_RATE, VOICE_SPEAKER, VOICE_SPEED
from services.voice.voice_analytics import VoiceSessionAnalytics
from services.voice.call_audio_recorder import build_recorder_from_env

logger = logging.getLogger(__name__)


def _normalize_inbound_cli(raw: str) -> Optional[str]:
    """Нормализация номера из CALLERID/AstDB (без зависимости от telegram_bot в образе voice)."""
    s = (raw or "").strip()
    if not s:
        return None
    digits = re.sub(r"\D", "", s)
    if len(digits) == 10 and digits[0] == "9":
        return "+7" + digits
    if len(digits) == 11 and digits[0] == "8" and digits[1] == "9":
        return "+7" + digits[1:]
    if len(digits) == 11 and digits[0] == "7" and digits[1] == "9":
        return "+" + digits
    if s.startswith("+") and len(digits) >= 10:
        return "+" + digits
    if len(digits) >= 10:
        return "+" + digits if not s.startswith("+") else s
    return s


ASTERISK_SAMPLE_RATE = 8000  # slin = 8 kHz signed 16-bit
STT_SAMPLE_RATE = 16000
# После ответа бота не слушаем первые N мс (эхо); меньше — не съедаем начало реплики абонента.
POST_PLAYBACK_GUARD_MS = float(os.getenv("POST_PLAYBACK_GUARD_MS", "300"))
# Окно на ответ после вопроса бота (с). После начала речи — доп. время на дособрать фразу
# (для фазы «имя после приветствия» не используется: одно окно ANSWER_WAIT_SEC, затем отделы).
MIN_CLIENT_REPLY_SEC = 8.0
ANSWER_WAIT_SEC = max(float(os.getenv("VOICE_ANSWER_WAIT_SEC", "8.0")), MIN_CLIENT_REPLY_SEC)
# Базовая пауза тишины после речи клиента перед переходом к следующему шагу.
GENERAL_VAD_SILENCE_MS = max(int(os.getenv("VOICE_VAD_SILENCE_MS", "500")), 500)
# Устойчивое окно ответа для этапа записи на сервис (дата/время и др.).
SERVICE_DATA_COLLECTION_LISTEN_SEC = float(
    os.getenv("VOICE_SERVICE_DATA_COLLECTION_WAIT_SEC", "8.0")
)
SERVICE_DATA_COLLECTION_LISTEN_SEC = max(SERVICE_DATA_COLLECTION_LISTEN_SEC, MIN_CLIENT_REPLY_SEC)
# Шаг «назовите дату и время»: не короче 5 с (по умолчанию 8 с).
DATE_TIME_LISTEN_SEC = max(float(os.getenv("VOICE_DATE_TIME_WAIT_SEC", "8.0")), MIN_CLIENT_REPLY_SEC)
# После «на эту дату нет, назовите другую» — не менее 5 с на ответ (эхо не считать ответом).
SLOT_AWAIT_NEW_DATE_LISTEN_SEC = float(
    os.getenv("VOICE_SLOT_AWAIT_DATE_SEC", "8.0")
)
SLOT_AWAIT_NEW_DATE_LISTEN_SEC = max(SLOT_AWAIT_NEW_DATE_LISTEN_SEC, MIN_CLIENT_REPLY_SEC)
# Пустой/шумовой ответ в выборе слота не считаем, пока не прошло минимум N секунд
# после последней реплики бота (чтобы не "съедать" клиента на быстрых ложных VAD/STT).
SLOT_SELECTION_EMPTY_GUARD_SEC = float(
    os.getenv("VOICE_SLOT_SELECTION_EMPTY_GUARD_SEC", "8.0")
)
SLOT_SELECTION_EMPTY_GUARD_SEC = max(SLOT_SELECTION_EMPTY_GUARD_SEC, MIN_CLIENT_REPLY_SEC)
# После фразы «есть ли еще вопросы» держим защиту от ложной тишины/шума,
# чтобы не завершать звонок до реальной реплики клиента.
SERVICE_BOOKED_EMPTY_GUARD_SEC = float(
    os.getenv("VOICE_SERVICE_BOOKED_EMPTY_GUARD_SEC", "8.0")
)
SERVICE_BOOKED_EMPTY_GUARD_SEC = max(SERVICE_BOOKED_EMPTY_GUARD_SEC, MIN_CLIENT_REPLY_SEC)
# v2 сбор данных: короткий всплеск (эхо) не считать ответом, пока не вышло окно ожидания.
V2_SERVICE_DATA_COLLECTION_DISCARD_MAX_SEC = float(
    os.getenv("VOICE_V2_SERVICE_DATA_DISCARD_MAX_SEC", "1.2")
)
V2_SERVICE_DATA_COLLECTION_SILENCE_MS = int(
    os.getenv("VOICE_V2_SERVICE_DATA_SILENCE_MS", "500")
)
V2_SERVICE_DATA_COLLECTION_SILENCE_MS = max(V2_SERVICE_DATA_COLLECTION_SILENCE_MS, 500)
# Окно ожидания реплики после WAV 23 (нерабочее v2); не меньше ANSWER_WAIT_SEC, чтобы .env с 2 с не ломало ТЗ.
AFTER_HOURS_NAME_LISTEN_SEC = max(
    float(os.getenv("VOICE_AFTER_HOURS_NAME_WAIT_SEC", "8.0")),
    MIN_CLIENT_REPLY_SEC,
)
# Подтверждение модели «верно?» — минимум 8 с на ответ.
CAR_CONFIRM_LISTEN_SEC = max(float(os.getenv("VOICE_CAR_CONFIRM_WAIT_SEC", "8.0")), MIN_CLIENT_REPLY_SEC)
CAR_CONFIRM_COLLECT_SEC = float(os.getenv("VOICE_CAR_CONFIRM_COLLECT_SEC", "3.0"))
# Короткий всплеск энергии после TTS — не считать концом фразы, пока не вышло окно ожидания (см. _record_speech).
AFTER_HOURS_NAME_DISCARD_MAX_SEC = float(os.getenv("VOICE_AFTER_HOURS_NAME_DISCARD_MAX_SEC", "0.35"))
# v2: после длинного приветствия — дольше антиэхо и отсекаем короткий шум (<1.2 с).
V2_GREETING_GUARD_MS = float(os.getenv("VOICE_V2_GREETING_GUARD_MS", "1200"))
V2_FIRST_DEPARTMENT_LISTEN_SEC = float(os.getenv("VOICE_V2_FIRST_DEPT_WAIT_SEC", "8.0"))
# Суммарное окно ответа после приветствия v2 и после WAV 22 (перечисление отделов).
V2_CLIENT_REPLY_WAIT_SEC = float(
    os.getenv("VOICE_V2_CLIENT_REPLY_WAIT_SEC", str(V2_FIRST_DEPARTMENT_LISTEN_SEC))
)
# «Австралия» часто <1.2 с — при 1.2 отбрасывалась как эхо, срабатывала со 2-й попытки.
V2_FIRST_DEPARTMENT_DISCARD_MAX_SEC = float(
    os.getenv("VOICE_V2_FIRST_DEPT_DISCARD_SEC", "0.5")
)
ANSWER_COLLECT_AFTER_SPEECH_SEC = float(os.getenv("VOICE_ANSWER_COLLECT_SEC", "12.0"))
# Пауза после WAV перевода до AMI Redirect — снижает гонку с ast_write в AudioSocket.
TRANSFER_AMI_DELAY_SEC = float(os.getenv("VOICE_TRANSFER_AMI_DELAY_SEC", "0.8"))
# После повтора меню 14 («Записать Вас на ТЭО или перевести?») всегда
# держим минимум 6 секунд на ответ клиента, даже если общий ANSWER_WAIT_SEC ниже.
MENU14_REPEAT_LISTEN_SEC = max(
    float(os.getenv("VOICE_MENU14_REPEAT_WAIT_SEC", "8.0")),
    MIN_CLIENT_REPLY_SEC,
)
# menu14: не обрывать фразу на коротком «Пе...», увеличиваем паузу тишины для VAD.
MENU14_SILENCE_MS = max(int(os.getenv("VOICE_MENU14_SILENCE_MS", "500")), 500)
# menu14: если клиент успел сказать только начало слова, дослушиваем ещё немного.
MENU14_SHORT_FRAGMENT_MAX_SEC = float(os.getenv("VOICE_MENU14_SHORT_FRAGMENT_MAX_SEC", "1.0"))
MENU14_SHORT_FRAGMENT_EXTRA_SEC = float(os.getenv("VOICE_MENU14_SHORT_FRAGMENT_EXTRA_SEC", "2.0"))
# Шаг сбора телефона (ТО v2): всегда слушаем не менее 8 секунд.
TO_V2_PHONE_LISTEN_SEC = float(os.getenv("VOICE_TO_V2_PHONE_WAIT_SEC", "8.0"))
# Если клиент уже начал диктовать номер — даём договорить (не обрывать по исходным 8 с).
TO_V2_PHONE_COLLECT_AFTER_SPEECH_SEC = float(
    os.getenv("VOICE_TO_V2_PHONE_COLLECT_SEC", str(ANSWER_COLLECT_AFTER_SPEECH_SEC))
)
# Пауза между цифрами номера длиннее обычной — не резать фразу на короткой тишине.
TO_V2_PHONE_SILENCE_MS = int(os.getenv("VOICE_TO_V2_PHONE_SILENCE_MS", "1500"))


class CallSession(BotDialogMixin):
    """
    One phone call = one CallSession.

    Inherits dialog logic from BotDialogMixin;
    provides say() / play_wav_or_tts() via AudioSocket + HTTP STT/TTS.
    """

    def __init__(
        self,
        call_uuid: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        stt_url: str,
        tts_url: str,
        wav_cache: dict[str, tuple[np.ndarray, int]],
        audio_responses_dir: Path,
        clients_base_path: Path,
        norma_path: Path,
        slot_path: Path,
        leads_path: Path,
        greeting_type: str = "chery_tenet",  # единый сценарий; значение для аналитики/логов
        asterisk_astdb_key: Optional[str] = None,
    ):
        self.call_uuid = call_uuid
        self.greeting_type = greeting_type
        self._reader = reader
        self._writer = writer
        self._stt_url = stt_url.rstrip("/")
        self._tts_url = tts_url.rstrip("/")
        self._wav_cache = wav_cache
        self._audio_responses_dir = audio_responses_dir
        self._closed = False
        self._http_session = None

        self.state_machine = ConversationStateMachine(session_uuid=call_uuid)
        self.name_extractor = NameExtractor()
        self.date_parser = DateParser()
        self.car_brand_extractor = CarBrandExtractor()
        self.db_manager = DatabaseManager(clients_base_path, norma_path, slot_path)
        self.leads_manager = LeadsManager(leads_path)
        self.rag_system = RAGSystemStates(audio_responses_dir, 0.85)
        self.service_day_part: Optional[str] = None
        self.silence_rounds = 0
        self.voice_scenario: str = get_voice_scenario()
        _gw = "01b_greeting_v2_chery_tenet.wav" if self.voice_scenario == "v2" else "01_greeting_chery_tenet.wav"
        self.last_bot_phrase: tuple[Optional[str], Optional[str]] = (_gw, None)
        # Ключ AstDB (32 hex) = FILTER(0-9a-fA-F,${call_uuid}) в extensions.conf; для AMI DBGet.
        self.asterisk_channel_id: Optional[str] = (asterisk_astdb_key or "").strip().lower() or None
        if not self.asterisk_channel_id:
            self.asterisk_channel_id = "".join(c for c in call_uuid if c in "0123456789abcdefABCDEF").lower() or call_uuid
        self._record_attempt = 0
        self._departments_not_played_yet = True  # Перечисление отделов — после первой записи (5 сек на имя)
        self._last_playback_end_ts: Optional[float] = None
        self._last_playback_label = "startup"
        self._vox = VoiceSessionAnalytics(call_uuid, greeting_type, self.asterisk_channel_id)
        self._call_recorder = build_recorder_from_env(call_uuid)
        # ANI из AstDB (extensions.conf vikingi_ani) — для лидов и нерабочего времени; см. _resolve_caller_phone_from_astdb
        self.caller_phone: Optional[str] = None
        # DID из AstDB (extensions.conf vikingi_did) — входящий номер, например 697070.
        self.incoming_did: Optional[str] = None
        # Исход AMI-перевода → лид в «Лиды» при завершении звонка (если не сохранили в нерабочее время)
        self._voice_transfer_last_outcome: Optional[str] = None
        self._voice_lead_saved: bool = False
        # asyncio loop time: конец окна ответа на «Как Вас зовут?» (= конец приветствия + ANSWER_WAIT_SEC).
        self._name_listen_deadline: Optional[float] = None
        # Перебивание длинного WAV (gate5): аудио клиента до следующего _record_speech.
        self._pending_barge_in_audio: Optional[np.ndarray] = None
        # Одна строка в журнале на «пачку» пустых STT/VAD до ответа с текстом (несколько сегментов VAD подряд).
        self._name_phase_empty_logged_to_db: bool = False
        # v2: один слот ответа (до 8 с) → одна строка client в voice_bot_transcript_turns.
        self._v2_slot_journal_parts: list[str] = []
        self._v2_slot_journal_pending: bool = False
        self._v2_slot_journal_flushed: bool = False

    def _is_v2_client_reply_wait_phase(self) -> bool:
        """v2 INITIAL: ожидание ответа после приветствия (dps=0) или после WAV 22 (dps>=1)."""
        if getattr(self, "voice_scenario", "legacy") != "v2":
            return False
        if self.state_machine.state != ConversationState.INITIAL:
            return False
        if not self.state_machine.post_greeting_name_done:
            return False
        return getattr(self.state_machine, "department_prompts_shown", 0) <= 1

    def _begin_v2_reply_slot(self) -> None:
        """Новое окно ответа клиента после приветствия v2 или WAV 22."""
        self._v2_slot_journal_parts = []
        self._v2_slot_journal_pending = True
        self._v2_slot_journal_flushed = False
        self._name_phase_empty_logged_to_db = False

    def _accumulate_v2_slot_fragment(self, text: str) -> str:
        """
        Осмысленные фрагменты STT в пределах одного 8-с слота склеиваются;
        возвращает накопленный текст для bot_logic.
        """
        if not (self._v2_slot_journal_pending and self._is_v2_client_reply_wait_phase()):
            return (text or "").strip()
        frag = (text or "").strip()
        if frag and not is_meaningless_voice_stt(frag):
            self._v2_slot_journal_parts.append(frag)
        return " ".join(self._v2_slot_journal_parts).strip()

    async def _flush_v2_slot_journal(self) -> None:
        """Одна строка client в БД на весь слот (пустота/шум не плодят строки)."""
        if not self._v2_slot_journal_pending or self._v2_slot_journal_flushed:
            return
        self._v2_slot_journal_flushed = True
        combined = " ".join(self._v2_slot_journal_parts).strip()
        if combined:
            self._name_phase_empty_logged_to_db = False
            await self._vox.log_client_text(combined)
        elif not self._name_phase_empty_logged_to_db:
            self._name_phase_empty_logged_to_db = True
            await self._vox.log_client_text("")

    async def _maybe_flush_v2_slot_journal_before_bot(self) -> None:
        await self._flush_v2_slot_journal()

    async def _log_client_text_for_journal(self, text: str) -> None:
        """
        Пишет реплику клиента в voice_bot_transcript_turns.
        v2-слот: буфер в _accumulate_v2_slot_fragment, flush — перед ответом бота.
        """
        if self._v2_slot_journal_pending and self._is_v2_client_reply_wait_phase():
            return
        t = (text or "").strip()
        if t:
            self._name_phase_empty_logged_to_db = False
            await self._vox.log_client_text(text)
            return
        if not self.state_machine.post_greeting_name_done or self._is_v2_client_reply_wait_phase():
            if self._name_phase_empty_logged_to_db:
                return
            self._name_phase_empty_logged_to_db = True
        await self._vox.log_client_text("")

    async def on_voice_transfer_decision(self, wav_file, need=None, voice_admin_reason=None):
        await self._vox.on_transfer_decision(wav_file, need, voice_admin_reason)

    async def execute_transfer_ami_for_wav(self, wav_file: str) -> None:
        """AMI после TTS без воспроизведения transfer-WAV (skip_announcement в bot_logic)."""
        await self._maybe_signal_transfer(wav_file)

    async def _get_http(self):
        if self._http_session is None:
            import aiohttp
            self._http_session = aiohttp.ClientSession()
        return self._http_session

    async def close(self):
        self._closed = True
        try:
            await self._flush_v2_slot_journal()
        except Exception:
            logger.debug("[%s] v2 slot journal flush on close failed", self.call_uuid, exc_info=True)
        if self._http_session:
            await self._http_session.close()
            self._http_session = None
        try:
            if not getattr(self, "_audiosocket_hangup_sent", False):
                # После успешного AMI Redirect канал уходит в Dial/АТС; кадр HANGUP с AudioSocket
                # может оборвать абонента во время гудков ожидания — только закрываем TCP.
                # См. docs/infolada_transfer_silence_brief.txt («перевод — ранний обрыв»).
                if getattr(self, "_voice_transfer_last_outcome", None) == "transfer_started":
                    self._audiosocket_hangup_sent = True
                    logger.info(
                        "[%s] AudioSocket: без HANGUP после успешного перевода "
                        "(сессия бота закрыта; дальше — АТС/клиент)",
                        self.call_uuid,
                    )
                else:
                    self._writer.write(build_hangup_frame())
                    await self._writer.drain()
                    self._audiosocket_hangup_sent = True
                    logger.info("[%s] AudioSocket HANGUP sent (завершение сессии для Asterisk)", self.call_uuid)
        except Exception:
            logger.debug("[%s] AudioSocket HANGUP не отправлен (канал уже закрыт?)", self.call_uuid)
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _resolve_caller_phone_from_astdb(self) -> None:
        """Подставляет caller_phone из AstDB (CALLERID до AudioSocket), если AMI настроен."""
        key = self.asterisk_channel_id
        if not key:
            return

        def _sync_fetch() -> Optional[str]:
            try:
                from asterisk_transfer import fetch_caller_ani_by_session_key

                return fetch_caller_ani_by_session_key(key)
            except Exception:
                logger.exception("[%s] fetch_caller_ani_by_session_key failed", self.call_uuid)
                return None

        raw = await asyncio.to_thread(_sync_fetch)
        if not raw or not str(raw).strip():
            logger.info("[%s] caller ANI not in AstDB or AMI unavailable", self.call_uuid)
            return
        raw_s = str(raw).strip()
        normalized = _normalize_inbound_cli(raw_s)
        self.caller_phone = normalized or raw_s
        logger.info("[%s] caller_phone from AstDB: %r -> %r", self.call_uuid, raw_s, self.caller_phone)

    async def _resolve_incoming_did_from_astdb(self) -> None:
        """Подставляет входящий DID из AstDB (если AMI настроен)."""
        key = self.asterisk_channel_id
        if not key:
            return

        def _sync_fetch() -> Optional[str]:
            try:
                from asterisk_transfer import fetch_incoming_did_by_session_key

                return fetch_incoming_did_by_session_key(key)
            except Exception:
                logger.exception("[%s] fetch_incoming_did_by_session_key failed", self.call_uuid)
                return None

        raw = await asyncio.to_thread(_sync_fetch)
        if not raw or not str(raw).strip():
            logger.info("[%s] incoming DID not in AstDB or AMI unavailable", self.call_uuid)
            return
        self.incoming_did = str(raw).strip()[:40]
        logger.info("[%s] incoming_did from AstDB: %r", self.call_uuid, self.incoming_did)

    def _voice_lead_need_str(self) -> str:
        need = self.state_machine.identified_need
        return need.value if need else "secretary"

    async def _persist_voice_lead_if_needed(self) -> None:
        """
        Один лид на звонок в конце сессии:
        - после попытки перевода (AMI): transfer_started | no_operator;
        - иначе: нерабочее время (обрыв) | рабочее (обрыв / завершение без перевода).
        """
        if getattr(self, "_voice_lead_saved", False):
            return
        vo = getattr(self, "_voice_transfer_last_outcome", None)
        need_str = self._voice_lead_need_str()
        phone = self._lead_phones_for_db()
        name = self.state_machine.client_name or ""
        wh_wall = not self._is_non_working_hours()

        if vo:
            if vo == "transfer_started":
                outcome = "перевод к сотруднику инициирован (AMI успех; факт разговора не подтверждён)"
            else:
                outcome = "перевод к сотруднику не выполнен"
            vco = vo
            working_hours = wh_wall
        else:
            if not wh_wall:
                vco = "after_hours_partial"
                outcome = (
                    "нерабочее время: обрыв до полного сценария "
                    "(сохранены номер/время по возможности)"
                )
                working_hours = False
            else:
                sm = self.state_machine
                sd = sm.service_data or {}
                rich = (
                    sm.need_confirmed
                    or bool((sd.get("phone") or "").strip())
                    or bool((sd.get("car_brand") or "").strip())
                    or bool((sm.client_name or "").strip())
                )
                if rich:
                    vco = "session_complete"
                    outcome = "сессия завершена без перевода на оператора (данные по наличию)"
                else:
                    vco = "hangup_before_transfer"
                    outcome = "клиент завершил звонок до попытки перевода на оператора"
                working_hours = True

        def _save() -> bool:
            return self.leads_manager.save_lead(
                client_name=name or None,
                phone=phone or None,
                need=need_str,
                outcome=outcome,
                working_hours=working_hours,
                source="phone",
                voice_contact_outcome=vco,
                service_data=self.state_machine.service_data,
                voice_bot_session_id=self._vox.get_session_id(),
            )

        try:
            saved = await asyncio.to_thread(_save)
            if saved:
                self._voice_lead_saved = True
                logger.info(
                    "[%s] voice lead persisted: vco=%s vo=%s",
                    self.call_uuid,
                    vco,
                    vo,
                )
        except Exception:
            logger.exception("[%s] voice lead persist failed", self.call_uuid)

    async def _notify_max_booking_after_session_if_needed(self) -> None:
        """
        Отложенная отправка MAX-заявки по записи на ТО:
        выполняется в конце сессии, чтобы в note попала финальная цена.
        """
        sd = self.state_machine.service_data or {}
        payload = sd.get("max_booking_notify_payload")
        if not isinstance(payload, dict):
            return
        if sd.get("max_booking_notify_sent"):
            return
        slot_start_iso = str(payload.get("slot_start_iso") or "").strip()
        if not slot_start_iso:
            return
        try:
            slot_start = datetime.fromisoformat(slot_start_iso)
        except Exception:
            logger.exception("[%s] MAX deferred notify: bad slot_start_iso=%r", self.call_uuid, slot_start_iso)
            return

        booking_id = str(
            payload.get("booking_id")
            or sd.get("service_1c_booking_id")
            or ""
        ).strip()
        if not booking_id:
            return

        try:
            from dialog.sto_to_price_inquiry import format_to_price_for_lead
            from max_bot.voice_service_booking_notify import notify_voice_service_booking_to_max

            ok = await notify_voice_service_booking_to_max(
                fio=str(payload.get("fio") or ""),
                phone=str(payload.get("phone") or ""),
                phone_named_by_client=str(payload.get("phone_named_by_client") or ""),
                car_brand=str(payload.get("car_brand") or ""),
                car_model=str(payload.get("car_model") or ""),
                car_brand_raw=str(payload.get("car_brand_raw") or ""),
                car_model_raw=str(payload.get("car_model_raw") or ""),
                car_brand_norm=str(payload.get("car_brand_norm") or ""),
                car_model_norm=str(payload.get("car_model_norm") or ""),
                car_year=str(payload.get("car_year") or ""),
                mileage_norm=str(payload.get("mileage_norm") or ""),
                mileage_raw=str(payload.get("mileage_raw") or ""),
                work_wishes=str(payload.get("work_wishes") or ""),
                slot_start=slot_start,
                booking_id=booking_id,
                mechanic_name=str(payload.get("mechanic_name") or ""),
                price_note=format_to_price_for_lead(sd),
            )
            if ok:
                sd["max_booking_notify_sent"] = True
        except Exception:
            logger.exception("[%s] deferred MAX booking notify failed", self.call_uuid)

    def _lead_phones_for_db(self) -> str:
        """Оба номера для лида: названный клиентом и номер входящего звонка."""
        from dialog.bot_logic import _booking_phones_for_1c

        sd = self.state_machine.service_data or {}
        joined = _booking_phones_for_1c(
            phone_from_db=sd.get("phone_from_db"),
            phone_input=sd.get("phone"),
            caller_phone=getattr(self, "caller_phone", None),
        )
        if joined:
            return joined
        phone_raw = (sd.get("phone_raw") or "").strip()
        if phone_raw:
            return phone_raw[:128]
        cp = getattr(self, "caller_phone", None)
        if cp and str(cp).strip():
            return str(cp).strip()[:128]
        return (sd.get("phone") or "").strip()[:128]

    def _client_phone_for_db(self) -> Optional[str]:
        """
        Телефон для БД сессии — приоритет: оба номера (как в лиде), иначе ANI, иначе phone из sd.
        """
        ph = self._lead_phones_for_db()
        return ph or None

    async def _persist_caller_phone_to_analytics(self) -> None:
        ph = self._client_phone_for_db()
        if not ph:
            return

        def _write() -> None:
            from database.postgresql_manager import VoiceBotAnalyticsDB

            VoiceBotAnalyticsDB.set_caller_phone(self.call_uuid, ph)

        try:
            await asyncio.to_thread(_write)
        except Exception:
            logger.exception("[%s] persist caller_phone to analytics failed", self.call_uuid)

    async def _persist_incoming_did_to_analytics(self) -> None:
        did = (self.incoming_did or "").strip()
        if not did:
            return

        def _write() -> None:
            from database.postgresql_manager import VoiceBotAnalyticsDB

            VoiceBotAnalyticsDB.set_incoming_did(self.call_uuid, did)

        try:
            await asyncio.to_thread(_write)
        except Exception:
            logger.exception("[%s] persist incoming_did to analytics failed", self.call_uuid)

    async def run(self) -> None:
        logger.info("[%s] Call started", self.call_uuid)
        await self._resolve_caller_phone_from_astdb()
        await self._resolve_incoming_did_from_astdb()
        try:
            logger.info("[%s] voice_scenario=%s", self.call_uuid, self.voice_scenario)
            await self._send_greeting()
            await self._dialog_loop()
        except asyncio.CancelledError:
            logger.info("[%s] Call cancelled", self.call_uuid)
        except Exception:
            logger.exception("[%s] Call error", self.call_uuid)
        finally:
            try:
                await self._persist_voice_lead_if_needed()
            except Exception:
                logger.exception("[%s] _persist_voice_lead_if_needed", self.call_uuid)
            try:
                await self._notify_max_booking_after_session_if_needed()
            except Exception:
                logger.exception("[%s] _notify_max_booking_after_session_if_needed", self.call_uuid)
            rel_path = None
            if getattr(self, "_call_recorder", None):
                try:
                    rel_path = self._call_recorder.finalize()
                except Exception:
                    logger.exception("[%s] call recorder finalize failed", self.call_uuid)
            if rel_path:
                try:

                    def _save_audio_path() -> None:
                        from database.postgresql_manager import VoiceBotAnalyticsDB

                        VoiceBotAnalyticsDB.set_audio_storage_path(self.call_uuid, rel_path)

                    await asyncio.to_thread(_save_audio_path)
                except Exception:
                    logger.exception("[%s] save audio_storage_path failed", self.call_uuid)
            try:
                # После диалога в service_data может появиться телефон — как в «Лидах»
                await self._persist_caller_phone_to_analytics()
            except Exception:
                logger.exception("[%s] persist caller_phone before finalize", self.call_uuid)
            try:
                await self._persist_incoming_did_to_analytics()
            except Exception:
                logger.exception("[%s] persist incoming_did before finalize", self.call_uuid)
            try:
                await self._vox.finalize(
                    self.state_machine.state.value,
                    self._client_phone_for_db(),
                    client_spoken_name=self.state_machine.client_name or None,
                )
            except Exception:
                logger.exception("[%s] voice analytics finalize failed", self.call_uuid)
            await self.close()
            logger.info("[%s] Call ended (state=%s)", self.call_uuid, self.state_machine.state.value)

    async def _send_greeting(self):
        """Приветствие (ИнструкцияRAG). legacy: имя + затем меню; v2: сразу вопрос про отдел."""
        if self.voice_scenario == "v2":
            logger.info("[%s] greeting_playback_start (VOICE_SCENARIO=v2)", self.call_uuid)
            wav_name = "01b_greeting_v2_chery_tenet.wav"
            text = GREETING_V2_CHERY_TENET
            await self.play_wav_or_tts(wav_name, text)
            self.state_machine.post_greeting_name_done = True
            logger.info(
                "[%s] greeting_playback_complete v2 (reply window %.1fs from playback_end)",
                self.call_uuid,
                V2_CLIENT_REPLY_WAIT_SEC,
            )
            return
        logger.info("[%s] greeting_playback_start (VOICE_SCENARIO=legacy)", self.call_uuid)
        wav_name = "01_greeting_chery_tenet.wav"
        text = GREETING_CHERY_TENET
        await self.play_wav_or_tts(wav_name, text)
        self._name_listen_deadline = asyncio.get_running_loop().time() + ANSWER_WAIT_SEC
        logger.info(
            "[%s] greeting_playback_complete (name reply until %.3f = now+%.1fs)",
            self.call_uuid,
            self._name_listen_deadline,
            ANSWER_WAIT_SEC,
        )

    def get_department_choice_phrase(self) -> str:
        """Фраза перечисления отделов (единый сценарий для всех DID)."""
        return DEPARTMENT_CHOICE_CHERY_TENET

    def _arm_v2_client_reply_window(self, reason: str) -> None:
        if getattr(self, "voice_scenario", "legacy") != "v2":
            return
        self._name_listen_deadline = asyncio.get_running_loop().time() + V2_CLIENT_REPLY_WAIT_SEC
        logger.info(
            "[%s] v2 reply window (%s) until %.3f (+%.1fs)",
            self.call_uuid,
            reason,
            self._name_listen_deadline,
            V2_CLIENT_REPLY_WAIT_SEC,
        )

    def _mark_playback_end(self, label: str) -> None:
        self._last_playback_end_ts = asyncio.get_running_loop().time()
        self._last_playback_label = label
        if getattr(self, "voice_scenario", "legacy") == "v2":
            if label == "01b_greeting_v2_chery_tenet.wav":
                self._name_listen_deadline = self._last_playback_end_ts + V2_CLIENT_REPLY_WAIT_SEC
                self._begin_v2_reply_slot()
                logger.info(
                    "[%s] v2 reply window after greeting until %.3f (+%.1fs)",
                    self.call_uuid,
                    self._name_listen_deadline,
                    V2_CLIENT_REPLY_WAIT_SEC,
                )
            elif label == "22_department_clarify_v2.wav":
                self._name_listen_deadline = self._last_playback_end_ts + V2_CLIENT_REPLY_WAIT_SEC
                self._begin_v2_reply_slot()
                logger.info(
                    "[%s] v2 reply window after department menu until %.3f (+%.1fs)",
                    self.call_uuid,
                    self._name_listen_deadline,
                    V2_CLIENT_REPLY_WAIT_SEC,
                )
        logger.info("[%s] playback_end[%s]", self.call_uuid, label)

    def _silence_counts_as_unclear_need_reply(self) -> bool:
        """
        Тишина / пустой STT уходит в bot_logic как "" (непонятный ответ), чтобы счётчики
        и перевод на администратора совпадали с нераспознанной речью. Исключение — ENDED.
        """
        return self.state_machine.state != ConversationState.ENDED

    async def _dialog_loop(self):
        while not self._closed:
            self._record_attempt += 1
            if self._last_playback_end_ts is not None:
                delta_ms = (asyncio.get_running_loop().time() - self._last_playback_end_ts) * 1000
                logger.info(
                    "[%s] record_attempt=%d start %.1fms after playback_end[%s]",
                    self.call_uuid,
                    self._record_attempt,
                    delta_ms,
                    self._last_playback_label,
                )
            else:
                logger.info("[%s] record_attempt=%d start with no playback marker", self.call_uuid, self._record_attempt)
            user_audio = await self._record_speech()
            if user_audio is None:
                logger.info("[%s] Client hung up during recording", self.call_uuid)
                break
            if user_audio.size == 0:
                if self._silence_counts_as_unclear_need_reply():
                    if self.state_machine.state in (
                        ConversationState.SERVICE_DATA_COLLECTION,
                        ConversationState.SERVICE_SLOT_SELECTION,
                        ConversationState.SERVICE_BOOKED,
                    ):
                        if self.state_machine.state == ConversationState.SERVICE_DATA_COLLECTION:
                            guard_sec = SERVICE_DATA_COLLECTION_LISTEN_SEC
                            state_tag = "service_data_collection"
                        elif self.state_machine.state == ConversationState.SERVICE_SLOT_SELECTION:
                            guard_sec = SLOT_SELECTION_EMPTY_GUARD_SEC
                            state_tag = "slot_selection"
                        else:
                            guard_sec = SERVICE_BOOKED_EMPTY_GUARD_SEC
                            state_tag = "service_booked"
                        now_ts = asyncio.get_running_loop().time()
                        if (
                            self._last_playback_end_ts is not None
                            and (now_ts - self._last_playback_end_ts) < guard_sec
                        ):
                            logger.info(
                                "[%s] %s empty VAD ignored: %.2fs < guard %.2fs",
                                self.call_uuid,
                                state_tag,
                                now_ts - self._last_playback_end_ts,
                                guard_sec,
                            )
                            continue
                    self.silence_rounds = 0
                    logger.info(
                        "[%s] VAD empty -> unclear via bot_logic (silence_rounds bypass)",
                        self.call_uuid,
                    )
                    logic_text = self._accumulate_v2_slot_fragment("")
                    if not (
                        self._v2_slot_journal_pending and self._is_v2_client_reply_wait_phase()
                    ):
                        await self._log_client_text_for_journal("")
                    await self.process_client_text(
                        logic_text,
                        empty_kind="vad_no_audio",
                        name_listen_deadline_ts=self._name_listen_deadline,
                    )
                    if self.state_machine.state == ConversationState.ENDED:
                        break
                    continue
                self.silence_rounds += 1
                logger.warning("[%s] silence_rounds=%d: VAD returned empty audio", self.call_uuid, self.silence_rounds)
                if self._departments_not_played_yet:
                    self._departments_not_played_yet = False
                    if self.state_machine.post_greeting_name_done:
                        await self._ask_about_departments()
                    else:
                        await self.play_wav_or_tts("12_ask_department.wav", self.get_department_choice_phrase())
                elif self.silence_rounds == 1:
                    await self.play_wav_or_tts("17_repeat.wav", None)
                else:
                    logger.info("[%s] silence_rounds>=2 (VAD empty) -> transfer admin", self.call_uuid)
                    await self.play_wav_or_tts(None, "Перевожу Вас на администратора.")
                    await self._transfer_or_after_hours(
                        "03_transfer_admin.wav", None, voice_admin_reason="silence", skip_announcement=True
                    )
                    break
                continue

            user_text = await self._stt(user_audio)
            from dialog.department_stt_normalize import is_voice_stt_da_as_a

            if (
                self.state_machine.state == ConversationState.SERVICE_SLOT_SELECTION
                and is_voice_stt_da_as_a(user_text)
            ):
                logger.info(
                    "[%s] STT «А» в подтверждении слота → «да»",
                    self.call_uuid,
                )
                user_text = "да"
            if self._is_to_v2_car_confirm_listen() and (user_text or "").strip():
                from dialog.bot_logic import _normalize_voice_confirm_stt

                norm_confirm = _normalize_voice_confirm_stt(user_text)
                if norm_confirm != (user_text or "").strip().lower():
                    logger.info(
                        "[%s] STT car_confirm normalize: %r -> %r",
                        self.call_uuid,
                        user_text[:80],
                        norm_confirm[:80],
                    )
                    user_text = norm_confirm
            stt_meaningless_folded = False
            if user_text.strip() and is_meaningless_voice_stt(user_text):
                logger.info(
                    "[%s] STT meaningless fragment, treat as empty: %r",
                    self.call_uuid,
                    user_text[:120],
                )
                user_text = ""
                stt_meaningless_folded = True
            logic_text = self._accumulate_v2_slot_fragment(user_text)
            if not user_text.strip():
                if self._silence_counts_as_unclear_need_reply():
                    if self.state_machine.state in (
                        ConversationState.SERVICE_DATA_COLLECTION,
                        ConversationState.SERVICE_SLOT_SELECTION,
                        ConversationState.SERVICE_BOOKED,
                    ):
                        if self.state_machine.state == ConversationState.SERVICE_DATA_COLLECTION:
                            guard_sec = SERVICE_DATA_COLLECTION_LISTEN_SEC
                            state_tag = "service_data_collection"
                        elif self.state_machine.state == ConversationState.SERVICE_SLOT_SELECTION:
                            guard_sec = SLOT_SELECTION_EMPTY_GUARD_SEC
                            state_tag = "slot_selection"
                        else:
                            guard_sec = SERVICE_BOOKED_EMPTY_GUARD_SEC
                            state_tag = "service_booked"
                        now_ts = asyncio.get_running_loop().time()
                        if (
                            self._last_playback_end_ts is not None
                            and (now_ts - self._last_playback_end_ts) < guard_sec
                        ):
                            logger.info(
                                "[%s] %s empty STT ignored: %.2fs < guard %.2fs",
                                self.call_uuid,
                                state_tag,
                                now_ts - self._last_playback_end_ts,
                                guard_sec,
                            )
                            continue
                    self.silence_rounds = 0
                    logger.info(
                        "[%s] STT empty%s -> unclear via bot_logic (audio %d samples)",
                        self.call_uuid,
                        " (meaningless folded)" if stt_meaningless_folded else "",
                        user_audio.size,
                    )
                    if not (
                        self._v2_slot_journal_pending and self._is_v2_client_reply_wait_phase()
                    ):
                        if not stt_meaningless_folded:
                            await self._log_client_text_for_journal("")
                    await self.process_client_text(
                        logic_text,
                        empty_kind="stt_empty",
                        name_listen_deadline_ts=self._name_listen_deadline,
                    )
                    if self.state_machine.state == ConversationState.ENDED:
                        break
                    continue
                self.silence_rounds += 1
                logger.warning("[%s] silence_rounds=%d: STT returned empty (audio had %d samples)", self.call_uuid, self.silence_rounds, user_audio.size)
                if self._departments_not_played_yet:
                    self._departments_not_played_yet = False
                    if self.state_machine.post_greeting_name_done:
                        await self._ask_about_departments()
                    else:
                        await self.play_wav_or_tts("12_ask_department.wav", self.get_department_choice_phrase())
                elif self.silence_rounds == 1:
                    await self.play_wav_or_tts("17_repeat.wav", None)
                else:
                    logger.info("[%s] silence_rounds>=2 (STT empty) -> transfer admin", self.call_uuid)
                    await self.play_wav_or_tts(None, "Перевожу Вас на администратора.")
                    await self._transfer_or_after_hours(
                        "03_transfer_admin.wav", None, voice_admin_reason="silence", skip_announcement=True
                    )
                    break
                continue

            self.silence_rounds = 0
            logger.info("[%s] CLIENT: %s", self.call_uuid, user_text)
            if not (
                self._v2_slot_journal_pending and self._is_v2_client_reply_wait_phase()
            ):
                await self._log_client_text_for_journal(user_text)

            if self.state_machine.state == ConversationState.ENDED:
                break

            await self.process_client_text(logic_text)

            # Перебивание gate5: реплика уже записана во время длинного WAV.
            while self._pending_barge_in_audio is not None:
                barge_audio = self._pending_barge_in_audio
                self._pending_barge_in_audio = None
                if barge_audio.size == 0:
                    break
                user_text = await self._stt(barge_audio)
                if not user_text.strip():
                    break
                self.silence_rounds = 0
                logger.info("[%s] CLIENT (barge-in): %s", self.call_uuid, user_text)
                await self._log_client_text_for_journal(user_text)
                if self.state_machine.state == ConversationState.ENDED:
                    break
                await self.process_client_text(user_text)
                if self.state_machine.state == ConversationState.ENDED:
                    break

            # legacy: после фиксации имени — перечисление отделов (12_*.wav). v2: меню только через _ask_about_departments при неясной потребности.
            if (
                self.voice_scenario == "legacy"
                and self._departments_not_played_yet
                and self.state_machine.state == ConversationState.INITIAL
                and self.state_machine.post_greeting_name_done
            ):
                self._departments_not_played_yet = False
                await self._ask_about_departments()

            if self.state_machine.state == ConversationState.ENDED:
                break

    # ------------------------------------------------------------------
    # Audio I/O via AudioSocket
    # ------------------------------------------------------------------

    def _is_to_v2_gate5_listen(self) -> bool:
        sd = self.state_machine.service_data or {}
        return (
            self.state_machine.state == ConversationState.SERVICE_DATA_COLLECTION
            and bool(sd.get("to_v2"))
            and sd.get("to_v2_substate") == "gate5"
        )

    def _is_to_v2_car_confirm_listen(self) -> bool:
        sd = self.state_machine.service_data or {}
        return (
            self.state_machine.state == ConversationState.SERVICE_DATA_COLLECTION
            and bool(sd.get("to_v2"))
            and sd.get("to_v2_substate") == "car_confirm"
        )

    def _is_menu14_repeat_wait(self) -> bool:
        """Ожидание ответа после повтора меню 14 в NEED_IDENTIFIED."""
        return (
            self.state_machine.state == ConversationState.NEED_IDENTIFIED
            and self.state_machine.identified_need == ClientNeed.SERVICE
            and bool(getattr(self.state_machine, "menu14_booking_active", False))
            and int(getattr(self.state_machine, "menu14_unclear_attempts", 0)) >= 1
        )

    def _is_menu14_booking_wait(self) -> bool:
        """Любое ожидание ответа в menu14 (первый вопрос и повтор)."""
        return (
            self.state_machine.state == ConversationState.NEED_IDENTIFIED
            and self.state_machine.identified_need == ClientNeed.SERVICE
            and bool(getattr(self.state_machine, "menu14_booking_active", False))
        )

    def _is_to_v2_phone_step_listen(self) -> bool:
        sd = self.state_machine.service_data or {}
        return (
            self.state_machine.state == ConversationState.SERVICE_DATA_COLLECTION
            and bool(sd.get("to_v2"))
            and int(sd.get("to_v2_step") or 0) == 2
        )

    async def _record_speech(self) -> Optional[np.ndarray]:
        """Читает аудио до конца реплики (VAD). До начала речи ждём ANSWER_WAIT_SEC с момента вопроса бота."""
        is_service_data_collection = (
            self.state_machine.state == ConversationState.SERVICE_DATA_COLLECTION
        )
        is_to_v2_sdc = is_service_data_collection and bool(
            (self.state_machine.service_data or {}).get("to_v2")
        )
        is_slot_await_new_date = (
            self.state_machine.state == ConversationState.SERVICE_SLOT_SELECTION
            and bool((self.state_machine.service_data or {}).get("slot_await_new_date"))
        )
        is_slot_confirmation = (
            self.state_machine.state == ConversationState.SERVICE_SLOT_SELECTION
            and not is_slot_await_new_date
        )
        is_price_disambiguation = (
            (self.state_machine.service_data or {}).get("to_v2_substate") == "price"
        )
        is_car_confirm = self._is_to_v2_car_confirm_listen()
        is_menu14_repeat_wait = self._is_menu14_repeat_wait()
        is_menu14_booking_wait = self._is_menu14_booking_wait()
        is_to_v2_phone_step = self._is_to_v2_phone_step_listen()
        max_duration_sec = 10.0 if is_service_data_collection else 5.0
        if is_car_confirm:
            max_duration_sec = CAR_CONFIRM_LISTEN_SEC + CAR_CONFIRM_COLLECT_SEC
        elif is_to_v2_sdc:
            max_duration_sec = SERVICE_DATA_COLLECTION_LISTEN_SEC + ANSWER_COLLECT_AFTER_SPEECH_SEC
        if is_to_v2_phone_step:
            max_duration_sec = max(
                max_duration_sec,
                TO_V2_PHONE_LISTEN_SEC + TO_V2_PHONE_COLLECT_AFTER_SPEECH_SEC,
            )
        vad_silence_ms = V2_SERVICE_DATA_COLLECTION_SILENCE_MS if is_to_v2_sdc else GENERAL_VAD_SILENCE_MS
        if is_to_v2_phone_step:
            vad_silence_ms = max(vad_silence_ms, TO_V2_PHONE_SILENCE_MS)
        if is_menu14_booking_wait:
            vad_silence_ms = max(vad_silence_ms, MENU14_SILENCE_MS)
        vad = VADCollector(
            sample_rate=ASTERISK_SAMPLE_RATE,
            silence_ms=vad_silence_ms,
            max_duration_sec=max_duration_sec,
        )
        attempt_no = self._record_attempt
        loop = asyncio.get_running_loop()
        is_v2_first_department_wait = (
            getattr(self, "voice_scenario", "legacy") == "v2"
            and self.state_machine.state == ConversationState.INITIAL
            and self.state_machine.post_greeting_name_done
            and getattr(self.state_machine, "department_prompts_shown", 0) == 0
        )
        is_v2_post_department_clarify = (
            getattr(self, "voice_scenario", "legacy") == "v2"
            and self.state_machine.state == ConversationState.INITIAL
            and self.state_machine.post_greeting_name_done
            and getattr(self.state_machine, "department_prompts_shown", 0) >= 1
        )
        is_v2_client_reply_wait = is_v2_first_department_wait or is_v2_post_department_clarify
        if self.state_machine.post_greeting_name_done and not is_v2_client_reply_wait:
            self._name_listen_deadline = None
        is_after_hours_name = self.state_machine.state == ConversationState.AFTER_HOURS_NAME
        # Нерабочее 23 + ответ после WAV 22 «Что Вас интересует»: полное окно ожидания, без раннего VAD-обрыва.
        # Для первого вопроса про отдел в v2 также держим устойчивое окно ответа.
        tight_listen_window = (
            is_after_hours_name
            or is_v2_post_department_clarify
            or is_v2_first_department_wait
            or is_service_data_collection
            or is_slot_await_new_date
        )
        listen_sec = ANSWER_WAIT_SEC
        if tight_listen_window:
            listen_sec = max(ANSWER_WAIT_SEC, AFTER_HOURS_NAME_LISTEN_SEC)
        if is_menu14_booking_wait:
            # Для menu14 всегда выдерживаем минимум 8 секунд на ответ.
            listen_sec = max(listen_sec, MENU14_REPEAT_LISTEN_SEC)
        if is_to_v2_phone_step:
            listen_sec = max(listen_sec, TO_V2_PHONE_LISTEN_SEC)
        if is_v2_client_reply_wait:
            listen_sec = max(listen_sec, V2_CLIENT_REPLY_WAIT_SEC)
        if is_car_confirm:
            listen_sec = CAR_CONFIRM_LISTEN_SEC
        elif is_service_data_collection:
            listen_sec = max(listen_sec, SERVICE_DATA_COLLECTION_LISTEN_SEC)
            sd_listen = self.state_machine.service_data or {}
            # Шаг даты/времени: гарантированно >= MIN_CLIENT_REPLY_SEC после фразы бота.
            if (
                sd_listen.get("to_v2")
                and int(sd_listen.get("to_v2_step") or 0) == 7
            ) or (
                sd_listen.get("mileage_work_confirmed")
                and not sd_listen.get("date_time_confirmed")
            ):
                listen_sec = max(listen_sec, DATE_TIME_LISTEN_SEC, MIN_CLIENT_REPLY_SEC)
        if is_slot_await_new_date:
            listen_sec = max(listen_sec, SLOT_AWAIT_NEW_DATE_LISTEN_SEC)
        listen_deadline = loop.time() + listen_sec
        if is_v2_client_reply_wait and self._name_listen_deadline is not None:
            listen_deadline = min(listen_deadline, self._name_listen_deadline)
        elif (
            not self.state_machine.post_greeting_name_done
            and self._name_listen_deadline is not None
        ):
            listen_deadline = min(listen_deadline, self._name_listen_deadline)
        collect_deadline: Optional[float] = None
        # v2 сбор ТО: продлеваем после начала речи (длинные марка/пробег).
        skip_extended_collect = (not self.state_machine.post_greeting_name_done) or (
            tight_listen_window and not is_to_v2_sdc and not is_v2_client_reply_wait
        ) or is_car_confirm
        total_frames = 0
        audio_frames = 0
        first_raw_audio_logged = False
        first_listened_audio_logged = False
        skipped_guard_audio_frames = 0
        guard_reported = False
        first_speech_ts: Optional[float] = None
        menu14_short_extension_used = False
        # Антиэхо после WAV/TTS. После v2-приветствия — короткий guard: длинный WAV уже
        # закончился, не съедать начало «Австралия» (V2_GREETING_GUARD_MS давал ~1.2 с тишины).
        if (
            is_v2_client_reply_wait
            and self._last_playback_label
            and (
                "greeting" in self._last_playback_label.lower()
                or self._last_playback_label == "22_department_clarify_v2.wav"
            )
        ):
            effective_guard_ms = max(POST_PLAYBACK_GUARD_MS, 400.0)
        elif is_car_confirm and self._last_playback_label and (
            "верно" in self._last_playback_label.lower()
        ):
            effective_guard_ms = max(POST_PLAYBACK_GUARD_MS, 400.0)
        elif is_slot_confirmation:
            # Короткие «давайте» / «записывай» начинались сразу после вопроса,
            # и 650 мс могли отрезать начало слова.
            effective_guard_ms = max(POST_PLAYBACK_GUARD_MS, 400.0)
        elif is_price_disambiguation:
            # Не срезать начало коротких «автомат», «два», «полный».
            effective_guard_ms = max(POST_PLAYBACK_GUARD_MS, 400.0)
        else:
            effective_guard_ms = max(POST_PLAYBACK_GUARD_MS, 650.0)
        record_start_ts = loop.time()
        tight_discard_round = 0
        tight_max_discards = 25
        audio = np.array([], dtype=np.float32)

        while True:
            while True:
                now = loop.time()
                if vad.speech_ended:
                    if is_to_v2_phone_step:
                        # До начала диктовки ждём в listen-окне; после начала — длинная
                        # пауза между цифрами (TO_V2_PHONE_SILENCE_MS) означает «договорил».
                        if collect_deadline is not None:
                            break
                    else:
                        if (
                            is_menu14_booking_wait
                            and not menu14_short_extension_used
                            and collect_deadline is None
                            and first_speech_ts is not None
                        ):
                            speech_span_sec = now - first_speech_ts
                            if 0.0 < speech_span_sec <= MENU14_SHORT_FRAGMENT_MAX_SEC:
                                extra_deadline = min(
                                    listen_deadline,
                                    now + MENU14_SHORT_FRAGMENT_EXTRA_SEC,
                                )
                                if extra_deadline > now:
                                    menu14_short_extension_used = True
                                    collect_deadline = extra_deadline
                                    logger.info(
                                        "[%s] menu14 short fragment %.2fs -> extend listen by %.2fs",
                                        self.call_uuid,
                                        speech_span_sec,
                                        float(extra_deadline - now),
                                    )
                                    continue
                        break
                if collect_deadline is None:
                    time_left = listen_deadline - now
                else:
                    time_left = collect_deadline - now
                if time_left <= 0:
                    break

                try:
                    frame = await asyncio.wait_for(read_frame(self._reader), timeout=time_left)
                except asyncio.TimeoutError:
                    break

                if frame is None:
                    return None  # connection closed

                total_frames += 1

                if frame.type in (FrameType.ERROR, FrameType.HANGUP):
                    return None  # hangup

                if frame.type == FrameType.AUDIO and len(frame.payload) > 0:
                    audio_frames += 1
                    pcm_int16 = np.frombuffer(frame.payload, dtype=np.int16)
                    if self._call_recorder and self._call_recorder.enabled:
                        self._call_recorder.feed_client_pcm(pcm_int16)
                    pcm_float = pcm_int16.astype(np.float32) / 32768.0
                    chunk_rms = float(np.sqrt(np.mean(pcm_float.astype(np.float64) ** 2)))
                    playback_age_ms: Optional[float] = None
                    if self._last_playback_end_ts is not None:
                        playback_age_ms = (loop.time() - self._last_playback_end_ts) * 1000

                    if not first_raw_audio_logged:
                        first_raw_audio_logged = True
                        if self._last_playback_end_ts is not None:
                            logger.debug(
                                "[%s] record_attempt=%d first_raw_audio_frame %.1fms after playback_end[%s], payload=%d, chunk_rms=%.6f",
                                self.call_uuid,
                                attempt_no,
                                playback_age_ms,
                                self._last_playback_label,
                                len(frame.payload),
                                chunk_rms,
                            )
                        else:
                            logger.debug(
                                "[%s] record_attempt=%d first_raw_audio_frame with no playback marker, payload=%d, chunk_rms=%.6f",
                                self.call_uuid,
                                attempt_no,
                                len(frame.payload),
                                chunk_rms,
                            )

                    if playback_age_ms is not None and playback_age_ms < effective_guard_ms:
                        skipped_guard_audio_frames += 1
                        if skipped_guard_audio_frames <= 5:
                            logger.debug(
                                "[%s] record_attempt=%d guard_skip frame=%d age=%.1fms payload=%d chunk_rms=%.6f guard_ms=%.0f",
                                self.call_uuid,
                                attempt_no,
                                skipped_guard_audio_frames,
                                playback_age_ms,
                                len(frame.payload),
                                chunk_rms,
                                effective_guard_ms,
                            )
                        continue

                    if skipped_guard_audio_frames and not guard_reported:
                        logger.debug(
                            "[%s] record_attempt=%d guard_window_passed, skipped_audio_frames=%d, first_listen_age=%.1fms",
                            self.call_uuid,
                            attempt_no,
                            skipped_guard_audio_frames,
                            playback_age_ms if playback_age_ms is not None else -1.0,
                        )
                        guard_reported = True

                    if not first_listened_audio_logged:
                        first_listened_audio_logged = True
                        logger.debug(
                            "[%s] record_attempt=%d first_listened_audio_frame payload=%d chunk_rms=%.6f",
                            self.call_uuid,
                            attempt_no,
                            len(frame.payload),
                            chunk_rms,
                        )

                    if first_listened_audio_logged and (audio_frames - skipped_guard_audio_frames) <= 5:
                        logger.debug(
                            "[%s] record_attempt=%d listened_audio_frame=%d payload=%d chunk_rms=%.6f",
                            self.call_uuid,
                            attempt_no,
                            audio_frames - skipped_guard_audio_frames,
                            len(frame.payload),
                            chunk_rms,
                        )
                    vad.feed(pcm_float)
                    if first_speech_ts is None and vad.is_collecting:
                        first_speech_ts = loop.time()
                    if vad.is_collecting and collect_deadline is None:
                        # Если клиент начал говорить в пределах окна ответа, не обрываем
                        # его реплику по истечению listen-таймера: слушаем до завершения
                        # речи (VAD + тишина), оставляя запас на длинные ответы.
                        if is_to_v2_phone_step:
                            collect_sec = TO_V2_PHONE_COLLECT_AFTER_SPEECH_SEC
                        elif is_car_confirm:
                            collect_sec = max(ANSWER_COLLECT_AFTER_SPEECH_SEC, CAR_CONFIRM_COLLECT_SEC)
                        elif is_v2_client_reply_wait:
                            collect_sec = max(ANSWER_COLLECT_AFTER_SPEECH_SEC, 2.5)
                        elif not skip_extended_collect:
                            collect_sec = ANSWER_COLLECT_AFTER_SPEECH_SEC
                        else:
                            collect_sec = ANSWER_COLLECT_AFTER_SPEECH_SEC
                        collect_deadline = max(
                            listen_deadline,
                            loop.time() + collect_sec,
                        )
                        logger.info(
                            "[%s] speech started -> collect window %.2fs "
                            "(listen_left=%.2fs, collect_sec=%.2fs)",
                            self.call_uuid,
                            float(collect_deadline - loop.time()),
                            float(listen_deadline - loop.time()),
                            float(collect_sec),
                        )

            audio = vad.get_audio()
            if not tight_listen_window:
                break
            elapsed = loop.time() - record_start_ts
            listen_cap = max(ANSWER_WAIT_SEC, AFTER_HOURS_NAME_LISTEN_SEC)
            if is_car_confirm:
                listen_cap = CAR_CONFIRM_LISTEN_SEC
            elif is_service_data_collection:
                listen_cap = max(listen_cap, SERVICE_DATA_COLLECTION_LISTEN_SEC)
                sd_cap = self.state_machine.service_data or {}
                if (
                    sd_cap.get("to_v2")
                    and int(sd_cap.get("to_v2_step") or 0) == 7
                ) or (
                    sd_cap.get("mileage_work_confirmed")
                    and not sd_cap.get("date_time_confirmed")
                ):
                    listen_cap = max(listen_cap, DATE_TIME_LISTEN_SEC, MIN_CLIENT_REPLY_SEC)
            if is_slot_await_new_date:
                listen_cap = max(listen_cap, SLOT_AWAIT_NEW_DATE_LISTEN_SEC)
            dur = len(audio) / float(ASTERISK_SAMPLE_RATE)
            discard_max_sec = (
                V2_SERVICE_DATA_COLLECTION_DISCARD_MAX_SEC
                if is_to_v2_sdc or is_slot_await_new_date
                else V2_FIRST_DEPARTMENT_DISCARD_MAX_SEC
                if is_v2_first_department_wait
                else AFTER_HOURS_NAME_DISCARD_MAX_SEC
            )
            if self._is_to_v2_gate5_listen() or is_car_confirm or is_v2_client_reply_wait:
                discard_max_sec = 0.0
            if (
                elapsed < listen_cap
                and 0 < dur < discard_max_sec
                and tight_discard_round < tight_max_discards
            ):
                tight_discard_round += 1
                logger.info(
                    "[%s] tight_listen_window: discard short VAD segment %.0fms (round %d, elapsed=%.2fs)",
                    self.call_uuid,
                    dur * 1000,
                    tight_discard_round,
                    elapsed,
                )
                vad.reset()
                continue
            break
        if audio.size > 0:
            rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
            logger.info(
                "[%s] _record_speech attempt=%d: got %.1fms audio, RMS=%.6f, total_frames=%d, audio_frames=%d, speech_detected=%s",
                self.call_uuid,
                attempt_no,
                len(audio) / ASTERISK_SAMPLE_RATE * 1000,
                rms,
                total_frames,
                audio_frames,
                vad.is_collecting,
            )
        else:
            logger.info(
                "[%s] _record_speech attempt=%d: empty (timeout or no speech), total_frames=%d, audio_frames=%d, guard_skipped=%d",
                self.call_uuid,
                attempt_no,
                total_frames,
                audio_frames,
                skipped_guard_audio_frames,
            )
        return audio

    async def _send_audio_pcm_with_barge_in(
        self,
        audio_float: np.ndarray,
        source_sr: int,
        *,
        label: str,
    ) -> Optional[np.ndarray]:
        """
        Воспроизведение с перебиванием: если клиент говорит во время длинного WAV,
        останавливаем TTS и возвращаем записанную реплику (gate5).
        """
        if self._closed or audio_float.size == 0:
            return None

        if source_sr != ASTERISK_SAMPLE_RATE:
            import scipy.signal
            n_samples = int(len(audio_float) * ASTERISK_SAMPLE_RATE / source_sr)
            audio_float = scipy.signal.resample(audio_float, n_samples).astype(np.float32)

        audio_int16 = (np.clip(audio_float, -1.0, 1.0) * 32767).astype(np.int16)
        raw = audio_int16.tobytes()
        chunk_size = ASTERISK_SAMPLE_RATE * 2 * 20 // 1000
        loop = asyncio.get_running_loop()
        playback_start_ts = loop.time()
        guard_ms = max(POST_PLAYBACK_GUARD_MS, 650.0)
        vad = VADCollector(
            sample_rate=ASTERISK_SAMPLE_RATE,
            silence_ms=V2_SERVICE_DATA_COLLECTION_SILENCE_MS,
            max_duration_sec=15.0,
        )
        interrupted = False
        min_barge_samples = int(ASTERISK_SAMPLE_RATE * 0.25)

        async def _read_client_frames(budget_sec: float) -> None:
            nonlocal interrupted
            deadline = loop.time() + budget_sec
            while loop.time() < deadline and not interrupted:
                try:
                    frame = await asyncio.wait_for(read_frame(self._reader), timeout=0.02)
                except asyncio.TimeoutError:
                    break
                if frame is None:
                    self._closed = True
                    return
                if frame.type in (FrameType.ERROR, FrameType.HANGUP):
                    self._closed = True
                    return
                if frame.type != FrameType.AUDIO or len(frame.payload) == 0:
                    continue
                pcm_int16 = np.frombuffer(frame.payload, dtype=np.int16)
                if self._call_recorder and self._call_recorder.enabled:
                    self._call_recorder.feed_client_pcm(pcm_int16)
                pcm_float = pcm_int16.astype(np.float32) / 32768.0
                age_ms = (loop.time() - playback_start_ts) * 1000
                if age_ms < guard_ms:
                    continue
                vad.feed(pcm_float)
                if vad.speech_ended and vad.get_audio().size >= min_barge_samples:
                    interrupted = True

        for offset in range(0, len(raw), chunk_size):
            if self._closed or interrupted:
                break
            chunk = raw[offset : offset + chunk_size]
            if self._call_recorder and self._call_recorder.enabled:
                self._call_recorder.feed_bot_pcm(np.frombuffer(chunk, dtype=np.int16))
            frame = build_audio_frame(chunk)
            self._writer.write(frame)
            try:
                await self._writer.drain()
            except ConnectionError:
                self._closed = True
                return None
            await asyncio.sleep(0.018)
            await _read_client_frames(0.035)

        if interrupted:
            collect_deadline = loop.time() + ANSWER_COLLECT_AFTER_SPEECH_SEC
            while loop.time() < collect_deadline and not self._closed:
                if vad.speech_ended:
                    break
                try:
                    frame = await asyncio.wait_for(
                        read_frame(self._reader),
                        timeout=min(0.15, collect_deadline - loop.time()),
                    )
                except asyncio.TimeoutError:
                    continue
                if frame is None or frame.type in (FrameType.ERROR, FrameType.HANGUP):
                    break
                if frame.type == FrameType.AUDIO and len(frame.payload) > 0:
                    pcm_int16 = np.frombuffer(frame.payload, dtype=np.int16)
                    if self._call_recorder and self._call_recorder.enabled:
                        self._call_recorder.feed_client_pcm(pcm_int16)
                    vad.feed(pcm_int16.astype(np.float32) / 32768.0)
            user_audio = vad.get_audio()
            self._mark_playback_end(f"{label}:barge_in")
            logger.info(
                "[%s] barge_in during %s: %.0fms captured",
                self.call_uuid,
                label,
                len(user_audio) / ASTERISK_SAMPLE_RATE * 1000 if user_audio.size else 0,
            )
            return user_audio if user_audio.size > 0 else None

        self._mark_playback_end(label)
        return None

    async def _send_audio_pcm(self, audio_float: np.ndarray, source_sr: int) -> None:
        """Resample audio to 8 kHz int16 and send through AudioSocket in chunks."""
        if self._closed or audio_float.size == 0:
            return

        if source_sr != ASTERISK_SAMPLE_RATE:
            import scipy.signal
            n_samples = int(len(audio_float) * ASTERISK_SAMPLE_RATE / source_sr)
            audio_float = scipy.signal.resample(audio_float, n_samples).astype(np.float32)

        audio_int16 = (np.clip(audio_float, -1.0, 1.0) * 32767).astype(np.int16)
        raw = audio_int16.tobytes()

        chunk_size = ASTERISK_SAMPLE_RATE * 2 * 20 // 1000  # 20ms chunks = 320 bytes
        for offset in range(0, len(raw), chunk_size):
            if self._closed:
                return
            chunk = raw[offset:offset + chunk_size]
            if self._call_recorder and self._call_recorder.enabled:
                self._call_recorder.feed_bot_pcm(np.frombuffer(chunk, dtype=np.int16))
            frame = build_audio_frame(chunk)
            self._writer.write(frame)
            try:
                await self._writer.drain()
            except ConnectionError:
                self._closed = True
                return
            await asyncio.sleep(0.018)  # ~20ms pacing to avoid buffer overflow

    # ------------------------------------------------------------------
    # STT via HTTP
    # ------------------------------------------------------------------

    async def _stt(self, audio_float: np.ndarray) -> str:
        """Send audio to STT service and get text back."""
        if audio_float.size == 0:
            return ""

        if ASTERISK_SAMPLE_RATE != STT_SAMPLE_RATE:
            import scipy.signal
            n_samples = int(len(audio_float) * STT_SAMPLE_RATE / ASTERISK_SAMPLE_RATE)
            audio_float = scipy.signal.resample(audio_float, n_samples).astype(np.float32)

        pcm_int16 = (np.clip(audio_float, -1.0, 1.0) * 32767).astype(np.int16)
        raw_bytes = pcm_int16.tobytes()

        t0 = time.perf_counter()
        try:
            import aiohttp
            session = await self._get_http()
            data = aiohttp.FormData()
            data.add_field("file", raw_bytes, filename="audio.pcm", content_type="application/octet-stream")
            data.add_field("sample_rate", str(STT_SAMPLE_RATE))
            data.add_field("language", "ru")

            async with session.post(f"{self._stt_url}/transcribe/bytes", data=data, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.error("[%s] STT error: %s", self.call_uuid, resp.status)
                    return ""
                result = await resp.json()
                text = result.get("text", "")
        except Exception:
            logger.exception("[%s] STT request failed", self.call_uuid)
            return ""

        elapsed = (time.perf_counter() - t0) * 1000
        logger.info("[%s] STT: %.0fms, input=%d samples -> '%s'", self.call_uuid, elapsed, audio_float.size, text)
        return text

    # ------------------------------------------------------------------
    # TTS via HTTP
    # ------------------------------------------------------------------

    async def _tts(self, text: str) -> tuple[np.ndarray, int]:
        """Send text to TTS service and get WAV audio back."""
        from services.voice.voice_phrases import apply_silero_question_intonation

        text = (text or "").replace("Тенет", "Тэнет").replace("тенет", "тэнет")
        text = apply_silero_question_intonation(text)
        text = replace_numbers_for_tts(text)

        t0 = time.perf_counter()
        try:
            import aiohttp
            session = await self._get_http()
            data = aiohttp.FormData()
            data.add_field("text", text)
            data.add_field("speaker", VOICE_SPEAKER)
            data.add_field("sample_rate", str(VOICE_SAMPLE_RATE))
            data.add_field("speed", str(VOICE_SPEED))

            async with session.post(f"{self._tts_url}/synthesize", data=data, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.error("[%s] TTS error: %s", self.call_uuid, resp.status)
                    return np.array([], dtype=np.float32), ASTERISK_SAMPLE_RATE
                wav_bytes = await resp.read()
        except Exception:
            logger.exception("[%s] TTS request failed", self.call_uuid)
            return np.array([], dtype=np.float32), ASTERISK_SAMPLE_RATE

        import soundfile as sf
        audio_data, sample_rate = sf.read(io.BytesIO(wav_bytes), dtype="float32")
        elapsed = (time.perf_counter() - t0) * 1000
        logger.info("[%s] TTS: %.0fms, %.1fs audio @ %d Hz", self.call_uuid, elapsed, len(audio_data) / sample_rate, sample_rate)
        return audio_data, sample_rate

    # ------------------------------------------------------------------
    # say / play_wav_or_tts (required by BotDialogMixin)
    # ------------------------------------------------------------------

    async def say(self, text: str) -> None:
        await self._maybe_flush_v2_slot_journal_before_bot()
        self.last_bot_phrase = (None, text)
        audio, sr = await self._tts(text)
        if audio.size > 0:
            await self._send_audio_pcm(audio, sr)
            self._mark_playback_end(f"tts:{text[:40]}")
            await self._vox.log_bot_utterance(None, text)

    async def play_wav_or_tts(
        self,
        wav_file: Optional[str],
        text: Optional[str],
        *,
        barge_in: bool = False,
    ) -> None:
        await self._maybe_flush_v2_slot_journal_before_bot()
        self.last_bot_phrase = (wav_file, text)

        if wav_file == "12_ask_department.wav":
            self._departments_not_played_yet = False
            dept_wav = "12_ask_department_chery_tenet.wav"
            if (self._audio_responses_dir / dept_wav).exists() or dept_wav in self._wav_cache:
                wav_file = dept_wav
            else:
                await self.say(self.get_department_choice_phrase())
                return

        if wav_file == "22_department_clarify_v2.wav":
            self._departments_not_played_yet = False
            tts_fallback = (text or "").strip() or DEPARTMENT_CLARIFY_V2_CHERY_TENET
            cached = self._wav_cache.get(wav_file)
            if cached is not None:
                data, fs = cached
                logger.info("[%s] BOT [WAV]: %s", self.call_uuid, wav_file)
                if barge_in:
                    barge = await self._send_audio_pcm_with_barge_in(data, int(fs), label=wav_file)
                    if barge is not None and barge.size > 0:
                        self._pending_barge_in_audio = barge
                else:
                    await self._send_audio_pcm(data, fs)
                    self._mark_playback_end(wav_file)
                await self._vox.log_bot_utterance(wav_file, tts_fallback)
                return
            path = self._audio_responses_dir / wav_file
            if path.exists():
                import soundfile as sf

                data, fs = sf.read(path, dtype="float32")
                self._wav_cache[wav_file] = (data, int(fs))
                logger.info("[%s] BOT [WAV]: %s", self.call_uuid, wav_file)
                await self._send_audio_pcm(data, int(fs))
                self._mark_playback_end(wav_file)
                await self._vox.log_bot_utterance(wav_file, tts_fallback)
                return
            await self.say(tts_fallback)
            if getattr(self, "voice_scenario", "legacy") == "v2":
                self._arm_v2_client_reply_window("22_department_clarify_tts")
            return

        if wav_file:
            cached = self._wav_cache.get(wav_file)
            if cached is not None:
                data, fs = cached
                logger.info("[%s] BOT [WAV]: %s", self.call_uuid, WAV_FILE_TEXTS.get(wav_file, ""))
                if barge_in:
                    barge = await self._send_audio_pcm_with_barge_in(data, int(fs), label=wav_file)
                    if barge is not None and barge.size > 0:
                        self._pending_barge_in_audio = barge
                else:
                    await self._send_audio_pcm(data, fs)
                    self._mark_playback_end(wav_file)
                await self._maybe_signal_transfer(wav_file)
                await self._vox.log_bot_utterance(wav_file, text or WAV_FILE_TEXTS.get(wav_file, ""))
                return
            path = self._audio_responses_dir / wav_file
            if path.exists():
                import soundfile as sf
                data, fs = sf.read(path, dtype="float32")
                self._wav_cache[wav_file] = (data, int(fs))
                logger.info("[%s] BOT [WAV]: %s", self.call_uuid, WAV_FILE_TEXTS.get(wav_file, ""))
                if barge_in:
                    barge = await self._send_audio_pcm_with_barge_in(data, int(fs), label=wav_file)
                    if barge is not None and barge.size > 0:
                        self._pending_barge_in_audio = barge
                else:
                    await self._send_audio_pcm(data, int(fs))
                    self._mark_playback_end(wav_file)
                await self._maybe_signal_transfer(wav_file)
                await self._vox.log_bot_utterance(wav_file, text or WAV_FILE_TEXTS.get(wav_file, ""))
                return
            text = WAV_FILE_TEXTS.get(wav_file, text)

        if text:
            await self.say(text)

    async def _maybe_signal_transfer(self, wav_file: str) -> None:
        """AMI Redirect после WAV перевода: пауза, чтобы Asterisk дописал кадры в канал."""
        if not is_transfer_to_admin_wav(wav_file):
            # Не трогаем ami_result у последнего перевода: иначе любой следующий WAV
            # (приветствие, повтор и т.д.) затирал бы реальный исход AMI.
            return
        channel_id = getattr(self, "asterisk_channel_id", None)
        if not channel_id:
            logger.info("[%s] Transfer signal (no channel_id): %s", self.call_uuid, wav_file)
            self._voice_transfer_last_outcome = "no_operator"
            await self._vox.patch_latest_transfer_ami("no_operator")
            return
        exten = get_exten_for_wav(wav_file)
        logger.info("[%s] Transfer signal: %s -> exten=%s", self.call_uuid, wav_file, exten)
        await asyncio.sleep(TRANSFER_AMI_DELAY_SEC)
        ok = await asyncio.to_thread(request_transfer_to_admin, channel_id, None, exten, self.call_uuid)
        if ok:
            logger.info("[%s] AMI transfer ok (exten=%s)", self.call_uuid, exten)
            self._voice_transfer_last_outcome = "transfer_started"
            await self._vox.patch_latest_transfer_ami("transfer_started")
        else:
            logger.warning("[%s] AMI transfer failed (exten=%s)", self.call_uuid, exten)
            self._voice_transfer_last_outcome = "no_operator"
            await self._vox.patch_latest_transfer_ami("no_operator")
