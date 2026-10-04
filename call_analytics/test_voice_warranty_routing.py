"""Маршрутизация «специалист по …»: не админ, кузовной или ассистент сервиса."""
import unittest
from unittest.mock import AsyncMock, patch

from dialog.bot_logic import (
    BotDialogMixin,
    _hits_voice_admin_marker,
    _stt_implies_body_shop_specialist,
    _stt_implies_service_specialist,
    _stt_implies_to_booking_request,
    _stt_implies_warranty_service,
)
from dialog.conversation_state import ClientNeed, ConversationStateMachine
from dialog.department_stt_normalize import normalize_department_stt_text

_ADMIN_MARKERS = (
    "администратор",
    "специалист",
    "оператор",
)


class _RoutingBot(BotDialogMixin):
    def __init__(self) -> None:
        self.state_machine = ConversationStateMachine("test-warranty-routing")
        self.voice_scenario = "v2"
        self.play_wav_or_tts = AsyncMock()
        self._transfer_or_after_hours = AsyncMock()
        self._open_service_booking_choice = AsyncMock()

    def _is_non_working_hours(self) -> bool:
        return False

    def _log(self, *_args) -> None:
        return None


class TestVoiceSpecialistRouting(unittest.TestCase):
    def test_explicit_to_booking_phrases(self):
        for raw in (
            "хочу записаться на ТО",
            "на ТО записаться",
            "запишите на техобслуживание",
            "на техническое обслуживание записаться",
            "хочу пройти ТО",
            "ТО пройти",
            "техобслуживание пройти",
            "техническоее обслуживание пройти",
            "ТО Сервис",
            "сервис ТО",
            "то на сервис",
            "техническое обслуживание",
            "техобслуживание",
            "Техническое обслуживание",
            "Техническое, техническое обслуживание",
            "хочу техническое обслуживание автомобиля",
            "нужно пройти плановое техническое обслуживание",
            "плановое техническое обслуживание",
            "обслуживание техническое",
            "записатьсянато",
            "пройти",
            "П пройти",
            "ппройти",
            "го сделать",
            "го пройти",
            "мне нужно сделать ТО на Чере",
            "хочу узнать про ТО для автомобиля",
        ):
            with self.subTest(raw=raw):
                self.assertTrue(_stt_implies_to_booking_request(raw), raw)

    def test_technical_service_department_context_is_not_auto_booking(self):
        for raw in (
            "отдел технического обслуживания",
            "с отделом технического обслуживания",
            "по техническому обслуживанию",
            "с техническим обслуживанием",
            "соедините с техническим отделом",
        ):
            with self.subTest(raw=raw):
                self.assertFalse(_stt_implies_to_booking_request(raw), raw)

    def test_go_sdelat_proyti_stt_normalizes_to_service(self):
        for raw in ("го сделать", "го пройти"):
            with self.subTest(raw=raw):
                normalized = normalize_department_stt_text(raw).lower()
                self.assertEqual(normalized, "техобслуживание")
                self.assertTrue(_stt_implies_to_booking_request(normalized))

    def test_service_requests_without_to_booking_intent(self):
        for raw in (
            "с отделом ремонта",
            "по ремонту",
            "ремонт",
            "ассистент сервиса",
            "диспетчер сервиса",
            "мастер",
            "приёмщик",
            "нужна диагностика",
            "сервис",
        ):
            with self.subTest(raw=raw):
                self.assertFalse(_stt_implies_to_booking_request(raw), raw)

    def test_warranty_department_phrases(self):
        for raw in (
            "отдел гарантий",
            "отдел гарантии",
            "гарантия",
            "гарантии",
            "гарантин",
            "карантин",
        ):
            with self.subTest(raw=raw):
                normalized = normalize_department_stt_text(raw).lower()
                self.assertTrue(_stt_implies_warranty_service(raw.lower()), raw)
                self.assertTrue(_stt_implies_warranty_service(normalized), raw)

    def test_warranty_specialist_not_admin(self):
        raw = "с специалистом по гарантийному ремонту"
        raw_l = raw.lower()
        t = normalize_department_stt_text(raw).lower()
        self.assertTrue(_stt_implies_warranty_service(t))
        self.assertTrue(_stt_implies_service_specialist(t, raw_l))
        self.assertFalse(_hits_voice_admin_marker(t, _ADMIN_MARKERS, raw_l))

    def test_bare_specialist_is_admin(self):
        t = "нужен специалист"
        self.assertTrue(_hits_voice_admin_marker(t, _ADMIN_MARKERS, t))

    def test_paint_specialist_body_shop(self):
        for raw in (
            "со специалистом по окраске",
            "со специалистом по рихтовке",
            "со специалистом по кузовному ремонту",
        ):
            raw_l = raw.lower()
            t = normalize_department_stt_text(raw).lower()
            with self.subTest(raw=raw):
                self.assertTrue(_stt_implies_body_shop_specialist(t, raw_l), raw)
                self.assertFalse(_stt_implies_service_specialist(t, raw_l), raw)
                self.assertFalse(_hits_voice_admin_marker(t, _ADMIN_MARKERS, raw_l), raw)

    def test_repair_specialist_service(self):
        raw = "со специалистом по ремонту"
        raw_l = raw.lower()
        t = normalize_department_stt_text(raw).lower()
        self.assertTrue(_stt_implies_service_specialist(t, raw_l))
        self.assertFalse(_stt_implies_body_shop_specialist(t, raw_l))
        self.assertFalse(_hits_voice_admin_marker(t, _ADMIN_MARKERS, raw_l))


class TestWarrantyDepartmentTransfer(unittest.IsolatedAsyncioTestCase):
    @patch("dialog.bot_logic.VOICE_TO_BOOKING_PROD_ENABLED", True)
    async def test_all_supported_to_phrases_from_second_menu_enter_booking_cycle(self):
        """После WAV 22 все поддерживаемые формулировки ТО открывают выбор бот/ассистент."""
        for raw in (
            "запись на ТО",
            "хочу записаться на ТО",
            "на ТО записаться",
            "запишите на техобслуживание",
            "на техническое обслуживание записаться",
            "хочу пройти ТО",
            "ТО пройти",
            "техобслуживание пройти",
            "техническое обслуживание",
            "техобслуживание",
            "Техническое, техническое обслуживание",
            "хочу техническое обслуживание автомобиля",
            "нужно пройти плановое техническое обслуживание",
            "обслуживание техническое",
            "ТО Сервис",
            "сервис ТО",
            "то на сервис",
            "записатьсянато",
            "пройти",
            "П пройти",
            "ппройти",
            "го сделать",
            "го пройти",
        ):
            with self.subTest(raw=raw):
                bot = _RoutingBot()
                normalized = normalize_department_stt_text(raw)
                handled = await bot._try_department_phase_from_client_speech(
                    raw,
                    normalized,
                )
                self.assertTrue(handled)
                bot._open_service_booking_choice.assert_awaited_once()
                bot._transfer_or_after_hours.assert_not_awaited()

    @patch("dialog.bot_logic.VOICE_TO_BOOKING_PROD_ENABLED", True)
    async def test_warranty_goes_directly_to_service_assistant(self):
        for raw in (
            "отдел гарантий",
            "отдел гарантии",
            "гарантия",
            "гарантии",
            "гарантин",
            "карантином",
        ):
            with self.subTest(raw=raw):
                bot = _RoutingBot()
                await bot._department_phase_transfer_for_need(
                    ClientNeed.SERVICE,
                    raw_client_text=raw,
                )
                bot._open_service_booking_choice.assert_not_awaited()
                bot.play_wav_or_tts.assert_awaited_once_with(
                    None,
                    "Поняла, перевожу Вас на ассистента сервиса.",
                )
                bot._transfer_or_after_hours.assert_awaited_once_with(
                    "16_transfer_service_assistant.wav",
                    ClientNeed.SERVICE,
                    skip_announcement=True,
                )

    @patch("dialog.bot_logic.VOICE_TO_BOOKING_PROD_ENABLED", True)
    async def test_only_explicit_to_booking_opens_booking_choice(self):
        for raw in (
            "хочу записаться на ТО",
            "на техническое обслуживание записаться",
            "ТО пройти",
            "техобслуживание пройти",
            "ТО Сервис",
            "ТО.",
            "Тэо.",
            "Тео.",
            "техническое обслуживание",
            "техобслуживание",
            "Мне нужно сделать ТО на Чере.",
        ):
            with self.subTest(raw=raw):
                bot = _RoutingBot()
                await bot._department_phase_transfer_for_need(
                    ClientNeed.SERVICE,
                    raw_client_text=raw,
                )
                bot._open_service_booking_choice.assert_awaited_once()
                bot._transfer_or_after_hours.assert_not_awaited()

    @patch("dialog.bot_logic.VOICE_TO_BOOKING_PROD_ENABLED", True)
    async def test_standalone_maintenance_phrase_enters_to_booking(self):
        for raw in (
            "Техническое обслуживание",
            "техобслуживание",
            "ТО.",
            "Тэо.",
            "Тео.",
        ):
            with self.subTest(raw=raw):
                bot = _RoutingBot()
                handled = await bot._try_department_phase_from_client_speech(
                    raw,
                    raw.lower(),
                )
                self.assertTrue(handled)
                bot._open_service_booking_choice.assert_awaited_once()
                bot._transfer_or_after_hours.assert_not_awaited()

    @patch("dialog.bot_logic.VOICE_TO_BOOKING_PROD_ENABLED", True)
    async def test_to_service_phrase_enters_to_booking(self):
        for raw in ("ТО Сервис", "сервис ТО", "то на сервис"):
            with self.subTest(raw=raw):
                bot = _RoutingBot()
                handled = await bot._try_department_phase_from_client_speech(
                    raw,
                    raw.lower(),
                )
                self.assertTrue(handled)
                bot._open_service_booking_choice.assert_awaited_once()
                bot._transfer_or_after_hours.assert_not_awaited()

    @patch("dialog.bot_logic.VOICE_TO_BOOKING_PROD_ENABLED", True)
    async def test_short_proyti_stt_enters_to_booking(self):
        for raw in ("пройти", "П пройти", "ппройти"):
            with self.subTest(raw=raw):
                bot = _RoutingBot()
                handled = await bot._try_department_phase_from_client_speech(
                    raw,
                    raw.lower(),
                )
                self.assertTrue(handled)
                bot._open_service_booking_choice.assert_awaited_once()
                bot._transfer_or_after_hours.assert_not_awaited()

    @patch("dialog.bot_logic.VOICE_TO_BOOKING_PROD_ENABLED", True)
    async def test_other_service_requests_go_directly_to_assistant(self):
        for raw in (
            "с отделом ремонта",
            "по ремонту",
            "ремонт",
            "ассистент сервиса",
            "диспетчер сервиса",
            "мастер",
            "приёмщик",
            "нужна диагностика",
        ):
            with self.subTest(raw=raw):
                bot = _RoutingBot()
                await bot._department_phase_transfer_for_need(
                    ClientNeed.SERVICE,
                    raw_client_text=raw,
                )
                bot._open_service_booking_choice.assert_not_awaited()
                bot._transfer_or_after_hours.assert_awaited_once_with(
                    "16_transfer_service_assistant.wav",
                    ClientNeed.SERVICE,
                    skip_announcement=True,
                )

    @patch("dialog.bot_logic.VOICE_TO_BOOKING_PROD_ENABLED", True)
    async def test_technical_department_forms_go_directly_to_assistant(self):
        for raw in (
            "технический отдел",
            "с техническим отделом",
            "техотдел",
            "с техотделом",
            "отдел технического обслуживания",
            "с отделом технического обслуживания",
            "по техническому обслуживанию",
            "с техническим обслуживанием",
        ):
            with self.subTest(raw=raw):
                bot = _RoutingBot()
                handled = await bot._try_department_phase_from_client_speech(
                    raw,
                    raw.lower(),
                )
                self.assertTrue(handled)
                bot._open_service_booking_choice.assert_not_awaited()
                bot._transfer_or_after_hours.assert_awaited_once_with(
                    "16_transfer_service_assistant.wav",
                    ClientNeed.SERVICE,
                    skip_announcement=True,
                )


if __name__ == "__main__":
    unittest.main()
