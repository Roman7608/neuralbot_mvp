"""Регрессия: перезвон по обещанию менеджера → Прочие (v2), даже при маркерах CRM/сайта."""
import unittest

from call_analytics.classify_by_transcript import (
    classify_auto,
    classify_by_transcript_v2,
    _extract_speaker_name,
    _is_op_all_managers_busy_callback_only_misc,
    _is_op_failed_transfer_callback_only_misc,
    _is_op_reception_phone_callback_no_manager_misc,
    _is_op_dealer_ringback_intro,
    _is_op_deferred_decision_repeat_opening,
    _is_op_outbound_callback_promise_repeat,
    _is_op_inbound_recent_dealer_contact_repeat,
    _is_op_ordered_vehicle_arrival_status_followup_other,
    _is_op_outbound_already_purchased_vehicle_followup_other,
    _is_op_kak_to_po_mashine_repeat_followup,
    _is_op_manager_service_consultation_without_vehicle_purchase_other,
    _is_op_primary_outbound_sales_opening,
    _is_op_repeat_followup_outbound,
    _is_outbound,
    _has_op_outbound_surface_markers,
    _speaker_not_op_manager,
)
from call_analytics.sto_booking_dimensions import infer_sto_booking_dimensions
from call_analytics.sto_to_rubric import infer_sto_to_rubric_type


class TestClassifyCallbackPromise(unittest.TestCase):
    def test_21841_client_already_bought_vehicle_is_other(self):
        t = (
            "Алло, Андрей, доброе утро. Илья, звоню с официального дилера Тенет Викинги. "
            "Заявка пришла, вы интересовались покупкой нового автомобиля Tenet T4L? "
            "Так я уже у вас взял седьмой. Да, взял. "
            "Поздравляю вас с покупкой. Всего доброго."
        )
        self.assertTrue(_is_op_outbound_already_purchased_vehicle_followup_other(t))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_21839_ordered_vehicle_arrival_status_is_other(self):
        t = (
            "Официальный дилер Чери Тенет, администратор Диана. Добрый день. "
            "Я у вас автомобиль заказывал ТTEN4L чёрного цвета. У вас было поступление автомобилей? "
            "Сейчас ждём распределения автомобилей от завода, как только появятся, сразу с вами свяжемся."
        )
        self.assertTrue(_is_op_ordered_vehicle_arrival_status_followup_other(t))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_callback_promise_phrases_detected(self):
        low = "вот обещал вам отзвониться по комплектации викинги"
        self.assertTrue(_is_op_outbound_callback_promise_repeat(low))
        self.assertTrue(_is_op_outbound_callback_promise_repeat("обещала перезвонить клиенту чери"))
        self.assertTrue(_is_op_outbound_callback_promise_repeat("звоню вам как обещал андрей"))
        # STT 18395: «бещал» ≈ «обещал»
        self.assertTrue(
            _is_op_outbound_callback_promise_repeat("бещал вам отзвониться.но смотрите платёж")
        )

    def test_call_18395_beschal_callback_promise_other(self):
        """18395: STT «бещал вам отзвониться» + расчёт платежа — Прочие, не OP_OUT."""
        t = (
            "Алло? Алексей, Андрей. менеджер отдела продаж Тольятти, Викинги наставк,"
            "бещал вам отзвониться.Но смотрите, э-э на 10 нет платёж будут 41 600. "
            "41 600 при условии 3 миллиона машин. первоначальный взнос у вас 400. "
            "машину в наличии? нет, дефицитная позиция. выставляю счёт 5 000. "
            "в Саратове 2 800. у нас платёж 41 700. визитку отправлю."
        )
        self.assertTrue(_is_op_outbound_callback_promise_repeat(t.lower()))
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_op_out_with_site_stays_other_when_callback_promise(self):
        t = (
            "Начинаем звонить абонента. Алло, Михаил, здравствуйте. Это Андрей, менеджер отдела продаж Викинги Чери. "
            "Вот обещал вам отзвониться по поводу комплектации. Вы оставляли заявку на нашем сайте на тест-драйв. "
            "Уточняю детали по комплектации премиум и цвету кузова, готов ответить на вопросы по кредиту и трейд-ин."
        )
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertTrue(_is_op_repeat_followup_outbound(t))

    def test_post_vacation_repeat_followup_other(self):
        """STT «Ильянет» = Илья+бренд; «был в отпуске» + уточнения → повторный исходящий, Прочие."""
        t = (
            "Алло. Это Ильянет Викинги на Заставной. Удобно? "
            "Да, я был в отпуске, вот хотел узнать, купили автомобиль себе, выбрали, приезжали к нам в салон?"
        )
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))

    def test_19027_recent_dealer_visit_and_talk_is_other(self):
        t = (
            "Официальный дилер Чери Tenet, администратор Диана. Добрый день. "
            "Я у вас дня два-три назад был, разговаривал там с вашим менеджером. "
            "Переведите меня к нему. Андрей, менеджер отдела продаж Викинги. "
            "Насчёт Tenet T7 полноприводного: нужна белая комплектация Prime за наличные. "
            "Сколько будет полная стоимость со скидкой?"
        )
        self.assertTrue(_is_op_inbound_recent_dealer_contact_repeat(t))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_future_visit_in_two_three_days_is_not_repeat(self):
        t = (
            "Официальный дилер Чери Tenet. Менеджер отдела продаж Андрей. "
            "Хочу купить Tenet T7, через два-три дня буду у вас в салоне. "
            "Подскажите стоимость комплектации Prime."
        )
        self.assertFalse(_is_op_inbound_recent_dealer_contact_repeat(t))
        self.assertEqual(classify_auto(t), ("OP", "OP_IN"))

    def test_19113_op_manager_only_to_consultation_is_other(self):
        t = (
            "Официальный дилер Чери и Тенет, администратор Диана. "
            "Соедините с отделом продаж, Андрей работает? По покупке нового автомобиля? Да. "
            "Андрей, менеджер отдела продаж Tenet. Ты говорил, в конце июля нужно будет "
            "записаться мне на ТО, первое. С кем мне всё время ездить? "
            "Просто звоните на ресепшен, записывайтесь. За сколько дней? "
            "Чем раньше, тем лучше, больше выбор по времени."
        )
        self.assertTrue(_is_op_manager_service_consultation_without_vehicle_purchase_other(t))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_vehicle_purchase_with_to_question_stays_op_in(self):
        t = (
            "Официальный дилер Чери и Тенет. Андрей, менеджер отдела продаж. "
            "Хочу купить новый Tenet T7 в комплектации Prime, какая цена и есть ли в наличии? "
            "И ещё скажите, когда потом проходить первое ТО."
        )
        self.assertFalse(_is_op_manager_service_consultation_without_vehicle_purchase_other(t))
        self.assertEqual(classify_auto(t), ("OP", "OP_IN"))

    def test_19134_prior_talk_and_deferred_decision_is_other(self):
        t = (
            "Александр, здравствуйте, это Андрей, менеджер отдела продаж "
            "Тольятти-Викинги на Заставной. По машине с общались, вот звоню. "
            "Мы пока это тормознём, вроде бы пока передумали. "
            "Понятно. На какой срок решили отложить? Когда можно в следующий раз позвонить? "
            "Пока у нас другой вопрос решается, изменились планы."
        )
        opening = t.lower()
        self.assertTrue(_is_op_deferred_decision_repeat_opening(opening))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_primary_sales_call_with_client_thinking_stays_op_out(self):
        t = (
            "Алло, Александр, это Андрей, менеджер отдела продаж Викинги на Заставной. "
            "Звоню проконсультировать по Tenet T7: есть в наличии комплектация Prime. "
            "Спасибо, мы пока подумаем, возможно, планы изменятся."
        )
        self.assertFalse(_is_op_deferred_decision_repeat_opening(t.lower()))
        self.assertEqual(classify_auto(t), ("OP", "OP_OUT"))

    def test_19138_repeat_phrase_after_noisy_manager_preamble_is_other(self):
        t = (
            "Служебная реплика менеджера до соединения. Алло. Евгений, здравствуйте, "
            "это Андрей, менеджер отдела продаж Tenet Тольятти-Викинги на Заставной. "
            "Вот по машине с вами. — Что, ещё раз, не слышу. — Андрей, менеджер отдела продаж. "
            "Мы с вами как-то по машине общались. — Да-да. Пока на паузе мы. "
            "Понятно. Когда можно в следующий раз попробовать позвонить, узнать планы?"
        )
        self.assertTrue(_is_op_kak_to_po_mashine_repeat_followup(t.lower()[:2400]))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_19449_days_ago_car_talk_repeat_followup_other(self):
        t = (
            "Алло. Олеся, здравствуйте, это Андрей, менеджер отдела продаж "
            "Тольятти Викинги на Заставной. "
            "Мы как-то дня два назад с вами по машине общались. "
            "Звоню узнать, купили, выбрали что-нибудь? "
            "Вообще рассматриваете покупку? Пока думаем, машина не понравилась. "
            "Покупку планируем, но не знаем, на чём остановиться."
        )
        self.assertTrue(_is_op_kak_to_po_mashine_repeat_followup(t.lower()[:2400]))
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_19572_test_drive_interest_followup_other(self):
        t = (
            "Алло. Иван, здравствуйте, это Андрей, менеджер отдела продаж "
            "Тольятти-Викинги на Заставной. "
            "Вот машиной у нас интересовались, тест-драйв проходили. "
            "Звоню узнать, заинтересовались? "
            "Нет, мы в другом месте машину взяли. Что купили, если не секрет? Чанган."
        )
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_26921_manager_left_client_base_followup_is_other(self):
        t = (
            "Алло. Александр, здравствуйте, это Андрей, менеджер отдела продаж Tenet "
            "Тольятти-Викинги на Заставной. Вы у нас в салоне машины интересовались, "
            "с Анастасией общались. Настя у нас перевелась в другую организацию. "
            "Я вот её клиентов обзваниваю, звоню узнать, есть в планах покупка автомобиля "
            "или не планируете?"
        )
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_26924_prior_interest_ranee_status_check_is_other(self):
        t = (
            "Алло. Сергей, здравствуйте, это Андрей, менеджер отдела продаж "
            "Тольятти-Викинги на Заставной. Машиной у нас интересовались ранее. "
            "Вот звоню узнать, есть в планах покупка автомобиля или не планируете?"
        )
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_27004_promised_monday_dozvon_is_other(self):
        t = (
            "Алло. Владимир, здравствуйте, это Андрей, менеджер отдела продаж "
            "Тольятти Викинги на Заставной. "
            "По машине с вами общались, обещал в понедельник дозвониться. "
            "Есть новости по комплектации, поэтому и набрал."
        )
        self.assertTrue(_is_op_outbound_callback_promise_repeat(t.lower()))
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_21339_post_sale_vehicle_condition_check_is_other(self):
        t = (
            "Алло. Здравствуйте. Это Андрей, менеджер отдела продаж Тэнет Викинги. "
            "Хотела уточнить, как у вам с автомобилем, всё ли в порядке? "
            "Да, с автомобилем-то как бы всё нормально."
        )
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_periodic_crm_callback_op_in_becomes_other(self):
        """Периодический CRM: «созваниваемся» + «в прошлый раз обозначили» — Прочие, не OP_IN (7804)."""
        t = (
            "Здравствуйте, это Викинги Чери, менеджер отдела продаж. "
            "Мы с вами периодически созваниваемся раз в три месяца. "
            "В прошлый раз вы обозначили, что интересна вам четвёрка в разных комплектациях "
            "и на вариаторе, и на роботе. Вопрос открытый или уже закрыли?"
        )
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))

    def test_recent_price_interest_followup_op_out_other(self):
        """«Не так давно интересовались ценой» + уточнение решения — Прочие, не OP_исх (7836)."""
        t = (
            "Начинаем звонить абонента. Алло, Мария, здравствуйте. Это Илья, менеджер отдела продаж Викинги Чери. "
            "Удобно вам говорить? Вы не так давно интересовались ценой автомобиля. "
            "Хотел уточнить, какое-то решение приняли?"
        )
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))

    def test_call_9340_stt_double_allo_repeat_followup_other(self):
        """Регрессия 9340: исходящий CRM-перезвон (STT: двойное алло, ИльяВикинги) → Прочие, не ОП вх."""
        t = (
            "а. . Алло. Алло, Александр, здравствуйте. Это ИльяВикинги. Минутку. "
            "Да, Илья, да, здравствуйте. Хотел узнать, нашли машину себе, нет? "
            "Делали вам предложение, от которого... Да, вчера предоплату внёс."
        )
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_14786_failed_op_transfer_callback_other(self):
        """14786: перевод на Захарова не состоялся — только перезвон — Прочие."""
        t = (
            "Администратор. саллон, Оксана, вечер добрый! Покупка автомобиля Cherr Tig 4. "
            "сейчас переключу на него. контактный номер на случай разъединения линии оставьте. "
            "Переключаю. Алло, Елена? Алло, да. Если он Чери несколько минут вам перезвонит, "
            "удобно будет? Да, удобно будет, я подожду."
        )
        self.assertTrue(_is_op_failed_transfer_callback_only_misc(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_11109_failed_op_manager_transfer_other(self):
        """Регрессия 11109: «соедините с Андреем» → не берёт трубочку → переключу позже — Прочие."""
        t = (
            "Администратор саллона. Оксана, добрый день. "
            "А соедините ещё раз, пожалуйста. с Андреем. сейчас я попытаюсь. "
            "Алло? Алло. Не берёт трубочку, видимо, с клиентом где-то на стоянке. "
            "Чуть попозже наберите, переключу. А скажите, у вас есть на тест-драйве? Есть."
        )
        self.assertTrue(_is_op_all_managers_busy_callback_only_misc(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_13597_op_out_client_busy_defer_other(self):
        """13597: исх. ОП «общались по машине», клиент «неудобно, завтра созвонимся» — Прочие."""
        t = (
            "Алло. Юлия, здравствуйте, это Андрей, менеджер отдела продаж Т Тольятти-Викинги на Заставной. "
            "Общались с вами по машине, вот. — Да, сейчас неудобно говорить, "
            "я сейчас на работе. Давайте завтра созвонимся.— Да, хорошо. — Угу, спасибо большое."
        )
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_11257_avito_lead_op_out(self):
        """Регрессия 11257: исх. «Алло, Кирилл… Меня зовут Евгений, дилерский центр… запрос с Авито на T8» → ОП_исх."""
        t = (
            "Алло. Кирилл, здравствуйте. Здравствуйте. Меня зовут Евгений, Дилерский центр. "
            "Cherry Тенет Викинги, город Тольятти. Удобно говорить? Да-да-да-да. "
            "У нас был запрос Чери Авито на Tenet T8? Да. Что именно интересно? T8 Ultra. "
            "В кредит рассматривать, платёж до 250 тысяч в месяц."
        )
        self.assertTrue(_has_op_outbound_surface_markers(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OP", "OP_OUT"))
        self.assertEqual(classify_auto(t), ("OP", "OP_OUT"))

    def test_call_11260_client_called_back_op_out(self):
        """Регрессия 11260: исх. «вы сегодня уже звонили, спрашивали про Tenet T7» — не Прочие (ложный «звонил»)."""
        t = (
            "Алло. Виктор, добрый день. Добрый. Меня зовут Евгений, Дилерский центр Cherry Тэнет Викинги. "
            "Если я правильно понял, вы сегодня уже звонили, спрашивали про автомобиль Tenet T7 Прайм. "
            "Полный привод интересует? Полный. В наличии одна машина красного цвета. Покупку планируете когда?"
        )
        self.assertFalse(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OP", "OP_OUT"))
        self.assertEqual(classify_auto(t), ("OP", "OP_OUT"))

    def test_call_23680_first_successful_contact_after_failed_dials_is_op_out(self):
        """23680: неудачные дозвоны по новой заявке не превращают первый разговор в repeat follow-up."""
        t = (
            "Алло. Наталья, здравствуйте, Андрей, менеджер отдела продаж Tenet Тольятти-Викинги. "
            "У вас заявка была от вас, машиной интересовались. Не мог вам дозвониться, "
            "несколько раз звонил раза три. Звоню узнать, нужно проконсультировать по автомобилю, "
            "по ценам, по акциям? Да, очень интересно. Chery T4 есть в наличии. "
            "Хотелось бы приехать, посмотреть и пройти тест-драйв. "
            "Свой Citroen сдадим в трейд-ин, новый автомобиль рассмотрим в кредит или рассрочку. "
            "Приезжайте в салон, всё рассчитаем."
        )
        self.assertTrue(_is_op_primary_outbound_sales_opening(t.lower()))
        self.assertFalse(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OP", "OP_OUT"))
        self.assertEqual(classify_auto(t), ("OP", "OP_OUT"))

    def test_service_assistant_transfer_to_op_manager_is_op_in(self):
        """Линия сервиса приняла звонок, перевод на менеджера ОП состоялся — ОП_вх, не СТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Слушаю вас. "
            "Здравствуйте, хотел узнать по поводу покупки Chery Tiggo 7, новый автомобиль. "
            "Сейчас переведу вас на менеджера отдела продаж. Оставайтесь на линии. "
            "Менеджер отдела продаж Евгений. Здравствуйте. "
            "Интересует Tiggo 7 полный привод, что в наличии? "
            "Комплектация Active, кредит, первоначальный взнос 30%."
        )
        self.assertEqual(classify_auto(t), ("OP", "OP_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, "OP_IN", dims, department="OP")
        self.assertEqual(reason, "not_sto_call_type")

    def test_call_28728_service_to_admin_test_drive_slot_is_other_not_sto(self):
        """28728: запись на тест-драйв через администратора без консультации менеджера — Прочие."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "А скажите, а на тест-драйв к вас можно записаться? На какой тест-драйв? Нового автомобиля? "
            "На т семь. Ну, я вас переключу на администратора. "
            "Администратор салона Анастасия, здравствуйте. "
            "А скажите, а на тестдрайв к вас можно записаться на Т семь? "
            "Василий? Да, конечно, на какое число вы хотели бы? "
            "Вы с менеджером хотите сейчас поговорить, задать ему какие-то вопросы? Неет. "
            "Оставьте номер телефона, чтобы я записала вас. "
            "Я записала тогда вас на 6 октября на 1-2. Права, да. "
        )
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_11240_failed_transfer_managers_busy_other(self):
        """Регрессия 11240: админ переводит на менеджера → звонок вернулся → перезвонят — Прочие."""
        t = (
            "Администратор салона Анастасия, здравствуйте. "
            "Скажите, пожалуйста, коннет 7 правильно комплектации 8 есть? "
            "Виктор, я сейчас переведу вас на менеджера по продажам. "
            "Оставьте номер телефона на случай разъединения линий. "
            "Хорошо, Виктор, перевожу вас. Оставайтесь на линию. "
            "Виктор, звонок вернулся назад. Сейчас менеджеры с клиентами, "
            "я передам ваш номер телефона, вас перезвонят."
        )
        self.assertTrue(_is_op_all_managers_busy_callback_only_misc(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_11100_dealer_ringback_op_out(self):
        """Регрессия 11100: «звоночек от вас был» + перевод на ОП + «вы звонили по поводу» → ОП исх."""
        t = (
            "Алло, добрый вечер. Официальный дилер Чери, это администратор салона Оксана. "
            "Звоночек от вас был. Отдел продаж, сервис интересовал или уже созвонились? "
            "Нет, не созвонился. У меня пропущенный от вас был. Нет, отдел продаж. "
            "Переключу. Алексей, добрый день. Анастасия, менеджер отдела продаж. "
            "Вы звонили по поводу автомобиль Тэнет 7, я вас просто перезвонила."
        )
        self.assertTrue(_is_op_dealer_ringback_intro(t))
        self.assertTrue(_is_outbound(t))
        self.assertTrue(_has_op_outbound_surface_markers(t))
        self.assertFalse(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OP", "OP_OUT"))
        self.assertEqual(classify_auto(t), ("OP", "OP_OUT"))

    def test_call_26187_ringback_after_consultation_status_is_other(self):
        """26187: ringback после прошлой консультации + «мы пока думаем» — статусный follow-up (OTHER)."""
        t = (
            "Алло. Здравствуйте. Официальный дилер Cherry Тенет Викинги. "
            "Звоночек от вас был в отдел продаж. "
            "Консультацию получили и остались ещё вопросы? "
            "Да, получили. Илья мне всё рассказал, мы пока ещё думаем."
        )
        self.assertTrue(_is_op_dealer_ringback_intro(t))
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_11123_site_lead_op_out_not_tam_menedzher(self):
        """Регрессия 11123: «меня Андрей зовут» + заявка с сайта; «там менеджер» в середине → ОП исх., не Прочие."""
        t = (
            "Алло? Алло. Да, Александр, добрый день! Здравствуйте! Алло, да, меня Андрей зовут, "
            "компания Викинги Тольятти Tenet на Заставной улице. Заявку нам вот отправляли на сайте "
            "по оценке вашего автомобиля, новый рассматриваете? Да, хочу новый в кредит, трейд-ин. "
            "Tenet T4, комплектация, стоимость, кредит, официальный дилер. "
            "Но в итоге там менеджер ничего и не написал ему в Пензе."
        )
        self.assertEqual(_extract_speaker_name(t), "андрей")
        self.assertFalse(_speaker_not_op_manager(t))
        self.assertTrue(_is_outbound(t))
        self.assertFalse(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OP", "OP_OUT"))
        self.assertEqual(classify_auto(t), ("OP", "OP_OUT"))

    def test_call_11125_visit_followup_other(self):
        """Регрессия 11125: «приезжали, смотрели» + «купили-не купили» → Прочие, не ОП исх."""
        t = (
            "Алло. Да, Андрей, добрый день. Да, добрый день. Это Андрей звонит компании Викинги. "
            "Звонит компания Викинги Тольятти на Заставной улице. "
            "Приезжали ко мне, машину сами смотрели, да, вот под такси. "
            "Я хотел узнать, купили-не купили новый автомобиль? "
            "Ну, мы пока решаем, я же там написал всё. Понял. "
            "Я чё-то не понял, до там дозвониться не могу несколько, не знаю, там, мне кажется, весь месяц или нет? "
            "Ну, наверное, неделю точ. Ну, наверное, неделю точно. Я там в смс всё написал как бы, да. "
            "Думаю, всё-всё понятно. Понял. А я думал, а то вдруг, может, телефон не работает как бы "
            "иличё-то там сбой какой-то идёт. Ну, вы сами как думаете, примерно там это на июнь, там, на июль, "
            "зима, не знаю, там. ПоН не могу. меня мы решаем с братом пока. всё. "
            "У там телефон сотывый есть же мой, всё нормально? Да-тому всё. Да, потому. всё, давайте."
        )
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_26506_post_showroom_visit_status_followup_other(self):
        """26506: после визита к Илье («подъезжали», «посмотрели машину») status-check — Прочие, не ОП_исх."""
        t = (
            "Да, алло. Александр, здравствуйте, что Андрей, менеджер отдела продаж "
            "Тэнет Тольятти Викинги на Заставной. "
            "К Илье подъезжали. Илья мне что передал, что, ну, посмотрели машину. "
            "Я звонил узнать, как машина, понравилась, не понравилось. "
            "Ну, он мне сказал, пока не прямо вот покупка, а через какое-то время, да? "
            "Да-да. Ну, давайте октябрь где-нибудь. Октябрь. Ну, начало, да."
        )
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_28475_kak_to_mashinoi_interesovalis_is_other(self):
        """28475: «как-то машиной интересовались» в начале исходящего ОП — Прочие, не ОП_исх."""
        t = (
            "Алло? Роман, здравствуйте, это Андрей, менеджер отдела продаж "
            "Тэнет Тольятти Викинги на Заставной. "
            "Как-то машиной интересовались. Да, ну В интересовались? "
            "Да, но я планирую, наверное, завтра подъеду. Сегодня не получается у меня. "
            "Работаем завтра с 9:00 до 6:00. Хочу подъехать посмотреть."
        )
        self.assertTrue(_is_op_kak_to_po_mashine_repeat_followup(t.lower()[:2400]))
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_11099_dropped_call_repeat_followup_other(self):
        """Регрессия 11099: «мы с вам общались, связь прервалась» после приветствия → Прочие, не ОП исх."""
        t = (
            "Алло. Андрей, добрый вечер. Анастасия, автосалон Викинги. — Саун Викинги? — Да. "
            "— Да, сейчас удобно разговаривать? Мы с вам общались, но связь с вам прервалась. "
            "— Да, удобно. — Вы уже съездили в автосалон? — Да, мне не одобрили кредит. "
            "Какую стоимость предложили? Кредит под 14 процентов. Трейд-ин, комплектация, официальный дилер."
        )
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_27863_dobry_den_eshe_raz_obshalis_s_vami_is_other(self):
        """27863: «добрый день ещё раз ... общались с вами» — повторный OP follow-up, Прочие."""
        t = (
            "Алло? Алло. Да, Сергей. Добрый день ещё раз. Андрей, Тольятти. Общались с вами. "
            "По цене как обсуждали: таких программ нет. "
            "Максимум можем предложить 3-700 наличкой, с трейд-ин 3-500. "
            "Я понял вас. Если не получится — приезжайте, все посчитаем."
        )
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_9402_test_drive_repeat_followup_other(self):
        """Регрессия 9402: перезвон после тест-драйва на Заставной → Прочие, не ОП вх."""
        t = (
            "Д. Алло. Алло, Михаил, здравствуйте.е. Чери Центр Викингов на Заставной "
            "были у нас проходили тест-драйв на семёрке. Хотел узнать, купили машину, нет, выбрали себе что-нибудь?"
        )
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_11625_managers_busy_callback_only_other(self):
        """11625: админ → перевод на ОП → «все заняты» → номер/перезвон — Прочие, не OP_IN."""
        t = (
            "Официальный дилер Cheritent, администратор Диана. "
            "хотел бы уточнить, мне вот машину надо купить. Стоимость хотел уточнить. "
            "Tenet Tenet T7. Александр. "
            "Переведу вас на менеджера отдела продаж, подскажет вам по автомобилю. "
            "Оставайтесь на линии. "
            "Алло, Александр, менеджер, у нас сейчас вс, к сожалению, заняты. "
            "контактный номер телефона ваш запишу, передам, перезвонят, вам, проконсультируют."
        )
        self.assertTrue(_is_op_all_managers_busy_callback_only_misc(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_11625_manager_admin_diana_not_client_alexander(self):
        """11625: «Алло, Александр, менеджер… заняты» — клиент, в поле менеджера админ Диана."""
        from analyze_call_quality import extract_manager_name_from_full_transcript

        t = (
            "Официальный дилер Cheritent, администратор Диана. "
            "хотел бы уточнить, мне вот машину надо купить. Стоимость хотел уточнить. "
            "Tenet Tenet T7. Александр. "
            "Переведу вас на менеджера отдела продаж, подскажет вам по автомобилю. "
            "Оставайтесь на линии. "
            "Алло, Александр, менеджер, у нас сейчас вс, к сожалению, заняты. "
            "контактный номер телефона ваш запишу, передам, перезвонят, вам, проконсультируют."
        )
        mgr = extract_manager_name_from_full_transcript(t, department="OTHER")
        self.assertEqual(mgr, "Администратор Диана")
        self.assertNotEqual(mgr, "Александр")

    def test_call_9350_admin_transfer_op_in_not_other(self):
        """Регрессия 9350: админ → перевод на менеджера, клиент интересуется Чери → ОП вх., не Прочие."""
        t = (
            "Администратор салона. Оксана, добрый день! Здравствуйте, Оксана, меня зовут Алик. "
            "меня вот вопрос интересует Чери четвёртая серия. Полный привод. "
            "Алик, до этого с кем-то общались у нас из менеджера? да-да, в других салонах. "
            "сейчас переключу нас на менеджера. "
            "Добрый день, официальный дилер Тольятти. Викинги, меня зовут Андрей, нас Алик, правильно? "
            "интересуюсь, просто присматриваю машины. хотим купить машины в течение двух недель."
        )
        self.assertFalse(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OP", "OP_IN"))
        self.assertEqual(classify_auto(t), ("OP", "OP_IN"))

    def test_call_13495_trade_in_payment_option_op_in_not_other(self):
        """13495: админ → Евгений, T7 лизинг; «трейд-ин» в перечне оплаты — OP_IN, не Прочие."""
        t = (
            "Официальный дилер Cеritент, администратор Диана. Добрый день. Здравствуйте. "
            "Автомобиль интересует? Какой? Автомобиль? Определились с моделью? Да. "
            "Книга семь, ну, или интернет-семь. Подскажите, Так могу к вам обращаться? Сергей. "
            "Сергей, переведу вас на менеджера отдела продаж. Оставайтесь на линии. "
            "Сергей, здравствуйте, меня зовут Евгений. Слушаю вас. "
            "Иресует на7 на полном приводе. Какой цвет нужен? "
            "То есть это будет кредит, трейд-ин или наличка? Лизинг? Лизинг. "
            "полный привод 7. наш лизинговый менеджер с вами свяжется."
        )
        self.assertEqual(classify_by_transcript_v2(t), ("OP", "OP_IN"))
        self.assertEqual(classify_auto(t), ("OP", "OP_IN"))

    def test_call_13355_reception_phone_callback_no_manager_other(self):
        """Регрессия 13355: стойка записала номер, обещала перезвон — Прочие, не ОП."""
        from database.postgresql_manager import CallAnalyticsDB

        row = CallAnalyticsDB.get_call_with_details(13355)
        t = row["transcription"]["transcription_text"]
        self.assertTrue(_is_op_reception_phone_callback_no_manager_misc(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_27958_used_cars_fine_transfer_is_other(self):
        """27958: «по БУ-машинам ... по поводу штрафа» — Прочие, не ОП."""
        t = (
            "Администратор салона Анастасия, здравствуйте. "
            "Девушка, скажите, пожалуйста, по БУ-машинам пришел кто-то? Я по поводу штрафа. "
            "Давайте я переведу вас сейчас. "
            "Алло. Да, по БУ-автомобилям. Шестого сентября мы по трейд-ин сдали автомобиль, "
            "а от восьмого пришел штраф. Договор купли-продажи от шестого числа. "
            "Распечатывайте штраф, привозите нам, мы отправим покупателю."
        )
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))

    def test_call_27985_dogovarivalis_sozvonitsya_followup_is_other(self):
        """27985: «договаривались созвониться ... интересовались Тэнет-8» — повторный OP follow-up, Прочие."""
        t = (
            "Алло, Константин, добрый день. Евгений, дилерский центр Чери Тэнет Викинги, город Тольятти. "
            "Мы с вами договаривались созвониться в ближайшие два дня. "
            "Вы автомобилем Тэнет-8 интересовались. Как у вас дела двигаются по этому поводу? "
            "Я пока воздержусь от приобретения, не устраивают условия и процентная ставка."
        )
        self.assertTrue(_is_op_repeat_followup_outbound(t))
        self.assertEqual(classify_by_transcript_v2(t), ("OTHER", "OTHER"))
        self.assertEqual(classify_auto(t), ("OTHER", "OTHER"))


if __name__ == "__main__":
    unittest.main()
