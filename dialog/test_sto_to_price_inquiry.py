"""Тесты маркеров и блока цены ТО v2."""

import unittest

from dialog.sto_to_price_inquiry import (
    disambiguation_prompt,
    format_price_announcement_tts,
    is_chery_tenet_brand,
    is_price_transfer_confirm_stt,
    is_to_price_inquiry,
    is_to_v2_continue,
    is_to_v2_gate5_unclear,
    is_to_booking_test_trigger_stt,
    is_to_v2_record_choice,
    is_to_v2_service_assistant_request,
    is_service_human_transfer_request,
    parse_disambiguation_answer,
    rubles_amount_to_words_ru,
)
from dialog.sto_to_summary_table import parse_engine_gearbox_cell
from services.voice.voice_phrases import MONTHS_TTS_GENITIVE


class StoToPriceInquiryTest(unittest.TestCase):
    def test_month_tts_accents(self) -> None:
        self.assertEqual(
            MONTHS_TTS_GENITIVE[1:],
            (
                "январ+я", "феврал+я", "м+арта", "апр+еля", "м+ая", "и+юня",
                "и+юля", "+августа", "сентябр+я", "октябр+я", "ноябр+я", "декабр+я",
            ),
        )

    def test_to_price_inquiry(self) -> None:
        self.assertTrue(is_to_price_inquiry("сколько стоит то"))
        self.assertTrue(is_to_price_inquiry("какая стоимость технического обслуживания"))
        self.assertTrue(is_to_price_inquiry("цена ТО1"))
        self.assertTrue(is_to_price_inquiry("почём ТО-2"))
        self.assertTrue(is_to_price_inquiry("узнать цену НТО"))
        self.assertFalse(is_to_price_inquiry("сколько стоит замена колодок"))
        self.assertFalse(is_to_price_inquiry("запишите на сервис"))

    def test_to_booking_price_inquiry(self) -> None:
        from dialog.sto_to_price_inquiry import is_to_booking_price_inquiry

        self.assertTrue(is_to_booking_price_inquiry("А сколько будет стоить товар?"))
        self.assertTrue(is_to_booking_price_inquiry("сколько будет стоить"))
        self.assertTrue(is_to_booking_price_inquiry("Стоит того?"))
        self.assertTrue(is_to_booking_price_inquiry("стоит ТО?"))
        self.assertTrue(is_to_booking_price_inquiry("почём ТО?"))
        self.assertTrue(is_to_booking_price_inquiry("Почем будет ТО?"))
        self.assertFalse(is_to_booking_price_inquiry("запишите на сервис"))

    def test_continue_vs_assistant(self) -> None:
        self.assertTrue(is_to_v2_continue("дальше"))
        self.assertFalse(is_to_v2_service_assistant_request("дальше"))
        self.assertTrue(is_to_v2_service_assistant_request("диспетчер"))
        self.assertTrue(is_to_v2_service_assistant_request("менеджер"))

    def test_human_transfer_slot_phrases(self) -> None:
        self.assertTrue(is_service_human_transfer_request("переключите на диспетчера"))
        self.assertTrue(is_service_human_transfer_request("дайте на человека"))
        self.assertTrue(is_service_human_transfer_request("переведите на ассистента"))
        self.assertTrue(is_service_human_transfer_request("Ассистент, запишите меня."))
        self.assertTrue(
            is_service_human_transfer_request(
                "А можно соединить с Казаковым Евгением, настерприемщик?"
            )
        )
        self.assertTrue(is_service_human_transfer_request("Настер."))
        for phrase in ("Консультация", "консультант", "консультацию", "консультанта"):
            with self.subTest(phrase=phrase):
                self.assertTrue(is_service_human_transfer_request(phrase))
        self.assertTrue(is_service_human_transfer_request("Переведи."))
        self.assertTrue(is_service_human_transfer_request("Преведи."))
        self.assertTrue(is_service_human_transfer_request("Периведи."))
        self.assertTrue(is_service_human_transfer_request("Дайте."))
        self.assertTrue(is_service_human_transfer_request("Вести."))
        self.assertTrue(is_service_human_transfer_request("Привести."))
        self.assertFalse(is_service_human_transfer_request("вести машину на сервис"))
        self.assertFalse(is_service_human_transfer_request("давайте на 5 июля"))
        self.assertFalse(is_service_human_transfer_request("дальше"))

    def test_record_choice_stt_sama(self) -> None:
        self.assertTrue(is_to_v2_record_choice("сама"))
        self.assertTrue(is_to_v2_record_choice("Тама."))
        self.assertTrue(is_to_v2_record_choice("запиши сама"))
        self.assertTrue(is_to_v2_record_choice("Записать."))
        self.assertTrue(is_to_v2_record_choice("Записывай."))
        self.assertTrue(is_to_v2_record_choice("Запис."))
        self.assertTrue(is_to_v2_record_choice("Пиши."))
        self.assertTrue(is_to_v2_record_choice("пишите"))
        self.assertTrue(is_to_v2_record_choice("Давай."))
        self.assertTrue(is_to_v2_record_choice("Авай"))
        self.assertTrue(is_to_v2_record_choice("Ама."))

    def test_booking_test_trigger_stt_cases(self) -> None:
        for phrase in (
            "Австралия.",
            "австралию",
            "Австрали.",
            "australia",
            "нужна Австралия для записи",
            "Трали я.",
            "Страли я",
            "Сале.",
            "австрал",
        ):
            self.assertTrue(is_to_booking_test_trigger_stt(phrase), phrase)
            self.assertTrue(is_to_v2_record_choice(phrase), phrase)
        self.assertFalse(is_to_booking_test_trigger_stt("автосалон"))
        self.assertFalse(is_to_booking_test_trigger_stt(""))

    def test_gate5_unclear(self) -> None:
        self.assertTrue(is_to_v2_gate5_unclear("Алло."))
        self.assertTrue(is_to_v2_gate5_unclear("Алле"))
        self.assertFalse(is_to_v2_gate5_unclear("дальше"))
        self.assertFalse(is_to_v2_gate5_unclear("диспетчер"))

    def test_chery_tenet_brand(self) -> None:
        self.assertTrue(is_chery_tenet_brand("Chery"))
        self.assertTrue(is_chery_tenet_brand("Тенет"))
        self.assertTrue(is_chery_tenet_brand("Tenet"))
        self.assertFalse(is_chery_tenet_brand("Volkswagen"))

    def test_tenet_stt_aliases_extract_and_price_brand(self) -> None:
        from dialog.car_brand_extractor import CarBrandExtractor

        ex = CarBrandExtractor()
        for phrase in ("Тэнт Т4", "тент т4", "Tenet T4", "тинет т4"):
            info = ex.extract_car_info(phrase)
            self.assertIsNotNone(info, phrase)
            brand, _model = info
            self.assertTrue(is_chery_tenet_brand(brand), phrase)

    def test_chery_tiggo_stt_tikslim(self) -> None:
        from dialog.car_brand_extractor import CarBrandExtractor

        info = CarBrandExtractor().extract_car_info("Чери-тикслим")
        self.assertIsNotNone(info)
        brand, model = info
        self.assertEqual(brand, "Chery")
        self.assertEqual(model, "ТИГГО 7")

    def test_price_transfer_confirm_stt(self) -> None:
        self.assertTrue(is_price_transfer_confirm_stt("да."))
        self.assertTrue(is_price_transfer_confirm_stt("Превести."))
        self.assertTrue(is_price_transfer_confirm_stt("переведите"))
        self.assertFalse(is_price_transfer_confirm_stt(""))
        self.assertFalse(is_price_transfer_confirm_stt("нет"))
        self.assertFalse(is_price_transfer_confirm_stt("Идите."))

    def test_parse_engine_gearbox(self) -> None:
        spec = parse_engine_gearbox_cell("1.5 MT")
        self.assertEqual(spec.volume_l, 1.5)
        self.assertEqual(spec.transmission, "mt")

    def test_disambiguation_answer(self) -> None:
        self.assertEqual(parse_disambiguation_answer("автомат", "transmission"), "at")
        self.assertEqual(parse_disambiguation_answer("Атомат.", "transmission"), "at")
        self.assertEqual(parse_disambiguation_answer("ат.", "transmission"), "at")
        self.assertEqual(parse_disambiguation_answer("автамат", "transmission"), "at")
        self.assertEqual(parse_disambiguation_answer("AT", "transmission"), "at")
        self.assertEqual(parse_disambiguation_answer("полный", "drive"), "awd")
        self.assertEqual(parse_disambiguation_answer("один и шесть", "volume_l"), 1.6)
        self.assertEqual(parse_disambiguation_answer("два", "volume_l"), 2.0)
        self.assertEqual(parse_disambiguation_answer("два литра", "volume_l"), 2.0)
        self.assertEqual(parse_disambiguation_answer("два ноль", "volume_l"), 2.0)
        # Наблюдавшиеся короткие ошибки STT допустимы только в контексте volume_l.
        self.assertEqual(parse_disambiguation_answer("тро.", "volume_l"), 2.0)
        self.assertEqual(parse_disambiguation_answer("ва", "volume_l"), 2.0)

    def test_rubles_amount_words(self) -> None:
        self.assertIn("семнадцать тысяч", rubles_amount_to_words_ru(17773))
        self.assertNotIn("17773", rubles_amount_to_words_ru(17773))
        # 17773 → …три рубля (не «рублей»)
        self.assertTrue(rubles_amount_to_words_ru(17773).endswith("рубля"))
        self.assertTrue(rubles_amount_to_words_ru(1).endswith("рубль"))
        self.assertTrue(rubles_amount_to_words_ru(21).endswith("рубль"))
        self.assertTrue(rubles_amount_to_words_ru(2).endswith("рубля"))
        self.assertTrue(rubles_amount_to_words_ru(5).endswith("рублей"))
        self.assertTrue(rubles_amount_to_words_ru(11).endswith("рублей"))
        self.assertTrue(rubles_amount_to_words_ru(1012).endswith("рублей"))
        self.assertTrue(rubles_amount_to_words_ru(1000).endswith("рублей"))

    def test_price_announcement_no_digits(self) -> None:
        t = format_price_announcement_tts("10 т.км", 17773.45)
        self.assertNotIn("17773", t)
        self.assertNotIn("т.км", t.lower())
        self.assertIn("рубля", t)
        self.assertIn("семнадцать тысяч", t)

    def test_format_to_price_for_lead(self) -> None:
        from dialog.sto_to_price_inquiry import format_to_price_for_lead

        self.assertEqual(
            format_to_price_for_lead(
                {
                    "price_announced": True,
                    "price_rub": 17773.45,
                    "to_label": "ТО-2",
                    "price_block": {
                        "transmission": "at",
                        "volume_l": 1.5,
                        "drive": "fwd",
                    },
                }
            ),
            "комплектация: КПП автомат, объём 1.5 л, передний привод; стоимость ТО-2: 17773 руб",
        )
        self.assertEqual(
            format_to_price_for_lead({"price_rub": 1000}),
            "цена не называлась",
        )
        self.assertEqual(format_to_price_for_lead(None), "цена не называлась")
        self.assertEqual(
            format_to_price_for_lead({"price_announced": True, "price_rub": 20000}),
            "стоимость ТО: 20000 руб",
        )

    def test_normalize_model_for_sto_lookup(self) -> None:
        from dialog.sto_to_price_inquiry import _normalize_model_for_sto_lookup

        self.assertEqual(
            _normalize_model_for_sto_lookup("Chery", "Chery ТИГГО 7"),
            "Chery TIGGO 7",
        )
        self.assertEqual(
            _normalize_model_for_sto_lookup("Chery", "ТИГГО 7"),
            "Chery TIGGO 7",
        )

    def test_auto_fill_drive_when_sole_value(self) -> None:
        from dialog.sto_to_price_inquiry import _auto_fill_sole_disambig_choices

        eg_col = "engine"
        candidates = [
            {"engine": "1.5 MT AWD"},
            {"engine": "1.6 AT AWD"},
        ]
        chosen: dict = {}
        _auto_fill_sole_disambig_choices(candidates, eg_col, chosen)
        self.assertEqual(chosen.get("drive"), "awd")
        self.assertIsNone(chosen.get("transmission"))

    def test_engine_gearbox_params_for_1c_defaults(self) -> None:
        from dialog.sto_to_price_inquiry import engine_gearbox_params_for_1c

        self.assertEqual(
            engine_gearbox_params_for_1c({}),
            {"transmission": "0", "engine_volume": "0", "drive": "0"},
        )

    def test_engine_gearbox_params_for_1c_from_price_block(self) -> None:
        from dialog.sto_to_price_inquiry import engine_gearbox_params_for_1c

        self.assertEqual(
            engine_gearbox_params_for_1c({
                "price_block": {
                    "transmission": "at",
                    "volume_l": 1.6,
                    "drive": "fwd",
                },
            }),
            {"transmission": "at", "engine_volume": "1.6", "drive": "fwd"},
        )

    def test_disambiguation_wav_mapping(self) -> None:
        wav, _ = disambiguation_prompt("transmission", repeat=False)
        self.assertEqual(wav, "35_to_v2_price_ask_transmission.wav")
        wav_r, _ = disambiguation_prompt("volume_l", repeat=True)
        self.assertEqual(wav_r, "39_to_v2_price_repeat_volume.wav")


if __name__ == "__main__":
    unittest.main()
