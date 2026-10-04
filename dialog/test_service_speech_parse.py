import unittest

from dialog.service_speech_parse import (
    mileage_km_for_1c,
    mileage_label_for_storage_and_notify,
    normalize_car_model_for_1c,
    parse_car_year_from_speech,
    parse_mileage_km_from_speech,
    parse_mileage_km_from_work_list,
    resolve_mileage_km_for_booking,
)


class TestServiceSpeechParse(unittest.TestCase):
    def test_year_not_mileage(self):
        self.assertIsNone(parse_mileage_km_from_speech("2025", car_year="2025"))
        self.assertIsNone(mileage_km_for_1c("2025", car_year="2025"))

    def test_mileage_label_strips_stt_phrase(self):
        phrase = "Как же у вас там всё сложно. 14 000. Проще через этот интернет записаться."
        self.assertEqual(
            mileage_label_for_storage_and_notify(
                mileage_norm="14000",
                mileage_raw=phrase,
            ),
            "14000",
        )
        self.assertEqual(
            mileage_label_for_storage_and_notify(
                mileage_norm=None,
                mileage_raw=phrase,
            ),
            "14000",
        )
        self.assertEqual(
            parse_mileage_km_from_speech(phrase, car_year="2024"),
            "14000",
        )

    def test_year_and_mileage(self):
        self.assertEqual(
            parse_mileage_km_from_speech("2025 год, 30 000", car_year="2025"),
            "30000",
        )

    def test_thousands(self):
        self.assertEqual(
            parse_mileage_km_from_speech("2025, 30 тысяч", car_year="2025"),
            "30000",
        )

    def test_spoken_mileage_four_three_hundred(self):
        self.assertEqual(
            parse_mileage_km_from_speech("четыре триста", car_year="2025"),
            "4300",
        )

    def test_mileage_from_work_list(self):
        self.assertEqual(
            parse_mileage_km_from_work_list("Техническое обслуживание 30 000.", car_year="2025"),
            "30000",
        )
        self.assertEqual(
            parse_mileage_km_from_work_list("Того 30 000.", car_year="2025"),
            "30000",
        )

    def test_resolve_mileage_prefers_step4(self):
        self.assertEqual(
            resolve_mileage_km_for_booking(
                mileage="12000",
                work_list="ТО 30 000",
                car_year="2025",
            ),
            "12000",
        )

    def test_resolve_mileage_from_work_list(self):
        self.assertEqual(
            resolve_mileage_km_for_booking(
                mileage=None,
                work_list="Техническое обслуживание 30 000.",
                car_year="2025",
            ),
            "30000",
        )

    def test_normalize_model_for_1c(self):
        self.assertEqual(
            normalize_car_model_for_1c("Tenet", "Tenet Т4"),
            "Т4",
        )

    def test_car_year_stt_pyaty(self):
        self.assertEqual(parse_car_year_from_speech("Пяты 10 000"), "2025")
        self.assertEqual(
            parse_mileage_km_from_speech("Пяты 10 000", car_year="2025"),
            "10000",
        )

    def test_car_year_ordinal_25(self):
        self.assertEqual(parse_car_year_from_speech("25-й год, 10 тысяч"), "2025")
        self.assertEqual(parse_car_year_from_speech("двадцать пятый, 10000"), "2025")

    def test_car_year_takes_last_candidate_in_phrase(self):
        raw = "Не знаю, Двадцать-двадцать третье, наверное, двадцать четвёртый. Ой, блин. Господи."
        self.assertEqual(parse_car_year_from_speech(raw), "2024")

    def test_car_year_and_mileage_real_stt_025(self):
        raw = "025-. 10 000 км."
        year = parse_car_year_from_speech(raw)
        self.assertEqual(year, "2025")
        self.assertEqual(
            parse_mileage_km_from_speech(raw, car_year=year),
            "10000",
        )

    def test_normalize_stt_booking_date_2roe(self):
        from dialog.service_speech_parse import normalize_stt_booking_date_text
        from dialog.date_parser import DateParser
        from datetime import datetime

        for raw in ("На 2рое июля.", "На 2ро июля."):
            norm = normalize_stt_booking_date_text(raw)
            self.assertIn("второе", norm)
            d = DateParser().parse_date(norm, datetime(2026, 6, 23))
            self.assertIsNotNone(d)
            self.assertEqual(d.month, 7)
            self.assertEqual(d.day, 2)

    def test_all_ordinals_normalize_split_or_missing_prefix_before_month(self):
        from datetime import datetime

        from dialog.date_parser import DateParser
        from dialog.service_speech_parse import (
            _ORDINAL_DIGIT_STT,
            _ORDINAL_PREFIX_STT_VARIANTS,
            normalize_stt_booking_date_text,
        )

        parser = DateParser()
        ref = datetime(2026, 7, 26)
        supported_variants = {variant for variant, _ in _ORDINAL_PREFIX_STT_VARIANTS}
        for day_text, canonical in _ORDINAL_DIGIT_STT.items():
            first_word, *tail = canonical.split()
            suffix = f" {' '.join(tail)}" if tail else ""
            variants = (
                f"{first_word[:1]} {first_word[1:]}{suffix}",
                f"{first_word[:2]} {first_word[2:]}{suffix}",
                f"{first_word[1:]}{suffix}",
                f"{first_word[2:]}{suffix}",
            )
            for variant in variants:
                if variant not in supported_variants:
                    continue
                with self.subTest(day=day_text, variant=variant):
                    norm = normalize_stt_booking_date_text(
                        f"{variant} августа в 9:00"
                    )
                    parsed = parser.parse_date(norm, ref)
                    self.assertIsNotNone(parsed)
                    self.assertEqual(parsed.month, 8)
                    self.assertEqual(parsed.day, int(day_text))

    def test_valid_seventeenth_is_not_guessed_as_truncated_eighteenth(self):
        from datetime import datetime

        from dialog.date_parser import DateParser

        parsed = DateParser().parse_date(
            "семнадцатое августа",
            datetime(2026, 7, 26),
        )
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.day, 17)

    def test_ordinal_prefix_is_not_guessed_without_date_context(self):
        from dialog.service_speech_parse import normalize_stt_booking_date_text

        self.assertEqual(
            normalize_stt_booking_date_text("рвое колесо"),
            "рвое колесо",
        )

    def test_normalize_stt_tyulya(self):
        from dialog.service_speech_parse import normalize_stt_booking_date_text
        from dialog.date_parser import DateParser
        from datetime import datetime

        norm = normalize_stt_booking_date_text("Ты Юля.")
        d = DateParser().parse_date(norm, datetime(2026, 6, 23))
        self.assertEqual(d.day, 9)
        self.assertEqual(d.month, 7)

    def test_normalize_stt_5toe_iulya(self):
        from dialog.service_speech_parse import normalize_stt_booking_date_text
        from dialog.date_parser import DateParser
        from datetime import datetime

        ref = datetime(2026, 6, 23)
        for raw in ("На 5тое июля.", "5тое июля"):
            norm = normalize_stt_booking_date_text(raw)
            d = DateParser().parse_date(norm, ref)
            self.assertEqual(d.day, 5, raw)
            self.assertEqual(d.month, 7, raw)

    def test_normalize_stt_15e_oktyabrya(self):
        from dialog.service_speech_parse import normalize_stt_booking_date_text
        from dialog.date_parser import DateParser
        from datetime import datetime

        ref = datetime(2026, 10, 1, 10, 54)
        norm = normalize_stt_booking_date_text("Ой, например, 15е октября.")
        parsed = DateParser().parse_date(norm, ref)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.day, 15)
        self.assertEqual(parsed.month, 10)

    def test_normalize_stt_avu_to_avgusta(self):
        from dialog.service_speech_parse import normalize_stt_booking_date_text
        from dialog.date_parser import DateParser
        from datetime import datetime

        norm = normalize_stt_booking_date_text("На пятнадцатое аву.")
        self.assertIn("августа", norm.lower())
        d = DateParser().parse_date(norm, datetime(2026, 8, 7))
        self.assertIsNotNone(d)
        self.assertEqual(d.day, 15)
        self.assertEqual(d.month, 8)

    def test_normalize_stt_2tsat_devyaye_chislo(self):
        from datetime import datetime

        from dialog.date_parser import DateParser
        from dialog.service_speech_parse import normalize_stt_booking_date_text

        ref = datetime(2026, 8, 12)
        for raw in ("2цать девя-е число", "двадцать девя-е число"):
            norm = normalize_stt_booking_date_text(raw)
            parsed = DateParser().parse_date(norm, ref)
            self.assertIsNotNone(parsed, raw)
            self.assertEqual(parsed.day, 29, raw)
            self.assertEqual(parsed.month, 8, raw)

    def test_normalize_stt_9yatoe_iulya(self):
        from dialog.service_speech_parse import normalize_stt_booking_date_text
        from dialog.date_parser import DateParser
        from datetime import datetime

        ref = datetime(2026, 6, 23)
        norm = normalize_stt_booking_date_text("9ятое июля.")
        d = DateParser().parse_date(norm, ref)
        self.assertEqual(d.day, 9)
        self.assertEqual(d.month, 7)

    def test_normalize_stt_vyatoe_iulya(self):
        from dialog.service_speech_parse import normalize_stt_booking_date_text
        from dialog.date_parser import DateParser
        from datetime import datetime

        ref = datetime(2026, 6, 23)
        norm = normalize_stt_booking_date_text("вятое июля.")
        d = DateParser().parse_date(norm, ref)
        self.assertEqual(d.day, 9)
        self.assertEqual(d.month, 7)
        norm_ptoe = normalize_stt_booking_date_text("Птое.")
        self.assertEqual(norm_ptoe.lower(), "девятое.")

    def test_slot_day9_hint_remaps_pyatoe(self):
        from dialog.service_speech_parse import (
            normalize_stt_booking_date_text,
            slot_day9_stt_markers,
        )
        from dialog.date_parser import DateParser
        from datetime import datetime

        ref = datetime(2026, 6, 23)
        self.assertTrue(slot_day9_stt_markers("вятое июля."))
        norm = normalize_stt_booking_date_text("Пятое июля.", slot_day9_hint=True)
        d = DateParser().parse_date(norm, ref, slot_day9_hint=True)
        self.assertEqual(d.day, 9)
        norm2 = normalize_stt_booking_date_text("На 5 июля.", slot_day9_hint=True)
        d2 = DateParser().parse_date(norm2, ref, slot_day9_hint=True)
        self.assertEqual(d2.day, 9)

    def test_desyatoe_without_month_only_with_day9_hint(self):
        from dialog.service_speech_parse import normalize_stt_booking_date_text
        from dialog.date_parser import DateParser
        from datetime import datetime

        ref = datetime(2026, 6, 23)
        # «десятое» всегда 10-е, даже если раньше в диалоге путали с 9-м.
        norm = normalize_stt_booking_date_text("На десятое.", slot_day9_hint=True)
        d = DateParser().parse_date(f"{norm} июля", ref, slot_day9_hint=True)
        self.assertEqual(d.day, 10)
        norm10 = normalize_stt_booking_date_text("На десятое.")
        d10 = DateParser().parse_date(f"{norm10} июля", ref)
        self.assertEqual(d10.day, 10)

    def test_desyatoe_never_becomes_ninth(self):
        from dialog.service_speech_parse import prepare_booking_date_stt
        from dialog.date_parser import DateParser
        from datetime import datetime

        ref = datetime(2026, 6, 18)
        sd = {"slot_day9_hint": True}
        for raw in (
            "на десятое",
            "На десятое.",
            "десятое июля",
            "на десятое июля",
            "10ое июля",
            "10е июля",
        ):
            norm, d9, d10 = prepare_booking_date_stt(raw, sd)
            chunk = norm if "июл" in norm.lower() else f"{norm} июля"
            d = DateParser().parse_date(chunk, ref, slot_day9_hint=d9, slot_day10_hint=d10)
            self.assertIsNotNone(d, raw)
            self.assertEqual(d.day, 10, raw)
            self.assertFalse(sd.get("slot_day9_hint"), raw)

    def test_slot_day9_markers_not_on_desyatoe(self):
        from dialog.service_speech_parse import slot_day9_stt_markers, slot_day10_stt_markers

        for raw in ("десятое июля", "на десятое", "десятого июля"):
            self.assertTrue(slot_day10_stt_markers(raw), raw)
            self.assertFalse(slot_day9_stt_markers(raw), raw)

    def test_na_desyatoe_is_tenth(self):
        from dialog.service_speech_parse import (
            normalize_stt_booking_date_text,
            slot_day10_stt_markers,
        )
        from dialog.date_parser import DateParser
        from datetime import datetime

        ref = datetime(2026, 6, 23)
        raw = "А на десятое."
        self.assertTrue(slot_day10_stt_markers(raw))
        norm = normalize_stt_booking_date_text(raw, slot_day10_hint=True)
        d = DateParser().parse_date(f"{norm} июля", ref, slot_day10_hint=True)
        self.assertEqual(d.day, 10)

    def test_desyatoe_iulya_stays_tenth(self):
        from dialog.service_speech_parse import (
            normalize_stt_booking_date_text,
            slot_day10_stt_markers,
        )
        from dialog.date_parser import DateParser
        from datetime import datetime

        ref = datetime(2026, 6, 23)
        raw = "десятое июля"
        self.assertTrue(slot_day10_stt_markers(raw))
        norm = normalize_stt_booking_date_text(raw, slot_day10_hint=True)
        d = DateParser().parse_date(norm, ref, slot_day10_hint=True)
        self.assertEqual(d.day, 10)

    def test_10oe_iulya_stt(self):
        from dialog.service_speech_parse import prepare_booking_date_stt
        from dialog.date_parser import DateParser
        from datetime import datetime

        ref = datetime(2026, 6, 23)
        sd: dict = {}
        for raw in ("10ое июля", "на 10ое июля", "10ое июля.", "10е июля"):
            norm, d9, d10 = prepare_booking_date_stt(raw, sd)
            d = DateParser().parse_date(norm, ref, slot_day9_hint=d9, slot_day10_hint=d10)
            self.assertEqual(d.day, 10, raw)
            self.assertEqual(d.month, 7, raw)

    def test_parse_pyatoe_iulya_after_noise(self):
        from dialog.date_parser import DateParser
        from datetime import datetime

        d = DateParser().parse_date(
            "нужно мне. пятое июля.",
            datetime(2026, 6, 23),
        )
        self.assertEqual(d.day, 5)
        self.assertEqual(d.month, 7)

    def test_normalize_stt_tridcat_1er_august(self):
        from datetime import datetime

        from dialog.date_parser import DateParser
        from dialog.service_speech_parse import normalize_stt_booking_date_text

        norm = normalize_stt_booking_date_text("Тридцать 1ер августа")
        parsed = DateParser().parse_date(norm, datetime(2026, 8, 5))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.day, 31)
        self.assertEqual(parsed.month, 8)

    def test_ordinal_only_with_proposed_month(self):
        from dialog.date_parser import DateParser
        from datetime import datetime

        d = DateParser().parse_date("пятое июля", datetime(2026, 6, 23))
        self.assertEqual(d.day, 5)
        self.assertEqual(d.month, 7)

    def test_year_205_and_mileage(self):
        raw = "205-й год — 10 000."
        year = parse_car_year_from_speech(raw)
        self.assertEqual(year, "2025")
        self.assertEqual(
            parse_mileage_km_from_speech(raw, car_year=year),
            "10000",
        )


if __name__ == "__main__":
    unittest.main()
