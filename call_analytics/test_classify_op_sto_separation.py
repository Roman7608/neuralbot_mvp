"""Разделение OP vs STO: тест-драйв и модель не дают OP без намерения покупки."""
import unittest

from call_analytics.classify_by_transcript import (
    classify_auto,
    _has_new_vehicle_model_in_purchase_context,
    _has_sto_substantive_service_intent,
    _test_drive_is_customer_sales_context,
)


class TestClassifyOpStoSeparation16382(unittest.TestCase):
    def _transcript_from_log(self, call_id: int) -> str:
        with open("/home/vikingi/VikingiAll/logs/vikingi_autofetch.log") as f:
            log = f.read()
        return (
            log.split(f"call_id={call_id}")[1]
            .split("--- Транскрипт ---")[1]
            .split("---")[0]
            .strip()
        )

    def test_16382_stabilizer_labor_quote_sto_in_not_op(self):
        """16382: диспетчер + стабилизаторы + «на тест-драйве» у мастера — STO_IN, не OP."""
        t = self._transcript_from_log(16382)
        self.assertEqual(classify_auto(t), ("STO", "STO_IN"))
        low = t.lower()
        self.assertTrue(_has_sto_substantive_service_intent(low))
        self.assertFalse(_test_drive_is_customer_sales_context(low))
        self.assertFalse(_has_new_vehicle_model_in_purchase_context(low))

    def test_op_test_drive_booking_still_op_in(self):
        t = (
            "Администратор салона. Перевожу на менеджера. "
            "Меня зовут Андрей, менеджер отдела продаж. "
            "Хотели записаться на тест-драйв Tiggo 7 Pro?"
        )
        self.assertEqual(classify_auto(t), ("OP", "OP_IN"))
        self.assertTrue(_test_drive_is_customer_sales_context(t.lower()))

    def test_19588_admin_handoff_sales_warranty_words_still_op_in(self):
        """19588: гарантия/защита в презентации нового авто не превращают ОП_вх в СТО."""
        t = (
            "Администратор салона Оксана. Меня интересует самый бюджетный новый автомобиль Tenet. "
            "Переключу вас на менеджера. Добрый день, официальный дилер Тольятти, меня зовут Андрей. "
            "Какую машину смотрите? Самый бюджетный вариант, только новый без пробега. "
            "Есть Tenet T4L в наличии, гарантия пять лет, кузов оцинкованный кроме крыши. "
            "Прайсовая цена 2 260 000, скидка за наличный расчёт 100 000. "
            "Если нужен кредит, скидка 60 000. Допы по желанию: коврики и защита. "
            "По цветам есть серый и чёрный. Завтра приезжайте, обсудим расчёт и ставку."
        )
        self.assertEqual(classify_auto(t), ("OP", "OP_IN"))

    def test_op_model_with_purchase_intent_still_op_in(self):
        t = (
            "Меня зовут Андрей, менеджер отдела продаж. "
            "Интересует Tiggo 4 Pro, сколько стоит?"
        )
        self.assertEqual(classify_auto(t), ("OP", "OP_IN"))

    def test_18735_outbound_sales_driving_qualities_not_sto(self):
        """18735: «ходовые качества» автомобиля — свойство модели, не ремонт ходовой."""
        t = (
            "Алло, Руслан, добрый вечер. Меня зовут Захаров Илья, "
            "звоню вам с официального дилера Чери Тенет Викинги на Заставной. "
            "Интересовались покупкой Tenet T4, всё верно? "
            "Мотор и коробки одинаковые, поэтому по ходовым качествам и характеристикам "
            "машины абсолютно одинаковые. Можно приехать, посмотреть и пройти тест-драйв."
        )
        self.assertEqual(classify_auto(t), ("OP", "OP_OUT"))

    def test_20080_b2b_transfer_process_is_other_not_op_in(self):
        """20080: B2B-процесс (доверенность/аукцион/письмо), не цикл покупки нового авто."""
        t = (
            "Официальный дилер, администратор Диана. Добрый день. "
            "Подскажите, Владимир работает? Можно связаться? "
            "Меня зовут Денис, компания Автозавод Санкт-Петербург, бывший Ниссан. "
            "Переведу вас. Добрый день, Владимир? Да. "
            "Я звоню по вопросу клиента Титов, Nissan X-Trail. "
            "Ранее вам писала Елена, но ответа на письмо не было. "
            "Нужен человек, который будет принимать паспортные данные, "
            "сделаем доверенность, клиент передаст автомобиль, "
            "дальше будем реализовывать с вашей площадки, "
            "если не выкупите — продадим через онлайн-аукцион."
        )
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_21195_outbound_admin_lead_callback_is_op_out(self):
        """21195: «заявочка от вас была» + перевод в ОП — исходящий лид-коллбэк OP_OUT."""
        t = self._transcript_from_log(21195)
        self.assertEqual(classify_auto(t), ("OP", "OP_OUT"))

    def test_21232_outbound_manager_intro_with_client_name_is_op_out(self):
        """21232: «Наталья ... меня зовут Илья, звоню вам ...» — исходящий ОП звонок."""
        t = self._transcript_from_log(21232)
        self.assertEqual(classify_auto(t), ("OP", "OP_OUT"))

    def test_21170_outbound_lead_arrived_phrase_is_op_out(self):
        """21170: «от вам заявка приходила» в исходящем диалоге — ОП_исх."""
        t = self._transcript_from_log(21170)
        self.assertEqual(classify_auto(t), ("OP", "OP_OUT"))

    def test_21199_inbound_corporate_sales_handoff_is_op_in(self):
        """21199: входящий перевод на корпоративные продажи (Европлан, Tenet T7) — ОП_вх."""
        t = self._transcript_from_log(21199)
        self.assertEqual(classify_auto(t), ("OP", "OP_IN"))

    def test_21401_test_drive_time_slot_without_sales_cycle_is_other(self):
        """21401: запись на тест-драйв по времени без цикла ОП/перевода на менеджера — Прочие."""
        t = self._transcript_from_log(21401)
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_21458_showroom_visit_time_without_manager_consultation_is_other(self):
        """21458: админ согласует визит в салон, консультация менеджера не нужна — Прочие."""
        t = self._transcript_from_log(21458)
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_22498_spare_parts_oil_price_inquiry_is_other_not_op(self):
        """22498: входящий в ОЗЧ (масло/допуск/цена/наличие), не ОП_вх."""
        t = self._transcript_from_log(22498)
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_21504_outbound_repeat_plans_changed_is_other(self):
        """21504: повторный OP_OUT follow-up («общались», «планы поменялись») — Прочие."""
        t = self._transcript_from_log(21504)
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))


if __name__ == "__main__":
    unittest.main()
