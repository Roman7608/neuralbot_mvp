"""Извлечение имени клиента и p4 (как обращаться) для OP_IN: с переводом и без."""
import unittest

from analyze_call_quality import (
    client_self_intro_in_text,
    count_customer_name_mentions_after_handover,
    evaluate_call_by_rules,
    extract_customer_name_from_full_transcript,
    extract_manager_name_from_full_transcript,
)
from call_analytics.op_in_rubric_eval import evaluate_op_in_rubric


class TestCustomerNameAfterAdminHandover(unittest.TestCase):
    """Админ → менеджер: имя клиента не из блока администратора."""

    _T_HANDOVER_GENNADIY = (
        "Администратор салона. Оксана, добрый день. Официальный дилер Викинги Чери. "
        "С кем-нибудь из отдела продаж поговорить. Тигго Т4. Перевожу вас на менеджера. "
        "Геннадий, добрый день, меня зовут Евгений, менеджер отдела продаж. "
        "Геннадий, подскажите, какой комплектации интересует. Геннадий, спасибо за звонок."
    )

    def test_customer_not_admin_oksana(self):
        mgr = extract_manager_name_from_full_transcript(self._T_HANDOVER_GENNADIY, department="OP")
        cust = extract_customer_name_from_full_transcript(self._T_HANDOVER_GENNADIY, mgr)
        self.assertEqual(cust, "Геннадий")
        self.assertNotEqual(cust, "Оксана")

    def test_name_mentions_after_handover(self):
        mgr = extract_manager_name_from_full_transcript(self._T_HANDOVER_GENNADIY, department="OP")
        cust = extract_customer_name_from_full_transcript(self._T_HANDOVER_GENNADIY, mgr)
        cnt = count_customer_name_mentions_after_handover(self._T_HANDOVER_GENNADIY, cust)
        self.assertGreaterEqual(cnt, 2)

    def test_p4_passes_via_name_usage(self):
        scores = evaluate_op_in_rubric(self._T_HANDOVER_GENNADIY)
        self.assertGreaterEqual(scores["p4_ask_name_form"], 0.99)
        self.assertGreaterEqual(scores["p5_name_usage_3plus"], 0.99)


class TestCustomerNameDirectManager(unittest.TestCase):
    """Менеджер принял звонок сразу — весь текст релевантен."""

    _T_MANAGER_ASKS = (
        "Добрый день, меня зовут Андрей, менеджер отдела продаж Викинги. "
        "Как к вам могу обращаться? Владимир. Очень приятно, Владимир. "
        "Какой автомобиль интересует?"
    )

    _T_CLIENT_INTRO = (
        "Здравствуйте, официальный дилер Чери Викинги. Меня зовут Анастасия, менеджер отдела продаж. "
        "Слушаю вас. Здравствуйте, меня зовут Михаил. Хотел узнать про Тигго 7."
    )

    _T_NO_ASK_NO_NAME = (
        "Добрый день, меня зовут Андрей, менеджер отдела продаж Викинги. "
        "Слушаю вас. Здравствуйте, подскажите цену на Тигго 4."
    )

    def test_manager_asks_customer_name(self):
        mgr = extract_manager_name_from_full_transcript(self._T_MANAGER_ASKS, department="OP")
        cust = extract_customer_name_from_full_transcript(self._T_MANAGER_ASKS, mgr)
        self.assertEqual(cust, "Владимир")

    def test_p4_when_manager_asks(self):
        scores = evaluate_op_in_rubric(self._T_MANAGER_ASKS)
        self.assertGreaterEqual(scores["p4_ask_name_form"], 0.99)

    def test_p4_when_client_self_intro(self):
        self.assertTrue(client_self_intro_in_text(self._T_CLIENT_INTRO, "Анастасия"))
        scores = evaluate_op_in_rubric(self._T_CLIENT_INTRO)
        self.assertGreaterEqual(scores["p4_ask_name_form"], 0.99)

    def test_customer_from_self_intro_direct(self):
        mgr = extract_manager_name_from_full_transcript(self._T_CLIENT_INTRO, department="OP")
        cust = extract_customer_name_from_full_transcript(self._T_CLIENT_INTRO, mgr)
        self.assertEqual(cust, "Михаил")

    def test_p4_zero_without_ask_or_intro(self):
        scores = evaluate_op_in_rubric(self._T_NO_ASK_NO_NAME)
        self.assertLess(scores["p4_ask_name_form"], 0.01)


class TestP4AdminAskBeforeHandoverIgnored(unittest.TestCase):
    """Вопрос админа до перевода не засчитывается менеджеру."""

    _T = (
        "Администратор салона. Оксана, добрый день. Как вас зовут? Аноним. "
        "Перевожу на менеджера. Меня зовут Евгений, менеджер. Слушаю вас. "
        "Хотел узнать про кредит."
    )

    def test_p4_not_from_admin_question(self):
        scores = evaluate_op_in_rubric(self._T)
        self.assertLess(scores["p4_ask_name_form"], 0.01)


class TestP7FamiliarWithModel(unittest.TestCase):
    """p7: «Вы с моделью знакомы?» — знакомство с авто."""

    def test_model_familiar_question(self):
        t = "Вы с моделью знакомы, правильно? понимаю? — Да, звоню из Самары."
        scores = evaluate_op_in_rubric(t)
        self.assertGreaterEqual(scores["p7_familiar_with_car"], 0.99)


class TestCall19826IlyaAndExpectations(unittest.TestCase):
    """19826: «Алья» = Илья; комплектация и панорама закрывают p7."""

    _T = (
        "Администратор Диана. Добрый день. Подскажите, как могу к вам обращаться? "
        "Меня Илья зовут. Илья, переведу вам на менеджера отдела продаж. "
        "Оставайтесь на линии. Алья, добрый день. Меня зовут Анастасия, "
        "менеджер отдела продаж Викинги на Заставной. "
        "С комплектациями знакомы? Ну, относительно. "
        "Какую рассматриваете? С панорамой или без?"
    )

    def test_customer_name_and_stt_mention(self):
        manager = extract_manager_name_from_full_transcript(self._T, department="OP")
        customer = extract_customer_name_from_full_transcript(self._T, manager)
        self.assertEqual(customer, "Илья")
        self.assertEqual(
            count_customer_name_mentions_after_handover(self._T, customer),
            1,
        )

    def test_op_in_rubric_name_and_expectations(self):
        scores = evaluate_op_in_rubric(self._T)
        self.assertGreaterEqual(scores["p4_ask_name_form"], 0.99)
        self.assertGreaterEqual(scores["p5_name_usage_3plus"], 0.99)
        self.assertGreaterEqual(scores["p7_familiar_with_car"], 0.99)

    def test_weighted_rules_count_one_name_and_full_p7(self):
        scores = evaluate_call_by_rules(self._T, call_type="OP_IN")
        self.assertEqual(scores["p5_name_usage_3plus"], 0.25)
        self.assertGreaterEqual(scores["p7_familiar_with_car"], 0.99)


class TestManagerLastExplicitIntro(unittest.TestCase):
    """Явное представление менеджера важнее упоминания коллеги позже в тексте."""

    _T = (
        "Администратор. Переключу на менеджера. Оставайтесь на линии. "
        "Здравствуйте, это Андрей, менеджер отдела продаж Тольятти-Викинги. "
        "Сергей, подскажите по цене. "
        "В понедельник работает мой напарник, старший менеджер Захаров Илья."
    )

    def test_manager_is_last_explicit_intro_not_colleague(self):
        mgr = extract_manager_name_from_full_transcript(self._T, department="OP")
        self.assertIn("андрей", (mgr or "").lower())
        self.assertNotIn("илья", (mgr or "").lower())


class TestCustomerNameFromMentionsWithoutAsk(unittest.TestCase):
    """Без «как обращаться» — имя клиента из частых обращений менеджера."""

    _T = (
        "Добрый день, меня зовут Андрей, менеджер отдела продаж Викинги. "
        "Слушаю вас. Сергей, подскажите, как планируете покупку? "
        "Сергей, спасибо, тогда созвонимся."
    )

    def test_customer_from_repeated_mentions(self):
        mgr = extract_manager_name_from_full_transcript(self._T, department="OP")
        cust = extract_customer_name_from_full_transcript(self._T, mgr)
        self.assertEqual(cust, "Сергей")

    def test_p5_from_mentions_without_ask(self):
        scores = evaluate_op_in_rubric(self._T)
        self.assertGreaterEqual(scores["p5_name_usage_3plus"], 0.99)


class TestAdminCollectedNameBeforeHandover(unittest.TestCase):
    """Админ уточнил имя до перевода — менеджер обращается по имени (p5)."""

    _T = (
        "Администратор салона. Оксана, добрый день. Как вас зовут? — Э-э Сергей. "
        "Сейчас Сергея переключу на менеджера. Оставайтесь на линии. "
        "Здравствуйте, это Андрей, менеджер отдела продаж. "
        "Сергей, подскажите, как планируете приобретать? Спасибо, Сергей, до связи."
    )

    def test_customer_name_from_admin_block(self):
        mgr = extract_manager_name_from_full_transcript(self._T, department="OP")
        cust = extract_customer_name_from_full_transcript(self._T, mgr)
        self.assertEqual(cust, "Сергей")

    def test_p5_after_manager_uses_admin_collected_name(self):
        scores = evaluate_op_in_rubric(self._T)
        self.assertGreaterEqual(scores["p5_name_usage_3plus"], 0.99)


class TestP4AdminAskSttKTam16283(unittest.TestCase):
    """16283: STT «к там обращаться» + имя до перевода — p4 и p5."""

    def _transcript_from_log(self, call_id: int) -> str:
        with open("/home/vikingi/VikingiAll/logs/vikingi_autofetch.log") as f:
            log = f.read()
        return (
            log.split(f"call_id={call_id}")[1]
            .split("--- Транскрипт ---")[1]
            .split("---")[0]
            .strip()
        )

    def test_16283_admin_ask_k_tam_obrashchatsya_p4(self):
        t = self._transcript_from_log(16283)
        scores = evaluate_op_in_rubric(t)
        self.assertGreaterEqual(scores["p4_ask_name_form"], 0.99)
        self.assertGreaterEqual(scores["p5_name_usage_3plus"], 0.99)
        mgr = extract_manager_name_from_full_transcript(t, department="OP")
        cust = extract_customer_name_from_full_transcript(t, mgr)
        self.assertEqual(cust, "Динара")


class TestP4AdminAskTakMoguKVas28536(unittest.TestCase):
    """28536: STT «Так могу к вас обращаться? Фёдором звать» — p4 засчитывается."""

    def _transcript_from_log(self, call_id: int) -> str:
        with open("/home/vikingi/VikingiAll/logs/vikingi_autofetch.log") as f:
            log = f.read()
        return (
            log.split(f"call_id={call_id}")[1]
            .split("--- Транскрипт ---")[1]
            .split("---")[0]
            .strip()
        )

    def test_28536_tak_mogu_k_vas_obrashchatsya_p4(self):
        t = self._transcript_from_log(28536)
        scores = evaluate_op_in_rubric(t)
        self.assertGreaterEqual(scores["p4_ask_name_form"], 0.99)
        mgr = extract_manager_name_from_full_transcript(t, department="OP")
        cust = extract_customer_name_from_full_transcript(t, mgr)
        self.assertEqual((cust or "").lower().replace("ё", "е"), "федор")


class TestP14AskContactsPhrases(unittest.TestCase):
    """p14: запрос телефона — разный порядок слов в STT."""

    def test_phone_number_prodiktu_yte_svoj(self):
        t = (
            "Меня зовут Андрей, менеджер. Сколько времени потребуется? "
            "Номер телефона продиктуйте свой. 917 141 33 93."
        )
        scores = evaluate_op_in_rubric(t)
        self.assertGreaterEqual(scores["p14_ask_contacts"], 0.99)


class TestCall20487CustomerNameAndP4P5(unittest.TestCase):
    """20487: имя из «вас Александр, правильно?» + p4/p5 должны засчитываться."""

    def _transcript_from_log(self, call_id: int) -> str:
        with open("/home/vikingi/VikingiAll/logs/vikingi_autofetch.log") as f:
            log = f.read()
        return (
            log.split(f"call_id={call_id}")[1]
            .split("--- Транскрипт ---")[1]
            .split("---")[0]
            .strip()
        )

    def test_20487_customer_name_and_scores(self):
        t = self._transcript_from_log(20487)
        mgr = extract_manager_name_from_full_transcript(t, department="OP")
        cust = extract_customer_name_from_full_transcript(t, mgr)
        self.assertEqual(cust, "Александр")

        scores = evaluate_op_in_rubric(t)
        self.assertGreaterEqual(scores["p4_ask_name_form"], 0.99)
        self.assertGreaterEqual(scores["p5_name_usage_3plus"], 0.99)


class TestFailedServiceHandoffManager(unittest.TestCase):
    """Названный до перевода мастер не становится менеджером, если перевод не состоялся."""

    def test_17834_keeps_admin_diana_not_alexandra_plaksina(self):
        t = (
            "Официальный дилер Чери и Тенет Викинги на Заставной, "
            "администратор Диана. Добрый день! "
            "Какой мастер-приёмщик, не подскажете? Александр, по-моему. "
            "Оставайтесь на линии, переведу вас. Алло, специалист сейчас с клиентами. "
            "Я контактный номер телефона ваш запишу, передам, перезвонит вам ещё раз."
        )

        for department in ("OTHER", "STO", None):
            with self.subTest(department=department):
                self.assertEqual(
                    extract_manager_name_from_full_transcript(t, department=department),
                    "Администратор Диана",
                )


class TestPaymentFormCriterion(unittest.TestCase):
    def test_23705_cash_and_card_payment_is_counted(self):
        t = (
            "Менеджер отдела продаж Илья. "
            "А за наличку у вас какая цена? Цена два миллиона двести пятьдесят. "
            "Оплата может быть наличными, можно картой оплатить."
        )

        scores = evaluate_op_in_rubric(t)

        self.assertEqual(scores["p10_payment_form"], 1.0)


if __name__ == "__main__":
    unittest.main()
