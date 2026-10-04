"""Тесты gate5 в сценарии ТО v2."""

import unittest
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

from dialog.bot_logic import BotDialogMixin, _stt_implies_to_booking_request
from dialog.conversation_state import ClientNeed, ConversationState, ConversationStateMachine
from dialog.date_parser import DateParser
from dialog.department_stt_normalize import normalize_department_stt_text
from dialog.service_slot_day_part import extract_day_part
from dialog.service_slot_pick import PickedSlot
from dialog.to_booking_v2 import ToBookingV2Mixin


class _Bot(ToBookingV2Mixin):
    def __init__(self) -> None:
        self.state_machine = ConversationStateMachine("test-gate5")
        self.state_machine.transition_to(ConversationState.SERVICE_DATA_COLLECTION)
        self._init_to_v2_service_data()
        self.state_machine.service_data["to_v2_step"] = 5
        self.state_machine.service_data["to_v2_substate"] = "gate5"
        self.play_wav_or_tts = AsyncMock()
        self._to_v2_prompt_step = AsyncMock()
        self._to_v2_transfer_service = AsyncMock()
        self._to_v2_interrupt_booking_transfer = AsyncMock()
        self._to_v2_maybe_start_price_block = AsyncMock(return_value=True)
        self._extract_day_part = lambda _text: None
        self.service_day_part = None


class _AfterHoursBot(BotDialogMixin):
    def __init__(self, *, fio: str = "") -> None:
        self.state_machine = ConversationStateMachine("test-after-hours")
        self.state_machine.service_data["fio"] = fio
        self.voice_scenario = "v2"
        self.play_wav_or_tts = AsyncMock()
        self._do_after_hours_closure = AsyncMock()
        self._v2_after_hours_begin_name_flow = AsyncMock()

    def _is_non_working_hours(self) -> bool:
        return True

    def _log(self, *_args) -> None:
        return None


class _AfterHoursLegacyBot(BotDialogMixin):
    def __init__(self) -> None:
        self.state_machine = ConversationStateMachine("test-after-hours-legacy")
        self.voice_scenario = "legacy"
        self.play_wav_or_tts = AsyncMock()
        self._do_after_hours_closure = AsyncMock()

    def _is_non_working_hours(self) -> bool:
        return True

    def _log(self, *_args) -> None:
        return None


class ToV2Gate5Test(unittest.IsolatedAsyncioTestCase):
    async def test_new_flow_skips_gate_and_works_after_year_mileage(self) -> None:
        bot = _Bot()
        sd = bot.state_machine.service_data
        sd["to_v2_step"] = 4
        sd["to_v2_substate"] = None

        await bot._to_v2_advance_linear(4)

        self.assertEqual(sd["to_v2_step"], 6)
        self.assertIsNone(sd.get("work_list"))
        bot._to_v2_prompt_step.assert_awaited_once_with(6)
        bot.play_wav_or_tts.assert_not_awaited()

    async def test_new_flow_sets_to_after_mileage_step(self) -> None:
        bot = _Bot()
        sd = bot.state_machine.service_data
        sd["to_v2_step"] = 6
        sd["to_v2_substate"] = None

        await bot._to_v2_advance_linear(6)

        self.assertEqual(sd["to_v2_step"], 7)
        self.assertEqual(sd["work_list"], "ТО")
        self.assertFalse(sd["operation_unknown"])
        bot._to_v2_prompt_step.assert_awaited_once_with(7)

    async def test_allo_reprompts_not_transfer(self) -> None:
        bot = _Bot()
        await bot._to_v2_handle_gate5("Алло.", "алло.")
        bot._to_v2_interrupt_booking_transfer.assert_not_awaited()
        bot.play_wav_or_tts.assert_awaited()

    async def test_dalshe_advances(self) -> None:
        bot = _Bot()
        await bot._to_v2_handle_gate5("дальше", "дальше")
        bot._to_v2_prompt_step.assert_awaited_once_with(6)
        bot._to_v2_interrupt_booking_transfer.assert_not_awaited()

    async def test_da_advances(self) -> None:
        bot = _Bot()
        await bot._to_v2_handle_gate5("да", "да")
        bot._to_v2_prompt_step.assert_awaited_once_with(6)

    async def test_collection_interrupts_and_transfers_on_human_markers(self) -> None:
        for phrase in (
            "Переведите.",
            "Дайте.",
            "ассистента",
            "диспетчера",
            "человека",
        ):
            with self.subTest(phrase=phrase):
                bot = _Bot()
                bot.state_machine.service_data["to_v2_step"] = 2
                bot.state_machine.service_data["to_v2_substate"] = None

                await bot._handle_service_data_collection_v2(phrase)

                bot._to_v2_transfer_service.assert_awaited_once()
                bot._to_v2_prompt_step.assert_not_awaited()
                bot._to_v2_interrupt_booking_transfer.assert_not_awaited()

    async def test_step2_stores_raw_phone_text(self) -> None:
        bot = _Bot()
        bot.state_machine.service_data["to_v2_step"] = 2
        bot.state_machine.service_data["to_v2_substate"] = None
        bot.name_extractor = Mock()
        bot.name_extractor.extract_phone = Mock(return_value=None)

        await bot._handle_service_data_collection_v2("9 3 7")

        self.assertEqual(bot.state_machine.service_data.get("phone_raw"), "9 3 7")

    async def test_step7_reasks_once_then_offers_nearest_slot(self) -> None:
        bot = _Bot()
        bot.state_machine.service_data["to_v2_step"] = 7
        bot.date_parser = DateParser()
        bot._offer_nearest_slot_after_date_silence = AsyncMock()

        await bot._to_v2_handle_step7("дата непонятна", "дата непонятна")
        self.assertEqual(bot.play_wav_or_tts.await_count, 1)
        bot._offer_nearest_slot_after_date_silence.assert_not_awaited()

        await bot._to_v2_handle_step7("по-прежнему непонятно", "по-прежнему непонятно")

        bot._offer_nearest_slot_after_date_silence.assert_awaited_once_with(
            after_unrecognized_date=True,
        )

    async def test_step7_empty_after_first_unrecognized_offers_nearest_slot(self) -> None:
        bot = _Bot()
        bot.state_machine.service_data["to_v2_step"] = 7
        bot.date_parser = DateParser()
        bot._offer_nearest_slot_after_date_silence = AsyncMock()

        await bot._to_v2_handle_step7("дата непонятна", "дата непонятна")
        bot._offer_nearest_slot_after_date_silence.assert_not_awaited()

        bot._dialog_empty_kind = "stt_empty"
        await bot._to_v2_handle_step7("", "")
        bot._offer_nearest_slot_after_date_silence.assert_awaited_once_with(
            after_unrecognized_date=True,
        )

    async def test_step7_parses_3catoye_august_without_using_time_as_date(self) -> None:
        bot = _Bot()
        bot.state_machine.service_data["to_v2_step"] = 7
        bot.date_parser = DateParser()
        bot._process_service_booking = AsyncMock()

        with patch(
            "dialog.dealer_time.dealer_local_now_naive",
            return_value=datetime(2026, 7, 26),
        ):
            await bot._to_v2_handle_step7(
                "3цатое августа, 9:00",
                "3цатое августа, 9:00",
            )

        self.assertEqual(bot.state_machine.service_data["desired_date"], "2026-08-30")
        self.assertEqual(bot.state_machine.service_data["desired_time"], "09:00")
        bot._process_service_booking.assert_awaited_once()

    async def test_step7_parses_day_without_month_and_morning_interval(self) -> None:
        bot = _Bot()
        bot.state_machine.service_data["to_v2_step"] = 7
        bot.date_parser = DateParser()
        bot._process_service_booking = AsyncMock()
        bot._extract_day_part = extract_day_part

        with patch(
            "dialog.dealer_time.dealer_local_now_naive",
            return_value=datetime(2026, 7, 27, 11, 23),
        ):
            await bot._to_v2_handle_step7(
                "Э-э, двадцать восьмое утро.",
                "э-э, двадцать восьмое утро.",
            )

        self.assertEqual(bot.state_machine.service_data["desired_date"], "2026-07-28")
        self.assertEqual(bot.service_day_part, "morning")
        bot._process_service_booking.assert_awaited_once()

    async def test_step7_month_only_offers_nearest_slot_in_same_month(self) -> None:
        bot = _Bot()
        bot.state_machine.service_data["to_v2_step"] = 7
        bot.date_parser = DateParser()
        bot._process_service_booking = AsyncMock()
        bot._offer_nearest_slot_in_month = AsyncMock()

        with patch(
            "dialog.dealer_time.dealer_local_now_naive",
            return_value=datetime(2026, 7, 27, 11, 23),
        ):
            await bot._to_v2_handle_step7(
                "В августе.",
                "в августе.",
            )

        bot._offer_nearest_slot_in_month.assert_awaited_once_with(2026, 8)
        bot._process_service_booking.assert_not_awaited()

    async def test_asap_booking_offers_nearest_slot_directly(self) -> None:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-current-week-nearest")
        bot.state_machine.service_data.update(
            {
                "desired_date": "2026-07-27",
                "asap_slot": True,
                "work_list": "ТО",
            }
        )
        bot._offer_nearest_slot_after_date_silence = AsyncMock()

        await bot._process_service_booking()

        bot._offer_nearest_slot_after_date_silence.assert_awaited_once_with()

    async def test_step7_rejects_explicit_date_that_already_passed(self) -> None:
        bot = _Bot()
        bot.state_machine.service_data["to_v2_step"] = 7
        bot.date_parser = DateParser()
        bot._process_service_booking = AsyncMock()

        with patch(
            "dialog.dealer_time.dealer_local_now_naive",
            return_value=datetime(2026, 7, 27, 8, 30),
        ):
            await bot._to_v2_handle_step7(
                "двадцать первое июля в девять",
                "двадцать первое июля в девять",
            )

        self.assertIsNone(bot.state_machine.service_data["desired_date"])
        bot.play_wav_or_tts.assert_awaited_once_with(
            None,
            "Эта дата уже прошла. Назовите, пожалуйста, другую дату и время.",
        )
        bot._process_service_booking.assert_not_awaited()

    async def test_step7_past_date_after_unrecognized_offers_nearest_slot(self) -> None:
        bot = _Bot()
        bot.state_machine.service_data["to_v2_step"] = 7
        bot.state_machine.service_data["date_time_attempts"] = 1
        bot.date_parser = DateParser()
        bot._process_service_booking = AsyncMock()
        bot._offer_nearest_slot_after_date_silence = AsyncMock()

        with patch(
            "dialog.dealer_time.dealer_local_now_naive",
            return_value=datetime(2026, 9, 7, 8, 30),
        ):
            await bot._to_v2_handle_step7(
                "тридцатое августа",
                "тридцатое августа",
            )

        bot.play_wav_or_tts.assert_awaited_once_with(
            None,
            "Эта дата уже прошла. Предлагаю ближайший свободный слот.",
        )
        bot._offer_nearest_slot_after_date_silence.assert_awaited_once_with(
            after_unrecognized_date=True,
        )
        bot._process_service_booking.assert_not_awaited()

    async def test_step3_tenet_brand_only_prompts_model_clarify(self) -> None:
        bot = _Bot()
        bot.state_machine.service_data["to_v2_step"] = 3
        bot.state_machine.service_data["to_v2_substate"] = None
        bot.car_brand_extractor = Mock()
        bot.car_brand_extractor.extract_car_info = Mock(return_value=("tenet", None))

        await bot._to_v2_handle_step3_car("Тенет", "тенет")

        bot.play_wav_or_tts.assert_awaited_once()
        args = bot.play_wav_or_tts.await_args.args
        self.assertEqual(args[0], None)
        self.assertIn("Уточните, пожалуйста, модель", args[1])
        self.assertIn("Tenet", args[1])
        bot._to_v2_prompt_step.assert_not_awaited()

    async def test_step3_chery_brand_only_prompts_model_clarify(self) -> None:
        bot = _Bot()
        bot.state_machine.service_data["to_v2_step"] = 3
        bot.state_machine.service_data["to_v2_substate"] = None
        bot.car_brand_extractor = Mock()
        bot.car_brand_extractor.extract_car_info = Mock(return_value=("chery", None))

        await bot._to_v2_handle_step3_car("Чери", "чери")

        bot.play_wav_or_tts.assert_awaited_once()
        args = bot.play_wav_or_tts.await_args.args
        self.assertEqual(args[0], None)
        self.assertIn("Уточните, пожалуйста, модель", args[1])
        self.assertIn("Chery", args[1])
        bot._to_v2_prompt_step.assert_not_awaited()

    async def test_step7_wait_request_offers_nearest_slot(self) -> None:
        bot = _Bot()
        bot.state_machine.service_data["to_v2_step"] = 7
        bot.date_parser = DateParser()
        bot._process_service_booking = AsyncMock()
        bot._offer_nearest_slot_after_date_silence = AsyncMock()

        await bot._to_v2_handle_step7(
            "Сейчас, подожди.",
            "сейчас, подожди.",
        )

        self.assertIsNone(bot.state_machine.service_data["desired_date"])
        bot.play_wav_or_tts.assert_not_awaited()
        bot._process_service_booking.assert_not_awaited()
        bot._offer_nearest_slot_after_date_silence.assert_awaited_once()

    async def test_step7_without_time_preference_sets_asap_and_processes_booking(self) -> None:
        bot = _Bot()
        bot.state_machine.service_data["to_v2_step"] = 7
        bot.date_parser = DateParser()
        bot._process_service_booking = AsyncMock()

        await bot._to_v2_handle_step7(
            "Без разницы.",
            "без разницы.",
        )

        self.assertIsNotNone(bot.state_machine.service_data["desired_date"])
        self.assertTrue(bot.state_machine.service_data.get("asap_slot"))
        bot._process_service_booking.assert_awaited_once()
        bot.play_wav_or_tts.assert_not_awaited()

    async def test_service_transfer_uses_hours_guard_before_wav(self) -> None:
        bot = _Bot()
        bot._transfer_or_after_hours = AsyncMock()

        await ToBookingV2Mixin._to_v2_transfer_service(bot, reason="test")

        bot.play_wav_or_tts.assert_not_awaited()
        bot._transfer_or_after_hours.assert_awaited_once_with(
            "16_transfer_service_assistant.wav",
            ClientNeed.SERVICE,
        )

    async def test_after_hours_reuses_collected_fio(self) -> None:
        bot = _AfterHoursBot(fio="Роман Козенков")

        await bot._department_phase_transfer_for_need(
            ClientNeed.SERVICE,
            raw_client_text="переведи на ассистента",
            allow_booking_reentry=False,
        )

        bot._do_after_hours_closure.assert_awaited_once_with(ClientNeed.SERVICE)
        bot._v2_after_hours_begin_name_flow.assert_not_awaited()
        bot.play_wav_or_tts.assert_not_awaited()

    async def test_after_hours_asks_name_when_not_collected(self) -> None:
        bot = _AfterHoursBot()

        await bot._department_phase_transfer_for_need(
            ClientNeed.SERVICE,
            raw_client_text="переведи на ассистента",
            allow_booking_reentry=False,
        )

        bot._v2_after_hours_begin_name_flow.assert_awaited_once_with(ClientNeed.SERVICE)
        bot._do_after_hours_closure.assert_not_awaited()

    async def test_after_hours_unclear_reply_starts_callback_name_flow(self) -> None:
        bot = _AfterHoursBot()
        bot.state_machine.department_prompts_shown = 1

        await bot._ask_about_departments()

        bot._v2_after_hours_begin_name_flow.assert_awaited_once_with(None)
        bot._do_after_hours_closure.assert_not_awaited()
        bot.play_wav_or_tts.assert_not_awaited()

    async def test_after_hours_legacy_transfer_skips_transfer_phrase(self) -> None:
        bot = _AfterHoursLegacyBot()

        await bot._department_phase_transfer_for_need(
            ClientNeed.PARTS,
            raw_client_text="запчасти",
            allow_booking_reentry=False,
        )

        bot._do_after_hours_closure.assert_awaited_once_with(ClientNeed.PARTS)
        bot.play_wav_or_tts.assert_not_awaited()


class ToBookingIntentDetectionTest(unittest.TestCase):
    def test_particle_to_phrase_is_not_booking(self) -> None:
        self.assertFalse(_stt_implies_to_booking_request("чё поговорить-то?"))

    def test_real_to_phrases_still_detected(self) -> None:
        self.assertTrue(_stt_implies_to_booking_request("мне нужно ТО"))
        self.assertTrue(_stt_implies_to_booking_request("хочу пройти ТО-2"))
        self.assertTrue(_stt_implies_to_booking_request("записаться на ТО"))


class ToV2Menu14DefaultBookingTest(unittest.IsolatedAsyncioTestCase):
    def _bot(self) -> BotDialogMixin:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-menu14-default")
        bot.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
        bot.state_machine.set_identified_need(ClientNeed.SERVICE)
        bot.state_machine.menu14_booking_active = True
        bot.play_wav_or_tts = AsyncMock()
        bot._to_v2_begin_data_collection = AsyncMock()
        bot._to_v2_transfer_service = AsyncMock()
        bot._log = lambda *_args: None
        return bot

    async def test_explicit_booking_markers_start_booking(self) -> None:
        for phrase in ("А на ТО.", "запиши меня", "записаться", "запишите, пожалуйста"):
            with self.subTest(phrase=phrase):
                bot = self._bot()

                handled = await bot._handle_need_identified_to_v2_menu14(phrase)

                self.assertTrue(handled)
                bot._to_v2_begin_data_collection.assert_awaited_once()
                bot._to_v2_transfer_service.assert_not_awaited()
                self.assertFalse(bot.state_machine.menu14_booking_active)

    async def test_dvesti_treated_as_unclear_not_booking(self) -> None:
        bot = self._bot()

        handled = await bot._handle_need_identified_to_v2_menu14("Двести.")

        self.assertTrue(handled)
        bot._to_v2_transfer_service.assert_awaited_once_with(reason="menu14_unclear")
        bot._to_v2_begin_data_collection.assert_not_awaited()

    async def test_empty_answer_transfers_to_assistant(self) -> None:
        bot = self._bot()

        await bot._handle_need_identified_to_v2_menu14("")

        bot._to_v2_transfer_service.assert_awaited_once_with(reason="menu14_unclear")
        bot.play_wav_or_tts.assert_not_awaited()

    async def test_zapishis_na_to_stt_starts_booking_before_service_reroute(self) -> None:
        """STT «запишись» = «запиши»: повторное «на ТО» не переводит к ассистенту."""
        bot = self._bot()
        bot._transfer_or_after_hours = AsyncMock()

        text = normalize_department_stt_text(
            "НуЭ-э, запишись на ТО, если сегодня можно."
        )
        await bot._handle_need_identified_state(text)

        bot._to_v2_begin_data_collection.assert_awaited_once()
        bot._to_v2_transfer_service.assert_not_awaited()
        bot._transfer_or_after_hours.assert_not_awaited()
        self.assertFalse(bot.state_machine.menu14_booking_active)

    async def test_zapisat_na_telo_stt_starts_booking(self) -> None:
        """STT «на тело» нормализуется и запускает сбор данных записи."""
        bot = self._bot()
        bot._transfer_or_after_hours = AsyncMock()

        text = normalize_department_stt_text("Записать на тело.")
        await bot._handle_need_identified_state(text)

        bot._to_v2_begin_data_collection.assert_awaited_once()
        bot._to_v2_transfer_service.assert_not_awaited()
        bot._transfer_or_after_hours.assert_not_awaited()
        self.assertFalse(bot.state_machine.menu14_booking_active)

    async def test_zapisatsya_na_tv_stt_starts_booking(self) -> None:
        """STT «на ТВ» трактуется как «на ТО», без перевода на ассистента."""
        bot = self._bot()
        bot._transfer_or_after_hours = AsyncMock()

        text = normalize_department_stt_text("Записаться на ТВ.")
        await bot._handle_need_identified_state(text)

        bot._to_v2_begin_data_collection.assert_awaited_once()
        bot._to_v2_transfer_service.assert_not_awaited()
        bot._transfer_or_after_hours.assert_not_awaited()
        self.assertFalse(bot.state_machine.menu14_booking_active)

    async def test_explicit_human_request_still_transfers(self) -> None:
        for phrase in (
            "переведите на человека",
            "соедините с диспетчером",
            "хочу ассистента сервиса",
            "ассистент, запишите меня",
            "консультацию",
            "Вести.",
            "Привести.",
        ):
            with self.subTest(phrase=phrase):
                bot = self._bot()

                handled = await bot._handle_need_identified_to_v2_menu14(phrase)

                self.assertTrue(handled)
                bot._to_v2_transfer_service.assert_awaited_once_with(reason="menu14_assistant")
                bot._to_v2_begin_data_collection.assert_not_awaited()

    async def test_to_question_without_booking_intent_transfers_to_assistant(self) -> None:
        bot = self._bot()

        handled = await bot._handle_need_identified_to_v2_menu14("ТО Джитурдашинг вы делаете?")

        self.assertTrue(handled)
        bot._to_v2_transfer_service.assert_awaited_once_with(reason="menu14_assistant")
        bot._to_v2_begin_data_collection.assert_not_awaited()

    async def test_price_inquiry_transfers_instead_of_booking(self) -> None:
        for phrase in (
            "цена ТО нулевого",
            "цена ТО1",
            "сколько стоит ТО",
            "почем то-2",
        ):
            with self.subTest(phrase=phrase):
                bot = self._bot()

                handled = await bot._handle_need_identified_to_v2_menu14(phrase)

                self.assertTrue(handled)
                bot._to_v2_transfer_service.assert_awaited_once_with(
                    reason="menu14_price_inquiry"
                )
                bot._to_v2_begin_data_collection.assert_not_awaited()

    async def test_already_booked_phrase_transfers_instead_of_booking(self) -> None:
        for phrase in (
            "я уже записан",
            "я записан на понедельник",
            "записалась уже",
        ):
            with self.subTest(phrase=phrase):
                bot = self._bot()

                handled = await bot._handle_need_identified_to_v2_menu14(phrase)

                self.assertTrue(handled)
                bot._to_v2_transfer_service.assert_awaited_once_with(
                    reason="menu14_already_booked"
                )
                bot._to_v2_begin_data_collection.assert_not_awaited()

    async def test_unclear_answer_repeats_menu(self) -> None:
        bot = self._bot()

        handled = await bot._handle_need_identified_to_v2_menu14("Алло.")

        self.assertTrue(handled)
        bot._to_v2_transfer_service.assert_awaited_once_with(reason="menu14_unclear")
        bot._to_v2_begin_data_collection.assert_not_awaited()

    async def test_unclear_answer_on_sunday_transfers_to_admin(self) -> None:
        bot = self._bot()
        bot._transfer_or_after_hours = AsyncMock()

        sunday = datetime(2026, 8, 16, 13, 20, tzinfo=ZoneInfo("Europe/Samara"))
        with patch("dialog.bot_logic.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = sunday
            handled = await bot._handle_need_identified_to_v2_menu14("Алло.")

        self.assertTrue(handled)
        bot._to_v2_transfer_service.assert_not_awaited()
        bot.play_wav_or_tts.assert_awaited_once_with(
            None,
            "Поняла, перевожу Вас на администратора.",
        )
        bot._transfer_or_after_hours.assert_awaited_once_with(
            "03_transfer_admin.wav",
            None,
            voice_admin_reason="client_request",
            skip_announcement=True,
        )


class NearestAfterUnrecognizedDateTest(unittest.IsolatedAsyncioTestCase):
    def _bot(self) -> BotDialogMixin:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-nearest-date-fallback")
        bot.state_machine.transition_to(ConversationState.SERVICE_SLOT_SELECTION)
        bot.date_parser = DateParser()
        bot.service_day_part = None
        bot.play_wav_or_tts = AsyncMock()
        bot._transfer_service_assistant_slot_selection = AsyncMock()
        sd = bot.state_machine.service_data
        sd.update(
            {
                "desired_date": "2026-07-26",
                "proposed_date": "2026-07-31",
                "proposed_time": "10:10",
                "proposed_post": "post-1",
                "proposed_acceptor_id": "acceptor-1",
                "proposed_slot_start_iso": "2026-07-31T10:10:00",
                "slot_await_confirm": True,
                "nearest_after_unrecognized_date": True,
            }
        )
        return bot

    async def test_no_rejects_nearest_and_requests_date_again(self) -> None:
        bot = self._bot()

        await bot._handle_service_slot_selection_state("Нет.")

        sd = bot.state_machine.service_data
        self.assertIsNone(sd["desired_date"])
        self.assertIsNone(sd["proposed_date"])
        self.assertFalse(sd["nearest_after_unrecognized_date"])
        bot.play_wav_or_tts.assert_awaited_once_with(
            None,
            "Назовите желаемую дату и желаемое время.",
        )
        bot._transfer_service_assistant_slot_selection.assert_not_awaited()

    async def test_unclear_nearest_answer_transfers_to_service_assistant(self) -> None:
        bot = self._bot()

        await bot._handle_service_slot_selection_state("Не знаю.")

        bot._transfer_service_assistant_slot_selection.assert_awaited_once_with(
            reason="nearest_after_unrecognized_date_unclear",
        )

    async def test_later_selects_another_slot_without_transfer(self) -> None:
        bot = self._bot()
        picked = PickedSlot(
            "2026-07-31",
            "13:00",
            "post-2",
            "2026-07-31T13:00:00",
            "acceptor-2",
        )

        with patch(
            "dialog.service_slot_pick.pick_slot_for_day",
            new=AsyncMock(return_value=picked),
        ):
            await bot._handle_service_slot_selection_state("Позже.")

        self.assertEqual(bot.state_machine.service_data["proposed_time"], "13:00")
        self.assertFalse(
            bot.state_machine.service_data["nearest_after_unrecognized_date"]
        )
        bot._transfer_service_assistant_slot_selection.assert_not_awaited()

    async def test_new_date_replaces_nearest_slot_without_transfer(self) -> None:
        bot = self._bot()
        picked = PickedSlot(
            "2026-08-30",
            "09:00",
            "post-3",
            "2026-08-30T09:00:00",
            "acceptor-3",
        )

        with (
            patch(
                "dialog.dealer_time.dealer_local_now_naive",
                return_value=datetime(2026, 7, 26),
            ),
            patch(
                "dialog.service_slot_pick.pick_slot_for_day",
                new=AsyncMock(return_value=picked),
            ),
        ):
            await bot._handle_service_slot_selection_state(
                "3цатое августа, 9:00."
            )

        self.assertEqual(bot.state_machine.service_data["proposed_date"], "2026-08-30")
        self.assertEqual(bot.state_machine.service_data["proposed_time"], "09:00")
        self.assertFalse(
            bot.state_machine.service_data["nearest_after_unrecognized_date"]
        )
        bot._transfer_service_assistant_slot_selection.assert_not_awaited()

    async def test_later_with_explicit_date_prefers_explicit_date(self) -> None:
        bot = self._bot()
        picked = PickedSlot(
            "2026-08-15",
            "09:00",
            "post-4",
            "2026-08-15T09:00:00",
            "acceptor-4",
        )

        with (
            patch(
                "dialog.dealer_time.dealer_local_now_naive",
                return_value=datetime(2026, 8, 7),
            ),
            patch(
                "dialog.service_slot_pick.pick_slot_for_day",
                new=AsyncMock(return_value=picked),
            ),
        ):
            await bot._handle_service_slot_selection_state("Позже 15-го числа.")

        self.assertEqual(bot.state_machine.service_data["proposed_date"], "2026-08-15")
        self.assertEqual(bot.state_machine.service_data["proposed_time"], "09:00")
        self.assertFalse(
            bot.state_machine.service_data["nearest_after_unrecognized_date"]
        )
        bot._transfer_service_assistant_slot_selection.assert_not_awaited()

    async def test_alt_date_with_explicit_time_uses_requested_time(self) -> None:
        bot = self._bot()
        sd = bot.state_machine.service_data
        sd["slot_await_alt_choice"] = True
        sd["slot_alt_before"] = {
            "date": "2026-08-16",
            "time": "08:00",
            "post": "post-before",
            "acceptor": "acc-before",
            "iso": "2026-08-16T08:00:00",
        }
        sd["slot_alt_after"] = {
            "date": "2026-08-20",
            "time": "08:00",
            "post": "post-after",
            "acceptor": "acc-after",
            "iso": "2026-08-20T08:00:00",
        }
        picked = PickedSlot(
            "2026-08-20",
            "12:00",
            "post-after",
            "2026-08-20T12:00:00",
            "acc-after",
        )
        with (
            patch(
                "dialog.dealer_time.dealer_local_now_naive",
                return_value=datetime(2026, 8, 11),
            ),
            patch(
                "dialog.service_slot_pick.pick_slot_for_day",
                new=AsyncMock(return_value=picked),
            ),
        ):
            await bot._handle_service_slot_selection_state("20 августа, 12:00.")

        self.assertEqual(sd["proposed_date"], "2026-08-20")
        self.assertEqual(sd["proposed_time"], "12:00")
        self.assertFalse(sd.get("slot_await_alt_choice"))


class ToV2DepartmentSwitchTest(unittest.IsolatedAsyncioTestCase):
    async def test_station_phrases_route_directly_to_service(self) -> None:
        for phrase in ("со станции", "со станцией"):
            with self.subTest(phrase=phrase):
                bot = BotDialogMixin()
                bot.state_machine = ConversationStateMachine(
                    f"test-station-{phrase}"
                )
                bot._department_phase_transfer_for_need = AsyncMock()
                bot._log = lambda *_args: None

                handled = await bot._try_department_phase_from_client_speech(
                    phrase,
                    normalize_department_stt_text(phrase),
                )

                self.assertTrue(handled)
                bot._department_phase_transfer_for_need.assert_awaited_once_with(
                    ClientNeed.SERVICE,
                    raw_client_text=phrase,
                    allow_booking_reentry=True,
                )

    async def test_to_price_inquiry_with_nto_routes_to_service(self) -> None:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-nto-price-service-route")
        bot._department_phase_transfer_for_need = AsyncMock()
        bot._log = lambda *_args: None

        phrase = "Узнать цену НТО."
        handled = await bot._try_department_phase_from_client_speech(
            phrase,
            normalize_department_stt_text(phrase),
        )

        self.assertTrue(handled)
        bot._department_phase_transfer_for_need.assert_awaited_once_with(
            ClientNeed.SERVICE,
            raw_client_text=phrase,
            allow_booking_reentry=True,
        )

    async def test_pozyvnoy_remont_routes_to_body_repair(self) -> None:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-pozyvnoy-remont-body-route")
        bot._department_phase_transfer_for_need = AsyncMock()
        bot._log = lambda *_args: None

        phrase = "Позывной ремонт."
        handled = await bot._try_department_phase_from_client_speech(
            phrase,
            normalize_department_stt_text(phrase),
        )

        self.assertTrue(handled)
        bot._department_phase_transfer_for_need.assert_awaited_once_with(
            ClientNeed.BODY_REPAIR,
            raw_client_text=phrase,
            allow_booking_reentry=True,
        )

    async def test_sales_prefix_without_parts_or_details_routes_to_new_cars(self) -> None:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-sales-prefix-new-cars")
        bot._department_phase_transfer_for_need = AsyncMock()
        bot._log = lambda *_args: None

        phrase = "Продажи мне надо установить."
        handled = await bot._try_department_phase_from_client_speech(
            phrase,
            normalize_department_stt_text(phrase),
        )

        self.assertTrue(handled)
        bot._department_phase_transfer_for_need.assert_awaited_once_with(
            ClientNeed.NEW_CARS_CHERY_TENET,
            raw_client_text=phrase,
            allow_booking_reentry=True,
        )

    async def test_all_six_department_choices_override_to_menu(self) -> None:
        cases = (
            ("слесарный цех", "16_transfer_service_assistant.wav", ClientNeed.SERVICE),
            ("соедините с кузовным цехом", "13_transfer_body_repair.wav", ClientNeed.BODY_REPAIR),
            ("переведите в отдел запчастей", "11_transfer_parts.wav", ClientNeed.PARTS),
            ("соедините с администратором", "16_transfer_service_assistant.wav", ClientNeed.SERVICE),
            (
                "отдел продаж Чери Тенет",
                "05_transfer_chery_tenet.wav",
                ClientNeed.NEW_CARS_CHERY_TENET,
            ),
            ("автомобили с пробегом", "04_transfer_used_cars.wav", ClientNeed.USED_CARS),
        )
        for phrase, expected_wav, expected_need in cases:
            with self.subTest(phrase=phrase):
                bot = BotDialogMixin()
                bot.state_machine = ConversationStateMachine(f"test-department-{expected_wav}")
                bot.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
                bot.state_machine.set_identified_need(ClientNeed.SERVICE)
                bot.state_machine.menu14_booking_active = True
                bot.voice_scenario = "v2"
                bot.play_wav_or_tts = AsyncMock()
                bot._transfer_or_after_hours = AsyncMock()
                bot._log = lambda *_args: None
                bot._is_non_working_hours = lambda: False

                text = normalize_department_stt_text(phrase)
                await bot._handle_need_identified_state(text)

                bot._transfer_or_after_hours.assert_awaited_once()
                call = bot._transfer_or_after_hours.await_args
                self.assertEqual(call.args[0], expected_wav)
                if expected_need is None:
                    self.assertIsNone(call.args[1])
                else:
                    self.assertEqual(call.args[1], expected_need)

    async def test_sunday_locksmith_branch_starts_admin_or_booking_choice(self) -> None:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-sunday-locksmith-start")
        bot.play_wav_or_tts = AsyncMock()
        bot._transfer_or_after_hours = AsyncMock()
        bot._to_v2_begin_data_collection = AsyncMock()
        bot._log = lambda *_args: None

        sunday = datetime(2026, 7, 26, 11, 0, tzinfo=ZoneInfo("Europe/Samara"))
        with patch("dialog.bot_logic.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = sunday
            handled = await bot._try_department_phase_from_client_speech(
                "слесарный цех",
                normalize_department_stt_text("слесарный цех"),
            )

        self.assertTrue(handled)
        self.assertTrue(bot.state_machine.service_data["sunday_locksmith_choice_active"])
        self.assertEqual(bot.state_machine.state, ConversationState.NEED_IDENTIFIED)
        self.assertEqual(bot.state_machine.identified_need, ClientNeed.SERVICE)
        bot.play_wav_or_tts.assert_awaited_once()
        prompt = bot.play_wav_or_tts.await_args.args[1]
        self.assertIn("В воскресенье приемка слесарного цеха не работает", prompt)
        bot._transfer_or_after_hours.assert_not_awaited()

    async def test_sunday_service_department_starts_admin_or_booking_choice(self) -> None:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-sunday-service-start")
        bot.play_wav_or_tts = AsyncMock()
        bot._transfer_or_after_hours = AsyncMock()
        bot._to_v2_begin_data_collection = AsyncMock()
        bot._log = lambda *_args: None

        sunday = datetime(2026, 7, 26, 11, 0, tzinfo=ZoneInfo("Europe/Samara"))
        with patch("dialog.bot_logic.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = sunday
            handled = await bot._try_department_phase_from_client_speech(
                "Отдел техобслуживания.",
                normalize_department_stt_text("Отдел техобслуживания."),
            )

        self.assertTrue(handled)
        self.assertTrue(bot.state_machine.service_data["sunday_locksmith_choice_active"])
        self.assertEqual(bot.state_machine.state, ConversationState.NEED_IDENTIFIED)
        self.assertEqual(bot.state_machine.identified_need, ClientNeed.SERVICE)
        bot.play_wav_or_tts.assert_awaited_once()
        prompt = bot.play_wav_or_tts.await_args.args[1]
        self.assertIn("В воскресенье приемка слесарного цеха не работает", prompt)
        bot._transfer_or_after_hours.assert_not_awaited()

    async def test_sunday_repair_booking_phrase_starts_admin_or_booking_choice(self) -> None:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-sunday-repair-start")
        bot.play_wav_or_tts = AsyncMock()
        bot._transfer_or_after_hours = AsyncMock()
        bot._to_v2_begin_data_collection = AsyncMock()
        bot._log = lambda *_args: None

        sunday = datetime(2026, 7, 26, 11, 0, tzinfo=ZoneInfo("Europe/Samara"))
        with patch("dialog.bot_logic.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = sunday
            handled = await bot._try_department_phase_from_client_speech(
                "на ремонт записаться",
                normalize_department_stt_text("на ремонт записаться"),
            )

        self.assertTrue(handled)
        self.assertTrue(bot.state_machine.service_data["sunday_locksmith_choice_active"])
        self.assertEqual(bot.state_machine.state, ConversationState.NEED_IDENTIFIED)
        self.assertEqual(bot.state_machine.identified_need, ClientNeed.SERVICE)
        bot.play_wav_or_tts.assert_awaited_once()
        prompt = bot.play_wav_or_tts.await_args.args[1]
        self.assertIn("В воскресенье приемка слесарного цеха не работает", prompt)
        bot._transfer_or_after_hours.assert_not_awaited()

    async def test_sunday_locksmith_dispatcher_request_goes_to_admin(self) -> None:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-sunday-locksmith-admin")
        bot.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
        bot.state_machine.set_identified_need(ClientNeed.SERVICE)
        bot.state_machine.menu14_booking_active = True
        bot.state_machine.service_data["sunday_locksmith_choice_active"] = True
        bot.state_machine.service_data["sunday_locksmith_unclear_attempts"] = 0
        bot.play_wav_or_tts = AsyncMock()
        bot._transfer_or_after_hours = AsyncMock()
        bot._to_v2_begin_data_collection = AsyncMock()
        bot._log = lambda *_args: None

        await bot._handle_need_identified_state("переведите на диспетчера")

        bot._to_v2_begin_data_collection.assert_not_awaited()
        bot._transfer_or_after_hours.assert_awaited_once_with(
            "03_transfer_admin.wav",
            None,
            voice_admin_reason="client_request",
            skip_announcement=True,
        )
        bot.play_wav_or_tts.assert_awaited_once_with(
            None, "Поняла, перевожу Вас на администратора."
        )

    async def test_sunday_locksmith_operator_request_with_booking_words_goes_to_admin(self) -> None:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-sunday-locksmith-operator-priority")
        bot.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
        bot.state_machine.set_identified_need(ClientNeed.SERVICE)
        bot.state_machine.menu14_booking_active = True
        bot.state_machine.service_data["sunday_locksmith_choice_active"] = True
        bot.state_machine.service_data["sunday_locksmith_unclear_attempts"] = 0
        bot.play_wav_or_tts = AsyncMock()
        bot._transfer_or_after_hours = AsyncMock()
        bot._to_v2_begin_data_collection = AsyncMock()
        bot._log = lambda *_args: None

        await bot._handle_need_identified_state(
            "Мне оператора надо. Оператора переведите, где записывают на ТО."
        )

        bot._to_v2_begin_data_collection.assert_not_awaited()
        bot._transfer_or_after_hours.assert_awaited_once_with(
            "03_transfer_admin.wav",
            None,
            voice_admin_reason="client_request",
            skip_announcement=True,
        )
        bot.play_wav_or_tts.assert_awaited_once_with(
            None, "Поняла, перевожу Вас на администратора."
        )

    async def test_sunday_locksmith_unclear_twice_transfers_to_admin(self) -> None:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-sunday-locksmith-unclear")
        bot.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
        bot.state_machine.set_identified_need(ClientNeed.SERVICE)
        bot.state_machine.menu14_booking_active = True
        bot.state_machine.service_data["sunday_locksmith_choice_active"] = True
        bot.state_machine.service_data["sunday_locksmith_unclear_attempts"] = 0
        bot.play_wav_or_tts = AsyncMock()
        bot._transfer_or_after_hours = AsyncMock()
        bot._to_v2_begin_data_collection = AsyncMock()
        bot._log = lambda *_args: None

        await bot._handle_need_identified_state("не понял")
        await bot._handle_need_identified_state("еще раз")

        self.assertEqual(bot.play_wav_or_tts.await_count, 2)
        self.assertEqual(
            bot.play_wav_or_tts.await_args_list[0].args,
            (None, "Простите, повторите еще раз."),
        )
        self.assertEqual(
            bot.play_wav_or_tts.await_args_list[1].args,
            (None, "Перевожу Вас на администратора."),
        )
        bot._to_v2_begin_data_collection.assert_not_awaited()
        bot._transfer_or_after_hours.assert_awaited_once_with(
            "03_transfer_admin.wav",
            None,
            voice_admin_reason="client_request",
            skip_announcement=True,
        )

    async def test_sunday_locksmith_booking_marker_starts_to_flow(self) -> None:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-sunday-locksmith-booking")
        bot.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
        bot.state_machine.set_identified_need(ClientNeed.SERVICE)
        bot.state_machine.menu14_booking_active = True
        bot.state_machine.service_data["sunday_locksmith_choice_active"] = True
        bot.state_machine.service_data["sunday_locksmith_unclear_attempts"] = 0
        bot.play_wav_or_tts = AsyncMock()
        bot._transfer_or_after_hours = AsyncMock()
        bot._to_v2_begin_data_collection = AsyncMock()
        bot._log = lambda *_args: None

        await bot._handle_need_identified_state("запишите меня")

        bot._to_v2_begin_data_collection.assert_awaited_once()
        bot._transfer_or_after_hours.assert_not_awaited()

    async def test_sunday_locksmith_price_inquiry_goes_to_admin(self) -> None:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-sunday-locksmith-price")
        bot.state_machine.transition_to(ConversationState.NEED_IDENTIFIED)
        bot.state_machine.set_identified_need(ClientNeed.SERVICE)
        bot.state_machine.menu14_booking_active = True
        bot.state_machine.service_data["sunday_locksmith_choice_active"] = True
        bot.state_machine.service_data["sunday_locksmith_unclear_attempts"] = 0
        bot.play_wav_or_tts = AsyncMock()
        bot._transfer_or_after_hours = AsyncMock()
        bot._to_v2_begin_data_collection = AsyncMock()
        bot._log = lambda *_args: None

        await bot._handle_need_identified_state("сколько стоит ТО")

        bot._to_v2_begin_data_collection.assert_not_awaited()
        bot._transfer_or_after_hours.assert_awaited_once_with(
            "03_transfer_admin.wav",
            None,
            voice_admin_reason="client_request",
            skip_announcement=True,
        )
        bot.play_wav_or_tts.assert_awaited_once_with(
            None, "Поняла, перевожу Вас на администратора."
        )


class SlotConfirmAffirmativeTest(unittest.IsolatedAsyncioTestCase):
    def _bot(self) -> BotDialogMixin:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-slot-confirm-affirmative")
        bot.state_machine.transition_to(ConversationState.SERVICE_SLOT_SELECTION)
        bot.date_parser = DateParser()
        bot.service_day_part = None
        bot.play_wav_or_tts = AsyncMock()
        bot._transfer_to_service_on_network_error = AsyncMock()
        bot._is_non_working_hours = lambda: False
        sd = bot.state_machine.service_data
        sd.update(
            {
                "proposed_date": "2027-08-08",
                "proposed_time": "15:40",
                "proposed_post": "post-1",
                "proposed_acceptor_id": "acceptor-1",
                "proposed_slot_start_iso": "2027-08-08T15:40:00",
                "slot_await_confirm": True,
                "slot_await_alt_choice": False,
                "nearest_after_unrecognized_date": False,
                "work_list": "ТО",
            }
        )
        return bot

    async def test_time_plus_normally_confirms_whole_proposed_slot(self) -> None:
        bot = self._bot()

        with patch(
            "dialog.bot_logic.create_1c_booking",
            new=AsyncMock(return_value="local_test_booking_id"),
        ):
            await bot._handle_service_slot_selection_state("Пятнадцать сорок нормально.")

        bot._transfer_to_service_on_network_error.assert_not_awaited()
        self.assertEqual(bot.state_machine.state, ConversationState.SERVICE_BOOKED)
        self.assertEqual(
            bot.state_machine.service_data.get("service_1c_booking_id"),
            "local_test_booking_id",
        )

        all_bot_replies = [c.args[1] for c in bot.play_wav_or_tts.await_args_list if len(c.args) > 1]
        self.assertTrue(
            any(isinstance(t, str) and "Вы записаны на" in t for t in all_bot_replies)
        )
        self.assertFalse(
            any(isinstance(t, str) and "Не удалось распознать дату" in t for t in all_bot_replies)
        )

    async def test_confirm_with_misrecognized_time_books_last_offered_slot(self) -> None:
        bot = self._bot()

        with patch(
            "dialog.bot_logic.create_1c_booking",
            new=AsyncMock(return_value="local_test_booking_id"),
        ) as booking_mock:
            await bot._handle_service_slot_selection_state("Записать на 15 октября 1:10.")

        bot._transfer_to_service_on_network_error.assert_not_awaited()
        self.assertEqual(bot.state_machine.state, ConversationState.SERVICE_BOOKED)
        self.assertEqual(
            bot.state_machine.service_data.get("service_1c_booking_id"),
            "local_test_booking_id",
        )
        booking_mock.assert_awaited_once()
        slot_start = booking_mock.await_args.kwargs["slot_start"]
        self.assertEqual(slot_start.hour, 15)
        self.assertEqual(slot_start.minute, 40)

        all_bot_replies = [c.args[1] for c in bot.play_wav_or_tts.await_args_list if len(c.args) > 1]
        self.assertFalse(
            any(isinstance(t, str) and "Не удалось распознать дату" in t for t in all_bot_replies)
        )

    async def test_poyzet_confirms_slot_without_extra_reprompt(self) -> None:
        bot = self._bot()

        with patch(
            "dialog.bot_logic.create_1c_booking",
            new=AsyncMock(return_value="local_test_booking_id"),
        ):
            await bot._handle_service_slot_selection_state("Пойзёт.")

        self.assertEqual(bot.state_machine.state, ConversationState.SERVICE_BOOKED)
        self.assertEqual(
            bot.state_machine.service_data.get("service_1c_booking_id"),
            "local_test_booking_id",
        )

    async def test_same_date_time_as_offer_books_without_confirm_word(self) -> None:
        bot = self._bot()
        # Предложенный слот: 08.08.2027 15:40.
        with (
            patch(
                "dialog.bot_logic.create_1c_booking",
                new=AsyncMock(return_value="local_test_booking_id"),
            ) as booking_mock,
            patch(
                "dialog.dealer_time.dealer_local_now_naive",
                return_value=datetime(2027, 8, 1, 10, 0),
            ),
        ):
            await bot._handle_service_slot_selection_state("8 августа, 15:40.")

        self.assertEqual(bot.state_machine.state, ConversationState.SERVICE_BOOKED)
        booking_mock.assert_awaited_once()
        slot_start = booking_mock.await_args.kwargs["slot_start"]
        self.assertEqual(slot_start.strftime("%Y-%m-%d %H:%M"), "2027-08-08 15:40")

    async def test_no_with_new_time_keeps_date_and_reoffers_by_time(self) -> None:
        bot = self._bot()
        bot.service_day_part = "morning"
        picked = PickedSlot(
            "2027-08-08",
            "12:00",
            "post-2",
            "2027-08-08T12:00:00",
            "acceptor-2",
        )

        with patch(
            "dialog.service_slot_pick.pick_slot_for_day",
            new=AsyncMock(return_value=picked),
        ) as pick_mock:
            await bot._handle_service_slot_selection_state("Нет, в двенадцать.")

        self.assertEqual(bot.state_machine.state, ConversationState.SERVICE_SLOT_SELECTION)
        self.assertEqual(bot.state_machine.service_data.get("proposed_date"), "2027-08-08")
        self.assertEqual(bot.state_machine.service_data.get("proposed_time"), "12:00")
        self.assertTrue(bot.state_machine.service_data.get("slot_await_confirm"))
        self.assertEqual(bot.service_day_part, "morning")

        pick_mock.assert_awaited_once()
        self.assertIsNone(pick_mock.await_args.kwargs.get("day_part"))
        self.assertTrue(
            any(
                isinstance(c.args[1], str) and "двенадцать ноль ноль" in c.args[1]
                for c in bot.play_wav_or_tts.await_args_list
                if len(c.args) > 1
            )
        )

    async def test_affirmative_with_other_time_reoffers_instead_of_booking_old_slot(self) -> None:
        bot = self._bot()
        picked = PickedSlot(
            "2027-08-08",
            "15:30",
            "post-3",
            "2027-08-08T15:30:00",
            "acceptor-3",
        )

        with (
            patch(
                "dialog.service_slot_pick.pick_slot_for_day",
                new=AsyncMock(return_value=picked),
            ) as pick_mock,
            patch(
                "dialog.bot_logic.create_1c_booking",
                new=AsyncMock(return_value="local_should_not_book_old_slot"),
            ) as booking_mock,
        ):
            await bot._handle_service_slot_selection_state("А давайте лучше 15:30.")

        self.assertEqual(bot.state_machine.state, ConversationState.SERVICE_SLOT_SELECTION)
        self.assertEqual(bot.state_machine.service_data.get("proposed_time"), "15:30")
        self.assertTrue(bot.state_machine.service_data.get("slot_await_confirm"))
        pick_mock.assert_awaited_once()
        booking_mock.assert_not_awaited()

    async def test_time_requested_busy_announces_requested_time_unavailable(self) -> None:
        bot = self._bot()
        picked = PickedSlot(
            "2027-08-08",
            "15:00",
            "post-4",
            "2027-08-08T15:00:00",
            "acceptor-4",
        )

        with patch(
            "dialog.service_slot_pick.pick_slot_for_day",
            new=AsyncMock(return_value=picked),
        ):
            await bot._handle_service_slot_selection_state("Нет, в восемь утра.")

        replies = [
            c.args[1]
            for c in bot.play_wav_or_tts.await_args_list
            if len(c.args) > 1 and isinstance(c.args[1], str)
        ]
        self.assertTrue(
            any("в восемь ноль ноль свободных мест нет" in msg.lower() for msg in replies),
            replies,
        )
        self.assertEqual(bot.state_machine.service_data.get("proposed_time"), "15:00")


class WorkingHoursTest(unittest.TestCase):
    def _is_closed_at(self, hour: int, minute: int, *, month: int = 7, day: int = 14) -> bool:
        bot = BotDialogMixin()
        now = datetime(2026, month, day, hour, minute, tzinfo=ZoneInfo("Europe/Samara"))
        with patch("dialog.bot_logic.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = now
            return bot._is_non_working_hours()

    def test_service_hours_boundaries(self) -> None:
        self.assertTrue(self._is_closed_at(7, 59))
        self.assertFalse(self._is_closed_at(8, 0))
        self.assertFalse(self._is_closed_at(19, 59))
        self.assertTrue(self._is_closed_at(20, 0))

    def test_may_9_is_non_working_day(self) -> None:
        self.assertTrue(self._is_closed_at(12, 0, month=5, day=9))


class ServiceBookedFollowupTest(unittest.IsolatedAsyncioTestCase):
    def _bot(self) -> BotDialogMixin:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-service-booked")
        bot.state_machine.transition_to(ConversationState.SERVICE_BOOKED)
        bot.play_wav_or_tts = AsyncMock()
        bot._transfer_or_after_hours = AsyncMock()
        bot._try_department_phase_from_client_speech = AsyncMock(return_value=False)
        bot._is_non_working_hours = lambda: False
        bot.rag_system = None
        return bot

    async def test_no_questions_phrase_says_goodbye_and_no_transfer(self) -> None:
        bot = self._bot()

        await bot._handle_service_booked_state(
            "Нет, вопросов больше нет.",
            "Нет, вопросов больше нет.",
        )

        bot.play_wav_or_tts.assert_awaited_once_with("09_goodbye.wav", None)
        bot._transfer_or_after_hours.assert_not_awaited()
        self.assertEqual(bot.state_machine.state, ConversationState.ENDED)

    async def test_has_question_requests_real_service_transfer(self) -> None:
        bot = self._bot()

        await bot._handle_service_booked_state(
            "Да, есть вопрос по обслуживанию.",
            "Да, есть вопрос по обслуживанию.",
        )

        bot._transfer_or_after_hours.assert_awaited_once_with(
            "16_transfer_service_assistant.wav",
            ClientNeed.SERVICE,
        )
        self.assertEqual(bot.state_machine.state, ConversationState.SERVICE_BOOKED)

    async def test_closing_phrase_with_da_does_not_transfer(self) -> None:
        bot = self._bot()

        await bot._handle_service_booked_state(
            "А, ну всё, отлично. Да, хорошо, всё.",
            "А, ну всё, отлично. Да, хорошо, всё.",
        )

        bot.play_wav_or_tts.assert_awaited_once_with("09_goodbye.wav", None)
        bot._transfer_or_after_hours.assert_not_awaited()
        self.assertEqual(bot.state_machine.state, ConversationState.ENDED)

    async def test_after_goodbye_session_ignores_any_next_client_words(self) -> None:
        bot = self._bot()

        await bot._handle_service_booked_state(
            "Нет, вопросов больше нет.",
            "Нет, вопросов больше нет.",
        )
        self.assertEqual(bot.state_machine.state, ConversationState.ENDED)
        first_calls = bot.play_wav_or_tts.await_count

        await bot.process_client_text("Алло, переведите на диспетчера.")

        self.assertEqual(bot.play_wav_or_tts.await_count, first_calls)
        bot._transfer_or_after_hours.assert_not_awaited()

    async def test_sunday_dispatcher_request_transfers_to_admin_without_repeat_sunday_prompt(self) -> None:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-service-booked-sunday-admin")
        bot.state_machine.transition_to(ConversationState.SERVICE_BOOKED)
        bot.play_wav_or_tts = AsyncMock()
        bot._transfer_or_after_hours = AsyncMock()
        bot._is_non_working_hours = lambda: False
        bot.rag_system = None
        bot._log = lambda *_args: None

        sunday = datetime(2026, 8, 16, 13, 20, tzinfo=ZoneInfo("Europe/Samara"))
        with patch("dialog.bot_logic.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = sunday
            await bot._handle_service_booked_state(
                "Свяжитесь с диспетчером.",
                "Свяжитесь с диспетчером.",
            )

        bot._transfer_or_after_hours.assert_awaited_once_with(
            "03_transfer_admin.wav",
            None,
            voice_admin_reason="client_request",
            skip_announcement=True,
        )
        bot.play_wav_or_tts.assert_awaited_once_with(
            None,
            "Поняла, перевожу Вас на администратора.",
        )

    async def test_sunday_dispatcher_request_during_data_collection_transfers_to_admin(self) -> None:
        bot = BotDialogMixin()
        bot.state_machine = ConversationStateMachine("test-service-collect-sunday-admin")
        bot.state_machine.transition_to(ConversationState.SERVICE_DATA_COLLECTION)
        bot.state_machine.service_data["sunday_service_limit_announced"] = True
        bot.play_wav_or_tts = AsyncMock()
        bot._transfer_or_after_hours = AsyncMock()
        bot._handle_service_data_collection_state = AsyncMock()
        bot._is_non_working_hours = lambda: False
        bot.rag_system = None
        bot._log = lambda *_args: None

        sunday = datetime(2026, 8, 16, 13, 25, tzinfo=ZoneInfo("Europe/Samara"))
        with patch("dialog.bot_logic.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = sunday
            await bot.process_client_text("Свяжите с диспетчером.")

        bot._handle_service_data_collection_state.assert_not_awaited()
        bot._transfer_or_after_hours.assert_awaited_once_with(
            "03_transfer_admin.wav",
            None,
            voice_admin_reason="client_request",
            skip_announcement=True,
        )
        bot.play_wav_or_tts.assert_awaited_once_with(
            None,
            "Поняла, перевожу Вас на администратора.",
        )


if __name__ == "__main__":
    unittest.main()
