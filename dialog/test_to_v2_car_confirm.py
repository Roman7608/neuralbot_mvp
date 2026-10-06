"""Тесты распознавания модели авто в сценарии ТО v2."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from dialog.car_brand_extractor import CarBrandExtractor
from dialog.conversation_state import ConversationState
from dialog.to_booking_v2 import ToBookingV2Mixin


class _Bot(ToBookingV2Mixin):
    def __init__(self) -> None:
        from dialog.conversation_state import ConversationStateMachine

        self.state_machine = ConversationStateMachine("test-session")
        self.state_machine.transition_to(ConversationState.SERVICE_DATA_COLLECTION)
        self._init_to_v2_service_data()
        self.state_machine.service_data["to_v2_step"] = 3
        self.car_brand_extractor = MagicMock()
        self.play_wav_or_tts = AsyncMock()
        self._to_v2_advance_linear = AsyncMock()
        self._to_v2_prompt_step = AsyncMock()
        self._to_v2_transfer_service = AsyncMock()
        self._to_v2_interrupt_booking_transfer = AsyncMock()
        self._to_v2_maybe_retry_empty_step = AsyncMock(return_value=False)
        self._dialog_empty_kind = None

    def _format_car_for_tts(self, sd_data: dict) -> str:
        return sd_data.get("car_model") or "автомобиль"


class ToV2CarConfirmTest(unittest.IsolatedAsyncioTestCase):
    def test_nissan_almera_stt_analmera_variants(self) -> None:
        extractor = CarBrandExtractor()
        for phrase in (
            "Анальмера",
            "Альмера",
            "Эльмера",
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    extractor.extract_car_info(phrase),
                    ("Nissan", "ALMERA"),
                )

    def test_nissan_terrano_stt_tipan_variants(self) -> None:
        extractor = CarBrandExtractor()
        for phrase in (
            "Nissan Типап",
            "Nissan Тирап",
            "Nissan Типан",
            "Nissan Tipan",
            "Nissan Tиpan",
            "Nissan Ti pan",
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    extractor.extract_car_info(phrase),
                    ("Nissan", "TERRANO"),
                )

    def test_tipan_without_nissan_is_not_terrano(self) -> None:
        extractor = CarBrandExtractor()
        brand, model = extractor.extract_car_info("Volkswagen Типан")
        self.assertEqual(brand, "Volkswagen")
        self.assertNotEqual(model, "TERRANO")

    def test_tenet_t7_stt_tenat_tonat_variants(self) -> None:
        extractor = CarBrandExtractor()
        for phrase in (
            "Тенат Т7",
            "Тонат Т7",
            "Тенат*7",
            "Тонат?7",
            "Чели тига 8ProMaxс",
        ):
            with self.subTest(phrase=phrase):
                expected = ("Chery", "TIGGO 8 PRO MAX") if "8ProMax" in phrase else ("Tenet", "T7")
                self.assertEqual(extractor.extract_car_info(phrase), expected)

    def test_jetour_t1_stt_variants(self) -> None:
        extractor = CarBrandExtractor()
        for phrase in ("Джеторт один.", "Джетоур Т1.", "Jetour T1."):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    extractor.extract_car_info(phrase),
                    ("Jetour", "T1"),
                )

    async def test_step3_recognized_model_advances_without_confirm(self) -> None:
        bot = _Bot()
        bot.car_brand_extractor.extract_car_info.return_value = ("Chery", "TIGGO 7")
        await bot._to_v2_handle_step3_car("чери тигго 7", "чери тигго 7")
        sd = bot.state_machine.service_data
        self.assertIsNone(sd["to_v2_substate"])
        self.assertTrue(sd["car_confirmed"])
        self.assertEqual(sd["car_model"], "Chery Tiggo 7")
        bot.play_wav_or_tts.assert_not_awaited()
        bot._to_v2_advance_linear.assert_awaited_once_with(3)

    async def test_unrecognized_car_raw_goes_to_submission(self) -> None:
        bot = _Bot()
        bot.car_brand_extractor.extract_car_info.return_value = None

        await bot._to_v2_handle_step3_car("неизвестная модель один", "неизвестная модель один")
        await bot._to_v2_handle_step3_car("неизвестная модель два", "неизвестная модель два")

        sd = bot.state_machine.service_data
        self.assertEqual(sd["car_raw"], "неизвестная модель два")
        self.assertEqual(
            bot._to_v2_car_submission_values(sd),
            ("неизвестная", "модель два"),
        )
        bot._to_v2_advance_linear.assert_awaited_once_with(3)

    def test_submission_values_use_raw_stt_when_present(self) -> None:
        bot = _Bot()
        sd = bot.state_machine.service_data
        sd.update(
            {
                "car_raw": "Кия Карнивал.",
                "car_brand": "Kia",
                "car_model": "Kia Carnival",
            }
        )
        self.assertEqual(
            bot._to_v2_car_submission_values(sd),
            ("Кия", "Карнивал"),
        )

    def test_submission_values_normalize_tenet_spoken_digit_variants(self) -> None:
        bot = _Bot()
        bot.car_brand_extractor = CarBrandExtractor()
        cases = (
            ("Тенет, а 4ре. | Четыре.", ("Tenet", "T4")),
            ("Тенат семь", ("Tenet", "T7")),
            ("Тонат восемь", ("Tenet", "T8")),
            ("Tenet девятый", ("Tenet", "T9")),
            ("тэнет т-7", ("Tenet", "T7")),
            # Voice/MAX: марка и модель в разных репликах после переспроса.
            ("Тэнет. | Семь.", ("Tenet", "T7")),
            ("Тэнет | Семь", ("Tenet", "T7")),
            ("tenet семь", ("Tenet", "T7")),
            ("Тенет восемь", ("Tenet", "T8")),
            ("Tenet четыре", ("Tenet", "T4")),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                sd = dict(bot.state_machine.service_data)
                sd.update({"car_raw": raw, "car_brand": "", "car_model": ""})
                self.assertEqual(bot._to_v2_car_submission_values(sd), expected)

    async def test_step3_tenet_then_sem_clarifies_to_t7(self) -> None:
        """Переспрос модели: «Тэнет» → «Семь» → Tenet T7 (не raw «Тенет Семь»)."""
        bot = _Bot()
        bot.car_brand_extractor = CarBrandExtractor()
        await bot._to_v2_handle_step3_car("Тэнет.", "тэнет.")
        self.assertEqual(bot.state_machine.service_data.get("car_brand"), "Tenet")
        self.assertTrue(bot.state_machine.service_data.get("car_await_model"))
        await bot._to_v2_handle_step3_car("Семь.", "семь.")
        sd = bot.state_machine.service_data
        self.assertEqual(sd.get("car_brand"), "Tenet")
        self.assertEqual(bot._to_v2_service_data_model_name(sd), "T7")
        self.assertEqual(bot._to_v2_car_submission_values(sd), ("Tenet", "T7"))
        self.assertTrue(sd.get("car_confirmed"))
        bot._to_v2_advance_linear.assert_awaited_once_with(3)

    async def test_step3_tenet_then_t4l_glues_latin_for_max(self) -> None:
        """Марка «Тенет» → модель «T4L» → MAX: Tenet T4L (не угадывать Chery)."""
        bot = _Bot()
        bot.car_brand_extractor = CarBrandExtractor()
        await bot._to_v2_handle_step3_car("Тенет", "тенет")
        self.assertEqual(bot.state_machine.service_data.get("car_brand"), "Tenet")
        await bot._to_v2_handle_step3_car("T4L", "t4l")
        sd = bot.state_machine.service_data
        self.assertEqual(sd.get("car_brand"), "Tenet")
        self.assertEqual(bot._to_v2_service_data_model_name(sd), "T4L")
        self.assertEqual(bot._to_v2_car_submission_values(sd), ("Tenet", "T4L"))
        bot._to_v2_advance_linear.assert_awaited_once_with(3)

    async def test_step3_chery_then_t4l_glues_to_chery(self) -> None:
        """Та же модель T4L после «Чери» → Chery T4L (не Tenet)."""
        bot = _Bot()
        bot.car_brand_extractor = CarBrandExtractor()
        await bot._to_v2_handle_step3_car("Чери", "чери")
        self.assertEqual(bot.state_machine.service_data.get("car_brand"), "Chery")
        await bot._to_v2_handle_step3_car("T4L", "t4l")
        sd = bot.state_machine.service_data
        self.assertEqual(sd.get("car_brand"), "Chery")
        self.assertEqual(bot._to_v2_service_data_model_name(sd), "T4L")
        self.assertEqual(bot._to_v2_car_submission_values(sd), ("Chery", "T4L"))
        bot._to_v2_advance_linear.assert_awaited_once_with(3)

    async def test_step3_chery_then_t4_glues_to_chery(self) -> None:
        bot = _Bot()
        bot.car_brand_extractor = CarBrandExtractor()
        await bot._to_v2_handle_step3_car("Чери", "чери")
        await bot._to_v2_handle_step3_car("T4", "t4")
        sd = bot.state_machine.service_data
        self.assertEqual(sd.get("car_brand"), "Chery")
        self.assertEqual(bot._to_v2_service_data_model_name(sd), "T4")
        self.assertEqual(bot._to_v2_car_submission_values(sd), ("Chery", "T4"))

    def test_submission_values_normalize_chery_tiggo_variants(self) -> None:
        bot = _Bot()
        bot.car_brand_extractor = CarBrandExtractor()
        cases = (
            ("Cеry 4 Tg Pro. | Чherry 4 тигго.", ("Chery", "Tiggo 4 Pro")),
            ("Cherry 7 pro max", ("Chery", "Tiggo 7 Pro Max")),
            ("Чhr 7 Проo Макс. | Семь.", ("Chery", "Tiggo 7 Pro Max")),
            ("чери тигго восемь про", ("Chery", "Tiggo 8 Pro")),
            ("Chery Tiggo 9", ("Chery", "Tiggo 9")),
            ("Chery Arrizo 8", ("Chery", "Arrizo 8")),
            ("Чери тига се 7", ("Chery", "Tiggo 7")),
            ("Чери тига се 4", ("Chery", "Tiggo 4")),
            ("чери тигго се 7", ("Chery", "Tiggo 7")),
            # Voice/MAX: STT «Чири тига дев» + уточнение «Девять» → Tiggo 9.
            ("Чири тига дев.", ("Chery", "Tiggo 9")),
            ("Чири тига дев. | Девять.", ("Chery", "Tiggo 9")),
            ("чири тигго дев девять", ("Chery", "Tiggo 9")),
            ("чери тигго девять", ("Chery", "Tiggo 9")),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                sd = dict(bot.state_machine.service_data)
                sd.update({"car_raw": raw, "car_brand": "", "car_model": ""})
                self.assertEqual(bot._to_v2_car_submission_values(sd), expected)

    def test_submission_values_normalize_tenet_se_digit_variants(self) -> None:
        bot = _Bot()
        bot.car_brand_extractor = CarBrandExtractor()
        cases = (
            ("Тенет се 7", ("Tenet", "T7")),
            ("Тенет се 4", ("Tenet", "T4")),
            ("Tenet se 7", ("Tenet", "T7")),
        )
        for raw, expected in cases:
            with self.subTest(raw=raw):
                sd = dict(bot.state_machine.service_data)
                sd.update({"car_raw": raw, "car_brand": "", "car_model": ""})
                self.assertEqual(bot._to_v2_car_submission_values(sd), expected)

    async def test_confirm_yes_advances(self) -> None:
        bot = _Bot()
        sd = bot.state_machine.service_data
        sd.update(
            {
                "to_v2_substate": "car_confirm",
                "car_brand": "Chery",
                "car_model": "Chery TIGGO 7",
            }
        )
        await bot._to_v2_handle_car_confirm("да", "да")
        self.assertTrue(sd["car_confirmed"])
        self.assertIsNone(sd["to_v2_substate"])
        bot._to_v2_advance_linear.assert_awaited_once_with(3)

    async def test_confirm_no_reasks_car(self) -> None:
        bot = _Bot()
        bot.car_brand_extractor.extract_car_info.return_value = None
        sd = bot.state_machine.service_data
        sd.update(
            {
                "to_v2_substate": "car_confirm",
                "car_brand": "Chery",
                "car_model": "Chery TIGGO 7",
            }
        )
        await bot._to_v2_handle_car_confirm("нет", "нет")
        bot._to_v2_prompt_step.assert_awaited_with(3)
        bot._to_v2_interrupt_booking_transfer.assert_not_awaited()

    async def test_confirm_no_with_new_model_reprompts(self) -> None:
        bot = _Bot()
        bot.car_brand_extractor.extract_car_info.return_value = ("Chery", "TIGGO 8")
        sd = bot.state_machine.service_data
        sd.update(
            {
                "to_v2_substate": "car_confirm",
                "car_brand": "Chery",
                "car_model": "Chery TIGGO 7",
            }
        )
        await bot._to_v2_handle_car_confirm("нет восьмой", "нет восьмой")
        self.assertEqual(sd["car_model"], "Chery Tiggo 8")
        self.assertEqual(sd["to_v2_substate"], "car_confirm")
        self.assertIn("верно", bot.play_wav_or_tts.await_args[0][1].lower())


    async def test_da_without_model_reasks(self) -> None:
        bot = _Bot()
        sd = bot.state_machine.service_data
        sd.update(
            {
                "to_v2_substate": "car_confirm",
                "car_brand": "Chery",
                "car_model": "Chery",
            }
        )
        await bot._to_v2_handle_car_confirm("да", "да")
        bot._to_v2_prompt_step.assert_awaited_with(3)
        bot._to_v2_advance_linear.assert_not_awaited()

    async def test_confirm_empty_twice_advances(self) -> None:
        bot = _Bot()
        sd = bot.state_machine.service_data
        sd.update(
            {
                "to_v2_substate": "car_confirm",
                "car_brand": "Chery",
                "car_model": "Chery TIGGO 7",
            }
        )
        bot._dialog_empty_kind = "vad_no_audio"
        await bot._to_v2_handle_car_confirm("", "")
        self.assertEqual(sd.get("car_confirm_empty"), 1)
        bot._to_v2_advance_linear.assert_not_awaited()

        await bot._to_v2_handle_car_confirm("", "")
        self.assertTrue(sd["car_confirmed"])
        self.assertIsNone(sd["to_v2_substate"])
        bot._to_v2_advance_linear.assert_awaited_once_with(3)


if __name__ == "__main__":
    unittest.main()
