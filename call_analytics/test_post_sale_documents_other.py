"""Регрессии: документы завершённой сделки и повторный статус-звонок → Прочие."""

import unittest

from call_analytics.classify_by_transcript import (
    _is_op_repeat_followup_outbound,
    _is_post_sale_vehicle_documents_other,
    classify_auto,
)


class PostSaleAndRepeatCallsTest(unittest.TestCase):
    def test_17707_post_sale_epts_documents_are_other(self) -> None:
        text = (
            "Официальный дилер Чери и Тенет Викинги, администратор Диана. "
            "Я покупала машину два года назад. Хотела уточнить по электронному ПТС: "
            "вы отправляли его на электронную почту? Я ничего не нашла. "
            "Меня зовут Андрей, менеджер отдела продаж. Продиктуйте VIN, я нашёл вашу ПТС. "
            "Вообще вы хотите машину свою продать? Новую покупать планируете? "
            "Пока смотрим, ещё ничего не выбрали."
        )

        self.assertTrue(_is_post_sale_vehicle_documents_other(text))
        self.assertEqual(classify_auto(text), ("OTHER", "OTHER"))

    def test_post_sale_documents_with_active_purchase_process_stay_op(self) -> None:
        text = (
            "Покупали автомобиль два года назад, нужна копия электронного ПТС на почту. "
            "Теперь хотим купить новый автомобиль. Какая цена и комплектация есть в наличии? "
            "Запишите нас на тест-драйв. Менеджер отдела продаж Андрей."
        )

        self.assertFalse(_is_post_sale_vehicle_documents_other(text))

    def test_17825_repeat_status_question_stt_is_other(self) -> None:
        text = (
            "Алло. Андрей, здравствуйте. Это Андрей, менеджер отдела продаж Тэнет "
            "Тольятти-Викинги на Заставной. По машине с вами акуальнобща? "
            "Нет, не актуально, Андрей, пока вообще не до этого. Передумали."
        )

        self.assertTrue(_is_op_repeat_followup_outbound(text))
        self.assertEqual(classify_auto(text), ("OTHER", "OTHER"))

    def test_primary_sales_consultation_stays_op_out(self) -> None:
        text = (
            "Алло, Иван, здравствуйте. Это Андрей, менеджер отдела продаж Тэнет "
            "Тольятти-Викинги. Вы оставляли заявку на новый автомобиль. "
            "Какая комплектация вас интересует и когда удобно приехать на тест-драйв?"
        )

        self.assertEqual(classify_auto(text), ("OP", "OP_OUT"))


if __name__ == "__main__":
    unittest.main()
