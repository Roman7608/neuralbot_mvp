"""Тесты STT-нормализации Chery / Tenet для голосового бота."""

import unittest

from dialog.car_brand_extractor import CarBrandExtractor
from dialog.chery_tenet_stt_normalize import normalize_chery_tenet_car_stt


class CheryTenetSttNormalizeTest(unittest.TestCase):
    @staticmethod
    def _model_contains(model: str | None, part: str) -> bool:
        return part.upper() in (model or "").upper()

    def _extract(self, phrase: str):
        return CarBrandExtractor().extract_car_info(phrase)

    def test_voice_extras(self) -> None:
        for raw, need_brand, need_model_part in (
            ("Сери иг7 Промакс, полный привод.", "Chery", "PRO MAX"),
            ("Шери иг 4ро.", "Chery", "4 PRO"),
            ("Шери иг7ро.", "Chery", "7 PRO"),
            ("Шери иг8ро.", "Chery", "8 PRO"),
            ("Шери иг9ро.", "Chery", "9 PRO"),
            ("Чери Тигр 7 Промарс, полный привод.", "Chery", "PRO MAX"),
            ("Чери-тикслим", "Chery", "7"),
            ("Черитик гасим.", "Chery", "7"),
            ("чери тиг 7", "Chery", "7"),
            ("Черри тикгасин", "Chery", "7"),
            ("чери тикгасен", "Chery", "7"),
            ("Чери Тигга 7", "Chery", "7"),
            ("чери тигга сем", "Chery", "7"),
            ("Чери Тигга с7.", "Chery", "7"),
            ("Черри Тигга 7.", "Chery", "7"),
            ("Чери тигга сем.", "Chery", "7"),
            ("чери тигры сем", "Chery", "7"),
            ("Чери игга семь", "Chery", "7"),
            ("Чери Пигатин", "Chery", "7"),
            ("Эль Кигга с7.", "Chery", "7"),
            ("Кигга 7", "Chery", "7"),
            ("чери кигга 7", "Chery", "7"),
            ("Терига 8", "Chery", "8"),
            ("восьмерка гибрид", "Chery", "8"),
            ("Чирри восьмёрка", "Chery", "8"),
            ("чирри 8", "Chery", "8"),
            ("Чечерег 8еь", "Chery", "8"),
            ("Четыре г восемь", "Chery", "8"),
            ("Терига 8 Pro", "Chery", "8"),
            ("Чери г4", "Chery", "4"),
            ("чери г 4", "Chery", "4"),
            ("Chery g4", "Chery", "4"),
            ("Cрри Tгo 7", "Chery", "7"),
            ("CheriTga 7PrMax", "Chery", "7"),
            ("Черри, иgа 7 Пr Макс.", "Chery", "PRO MAX"),
            ("CherTg 4 New", "Chery", "4"),
            ("Chr Te7PMак", "Chery", "7"),
            ("Чирокс? ильтромакс", "Chery", "PRO MAX"),
            ("Чери чига 7л", "Chery", "7 L"),
            ("Чери эээ 7 л", "Chery", "7 L"),
            ("ЧереЧга", "Chery", "TIGGO"),
            ("чере", "Chery", None),
        ):
            info = self._extract(raw)
            self.assertIsNotNone(info, raw)
            brand, model = info
            self.assertEqual(brand, need_brand, raw)
            if need_model_part is None:
                continue
            self.assertTrue(self._model_contains(model, need_model_part), f"{raw!r} -> {model!r}")

    def test_tiggo_tts_stress(self) -> None:
        tts = CarBrandExtractor().format_for_tts("Chery", "ТИГГО 7")
        self.assertIn("Т+игго", tts)
        self.assertNotIn("ТИГГО", tts)

    def test_tenet_t4_tts(self) -> None:
        tts = CarBrandExtractor().format_for_tts("Tenet", "T4")
        self.assertIn("Т+енет", tts)
        self.assertIn("T чет+ыре", tts)

    def test_tenet_t7_voice_stt_aliases(self) -> None:
        for raw in (
            "Тенот Т7",
            "тэn т7",
            "Tenet T7",
            "тенет семь",
            "tenet семь",
            "Тэнет. | Семь.",
            "NтT7",
            "nтt 7",
            "Tэнт T7",
            "тэнт т7",
            "тенэт т7",
            "тенет т7",
        ):
            with self.subTest(raw=raw):
                info = self._extract(raw)
                self.assertIsNotNone(info, raw)
                brand, model = info
                self.assertEqual(brand, "Tenet", raw)
                self.assertEqual(model, "T7", raw)

    def test_tenet_brand_aliases_without_model(self) -> None:
        for raw in ("Тэнт", "тенэт", "Tенет", "тенет"):
            with self.subTest(raw=raw):
                info = self._extract(raw)
                self.assertIsNotNone(info, raw)
                brand, _model = info
                self.assertEqual(brand, "Tenet", raw)

    def test_chiri_kiga_7l_is_chery_tiggo_7_l(self) -> None:
        for raw in (
            "Чири Кига 7л",
            "Чири Кига 7 л",
            "Чири Кига 7эл",
            "Чири Кига 7 эль",
            "Чири Кига 7ль",
            "Chery Tiggo 7 L",
            "Черитига л7",
            "Черитига эл7",
            "Черитига л7 L-7",
        ):
            with self.subTest(raw=raw):
                info = self._extract(raw)
                self.assertEqual(info, ("Chery", "TIGGO 7 L"))

    def test_cheretiga_7promarket_maps_to_tiggo_7_pro(self) -> None:
        for raw in ("Черетиgа 7Proмаaрkеt", "черетига 7promarket"):
            with self.subTest(raw=raw):
                info = self._extract(raw)
                self.assertIsNotNone(info, raw)
                brand, model = info
                self.assertEqual(brand, "Chery", raw)
                self.assertEqual(model, "TIGGO 7 PRO", raw)

    def test_eri_iga_kompromat_variants_map_to_tiggo_7_pro_max(self) -> None:
        for raw in ("ери ига компромат", "черевзигасонпромат"):
            with self.subTest(raw=raw):
                info = self._extract(raw)
                self.assertIsNotNone(info, raw)
                brand, model = info
                self.assertEqual(brand, "Chery", raw)
                self.assertEqual(model, "TIGGO 7 PRO MAX", raw)

    def test_analytics_golden_phrases(self) -> None:
        cases = (
            ("47руга", "Chery", "7"),
            ("Чериига 7", "Chery", "7"),
            ("ChrTeg 7Pr", "Chery", "7"),
            ("ТН34", "Tenet", "T4"),
            ("ТМ 4", "Tenet", "T4"),
            ("че Сероммакс", "Chery", "7"),
            ("Чг7 Эльгус", "Chery", "7"),
            ("C4рогус", "Chery", "4"),
            ("CT 4rogу", "Chery", "4"),
            ("ЧrTG 4 прога", "Chery", "4"),
            ("Tenet T4 Pro", "Tenet", "T4"),
            ("чери тигго 8 про макс", "Chery", "8"),
            ("Терига 8", "Chery", "8"),
            ("Cрри Tгo 7", "Chery", "7"),
            ("CheriTga 7PrMax", "Chery", "7"),
            ("CherTg 4 New", "Chery", "4"),
            ("срри тго 7", "Chery", "7"),
            ("Chr Te7PMак", "Chery", "7"),
            ("Чирокс? ильтромакс", "Chery", "PRO MAX"),
            ("ЧереЧга", "Chery", "TIGGO"),
            # Voice: «Чири тига дев» / «тигго девять» → Tiggo 9.
            ("Чири тига дев.", "Chery", "9"),
            ("чири тигго дев девять", "Chery", "9"),
            ("чери тигго девять", "Chery", "9"),
        )
        for raw, need_brand, need_model_part in cases:
            info = self._extract(raw)
            self.assertIsNotNone(info, raw)
            brand, model = info
            self.assertEqual(brand, need_brand, raw)
            self.assertTrue(
                self._model_contains(model, need_model_part),
                f"{raw!r} -> brand={brand!r} model={model!r}",
            )

    def test_normalize_arizzo_stt_phrases(self) -> None:
        from dialog.chery_tenet_stt_normalize import normalize_chery_tenet_car_stt

        for raw, need in (("Рiза 8", "arrizo 8"), ("Chr Tg 7PrMaрк", "тигго 7")):
            norm = normalize_chery_tenet_car_stt(raw)
            self.assertIn(need, norm, raw)

    def test_normalize_returns_lowercase(self) -> None:
        norm = normalize_chery_tenet_car_stt("  ChrTeg 7Pr  ")
        self.assertEqual(norm, norm.lower())
        self.assertIn("chery", norm)
        self.assertIn("tiggo", norm)


if __name__ == "__main__":
    unittest.main()
