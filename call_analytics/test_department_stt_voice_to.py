"""STT → ассистент сервиса: пройти ТУ, тхобслуж, тхбслуж; CRM «нажмите кнопку 5» → администратор."""
import unittest

from dialog.department_stt_normalize import (
    is_silent_immediate_admin_callback,
    normalize_department_stt_text,
    stt_implies_insurance_admin_department,
    stt_implies_parts_department,
    stt_implies_service_first_to_reply,
    stt_implies_used_cars_department,
)


class TestDepartmentSttVoiceTo(unittest.TestCase):
    def test_proiti_tu_normalizes_to_tech_service(self):
        self.assertEqual(
            normalize_department_stt_text("хочу пройти ту"),
            "хочу техобслуживание",
        )

    def test_txobsluzh_stt_fragment(self):
        self.assertEqual(
            normalize_department_stt_text("нужно тхобслуж"),
            "нужно техобслуживание",
        )

    def test_txbsluzh_stt_fragment(self):
        self.assertEqual(
            normalize_department_stt_text("записаться тхбслуж"),
            "записаться техобслуживание",
        )

    def test_na_tu_stt(self):
        self.assertEqual(
            normalize_department_stt_text("хочу записаться на ту"),
            "хочу записаться техобслуживание",
        )
        self.assertEqual(
            normalize_department_stt_text("записатьсянату"),
            "записаться техобслуживание",
        )

    def test_razval_s_khozhdeniya_stt(self):
        self.assertEqual(
            normalize_department_stt_text("развал с хождения"),
            "развал схождение",
        )
        self.assertEqual(
            normalize_department_stt_text("на развал схождения записаться"),
            "на развал схождение записаться",
        )

    def test_insurance_routes_to_admin(self):
        for phrase in ("страхование", "страховой отдел", "отдел страхования", "страховка", "каско"):
            with self.subTest(phrase=phrase):
                self.assertTrue(stt_implies_insurance_admin_department(phrase))
                self.assertEqual(normalize_department_stt_text(phrase), "администратор")
        self.assertFalse(stt_implies_insurance_admin_department("направление от страховой"))
        self.assertFalse(
            stt_implies_insurance_admin_department(
                "переключите на кузовной цех по направлению от страховой"
            )
        )

    def test_used_cars_bu_phrases(self):
        phrases = (
            "бэу авто",
            "беу машины",
            "отдел продаж бу машин",
            "отдел продаж бэу",
            "отдел продаж бу-машин",
            "б у машины",
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertTrue(stt_implies_used_cars_department(phrase), phrase)
                self.assertEqual(
                    normalize_department_stt_text(phrase),
                    "отдел автомобилей с пробегом",
                    phrase,
                )
        self.assertFalse(stt_implies_used_cars_department("отдел продаж чери"))

    def test_otdel_zap_parts_department(self):
        self.assertTrue(stt_implies_parts_department("отдел зап"))
        self.assertEqual(normalize_department_stt_text("отдел зап"), "отдел запасных частей")

    def test_mto_department_maps_to_sto_service(self):
        for phrase in ("Отдел МТО", "отдел м т о", "МТО", "эм тэ о"):
            with self.subTest(phrase=phrase):
                normalized = normalize_department_stt_text(phrase).lower()
                self.assertIn("сто", normalized, phrase)

    def test_press_button_five_silent_admin(self):
        phrase = "Абонентом нажмите кнопку пять."
        self.assertTrue(is_silent_immediate_admin_callback(phrase))
        self.assertIn("администратор", normalize_department_stt_text(phrase).lower())

    def test_mixed_script_reception_routes_to_admin(self):
        pearls = (
            "Reцепшеoн",
            "Rесепшен",
            "Reсепtion",
            "рeception",
            "рецепшion",
            "ресепшon",
            "rецепшен",
            "Reцепшн",
            "Рecепшен",
            "соедините с Reцепшеoн",
        )
        for phrase in pearls:
            with self.subTest(phrase=phrase):
                norm = normalize_department_stt_text(phrase).lower()
                self.assertIn("администратор", norm, phrase)

    def test_prohozhdenie_to_variants_normalize_to_service(self):
        phrases = (
            "Прохождение ТО",
            "Прохождение ТО.",
            "прохождение то",
            "прохождения то",
            "Прохождение TO",
            "Прохождение T O",
            "Прохождение t o",
            "Прохождение Т O",
            "прохождението",
            "прохождение teo",
            "прохождение тэо",
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    normalize_department_stt_text(phrase),
                    "техобслуживание",
                    phrase,
                )

    def test_tlo_zaiti_stt_to_service(self):
        for phrase in ("тло зайти", "Тло  Зайти.", "зайти тло"):
            with self.subTest(phrase=phrase):
                self.assertEqual(normalize_department_stt_text(phrase), "техобслуживание")

    def test_kuzonny_stt_to_body_shop(self):
        for phrase in ("кузонный", "кузонного", "кузонный цех"):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    normalize_department_stt_text(phrase),
                    "цех кузовного ремонта",
                )

    def test_kudavnoy_stt_to_body_shop(self):
        for phrase in ("кудавной", "кудовной", "кудовного цеха"):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    normalize_department_stt_text(phrase),
                    "цех кузовного ремонта",
                )

    def test_udobnoy_remont_stt_to_body_shop(self):
        """STT «удобной ремонт» ≈ кузовной ремонт → кузовной цех."""
        for phrase in (
            "Удобной ремонт.",
            "удобной ремонт",
            "удобный ремонт",
            "удобного ремонта",
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    normalize_department_stt_text(phrase),
                    "цех кузовного ремонта",
                )
        # «удобно говорить» / время — не кузовной
        self.assertNotEqual(
            normalize_department_stt_text("удобно говорить"),
            "цех кузовного ремонта",
        )
        self.assertNotEqual(
            normalize_department_stt_text("удобное время"),
            "цех кузовного ремонта",
        )

    def test_pozyvnoy_remont_stt_to_body_shop(self):
        """STT «позывной ремонт» ≈ кузовной ремонт → кузовной цех."""
        for phrase in (
            "Позывной ремонт.",
            "позывной ремонт",
            "позывного ремонта",
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    normalize_department_stt_text(phrase),
                    "цех кузовного ремонта",
                )

    def test_stt_service_fragments_normalize_to_service(self):
        for phrase in ("ервис", "Ервис.", "специервис", "Специервис"):
            with self.subTest(phrase=phrase):
                self.assertEqual(normalize_department_stt_text(phrase), "сервис", phrase)

    def test_warranty_stt_variants_normalize_to_warranty(self):
        for phrase in ("гарантин", "гарантином", "карантин", "карантином"):
            with self.subTest(phrase=phrase):
                self.assertEqual(normalize_department_stt_text(phrase), "гарантия", phrase)

    def test_strakhovoe_routes_to_admin(self):
        for phrase in ("страховое", "Страховое", "страховая"):
            with self.subTest(phrase=phrase):
                self.assertTrue(stt_implies_insurance_admin_department(phrase), phrase)
                self.assertEqual(normalize_department_stt_text(phrase), "администратор", phrase)

    def test_pervoe_service_only_whole_utterance(self):
        self.assertTrue(stt_implies_service_first_to_reply("первое"))
        self.assertTrue(stt_implies_service_first_to_reply("Первую"))
        self.assertFalse(stt_implies_service_first_to_reply("первое число"))
        self.assertNotEqual(normalize_department_stt_text("первое"), "сервис")


if __name__ == "__main__":
    unittest.main()
