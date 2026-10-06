"""
Сценарий записи на ТО v2 (ZapisSTO.txt, триггер «австралия»).

Mixin для BotDialogMixin — методы _to_v2_* и _handle_service_data_collection_v2.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

from dialog.conversation_state import ClientNeed, ConversationState
from dialog.chery_tenet_stt_normalize import normalize_chery_tenet_car_stt
from dialog.department_stt_normalize import is_meaningless_voice_stt
from dialog.sto_to_price_inquiry import (
    disambiguation_prompt,
    format_price_announcement_tts,
    is_chery_tenet_brand,
    is_non_to_price_inquiry,
    is_to_booking_price_inquiry,
    is_to_v2_continue,
    is_to_v2_record_choice,
    is_price_transfer_confirm_stt,
    is_to_v2_service_assistant_request,
    is_service_human_transfer_request,
    parse_disambiguation_answer,
    resolve_to_price_from_service_data,
)
from dialog.service_speech_parse import parse_car_year_from_speech, parse_mileage_km_from_speech
from dialog.voice_to_booking_trace import summarize_service_data, voice_to_booking_trace
from services.voice.voice_phrases import (
    SERVICE_CHOICE,
    TO_V2_AFTER_PRICE_RESUME,
    TO_V2_ASK_CAR,
    TO_V2_ASK_DATETIME,
    TO_V2_ASK_NAME,
    TO_V2_ASK_PHONE,
    TO_V2_ASK_MILEAGE,
    TO_V2_ASK_YEAR,
    TO_V2_GATE5,
    TO_V2_GATE5_REMINDER,
    TO_V2_INTERRUPT_BOOKING,
    TO_V2_PRICE_INTRO,
    TO_V2_PRICE_NOT_FOUND,
    TO_V2_PRICE_UNAVAILABLE,
    MONTHS_TTS_GENITIVE,
)

logger = logging.getLogger(__name__)

_TO_V2_STEP_PROMPTS: dict[int, tuple[Optional[str], str]] = {
    1: ("25_to_v2_ask_name.wav", TO_V2_ASK_NAME),
    2: ("26_to_v2_ask_phone.wav", TO_V2_ASK_PHONE),
    3: ("27_to_v2_ask_car.wav", TO_V2_ASK_CAR),
    # Разделяем год и пробег, чтобы снизить STT-путаницу на комбинированной реплике.
    4: ("28a_to_v2_ask_year.wav", TO_V2_ASK_YEAR),
    6: ("30a_to_v2_ask_mileage.wav", TO_V2_ASK_MILEAGE),
    7: ("31_to_v2_ask_datetime.wav", TO_V2_ASK_DATETIME),
}


def _menu14_already_booked_phrase(text: str) -> bool:
    """Клиент сообщает о существующей записи, а не просит новую запись."""
    t = (text or "").lower()
    if not t.strip():
        return False
    return bool(
        re.search(
            r"\b(?:уже\s+)?запис(?:ан|ана|аны)\b|\bзаписал(?:ся|ась|ись)\b",
            t,
            re.IGNORECASE,
        )
    )


class ToBookingV2Mixin:
    """Методы сценария записи на ТО v2."""

    def _parse_month_only_request_date(self, text: str, reference_date: datetime) -> Optional[datetime]:
        """Распознаёт запрос только месяца: «в августе», «на сентябрь»."""
        if not self.date_parser:
            return None
        low = (text or "").lower()
        # Если день уже назван цифрами, это не month-only.
        if re.search(r"\b(?:[0-2]?\d|3[01])\b", low):
            return None
        month: Optional[int] = None
        month_match = self.date_parser.month_pattern.search(low)
        if month_match:
            month_name = month_match.group(1).lower()
            month = self.date_parser.MONTHS_GENITIVE.get(month_name) or self.date_parser.MONTHS_NOMINATIVE.get(
                month_name
            )
        if month is None:
            month_forms = {
                1: "январе",
                2: "феврале",
                3: "марте",
                4: "апреле",
                5: "мае",
                6: "июне",
                7: "июле",
                8: "августе",
                9: "сентябре",
                10: "октябре",
                11: "ноябре",
                12: "декабре",
            }
            for m_num, form in month_forms.items():
                if re.search(rf"\b{form}\b", low, re.IGNORECASE):
                    month = m_num
                    break
        if not month:
            return None
        year = reference_date.year
        if month < reference_date.month:
            year += 1
        return datetime(year, month, 1)

    def _init_to_v2_service_data(self) -> None:
        sd = self.state_machine.service_data
        sd["to_v2"] = True
        sd["to_v2_step"] = 1
        sd["to_v2_substate"] = None
        sd["gate5_played"] = False
        sd["resume_after_price_step"] = 6
        sd["price_block"] = {}
        sd["after_price_unclear"] = 0
        sd["date_confirm_attempts"] = 0
        sd["to_v2_step_empty_attempts"] = {}
        sd["price_requested"] = False
        sd["price_announced"] = False
        sd["phone_raw"] = None
        sd["car_attempts"] = 0
        sd["car_confirm_unclear"] = 0
        sd["car_confirmed"] = False
        sd["car_raw"] = None
        sd["car_await_model"] = False

    async def _to_v2_begin_data_collection(self) -> None:
        self._init_to_v2_service_data()
        self.state_machine.transition_to(ConversationState.SERVICE_DATA_COLLECTION)
        voice_to_booking_trace("to_v2_collection_start")
        await self._to_v2_prompt_step(1)

    async def _to_v2_prompt_step(self, step: int) -> None:
        wav, text = _TO_V2_STEP_PROMPTS[step]
        await self.play_wav_or_tts(wav, text)

    def _to_v2_service_data_model_name(self, sd: dict) -> Optional[str]:
        brand = (sd.get("car_brand") or "").strip()
        cm = (sd.get("car_model") or "").strip()
        if not cm:
            return None
        if brand and cm.upper().startswith(brand.upper()):
            tail = cm[len(brand) :].strip()
            return tail or None
        return cm if cm.upper() != brand.upper() else None

    def _to_v2_canonical_chery_tenet_brand(self, brand: Optional[str]) -> Optional[str]:
        """Латиница для MAX/1С: Tenet или Chery (T4/T4L бывают у обеих марок)."""
        if not brand:
            return None
        b = str(brand).strip().lower()
        if any(
            m in b
            for m in (
                "tenet",
                "тенет",
                "тэнет",
                "тенат",
                "тонат",
                "тэнт",
                "тинет",
                "тент",
            )
        ):
            return "Tenet"
        if any(m in b for m in ("chery", "чери", "черри", "cherry")):
            return "Chery"
        if is_chery_tenet_brand(brand):
            return "Tenet" if re.search(r"ten|тен|тэн|тнт|tnt", b) else "Chery"
        return str(brand).strip() or None

    def _to_v2_car_model_sufficient(self, brand: Optional[str], model: Optional[str]) -> bool:
        """Для Chery/Tenet нужна конкретная модель (Tiggo 4–9, T4/T4L–T9, Arrizo…), не только марка."""
        if not brand:
            return False
        if not is_chery_tenet_brand(brand):
            return bool(model or brand)
        blob = f"{brand} {model or ''}".upper()
        if re.search(r"TIGGO|ТИГГО", blob) and re.search(r"[4789]", blob):
            return True
        # T4 / T4L / T7… — без угадывания марки: марка уже известна из диалога.
        if re.search(r"\bT[4789]L?\b", blob):
            return True
        if "ARRIZO" in blob or "АРРИЗО" in blob:
            return True
        return False

    def _to_v2_store_car_extract(self, brand: str, model: Optional[str]) -> None:
        sd = self.state_machine.service_data
        canon_brand = self._to_v2_canonical_chery_tenet_brand(brand) or brand
        sd["car_brand"] = canon_brand
        canon_model = self._to_v2_canonical_model_label(model) if model else None
        sd["car_model"] = f"{canon_brand} {canon_model}".strip() if canon_model else canon_brand

    def _to_v2_parse_short_model_token(self, text: str) -> Optional[str]:
        """Короткий ответ на переспрос: T4 / T4L / T7… (без марки)."""
        compact = re.sub(r"[\s\-\.]+", "", (text or "").strip())
        m = re.fullmatch(r"[TtТт]([4789])([LlЛл])?", compact)
        if not m:
            return None
        return f"T{m.group(1)}" + ("L" if m.group(2) else "")

    def _to_v2_try_glue_model_to_awaited_brand(self, text: str) -> bool:
        """
        Марка уже названа (Tenet/Chery), клиент отвечает только моделью (T4L / семь…).
        Приклеиваем к сохранённой марке — T4/T4L есть и у Chery, и у Tenet.
        """
        sd = self.state_machine.service_data
        brand = self._to_v2_canonical_chery_tenet_brand(sd.get("car_brand"))
        if not brand or not is_chery_tenet_brand(brand):
            return False
        if self._to_v2_car_model_sufficient(brand, self._to_v2_service_data_model_name(sd)):
            return False
        raw = (text or "").strip()
        if not raw or is_meaningless_voice_stt(raw):
            return False

        # Сначала короткий токен (T4/T4L): STT иначе делает голый «T4» → Tenet.
        model = self._to_v2_canonical_model_label(self._to_v2_parse_short_model_token(raw))
        if not model:
            glued = self._to_v2_extract_car_with_common_normalizer(f"{brand} {raw}")
            if glued:
                _b, model = glued
                # Убрать ложный «TENET» внутри модели после склейки Chery+T4.
                if model:
                    model = re.sub(
                        r"^(?:TENET|ТЕНЕТ|ТЭНЕТ)\s+",
                        "",
                        str(model),
                        count=1,
                        flags=re.IGNORECASE,
                    ).strip()
                    model = self._to_v2_canonical_model_label(model)
        if model and self._to_v2_car_model_sufficient(brand, model):
            self._to_v2_store_car_extract(brand, model)
            sd["car_await_model"] = False
            return True
        return False

    def _to_v2_extract_car_with_common_normalizer(
        self,
        text: str,
        *,
        allow_incomplete: bool = False,
    ) -> Optional[tuple[str, Optional[str]]]:
        """
        Единый путь нормализации авто для voice/1C/MAX:
        сначала основной STT-нормализатор из аудио-контура, затем общий extractor.
        """
        raw = (text or "").strip()
        if not raw:
            return None
        normalized = normalize_chery_tenet_car_stt(raw)
        car_info = self.car_brand_extractor.extract_car_info(normalized)
        if not car_info:
            car_info = self.car_brand_extractor.extract_car_info(raw)
        if not car_info:
            return None
        if not isinstance(car_info, tuple) or not car_info:
            return None
        brand = str(car_info[0] or "").strip()
        model = str(car_info[1] or "").strip() if len(car_info) > 1 else None
        if not brand:
            return None
        brand = self._to_v2_canonical_chery_tenet_brand(brand) or brand
        if not allow_incomplete and not self._to_v2_car_model_sufficient(brand, model):
            return None
        return brand, self._to_v2_canonical_model_label(model)

    def _to_v2_canonical_model_label(self, model: Optional[str]) -> Optional[str]:
        """Приводит модель к единому виду для 1C/MAX (Tiggo/Arrizo/T4..T9/T4L)."""
        if not model:
            return None
        m = str(model).strip()
        up = m.upper()
        if re.fullmatch(r"T[4789]L?", up):
            return up
        replacements = (
            ("ТИГГО", "Tiggo"),
            ("TIGGO", "Tiggo"),
            ("АРРИЗО", "Arrizo"),
            ("ARRIZO", "Arrizo"),
            ("ПРО", "Pro"),
            ("PRO", "Pro"),
            ("МАКС", "Max"),
            ("MAX", "Max"),
            ("ПЛЮС", "Plus"),
            ("PLUS", "Plus"),
            (" ЭЛЬ", " L"),
            (" Л", " L"),
        )
        out = f" {up} "
        for old, new in replacements:
            out = out.replace(old, new)
        out = re.sub(r"\s+", " ", out).strip()
        return out

    def _to_v2_car_submission_values(self, sd: dict) -> tuple[str, str]:
        """Для 1С/лида: единый нормализатор авто + безопасный fallback по токенам."""
        brand = str(sd.get("car_brand") or "").strip()
        model = (self._to_v2_service_data_model_name(sd) or "").strip()
        # Марка+модель уже склеены в диалоге (Tenet/Chery + T4/T4L) — не пересобирать из raw,
        # иначе голый «T4» в car_raw снова станет Tenet.
        if (
            brand
            and model
            and is_chery_tenet_brand(brand)
            and self._to_v2_car_model_sufficient(brand, model)
        ):
            canon = self._to_v2_canonical_chery_tenet_brand(brand) or brand
            return canon, model
        raw = str(sd.get("car_raw") or "").strip()
        if raw:
            car_info = self._to_v2_extract_car_with_common_normalizer(raw)
            if car_info:
                return car_info
            tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9-]+", raw, flags=re.UNICODE)
            if tokens:
                if len(tokens) == 1:
                    return tokens[0], ""
                return tokens[0], " ".join(tokens[1:])
        return brand, str(sd.get("car_model") or "").strip()

    async def _to_v2_prompt_gate5(self) -> None:
        sd = self.state_machine.service_data
        sd["gate5_played"] = True
        sd["to_v2_step"] = 5
        sd["to_v2_substate"] = "gate5"
        sd["gate5_unclear"] = 0
        sd["gate5_empty"] = 0
        await self.play_wav_or_tts("29_to_v2_gate5.wav", TO_V2_GATE5)

    async def _to_v2_prompt_gate5_reminder(self) -> None:
        await self.play_wav_or_tts(None, TO_V2_GATE5_REMINDER)

    async def _to_v2_prompt_after_price(self) -> None:
        sd = self.state_machine.service_data
        sd["to_v2_substate"] = "after_price"
        sd["after_price_unclear"] = 0
        await self.play_wav_or_tts("32_to_v2_after_price_resume.wav", TO_V2_AFTER_PRICE_RESUME)

    async def _to_v2_transfer_service(self, *, reason: str) -> None:
        voice_to_booking_trace("to_v2_transfer", reason=reason)
        # Единственная точка решения о переводе: она сначала проверяет часы/календарь.
        # Нельзя проигрывать transfer-WAV заранее — CallSession запускает AMI Redirect
        # сразу после такого WAV, ещё до проверки нерабочего времени.
        await self._transfer_or_after_hours(
            "16_transfer_service_assistant.wav",
            ClientNeed.SERVICE,
        )

    async def _to_v2_interrupt_booking_transfer(self) -> None:
        await self.play_wav_or_tts("34_to_v2_interrupt_booking.wav", TO_V2_INTERRUPT_BOOKING)
        await self._to_v2_transfer_service(reason="gate5_other")

    async def _handle_need_identified_to_v2_menu14(self, text: str) -> bool:
        """
        Обработка ответа после меню 14.

        Явная просьба о человеке переводит на ассистента.
        Запуск самостоятельной записи — только по явным маркерам записи.
        Любой иной ответ считаем неясным.
        Возвращает True, если ответ обработан.
        """
        need = self.state_machine.identified_need
        if need != ClientNeed.SERVICE:
            return False

        if is_to_v2_service_assistant_request(text):
            self._log("NEED_IDENTIFIED v2: assistant marker -> 697777")
            await self._to_v2_transfer_service(reason="menu14_assistant")
            return True

        if is_to_booking_price_inquiry(text):
            self._log("NEED_IDENTIFIED v2: price inquiry on menu14 -> 697777")
            await self._to_v2_transfer_service(reason="menu14_price_inquiry")
            return True

        if _menu14_already_booked_phrase(text):
            self._log("NEED_IDENTIFIED v2: already booked phrase -> 697777")
            await self._to_v2_transfer_service(reason="menu14_already_booked")
            return True

        text_low = (text or "").lower()
        has_to_token = bool(
            re.search(
                r"\b(?:на\s+)?(?:то|техобслуж\w*|техническ\w+\s+обслуживан\w*)\b",
                text_low,
                re.I,
            )
        )
        to_consult_question = (
            has_to_token
            and not is_to_v2_record_choice(text)
            and not bool(re.search(r"\bзапис\w*|оформ\w*", text_low, re.I))
            and bool(
                re.search(
                    r"\?|(?:\bвы\s+дела\w*\b)|(?:\bдела\w*\b)|(?:\bесть\s+ли\b)|"
                    r"(?:\bзанима\w*сь\b)|(?:\bможно\b\s+(?!запис\w*))|"
                    r"(?:\bподскаж\w*\b)|(?:\bинтересу\w*\b)",
                    text_low,
                    re.I,
                )
            )
        )
        if to_consult_question:
            self._log("NEED_IDENTIFIED v2: TO consult question -> 697777")
            await self._to_v2_transfer_service(reason="menu14_assistant")
            return True

        booking_marker = is_to_v2_record_choice(text) or has_to_token
        if booking_marker:
            self._log("NEED_IDENTIFIED v2: explicit booking marker -> step 1")
            self.state_machine.menu14_booking_active = False
            voice_to_booking_trace("to_service_data_collection", trigger="menu14_explicit_booking")
            await self._to_v2_begin_data_collection()
            return True

        self.state_machine.menu14_unclear_attempts = int(
            getattr(self.state_machine, "menu14_unclear_attempts", 0)
        ) + 1
        # Продуктовое правило: меню 14 задаём один раз. Если ответа нет/ответ неясный —
        # переводим сразу после окна ожидания (вс: на администратора).
        from dialog.bot_logic import _is_sunday_samara_now

        if _is_sunday_samara_now():
            self._log("NEED_IDENTIFIED v2: menu14 unclear on sunday -> admin")
            await self.play_wav_or_tts(None, "Поняла, перевожу Вас на администратора.")
            await self._transfer_or_after_hours(
                "03_transfer_admin.wav",
                None,
                voice_admin_reason="client_request",
                skip_announcement=True,
            )
            return True
        self._log("NEED_IDENTIFIED v2: menu14 unclear -> 697777")
        await self._to_v2_transfer_service(reason="menu14_unclear")
        return True

    def _to_v2_empty_timeout(self) -> bool:
        kind = getattr(self, "_dialog_empty_kind", None)
        return kind in ("vad_no_audio", "stt_empty")

    async def _to_v2_maybe_retry_empty_step(self, step: int) -> bool:
        """Повтор вопроса на шагах 3–4 при пустом STT. True — переспрос, без advance."""
        if step not in (3, 4) or not self._to_v2_empty_timeout():
            return False
        sd = self.state_machine.service_data
        attempts = sd.setdefault("to_v2_step_empty_attempts", {})
        n = int(attempts.get(step, 0)) + 1
        attempts[step] = n
        if n < 2:
            voice_to_booking_trace("to_v2_step_empty_retry", step=step, attempt=n)
            await self._to_v2_prompt_step(step)
            return True
        return False

    def _to_v2_extract_step_data(self, step: int, text: str, t_low: str) -> None:
        sd = self.state_machine.service_data

        if step == 1:
            fio = self.name_extractor.extract_fio(text)
            if fio:
                name = (fio.get("name") or "").strip()
                surname = (fio.get("surname") or "").strip()
                parts = [p for p in (surname, name) if p]
                if parts:
                    sd["fio"] = " ".join(parts)
                    if name:
                        self.state_machine.set_name(name)
            elif text.strip() and not is_meaningless_voice_stt(text):
                sd["fio"] = text.strip()[:120]

        elif step == 2:
            raw_phone = (text or "").strip()
            if raw_phone and not is_meaningless_voice_stt(raw_phone):
                prev_raw = (sd.get("phone_raw") or "").strip()
                sd["phone_raw"] = (
                    f"{prev_raw} | {raw_phone}"
                    if prev_raw and raw_phone not in prev_raw
                    else (prev_raw or raw_phone)
                )[:255]
            phone = self.name_extractor.extract_phone(text)
            if phone:
                sd["phone"] = phone

        elif step == 3:
            raw_text = (text or "").strip()
            prev_raw = (sd.get("car_raw") or "").strip()
            if raw_text and not is_meaningless_voice_stt(text):
                # Храним последнюю реплику про авто: это и ожидают тесты, и
                # так проще не тянуть случайный «хвост» из предыдущих попыток.
                sd["car_raw"] = raw_text[:255]
            awaiting_model = bool(sd.get("car_await_model")) or (
                is_chery_tenet_brand(sd.get("car_brand"))
                and not self._to_v2_car_model_sufficient(
                    sd.get("car_brand"), self._to_v2_service_data_model_name(sd)
                )
            )
            if awaiting_model and raw_text:
                # Не вызываем extract(«T4») — STT/аналитика превратит голый T4 в Tenet.
                self._to_v2_try_glue_model_to_awaited_brand(raw_text)
            else:
                car_info = self._to_v2_extract_car_with_common_normalizer(text)
                if not car_info and prev_raw and raw_text:
                    # Короткий ответ на переспрос модели («L-7») разбираем вместе с
                    # предыдущей репликой, где обычно уже есть марка/линейка.
                    car_info = self._to_v2_extract_car_with_common_normalizer(
                        f"{prev_raw} {raw_text}"
                    )
                if car_info:
                    brand, model = car_info
                    self._to_v2_store_car_extract(brand, model)
                    sd["car_await_model"] = False

        elif step == 4:
            car_year = parse_car_year_from_speech(text, t_low) or sd.get("car_year")
            if car_year:
                sd["car_year"] = car_year
        elif step == 6:
            raw_mileage = (text or "").strip()
            mileage = parse_mileage_km_from_speech(
                text,
                t_low,
                car_year=sd.get("car_year"),
            )
            if mileage:
                sd["mileage"] = mileage
                # В raw только число км — не вся реплика STT (иначе MAX показывает фразу целиком).
                sd["mileage_raw"] = mileage
            elif raw_mileage and not is_meaningless_voice_stt(text):
                prev_raw_mileage = (sd.get("mileage_raw") or "").strip()
                sd["mileage_raw"] = (
                    f"{prev_raw_mileage} | {raw_mileage}"
                    if prev_raw_mileage and raw_mileage.lower() not in prev_raw_mileage.lower()
                    else (prev_raw_mileage or raw_mileage)
                )[:255]

        elif step == 5:
            works_found = bool(re.search(
                r"(\bто\d*\b|\bтехнич\w*\s+обслужив\w*\b|\bтехобслуж\w*\b|"
                r"\bколодк\w*\b|\bмасл\w*\b|\bдиагност\w*\b|\bремонт\w*\b)",
                t_low,
                flags=re.IGNORECASE,
            ))
            if works_found or (text.strip() and not is_meaningless_voice_stt(text)):
                sd["work_list"] = text.strip()[:500]
            elif self._to_v2_empty_timeout():
                sd["work_list"] = sd.get("work_list") or "операция не названа"
                sd["operation_unknown"] = True

    async def _to_v2_prompt_car_confirm(self) -> None:
        sd = self.state_machine.service_data
        car_info_tts = self._format_car_for_tts(sd)
        voice_to_booking_trace("to_v2_car_confirm_prompt", car_tts=car_info_tts)
        await self.play_wav_or_tts(None, f"У Вас {car_info_tts}, верно?")

    async def _to_v2_prompt_chery_tenet_model_clarify(self, brand: str) -> None:
        """Точечный переспрос модели для Chery/Tenet, если клиент назвал только марку."""
        brand_canon = self._to_v2_canonical_chery_tenet_brand(brand) or "Chery"
        brand_tts = "Tenet" if brand_canon == "Tenet" else "Chery"
        await self.play_wav_or_tts(
            None,
            f"Уточните, пожалуйста, модель {brand_tts}: например, "
            "T4, T4L, T7, T8, T9, Tiggo 4, Tiggo 7, Tiggo 8 или Arrizo 8.",
        )

    async def _to_v2_handle_step3_car(self, text: str, t_low: str) -> None:
        """Шаг 3: распознать модель и сразу продолжить без отдельного подтверждения."""
        sd = self.state_machine.service_data
        if is_to_v2_service_assistant_request(text):
            await self._to_v2_transfer_service(reason="step3_assistant")
            return
        if await self._to_v2_maybe_retry_empty_step(3):
            return
        if not self._to_v2_empty_timeout():
            self._to_v2_extract_step_data(3, text, t_low)

        brand = self._to_v2_canonical_chery_tenet_brand(sd.get("car_brand")) or sd.get("car_brand")
        model = self._to_v2_service_data_model_name(sd)
        if brand and self._to_v2_car_model_sufficient(brand, model):
            sd["car_brand"] = brand
            sd["car_await_model"] = False
            sd["to_v2_substate"] = None
            sd["car_confirmed"] = True
            sd["car_confirm_unclear"] = 0
            voice_to_booking_trace(
                "to_v2_car_recognized_without_confirm",
                brand=brand,
                model=model,
            )
            await self._to_v2_advance_linear(3)
            return

        # Только марка Chery/Tenet → сохраняем латиницу и ждём модель (T4/T4L у обеих марок).
        brand_only_name = ""
        if text.strip() and not is_meaningless_voice_stt(text):
            car_info_now = self._to_v2_extract_car_with_common_normalizer(
                text, allow_incomplete=True
            )
            if car_info_now:
                brand_now, model_now = car_info_now
                brand_now = self._to_v2_canonical_chery_tenet_brand(brand_now) or brand_now
                if is_chery_tenet_brand(brand_now) and not self._to_v2_car_model_sufficient(
                    brand_now, model_now
                ):
                    self._to_v2_store_car_extract(brand_now, None)
                    sd["car_await_model"] = True
                    brand_only_name = brand_now

        if brand_only_name or (
            sd.get("car_await_model")
            and is_chery_tenet_brand(sd.get("car_brand"))
            and not self._to_v2_car_model_sufficient(
                sd.get("car_brand"), self._to_v2_service_data_model_name(sd)
            )
        ):
            clarify_brand = brand_only_name or self._to_v2_canonical_chery_tenet_brand(
                sd.get("car_brand")
            )
            if brand_only_name:
                # Первый переспрос после марки — не сжигаем попытку как «не распознано».
                voice_to_booking_trace("to_v2_car_await_model", brand=clarify_brand)
                await self._to_v2_prompt_chery_tenet_model_clarify(clarify_brand or "")
                return
            attempts = int(sd.get("car_attempts") or 0) + 1
            sd["car_attempts"] = attempts
            voice_to_booking_trace(
                "to_v2_car_model_reply_not_recognized",
                attempt=attempts,
                brand=clarify_brand,
            )
            if attempts >= 2:
                sd["car_confirmed"] = True
                sd["car_await_model"] = False
                await self._to_v2_advance_linear(3)
                return
            await self._to_v2_prompt_chery_tenet_model_clarify(clarify_brand or "")
            return

        if brand and not self._to_v2_car_model_sufficient(brand, model):
            sd["car_brand"] = None
            sd["car_model"] = None
            sd["car_await_model"] = False
            voice_to_booking_trace("to_v2_car_brand_only_rejected", brand=brand)

        attempts = int(sd.get("car_attempts") or 0) + 1
        sd["car_attempts"] = attempts
        voice_to_booking_trace("to_v2_car_not_recognized", attempt=attempts)
        if attempts >= 2:
            sd["car_confirmed"] = True
            await self._to_v2_advance_linear(3)
            return
        await self._to_v2_prompt_step(3)

    async def _to_v2_reask_car_model(self) -> None:
        sd = self.state_machine.service_data
        sd["car_brand"] = None
        sd["car_model"] = None
        sd["car_confirmed"] = False
        sd["car_await_model"] = False
        sd["to_v2_substate"] = None
        await self._to_v2_prompt_step(3)

    async def _to_v2_handle_car_confirm(self, text: str, t_low: str) -> None:
        """Подтверждение модели после шага 3."""
        sd = self.state_machine.service_data
        if is_to_v2_service_assistant_request(text):
            await self._to_v2_transfer_service(reason="car_confirm_assistant")
            return
        if self._to_v2_empty_timeout():
            attempts = int(sd.get("car_confirm_empty") or 0) + 1
            sd["car_confirm_empty"] = attempts
            voice_to_booking_trace("to_v2_car_confirm_empty", attempt=attempts)
            if attempts >= 2:
                sd["car_confirmed"] = True
                sd["to_v2_substate"] = None
                sd["car_confirm_empty"] = 0
                voice_to_booking_trace("to_v2_car_confirm_empty_advance")
                await self._to_v2_advance_linear(3)
                return
            await self._to_v2_prompt_car_confirm()
            return

        if self._voice_affirmative(t_low):
            brand = sd.get("car_brand")
            model = self._to_v2_service_data_model_name(sd)
            if not self._to_v2_car_model_sufficient(brand, model):
                voice_to_booking_trace("to_v2_car_confirm_yes_but_no_model")
                await self._to_v2_reask_car_model()
                return
            sd["car_confirmed"] = True
            sd["to_v2_substate"] = None
            sd["car_confirm_unclear"] = 0
            sd["car_confirm_empty"] = 0
            voice_to_booking_trace("to_v2_car_confirm_yes")
            await self._to_v2_advance_linear(3)
            return

        neg_words = self._NEGATIVE_WORDS()
        if any(w in t_low for w in neg_words):
            car_info = self._to_v2_extract_car_with_common_normalizer(text)
            if car_info:
                brand, model = car_info
                if self._to_v2_car_model_sufficient(brand, model):
                    self._to_v2_store_car_extract(brand, model)
                    sd["car_confirmed"] = False
                    sd["car_confirm_unclear"] = 0
                    voice_to_booking_trace("to_v2_car_confirm_corrected_on_no")
                    await self._to_v2_prompt_car_confirm()
                    return
            await self._to_v2_reask_car_model()
            voice_to_booking_trace("to_v2_car_confirm_no_reask")
            return

        cleaned_text = text
        for word in ("это", "у меня", "автомобиль", "машина", "авто"):
            cleaned_text = re.sub(rf"\b{word}\b", "", cleaned_text, flags=re.IGNORECASE).strip()
        car_info = self._to_v2_extract_car_with_common_normalizer(cleaned_text)
        if car_info:
            brand, model = car_info
            if self._to_v2_car_model_sufficient(brand, model):
                self._to_v2_store_car_extract(brand, model)
                sd["car_confirmed"] = False
                sd["car_confirm_unclear"] = 0
                voice_to_booking_trace("to_v2_car_confirm_reparsed")
                await self._to_v2_prompt_car_confirm()
                return

        attempts = int(sd.get("car_confirm_unclear") or 0) + 1
        sd["car_confirm_unclear"] = attempts
        voice_to_booking_trace("to_v2_car_confirm_unclear", attempt=attempts)
        if attempts >= 2:
            sd["car_confirmed"] = True
            sd["to_v2_substate"] = None
            await self._to_v2_advance_linear(3)
            return
        await self._to_v2_prompt_car_confirm()

    async def _to_v2_advance_linear(self, from_step: int) -> None:
        sd = self.state_machine.service_data
        # Для явной записи на ТО вид работ уже известен: после шага пробега
        # пропускаем gate5 и дополнительный вопрос о работах, сразу просим дату.
        if from_step == 6:
            sd["work_list"] = "ТО"
            sd["operation_unknown"] = False
        next_step = {1: 2, 2: 3, 3: 4, 4: 6, 6: 7}.get(from_step)
        if next_step is None:
            return
        sd["to_v2_step"] = next_step
        sd["to_v2_substate"] = None
        await self._to_v2_prompt_step(next_step)

    async def _to_v2_maybe_start_price_block(self, current_step: int) -> bool:
        """True если запущен блок D."""
        sd = self.state_machine.service_data
        sd["price_requested"] = True
        sd["resume_after_price_step"] = current_step + 1
        if current_step in (4, 5, 6, 7):
            sd["resume_after_price_step"] = 7
        sd["to_v2_substate"] = "price"
        sd["price_block"] = {"phase": "intro", "disambig_attempts": 0}
        await self.play_wav_or_tts("33_to_v2_price_intro.wav", TO_V2_PRICE_INTRO)
        return await self._to_v2_price_block_continue("")

    async def _to_v2_prompt_price_not_found(self) -> None:
        sd = self.state_machine.service_data
        if sd.get("price_post_booking"):
            await self.play_wav_or_tts(
                None,
                "Не удалось назвать ориентировочную стоимость ТО по регламенту. "
                "Могу перевести Вас на ассистента сервиса — перевести?",
            )
            sd["to_v2_substate"] = "price_offer_transfer"
            sd["price_post_booking"] = False
            sd["price_transfer_unclear"] = 0
            return
        sd["to_v2_substate"] = "after_price"
        sd["after_price_unclear"] = 0
        await self.play_wav_or_tts("41_to_v2_price_not_found.wav", TO_V2_PRICE_NOT_FOUND)

    async def _to_v2_play_disambiguation(self, field: str, *, repeat: bool = False) -> None:
        wav, text = disambiguation_prompt(field, repeat=repeat)
        await self.play_wav_or_tts(wav, text)

    async def _to_v2_price_block_continue(self, text: str) -> bool:
        sd = self.state_machine.service_data
        pb = sd.setdefault("price_block", {})
        brand = sd.get("car_brand") or ""
        model = sd.get("car_model") or ""

        if pb.get("phase") == "intro":
            if not is_chery_tenet_brand(brand):
                await self.play_wav_or_tts(None, TO_V2_PRICE_UNAVAILABLE)
                return await self._to_v2_finish_price_block()
            pb["phase"] = "resolve"

        if pb.get("phase") == "disambig":
            field = pb.get("pending_field")
            if field:
                answer = parse_disambiguation_answer(text, field)
                voice_to_booking_trace(
                    "to_v2_price_disambiguation",
                    field=field,
                    answer_text=(text or "").strip()[:40],
                    parsed=answer,
                )
                if answer is None:
                    attempts = int(pb.get("disambig_attempts") or 0) + 1
                    pb["disambig_attempts"] = attempts
                    if attempts >= 2:
                        return await self._to_v2_prompt_price_not_found()
                    await self._to_v2_play_disambiguation(field, repeat=True)
                    return True
                pb[field] = answer
                sd["price_block"] = pb
                pb["phase"] = "resolve"
                pb["disambig_attempts"] = 0

        if pb.get("phase") in ("resolve", "intro"):
            merged = {**pb, **sd.get("price_block", {})}
            sd["price_block"] = merged
            result, next_field = resolve_to_price_from_service_data(sd)
            voice_to_booking_trace(
                "to_v2_price_resolve",
                next_field=next_field,
                found=bool(result),
                transmission=merged.get("transmission"),
                volume_l=merged.get("volume_l"),
                drive=merged.get("drive"),
                price_rub=(result or {}).get("price_rub"),
                to_label=(result or {}).get("to_label"),
            )
            if next_field:
                pb["phase"] = "disambig"
                pb["pending_field"] = next_field
                pb["disambig_attempts"] = 0
                sd["price_block"] = pb
                await self._to_v2_play_disambiguation(next_field, repeat=False)
                return True
            if result:
                sd.update(result)
                await self.play_wav_or_tts(
                    None,
                    format_price_announcement_tts(result["to_label"], result["price_rub"]),
                )
                return await self._to_v2_finish_price_block()
            return await self._to_v2_prompt_price_not_found()

        return True

    async def _to_v2_finish_price_block(self) -> bool:
        sd = self.state_machine.service_data
        if sd.get("price_post_booking"):
            sd["to_v2_substate"] = None
            sd["price_post_booking"] = False
            await self._service_booked_prompt_more_questions()
            return True
        await self._to_v2_prompt_after_price()
        return True

    async def _service_booked_prompt_more_questions(self) -> None:
        await self.play_wav_or_tts(None, "Спасибо, есть ли у Вас еще вопросы?")

    async def _handle_service_booked_price_inquiry(self, text: str) -> bool:
        """Цена ТО после успешной записи (блок D), без перевода на ассистента."""
        sd = self.state_machine.service_data
        sd["price_post_booking"] = True
        sd["price_requested"] = True
        if sd.get("to_v2_substate") != "price":
            sd["to_v2_substate"] = "price"
            sd["price_block"] = {"phase": "resolve", "disambig_attempts": 0}
        await self._to_v2_price_block_continue(text)
        return True

    async def _handle_service_booked_price_transfer_offer(self, text: str) -> None:
        """После «цена не найдена» — перевод по согласию; после 2 неясных ответов — автоперевод."""
        sd = self.state_machine.service_data
        t_low = (text or "").lower()
        reject_words = ("нет", "не надо", "не нужно", "не переводи", "оставьте", "не хочу")
        if is_price_transfer_confirm_stt(text):
            sd["price_transfer_unclear"] = 0
            await self._to_v2_transfer_service(reason="post_booking_price_assistant_ok")
            return
        if any(w in t_low for w in reject_words):
            sd["to_v2_substate"] = None
            sd["price_transfer_unclear"] = 0
            await self._service_booked_prompt_more_questions()
            return
        attempts = int(sd.get("price_transfer_unclear") or 0) + 1
        sd["price_transfer_unclear"] = attempts
        if attempts >= 2:
            await self._to_v2_transfer_service(reason="post_booking_price_transfer_unclear")
            return
        await self.play_wav_or_tts(
            None,
            "Перевести на ассистента сервиса? Скажите да или нет.",
        )

    async def _to_v2_resume_after_price(self, text: str, t_low: str) -> bool:
        sd = self.state_machine.service_data
        if is_to_v2_service_assistant_request(text):
            await self._to_v2_transfer_service(reason="after_price_assistant")
            return True
        if is_to_v2_continue(text):
            step = int(sd.get("resume_after_price_step") or 6)
            sd["to_v2_step"] = step
            sd["after_price_unclear"] = 0
            if step == 8:
                sd["to_v2_substate"] = None
                await self._to_v2_reoffer_slot()
                return True
            sd["to_v2_substate"] = None
            if step == 6:
                await self._to_v2_prompt_step(6)
            elif step == 7:
                await self._to_v2_prompt_step(7)
            else:
                await self._to_v2_prompt_step(step)
            return True
        attempts = int(sd.get("after_price_unclear") or 0) + 1
        sd["after_price_unclear"] = attempts
        if attempts >= 2:
            await self._to_v2_transfer_service(reason="after_price_unclear")
            return True
        await self.play_wav_or_tts("32_to_v2_after_price_resume.wav", TO_V2_AFTER_PRICE_RESUME)
        return True

    async def _to_v2_handle_gate5(self, text: str, t_low: str) -> bool:
        from dialog.sto_to_price_inquiry import is_to_v2_gate5_unclear

        sd = self.state_machine.service_data
        if is_to_v2_service_assistant_request(text):
            await self._to_v2_transfer_service(reason="gate5_assistant")
            return True
        if is_to_booking_price_inquiry(text):
            sd["resume_after_price_step"] = 6
            return await self._to_v2_maybe_start_price_block(5)
        if is_to_v2_continue(text) or self._voice_affirmative(t_low):
            sd["to_v2_step"] = 6
            sd["to_v2_substate"] = None
            await self._to_v2_prompt_step(6)
            return True
        if is_to_v2_gate5_unclear(text):
            sd["gate5_unclear"] = int(sd.get("gate5_unclear") or 0) + 1
            await self._to_v2_prompt_gate5_reminder()
            return True
        if self._to_v2_empty_timeout():
            sd["gate5_empty"] = int(sd.get("gate5_empty") or 0) + 1
            await self._to_v2_prompt_gate5_reminder()
            return True
        if is_non_to_price_inquiry(text):
            await self._to_v2_interrupt_booking_transfer()
            return True
        if text.strip() and not is_meaningless_voice_stt(text):
            await self._to_v2_interrupt_booking_transfer()
            return True
        await self._to_v2_prompt_gate5_reminder()
        return True

    async def _to_v2_handle_step7(self, text: str, t_low: str) -> bool:
        sd = self.state_machine.service_data

        if is_to_booking_price_inquiry(text):
            return await self._to_v2_maybe_start_price_block(7)

        from dialog.sto_to_price_inquiry import is_service_human_transfer_request

        if is_service_human_transfer_request(text):
            await self._transfer_service_assistant_slot_selection(reason="step7_assistant")
            return True

        from dialog.bot_logic import _is_step7_wait_request

        wait_requested = _is_step7_wait_request(text)

        if self.date_parser:
            from dialog.service_speech_parse import prepare_booking_date_stt

            norm_text, d9, d10 = prepare_booking_date_stt(text, sd)
            from dialog.dealer_time import dealer_local_now_naive

            past_date = self.date_parser.parse_explicit_past_date(
                norm_text,
                dealer_local_now_naive(),
            )
            if past_date:
                sd["desired_date"] = None
                sd["desired_time"] = None
                if int(sd.get("date_time_attempts") or 0) >= 1:
                    await self.play_wav_or_tts(
                        None,
                        "Эта дата уже прошла. Предлагаю ближайший свободный слот.",
                    )
                    await self._offer_nearest_slot_after_date_silence(
                        after_unrecognized_date=True,
                    )
                    return True
                sd["date_time_attempts"] = 0
                await self.play_wav_or_tts(
                    None,
                    "Эта дата уже прошла. Назовите, пожалуйста, другую дату и время.",
                )
                return True
            new_date = self.date_parser.parse_date(
                norm_text,
                slot_day9_hint=d9,
                slot_day10_hint=d10,
            )
            if new_date:
                sd["desired_date"] = new_date.strftime("%Y-%m-%d")
                sd["date_time_attempts"] = 0
            else:
                from dialog.dealer_time import dealer_local_now_naive

                month_only = self._parse_month_only_request_date(
                    norm_text,
                    dealer_local_now_naive(),
                )
                if month_only and hasattr(self, "_offer_nearest_slot_in_month"):
                    await getattr(self, "_offer_nearest_slot_in_month")(month_only.year, month_only.month)
                    return True

        from dialog.bot_logic import (
            _is_step7_slot_availability_request,
            _is_within_week_request,
            _set_asap_desired_date,
        )

        if _is_within_week_request(t_low):
            sd["within_week_slot"] = True
            sd["until_week_end_slot"] = False
            sd["asap_slot"] = False
            sd["date_time_confirmed"] = True
            await self._process_service_booking()
            return True

        if _is_step7_slot_availability_request(text, t_low) and not sd.get("desired_date"):
            _set_asap_desired_date(sd)

        day_part = self._extract_day_part(text)
        if day_part:
            self.service_day_part = day_part

        time_match = re.search(r"(\d{1,2})\s*[:\.\-]\s*(\d{2})", t_low)
        if time_match:
            h, m = int(time_match.group(1)), int(time_match.group(2))
            if 0 <= h <= 23 and 0 <= m <= 59:
                sd["desired_time"] = f"{h:02d}:{m:02d}"

        if sd.get("desired_date"):
            sd["to_v2_substate"] = None
            await self._process_service_booking()
            return True

        if wait_requested:
            await self._offer_nearest_slot_after_date_silence()
            return True

        if self._to_v2_empty_timeout():
            kind = getattr(self, "_dialog_empty_kind", None)
            # После первого «не удалось распознать дату…» следующий пустой/непонятный
            # ответ не дублируем вторым «не удалось», а сразу предлагаем ближайший слот.
            if int(sd.get("date_time_attempts") or 0) >= 1:
                await self._offer_nearest_slot_after_date_silence(
                    after_unrecognized_date=True,
                )
                return True
            # Речь была, STT пустой — один мягкий переспрос; тишина — сразу ближайший слот.
            if kind == "stt_empty" and int(sd.get("date_stt_soft_reask") or 0) < 1:
                sd["date_stt_soft_reask"] = 1
                await self.play_wav_or_tts(
                    None,
                    "Не расслышала, повторите дату и время, пожалуйста.",
                )
                return True
            await self._offer_nearest_slot_after_date_silence()
            return True

        attempts = int(sd.get("date_time_attempts") or 0) + 1
        sd["date_time_attempts"] = attempts
        if attempts > 1:
            await self._offer_nearest_slot_after_date_silence(
                after_unrecognized_date=True,
            )
            return True
        await self.play_wav_or_tts(
            None,
            "Не удалось распознать дату. Повторите, пожалуйста, желаемую дату "
            "и время, например: тридцатое августа в девять часов.",
        )
        return True

    def _NEGATIVE_WORDS(self) -> list[str]:
        from dialog.bot_logic import NEGATIVE_WORDS
        return NEGATIVE_WORDS

    def _voice_affirmative(self, t_low: str) -> bool:
        from dialog.bot_logic import _voice_affirmative_stt
        return _voice_affirmative_stt(t_low)

    async def _to_v2_say_date_confirm(self) -> None:
        from dialog.bot_logic import day_to_ordinal_ru
        sd = self.state_machine.service_data
        dt = datetime.strptime(sd["desired_date"], "%Y-%m-%d")
        day_txt = day_to_ordinal_ru(dt.day)
        await self.play_wav_or_tts(
            None,
            f"Желаемая дата: {day_txt} {MONTHS_TTS_GENITIVE[dt.month]}. Всё верно?",
        )

    async def _to_v2_reoffer_slot(self) -> None:
        """Повтор предложения слота после блока D на этапе выбора времени."""
        sd = self.state_machine.service_data
        if sd.get("proposed_date") and sd.get("proposed_time"):
            from dialog.bot_logic import _slot_time_hhmm_to_tts, day_to_ordinal_ru
            from services.voice.voice_phrases import format_service_slot_offer_tts
            dt = datetime.strptime(sd["proposed_date"], "%Y-%m-%d")
            day_txt = day_to_ordinal_ru(dt.day)
            await self.play_wav_or_tts(
                None,
                format_service_slot_offer_tts(
                    day_txt,
                    MONTHS_TTS_GENITIVE[dt.month],
                    _slot_time_hhmm_to_tts(sd["proposed_time"]),
                    slot_hhmm=sd["proposed_time"],
                ),
            )

    async def _handle_service_slot_selection_v2(self, text: str) -> bool:
        """True если реплика обработана v2-логикой."""
        sd = self.state_machine.service_data
        if not sd.get("to_v2"):
            return False
        t_low = (text or "").lower()
        sub = sd.get("to_v2_substate")
        if sub == "price":
            await self._to_v2_price_block_continue(text)
            return True
        if sub == "after_price":
            if is_to_v2_continue(text):
                sd["to_v2_substate"] = None
                await self._to_v2_reoffer_slot()
                return True
            if is_service_human_transfer_request(text):
                await self._transfer_service_assistant_slot_selection(
                    reason="slot_after_price_assistant",
                )
                return True
            await self._to_v2_resume_after_price(text, t_low)
            return True
        if is_to_booking_price_inquiry(text):
            sd["resume_after_price_step"] = 8
            await self._to_v2_maybe_start_price_block(7)
            return True
        if is_service_human_transfer_request(text):
            await self._transfer_service_assistant_slot_selection(reason="slot_assistant")
            return True
        return False

    async def _handle_service_data_collection_v2(self, text: str) -> None:
        sd = self.state_machine.service_data
        t_low = (text or "").lower()
        caller_ph = getattr(self, "caller_phone", None)
        voice_to_booking_trace(
            "to_v2_turn",
            step=sd.get("to_v2_step"),
            substate=sd.get("to_v2_substate"),
            **summarize_service_data(sd, caller_phone=caller_ph),
        )

        substate = sd.get("to_v2_substate")
        if is_service_human_transfer_request(text):
            await self._to_v2_transfer_service(
                reason=f"collection_interrupt_assistant_{substate or ('step' + str(sd.get('to_v2_step') or 1))}"
            )
            return

        if substate == "price":
            if is_to_v2_service_assistant_request(text):
                await self._to_v2_transfer_service(reason="price_block_assistant")
                return
            await self._to_v2_price_block_continue(text)
            return

        if substate == "after_price":
            await self._to_v2_resume_after_price(text, t_low)
            return

        if substate == "car_confirm":
            await self._to_v2_handle_car_confirm(text, t_low)
            return

        step = int(sd.get("to_v2_step") or 1)

        if step == 3:
            await self._to_v2_handle_step3_car(text, t_low)
            return

        if step in (1, 2, 4, 6):
            if is_to_booking_price_inquiry(text) and step >= 4:
                await self._to_v2_maybe_start_price_block(step)
                return
            if is_to_v2_service_assistant_request(text):
                await self._to_v2_transfer_service(reason=f"step{step}_assistant")
                return
            if await self._to_v2_maybe_retry_empty_step(step):
                return
            if not self._to_v2_empty_timeout():
                self._to_v2_extract_step_data(step, text, t_low)
            await self._to_v2_advance_linear(step)
            return

        if step == 5 or substate == "gate5":
            await self._to_v2_handle_gate5(text, t_low)
            return

        if step == 7:
            await self._to_v2_handle_step7(text, t_low)
            return

        await self._to_v2_prompt_step(1)
