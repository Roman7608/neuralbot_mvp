"""Голосовой бот: «диагностики» / «диагност» → ассистент сервиса (не меню отделов)."""
import re
import unittest

from analyze_call_quality import (
    extract_customer_name_from_full_transcript,
    extract_manager_name_from_full_transcript,
)
from dialog.bot_logic import (
    SERVICE_ASSISTANT_DIRECT_MARKERS,
    _stt_implies_ac_service_request,
    _stt_implies_wheel_alignment_service_request,
)


def _direct_service_marker_hit(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in SERVICE_ASSISTANT_DIRECT_MARKERS)


class TestVoiceDiagnosticsRouting(unittest.TestCase):
    def test_diagnostik_stem_in_direct_markers(self):
        self.assertIn("диагностик", SERVICE_ASSISTANT_DIRECT_MARKERS)

    def test_diagnostiki_phrases_hit_service_direct_markers(self):
        for text in (
            "вы знаете мне насчёт диагностики соединить",
            "насчет диагностики",
            "нужен диагност",
            "записаться на диагностику",
            "делаете диагностику",
            "диагностика",
        ):
            with self.subTest(text=text):
                self.assertTrue(_direct_service_marker_hit(text), text)


class TestVoiceWheelAlignmentRouting(unittest.TestCase):
    def test_razval_stems_in_direct_markers(self):
        self.assertIn("развал", SERVICE_ASSISTANT_DIRECT_MARKERS)
        self.assertIn("схожден", SERVICE_ASSISTANT_DIRECT_MARKERS)

    def test_wheel_alignment_phrases_route_to_service(self):
        for text in (
            "развал с хождения",
            "на развал схождения можно записать",
            "развал схождение",
            "развосхождение",
            "нужно схождение",
        ):
            with self.subTest(text=text):
                t = text.lower()
                self.assertTrue(
                    _stt_implies_wheel_alignment_service_request(t),
                    text,
                )
                self.assertTrue(_direct_service_marker_hit(text), text)


class TestVoiceAcServiceRouting(unittest.TestCase):
    def test_ac_refill_and_check_phrases(self):
        for text in (
            "хочу заправить кондиционер",
            "дозаправить кондиционера",
            "заправка кондиционера",
            "дозаправка кондиционера",
            "дзаправить кондиционер",
            "проверка кондиционера",
            "проверить кондиционер",
        ):
            with self.subTest(text=text):
                self.assertTrue(_stt_implies_ac_service_request(text.lower()), text)

    def test_ac_evacuation_alone_not_service(self):
        for text in (
            "эвакуация кондиционера",
            "кондиционер не работает",
            "кондиционер",
        ):
            with self.subTest(text=text):
                self.assertFalse(_stt_implies_ac_service_request(text.lower()), text)


class TestOpManagerAfterAdminHandoff(unittest.TestCase):
    """10355: после перевода «Меня зовут Евгений», не обрывок «менеджера» → Жера."""

    _T10355 = (
        "Администратор салона. Оксана, добрый день. Здравствуйте, на отдел продаж можете "
        "меня на менеджера? Какой автомобиль интересует вас? Четыре-ЭЛ. "
        "Как к вас можно обращаться? Александр. Очень приятно, Александр, переключаю. "
        "переключай, Александр. Давайте. Александр, здравствуйте. Меня зовут Евгений. "
        "Слушаю вас. Здравствуйте, Евгений."
    )

    def test_manager_evdokimov_not_menedzher_suffix(self):
        mgr = extract_manager_name_from_full_transcript(self._T10355, department="OP")
        self.assertIn(
            mgr,
            ("Евдокимов Евгений", "Евгений Евдокимов", "Евгений"),
            msg=f"unexpected manager: {mgr!r}",
        )
        self.assertNotEqual(mgr.lower() if mgr else "", "жера")

    def test_customer_alexander(self):
        mgr = extract_manager_name_from_full_transcript(self._T10355, department="OP")
        cust = extract_customer_name_from_full_transcript(self._T10355, mgr)
        self.assertEqual(cust, "Александр")


class TestManagerIntroDepartmentPriority(unittest.TestCase):
    """10383: при явном представлении в звонке Прочие берём имя из него."""

    _T10383 = (
        "Викинги, Кузовной, Дмитрий. Дмитрий, добрый день. "
        "Снова вас беспокоит Анастасия. Чери 554. "
        "Да-да, вы вчера звонили."
    )

    def test_other_department_prefers_explicit_intro_name(self):
        mgr = extract_manager_name_from_full_transcript(self._T10383, department="OTHER")
        self.assertIn("дмитри", (mgr or "").lower())
        self.assertNotIn("анастас", (mgr or "").lower())

    def test_other_department_does_not_resolve_op_roster_name(self):
        mgr = extract_manager_name_from_full_transcript(self._T10383, department="OTHER")
        self.assertNotIn(mgr, ("Анастасия Краснощекова", "Краснощекова Анастасия"))

    def test_other_department_accepts_master_intro(self):
        t = (
            "Алло. Анастасия Иосифовна, здравствуйте. "
            "Это Евгений, мастер Викинги. Машина готова."
        )
        mgr = extract_manager_name_from_full_transcript(t, department="OTHER")
        self.assertEqual(mgr, "Евгений")

    def test_11976_dealer_menya_zovut_slushayu_not_megafon_caller(self):
        """11976: «Меня зовут Евгений, слушаю вас» — сотрудник; не «Алёна, компания Мегафон»."""
        t = (
            "Дилерский центр Чай от викинговой заставны. Меня зовут Евгений, слушаю вас. "
            "Алло. Добрый день, меня зовут Алёна, компания Мегафон, Корпоративный отдел. "
            "Звоню по организации Викинги."
        )
        mgr = extract_manager_name_from_full_transcript(t, department="OTHER")
        self.assertIn(
            mgr,
            ("Евгений Евдокимов", "Евдокимов Евгений", "Евгений"),
            msg=f"unexpected manager: {mgr!r}",
        )
        self.assertNotEqual((mgr or "").lower(), "алёна")

    def test_10568_sto_outbound_manager_yulia(self):
        """10568: исх. диспетчер Юлия, не «Удобно» из «Удобно?»."""
        t = (
            "Алло, Алексей, да? Да. здравствуйте. это компания Викинги Юлия, диспетчер сервиса. "
            "Удобно? Заявку оставляли по поводу замены лобового стекло на Nissan."
        )
        from call_analytics.classify_by_transcript import classify_auto

        self.assertEqual(classify_auto(t), ("STO", "STO_OUT"))
        mgr = extract_manager_name_from_full_transcript(t, department="STO")
        self.assertEqual(mgr, "Юлия")

    def test_14266_to_confirm_no_manager_esli_false_positive(self):
        """14266: «мастер-приёмщик, если будет свободен» — не имя менеджера «Если»."""
        t = (
            "Алло. Анна Михайловна, добрый день, компания Викинги. "
            "Завтра записаны к нам на ТО на 9:00. Хотели уточнить, подъедете, ожидаем вас? "
            "Да-да, приедем. Хорошо, запись вашу подтвердили, ожидаем. "
            "А если раньше подъедете, ничего страшного. "
            "Мастер-приёмщик, если будет свободен, вас примет. "
            "Ну, всё, спасибо. До свидания."
        )
        mgr = extract_manager_name_from_full_transcript(t, department="OTHER")
        self.assertIsNone(mgr, msg=f"unexpected manager: {mgr!r}")

    def test_10549_sto_manager_yulia_not_osvoboditsya(self):
        """10549: «сейчас освободится менеджер отдела» — не ФИО; диспетчер Юлия."""
        t = (
            "Викинги Чери — диспетчер сервиса. Юлия. Здравствуйте, Юлия. "
            "Я записан завтра на ТО. Рулевые наконечники есть? "
            "Сейчас освободится менеджер отдела она посмотрит по вашему автомобилю."
        )
        mgr = extract_manager_name_from_full_transcript(t, department="STO")
        self.assertEqual(mgr, "Юлия")
        self.assertNotIn((mgr or "").lower(), ("сейчас", "освободится"))

    def test_14443_sto_manager_yulia_andreeva_stt_slushayus(self):
        """14443: STT «Андреев Юлюсс, Слушаюс» → Юлия Андреева."""
        log = open("/home/vikingi/VikingiAll/logs/vikingi_autofetch.log", encoding="utf-8").read()
        t = (
            log.split("call_id=14443")[1]
            .split("--- Транскрипт ---")[1]
            .split("---")[0]
            .strip()
        )
        mgr = extract_manager_name_from_full_transcript(t, department="STO")
        self.assertEqual(mgr, "Юлия Андреева", msg=f"unexpected manager: {mgr!r}")

    def test_call_10986_op_intro_andrey_not_dealer_brand(self):
        """10986: «Павел, это Андрей, менеджер отдела продаж Тэнет ТольяттиВикинги» → Андрей."""
        t = (
            ". Р. Да.. Ну вот сюда можно положить. Алло. Павел, это Андрей, менеджер отдела продаж "
            "Тэнет ТольяттиВикинги на Заставной. Обещал вам отзвониться по цене автомобиля. "
            "Да-да.. Готовы за 2 590 машину отдать."
        )
        mgr = extract_manager_name_from_full_transcript(t, department="OTHER")
        self.assertEqual(mgr, "Андрей")
        self.assertNotIn("тэнет", (mgr or "").lower())
        self.assertNotIn("викинг", (mgr or "").lower())

    def test_self_intro_beats_text_after_menedzher_otdela_prodazh(self):
        """«это Андрей, менеджер отдела продаж <бренд>» — имя из самопрезентации, не хвост STT."""
        from analyze_call_quality import _collect_staff_intro_candidates, _pick_best_staff_intro_from_candidates

        t = "Павел, это Андрей, менеджер отдела продаж Тэнет ТольяттиВикинги на Заставной."
        cands = _collect_staff_intro_candidates(t, "OP")
        mgr = _pick_best_staff_intro_from_candidates(t, "OP", cands)
        self.assertEqual(mgr, "Андрей")

    def test_random_word_not_manager_without_catalog(self):
        """Случайное слово STT без каталога имён — не менеджер."""
        from analyze_call_quality import _check_manager_name, _is_valid_manager_name_from_catalog

        self.assertFalse(_is_valid_manager_name_from_catalog("Тэнет ТольяттиВикинги"))
        self.assertFalse(_is_valid_manager_name_from_catalog("Смотрите"))
        self.assertIsNone(
            _check_manager_name(
                "Смотрите, менеджер отдела продаж",
                "Смотрите",
                0,
                "смотрите, менеджер отдела продаж",
                intro_context=True,
                department="OP",
            )
        )
        self.assertTrue(_is_valid_manager_name_from_catalog("Андрей"))

    def test_call_11005_name_before_master_eugene(self):
        """11005: «Евгений, мастер-приёмщиккин» — последнее представление, имя до «мастер»."""
        t = (
            "Не. Алло. Добрый день, официальный дилер Cherry TENET, администратор салона Оксана. "
            "Звоночек от вам был. Отдел продаж, сервис интересовал. Переключить. "
            "— Павел Иванович. — Хорошо. Евгений, мастер-приёмщиккин, здравствуйте. "
            "Женя, добрый день. Павел Иванович, вот вчера машину отставляли."
        )
        mgr = extract_manager_name_from_full_transcript(t, department="OTHER")
        self.assertEqual(mgr, "Евгений")
        self.assertNotEqual(mgr, "Оксана")


    def test_call_12534_insurance_body_not_op_evdokimov(self):
        """12534: «Дилерский центр… Евгений, слушаю вас» + перевод на кузовной — не Евдокимов ОП."""
        t = (
            "Дилерский центр Ч Викингн застаный. Меня зовут Евгений, слушаю вас. "
            "У вас по страховке машина ремонтироваться должна. Осмотр делали перед кузовным ремонтом. "
            "После аварии. Одну секундочку, переключу на мастера-приёмщика. "
            "Викинги Ккузовной, Сергей, слушаю вас. Фамилию собственника назовите. Малахова."
        )
        mgr = extract_manager_name_from_full_transcript(t, department="OTHER")
        self.assertEqual(mgr, "Сергей")
        self.assertNotIn("евдокимов", (mgr or "").lower())

    def test_call_11073_kuzov_body_dmitry_after_admin_transfer(self):
        """11073: после перевода «Викинги кузовной, Дмитрий» — не администратор Оксана."""
        t = (
            "Администратор салона. Оксана, добрый день. Оксана, здравствуйте. "
            "Гарантийная покраска кузова Тэнет-7. Давайте на малярно-кузовной переключу. "
            "Оставайтесь на линии. Хорошо. Викинги кузовной, Дмитрий. Дмитрий, здравствуйте. "
            "Подменный автомобиль при гарантийной покраске?"
        )
        mgr = extract_manager_name_from_full_transcript(t, department="OTHER")
        self.assertIn("дмитри", (mgr or "").lower())
        self.assertNotEqual(mgr, "Оксана")

    def test_kuzov_intro_flexible_punctuation_and_word_order(self):
        """Якорь кузовной + имя: без запятой после «Викинги», имя до якоря."""
        t1 = "Переключу. Викинги кузовной Дмитрий здравствуйте."
        t2 = "Переключу. Дмитрий викинги кузовной цех здравствуйте."
        for t in (t1, t2):
            mgr = extract_manager_name_from_full_transcript(t, department="OTHER")
            self.assertIn("дмитри", (mgr or "").lower(), msg=t[:60])

    def test_20670_kuzov_sergey_is_manager_not_client_leonid(self):
        """20670: «Кузовной, Сергей, слушаю вас» приоритетнее клиентского «это Леонид»."""
        t = (
            "Вки Кузовной, Сергей, слушаю вас. "
            "Здравствуйте, это Леонид, по поводу Гранта. Я уже к вам подъехал."
        )
        mgr = extract_manager_name_from_full_transcript(t, department="OTHER")
        self.assertEqual(mgr, "Сергей")
        self.assertNotEqual(mgr, "Леонид")


if __name__ == "__main__":
    unittest.main()
