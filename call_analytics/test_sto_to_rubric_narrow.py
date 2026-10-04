"""Регрессия узкой рубрики СТО_ТО (НЕ_ТО = sto_to_rubric_type NULL)."""
import unittest

from call_analytics.classify_by_transcript import classify_auto
from call_analytics.sto_booking_dimensions import infer_sto_booking_dimensions
from call_analytics.sto_to_rubric import infer_sto_to_rubric_type, infer_sto_appointment_agreed


class TestStoToRubricNarrow(unittest.TestCase):
    def test_9371_balancing_third_party_station_not_narrow_to(self):
        """9371: балансировка + «любой станции ТО» — совет, не запись на ТО у дилера."""
        t = (
            "Администратор салона. Оксана, добрый день! Здравствуйте, девушка, подскажите, "
            "а как у вас балансировку сделать? Переключу на диспетчера. "
            "Викинги сервис, Андреева Юлия. Слушаю. Девушка, можно у вас балансировку сделать? "
            "вибрация в ролик. Запись сейчас ведётся только на июнь. "
            "балансировку вы можете сделать в любом шиномонтажном центре, "
            "необязательно к дилеру ехать. "
            "балансировку вы можете сделать на любой станции технического обслуживания, то есть."
        )
        dept, ct = classify_auto(t)
        self.assertIn((dept, ct), (("STO", "STO_IN"), ("OTHER", "OTHER")))
        ct_for_rubric = "STO_IN"
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct_for_rubric, dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertNotEqual(dims.get("work_type"), "to")

    def test_9476_past_to_visit_addendum_to_july_slot_not_narrow_to(self):
        """9476: была сегодня на ТО, слот 3 июля — дописать свист, не новая СТО_ТО_вх."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "Я сегодня была на техническом обслуживании с автомобилем. "
            "жалоба по свисту. рулевую колонку подтягивали, не помогло. "
            "я вот записалась третьего июля. "
            "Может быть, можно и заодно вот этот свист добавить? "
            "третье число у нас среда. давайте когда допишем. "
            "Хорошо, давайте я добавлю, да, на третье число когда этот вопрос ещё."
        )
        dept, ct = classify_auto(t)
        self.assertIn((dept, ct), (("STO", "STO_IN"), ("OTHER", "OTHER")))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_11708_past_delali_technicheskoe_stabilizer_not_narrow_to(self):
        """11708: «к нам заезжали, да, делали техническое обслуживание» — прошлый визит; запись на стук стойки — НЕ_ТО."""
        t = (
            "Викинги Челстин, Сервис Андреева, Юлия, слушаю. "
            "мне меняли стойки стабилизаторов в апреле. стук опять появился в передней подвеске. "
            "надо бы записаться. Срищак Николай Петрович. Nissan EXrella госномер 105. "
            "Так, в апреле к нам заезжали, да, делали техническое обслуживание. "
            "стойки, вижу, обе поменяли, передние. сейчас такой же стук у вас? "
            "Ближайший на девятое июня на 8:30. записали."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertNotEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_call_11662_dispatcher_after_hours_admin_callback_not_narrow_to(self):
        """11662: админ, диспетчер до 17:30, клиент перезвонит — нет диалога с приёмкой, НЕ_ТО."""
        t = (
            "пециальный диджер енр, администратор Диана. Добрый день! Добрый день. "
            "Мне на записаться на ТО и узнать сумму. "
            "Диспетчер у нас работает до полшестого. "
            "Контактный номер телефона оставить или завтра можете перезвонить? "
            "Лучше я завтра перезвоню тогда вам. Хорошо, до свидания."
        )
        dept, ct = classify_auto(t)
        self.assertIn((dept, ct), (("STO", "STO_IN"), ("OTHER", "OTHER")))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_23709_dispatcher_not_working_client_calls_tomorrow_not_narrow_to(self):
        """23709: диспетчер не работает, 8:30 — время повторного звонка, а не слот ТО."""
        t = (
            "Администратор салона. Оксана, добрый день. Добрый день. "
            "Девушка, я хотел записаться на ТО, ТО четвёртое, на Chery Tiggo 4 Pro. "
            "Диспетчер сегодня не работает, завтра в 8:30 можете набрать. Удобно будет? "
            "Угу, всего доброго."
        )
        dept, ct = classify_auto(t)
        self.assertIn((dept, ct), (("STO", "STO_IN"), ("OTHER", "OTHER")))
        dims = infer_sto_booking_dimensions(t)
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "no_actual_reception_contact")

    def test_call_13554_dashcam_programming_chery9_not_narrow_to(self):
        """13554: прописать видеорегистратор на Chery 9 — НЕ_ТО, work_type other_work."""
        t = (
            open("/home/vikingi/VikingiAll/logs/vikingi_autofetch.log")
            .read()
            .split("call_id=13554")[1]
            .split("--- Транскрипт ---")[1]
            .split("---")[0]
            .strip()
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_21729_existing_to_slot_with_dashcam_issue_not_new_to_booking(self):
        """21729: есть запись на ТО, но предмет звонка — видеорегистратор; НЕ_ТО и без новой записи."""
        t = (
            "Компания Викинги, Юлия, диспетчер сервис. "
            "У вас вопрос по видеорегистратору? Да, он слетел со стекла, отломал провод. "
            "Специалисты по дополнительному оборудованию работают только в будние дни. "
            "Я к вам на ТО привезу даже завтра машину. Ну, вы на ТО завтра записаны? Да. "
            "Завтра по факту посмотрит мастер-приёмщик, запись подтверждаем вашу. "
            "По регистратору напишу комментарий."
        )
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "existing_visit_clarification")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_23200_existing_to_with_warranty_routing_is_not_narrow_to(self):
        """23200: уже записан на ТО, звонок по гарантийному ремонту/дроп-офф — НЕ_ТО, work_type warranty."""
        t = (
            "Официальный дилер, администратор Диана. "
            "Как мне связаться с гарантийным ремонтом? "
            "Я записан в понедельник на 9:00 на нулевое ТО. "
            "Могу в воскресенье пригнать машину и оставить ключи? "
            "Переведите на диспетчера сервиса, пожалуйста."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "warranty")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "warranty_existing_visit_clarification",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "existing_to_warranty_repair_routing_not_narrow_to")

    def test_21564_outbound_repeat_followup_is_not_narrow_to(self):
        """21564: исходящий звонок-продолжение диспетчера — НЕ_ТО (не новый цикл записи на ТО)."""
        t = (
            "Сергей, ещё раз добрый день, компания Викинги, Юлия, диспетчер. По поводу сста ТТО. "
            "А, смотрите, ТО объёмное получается у нас. Как я вам уже говорила, меняется масло двигателя, "
            "фильтры, свечи зажигания, тормозная жидкость и антифриз. "
            "Всё, тогда 13-го августа, время 12:00 дня, вас записали. "
            "Звоночек от нас накануне тоже ожидайте, напомним о записи."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_28058_outbound_today_talk_repeat_followup_is_not_narrow_to(self):
        """28058: «добрый день ещё раз ... сегодня общались» — повторный STO_OUT, НЕ_ТО."""
        t = self._transcript_from_log(28058)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "outbound_repeat_followup_not_narrow_to")

    def test_28564_outbound_error_correction_continuation_is_not_narrow_to(self):
        """28564: «добрый день ещё раз» + «нашли ошибку» по объёму масла — продолжение, НЕ_ТО."""
        t = self._transcript_from_log(28564)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "outbound_repeat_followup_not_narrow_to")

    def test_28660_inbound_immediate_repeat_after_broken_call_is_not_narrow_to(self):
        """28660: «звонила только что ... порвался звонок» — продолжение, НЕ_ТО."""
        t = self._transcript_from_log(28660)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "outbound_repeat_followup_not_narrow_to")

    def test_27970_need_to_do_to_keeps_narrow_to_in(self):
        """27970: явное «мне надо будет ТО сделать» сохраняет СТО_ТО_вх."""
        t = self._transcript_from_log(27970)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_28042_to_price_dialog_after_handoff_is_sto_to_in(self):
        """28042: после перевода на диспетчера обсуждают ТО/стоимость — СТО_ТО_вх."""
        t = self._transcript_from_log(28042)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_29392_to_price_dialog_with_slot_and_zapishite_sets_booking_fact(self):
        """29392: цена ТО + «Запишете, да?» + согласованный слот = факт записи должен быть Да."""
        t = self._transcript_from_log(29392)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"), msg=f"expected booking=True, dims={dims}")
        self.assertNotEqual((dims.get("evidence") or {}).get("booking_intent"), "to_consultation_without_booking")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_call_13576_dispatcher_before_hours_admin_callback_not_narrow_to(self):
        """13576: админ, диспетчер до 17:00 (STT «до пяти часов работает»), клиент перезвонит — НЕ_ТО."""
        t = (
            "Официальный дилер Чиrн, администратор Диана. Добрый день. "
            "Здравствуйте, я хотела бы на ТО записаться. "
            "Диспетчер у нас до пяти часов работает. "
            "Могу номер телефона контактный записать или вы можете сами завтра перезвонить. "
            "Ну, во сколько вам можно перезвонить? "
            "А-а, он с восьми часов работает у нас. "
            "ладно, хорошо, спасибо. Сами перезвонить? "
            "Да-да, я перезвоню. Хорошо, спасибо. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_14354_admin_off_hours_client_callback_stt_not_narrow_to(self):
        """14354: админ, диспетчер до 17:00, STT «перезвонм» — нет записи, НЕ_ТО."""
        t = (
            open("/home/vikingi/VikingiAll/logs/vikingi_autofetch.log")
            .read()
            .split("call_id=14354")[1]
            .split("--- Транскрипт ---")[1]
            .split("---")[0]
            .strip()
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "no_actual_reception_contact")

    def test_14402_admin_no_dispatcher_monday_only_not_narrow_to(self):
        """14402: «диспетчера нет», запись только в понедельник — клиент ушёл без слота, НЕ_ТО."""
        t = (
            open("/home/vikingi/VikingiAll/logs/vikingi_autofetch.log")
            .read()
            .split("call_id=14402")[1]
            .split("--- Транскрипт ---")[1]
            .split("---")[0]
            .strip()
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "no_actual_reception_contact")

    def test_18454_polina_no_dispatchers_weekend_not_narrow_to(self):
        """18454: Полина, диспетчеров нету, сервис выходные — НЕ_ТО, без записи."""
        t = (
            "Официальный дилер бренда Тэнет, меня зовут Полина. Добрый день. "
            "Здравствуйте, я хотела бы на ТО записаться плановый. — Угу. "
            "У нас сервис не работает в субботу-воскресенье. можете в понедельник позвонить? "
            "— Вы меня даже записать не можете? К сожалению, нет, диспетчеров нету. "
            "Понятно, спасибо. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "no_actual_reception_contact")

    def test_18455_polina_weekend_monday_only_booking_not_narrow_to(self):
        """18455: Полина, сервис выходные, запись только в пн — НЕ_ТО, без записи."""
        t = (
            "Официальный дилер бренда Туnт. меня зовут Полина. Добрый день. "
            "Скажите, пожалуйста, а можно записаться на ТО, замену фильтра? Масляного. "
            "У нас сервис не работает в субботу-воскресенье. "
            "А только в понедельник вообще надо можно. То есть и записаться тоже, допустим. "
            "я хотела на понедельник записаться, то есть это только в понедельник. "
            "Только в понедельник можно записаться? А, понятно. Ну всё, ладненько, спасибо. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "no_actual_reception_contact")

    def test_18515_weekend_admin_no_to_booking_not_narrow_to(self):
        """18515: админ сообщает будний график; клиент позвонит завтра — слота нет, НЕ_ТО."""
        t = (
            "Официальный дилер Чери и Тенет Викинги на Заставной, администратор Диана. "
            "Добрый день! Здравствуйте, я бы хотел записаться на ТО. "
            "С понедельника по пятницу, с 8 до 5 можно записаться. "
            "А в выходные перестали работать? Да, сейчас не работает по выходным. "
            "Я не подскажу вам по записи, не знаю, запишут вас в один день или нет. "
            "Сегодня воскресенье. Можете завтра с утра позвонить, уточнить. "
            "Хорошо, тогда завтра наберу."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "no_actual_reception_contact")

    def test_18530_dispatchers_weekdays_callback_not_narrow_to(self):
        """18530: диспетчеры только в будни; перезвонить в понедельник — слота нет, НЕ_ТО."""
        t = (
            "Официальный дилер бренда Чери Тенет, слушаю. День добрый. "
            "Хотел бы к вам на ТО записаться. "
            "Диспетчера работают только в будний день. "
            "В понедельник перезвоните, они вам запишут. "
            "Всё, понял. Спасибо."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "no_actual_reception_contact")

    def test_call_13675_admin_third_to_callback_not_narrow_to(self):
        """13675: админ, «третье ТО», диспетчер сейчас занят — только номер и перезвон, узкий НЕ_ТО."""
        t = (
            "Официальный дилер Chrн, администратор Диана. Добрый день! Здравствуйте. "
            "Я бы хотела записаться на третье ТО. У меня автомобиль Чери е Промаакс. "
            "Диспетчер сйчасз. Давайте контактный номер телефона запишу, передам, перезвонят вам. "
            "Хорошо. Лилия меня зовут. Передам, перезвонят вам. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "no_actual_reception_contact")

    def test_17418_admin_weekend_application_callback_not_narrow(self):
        """17418: админ, сервис не работает, заявка — перезвонят в пн и запишут; нет приёмки → НЕ_ТО."""
        t = (
            "Администратор салона. Ана, добрый день. Здравствуйте. "
            "Девушка, на техническое обслуживание записаться? "
            "Записаться можете только в понедельник, сегодня-завтра они не работают, "
            "либо можете оставить контактный номер. Заявочку я оставлю, они в понедельник "
            "вам перезвонят и запишут на удобное время. "
            "Оставьте, да, заявку, пусть перезвонит. "
            "С секундочку, я запишу ваш контактный номер. "
            "Вас зовут? Александр. Записали, да? Да, на ТО, да? Да. "
            "Как придут, пускай звонят. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "no_actual_reception_contact")

    def test_call_11263_no_service_dispatcher_not_narrow_to(self):
        """11263: админ салона, нет диспетчера сервиса, только перезвон — НЕ_ТО (нет связи с приёмкой)."""
        t = (
            "Администратор салона Анастасия, здравствуйте. Анастасия, здравствуйте. "
            "Я бы хотела записаться на ТО в следующую субботу или в воскресенье. "
            "Как могу к вам обращаться? Ксения. "
            "Ксения, у нас сегодня нет диспетчера сервиса. "
            "Я могу записать ваш номер телефона, вам завтра перезвонят. "
            "Ну, а давайте я тогда сама позвоню завтра. Хорошо. Спасибо большое. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("OTHER", "OTHER"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_16512_marketing_sponsorship_charity_other_not_sto_not_narrow_to(self):
        """16512: исх. ОП-перезвон, тема маркетолог/спонсорство — Прочие, не СТО/НЕ_ТО."""
        t = (
            "Алло, добрый день. Здравствуйте. Меня зовут Илья, я вам звоню с оициального дилера Tenet Cherry. "
            "Пришёл прямой звонок по продажам, Да. "
            "Я просто до салона Викинги никак не могу дозвониться, мне нужен их маркетолог Юля. "
            "Меня зовут Елена, это благотворительный фонд Штурм. "
            "сейчас у нас проходят турниры по волейболу. "
            "И у нас есть такое предложение к вам, прорекламироваться у нас. "
            "вы нам предоставляете каждые выходные небольшие превенты для наших игроков. "
            "передам маркетологу ваш мобильный телефон, чтобы она с вами связалась."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("OTHER", "OTHER"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected not STO narrow layer, got {rub} ({reason})")
        self.assertEqual(reason, "not_sto_call_type")

    def test_16620_knock_symptom_diagnostics_admin_busy_line(self):
        """16620: стук при трогании, линия диспетчера занята — диагностика, не запись, НЕ_ТО."""
        t = (
            "Администратор салона Анастасия, здравствуйте. "
            "я хотела записаться к вам на приём, потому что когда садишься, трогаешься, "
            "в машине стук какой-то появляется, как будто чё-то отвалится. "
            "Дарья, сейчас переведу вам на диспетчерасервис. Оставайтесь на линии. "
            "Дарья, звонок вернулся назад. Сейчас линия диспетчера сервиса занята. "
            "Оставьте номер телефона, я передам, вам перезвонят."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "diagnostics")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "no_actual_reception_contact")

    def test_16634_slukh_stt_knock_symptom_diagnostics_booking(self):
        """16634: STT «слух в машине» при трогании — диагностика (стук), запись на слот, НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. слушаю вас. "
            "у меня слух в машине появился когда трогаюсь, как будто что-то отвалится. "
            "тигго 4 про, пробег почти 49. стук при трогании, спереди, на газ нажмёшь. "
            "ближайшая запись на 15 число, можно на 7:50. Давайте на 7:50. "
            "15 июля в 7:50, внесу в запись."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "diagnostics")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "primary_defect_diagnostic_not_narrow_to")

    def test_18588_conditional_to_nearest_slot_is_diagnostics_not_narrow_to(self):
        """18588: «если записаться, то на ближайшее» — союз; запись по звуку у колеса, НЕ_ТО."""
        t = self._transcript_from_log(18588)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        self.assertTrue(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "diagnostics",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "primary_defect_diagnostic_not_narrow_to")

    def test_18734_fluids_and_repair_specs_consultation_not_narrow_to(self):
        """18734: катализатор, антифриз и характеристики масла без визита — НЕ_ТО."""
        t = self._transcript_from_log(18734)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "service_specs_consultation",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_fluids_consultation_with_agreed_slot_stays_booking(self):
        """Обсуждение жидкостей не отменяет запись, если дата визита согласована."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Nissan X-Trail. "
            "Нужно проверить катализатор, заменить антифриз и масло. "
            "Какое масло и антифриз используются, сколько стоят работы? "
            "Давайте запишем на пятницу в 12:30. Да, подходит. Записали вас на пятницу."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertTrue(dims.get("is_booking"))
        self.assertNotEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "service_specs_consultation",
        )

    def test_16676_wheel_alignment_wandering_not_work_type_to(self):
        """16676: развал, хождение машины — прочие/диагностика, не ТО из «то да»/«то есть»."""
        t = (
            "компания Викинги, Юлия, диспетчер сервиса. телефон ваш передали. "
            "Вы автомобиль хотели записать? "
            "мне нужно произвести развал, хождения на машина. "
            "Nissan Terra восемнадцатого года. "
            "сказали то, что у вас более новое оборудование. то да, это то есть нам подходит. "
            "если на стенде развала есть эта машина, подъеду, произведу работу. "
            "стенд занят, перезвоните позже."
        )
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        self.assertIn(ct, ("STO_IN", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertNotEqual(dims.get("work_type"), "to")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "wheel_alignment")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_17932_wheel_alignment_vkhozhdenie_stt_to_1000_not_work_type_to(self):
        """17932: STT «развал вхождения» + «то 10:00» — прочие, не ложное ТО из времени."""
        t = (
            "Викинги Чери, диспетчер сервис. Юлия. Здравствуйте. "
            "Смотрите, вопрос, а развал вхождения, когда есть ближайшие? "
            "Ближайший развал вхождения. "
            "А по какой причине хотите развал вхождения делать? "
            "немножко его ведёт и посвистывает при повороте в одну сторону колесо. "
            "Напомните, какой автомобиль? GMC Acadia. "
            "ближайшее — это 21-е число на следующей неделе, вторник. "
            "если вторник смотрим, то 10:00 утра, можно попозже, примерно в 11:30. "
            "Давайте на вторник, на 10:00. "
            "Всё, записали вас. До встречи."
        )
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        self.assertEqual(ct, "STO_IN")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertNotEqual(dims.get("work_type"), "to")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "wheel_alignment")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_18962_wheel_alignment_is_other_work(self):
        t = (
            "Викинги Чери, диспетчер сервиса Андреева Юлия. Подскажите, сход-развал я могу сделать? "
            "Если к вам приехать, то когда и почём? Запись на 28-е. Машина какая? "
            "Чери 7 Л. 2500 будет стоить сход-развал. Есть время 11:00 или после обеда."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "wheel_alignment")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_21281_steering_pull_to_li_particle_is_not_regulatory_to(self):
        """21281: «то ли развал сделать» — частица, тема сход-развала/увода, значит НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса, здравствуйте. "
            "У меня вот я еду, у меня руль немножко криво стоит, "
            "то ли развал сделать, то ли что. "
            "Машину немножко уводит, если ровно руль ставлю, машину уводит в сторону."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "wheel_alignment")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_21457_wheel_alignment_with_false_to_170000_is_not_to(self):
        """21457: сход-развал с записью по времени; «то 170 000» в конце не должен делать ТО."""
        t = self._transcript_from_log(21457)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "wheel_alignment")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_22034_second_to_booking_not_wheel_alignment_false_positive(self):
        """22034: «прохождение второго ТО» + подтвержденный слот — СТО_ТО_вх, не wheel_alignment."""
        t = self._transcript_from_log(22034)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_22085_outbound_site_lead_with_confirmed_to_slot_is_sto_to_out(self):
        """22085: исх. лид + подтвержденный слот на ТО в звонке — СТО_ТО_исх."""
        t = self._transcript_from_log(22085)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_21529_exlantix_is_other_brand_not_chery_tenet(self):
        """21529: EXLantix(s) — Прочие, не Чери/Тенет даже при шумовом «чери» в речи."""
        t = self._transcript_from_log(21529)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")

    def test_9409_opening_proiti_to_second_to_booking_is_sto_to_in(self):
        """9409: в начале «пройти ТО», второе ТО, цена и слот — СТО_ТО_вх, не НЕ_ТО."""
        t = (
            "диспетчер сервиса слушаю. подскажете а пройти то 20000 два года второй год. "
            "чери тигго семь промакс. нулевое то сделали первое то сделали сейчас подошло второе то. "
            "на данном то меняются масло фильтр. по стоимости данное то выходит 20500 рублей. "
            "29 мая есть окно нет у меня не получится. "
            "давайте на второй на 17:00. мы с вами записались на 2 июня время 5 часов вечера."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(dims.get("work_type"), "to")

    def test_9467_mto_booking_second_to_is_sto_to_in(self):
        """9467: запись на очередное МТО (STT), второе ТО — СТО_ТО_вх; «клиент отменил» — не отмена визита."""
        t = (
            "слушаю вас. хотел проговорить насчет записи мто на очередное. "
            "чери 7 промакс. второе техническое обслуживание. "
            "на завтра окно на 8:00 клиент запись отменил. "
            "на 28 мая на 8:00. по цене 20500. диагностику можно делать."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertNotEqual(dims.get("evidence", {}).get("booking_intent"), "cancellation")

    def test_10918_zero_to0_price_inquiry_sto_to_in(self):
        """10918: «сколько стоит ТО0» на Tenet T7 — запрос цены нулевого ТО, СТО_ТО_вх."""
        t = (
            "Викинги черре, диспетчер сервиса. Юлия. Здравствуйте. "
            "А подскажите, пожалуйста, сколько у вас стоит ТО0 на Tenet Tenet T7? "
            "Защита двигателя дополнительная установлена? Нет, не было. "
            "14 700 по стоимости. Ага, хорошо, спасибо. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(dims.get("work_type"), "to")

    def test_26906_reg_to_booking_with_neutral_ruloevoe_phrase_stays_sto_to_in(self):
        """
        26906: в диалоге записи на ТО нейтральная фраза
        «тормозная часть, ходовая часть, рулевое управление — это всё механик осмотрит»
        не должна считаться жалобой «после ТО».
        """
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Юлия. "
            "Здравствуйте, можно на ТО записаться? Да, можно. "
            "Какие работы хотели бы сделать? Третье ТО, наверное. "
            "На третьем ТО меняются масло двигателя, фильтр масляный, фильтр воздушный, фильтр салонный, свечи зажигания. "
            "По стоимости выходит 28 850. Дату какую рассматриваете? После восемнадцатого. "
            "Могу предложить 21 сентября на 9:30. Давайте понедельник, 9:30. "
            "Вас записали. Какие-то нарекания есть? "
            "Да нет, замечаний нет. Тормозная часть, ходовая часть, рулевое управление — это всё механик осмотрит на ТО."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_26966_outbound_to_booking_with_23e_date_and_time_is_sto_to_out(self):
        """
        26966: исходящий сервисный обзвон по заявке, слот согласован в форме
        «на 23-е число ... на 11:30» — это СТО_ТО_исх, а не НЕ_ТО.
        """
        t = (
            "Дилерский центр Викинги, Юлия, диспетчер сервиса. "
            "Получили заявку по вашему автомобилю Тенет 7. "
            "Вы хотели бы на техническое обслуживание записать автомобиль? Да, очередное. "
            "На 23-е число планируете на 11:30, это время удобно? Да, удобно. "
            "По ТО меняем масло двигателя, масляный фильтр, фильтр салонный и воздушный. "
            "По стоимости 22 550 рублей. Мы накануне вам напомним."
        )
        dept, ct = "STO", "STO_OUT"
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_outbound_to_booking_with_ordinal_day_word_is_sto_to_out(self):
        """
        Проверка обобщённого дня слота словами: «первого числа в 09:30».
        """
        t = (
            "Викинги, диспетчер сервиса. Вы хотели записаться на техническое обслуживание. "
            "По стоимости ориентировочно 19 500 рублей, по составу работ масло и фильтры. "
            "Давайте запишем: первого числа в 09:30 вам удобно? Да, подходит."
        )
        dept, ct = "STO", "STO_OUT"
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_21643_pure_how_much_to_cost_is_not_narrow_to(self):
        """
        21643: «сколько стоит ТО» без слотов, поиска по владельцу, состава работ и записи — НЕ_ТО.
        """
        t = (
            "Викинги Чери, диспетчер сервиса, здравствуйте. "
            "Подскажите, пожалуйста, сколько стоит ТО? "
            "Ориентировочно от 14 до 19 тысяч, зависит от автомобиля. "
            "Понял, спасибо, до свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "pure_to_price_inquiry_not_narrow_to")

    def test_21673_to_price_and_guarantee_consultation_without_schedule_not_booking(self):
        """
        21673: обсуждают стоимость/состав ТО и гарантию, но день/время не назначены и не подтверждены.
        Запись на сервис в этом звонке не состоялась.
        """
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "Мне насчёт ТО поговорить. У меня восьмое ТО намечается. "
            "Я газовое оборудование поставил, и мне нужно понять, какие мои действия, чтобы не сняли с гарантии. "
            "Можно его проходить? Можно. "
            "Пробег 80 000, Tiggo 7 Pro, вариатор. "
            "Там очень большое ТО, порядка 70 000 по стоимости: масло, фильтры, тормозная жидкость, масло в вариаторе. "
            "Хорошо, спасибо, подумаю."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("booking_intent"),
            "to_eligibility_consultation_without_schedule",
        )

    def test_27644_to_price_quote_declined_is_not_booking(self):
        """27644: после сметы ТО клиент говорит «мне не подходит» — записи нет."""
        t = (
            "Викинги Чери, ассистент сервиса, здравствуйте. "
            "Хотел записаться на ТО, какая будет стоимость? "
            "Tenet T4, пробег пятнадцать с копейками. "
            "Получается первое ТО, по стоимости со снятием защиты 21 500. "
            "Я понял, хорошо, мне не подходит, спасибо."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("booking_intent"),
            "price_quote_declined_no_booking",
        )

    def test_27773_oil_change_only_with_insurance_handoff_is_other_work(self):
        """27773: «только масло» + перевод в страхование не делает кузовной ремонт."""
        t = (
            "Диспетчер сервиса. Ранее были на ТО, хотите приехать? "
            "Можно по минимуму, просто масло поменять, без дополнительных работ. "
            "Если только масло менять, в районе 7000. "
            "Давайте запишем на субботу, на 11:30. "
            "И еще со страховой свяжите, пожалуйста."
        )
        dept, ct = classify_auto(t)
        self.assertIn((dept, ct), (("STO", "STO_IN"), ("STO", "STO_OUT")))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertNotEqual((dims.get("evidence") or {}).get("work_intent"), "insurance_claim_inspection")

    def test_27738_first_to_timing_question_is_to_without_booking(self):
        """27738: «первое ТО во сколько делать» = вид работ ТО, но без записи."""
        t = self._transcript_from_log(27738)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "to_consultation_without_booking",
        )

    def test_27654_chre_seromak_maps_to_chery_tiggo_7_max_brand(self):
        """27654: «Чре Серомак» должно нормализоваться в Chery/Tenet (Tiggo 7 Pro Max)."""
        t = (
            "Викинги Чери, диспетчер сервиса. "
            "Хотел узнать стоимость лобового стекла на Чре Серомак."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_insurance_without_body_damage_is_not_body_shop(self):
        """Страхование без кузовных повреждений не означает кузовной ремонт."""
        t = (
            "Запишите на ТО, нужно заменить масло и фильтры. "
            "И еще со страховой соедините по полису, пожалуйста."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertNotEqual(dims.get("work_type"), "body_shop")

    def test_9506_zero_to_price_inquiry_is_sto_to_in(self):
        """9506: стоимость нулевого ТО (STT нулевоето), состав регламента — СТО_ТО_вх, вид работ ТО."""
        t = (
            "диспетчер сервиса слушаю вас. здравствуйте. "
            "хотел бы узнать стоимость нулевого узнать стоимость нулевоето tenet t7. "
            "нулевоето меняется масло в двигателе фильтр масляный общий осмотр автомобиля "
            "ходовая часть осматривается тормозная система рулевое управление. "
            "по стоимости выходит 14650 рублей. просто за цену узнать спасибо."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(dims.get("work_type"), "to")

    def test_18289_nulevoy_telo_stt_zero_to_price_sto_to_in(self):
        """18289: «Нулевой» / STT «тело»≈ТО + цена 14 300 — СТО_ТО_вх."""
        t = (
            "Викинги Чери, диспетчер сервиса Гринкина Юлия. Здравствуйте. "
            "э, хотел узнать насчёт сервиса. Какой вопрос у вас? спрашивайте? "
            "Нулевой. нулевой это самое тело. Угу. С4Л. "
            "По стоимости вас сориентировать? Да, по стоимости оориентировать. "
            "Защита двигателя. — Защита двигателя дополнительна установлена? — Да. — Угу. "
            "— Нулевой это обувит 14 300 ₽ по стоимости. — По. Ясненько. "
            "— Ну ладно, спасибо, по подумать. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_18298_first_to_price_with_warranty_stt_priority_sto_to_in(self):
        """18298: первое ТО / ТО один + расчёт; STT «по гарантии» не отменяет СТО_ТО_вх."""
        t = (
            "Добрый день, официальный дилер Тольятти. Викинги с каким отделом вас соединить? "
            "я хочу уз расчёт получить по гаранти, ой, по гарантии техобслуживанию Первое ТО сделать. "
            "Сейчас, Давайте я вас с сервисом соединю. Пикинче, диспетчер сервиса, Юлия. Здравствуйте. "
            "Мне хотелось бы узнать расчёт, ТО один сделать на. машину свою черетиг четвёрка. "
            "Тодин вы имеете в виду 10 000 пробег? Ну, у меня год получается, так. "
            "10 000 я ещё не накатал. Угу. Ну да, получается, первый. "
            "какая модель у вас и объём двигателя? ЧереtigNeв. Нeю. Механика. Полтора литра. "
            "По стоимости 18 400 ₽. Если есть дополнительная защита двигателя металлическая, то 19 100. "
            "Защита есть. получается 19 100 ₽. По времени около трёх часов. "
            "По графику определюсь. Если чё, я в приложении тогда заполню там. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertNotEqual(reason, "inbound_warranty_complaint_booking_not_narrow_to")

    def test_25686_to_booking_in_warranty_period_stays_sto_to_in(self):
        """25686: запись на ТО в гарантийный период с доп. претензиями — приоритет у ТО."""
        t = (
            "Чери Викинги, ассистент сервиса. "
            "Можно записаться всё-таки на третье ТО? "
            "Когда истекает гарантия? Если до 21-го выявите брак, по гарантии успеете? "
            "Нам нужно сделать техническое обслуживание, чтобы запись была до 20 сентября. "
            "На субботу можно записаться? На 12-е число. "
            "Есть на 9:30, давайте на 9:30. "
            "Пробег 26 726. По стоимости ТО 27 700, меняется масло и фильтры."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertTrue(dims.get("is_booking"))
        self.assertIn(dims.get("work_type"), ("to", "warranty"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertNotEqual(reason, "inbound_warranty_complaint_booking_not_narrow_to")

    def test_17071_zero_to_gift_certificate_booking_sto_to_in(self):
        """17071: запись на нулевое ТО + подарок/сертификат + 15 300 — СТО_ТО_вх, вид работ ТО."""
        t = self._transcript_from_log(17071)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "to_price_quote")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_17212_nissan_to105_mileage_code_booking_sto_to_in(self):
        """17212: Nissan «ТО 105» / «регламент ТО-105» + запись 15 июля — СТО_ТО_вх."""
        t = self._transcript_from_log(17212)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_mileage_to_interval_code_step5_recognition(self):
        """Коды ТО по пробегу: 0, 5, 10, … 135 (шаг 5 тыс. км); не цена «то 23 200»."""
        from call_analytics.sto_booking_dimensions import (
            _mileage_to_interval_code_km_valid,
            _mileage_to_interval_code_present,
            _normalize_text,
            contains_any_to_marker_hit,
            infer_sto_booking_dimensions,
        )

        self.assertTrue(_mileage_to_interval_code_km_valid(0))
        self.assertTrue(_mileage_to_interval_code_km_valid(5))
        self.assertTrue(_mileage_to_interval_code_km_valid(105))
        self.assertTrue(_mileage_to_interval_code_km_valid(135))
        self.assertFalse(_mileage_to_interval_code_km_valid(7))
        self.assertFalse(_mileage_to_interval_code_km_valid(23))
        self.assertTrue(_mileage_to_interval_code_present("на ниссан то 60 можно сделать"))
        self.assertTrue(_mileage_to_interval_code_present("регламент то-75 замена масла"))
        self.assertFalse(_mileage_to_interval_code_present("если нет то 23 200"))
        self.assertFalse(
            _mileage_to_interval_code_present(
                "было то 135 в прошлом году замена масла механическое кпп"
            )
        )
        t = (
            "Викинги Чери диспетчер сервиса. Здравствуйте. "
            "на ниссан то 90 можно записаться? да. запишите на среду в 10:30. "
            "регламент то-90 масло фильтры."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(contains_any_to_marker_hit(_normalize_text(t))[0])

    def test_17539_past_to_followup_not_to_other_work(self):
        """17539: исходящий «были недавно на ТО» + уточнение обращения — НЕ_ТО, Прочие."""
        t = self._transcript_from_log(17539)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "past_to_visit_followup",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "past_to_followup_without_new_to_booking")

    def test_17540_outbound_phone_handoff_seventh_to_is_sto_to_out(self):
        """17540: исходящий «мне телефон ваш передали» + Черетига 8, 7-е ТО, слот — СТО_ТО_исх."""
        t = self._transcript_from_log(17540)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_17562_po_sdelat_stt_is_sto_to_out(self):
        """17562: STT «на ПО сделать» = на ТО сделать + масло/слот — СТО_ТО_исх, не oil_change."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("на то сделать", _normalize_text("масло поменять, На ПО сделать"))
        t = self._transcript_from_log(17562)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_17962_outbound_zapisatsya_khotel_to120_is_sto_to_out(self):
        """17962: исх. «телефон передали» + «записаться хотел на ТО» + ТО-120 — СТО_ТО_исх."""
        t = self._transcript_from_log(17962)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        self.assertNotEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "past_to_visit_followup",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_17495_tehobsluzhivaniyu_case_is_sto_to_out(self):
        """17495: «по техобслуживанию» (дательный) + смета/слот — СТО_ТО_исх, не НЕ_ТО из «масло»."""
        from call_analytics.sto_booking_dimensions import explicit_to_marker_present

        self.assertTrue(explicit_to_marker_present("по техобслуживанию", "техобслуживание"))
        t = self._transcript_from_log(17495)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_17482_existing_booking_wrong_to_change_not_narrow(self):
        """17482: был записан; ошибочно ТО-60 вместо 70; можно поменять — НЕ_ТО (перенос/коррекция записи)."""
        t = self._transcript_from_log(17482)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "existing_visit_clarification",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "existing_to_reschedule_not_narrow_to",
                "existing_visit_clarification_not_narrow_to",
            ),
            msg=reason,
        )

    def test_17340_nissan_juke_mixed_script_stt_brand(self):
        """17340: «Nисsaн Жук» — nissan по STT-матрице N/н + и/i + s/с/c + a/а + n/н."""
        t = self._transcript_from_log(17340)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        ev = dims.get("evidence") or {}
        self.assertIn(ev.get("brand_method"), ("exact", "fuzzy"))
        self.assertIn("nissan", (ev.get("brand_hit") or "").lower())

    def test_18548_nissan_mixed_script_case_ending_brand(self):
        """18548: «ремонтами Нисsана» — смешанная форма с окончанием, марка Nissan."""
        t = self._transcript_from_log(18548)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertEqual(dims.get("work_type"), "diagnostics")

    def test_nissan_mixed_script_case_endings_normalize(self):
        """Падежные формы Нисsан с латинской s нормализуются в Nissan."""
        for form in ("Нисsана", "Нисsану", "Нисsане", "Нисsаном"):
            with self.subTest(form=form):
                dims = infer_sto_booking_dimensions(f"занимаетесь ремонтом {form}?")
                self.assertEqual(dims.get("service_brand"), "nissan")

    def test_17882_nessan_stt_nissan_brand(self):
        """17882: STT «Нессan» / «на Нессан» — nissan, не other_brand из приветствия Чери."""
        for t in (
            "консультация по установке ГБО на Нессan",
            "сняли с Нессана официальное дилерство",
            "на Нессан то есть он у вас приобретался",
        ):
            with self.subTest(t=t[:45]):
                dims = infer_sto_booking_dimensions(t)
                self.assertEqual(
                    dims.get("service_brand"),
                    "nissan",
                    msg=f"expected nissan, got {dims.get('service_brand')} for {t[:50]!r}",
                )
        t17882 = self._transcript_from_log(17882)
        dims = infer_sto_booking_dimensions(t17882)
        self.assertEqual(dims.get("service_brand"), "nissan")
        ev = dims.get("evidence") or {}
        self.assertIn("nissan", (ev.get("brand_hit") or "").lower())

    def test_19540_nessan_and_xtrl_each_identify_nissan_brand(self):
        """19540: латинские STT-формы Nessan и Xtrl каждая самостоятельно означают Nissan."""
        for phrase in (
            "Мы с вами общались по поводу Nessan.",
            "Мы с вами общались по поводу Xtrl.",
            "Мы с вами общались по поводу Nessan и Xtrl.",
        ):
            with self.subTest(phrase=phrase):
                dims = infer_sto_booking_dimensions(phrase)
                self.assertEqual(
                    dims.get("service_brand"),
                    "nissan",
                    msg=f"expected nissan for {phrase!r}, got {dims.get('service_brand')}",
                )

    def test_28257_neissan_maps_to_nissan_brand(self):
        """28257: «Neissan Terrana» должно определяться как nissan, не other_brand."""
        t = self._transcript_from_log(28257)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertIn((dims.get("evidence") or {}).get("brand_hit"), ("nissan", "neissan", "neisan"))

    def test_28270_issan_potfaintr_maps_to_nissan_pathfinder(self):
        """28270: «исsан» и «Potфайнtr» нормализуются по отдельности → nissan Pathfinder."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("nissan", _normalize_text("исsан"))
        self.assertIn("pathfinder", _normalize_text("Potфайнtr"))
        self.assertIn("nissan", _normalize_text("исsан Potфайнtr"))
        self.assertIn("pathfinder", _normalize_text("исsан Potфайнtr"))
        t = self._transcript_from_log(28270)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertIn(
            (dims.get("evidence") or {}).get("brand_hit"),
            ("nissan", "pathfinder", "патфайндер"),
        )

    def test_28647_owner_of_qashqai_maps_to_nissan_brand(self):
        """28647: «Владелец автомобиля Кашкай» должно определяться как nissan."""
        t = self._transcript_from_log(28647)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertIn(
            (dims.get("evidence") or {}).get("brand_hit"),
            ("кашкай", "кашка", "qashqai", "nissan"),
        )

    def test_28418_ch_4ro_maps_to_chery_4_pro_brand(self):
        """28418: «Ч 4ро» должно определяться как Chery/Tenet (Tiggo 4 Pro)."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        for form in ("Ч 4ро", "Ч4ро", "ч 4 ро"):
            with self.subTest(form=form):
                self.assertIn("chery tiggo 4 pro", _normalize_text(form))
        t = self._transcript_from_log(28418)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_28386_celtg_7bmaak_maps_to_chery_7_pro_max(self):
        """28386: «CеlTg» и «7Бмаaк» нормализуются по отдельности → Chery Tiggo 7 Pro Max."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("chery tiggo", _normalize_text("CеlTg"))
        n7 = _normalize_text("7Бмаaк")
        self.assertTrue(("pro max" in n7) or ("промакс" in n7), msg=n7)
        pair = _normalize_text("CеlTg 7Бмаaк")
        self.assertIn("chery tiggo", pair)
        self.assertTrue(("pro max" in pair) or ("промакс" in pair), msg=pair)
        t = self._transcript_from_log(28386)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_17560_cheg_stt_chery_tiggo_brand(self):
        """17560: «ПТО Чег 4 Ню» — Chery Tiggo 4 New → chery_tenet («Чег» = Chery Tiggo)."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("chery tiggo", _normalize_text("по пто чег 4 ню"))
        self.assertIn("chery tiggo", _normalize_text("чег 7"))
        t = self._transcript_from_log(17560)
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_17574_crm_chery_tig_vehicle_brand(self):
        """17574: CRM «по автомобилю чери Тиг» + госномер — chery_tenet (достаточно «чери»)."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("тигго", _normalize_text("чери тиг а-а 719 гос номер"))
        t = self._transcript_from_log(17574)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        method = (dims.get("evidence") or {}).get("brand_method") or ""
        self.assertNotIn("suppress_greeting", method)

    def test_26668_auto_ch7_maps_to_chery_tiggo7_brand(self):
        """26668: «автомобиль Ч7, госномер ...» = Chery/Tenet (Tiggo 7), не other_brand."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        normalized = _normalize_text("автомобиль ч7, госномер 503")
        self.assertIn("chery tiggo 7", normalized)
        t = self._transcript_from_log(26668)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        ev = dims.get("evidence") or {}
        self.assertIn((ev.get("brand_hit") or "").lower(), ("чери", "chery", "тигго 7", "chery tiggo 7"))

    def test_22563_ttig8_stt_is_chery_tiggo_8_brand(self):
        """22563: «по автомобилю ТТиг-8» должно нормализоваться в Chery/Tiggo."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("chery tiggo 8", _normalize_text("по автомобилю ТТиг-8"))
        t = self._transcript_from_log(22563)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        ev = dims.get("evidence") or {}
        self.assertIn("chery", (ev.get("brand_hit") or "").lower())

    def test_22832_vosmerka_gibrid_is_chery_tenet_brand(self):
        """22832: «восьмерка гибрид» должно определяться как Chery/Tenet (Tiggo 8)."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("chery tiggo 8", _normalize_text("восьмерка гибрид"))
        self.assertIn("chery tiggo 8", _normalize_text("гибрид восьмерку"))
        self.assertIn("chery tiggo 8", _normalize_text("Чери гибрид восьмёрку"))
        t = self._transcript_from_log(22832)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        ev = dims.get("evidence") or {}
        self.assertIn("chery", (ev.get("brand_hit") or "").lower())

    def test_22833_chirri_vosmerka_is_chery_tenet_brand(self):
        """22833: «Чирри восьмерка» должно определяться как Chery/Tenet (Tiggo 8)."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("chery tiggo 8", _normalize_text("Чирри восьмёрка"))
        t = self._transcript_from_log(22833)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        ev = dims.get("evidence") or {}
        self.assertIn("chery", (ev.get("brand_hit") or "").lower())

    def test_22841_chechereg8_diagnostics_and_chery_tenet_brand(self):
        """22841: «Чечерег 8еь/Четыре г восемь» + запись на диагностику -> Chery/Tenet + diagnostics."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("chery tiggo 8", _normalize_text("Чечерег 8еь"))
        self.assertIn("chery tiggo 8", _normalize_text("Четыре г восемь"))
        t = self._transcript_from_log(22841)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "diagnostics")
        ev = dims.get("evidence") or {}
        self.assertIn("chery", (ev.get("brand_hit") or "").lower())
        self.assertEqual(ev.get("work_intent"), "diagnostics")
        rub, _reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub}")

    def test_23311_chr_te7pmak_is_chery_tenet_brand(self):
        """23311: «Chr Te7PMак» должно определяться как Chery/Tenet."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        normalized = _normalize_text("Chr Te7PMак")
        self.assertIn("chery tiggo 7", normalized)
        self.assertTrue(("pro max" in normalized) or ("промакс" in normalized))
        t = self._transcript_from_log(23311)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_23465_chertg4new_is_chery_tenet_brand(self):
        """23465: «автомобиль у вас CherTg 4 New» должно определяться как Chery/Tenet."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("chery tiggo 4", _normalize_text("автомобиль у вас CherTg 4 New"))
        t = self._transcript_from_log(23465)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_25950_chiroks_iltromaks_is_chery_tiggo_7_pro_max(self):
        """25950: «Чирокс? ильтромакс» → Chery Tiggo 7 Pro Max / Chery-Tenet."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        normalized = _normalize_text("Чирокс? ильтромакс")
        self.assertIn("chery tiggo 7", normalized)
        self.assertTrue(("pro max" in normalized) or ("промакс" in normalized))
        t = self._transcript_from_log(25950)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_28611_chiregs_inromax_is_chery_tenet_brand(self):
        """28611: «Чирегс Инромакс» — Chery/Tiggo + Pro Max, марка chery_tenet."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        normalized = _normalize_text("Чирегс Инромакс")
        self.assertIn("chery tiggo", normalized)
        self.assertTrue(("pro max" in normalized) or ("промакс" in normalized), msg=normalized)
        t = self._transcript_from_log(28611)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_28763_8pro_phrase_maps_to_chery_tenet_brand(self):
        """28763: «8Pro» в контексте ТО должно давать марку Chery/Tenet."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        normalized = _normalize_text("Так, 8Pro. Шестое ТО.")
        self.assertIn("chery tiggo 8 pro", normalized)
        t = self._transcript_from_log(28763)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_28872_chirri_g8_promaks_maps_to_chery_tiggo8_pro_max(self):
        """28872: «Chirri Г8 ПроMакс» -> Chery Tiggo 8 Pro Max / Chery-Tenet."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        normalized = _normalize_text("на автомобиле Chirri Г8 ПроMакс")
        self.assertIn("chery tiggo 8", normalized)
        self.assertTrue(("pro max" in normalized) or ("промакс" in normalized), msg=normalized)
        t = self._transcript_from_log(28872)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_28889_battery_after_recent_to_is_diagnostics_not_to(self):
        """28889: аккумулятор/чек после недавнего ТО -> диагностика, не ТО."""
        t = self._transcript_from_log(28889)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "diagnostics")
        ev = dims.get("evidence") or {}
        self.assertIn("diagnostics", (ev.get("work_hit") or "").lower())

    def test_26027_cherechga_is_chery_tiggo_brand(self):
        """26027: «ЧереЧга» → Chery Tiggo; «чере» (не «через») → Chery."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("chery tiggo", _normalize_text("ЧереЧга"))
        self.assertIn("chery", _normalize_text("чере"))
        self.assertIn("через", _normalize_text("через месяц"))
        self.assertNotIn("chery", _normalize_text("через месяц"))
        t = self._transcript_from_log(26027)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_11237_chery_chiga_7l_is_chery_tenet_brand(self):
        """11237: «Чери чига 7л» должно определяться как Chery/Tenet (Tiggo 7 L)."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        normalized = _normalize_text("Чери чига 7л")
        self.assertIn("chery tiggo 7 l", normalized)
        t = self._transcript_from_log(11237)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_27787_ere_tg8_maps_to_chery_tiggo8_brand(self):
        """27787: «по ере Тг 8» должно давать марку Chery/Tenet (Tiggo 8)."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("chery tiggo 8", _normalize_text("сегодня на 11 записывался по ере Тг 8"))
        t = self._transcript_from_log(27787)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_27884_chertig_7l_maps_to_chery_tiggo7l_brand(self):
        """27884: «автомобиль Chertig 7L» должно давать марку Chery/Tenet."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("chery tiggo 7 l", _normalize_text("автомобиль chertig 7l"))
        t = self._transcript_from_log(27884)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_28241_salon_chery_not_vehicle_brand_jolion_is_other_brand(self):
        """28241: «салон Чери» не маркер авто; «на Джолиун/Jolion» => Прочие (Haval)."""
        t = self._transcript_from_log(28241)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        evidence = dims.get("evidence") or {}
        self.assertIn((evidence.get("brand_hit") or "").lower(), ("джолиун", "джолион", "jolion", "haval", "хавал"))

    def test_22933_to_price_consultation_not_document_or_parts_followup(self):
        """22933: консультация по цене/составу ТО не должна попадать в doc/parts follow-up."""
        t = self._transcript_from_log(22933)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "insufficient_sto_to_markers_not_narrow_to")

    def test_22652_to4_booking_owner_and_slot_is_sto_to_in(self):
        """22652: 4-е ТО + фамилия собственника + выбор времени/запись -> СТО_ТО_вх."""
        t = self._transcript_from_log(22652)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_17577_sto_to_out_aligns_work_type_to(self):
        """17577: СТО_ТО_исх (ТО-1 + запись) → вид работ ТО, даже при сайд-теме гарантии/подсветки."""
        from call_analytics.sto_booking_dimensions import align_sto_work_type_with_narrow_rubric

        t = self._transcript_from_log(17577)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")
        wt, ev = align_sto_work_type_with_narrow_rubric(
            dims.get("work_type"),
            rub,
            t,
            evidence=dims.get("evidence") if isinstance(dims.get("evidence"), dict) else {},
        )
        self.assertEqual(wt, "to")
        self.assertIn(ev.get("work_intent"), ("to_aligned_with_narrow_sto_to", "to_price_quote"))

    def test_nissan_stt_matrix_mixed_spellings(self):
        """Матрица Nissan: смешанные написания → nissan после нормализации."""
        from call_analytics.sto_booking_dimensions import (
            _normalize_text,
            _nissan_stt_matrix_hit,
            infer_sto_booking_dimensions,
        )

        variants = (
            "nисsaн жук",
            "nissan juke",
            "ниссане кашкай",
            "нисsan x-trail",
            "Nисsan жук",
        )
        for v in variants:
            with self.subTest(v=v):
                self.assertTrue(_nissan_stt_matrix_hit(v.lower())[0], msg=v)
                self.assertIn("nissan", _normalize_text(f"автомобиль {v}"))
        dims = infer_sto_booking_dimensions(
            "диспетчер сервиса. автомобиль nисsaн жук, запись на замену масла."
        )
        self.assertEqual(dims.get("service_brand"), "nissan")

    def test_17265_mfc_passport_to_delo_not_to_work_type(self):
        """17265: МФЦ / паспорт / «то дело №» — Прочие, не ТО из частицы «то»."""
        t = self._transcript_from_log(17265)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("OTHER", "OTHER"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_hit"), "civilian_documents")

    def test_17077_warranty_context_default_chery_tenet_brand(self):
        """17077: «на гарантии» без явной марки — chery_tenet."""
        t = self._transcript_from_log(17077)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(
            (dims.get("evidence") or {}).get("brand_method"),
            "warranty_context_chery_tenet_default",
        )

    def test_16923_inbound_to_price_quote_booking_sto_to_in(self):
        """16923: вх. «сколько будет стоить ТО» + смета + запись 6 августа — СТО_ТО_вх."""
        t = self._transcript_from_log(16923)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_16914_second_to_price_after_zero_first_sto_to_out(self):
        """16914: «надо ТО проходить», нулевое/первое прошла → вторая, цена — СТО_ТО_исх."""
        t = (
            "Алло, добрый день. Это компания Викинг, диспетчер сервиса Юля, "
            "передали вас телефона, звонили, слушаю. "
            "Да, Юля, мне надо ТО проходить, нулевой, первый я прошла, второй, да? Скорее всего. Сколько будет? "
            "Напомните ваш автомобиль. Чери Тига7. "
            "коробка передач какая у вас? Автомат? "
            "Мне только масло менять и там свечи, всё. И сколько будет стоить? "
            "вы проходили нулевое, потом первое, то есть сейчас получается вторая. "
            "у нас это то получается 18 200. "
            "там мне сказали за 15 делает. Позвоните к ним тогда."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "to_price_quote")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_9512_planned_diagnostics_required_work_intent(self):
        """9512: исх. — запланировать диагностику, потребуется диагностика, слот на 4-е."""
        t = (
            "диспетчер сервиса юлия. удобно говорить. "
            "необходимо запланировать диагностику снова. тэнет 8. "
            "потребуется диагностика свободный ресурс часа. "
            "на четвертое число в 11:30 тогда. накануне позвоним напомним."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_intent"), "diagnostics")
        self.assertEqual(ev.get("work_hit"), "diagnostics")

    def test_10441_existing_to_late_arrival_not_narrow_to(self):
        """10441: записывались на ТО на 2 ч, задерживаемся — опоздание, не СТО_ТО_вх."""
        t = (
            "чери викинги ассистент сервиса андреева слушаю "
            "записывались на то на 2 чс гимадинов рамиль "
            "задерживаемся пробка была минут 15 на южное шоссе поворачиваем "
            "спасибо что предупредили ожидаем подъезжайте"
        )
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_26334_car_already_left_for_to_status_followup_not_narrow_to(self):
        """26334: авто уже оставлено на ТО; звонок про статус/связь с мастером — НЕ_ТО."""
        t = self._transcript_from_log(26334)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "existing_to_in_service_status_not_narrow_to")

    def test_9741_today_slot_cannot_attend_reschedule_not_narrow_to(self):
        """9741: записался сегодня на 10:00, не получается приехать — перенос, не СТО_ТО_вх."""
        t = (
            "Викинги ЧелстиСервис Андреева Юлия, слуша вас. Здравствуйте. "
            "Записался сегодня к вас на 10:00. не получается приехать. "
            "на 10:00 записаны. Запись можем перенести только уже на июнь месяц, "
            "третье число, давайте перенесём. Переносим, да, запись? "
            "восьмое ТО подошло, и если пробег будет больше насколько это критично?"
        )
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_9985_tomorrow_booking_missed_call_cancel_not_narrow_to(self):
        """9985: перезвон — записывался на завтра 8:30, не получается; отмена — НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "Вы звонили, у меня пропущенный. "
            "записывался на завтра. Да затра на завтра. В 8:30, да-да-да. "
            "Не получается, да. "
            "Если хотите, тогда завтра отменим. "
            "когда будете записываться на ТО, вы тогда снова продиктуете."
        )
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_10880_existing_to_slot_confirmation_not_narrow_to(self):
        """10880: записывался на 30-е на ТО, «всё в силе» — подтверждение слота, не СТО_ТО_вх."""
        t = (
            "Викинги черре, диспетчер сервиса Юлия. Здравствуйте. Юлия, здравствуйте. "
            "Меня Денис зовут. Я записывался на тридцатого, на тридцатое число на ТО, на Первом дом 4. "
            "Хотел узнать, всё в силе. Минутку. На какое время записаны? "
            "Меня на 11:0 записывали. На 1100. Да, Денис, всё верно. Записаны. "
            "Я хотел спросить, а вот первое ТО входит кондиционер, обслуживание?"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "existing_visit_clarification",
        )
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_25969_existing_slot_price_clarification_is_not_narrow_to(self):
        """25969: «я вот записался на ... на ТО первое» + уточнение цены — НЕ_ТО."""
        with open("/home/vikingi/VikingiAll/logs/vikingi_autofetch.log", encoding="utf-8") as f:
            t = (
                f.read()
                .split("call_id=25969")[1]
                .split("--- Транскрипт ---")[1]
                .split("---")[0]
                .strip()
            )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "existing_to_slot_confirmation_not_narrow_to")

    def test_23473_existing_to_today_sms_missing_is_not_narrow(self):
        """23473: уточнение уже существующей записи на сегодня («смс не пришла») — НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервис. Юлия. Здравствуйте. "
            "Здравствуйте, Юлия. У нас сегодня запись на ТО. Я просто уточнить, эсэмэска не пришла. "
            "Вроде 11:00 было. "
            "А напомните, пожалуйста, фамилию владельца автомобиля? Колондарова Мария. "
            "Да, записана сегодня на 11:00. Да, сегодня на 11 записана, вы подъедете? "
            "Да-да-да-да. Ну всё, хорошо, мы вас ждём."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "existing_to_slot_confirmation_not_narrow_to")

    def test_10727_outbound_crm_to_lead_booking_sto_to_out(self):
        """10727: исх. по заявке «на ТО хотели записать», второе ТО — СТО_ТО_исх."""
        t = (
            "Алло, Елена Васильевна, Дилерский центр Викинги, Юлия, диспетчер сервиса. "
            "Удобно говорить? Да. Я звоню по заявке по вашей. "
            "Пришла заявка, вы на ТО хотели записать автомобиль? Да. Чери Тиг 4 Pro. "
            "У вам второй итоо к 20 тысячам должен пробег приближаться? Да. "
            "Желаемую дату указали 3 июня 12 часов. 13:30 было бы удобно? "
            "Третье июня, среда, к 12:00 будем ожидать. Накануне позвоним, напомним о визите."
        )
        ct = "STO_OUT"
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department="STO")
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")
        self.assertEqual(dims.get("work_type"), "to")

    def test_10915_outbound_holiday_slot_correction_not_narrow_to(self):
        """10915: исх. — сегодня записывали на ТО, корректировка времени из‑за праздника — НЕ_ТО."""
        t = (
            "Алло, Ксения Анатольевна, здравствуйте, это Дилерский центр Викинги, "
            "Юлия, диспетчер сервиса. Удобно говорить? Да, удобно. "
            "Да, мы сегодня с вами записывали автомобиль на 12 июня на техническое обслуживание. "
            "У нас небольшая корректировка произошла, так как праздничный день, оказывается. "
            "Смотрите, у нас предложение либо подъехать с раннего утра в 7:50, либо после 12:00. "
            "Да, можно после 12:00. В 13:00. "
            "Двенадцатого также июня оставляем, только время 13:00."
        )
        ct = "STO_OUT"
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_10870_body_shop_dent_booking_not_narrow_to(self):
        """10870: исх. кузовной, запись на осмотр вмятин; «смотреть-то 5 минут» — не СТО_ТО_исх."""
        t = (
            "Алло. Алексей Владимирович? Да. Добрый день, Викинги Кузовной. "
            "Сергей, мастер-приёмщик вас беспокоит? Добрый. "
            "Направление по ремонту Тиг7 Промакс получили 310, госномер. "
            "Оформлял Чери приложение на осмотр автомобиля. "
            "У вас задняя правая дверь, да? Да, у меня несколько вмятин. "
            "можно сейчас предварительно записаться. "
            "Там-то по большому счёту, там смотреть-то 5 минут. "
            "Возможен ли ремонт без покраски двери? ЛКП там есть однозначно. "
            "всё, я вас записываю тогда."
        )
        ct = "STO_OUT"
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "body_shop")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_10791_repair_status_engine_teardown_not_narrow_to(self):
        """10791: статус ремонта (разборка/дефектовка), подменный — не СТО_ТО_исх; «центр»/«что-то стоит» не ТО."""
        t = (
            "Сро. Алло. Андрей, добрый день. Сергей беспокоит лиский центр Чери. "
            "Добрый, добрый, да. Просили, чтобы перезвонил там. "
            "Да, я, ну, я хотел узнать, просто мне сказали, там решение по автомобилю завтра, "
            "ой, вчера скажут, а чтоё-то никто не позвонил ничего. "
            "Ну, вы стоите в планировке на разборку двигателя, дефектовку. "
            "Ага. И крайнее числа, крайнее числа стоит 11-е, но если сейчас раньше что-то "
            "Стоит одиннадцатое, но если сейчас раньше что-то освобождается на тарист, "
            "сразу забирают машину в ремонт. "
            "Я вот её сразу заглушил и эвакуатор вызвал. Ну, возможно, скорее всего, цепь. "
            "А подмена автомобиля. Всё, спасибо большое, до свидания."
        )
        ct = "STO_OUT"
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_13781_deferred_alarm_siren_after_to_visit_not_narrow_to(self):
        """13781: исх. CRM, прошлое ТО, дефект сигнализации, ожидаем сирену — STO_OUT, НЕ_ТО."""
        t = (
            "Алло. Татьяна Петровна, добрый день, Компания Викинги, Юлия, диспетчер. "
            "Удобно разговаривать вам? Да-да-да. "
            "Вы к нам заезжали на техническое обслуживание двенадцатого числа и заявляли то, что "
            "не работает сигнал штатной сигнализации. Вам одобрена замена, "
            "мы на конце этой недели ожидаем сирены. "
            "Можем с вами заранее записаться, 25-е на восемь. "
            "Когда запчасти поступят, мы вам позвоним накануне."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "deferred_parts_after_to_visit_not_narrow_to")

    def test_13787_promised_dispatcher_handoff_not_connected_not_narrow_to(self):
        """13787: обещали переключить на диспетчера по ТО, линия приёмки не ответила — НЕ_ТО."""
        t = (
            "Се, можно было В принципе, можно было бы согласовать. Приехали бы на ТО. маршрут забрали. "
            "Давайте я вас на диспетчера сфициального цеха переключу. Вы обсудите запись на ТО, "
            "и потом она на меня, чтобы перевела, и мы с вами уже обсудили, как. "
            "Сейчас. приду. Юль, есть клиент."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "no_actual_reception_contact")

    def test_10769_deferred_spark_plugs_after_to_not_narrow_to(self):
        """10769: ТО пройдено, свечи не было — поступили; исх. приглашение на замену, не СТО_ТО_*."""
        t = (
            "Викинги, Дилерский центр Юлия, диспетчер сервиса. Удобно? "
            "На техническом обслуживании приезжал автомобиль Тиг-4Pr, госномер 301. "
            "Не было на тот момент свечей зажигания, сейчас свечи поступили. "
            "Мы готовы вас пригласить. На завтра время есть? К двенадцати часам. "
            "Записываем? Да. Завтра в 12:00 ожидаем на замену свечей."
        )
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, "STO_OUT", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_22830_deferred_oil_after_completed_to_is_not_narrow_to(self):
        """22830: после ТО не было масла, масло поступило, приглашают на работу — НЕ_ТО."""
        t = self._transcript_from_log(22830)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "deferred_parts_after_to_visit_not_narrow_to")

    def test_22886_outbound_partial_cycle_time_callback_is_not_narrow_to(self):
        """22886: дозвон по освободившемуся времени/переносу существующего слота — НЕ_ТО."""
        t = self._transcript_from_log(22886)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "outbound_existing_to_reschedule_not_narrow_to")

    def test_22741_diagnostics_without_explicit_to_is_not_narrow_to(self):
        """22741: первичный запрос на диагностику без явного ТО — НЕ_ТО (diagnostics)."""
        t = self._transcript_from_log(22741)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "diagnostics_without_explicit_to_not_narrow_to")

    def test_22880_climate_diagnostics_booking_is_diagnostics(self):
        """22880: «записаться на диагностику климат-контроля» => diagnostics, не past_to_followup."""
        t = self._transcript_from_log(22880)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_intent"), "diagnostics")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_27618_ac_not_cooling_booking_with_30go_date_is_diagnostics_not_narrow_to(self):
        """27618: «кондиционер не холодит» + «то 30-го» (дата) не должно давать ложный ТО-код."""
        t = (
            "Официальный дилер, администратор. "
            "Записаться на кондиционер, чтобы посмотрели. "
            "Диспетчер сервиса: кондиционер перестал холодить, воздух то теплый, то холодный. "
            "Надо к диагностике, ближайшая запись 29-е число после обеда, либо если с утра хотите, то 30-го. "
            "Давайте запишемся на среду, на 10:00. "
            "Тридцатое сентября, в 10 часов, вас записали."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_intent"), "diagnostics")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "diagnostics_without_explicit_to_not_narrow_to")

    def test_10284_zapisyvatsya_na_diagnostiku_work_hit_diagnostics(self):
        """10284: STT «записываться на диагностику» — вид работ диагностика, не голые прочие."""
        t = (
            "ассистент сервиса андреева слушаю вас "
            "делаете диагностику у меня лада 16 лет глохнет на ходу "
            "нужно записываться на диагностику "
            "ближайшая запись на 5 июня от 3400 подключение прибора осмотр параметров"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_hit"), "diagnostics")
        self.assertEqual(ev.get("work_intent"), "diagnostics")

    def test_11299_crm_outbound_first_to_truncated_stt_sto_to_out(self):
        """11299: исх. CRM — имя клиента, «обращение получили», STT «Первое Т» → СТО_ТО_исх."""
        t = (
            "Д. — Алло. Анна Михайловна, добрый день, компания Викинги. Меня зовут Юлия. "
            "Удобно разговаривать вам? — Да-да-да-да. Обращение ваше получили по поводу записи на Первое Т. "
            "Вы хотите на 20 июня на субботу, верно? Да, можно записаться. "
            "Время самое раннее, любое можно выбрать с восьми часов. "
            "Там будет меняться масло, фильтр масляный, салонный, воздушный. "
            "По стоимости выходит 19 050. На 9 часов с вами выбираем? "
            "Хорошо, вас записали. Контрольный звонок накануне девятнадцатого числа ожидайте, "
            "мы с вами свяжемся, подтвердим вашу запись."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"), msg=f"expected STO_OUT, got {dept}/{ct}")
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_11383_second_to_booking_steering_side_work_sto_to_in(self):
        """11383: «второе ТО записаться» + стук рулевой + слот — СТО_ТО_вх, не НЕ_ТО."""
        t = (
            "викинги чери диспетчер сервиса юлия здравствуйте "
            "мжно второе то записаться угу давайте запишем "
            "чери тигго 4 нью пробег 19000 "
            "в рулевой стучит стук в рулевой колонке при повороте "
            "по самому то масло фильтры своё "
            "16 июня в 8:00 мы вас записали накануне перезвоним"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"), msg=f"expected STO_IN, got {dept}/{ct}")
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_11355_natogo_stt_inbound_book_to_sto_to_in(self):
        """11355: STT «мне бы натого записаться» = на ТО — СТО_ТО_вх."""
        t = (
            "викинги чери диспетчер сервиса юлия здравствуйте "
            "мне бы натого записаться ранее у нас автомобиль обслуживался "
            "чери тигго 8 промакс пробег 60 третье четвертое число если есть место "
            "пока не записываем"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"), msg=f"expected STO_IN, got {dept}/{ct}")
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_13702_tl_knto_stt_inbound_first_to_booking_sto_to_in(self):
        """13702: STT «на ТЛ» / «КНТО» + «какое ТО необходимо» — СТО_ТО_вх, вид работ ТО."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Андреева Юлия слушаю ва. "
            "Здравствуйте. Здравствуйте, девушка, на ТЛ можно записаться? "
            "КНТО можно записаться? Да, какой автомобиль у вас? Tenet Т-8. "
            "Какое ТО необходимо сделать? Первое. "
            "Ранее к нам заезжали, обслуживались у вас? Нет. "
            "первое ТО включает замену масла. 24 июня записали вас."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

        t_open = (
            "Чери Викинги на Заставной, ассистент сервиса Андреева Юлия слушаю ва. "
            "на ТЛ можно записаться? КНТО можно записаться?"
        )
        dims_o = infer_sto_booking_dimensions(t_open)
        rub_o, _ = infer_sto_to_rubric_type(t_open, "STO_IN", dims_o, department="STO")
        self.assertEqual(dims_o.get("work_type"), "to", msg="ТЛ/КНТО должны нормализоваться в ТО")
        self.assertEqual(rub_o, "STO_TO_IN", msg="запись на ТЛ/КНТО — узкая СТО_ТО_вх")

    def test_11354_kioshku_stt_inbound_book_to_sto_to_in(self):
        """11354: STT «на киошку» (тэошку/ТО) + «какое тог» — СТО_ТО_вх."""
        t = (
            "викинги чрисервис андреева юлию слушаю вас здравствуйте "
            "слушай на киошку можно на третье число записаться машинку "
            "какое тог необходимо сделать третья по-моему "
            "девять тридцать запишу комментарий на весь день оставляет всё записала"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"), msg=f"expected STO_IN, got {dept}/{ct}")
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_10258_crm_lead_tto_mto_sto_to_out(self):
        """10258: заявку получили + «ТтО провести» / «на МТО подъедет» — СТО_ТО_исх."""
        t = (
            "викинги юлия диспетчер удобно разговаривать "
            "мы заявку получили по автомобилю черетиг 4 пробы с номера 870 "
            "да необходимо тто провести лампа ближнего света датчики давления "
            "шиномонтаж вы делаете не у нас на мто подъедет механик посмотрит "
            "по записи третье июня 15:30 записали"
        )
        ct = "STO_OUT"
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department="STO")
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_10256_filter_booking_sedmogo_to_est_not_narrow_to(self):
        """10256: фильтры + слот; STT «до половины седьмого, то есть» — не СТО_ТО_исх."""
        t = (
            "юлия диспетчер renault sandero фильтра салонного и топливного нет в наличии "
            "записываться на следующей неделе четвёртое июня четверг "
            "механики работают до половины седьмого то есть в июне до половиседьмого "
            "четвёртое июня 7:50 записали фильтры"
        )
        ct = "STO_OUT"
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_10260_mirror_booking_chetverg_to_est_not_narrow_to(self):
        """10260: замена зеркала; STT «четвёртое июня, четверг. То есть» — не СТО_ТО_исх."""
        t = (
            "юлия диспетчер по поводу зеркала заднего вида замена "
            "третье июня среда четвёртое июня четверг то есть за 10 минут до начала "
            "12 часов дня записали"
        )
        ct = "STO_OUT"
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_10182_planned_to_work_oil_gearbox_work_type_to(self):
        """10182: «планировал работы там ТО» + масло в коробке — work_type to."""
        t = (
            "слушаю планировал запись на сервис nissan qashkai "
            "масло для коробки планировал работы там то охлаждающая "
            "масло в коробке частичная замена"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")

    def test_10155_to_proiti_oil_booking_is_sto_to_in(self):
        """10155: «ТО пройти» + масло/фильтры + слот — СТО_ТО_вх, не НЕ_ТО."""
        t = (
            "слушаю нам надо то пройти узнать день масло поменять "
            "заменить масло в двигателе фильтры второго июня 16:30 "
            "по стоимости 9200 записали на 2 июня"
        )
        ct = "STO_IN"
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department="STO")
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_10304_cancel_to2_chetvertoe_chislo_not_narrow_to(self):
        """10304: отмена записи на ТО-2 / 4-е число — НЕ_ТО."""
        t = (
            "слушаю записывался на четвертое число звонил на то-2 "
            "на солнечной туда записался снимите отменили запись"
        )
        ct = "STO_IN"
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_19445_cancel_monday_inspection_slot_not_narrow(self):
        """19445: отмена существующего слота; STT «вытеркните», перепишется потом — НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "В понедельник на техосмотр, в 12:30 записали меня. "
            "Вытеркните, пожалуйста, не ждите меня эту неделю. Я потом перепишусь. "
            "12:30, вижу вашу запись. Отменяем. Сейчас другой день подобрать? Нет. "
            "Хорошо, звоните тогда."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("booking_intent"), "cancellation")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "existing_to_cancellation_not_narrow_to")

    def test_22486_primary_diagnostic_with_incidental_to_slot_not_narrow_to(self):
        """22486: жалоба на панель/звук + диагностика; фраза про ТО от диспетчера не должна давать СТО_ТО_вх."""
        t = (
            "Официальный дилер Чери и Тенет Викинги на Заставной, администратор Диана. "
            "Подскажите, на панели разное время на мультимедиа и приборке, потом выравнивается. "
            "Переведу на диспетчера сервиса. "
            "Викинги Чери, диспетчер сервиса Юлия. "
            "Сейчас еще когда еду на мелких кочках, с правой стороны непонятный звук. "
            "Надо найти причину, а не просто сказать, что все нормально. "
            "Смотрите, может быть в ближайшее время планируется техническое обслуживание, заезд на сервис? "
            "Диагност нужен, подключить прибор и посмотреть настройки. "
            "Записали вас на следующее воскресенье, 23-е число, в 13:00."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(
            reason,
            "inbound_primary_diagnostic_with_incidental_to_slot_not_narrow_to",
        )

    def test_22536_oil_change_with_incidental_to_reference_not_narrow_to(self):
        """22536: замена масла + слот; шумный хвост с «техническое обслуживание» не должен давать СТО_ТО_вх."""
        t = (
            "Официальный дилер Чери и Тенет Викинги на Заставной, администратор Диана. "
            "Мне бы записаться на замену масла на семнадцатое или восемнадцатое. "
            "Нужно только масло в двигателе поменять с масляным фильтром. "
            "Фильтр салонный, воздушный не нужно, только масло и масляный фильтр. "
            "Девятнадцатого числа есть время в 11:30? Да, давайте на 19-е на 11:30. "
            "Накануне позвоним, напомним. "
            "Алло, Сергей, добрый день, по поводу автомобиля. "
            "Записались Чери голосового помощника на техническое обслуживание."
        )
        dept, ct = classify_auto(t)
        self.assertIn((dept, ct), (("STO", "STO_IN"), ("OTHER", "OTHER")))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(
            reason,
            "inbound_oil_change_with_incidental_to_reference_not_narrow_to",
        )

    def test_10302_svoj_nissan_not_dealer_chery_brand(self):
        """10302: «свой Ниссан обслуживаю» — nissan, не «дилерский центр Чери»."""
        t = (
            "викинги чери слушаю я у вас свой ниссан обслуживаю карточку "
            "половину автомобиля разобрать дилерский центр чери мастер сергей"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")

    def test_stt_oksid_exeed_brand(self):
        """STT «оксид» / «Oxid» — other_brand (Exeed)."""
        for model in ("оксид", "Oxid", "OXID"):
            dims = infer_sto_booking_dimensions(
                "ассистент сервиса. автомобиль " + model + " госномер 123"
            )
            self.assertEqual(
                dims.get("service_brand"),
                "other_brand",
                msg=f"failed for model={model!r}",
            )
            self.assertEqual((dims.get("evidence") or {}).get("brand_hit"), "exeed")

    def test_13954_na_nissane_oil_change_nissan_brand(self):
        """13954: «на Ниссане бы масло поменять» — nissan, не other_brand."""
        t = (
            "Администратор салона. Оксана, добрый день. Денис. "
            "На Ниссане бы масло поменять, когда есть? "
            "Переключу на диспетчера. "
            "ассистент сервиса Юлия, слушаю вас. "
            "На местали бы масло поменять, когда есть места? "
            "Давайте в 8:00 утра. Белозёров. Масло своё, фильтр своё."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertEqual(
            (dims.get("evidence") or {}).get("brand_zone"),
            "client_maintenance_request",
        )

    def test_22030_phone_dictation_noise_not_narrow_to(self):
        """22030: «... 697 157 ... шесть ... то ...» при диктовке номера — НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. "
            "По поводу машин из Германии, какие возите? "
            "Это не в тот отдел, подождите. "
            "Антон, добрый день, по немецким машинам подскажите. "
            "Сроки две-три недели. "
            "А телефон прямой скажешь? "
            "Шесть девять семь, сто пятьдесят семь. "
            "То пятьдесят семь. "
            "Спасибо."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_21985_oil_change_booking_without_regulatory_to_is_not_narrow_to(self):
        """21985: запись на замену масла в вариаторе без темы регламентного ТО — НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса. Юлия. Здравствуйте. "
            "Я вчера звонил, уточнял насчёт цены на замену масла в вариаторе Nissan X-Trail, "
            "и сегодня готов записаться. "
            "Запись на следующей неделе сейчас идёт. "
            "Мне надо записаться таким образом, чтобы к пяти машина была готова. "
            "Девятнадцатого числа могу вас пригласить на 10:00. "
            "Да, устраивает, договорились. "
            "Хорошо. 19 августа в 10:00 будем вас ожидать."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "oil_change_without_regulatory_to_not_narrow_to")

    def test_23993_cvt_service_specs_consultation_without_actual_slot_is_not_narrow_to(self):
        """23993: консультация по частичной/полной замене CVT и регламенту без оформления слота — НЕ_ТО."""
        with open("/home/vikingi/VikingiAll/logs/vikingi_autofetch.log") as f:
            t = (
                f.read()
                .split("call_id=23993")[1]
                .split("--- Транскрипт ---")[1]
                .split("---")[0]
                .strip()
            )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "service_specs_consultation")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "service_specs_consultation_not_narrow_to")

    def test_cvt_service_specs_without_oil_word_still_not_narrow_to(self):
        """Шире масла: консультация по CVT (фильтр/прокладка/частичная-полная) без слота — НЕ_ТО."""
        t = (
            "Викинги сервис. По вариатору подскажите: частичная или полная замена трансмиссионной жидкости, "
            "фильтр и прокладка обязательны? По регламенту это как лучше делать на 60 тысячах? "
            "Предварительная запись есть, я потом заранее позвоню."
        )
        dept, ct = classify_auto(t)
        self.assertIn((dept, ct), (("STO", "STO_IN"), ("OTHER", "OTHER")))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "service_specs_consultation")
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "service_specs_consultation_not_narrow_to")

    def test_13985_tenet7_not_past_nissan_almera_brand(self):
        """13985: Tenet семёрка — текущий авто; Nissan Almera «до этого» — не марка визита."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Андреева Юлия. Слушаю нас. "
            "Девушка, надо бы записаться на нулевое ТО. "
            "А-а, машина. Tenet, е, Tenet, сеем семёрка. Пробег около 2 000. "
            "Хотели на нулевое ТО записаться. "
            "К там ранее заезжали, обслуживались у нас? — нет, первый. "
            "Ну, а до этого у нас была Nissan Almera. Мы всё время у нас обслуживались. "
            "Попова Ольга Юрьевна. tenet t7 у нас получается первое нулевое то."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual((dims.get("evidence") or {}).get("brand_zone"), "early_client_vehicle")

    def test_10282_tenet_t7_to1_fulltext_brand(self):
        """10282: Tenet T7 в начале — chery_tenet даже после якоря «подскажите» в CRM."""
        t = (
            "юлия слушаю по поводу сервиса то-1 на tenet tenet t7 "
            "фамилия подскажите сафронова пробег 10355 3 июня 16:00"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_10330_ocherednoe_proyti_ego_obemnoe_tto_sto_to_in(self):
        """10330: «очередное пройти» / «пройти его» + объёмное ТтоО — СТО_ТО_вх."""
        t = (
            "администратор салона оксана добрый день хотел его пвоочередное пройти "
            "переключу на диспетчера чери викинги сервис андреева юлия слушаю вас "
            "хотел его очередное пройти ниссан кашкай пробег 96 "
            "было ттоо необъемное 88000 сейчас объемное ттоо меняется масло фильтры "
            "по стоимости 14000 28 мая 11 часов записали"
        )
        ct = "STO_IN"
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department="STO")
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_19102_current_kia_over_past_nissan_service_history(self):
        t = (
            "Чери Викинги, ассистент сервиса Андреева Юлия. "
            "Можно на КIA поменять масло в коробке и двигателе у вас? "
            "Ранее обслуживались? Я на Nissan был, когда обслуживался, "
            "а вот сейчас пересел на Киa. Для подбора запчастей потребуется VIN."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("brand_hit"), "kia")
        self.assertEqual(ev.get("brand_method"), "current_kia_over_past_nissan_history")

    def test_10449_riza_8_arrizo_chery_tenet_brand(self):
        """10449: STT «Риза 8» / «телек Риза 8» — Chery Arrizo 8, не other_brand."""
        t = (
            "чери викинги ассистент сервиса слушаю "
            "подскажите телек риза 8 то четвертое сколько будет стоить "
            "риза 8 четвертое то 22500 запись на июнь"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_10438_che_4_nyug_new_chery_tenet_brand(self):
        """10438: STT «че 4 Nюg N» — Chery Tiggo 4 New, не other_brand."""
        t = (
            "викинги чери сервис андреева слушаю записаться на то номер один "
            "ибрагимов айрат ахатович телефон 3033 "
            "ага че 4 nюg nноo 667 25 года выпуска на роботе автомобиле "
            "первое то замена масла 19050 1 июня 12:00 записали"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        ev = dims.get("evidence") or {}
        self.assertIn(ev.get("brand_hit") or "", ("chery", "чери", "тигго", "tiggo", "4"))

    def test_11706_g4nu_chery_tiggo_4_new_brand(self):
        """11706: STT «черезG4NU» / «4NU» — Chery Tiggo 4 New, не other_brand."""
        t = (
            "уточку автомобиль у вас черезG4NU госномер 49 "
            "четыре ю 25 года выпуска первое то замена масла"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_19105_tchere_ig4_proga_is_chery_tiggo_4_pro(self):
        from call_analytics.sto_booking_dimensions import _normalize_text

        for phrase in ("ТЧере ИГ4 прога", "Чере ИГ4 прога", "4 прога", "4 про"):
            with self.subTest(phrase=phrase):
                norm = _normalize_text(phrase)
                self.assertIn("chery", norm)
                dims = infer_sto_booking_dimensions(
                    f"ассистент сервиса, автомобиль {phrase}, щелчки руля и стук в подвеске"
                )
                self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertNotIn("chery", _normalize_text("приеду через месяц"))

    def test_12550_chretig_4_proby_chery_tiggo_4_pro_brand(self):
        """12550: STT «ЧреTIG 4 пробы» в CRM — Chery Tiggo 4 Pro, не other_brand."""
        t = (
            "заявочку получили по автомобилю чреtig 4 пробы с номеро 753 "
            "диагностикой занимается электрик запись на завтра 14:30"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_finit_stt_infiniti_nissan_brand(self):
        """STT «Finit» — Infiniti, группа Nissan, не other_brand."""
        t = (
            "Викинги Чери диспетчер сервиса Юлия. Здравствуйте. "
            "У меня Finit FX35. Нужно записаться на диагностику подвески."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")

    def test_28224_infiniti_mixed_script_maps_to_nissan_brand(self):
        """28224: «Инфiniтиy» должно определяться как Infiniti (группа Nissan)."""
        t = self._transcript_from_log(28224)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertIn((dims.get("evidence") or {}).get("brand_hit"), ("infiniti", "infinit", "infinity", "инфинити", "инфинит"))

    def test_28989_infiniti_mixed_script_infiniti_is_nissan_brand(self):
        """28989: «Инfiнити» = Infiniti = группа Nissan, не Chery из приветствия."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("infiniti", _normalize_text("Да, Инfiнити автомобиль у вас"))
        t = self._transcript_from_log(28989)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertIn(
            (dims.get("evidence") or {}).get("brand_hit"),
            ("infiniti", "infinit", "infinity", "инфинити", "инфинит"),
        )

    def test_10648_nesan_patroll_nissan_brand(self):
        """10648: STT «Nesan Patroll» на ремонт — nissan, не other_brand."""
        t = (
            "Викинги Чери диспетчер сервиса Юлия. "
            "Мне бы хотелось записаться на ремонт Nesan Patroll. "
            "Замена передних дисков. Диски свои."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertEqual((dims.get("evidence") or {}).get("brand_hit"), "nissan")

    def test_16247_nissan_titan_mixed_script_stt_brand(self):
        """16247: STT «Nисsan Tиrn» при записи на ремонт — nissan (Titan)."""
        t = (
            "Викинги Чери, диспетчер сервиса. Юлия. Здравствуйте! "
            "там записывалась машина на ремонт Nисsan Tиrn, детали не подошли. "
            "что было 18 июня. Таранов Андрей Вячеславович."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")

    def test_16331_nissan_micra_mixed_script_stt_brand(self):
        """16331: STT «Ниssan Микроo» / «Микро, госномер» — nissan (Micra)."""
        t = (
            "Викинги Чери, диспетчер сервис. Юлия. Здравствуйте. "
            "Я хотел бы к вам записаться на обслуживание. "
            "С каким автомобилем? Ниssan Микроo, Саутов Алексей. "
            "Так, Микро, Госномер 396 десятого года автомобиль. "
            "Поменять масло в двигателе, фильтры. "
            "А вот знаете, через неделю, то есть не на следующей неделю, а через неделю."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")

    def test_16485_nissan_juke_mixed_script_stt_brand(self):
        """16485: STT «Нисsaн Жук» — nissan (Juke), не other_brand."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "хотел узнать по расценкам, сколько стоит замена масла в двигателе с расходниками? "
            "На какой автомобиль? Нисsaн Жук, номер 723 У МТ."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertEqual((dims.get("evidence") or {}).get("brand_hit"), "nissan")

    def test_26654_juke_without_explicit_nissan_maps_to_nissan(self):
        """26654: «Жук/Жук-012» без слова Nissan — это Nissan Juke."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "Юль, хотел бы уточнить по Жуку 012. "
            "С кем можно сейчас подъехать, по поводу поломки турбины? "
            "Жук-012, мне сам Жук пригоняли на той неделе."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertIn((dims.get("evidence") or {}).get("brand_hit"), ("жук", "джук", "juke", "nissan"))

    def test_13427_nissan_tiida_stt_brand(self):
        """13427: STT «Nissan Ciдаa» / «Ниссанс-Зида» / «Ниsсan Ида» — nissan (Tiida)."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса. "
            "Михайличенко Nissan Ciдаa, автомобильГ. Ниссанс-Зида,— "
            "автомобиль Гос Ниsсan Ида, седьмого года."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")

    def test_13474_nissan_pathfinder_stt_brand(self):
        """13474: STT «Pдфаinдr» / «Подфайм» — nissan (Pathfinder)."""
        for fragment in (
            "у меня Pдфаinдr у вам обслуживается запись на техобслуживание",
            "Саламин Вячеслав.файм Подфайм сномер 323",
            "у меня подфinr записаться на техобслуживание",
        ):
            dims = infer_sto_booking_dimensions("ассистент сервиса " + fragment)
            self.assertEqual(
                dims.get("service_brand"),
                "nissan",
                msg=f"failed for: {fragment!r}",
            )

    def test_13412_nissan_pathfinder_stt_brand(self):
        """13412: STT «Nissan ер» / «Потфаймер» / «Пайдер» — nissan (Pathfinder)."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Андреева, Юлия, слушаю вас. "
            "Здравствуйте, вы на Nissan ер делаете перевод? Пайдер делаете? "
            "меня интересует замена масла и плитры. "
            "Потфаймер какого года выпуска? Двенадцатого."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")

    def test_16709_nissan_pathfinder_potfider_stt_brand(self):
        """16709: STT «Nissan Potfider» / «Сподфайндер» — nissan (Pathfinder), не other_brand."""
        t = (
            "Викинги Чери, диспетчер сервиса. Юлия. Здравствуйте. Юлия, здравствуйте. "
            "Я у вас был в субботу. Nissan Potfider U323 ТУ.У И меня обслуживал Владимир. "
            "выдали неправильный список запчастей. Давайте я с Владимиром соединю вас. "
            "Владимир. М, слушаю вас. Владимир, день добрый, это Андрей Сподфайндер в субботу у вас был."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        ev = dims.get("evidence") or {}
        self.assertIn(ev.get("brand_zone"), ("early_client_vehicle", "client_ownership"))

    def test_27383_nissan_foltsfainer_explicit_discussion_over_chery_noise(self):
        """27383: явное обсуждение Nissan Pathfinder важнее фонового «Чери» в репликах оператора."""
        t = (
            "Викинги Чиренсервис Андреева. Юлия, слушаю вас. "
            "Можно ли купить задние брызговики на Nissan Foltsfainer? "
            "Nissan Folkfiner 2014 года в кузове R-52, задние брызговики хотел бы приобрести. "
            "Чери интернет-магазины запчастей обычные искать, да?"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        ev = dims.get("evidence") or {}
        self.assertIn(
            ev.get("brand_method"),
            ("exact", "explicit_brand_discussion_priority_nissan"),
            msg=ev,
        )

    def test_explicit_chery_discussion_over_nissan_noise(self):
        """Явное обсуждение Chery/Tiggo должно побеждать фоновое упоминание Nissan."""
        t = (
            "Викинги сервис, здравствуйте. У меня Chery Tiggo 8, 2023 года, по кузову T31, "
            "VIN заканчивается на 1234, нужны передние колодки. "
            "На Nissan тоже что-то когда-то смотрели, но сейчас именно по Tiggo вопрос."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        ev = dims.get("evidence") or {}
        self.assertIn(
            ev.get("brand_method"),
            ("exact", "explicit_brand_discussion_priority_chery"),
            msg=ev,
        )

    def test_call_13430_second_to_price_inquiry_warranty_stamp_narrow_to_in(self):
        """13430: смета 2-го ТО, отметка в книжке — СТО_ТО_вх, не НЕ_ТО по гарантии."""
        t = (
            "Викинги Чери ссистен-сервис, Андреева Юлия. Слушаю вас. "
            "Девушка, вы меня можете сориентировать по стоимости ТО? "
            "Какой автомобиль у вас? сим-семь 7 737 Промаакс. 2023-й год. "
            "ранее к нам заезжали, обслуживались у нас? Губина Анна. "
            "Черетиго 7 Про Макс, госномер 827. У меня 20 000 пробег сейчас. "
            "второй то, значит, подошло. Второе то меняется масло в двигателе фильтр масляный. "
            "По стоимости 19 050 ₽ стоит ТО. "
            "я сейчас подумаю, когда это перезвоню. "
            "если вы привозите свои фильтра, отметку о проведении ТО мы вам в книжку гарантийную не поставим. "
            "это когда на гарантии, да?"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected STO_TO_IN, got {rub} ({reason})")

    def test_10615_nissan_diagnostics_vehicle_between_words(self):
        """10615: «на диагностику автомобиля Nissan записать» — диагностика, не «то» из «…конкретно то…»."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. "
            "Могу ли у нас на диагностику автомобиля Nissan записать? "
            "Топливная система барахлит. Что конкретно то с автомобилем? "
            "Запись на июнь, диспетчер сервиса."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "diagnostics")
        self.assertNotEqual(dims.get("work_type"), "to")

    def test_27519_conditional_cancel_after_confirmed_diagnostics_booking_not_cancellation(self):
        """27519: «если изменится — отменю» после подтверждения слота не отменяет запись на диагностику."""
        t = self._transcript_from_log(27519)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        self.assertTrue(dims.get("is_booking"))
        ev = dims.get("evidence") or {}
        self.assertNotEqual(ev.get("booking_intent"), "cancellation")
        self.assertEqual(ev.get("work_intent"), "diagnostics")

    def test_10547_diagnostics_booking_intake_work_type(self):
        """10547: «на диагностику можно? записаться?» — work_type диагностика, не прочие."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Юлия. Здравствуйте. "
            "Здравствуйте, на диагностику можно? записаться? Чек загорелся? "
            "Какой автомобиль у вас? Рыбалкин Анатолий Петрович. Так, Tiggo 4. "
            "Только чек горит? У нас запись на июнь."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "diagnostics")
        self.assertEqual((dims.get("evidence") or {}).get("work_hit"), "diagnostics")

    def test_10545_fourth_to_big_to_booking_sto_to_in(self):
        """10545: четвёртое ТО, ТО большое с маслом, запись на слот — СТО_ТО_вх, work_type to."""
        t = (
            "Викинги Чери, диспетчер сервиса, Юлия. Подскажите, Чери 4 Pro, четвёртый во что стоит? "
            "Так, четвёртое ТО. Так, смотрите, ТО большое с заменой масла. "
            "По стоимости получается 46 100. Планирую. На девятое июля на осмотр лакокрасочного. "
            "Да, вы записаны 9 июля к 10 часам к ним. Давайте в 10:30 запишем к нам."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertEqual(rub, "STO_TO_IN", msg=reason)

    def test_10675_tiggo8_tmax_booking_chery_tenet_brand(self):
        """10675: «третье ТО на-8 ТМакс» — Chery Tiggo 8 Max, не other из «вклада»."""
        t = (
            "Викинги Чери диспетчер сервиса Юлия. Здравствуйте. Меня Артём зовут. "
            "Хотел бы записаться на третье ТО на-8 ТМакс, максимальная комплектация. "
            "Напомните, на кого автомобиль оформлен? В лизинге. "
            "Сейчас добегу до вклада и узнаю."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        ev = dims.get("evidence") or {}
        self.assertNotEqual(ev.get("brand_hit"), "лада ")

    def test_che_digit_general_chery_tiggo_brand(self):
        """Общее правило: «че 7» / «че8» без «ри» → chery_tenet."""
        for fragment in ("запись на то че 7 2024 года", "автомобиль че8 пробег 12000"):
            dims = infer_sto_booking_dimensions(fragment)
            self.assertEqual(
                dims.get("service_brand"),
                "chery_tenet",
                msg=f"failed for: {fragment!r}",
            )

    def test_kashka_stt_nissan_qashqai_brand(self):
        """STT «кашка» — Nissan Qashqai, не other_brand."""
        for fragment in (
            "Викинги Чери, слушаю. Ниссан кашка, пробег 96000.",
            "какой автомобиль? кашка, белого цвета.",
            "запись на то на кашка 18 года.",
        ):
            dims = infer_sto_booking_dimensions(fragment)
            self.assertEqual(
                dims.get("service_brand"),
                "nissan",
                msg=f"failed for: {fragment!r}",
            )

    def test_14463_nissan_qashqai_stt_not_chery_from_chernogo(self):
        """14463: Nissan Qashqai (Ниssan/Кашхaй/Casain); «чёрного» не маркер Chery."""
        log = open("/home/vikingi/VikingiAll/logs/vikingi_autofetch.log", encoding="utf-8").read()
        t = (
            log.split("call_id=14463")[1]
            .split("--- Транскрипт ---")[1]
            .split("---")[0]
            .strip()
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertNotEqual((dims.get("evidence") or {}).get("brand_hit"), "chery")

    def test_14517_nissan_paski_stt_qashqai_brand(self):
        """14517: STT «NissanPаski» (Qashkai) — nissan, не other_brand."""
        log = open("/home/vikingi/VikingiAll/logs/vikingi_autofetch.log", encoding="utf-8").read()
        t = (
            log.split("call_id=14517")[1]
            .split("--- Транскрипт ---")[1]
            .split("---")[0]
            .strip()
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")

    def test_sem_maks_and_bare_maks_chery_tenet_brand(self):
        """STT «сем макс» / голое «МАКС» — Chery/Tenet, не other_brand."""
        for fragment in (
            "Викинги Чери, диспетчер. автомобиль сем макс, пробег 28000.",
            "какой автомобиль? макс, госномер 193.",
            "запись на то, машина МАКС.",
        ):
            dims = infer_sto_booking_dimensions(fragment)
            self.assertEqual(
                dims.get("service_brand"),
                "chery_tenet",
                msg=f"failed for: {fragment!r}",
            )

    def test_10422_scod_actavia_not_dealer_chery_brand(self):
        """10422: STT «Scod Actavia» в заявке клиента важнее «автосалон Чери Тенет»."""
        t = (
            "павел добрый день компания викинги меня зовут юлия удобно разговаривать "
            "мы заявку получили по автомобилю scod actavia gus номер 269 "
            "ошибка селектор в p обратитесь в сервисный центр "
            "смотрите мы находимся по адресу заставная 3 что автосалон чери тенет "
            "завтра в 9:00 подъезжайте сделаем диагностику"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")

    def test_10195_ac_warranty_chr_9_crm_not_narrow_to(self):
        """10195: Чər 9 + кондиционер/гарантия/стенд — chery_tenet, узкая НЕ_ТО."""
        t = (
            "юлия слушаю на сервис кондиционер плохо дует записаться "
            "фамилия александр телефон 3238 чər 9 черного цвета госномер 930 пробег 12400 "
            "15 июня гарантия стенд 2400 фрион диагностика"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_10268_ac_recharge_future_to_credit_not_narrow_to(self):
        """10268: заправка кондиционера; «ТО» только как зачёт оплаты — НЕ_ТО."""
        t = (
            "сергей чери тигго 8 проверка компрессора кондиционера эвакуация "
            "заправка компрессора кондиционера с красителем фрион 920 г "
            "как будете проходить техническое обслуживание деньги пойдут со скидкой "
            "заправим и сразу запишем на 13-14 июня"
        )
        ct = "STO_OUT"
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_explicit_to_priority_over_planned_diagnostics(self):
        """Явное ТО в тексте важнее «потребуется диагностика»."""
        t = (
            "диспетчер сервиса. хотел пройти то на следующей неделе. "
            "необходимо запланировать диагностику. потребуется диагностика."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertNotEqual((dims.get("evidence") or {}).get("work_intent"), "diagnostics")

    def test_10568_outbound_no_regulatory_to_discussion_not_narrow_to(self):
        """10568: исх. перезвон по заявке на стекло — нет обсуждения регламентного ТО → НЕ_ТО."""
        from call_analytics.classify_by_transcript import classify_auto

        t = (
            "Добрый день, слушаю. Алло, Алексей, да? Да. Угу, здравствуйте. "
            "это компания Викинги Юлия, диспетчер сервиса. Удобно? Да, пару минут? "
            "Заявку оставляли по поводу замены лобового стекло на Nissan Cashkai. Правильно?"
        )
        self.assertEqual(classify_auto(t), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, "STO_OUT", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "no_regulatory_to_discussion_not_narrow_to",
                "non_reg_service_topic",
                "no_explicit_to_topic",
            ),
            msg=reason,
        )

    def test_negated_to_on_which_not_scheduled_to_marker(self):
        """«без то на который я записался» — не маркер регламентного ТО (10568)."""
        from call_analytics.sto_booking_dimensions import contains_any_to_marker_hit

        low = (
            "заявку оставляли по поводу стекла. без то на который я записался, "
            "это мы только про стекло говорим."
        )
        self.assertFalse(contains_any_to_marker_hit(low)[0])

    def test_10930_outbound_zero_to_chertik9_sto_to_out(self):
        """10930: исх. CRM — «на ТО хотите», «время ТО», «нулевое ТОрем», «Чertik 9» → СТО_ТО_исх."""
        t = (
            "Д. Алло. Добрый день, компания Викинги. Меня зовут Юлия. Удобно разговаривать? "
            "Да-да. Заявку получили по автомобилю Чertik 9, госномер 719. "
            "На ТО хотите записаться? Дату выбрали — понедельник 1 июня. Время 2 часа дня. "
            "Удобно будет подъехать не в 2 часа дня, а в 14:30? "
            "А сколько займёт время Тго? Я просто это не знаю, вот это нулевое ТОрем. "
            "В среднем два часа занимает. Ну хорошо, давайте полтретьего тогда. "
            "Меняется в обязательном порядке маслодвигатели и фильтр масляный, и общий осмотр автомобиля."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, "STO_OUT", dims, department="STO")
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_11148_inbound_new_to_booking_sto_to_in(self):
        """11148: старый слот 1 июня + новая запись на пятое ТО 10 июня — СТО_ТО_вх, не pre_visit."""
        t = (
            "Викинги Чресервис Андреева Юлия. Здравствуйте. "
            "А подскажите, мне бы на Т ТО записаться? "
            "Ранее к нам заезжали, обслуживались? Да. Анна Гайдаева. "
            "Я вижу, вы записаны на 1 июня на 3 часа дня — по освоению заднего бокового блока. "
            "Вы подъедете, да? первого числа? Да. "
            "Смотрите, на ТО сейчас запись идёт, начиная с десятого числа. "
            "Десятое, среда, в 8:30. А сколько будет пятое то по времени? "
            "Пятый то в среднем занимает 3,5 ч. Меняется масло в двигателе, фильтры. "
            "По стоимости 32 200. Пятый тог в подарок — сертификат возьмите. "
            "10 июня, время 8:30, записали. Накануне позвоним, напомним о записи."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_13750_outbound_existing_to_reschedule_slot_not_narrow_to(self):
        """13750: «записались на ТО» + «время освободилось», перенос слота — STO_OUT, узкий НЕ_ТО."""
        t = (
            "Алло. Станислав Викторовна, добрый день, компания Викинги. Юлия, диспетчер. "
            "Удобно разговаривать вам? Да-да-да. Мы с вами на 19 июня записались на 9:00 на ТО. "
            "У нас на завтра время освободилось на 10:30. Если удобно, можем запись вашу перенести. "
            "Давайте на завтра на 10:30. Перенесём, да? Всё, тогда завтра в 10:30 нас будем ожидать. "
            "А сколько с собой денег взять? третье ТО. Без защиты 25 720."
        )
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, "STO_OUT", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "outbound_existing_to_reschedule_not_narrow_to")

    def test_9597_pre_visit_confirm_still_not_narrow_to(self):
        """9597: перезвон «завтра записаны на ТО, подъедете?» — узкая НЕ_ТО (регрессия)."""
        t = (
            "Алло. Николай Геннадьевич, добрый день. компания Викинги, Юлия, диспетчер. "
            "Завтра записаны к нам на 15:30 на ТО. Хотели уточнить, подъедете, ожидаем вас? "
            "Хорошо, ждём вас. Напомните сумму, у вас четвёртое ТО, стоимость 23 900. "
            "Завтра ждём в 15:30."
        )
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, "STO_OUT", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            ("pre_visit_confirmation", "pre_visit_confirmation_outbound"),
        )

    def test_18096_outbound_po_zapisi_utochnit_zvonju_not_narrow_to(self):
        """18096: STT «по записи уточнить звоню» + слот на завтра — НЕ_ТО, не СТО_ТО_исх."""
        t = (
            "Д. Что-то Женька напортачил. Алло. Алло, Алексей, здравствуйте. "
            "Это компания Викинги, Юлия, диспетчер сервиса. — Викинги Юлия, диспетчер сервиса. — "
            "Я по записи уточнить звоню. На завтра в 7:50 планировали автомобиль запись? — Да. — "
            "Ага, всё отлично, а то у нас какая-то тут нестыковочка.— "
            "Тогда завтра в 7:50 вас ждём. — Спасибо. — Хорошо, спасибо. — "
            "Всего доброго. — Всего добро."
        )
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        self.assertEqual(ct, "STO_OUT")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "pre_visit_confirmation")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "outbound_appointment_reminder",
                "pre_visit_confirmation",
                "pre_visit_confirmation_outbound",
            ),
        )

    def test_18714_outbound_tomorrows_booking_invoice_not_narrow_to(self):
        """18714: «по завтрашней записи», счёт и доплата — существующий визит, НЕ_ТО."""
        t = self._transcript_from_log(18714)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "pre_visit_confirmation",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            ("pre_visit_confirmation", "pre_visit_confirmation_outbound"),
        )

    def test_18107_outbound_zapisyvali_na_zavtra_pre_visit_not_narrow_to(self):
        """18107: «записывали на завтра … ТО. Всё ли в силе?» — НЕ_ТО."""
        t = (
            "! Алло. Екатерина Анатольевна, добрый день. Это компания Викинги, диспетчер сервиса Юлия. "
            "Записываете. Диспеер сервиса Юлия. Записывали на завтра, автомобиль в 12:00 на техническое "
            "обслуживание. Всё ли в силе? Вы приедете? Да, всё в силе. Угу. Напомните мне про время. "
            "Ну, я имею в виду. 12 часов. Нет, это мне понятно. Сколько по времени займёт? — Два часа, "
            "если никаких дополнительных работ, нареканий нет по автомобилю,да, около двух часов займёт. "
            "Угу. Всё, поняла. Ясно. Ладно, хорошо, спасибо. Всё, завтра вас ожидаем. Спасибо. "
            "Всего доброго. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        self.assertEqual(ct, "STO_OUT")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "pre_visit_confirmation")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "outbound_appointment_reminder",
                "pre_visit_confirmation",
                "pre_visit_confirmation_outbound",
            ),
        )

    def test_18153_outbound_today_booked_tehobsluzhivanie_not_narrow_to(self):
        """18153: «вы сегодня к нам записаны на техобслуживание … Вы будете?» — НЕ_ТО."""
        t = (
            "Побилькажите, пожалуйста, мия владельца. Алло. Александр, добрый день. "
            "Чери Центр Рыкигина Заставной Владимир беспокоит. Вы сегодня к нам записывались. "
            "Владимир беспокоит. Вы сегодня к нам записаны на техобслуживание в 13:00. Вы будете? "
            "Добрый день. Что у вас?"
        )
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        self.assertEqual(ct, "STO_OUT")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "pre_visit_confirmation")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "outbound_appointment_reminder",
                "pre_visit_confirmation",
                "pre_visit_confirmation_outbound",
            ),
        )

    def test_18187_lixiang_suspension_refusal_not_narrow_to_other_brand(self):
        """18187: LiXiang + рычаги/развал + «не принимали» — НЕ_ТО, марка Прочие."""
        t = (
            "Д ождь это ли начинается? Пахнет как-то прямо. Угу. Алло. Алло, Андрей, добрый день, "
            "Компания Викинги Юлия, диспетчер сервиса. Ваш телефон передали, звонили. Слушаю вас. "
            "Да, здравствуйте, Юлия. как, ну первый вопрос такой, вот смотрите, я раньше у вас там "
            "Ниссан был, у меня обслуживался.Уга. А сейчас у меня Лиссян. Вы такие машины делаете "
            "обслуживание? Ни разу не был, ещё не принимали такие автомобили. Ни разу не был? Нет. "
            "Ага. вот это у меня был первый вопрос. Просто мне там по подвеске, ну, нижние рычаг. "
            "Ну, нижние рычаги надо поменять и, соответственно, разварасхождение сделать. вот. "
            "Я как бы по привычке Просто, потому это я у вас почти всю жизнь обслуживался, вот я "
            "поэтому решил вас позвонить. А рычаги именно запчасти у вас есть в наличии? Вы уже "
            "где-то приобрели их? Да. Нижние рычаги. Лисян, да, у вас? Угу. вот насчёт развала тут "
            "надо. Хмм, не могу сказать. Надо будет уточнить этот момент. Не, вообще же вы же вообще "
            "делаете маши вхождение делаете. То есть у меня первое ТО был, я вроде как нашёл, есть. "
            "такие, ну, не очень хорошие, но, по крайней мере, они делают этот фит-сервис. "
            "Ну, у вас китайские, да, Cherry обслуживаются, но вот полисяном ещё вотчё-то даже никто "
            "к там не обращался. если чест — Понял. — Если честно. — Ага. — Ясно, тогда, понял вас, "
            "спасибо. — Ну, пожалуйста. Всего доброго.— Доброго. — До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertNotEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "brand_service_refusal_not_narrow_to",
                "primary_defect_diagnostic_not_narrow_to",
            ),
        )

    def test_13350_pre_visit_short_reminder_not_narrow_to(self):
        """13350: «завтра на 15:30 записан на ТО» + «я буду, ждём» — узкая НЕ_ТО."""
        t = (
            "Н. Блядь. Алло. Алло, Вячеслав, добрый день. Компания Викинги беспокоит. "
            "Завтра на 15:30 записан на техническое обслуживание. "
            "Да-да- всё, я буду. Угу, всё, спасибо, ждём. До свидания."
        )
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, "STO_OUT", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            ("pre_visit_confirmation", "pre_visit_confirmation_outbound"),
        )

    def test_20022_inbound_callback_existing_to2_confirmation_not_narrow_to(self):
        """20022: повторный перезвон по уже назначенному ТО-2 на завтра — НЕ_ТО."""
        t = (
            "Официальный дилер Чери и Тенет Викинги на Заставной, администратор Диана. Добрый день! "
            "Здравствуйте. Вы звонили, я помню, завтра я на ТУ-2 записывался на 14:30. "
            "Диспетчер сервиса Юлия. Здравствуйте. Вы звонили меня предупредить. "
            "Я помню, завтра мне на ТО-2, 14:30. Да, звонили вам. Всё приедете? "
            "Да-да, приеду. Хорошо, спасибо, что перезвонили. Завтра вас ожидаем."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "pre_visit_confirmation")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            ("pre_visit_confirmation", "pre_visit_confirmation_outbound"),
        )

    def test_19976_repair_quote_a_to_particle_not_regulatory_to(self):
        """19976: «а то ...» в речи о ремонте не должно считаться маркером регламентного ТО."""
        t = (
            "Официальный дилер Чери, администратор. Добрый день. "
            "Ниссан еще ремонтируете? Да. Хочу узнать, сколько будет стоить ремонт, "
            "а то приеду, денег не хватит. Перевожу на диспетчера. "
            "Викинги Чери, диспетчер сервиса Юлия. "
            "Надо поменять цепь ГРМ и отрегулировать клапана. "
            "Интересно, сколько это будет стоить работа. "
            "Сейчас мастер занят, как освободится, проконсультирует."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertNotEqual((dims.get("evidence") or {}).get("work_intent"), "to_price_quote")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_11518_engine_replacement_price_on_service_line_not_op(self):
        """11518: линия Андреевой, цена замены двигателя, авто на сервисе — STO_IN, не OP."""
        t = (
            "дикингчемсервис андреева юлия слушаю вас "
            "подскажите по стоимости замены мотора "
            "кузнецова валентина nissan у вас на сервисе ждёт замены двигателя "
            "передам мастеру-приёмщику евгению он проконсультирует "
            "ожидайте звонка"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"), msg=f"expected STO_IN, got {dept}/{ct}")

    def test_11394_first_to_price_tenet_t8_booking_sto_to_in(self):
        """11394: стоимость «первого Т» на Tenet T8 + запись — СТО_ТО_вх, не STO_OUT/НЕ_ТО."""
        t = (
            "викинги чери диспетчер сервиса юлия здравствуйте "
            "подскажите стоимость первого т на те нат8 "
            "тенет t8 20 700 по стоимости когда можно на ближайшие "
            "давайте на пятницу 13 30 записываю накануне напомним о визите"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"), msg=f"expected STO_IN, got {dept}/{ct}")
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_11441_autopartner_to_booking_sto_to_in(self):
        """11441: заявка Автопартнёра на ТО, «ТО подходит 30 000», запись — СТО_ТО_вх."""
        t = (
            "викинги чери диспетчер сервиса юлия здравствуйте "
            "запрос с автопартнер парtнерs на то можете посмотреть чери 226 "
            "запишите госномер 226 чери tiggo 4 pro "
            "то подходит да то 30 000 30 000 "
            "11 число 9 30 записали"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"), msg=f"expected STO_IN, got {dept}/{ct}")
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_16743_mileage_to_booking_arrizo8_sto_to_in(self):
        """16743: «на ТО машину записать» + смета на 30 000 км + запись на среду — СТО_ТО_вх, не oil_change."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. Юлия, добрый день. Меня Дмитрий зовут. "
            "Случайно на завтраНет у вас времени на ТО машину записать? На завтра, э-э, пока нет, "
            "может быть, и освободится какое-то окошко. Давайте я запишу номер телефона и перезвоню вас. "
            "Какое у вас будет ТО и какой автомобиль? Ореза 8. "
            "сигнализацию ставили, не совсем корректно работает. отдел допборудования. "
            "30ать тысяч у меня сейчас. на 30 0000 замена масла двигателя с масляным фильтром, "
            "салонный фильтр воздушного двигателя, свечи зажигания, топливный фильтр. 33 500. "
            "на следующую среду тогда запишите на утро. 8:30 утра. Я внесу в запись на следующую среду."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertIn(
            (dims.get("evidence") or {}).get("work_intent"),
            ("to_booking", "to_price_quote"),
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_call_11583_zero_to_consultation_booking_not_reschedule_false_positive(self):
        """11583: варианты когда делать нулевое ТО + запись 9:30 — СТО_ТО_вх, не перенос (8567)."""
        t = (
            "Викинги Чери, ассистент сервиса Андреева Юлия. Слушаю вас. "
            "что мне сделать с то нулевой то. "
            "первый вариант сделать заранее техническое обслуживание. "
            "третий вариант что уже провести у них третий то провести у них. "
            "может сейчас раньше сделать его перед поездкой. "
            "я завтра на 2 часа записалась на замену прав. "
            "нулевое техническое обслуживание стоимость то 13 300. "
            "ближайшее завтра есть утром в 9:30 могу вас записать. "
            "на завтра на 9:30 вас записали."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_8567_reschedule_existing_to_stays_not_narrow_to(self):
        """8567: завтра на ТО записана + перезаписаться — НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "Я завтра на ТО записана. А можно перезаписаться на другой день? "
            "Приболела, не получилось завтра приехать."
        )
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_28673_inbound_reschedule_existing_to_with_stt_too_is_not_narrow_to(self):
        """28673: «на завтра на ТОО записан ... перенести» — существующая запись, НЕ_ТО."""
        t = self._transcript_from_log(28673)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "existing_to_reschedule_not_narrow_to")

    def test_19385_existing_zero_to_early_dropoff_not_narrow(self):
        """19385: ТО уже назначено на завтра; вопрос о сдаче авто сегодня — НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "У вас нулевое ТО завтра в 11:00. Могу ли я сегодня машину пригнать? "
            "Напомните, пожалуйста, во сколько ещё раз записано? К 11:00 у вас. "
            "Сегодня с полшестого до полседьмого приезжайте, в воскресенье заберёте."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "pre_visit_confirmation")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "pre_visit_confirmation")

    def test_14714_new_to_booking_early_dropoff_is_sto_to_in(self):
        """14714: «запишите на ТО» + 4-е ТО + слот; вопрос про ранний заезд — СТО_ТО_вх."""
        t = (
            "Добрый день. Здравствуйте. Да, запишите, пожалуйста, на ТО. "
            "Андрей. Ранее к нам заезжали. Капитула. "
            "Подошло четвёртое техническое обслуживание. Четвёртое ТО объёмное. "
            "По стоимости выходит 46 200. На завтра есть время на половину второго. "
            "Давайте на завтра. завтра у вас 24-е число, время 13:30. Вас записали. "
            "А мы можем приехать раньше, оставить автомобиль и доехать? "
            "Ну, там, допустим, с утра 10:00. можно перевести пораньше, да?"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_call_11581_zero_to_booking_june20_is_sto_to_in(self):
        """11581: нулевое ТО, цена, слот 20 июня 12:00 — СТО_ТО_вх, не ложный 10549."""
        t = (
            "Викинги Чери,ассистент сервиса Андреев Юлия. Слушаю вас. Здравствуйте. "
            "Хотела на нулевое ТОО записаться. Елена. "
            "нулевое техническое обслуживание включает замену масла, "
            "осмотрим ходовую часть, тормозную систему, рулевое управление. "
            "стоимость ТО будет стоить 13 300 рублей. "
            "двадцатое время с 9:30. давайте на 12:00. "
            "мы с вами записались на 20 июня, время 12:00 дня. будем вас ожидать."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_10549_existing_to_tomorrow_parts_not_narrow_to(self):
        """10549: «записан завтра на ТО» + рулевые наконечники — НЕ_ТО, не СТО_ТО_вх."""
        t = (
            "Официальный дилер ChariTet, администратор Диана. Татьяна, я записан завтра на ТО. "
            "По сервису вопрос? Да, у меня вопрос по сервису. Переведу на специалиста. "
            "Викинги Чери — диспетчер сервиса. Юлия. Здравствуйте, Юлия. Я записан завтра на ТО. "
            "Рулевые наконечники есть у вас? Отдел запчастей, перезвоним. "
            "Приедете завтра на техническое обслуживание? Да."
        )
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_12574_third_to_wipers_booking_sto_to_in(self):
        """12574: новая запись на 3-е ТО + любые доп. работы — СТО_ТО_вх, не existing_to_visit_parts."""
        t = (
            "чери викинги ассистент сервиса юлия слушаю какой автомобиль "
            "тенет семь промакс третье то включает замену масла стоимость 28 200 "
            "щетки поменять передние ветровые проверю по наличию "
            "да есть в наличии за две штуки 3850 "
            "11:30 записались на техническое обслуживание 3-е то "
            "мы с вами записались на 10 июня время 11:30"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_16323_steering_pull_crooked_wheel_diagnostics_not_to(self):
        """16323: кривой руль + тянет влевo — диагностика, не work_type to от «то туда, то сюда»."""
        t = (
            "Викинги Чери диспетчер сервиса Юлия. Здравствуйте. "
            "Две недели назад у вас машину купил. "
            "на девятый день смотреть на руль, то tуда, то сюда, то в гараже она стояла. "
            "руль стоит криво, заметил, и тянет сильно влевo. "
            "При прямом руле, прямых спицах явно влевo сильно уходит, это очень опасно. "
            "машину тянет влевo и руль тянет слевo. "
            "я делаю запись. перезвоним вас и сообщим."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "diagnostics")
        dept, ct = classify_auto(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_12619_steering_knock_not_work_type_to(self):
        """12619: стук при повороте руля — other_work/диагностика, не work_type to от «то проводят»."""
        t = (
            "чери викинги сервис андреева юлия слушаю здравствуйте "
            "на моем автомобиле что-то звук при повороте налево стук появился "
            "какой автомобиль chritigamprmax пробег 16000 "
            "стук проявляется когда руль выкручиваешь налевo "
            "готовят автомобили то проводят в основном расписаны полностью то че "
            "22 июня понедельник 10:00 записали накануне подтверждать запись"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "diagnostics")
        dept, ct = classify_auto(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_11927_inbound_to_booking_not_sto_out(self):
        """11927: «мне бы на ТО записаться» + «вы хотели бы оплатить» — STO_IN, не ложный STO_OUT."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. Девушка, здравствуйте. "
            "мне бы на ТО записаться. Arrizo 8. Агекян. "
            "Какой пробег? 40000. К вам можно обращаться? Давид. "
            "А по расчётному счёту мы можем сделать? От юрлица вы хотели бы оплатить? Да. "
            "записали вас на десятое число."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_11908_car_at_service_pickup_after_to_not_narrow_to(self):
        """11908: «моя машина у вас», заберу после 6-го ТО — выдача, не запись на ТО (НЕ_ТО)."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Андреева Юлия, слушаю вас. "
            "Здравствуйте. Девушка, моя машина у вас Чери 8, номер 078. "
            "Я её сегодня заберу после шестого ТО она у вас. "
            "Фамилию владельца? Смирнов. Алексей Викторович. "
            "где-то в 14:15 я подъеду за автомобилем. "
            "передам мастер-приёмщику. Ожидаем тогда вас сегодня. "
            "выдадим вам автомобиль, подъезжайте."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        ev = dims.get("evidence") or {}
        self.assertTrue(ev.get("car_already_at_service"))
        self.assertEqual(dims.get("work_type"), "other_work")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_16946_readiness_car_already_at_to_pads_not_narrow(self):
        """16946: готовность авто уже на ТО (колодки) — НЕ_ТО, Прочие; не «ТО на колодки»."""
        from call_analytics.sto_booking_dimensions import contains_any_to_marker_hit

        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "Здравствуйте, девушка, я хотел узнать по готовности автомобиля, "
            "там на ТО, на колодки меняются. "
            "Так, а напомните, кому вчера оставляли автомобиль? Вчера же, да, оставляли? "
            "Да-да-да. Фамилию владельца? Прохоров. "
            "Владимир говорит, заканчивает работу, он вас наберёт. Спасибо. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        self.assertFalse(
            contains_any_to_marker_hit(t.lower())[0],
            msg="«на ТО, на колодки» must not be marker «то на колодки»",
        )
        dims = infer_sto_booking_dimensions(t)
        ev = dims.get("evidence") or {}
        self.assertTrue(ev.get("car_already_at_service"))
        self.assertEqual(dims.get("work_type"), "other_work")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "car_at_service_status_not_narrow_to",
                "inbound_master_car_at_service_followup_not_narrow_to",
                "non_reg_service_topic",
                "no_explicit_to_topic",
            ),
            msg=reason,
        )

    def test_22106_car_already_at_service_readiness_not_narrow_to(self):
        """22106: авто уже сдали на сервис, звонок про готовность — НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса. Юлия. здравствуйте. Здравствуйте, Юлия. "
            "Хотелось бы узнать, мы вот в 9:30 сдали Владимиру автомобиль на техобслуживания. "
            "Примерно хотелось бы знать, когда будет готов? "
            "Минутку, я уточню сейчас у Владимира Пока в работе примерно когда "
            "Алло, Алик Викторович. Владимир передаёт, что примерно ещё час-полтора где-то в работе, "
            "он с вами свяжется. Чаполтора. Хорошо, спасибо. Да, перезвонит. "
            "Хорошо. Хорошо, спасибо. Да, перезвонит вам в любом случае. Угу., Спасибо. "
            "Всего доброго."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertTrue((dims.get("evidence") or {}).get("car_already_at_service"))
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "car_at_service_status_not_narrow_to",
                "inbound_master_car_at_service_followup_not_narrow_to",
            ),
            msg=reason,
        )

    def test_16790_alarm_disable_not_to_work_type_other(self):
        """16790: отключить доп. сигнализацию + отдел допоборудования — Прочие, не ТО из «записаться то»."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. Мне бы связаться с Владимиром Чатаевым, можно? "
            "Минутку, я попробую переключить. Алло? Да-да. Владимир сейчас на улице с клиентом не может пока говорить. "
            "Вы записаться, может быть, захотели? Да мне, у меня записаться то чё-то, мне только отключить эту самую сигнализацию. "
            "Только это я уже третий раз говорю: Отключить сигнализацию. "
            "сигнализацию дополнительная установленная или в машине штатная? Нет, дополнительная, по-моему. "
            "Дополнительная сигнализацию — это вопрос не к нашему цеху, а есть у нас специальный отдел "
            "по сигнализациям, по дополнительному оборудованию. "
            "Давайте тогда Владимиру сейчас ваш телефон передам. Ну, он как освободится, вас наберёт."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertEqual((dims.get("evidence") or {}).get("work_hit"), "alarm_accessory")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_17038_seat_cushion_visit_to_scope_warranty_tail_not_to_work_type(self):
        """17038: CRM визит по подушке сиденья; четвёртое ТО + AC в хвосте — Прочие, не ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "По вашему автомобилю звоню, Чери Тиг 4 Про, обращались к вам по поводу водительского сиденья. "
            "Заказывали подушку сиденья. Запчасти у нас поступают в конце недели. "
            "Давайте спланируем вам визит. На семнадцатое число могу предложить. "
            "двадцатое утро раннее можете? В 8:00 утра. 20-е июля, понедельник, 8:00. Будем вам ожидать. "
            "А вы можете меня проконсультировать по гарантии? Не по гарантии точнее, а по четвёртому ТО? "
            "какие работы на четвёртом ТО происходят? И регламент работ, что должны проверять? "
            "На четвёртом ТО масло в двигателе с масляным фильтром, фильтра салонные воздушные. "
            "По стоимости порядка 46 000. А происходит диагностика оборудования кондиционер? "
            "про ТО система кондиционирования не проверяются. кондиционер оказался пустым. "
            "что платит за заправку кондиционер на двухгодовалом автомобиле? "
            "у меня было на четвёртом ТО к вам обращение, неправильно дул кондиционер. "
            "в рамках четвёртого ТО этот узел проходит проверку?"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertEqual((dims.get("evidence") or {}).get("work_hit"), "replacement")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_18574_outbound_new_to_application_with_seat_side_issue_narrow_to(self):
        """18574: новая заявка на второе ТО; сиденье — дополнительный вопрос."""
        t = self._transcript_from_log(18574)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_18670_past_to60_then_current_to75_booking_narrow_to(self):
        """18670: прошлое ТО-60 сменяется текущим ТО-75 с расчётом и записью."""
        t = self._transcript_from_log(18670)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_18643_chery_tiggo7pro_mixed_script_brand(self):
        """18643: STT «Чеrtga 7Pро Мк» — Chery Tiggo 7 Pro Max."""
        t = self._transcript_from_log(18643)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))

    def test_25693_chtg7l_maps_to_chery_tiggo7l(self):
        """25693: «ЧTg 7L» должен распознаваться как Chery/Tiggo 7 L."""
        t = (
            "Чери Викинги, ассистент сервиса. "
            "Хотел бы на ТО записаться. "
            "Автомобиль ЧTg 7L, госномер 294."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))

    def test_18651_lacquer_paint_inspection_is_body_shop(self):
        """18651: осмотр лакокрасочного покрытия — кузовные работы."""
        t = self._transcript_from_log(18651)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "body_shop")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_25936_dtp_body_shop_not_overridden_by_jetour_brand_refusal(self):
        """25936: ДТП/кузовной осмотр — body_shop; отказ по Jetour не меняет вид работ."""
        t = self._transcript_from_log(25936)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertEqual(dims.get("work_type"), "body_shop")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_18668_nissan_qashqai_time_phrase_not_chery(self):
        """18668: Nissan Qashqai; «а чё, 9:30 есть?» — вопрос о времени, не Chery 9."""
        t = self._transcript_from_log(18668)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertTrue(dims.get("is_booking"))

    def test_18673_wheel_balancing_is_other_work(self):
        """18673: балансировка колёс — прочие работы, не диагностика из фоновой речи."""
        t = self._transcript_from_log(18673)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertTrue(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "wheel_balancing")

    def test_28201_explicit_diagnostics_request_not_overridden_by_balancing_hint(self):
        """28201: «на диагностику записаться можно» => diagnostics, даже при «либо балансировка»."""
        t = self._transcript_from_log(28201)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        self.assertTrue(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "diagnostics")

    def test_18578_warranty_refusal_document_followup_work_type(self):
        """18578: получить письменный отказ по гарантийному случаю — warranty, не body_shop."""
        t = self._transcript_from_log(18578)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "warranty")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "warranty_decision_document",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_16798_to2_price_booking_tenet_t7_sto_to_in(self):
        """16798: цена ТО-2 Tenet T7 + запись 15:30 — СТО_ТО_вх, не master follow-up."""
        t = (
            "Викинги Чери, диспетчер сервиса Гринкина Юлия. Здравствуйте. "
            "Я хотел узнать, сколько будет стоить ТО-2 на территоре 7ЭЛ. "
            "ТО-2. Вы имеете в виду на 20ати тысячах километрах пробега? Да. "
            "С7мЭль у вас полный привод. По стоимости у вас получается 20 800 ₽. "
            "замена масла двигателя с масляным фильтром, салонный фильтр, "
            "контрольно-смотровые работы по автомобилю, подвеска, ходовая часть. "
            "ранее у вас обслуживались? Был автомобиля на сервисе? Первый раз, да. "
            "записываемся на 15-е число, на среду, 15:30. Записали вас."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_11996_gearbox_oil_price_not_regulatory_to(self):
        """11996: замена масла в вариаторе + «техобслуживание коробки» — прочие, не ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "меня интересует стоимость замены масла в вариаторе. "
            "Nissan Qashqai Даниленко. Только замена масла вариатора? Да. "
            "техническое обслуживание коробки вариатор 7 000 по стоимости. "
            "если просто только работы, седьмое то 000 фиксированная стоимость. "
            "спасибо, подумаем."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertEqual((dims.get("evidence") or {}).get("work_hit"), "oil_change")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_14057_cherez_igus_infromax_chery_tenet_brand(self):
        """14057: STT «Черезигус Инфромакс» / «Чиг 7ромокс» — chery_tenet, не other_brand."""
        for t in (
            "Машина какая у вас? Черезигус Инфромакс.",
            "автомобиль у вас Чиг 7ромокс",
            "Через 7 про макс",
        ):
            with self.subTest(t=t[:40]):
                dims = infer_sto_booking_dimensions(t)
                self.assertEqual(
                    dims.get("service_brand"),
                    "chery_tenet",
                    msg=f"expected chery_tenet, got {dims.get('service_brand')} for {t[:50]!r}",
                )
        dims_neg = infer_sto_booking_dimensions("перезвонить через месяц")
        self.assertNotEqual(dims_neg.get("service_brand"), "chery_tenet")

    def test_romaks_and_max_in_to_context_are_chery(self):
        """STT «ромакс» = Pro Max; «макс» в контексте модели/ТО достаточно для Chery."""
        for model in ("ромакс", "ромокс", "про Макс", "макс"):
            with self.subTest(model=model):
                t = (
                    "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
                    f"Скажите, пожалуйста, четвёртое ТО сколько будет стоить на {model}, "
                    "робот, передний привод?"
                )
                dims = infer_sto_booking_dimensions(t)
                self.assertEqual(
                    dims.get("service_brand"),
                    "chery_tenet",
                    msg=f"expected Chery for {model!r}: {dims}",
                )

    def test_18023_che_digit_eromaks_chery_tenet_brand(self):
        """18023: STT «Че7еромакс» — chery_tenet (Че + цифра + макс), не other_brand."""
        for t in (
            "Какой автомобиль у вас? Че7еромакс.",
            "автомобиль Че4еромакс запись на то",
            "Че8макс",
            "Че7про",
        ):
            with self.subTest(t=t[:40]):
                dims = infer_sto_booking_dimensions(t)
                self.assertEqual(
                    dims.get("service_brand"),
                    "chery_tenet",
                    msg=f"expected chery_tenet, got {dims.get('service_brand')} for {t[:50]!r}",
                )
        t18023 = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "когда можно записаться на ТО-5? С каким автомобилем? "
            "Че7еромакс. ТО-5, да, подходит? Да. "
            "на 21-е запишите меня. Давыденко Евгений. Всё, записали."
        )
        dims = infer_sto_booking_dimensions(t18023)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")

    def test_27762_chere_and_cher_short_forms_map_to_chery(self):
        """27762: «чере7макс», «чер 7/8/9», «чер макс» -> Chery/Tenet."""
        for t in (
            "по поводу автомобиля чере7макс с госномера 10",
            "автомобиль чер 7",
            "машина чер8макс",
            "по авто чер макс",
        ):
            with self.subTest(t=t):
                dims = infer_sto_booking_dimensions(t)
                self.assertEqual(
                    dims.get("service_brand"),
                    "chery_tenet",
                    msg=f"expected chery_tenet, got {dims.get('service_brand')} for {t!r}",
                )

    def test_19798_ch4_proba_is_chery_t4_pro_max(self):
        """19798: «по вашему автомобилю Ч4 проба» = Chery T4 Pro Max."""
        t = (
            "Компания Викинги. Мы заявку получили по вашему автомобилю Ч4 проба, "
            "госномер 306. На ТО к нам хотите записаться?"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")

    def test_19716_cs7mrka_l_is_chery_7l(self):
        """19716: «C,S7мрка L» = Chery Tiggo 7 L."""
        t = (
            "Можно у вас пройти ТО? Какой пробег у вас и какой автомобиль? "
            "ТО-2, C,S7мрка L. Угу, 7ЭЛ, да, у вас? Да."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")

    def test_19727_chiryareza_eight_is_chery_arrizo_8(self):
        """19727: «Чиряреза, 8емь» = Chery Arrizo 8."""
        for model in ("Чиряреза, 8емь", "Чиряреза восемь"):
            with self.subTest(model=model):
                dims = infer_sto_booking_dimensions(
                    f"Какой автомобиль? {model}. Хочу записаться на ТО."
                )
                self.assertEqual(dims.get("service_brand"), "chery_tenet")
                self.assertEqual(dims.get("work_type"), "to")

    def test_19749_mixed_tn8_is_tenet_t8(self):
        """19749: «ТN8» / «TN8» / «ТН8» = Tenet T8."""
        for model in ("ТN8", "TN8", "ТН8"):
            with self.subTest(model=model):
                dims = infer_sto_booking_dimensions(
                    f"Хотел узнать насчёт установки фаркопа на {model}."
                )
                self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_tonat_word_digit_maps_to_tenet_t8(self):
        """Тонат/Тенат + словесная цифра должны нормализоваться в Tenet T8."""
        dims = infer_sto_booking_dimensions(
            "Хотел бы записаться на ТО, автомобиль Тонат восемь."
        )
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_19845_mixed_chrri_is_chery(self):
        """19845: смешанное написание «автомобиля Chrри» = Chery."""
        for brand in ("Chrри", "Chрри", "Chри"):
            with self.subTest(brand=brand):
                dims = infer_sto_booking_dimensions(
                    f"Антикоррозийная обработка днища автомобиля {brand}. "
                    "Какой автомобиль у вас? Семь Эль."
                )
                self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_14140_t04_tenet_not_nissan_location(self):
        """14140: «Автомобиль Т04» / Tenet T4 — chery_tenet; «раньше здесь был Nissan» — не марка авто."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Андреев Юли. Слушаю ва. "
            "Девушка подскажите, можно записаться на техобслуживание? "
            "Автомобиль Т04. Какое ТО необходимо? Робото. "
            "Так, те Т4 на роботе. Tenet Т4, светло-серого цвета, 2025-го года выпуска. "
            "Вот. Раньше здесь был Nissan. Всё, хорошо, тридцатого в 11:00."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        for model in ("Т04", "T04", "t04"):
            dims_m = infer_sto_booking_dimensions(f"автомобиль {model} запись на первое то")
            self.assertEqual(
                dims_m.get("service_brand"),
                "chery_tenet",
                msg=f"expected chery_tenet for model={model!r}",
            )

    def test_14158_nissan_terrano_not_chery_four_years(self):
        """14158: «У меня Nissan, Ниссан Тирана» — nissan; «чери четыре года» — интервал, не марка."""
        t = (
            "Викинги Чери, ассистент сервиса Андреева Юля. Слушаю вас. "
            "Я бы хотел на техобслуживание записаться. "
            "Ранее к там заезжали, обслуживались у вас? Да. "
            "Фамилию собственника подскажите. Федотов. "
            "У меня Ниssan. Ниссан Тирана, госномер 981. "
            "на Nissan Fiac, на Nessan Ferrana, на двухлитровом двигателе, регламент ТО. "
            "следующий раз будет штатно чери четыре года. "
            "может чери четыре года тут ситуация улучшится."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertEqual((dims.get("evidence") or {}).get("brand_zone"), "client_ownership")

    def test_14017_fourth_to_booking_not_deferred_spark_repair(self):
        """14017: запись на 4-е ТО после CRM-истории; свечи/вариатор — состав визита, не deferred repair."""
        t = (
            "Чери Викинги сервис Андреева Юлия слушаю вас. "
            "Девушка, хотела узнать, записаться на ТО. "
            "Ранее к там заезжали, обслуживались? Да. Федотова Дарья. "
            "Nissan Кашкай, автомобиль 2021 года. Пробег 67. "
            "на ТО заезжали 14 ноября 2024, было третье техническое обслуживание. "
            "тогда получается четвёртое ТО шестидесятых будет. "
            "меняется масло в двигателе, фильтры, тормозная жидкость и свечи зажигания. "
            "свечи можем сделать контроль состояния, отложить замену. "
            "ТО без свечей 15 500. замена масла в вариаторе 29 100, можем попозже. "
            "записали на сегодня на 15:30."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertEqual(dims.get("service_brand"), "nissan")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_18144_tehobsluzhivanie_zapisat_sto_to_in(self):
        """18144: «надо машину на техобслуживание записать» + кроме ТО — СТО_ТО_вх, work_type to."""
        t = (
            "Викинги Чери, диспетчер сервис, Юлия. Здравствуйте. Здравствуйте, девушка, э-э, "
            "подскажите, пожалуйста, надо машину на техобслуживание записать. "
            "Какой автомобиль ваш, подскажите? Э-э-э Чери. Чhри Tиga 7 Про Макс. "
            "Ранее у вас обслуживался, был на сервисе? Да, он был, другой владелец был. "
            "Так, 7 Про Макс, вижу. Какой пробег Сейчас у автомобиля? — 41 где-то примерно. "
            "Так третье то сделано был в сентябре, сейчас соответственно четвертое. "
            "Кроме ТО, что-то ещё дополнительно требуется? Есть какие-то вопросы? "
            "Ну вот тут постоянно камера обзорная отрубается. "
            "Если какой-то гарантийный момент нужна будет обязательно от вас. "
            "Стоимость четвёртого ТО 28 500. Давайте запишем на 18-е число. Записали."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_18150_jetour_to_booking_refusal_work_type_to(self):
        """18150: «на ТО записаться» + отказ Jetour — НЕ_ТО, но вид работ ТО."""
        t = (
            "Д Алло. добрый день. Это компания Викинги, Ддиспетчер сервиса Юлия. "
            "Ваш телефон передали, вы звонили. Какой у вас вопрос? "
            "Мне бы на ТО записаться на следующей неделе. Какой автомобиль у вас, подскажите, пожалуйста. "
            "ДЖтур. Джетур у вас на гарантии автомобиль? Да. Смотрите, мы с 29 марта уже не являемся "
            "официальным дилером ДжиТУр. А-а, сейчас остались ближайшие дилерские центры в Самаре. "
            "К сожалению, не продлили с нами дилерское соглашение, в Тольятти закрыли. "
            "А не подскажете там телефончик? Конечно, подскажем."
        )
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        self.assertIn(ct, ("STO_IN", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(reason, ("jetour_service_refusal", "brand_service_refusal_not_narrow_to"))
        from call_analytics.sto_booking_dimensions import (
            align_sto_work_type_with_narrow_rubric,
            _normalize_text,
        )

        wt, _ = align_sto_work_type_with_narrow_rubric(
            dims.get("work_type"), rub, _normalize_text(t), evidence=dims.get("evidence")
        )
        self.assertEqual(wt, "to")

    def test_20940_jetour_limitation_with_confirmed_booking_is_sto_to_in(self):
        """20940: Jetour неофиц. дилер, но после разъяснения согласован слот — СТО_ТО_вх."""
        t = self._transcript_from_log(20940)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "to_despite_brand_service_refusal",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertNotIn(
            reason,
            ("jetour_service_refusal", "brand_service_refusal_not_narrow_to"),
        )

    def test_25922_jetour_warranty_refusal_but_confirmed_to_slot_is_sto_to_in(self):
        """25922: Jetour — отказ только по гарантии, но ТО можем провести + слот согласован → СТО_ТО_вх."""
        t = self._transcript_from_log(25922)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertNotIn(
            reason,
            ("jetour_service_refusal", "brand_service_refusal_not_narrow_to"),
        )

    def test_18680_jetour_no_official_stamp_dealer_referral_not_narrow_to(self):
        """18680: Jetour на гарантии — без отметки у нас, направили к дилеру в Самару; НЕ_ТО."""
        t = self._transcript_from_log(18680)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            ("jetour_service_refusal", "brand_service_refusal_not_narrow_to"),
        )

    def test_18349_jetour_stt_ddztur_dealer_refusal_not_to(self):
        """18349: STT Ддзтуре/Dижетуr + не офиц. дилеры → НЕ_ТО, other_brand, без записи."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "Скажите, пожалуйста, на Ддзтуре у вас ТО проходил? Так. "
            "А можете мне сказать, когда у меня следующее ТО? Давайте посмотрю. "
            "Напомните, пожалуйста, фамилию владельца автомобиля? Панкратов Андрей Юрьевич. Минутку. "
            "Так, крайний раз вы были у нас в декабре. У вас был на тот момент пробег 19 286 км, "
            "то есть, соответственно, ближе к 29 000. — Ближе к двадцати девяти тысячам. "
            "— Так делаете уже ВТО, да? — А-а, сейчас мы уже не являемся официальными дилерами Dижетуr. "
            "— А, не являетесь? — Да, в конце марта с нами не продлили дилерское соглашение. "
            "Осталось два дилерских центра в Самаре. — То есть 29 000, это до декабря мне нужно, правильно? "
            "— либо к декабрю вам, да. Так, всё, спасибо большое. Пожалуйста. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        self.assertEqual(ct, "STO_IN")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(reason, ("jetour_service_refusal", "brand_service_refusal_not_narrow_to"))

    def test_18297_jetour_no_dealership_redirect_no_booking_not_to(self):
        """18297: Jetour + нет дилерства, цена ТО, но к дилеру в Самару — НЕ_ТО, без записи."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. Добрый день. "
            "А у меня автомобиль ДJтуr у вас обслуживался, вото на ТО хотел записаться. "
            "Автомобиль гарантийный у вас? Да. У вас с 29 марта уже нет дилерства, "
            "неофициальные дилеры мы теперь Джитур. Не продлили. "
            "Не продлили с нами дилерство, к сожалению, закрыли. "
            "А где сейчас в Тольятти тогда? — В Тольятти, к сожалению, сейчас уже нет, "
            "только в Самаре два центра осталось. "
            "У вас можно техническое обслуживание пройти, но мы не сможем сделать отметку о ТО. "
            "А третье ТО сколько стоит? Вот у меня сейчас 30 000 км. Какой у вас модель? Джитур Дашинг. "
            "Какая коробка у вас? И объём двигателя. Робот. Полтора. "
            "третий ТО, здесь по стоимости будет со скидкой где-то в районе 18 600 ₽. "
            "— Ясно, хорошо. — вас телефон самарский продиктовать? Если хотите, могу продиктовать. "
            "— Ну, да, мне может эсэмэской как-то послать? Если только в Макс могу вас написать? — Да. "
            "— Большое спасибо. Всего доброго."
        )
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        self.assertEqual(ct, "STO_IN")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(reason, ("jetour_service_refusal", "brand_service_refusal_not_narrow_to"))
        from call_analytics.sto_booking_dimensions import (
            align_sto_work_type_with_narrow_rubric,
            _normalize_text,
        )

        wt, _ = align_sto_work_type_with_narrow_rubric(
            dims.get("work_type"), rub, _normalize_text(t), evidence=dims.get("evidence")
        )
        self.assertEqual(wt, "to")

    def test_18185_tehobsluzhivanie_zapisat_hoteli_sto_to_in(self):
        """18185: «техобслуживание 23. Записать хотели бы» + кроме ТО — СТО_ТО_вх."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. Добрый день. "
            "Сиri Piga 4Pr. Добрый день. Сеri Tiggo 4 Pro, Учаева Татьяна Викторовна. "
            "Аа техобслуживание 23. Записать хотели бы автомобиль? Плохо нас слышно? Да-да-да. "
            "Так, ещё раз, пожалуйста, фамилию скажите. — Учаева Татьяна. "
            "Пробег какой у автомобиля? Ну, где-то 19 800. "
            "Давайте пока спрошу, это-то кроме ТО потребуется, есть какие-то вопросы дополнительные? "
            "Вроде нет. Ну дёргается иногда при ТО. "
            "Так, я Так понимаю, это будет у нас второе ТО? — Ну да. "
            "На 21-е число, если вторник, могу предложить вам 15:00 есть запись, время. "
            "А-а, в 11:30 ещё есть пораньше. Ну, давайте 11:30. "
            "Записали на 21-е число, вторник, в 11:30."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_18191_cherritix_9_chery_tenet_parts_not_narrow_to(self):
        """18191: STT «CherritiX 9» → chery_tenet; подшипник на склад — НЕ_ТО."""
        t = (
            "А Алло? Алло, Павел Владимирович? Да. Алло, да, здравствуйте. что да, Викинги, Юлия, "
            "диспетчер сервиса. Удобно говорить там? Да, удобно. Угу. "
            "Заказывали для вашего автомобиля CherritiX 9 ступичный подшипник. Стите, в наличии, "
            "ну, то есть он поступил на склад. Юля, вы знаете, мне вот, когда забирал машину, мне ничего "
            "никто вообще не прокомментировал по поводу вот этого счёта. Он пришёл у нас, да. "
            "А я как будто бы заказывал только болт один ступичный и всё. "
            "Но как, смотрите, если что, в наличии всё есть, могли бы записать вас уже на визит. "
            "Юль, ну если там не сложно, запишите у нас. На следующей неделе командировка, "
            "а потом вот, ну, где-то, наверное, на четверг. Ну Давайте вот на пятницу лучше. "
            "Тридцае первое пятницу? Да, на тридцатье первое запишите на утро."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "other_work")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_18193_to60_after_busy_line_is_sto_to_in(self):
        """18193: линия занята у админа, затем Юлия — ТО-60, запись; СТО_ТО_вх, вид работ ТО."""
        t = (
            "Администратор салона. Оксана, добрый день! Добрый день. "
            "У вам сервис или отдел продаж? — Сервис. — тогда я вам сразу на диспетчера переключу. "
            "Так, линия пока занята, побудете на линии? "
            "вы можете контактный номер оставить Сейчас? — Да, я уже оставлял. "
            "Два дня жду, когда мне перезвонят. всё ясно, тогда ожидайте. "
            "Викинги Чери, диспетчер сервис, Юлия. Здравствуйте. "
            "Я хотел бы узнать, мне ТО надо сделать. Угу. Я звонил уже. ТО-60. "
            "Мне сказали, что перезвонят, стоимость узнать примерно. "
            "Ниссан Кашкай, который 656 госномер, правильно? — Да. "
            "Смотрите, мы с вами подбирали ТО плюс замена масла в коробке, правильно? "
            "Так, по стоимости, если всё выполняем, получается у вам на 29 600. "
            "Можете записаться, давайте спланируем. "
            "Среда, 22-е число. — 10:30. — Записать? — Да, на среду. — Да, Среда, 10:30."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertNotEqual(reason, "no_actual_reception_contact")

    def test_14019_fifth_to_booking_not_post_to_complaint(self):
        """14019: запись на 5-е ТО; «после 26-го … -то» и «не появилось» — не post_to_visit_complaint."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Андреева Юлию. "
            "можно записаться на пятое ТО. Иван. Трифонихин. "
            "Автомобиль Chery Tiggo 8 Pro Max, пробег 50. "
            "пятое ТО объёмное, меняется масло, 32 900. "
            "после 26-го какие-то нарекания? нарекания все те же, радар отключается, "
            "ничего нового не появилось. записали на 26 июня на 15:00."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_14073_eighth_to_price_with_variator_oil_sto_to_in(self):
        """14073: цена 8-го объёмного ТО + масло в вариаторе в составе — СТО_ТО_вх, не non_reg."""
        t = (
            "ассистент сервиса Андреев Юлия. "
            "сориентируйте меня по стоимости ТО. "
            "Chery Tiggo 4 Pro, турбовый. Какой необходимо сделать? 80 000. "
            "Восьмой, да, необходимо сделать. Объёмное. "
            "меняется масло двигатель, фильтры, масло полностью в вариаторе со снятием поддона. "
            "стоимость 166250. Спасибо."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_23939_to4_booking_with_short_stt_time_form_is_sto_to_in(self):
        """23939: «на 10ть» + «запишите, пожалуйста» — подтвержденная запись на ТО-4."""
        t = (
            "Официальный дилер, девушка, хотел бы узнать: четвертое ТО на Chery 7 Pro Max сколько стоит. "
            "Диспетчер сервиса Юлия. Четвертое ТО на 40 000, по стоимости 47 900. "
            "А как записаться, на какое число можно? На четвертое число, там свободно. "
            "Любое время свободно. На 10ть. Да, можем записать вас. "
            "Запишите, пожалуйста. ТО 4. Накануне позвоним и напомним."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_21921_direct_to_cost_question_without_booking_is_not_narrow_to(self):
        """21921: «шестое ТО. Мне узнать стоимость?» без слота/времени/оформления — НЕ_ТО."""
        t = (
            "Викинги Чери сервис, слушаю вас. "
            "Скажите, пожалуйста, Chery 8 Pro Max, шестое ТО. Мне узнать стоимость? "
            "Шестое ТО, пробег 60 000 уже, да, подошел? "
            "ТО объемное, меняется масло и фильтры, свечи, масло в коробке, "
            "по стоимости 61 100, по времени 3-4 часа. "
            "Меняется ли охлаждающая жидкость или это дополнительно? "
            "В регламент не входит. Всё, спасибо, до свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "direct_to_cost_question_without_booking_not_narrow_to")

    def test_22155_preparatory_to_price_quote_without_booking_is_not_narrow_to(self):
        """22155: только цена ТО «заранее, чтобы подготовиться», без записи — НЕ_ТО."""
        t = self._transcript_from_log(22155)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "to_price_quote")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "preparatory_to_price_quote_without_booking_not_narrow_to")

    def test_14091_abs_diagnostic_crm_past_to_not_work_type_to(self):
        """14091: диагностика АБС; CRM «недавно проводили ТО в анаавто» — не work_type to."""
        t = (
            "ассистент сервиса слушаю. хотела записаться на диагностику. "
            "Chery Tiggo 9. что случилось? горит датчик абс уже второй день. "
            "недавно проводили техническое обслуживание в анаавто да. "
            "предварительная диагностика полтора-два часа. "
            "на завтра на 12:30 есть время."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_intent"), "diagnostics")
        self.assertEqual(ev.get("work_hit"), "diagnostics")

    def test_21825_past_to_phrase_with_noise_complaint_is_diagnostics_not_narrow_to(self):
        """21825: «в мае ТО проходил» + бряцание/шум и запись на проверку — диагностика, НЕ_ТО."""
        t = (
            "Викинги Чери, ассистент сервиса Андреева Юлия. "
            "Я в мае ТО проходил. "
            "Когда заезжаю в гараж, сзади как будто консервных банок понавешали, брязгает. "
            "Вот как бы что проверить или записаться, как заехать к вам на что посмотреть. "
            "Ближайшее есть на 13-е число, четверг, время 15:30. "
            "Давайте на тринадцатое. Всё, вас записали."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        self.assertTrue(dims.get("is_booking"))
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_intent"), "diagnostics")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "primary_defect_diagnostic_not_narrow_to")

    def test_21844_water_in_tail_lamp_is_diagnostics_not_narrow_to(self):
        """21844: вода/конденсат в фонаре + «подъехать то показать» — диагностика, НЕ_ТО."""
        t = (
            "Викинги Чери, ассистент сервиса Юлия. "
            "Автомобиль на гарантии, в заднем фонаре всё время вода, конденсат не уходит. "
            "Так мне подъехать, то показать? Это нужно будет записываться. "
            "Сейчас свет включили, пока не могу подсказать дату, подберем день и время позже."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_intent"), "diagnostics")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "primary_defect_diagnostic_not_narrow_to")

    def test_14087_body_shop_repair_status_not_work_type_to(self):
        """14087: статус кузовного ремонта после аварии — кузовной цех, не «ТО» из «а то»."""
        t = (
            "Викинги Чери сервис Андреева Юлия слушаю вас. "
            "на ремонт когда забрать? Ремонт после аварии. "
            "Сейчас на кузовной цех вас переведу. "
            "детали должны были прийти числа девятнадцатого, никто не звонит. "
            "А то нам приблизительно сказали. мастер-приёмщик свяжется."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "body_shop")
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_post_to_visit_complaint_still_not_narrow_to(self):
        """Реальная жалоба после прошлого ТО — по-прежнему НЕ_ТО."""
        t = (
            "ассистент сервиса слушаю. в пятницу то делал у вас. "
            "теперь машину тянет вправо, развал-схождение нужно проверить."
        )
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "post_to_visit_complaint_not_narrow_to")

    def test_19375_missing_engine_cover_after_to_not_narrow(self):
        """19375: после ТО не вернули защиту/накладку под капотом — претензия, НЕ_ТО."""
        t = (
            "Викинги Чери, ассистент сервиса Юлия, слушаю вас. "
            "Я машину загоняла, и мне в машине кое-что не доставили, "
            "то есть на техническое обслуживание. "
            "21-го числа заезжали на техническое обслуживание. "
            "Что они там не положили? Когда капот открываешь, этой защиты нету. "
            "Куда она делась? Почему её обратно не поставили? "
            "Я передам ваш вопрос мастеру-приёмщику, с вами свяжутся."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "post_to_visit_complaint_not_narrow_to")

    def test_16969_post_to_quality_smell_complaint_not_narrow(self):
        """16969: после первого ТО — вонь/некачественные фильтры; повторный осмотр — НЕ_ТО, quality_check."""
        t = (
            "Викинги чери, диспетчер сервиса. Юлия. Здравствуйте. "
            "Вы знаете, мы второго числа у вас делали ТО первое. "
            "И после ТО, я не знаю, фильтра некачественные или что там некачественное, "
            "все газы, вся вонь, вся в салоне. Не устраивает такое качество. "
            "Фамилию владельца? Берков. ТНТ7, 944. "
            "Вас записать снова к нам? Пусть проверяют. "
            "Мастер-приёмщик завтра. Завтра в 16:30 подъезжайте."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "quality_check")
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "post_to_visit_complaint_not_narrow_to")

    def test_26481_claim_after_recent_to_visit_is_not_narrow_to(self):
        """26481: претензия после недавнего ТО + повторная запись на диагностику — НЕ_ТО."""
        t = self._transcript_from_log(26481)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "post_to_visit_complaint_not_narrow_to")

    def test_26487_complaint_after_zero_to_is_not_narrow_to(self):
        """26487: жалоба после нулевого ТО (звуки/грохот) + запись к мастеру — НЕ_ТО."""
        t = self._transcript_from_log(26487)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "post_to_visit_complaint_not_narrow_to")

    def test_26507_new_to_then_tail_complaint_stays_sto_to_in(self):
        """26507: сначала новое ТО (цена/состав), потом жалоба на прошлое ТО — СТО_ТО_вх."""
        t = self._transcript_from_log(26507)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_14040_inbound_fifth_to_price_not_sto_out(self):
        """14040: вх. «слушаю вас» + STT «Андреев, Юли» + цена 5-го ТО — STO_IN, не STO_OUT."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Андреев, Юли, слушаю вас. "
            "Здравствуйте. Алло, здравствуйте, девушка. Здравствуйте. "
            "На Cherry 7 Pro, пятое ТО. Сколько будет стоить? "
            "7Pro. На вариаторе, да, получается автомобиль у вас? Да. "
            "Пятое ТО, пробег 50 000 подошёл, да? "
            "По стоимости со снятием допзащиты 19 760. Спасибо большое. "
            "Во сколько записываться у вас там как вообще?"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"), msg=f"expected STO_IN, got {(dept, ct)}")
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_14039_outbound_during_to_lamp_not_sto_to_out(self):
        """14039: исх. во время ТО — лампа стоп-сигнала, согласование допработ — НЕ_ТО."""
        t = (
            "Алло, Александра Геннадьевна, добрый день. Это компания Викинги, "
            "мастер-приёмщик Александр. В процессе технического обслуживания обнаружили, "
            "что лампочка перегоревшая, лампы стоп-сигнала задние-правые. "
            "Рекомендуем поменять лампы. Стоимость лампы за две штуки 280 рублей "
            "и замена 1400. Меняем? Ну да. Хорошо, спасибо большое."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_12021_outbound_zero_to_invite_not_sto_in(self):
        """12021: «Алло, И.О.? … удобно говорить» + приглашение на нулевое ТО — STO_OUT."""
        t = (
            "Алло, Алексей Игоревич? Добрый день. Дилерский центр Викинги, Юлия, диспетчер сервиса. "
            "Удобно говорить вам? Да, слушаю. Звоним по автомобилю вашему Тенет-7, приобретали. "
            "необходимо пройти нулевое техническое обслуживание, хотим вам пригласить записаться. "
            "давайте на 9:30. На нулевом ТО идёт замена масла."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_16497_perezanivayu_na_to_hoteli_sto_to_out(self):
        """16497: «перезваниваю на ТО хотели» + запись на слот — СТО_ТО_исх, не вх."""
        t = self._transcript_from_log(16497)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_16501_crm_name_udobno_zayavka_sto_to_out(self):
        """16501: «Алло, И.О., добрый день» + удобно говорить + заявка на ТО — СТО_ТО_исх."""
        t = self._transcript_from_log(16501)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_16555_zvonili_to_consult_hoteli_sto_to_out(self):
        """16555: «звонили по ТО проконсультироваться хотели» — СТО_ТО_исх, не вх."""
        t = self._transcript_from_log(16555)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_sto_opening_role_outbound_vs_inbound_forms(self):
        """opening_role: набор клиенту → outbound_dial; приём линии → inbound_reception."""
        from call_analytics.classify_by_transcript import _sto_opening_role

        out_t = (
            "Алло, Сергей Викторович, добрый день. Это Чери Викинги, диспетчер сервиса Юлия. "
            "Удобно говорить вам? Вы оставляли заявку на техническое обслуживание."
        )
        in_t = (
            "Чери Викинги на Заставной, ассистент сервиса Андреева Юлия, слушаю вас. "
            "Здравствуйте. Хочу записаться на ТО."
        )
        self.assertEqual(_sto_opening_role(out_t), "outbound_dial")
        self.assertEqual(_sto_opening_role(in_t), "inbound_reception")

    def test_sto_outbound_variant2_env_rollback_switch(self):
        """VIKINGI_STO_OUTBOUND_VARIANT=2 включает legacy _is_outbound_variant2_legacy."""
        import os
        from call_analytics import classify_by_transcript as cbt

        self.assertEqual(cbt._sto_outbound_logic_variant(), 1)
        os.environ["VIKINGI_STO_OUTBOUND_VARIANT"] = "2"
        try:
            self.assertEqual(cbt._sto_outbound_logic_variant(), 2)
            t = (
                "Алло, Сергей Викторович, добрый день. Это Чери Викинги, диспетчер сервиса Юлия. "
                "Удобно говорить вам?"
            )
            # Оба варианта должны видеть исходящий на явном наборе.
            self.assertTrue(cbt._is_outbound_variant2_legacy(t))
            self.assertTrue(cbt._is_outbound(t))
        finally:
            os.environ.pop("VIKINGI_STO_OUTBOUND_VARIANT", None)
        self.assertEqual(cbt._sto_outbound_logic_variant(), 1)

    def test_12032_third_to_booking_sto_to_in(self):
        """12032: «мне ТО надо третий проходить» + запись на слот — СТО_ТО_вх."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия, здравствуйте. "
            "мне ТО надо уже третий проходить, пробег уже 28. "
            "предыдущее ТО выполняли в сентябре, пробег был 19 880 км. "
            "29 800 там нужно будет сделать ТО. "
            "давайте заранее запишемся, на девятнадцатое запишем. "
            "приедете на ТО к девятнадцатого числа? "
            "ТО у вас будет около трёх с половиной часов. "
            "на три тогда, на 15:00. Да, всё, записали вас."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_14644_tenet_t4_stt_brand_chery_tenet(self):
        """14644: «Тэнет сергу» / «4тыре-4ре» / «наЦеН 107» — Chery/Tenet."""
        t = (
            "Подскажите цены на первое ТО наЦеН 107. "
            "Тэнет сергу No 944, автомобиль 2025 года. 4тыре-4ре."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_14677_chrtig_7l_stt_brand_chery_tenet(self):
        """14677: STT «Чртиг 7L» — Chery/Tenet."""
        t = "Какой автомобиль у вас? Семель. Так, Чртиг 7L."
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_14683_g4rogus_stt_brand_chery_tenet(self):
        """14683: STT «Г4рогус» — Chery Tiggo 4 Pro."""
        t = "Г4рогус номер 527. На механике, 24 года выпуска."
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_14801_ct_4rogy_chery_tiggo_4_pro(self):
        """14801: STT «CT 4rogу» — Chery Tiggo 4 Pro, chery_tenet."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Андреева Юлия. "
            "Автомобиль CT 4rogу, No 758, 24 года выпуска. На вариаторе. "
            "Записаться на диагностику, потом на ремонт."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_14931_chrtg_4_proga_chery_tiggo_4_pro(self):
        """14931: STT «ЧrTG 4 прога» — Chery Tiggo 4 Pro, chery_tenet."""
        t = (
            "Компания Викинги, Юлия, диспетчер. "
            "Я звоню по поводу вашего автомобиля ЧrTG 4 прога с номера 697. "
            "Замена звукового сигнала, запись на 1 июля."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_14919_subaru_forester_other_brand_not_chery(self):
        """14919: Subaru Forester — other_brand, не «чhр» из приветствия Чhri."""
        t = (
            "Официальный дилер Chant, администратор Диана. "
            "Subaro Forest, проблемы с кондиционером. "
            "Викинги Чhri, ассистент сервиса Андреева Юлия. "
            "Subaru Forestr, кондиционер, дозаправить. "
            "Subar For, госномер 530. Запись на 3 июля."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")

    def test_19284_skoda_not_messenger_promax_as_chery(self):
        """19284: «Шкода» + «скинуть на промакс» (мессенджер) — other_brand, не Chery Pro Max."""
        t = (
            "Викинги Чери на Заставной, ассистент сервиса, слушаю вас. "
            "Здравствуйте, хотел бы записаться. С каким автомобилем подскажите пожалуйста? "
            "Шкода. А по какой причине развал-схождение хотели бы сделать. "
            "Нужно будет внести в заявку автомобиля вин номер автомобиля госномер СТС "
            "если есть возможность может быть куда-то отправить скинуть вас можно на промакс "
            "на промакс телефон какой."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertEqual((dims.get("evidence") or {}).get("brand_hit"), "шкода")

    def test_20987_tiggo8_pro_max_overrides_noisy_jetour_fragment(self):
        """20987: «автомобиль 8 Про Макс» в зоне клиента важнее шумового STT «Жтур»."""
        t = self._transcript_from_log(20987)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "diagnostics")
        self.assertTrue(dims.get("is_booking"))
        self.assertIn(
            (dims.get("evidence") or {}).get("brand_hit"),
            ("промакс", "pro max", "8 pro max"),
        )

    def test_20987_rubric_reason_not_jetour_when_client_brand_is_chery(self):
        """20987: при бренде клиента Chery/Tenet причина не должна быть jetour_not_narrow_to."""
        t = self._transcript_from_log(20987)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertNotEqual(reason, "jetour_not_narrow_to")

    def test_21405_8_pro_mmaaks_maps_to_chery_tenet(self):
        """21405: STT «8 Про Ммаакс» — марка должна определяться как Chery/Tenet."""
        t = self._transcript_from_log(21405)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_14639_seriariz_8_stt_brand_chery_tenet(self):
        """14639: STT «Серияриз 8» — Chery Arrizo 8."""
        t = "Какой автомобиль? Серияриз 8."
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_14744_chertelepermax_stt_brand_chery_tenet(self):
        """14744: STT «CherTeleperMax» — Chery Tiggo 8 Pro Max."""
        t = "Автомобиль CherTeleperMax чёрного цвета, госномер 87 203 года выпуска."
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_14320_tenet_t4pro_brand_chery_tenet_not_other(self):
        """14320: «Tenet T4Pr» при запросе стoimosti 4-го ТО — марка Chery/Tenet, не Прочие."""
        t = (
            "Викинги Чери, ассистент сервиса Андреева Юлия, слушаю вас. Здравствуйте. "
            "я из Самары вам звоню. Вы меня не сориентируете по стоимости ТОО Тенет Т4Pr четвёртое? "
            "Двигатель какой у вас? Турбовый, нетурбовый. 1,5 Экшoн. "
            "На вариаторе, да, автомобиль у вас? Да. "
            "Четвёртый ТО объёмный. стоимость то 45 700 выходит. Спасибо."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertIn((dims.get("evidence") or {}).get("brand_hit"), ("тенет", "tenet t4 pro", "tenet t4"))

    def test_14237_radiator_fan_repair_quote_existing_to_not_narrow_to(self):
        """14237: смета замены вентилятора радиатора + уже записан на ТО — НЕ_ТО, марка Nissan."""
        t = (
            "Викинги Челтьнсервис Андреева Юлия, слушаю вас. Здравствуйте. "
            "меня Даниил зовут. скажите, пожалуйста, вот по стоимости работ можете меня сориентировать? "
            "Какой автомобиль? Какие работы хотели бы сделать? "
            "Да, автомобиль N Nessan Potfind 015- года, R52, который? "
            "Замена вентилятора радиатора. "
            "То есть вентилятор охлаждения двигателя вы имеете в виду? Ну да, вентилятор. "
            "Работа, запчасти я уже купил. "
            "Алло, это снова Юлия, диспетчер. Спасибо за ожидание. "
            "Процесс непростой, то есть он нелегко там снимается, чтобы его заменить. "
            "По стоимости получается 13 800 по времени около четырёх с половиной часов занимает. "
            "Да, я просто на 25-е на ТО записан, просто думал, как бы совместить. "
            "То есть мне отдельно надо записываться? "
            "Можем тридцатого вас пригласить всё вместе и ТО сделать, и заменить вот этот вентилятор. "
            "Дайте мне время подумать, я перезвоню. Спасибо."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertEqual(dims.get("work_type"), "other_work")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_14222_wrong_department_lada_redirect_not_narrow_to(self):
        """14222: «записаться на ТО» + Лада Веста → перенаправление на Викинги Лада 63 0077."""
        t = (
            "Викинги Чери Сервис Андреева Юлия, слушаю вас. Здравствуйте. "
            "хотелось бы записаться на ТО. "
            "К вас по имени как можно обращаться? Александр. "
            "Ранее к нам заезжали, обслуживались у нас? Да. "
            "Фамилию собственника подскажите. Ровняков. Нет такого. "
            "Машина какая у вас? Веста. Лада Веста. "
            "Вы позвонили на Викингам на Заставну, мы обслуживаем автомобили Чери и Тоннет. "
            "Если вас нужно Викинги Лада, у них телефон 63 0077. "
            "63.00.00:77. Да. Или 63.00:50. Да. Спасибо."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_22071_wrong_number_redirect_other_dealer_not_narrow_to(self):
        """22071: клиент ошибся номером (Toyota), дали телефон другого дилера — НЕ_ТО."""
        t = (
            "Викинги Чирри, ассистент сервиса Андреева Юлия, слушаю вас. Здравствуйте. "
            "Здравствуйте, А мне т как это — Там мне Toyoту, как это на ТО, чтобы записать? "
            "Toyota? — Д, это не туда попал? "
            "Вы позвонили на Заставную 3, Викинги, обслуживаем автомобиля Chery, Nessan. "
            "А, если Тона. Если ТонаАв, то они находятся на Воскресенской. "
            "А у вас нет телефона? Телефон. сейчас одну минуточку, кто подскажет телефон? Сейчас секу. "
            "Телефон у них 94 30 33. Ага, спасибо. Пожалуйста."
        )
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "wrong_number_redirect_other_dealer_not_narrow_to")

    def test_14346_jetour_to_history_crm_not_narrow_to(self):
        """14346: Jetour — CRM «сколько ТО делали», по базе; перезвон — НЕ_ТО, не СТО_ТО_вх."""
        t = (
            "Викинги Чери Сервис Андреева Юлия, слушаю вас. "
            "меня Екатерина зовут. мы у вас раньше обслуживались, когда могли тут ТО делать по Джитуру. "
            "можно по базе посмотреть, сколько нам ТО делали? "
            "Jetour Dashing, компания ЕСПТранс. "
            "какое оно по счёту было? "
            "девятого февраля к нам заезжали на техническое обслуживание, "
            "это было техническое обслуживание, последнее из ТО. "
            "было пробег 72 787 км, было седьмое ТО. "
            "следующее ТО должно быть пробег 82 000. восьмое уже нужно вам проводить. "
            "девятый ТО — топливный насос по регламенту менять. "
            "надo ли нам его менять, если проблем нет? "
            "топливного насоса в наличии нет. "
            "можем провести восьмое ТО, но отметки в единой базе не будет. "
            "ладно, по топливному насосу узнаю и перезвоню. спасибо."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_intent"), "crm_to_history_consultation")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            ("to_history_crm_consultation_not_narrow_to", "past_to_followup_without_new_to_booking"),
        )

    def test_16976_last_to_mileage_history_not_narrow(self):
        """16976: «последний раз ТО», что меняли, пробег — история; смета масла — НЕ_ТО, не запись на ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "Подскажите, пожалуйста, я бы хотела узнать, тогда делали последний раз ТО? "
            "И ну что, может меняли, на каком пробеге? "
            "Фамилию владельца? Тимофеев Вячеслав Андреевич. "
            "Nissan Мурана, правильно? Да. 490 госномер. "
            "Был буквально в прошлом месяце, 18-го числа. "
            "Там именно техническое обслуживание, да? Да, вот, мне надо узнать, тогда масла меняли. "
            "Если замена масла, то крайний раз была она ровно год назад, 25 июня. "
            "Замена масла в двигателе, фильтр масляный, фильтр салонный. "
            "А можете посчитать, сколько будет стоить масла и фильтр? Да, конечно. "
            "Хорошо, тогда посчитаем и перезвоню."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "crm_to_history_consultation")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            ("to_history_crm_consultation_not_narrow_to", "past_to_followup_without_new_to_booking"),
        )

    def test_12025_diagnostic_followup_not_narrow_to(self):
        """12025: «это необходимо записать … для продолжения диагностики» — не СТО_ТО."""
        t = (
            "Михинги Чери диспетчер сервиса Юлия, здравствуйте. "
            "в понедельник приезжал к вам с проблемой на новой машине. "
            "Мастерприёмщик обещал во вторник перезвонить. Сегодня четверг, никто не звонит. "
            "А автомобиль у вам не на сервисе? Вы забрали автомобиль? Да. "
            "передали вам информацию, это необходимо записать вам на 2 ч к механику "
            "для продолжения диагностики, запланировать время. "
            "на завтра есть время, либо пятница на 10 часов."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_11927_inbound_to_booking_stays_sto_in_after_12021_guard(self):
        """11927: регрессия — входящая запись на ТО остаётся STO_IN после guard 12021."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. Девушка, здравствуйте. "
            "мне бы на ТО записаться. Arrizo 8. "
            "От юрлица вы хотели бы оплатить? Да. записали вас."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))

    def test_12658_employee_contact_correlative_to_not_narrow_to(self):
        """12658: «то на диспетчера, то на кого-то» — союз, не СТО_ТО_вх."""
        t = (
            "Администратор. Саллон. Оксана, добрый день. Казакова Евгения надо позвонить. "
            "На диспетчера вас переключил. мне именно с ним надо поговорить. "
            "Викинги, диспетчер сервиса Юлия. Как мне с Казаковым Евгением поговорить? "
            "Он сегодня не работает, будет двенадцатого. перезвоните двенадцатого. "
            "я попадаю, то на диспетчера, то или ещё на кого-то? "
            "рабочая смена у него теперь двенадцатого числа."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_12687_to_cancel_reschedule_not_narrow_to(self):
        """12687: отмена/перенос записи на техобслуживание — НЕ_ТО."""
        t = (
            "Администратор салона. по поводу отмены на техобслуживание. "
            "Переключу вас на диспетчера. Викинги, диспетчер сервиса Юлия. "
            "Я вот по поводу техобслуживания. Я записывался на 10, но у нас с аккумулятором проблема была. "
            "Вы вашу запись перенесли на 12е июня на 9:30. "
            "одиннадцатого числа с вами ещё свяжемся, напомним вам о записи."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertFalse(infer_sto_appointment_agreed(t, dims))

    def test_12788_outbound_crm_name_call_convenience_sto_out(self):
        """12788: «Алло. И.О.? … диспетчер … удобно говорить?» + приглашение на 2-е ТО — STO_OUT."""
        t = (
            "Д. это вам опять? Алло. Юрий Николаевич? Добрый день! Да. "
            "Дилерский центр Викинги, Юлия, диспетчер сервис. вам удобно говорить? "
            "Да-да-да, говорите. По вашему автомобиль звоним, ЧереГ4 Про, видим, это ТО подходит по времени. "
            "17 июня в прошлом году выполняли. можем записаться сейчас вам? "
            "на 17-е число. это входит во второе ТО? Да. на 17-е число тогда, 14:30 вас записываем."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_13631_outbound_crm_lead_chereg4ro_sto_to_out(self):
        """13631: «заявку получили» + ЧереГ4ро + на ТО хотите записаться — STO_TO_OUT, chery_tenet."""
        t = (
            "Алло. Валерий, добрый день, компания Викинги. Меня зовут Юлия. Удобно разговаривать вам? Да. "
            "Мы заявку получили по автомобилю ЧереГ4ро. Госномер 512 на ТО хотите к нам записаться? Да. "
            "запись уже возможна только на следующую неделю. на 15:30. "
            "Давайте 23-го, вторник. Всё, хорошо, вас записали. "
            "Накануне звонок от нас ожидайте, мы напомним вам о записи."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_23052_cherr_aariiza8_outbound_to_booking_is_sto_to_out(self):
        """23052: марка/модель из CRM-заявки — третий маркер для СТО_ТО_исх."""
        t = (
            "Алло. Это дилерский центр Викинги, Юлия, диспетчер сервиса. "
            "Мы получили заявку на техническое обслуживание Cherr АAриiзa 8, номер 979. "
            "Ближайшая запись на 26-е число. Давайте на среду на 11:00. "
            "Все, 26-го в среду в 11:00 записали."
        )
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, "STO_OUT", dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_23008_outbound_to2_booking_prevails_over_diagnostic_context(self):
        """23008: при согласованной записи на ТО-2 диагностический фон не должен уводить из СТО_ТО_исх."""
        t = (
            "Алло, Михаил, добрый день, компания Викинги, Юлия, диспетчер. "
            "Мы получили заявку по записи вашего автомобиля Чрег 7 Промакс, госномер 522. "
            "Автомобиль доставляли на эвакуаторе, в пятницу будут делать. "
            "На следующей неделе подходит срок проведения ТО, хотелось бы провести ТО-2. "
            "На 25-е в 10:00 занято, давайте 27-е. "
            "Тогда 27 августа, время 10:00 вам записала, накануне напомним о записи."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_23195_to_priority_over_arrived_part_followup_is_sto_to_out(self):
        """23195: при подтвержденной записи на ТО пришедшая запчасть не должна выбивать из СТО_ТО_исх."""
        t = self._transcript_from_log(23195)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_22972_outbound_to8_booking_with_fuel_phrase_is_sto_to_out(self):
        """22972: CRM-запись на ТО-8 с новой датой/временем не должна уходить в AC по слову «заправках»."""
        t = (
            "Антон, добрый день, компания Викинги, Юлия, диспетчер. Удобно разговаривать? Да. "
            "Заявку получили по вашему автомобилю, необходимо сделать техническое обслуживание ТО восьмое, "
            "плюс указано: горит ошибка чек и не работает ходовой огонь с правой стороны. "
            "Ошибка 404 по катализатору, плохим бензином не пользовался, на случайных заправках не заправляюсь. "
            "Смотрите, ТО-8 самое объемное, по времени порядка четырех часов, плюс диагностика. "
            "Есть время со следующей недели, можем на 25-е число. Давайте вторник. "
            "На 10:30 или можно с утра. Тогда 25 августа, время 8:30, вас записали."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_15657_inbound_reception_crm_tiggo8_to8_sto_to_in(self):
        """15657: «слушаю вас» + пропущенный + заявка на 8-е ТО — СТО_ТО_вх (форма приёма линии)."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса, Андреева Юлия, слушаю вас. "
            "Здравствуйте. Вы мне звонили, пропущенный. "
            "Так, Ильнур Дамирович, да, мы вашу заявку получили по автомобилю Чеrтиg 8, "
            "госномер 768 на ТО, да, к нам хотите записаться? Да-да, да. "
            "Так, сейчас ещё историю открою. Да, у вас получается восьмое техническое обслуживание. "
            "на ТО-8-е вас нужно сделать скидку 10% на работу и 10% на запчасти. "
            "7 июля на 11:00 вас записали. Накануне напомним о записи."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_15611_outbound_crm_na_to_neobhodimo_zapisat_sto_to_out(self):
        """15611: «На ТО необходимо вас записать» + нарекания — СТО_ТО_исх."""
        t = (
            "Денис, добрый день, компания Викинги. Меня зовут Юлия. Удобно разговаривать? Да. "
            "Мы заявку получили по автомобилю Ч4 руг, номер 965. "
            "На ТО необходимо вас записать. Также есть нарекания по автомобилю. "
            "заедает ручка водительской двери. "
            "суббота, 11 июля, время 11:00, вас записали. Накануне напомним о записи."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_15608_outbound_crm_vy_go_to_plus_complaints_sto_to_out(self):
        """15608: STT «Вы го тоже будете делать» + камера/стук — СТО_ТО_исх."""
        t = (
            "Алло, Александр Михайлович, добрый день, компания Викинги. Меня зовут Юлия. "
            "Удобно разговаривать вам? Да. Мы получили заявку по автомобилю Ч7 ПроМакс, госномер 611. "
            "Не совсем понятно, Вы го тоже будете делать или нет? "
            "Или только по нареканиям по камере и по стуку сзади? "
            "Хмм, я думаю, да. Ну, там у нас почти год прошёл уже. "
            "в прошлом году в августе проводили ТО. Это будет диагностика по камере. "
            "9 июля, время в 8:30 нас записали. Накануне напомним о записи."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_15843_inbound_second_to_tiggo7_pro_max_warranty_side_sto_to_in(self):
        """15843: «по пробегу подходит второе ТО», Tiggo 7 Pro Max + гарантия — СТО_ТО_вх."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "по пробегу подходит второе Т ТО,г Чери тигго 7 Промакс. Хотелось бы записаться. "
            "Давайте запишем. декоративная накладка зеркала отпадает, сигнализация то пикает. "
            "это гарантия будет? инженер по гарантии смотрит. "
            "по стоимости 20 700. записали на девятое июля к 19:00."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_15856_outbound_crm_tech_service_booking_stamp_discussion_sto_to_out(self):
        """15856: CRM «на техобслуживание записать» + отметка в книжке — СТО_ТО_исх."""
        t = (
            "Алло, Виталий, добрый день. Викинги Черри, диспетчер сервиса Юлия. Удобно говорить? Да. "
            "Получили заявку по вашему автомобилю Чhри ТИg 7 Про Макс, госномер 647. "
            "На техническое обслуживание хотели бы записать автомобиль? Да. "
            "первый прошел такие же. не сможем поставить отметку о техническом обслуживании в книжке. "
            "Давайте предварительно запишем на десятое июля в 10:30. перезвоню завтра."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_15770_zayavka_prishla_na_to_automobil_zapisat_sto_to_out(self):
        """15770: «заявка пришла на ТО» + записать автомобиль + дворники — СТО_ТО_исх."""
        t = (
            "Алло, Нина, здравствуйте. Компания Викинги, Юлия, диспетчер сервиса. Удобно говорить? Да. "
            "По вашему автомобилю звоню Чери Г 4 НИО. Заявка пришла на ТО. "
            "хотели бы, да, автомобиль записать, планировали? Да-да. "
            "Кроме ТО ещё есть какие-то вопросы или только ТО выполняем? ТО. "
            "Можно дворники заменить? 8-го числа в 10:30 записали. Накануне напомним о визите."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_15798_repeat_price_quote_delivered_not_narrow_to(self):
        """15798: «ещё раз здравствуйте», по стоимости звоню, посчитали — НЕ_ТО."""
        t = (
            "Алло. Анатолий Павлович, ещё раз здравствуйте. Викинги Юлия, диспетчер сервиса "
            "по стоимости звоню. Смотрите, посчитали всё. По стоимости получается 12 533. "
            "Стоимость ТО. Та в наличии всё есть, всё для вас зарезервировали на 17-е число. "
            "Накануне позвоним."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "outbound_to_quote_continuation_not_narrow_to")

    def test_15940_44_4_proga_crm_to_booking_chery_tenet(self):
        """15940: STT «44—4 прога» — Chery Tiggo 4 Pro, CRM-запись на ТО."""
        t = (
            "Нина Михайловна, добрый день, компания Викинги. Меня зовут Юлия. "
            "Удобно разговаривать вас? Да да. Я звоню по поводу автомобиля. "
            "Нам заявка пришла, автомобиль 44—4 прога с номера 623. "
            "На второе ТО необходимо вас записать. Ближайшее: 8 июля, на 8:00 или на 3 часа дня."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_15432_che_g7_eligus_chery_tenet(self):
        """15432: STT «Че Г7 Элигус» — Chery Tiggo 7 L, запись на первое ТО."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Андреева, слушаю вас. "
            "Записаться на ТО. Запись со следующей недели с 7-го числа. "
            "Давайте на шестое, на пять часов. Марка подскажите? "
            "Че Г7 Элигус, 116, 2025 года выпуска. Первое ТО. "
            "Понедельник 6 июля, 5 часов вечера, вас записали."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_15437_cancel_fresh_to_booking_not_narrow_to(self):
        """15437: отмена только что созданной записи на ТО — НЕ_ТО."""
        t = (
            "Викинги сервис Андреева, слушаю вас. Здравствуйте. "
            "Я только что буквально минут пять назад записался на четвёртое техобслуживание. "
            "Че Тг 4r 133. На седьмое число я хотел бы отменить, потому что у меня не получится. "
            "Всё, хорошо, тогда отменили запись."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "existing_to_cancellation_not_narrow_to")

    def test_18161_manager_not_approved_to_visit_refusal_not_narrow_to(self):
        """18161: «менеджер не одобрил приезд на ТО, поэтому не приеду» — НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервис. Юлия. здравствуйте. Юлия, здравствуйте. "
            "Звонили мне три раза, я не мог ответить. Ага. В записи сегодня автомобиль, поэтому, "
            "наверное, звонили. что, скорее всего, мастер-приёмщик звонил. Слушаю. "
            "Да,ээ менеджер не одобрил приезд на ТО, поэтому не приеду. — На ТО, поэтому не приеду. "
            "— Почему не одобрил? — Не объясняют они. — Тиг 4ро, правильно? 387? — Да-да-да. "
            "Ладно, уточним. — Они сказали только в субботу-воскресенье. — Или на буднях вечерм. "
            "— всё. Мы написали им, что на вечер мы не можем записать. "
            "На вечер-то точно мы нас не можем записать, что нереально.то ТО сделать вечером. "
            "— Да, я понимаю, у меня помимо ТО, там ещё с задними колёсами.. "
            "ещё раз напишите, поэтому что, ну вот мне сегодня вот надо было на ТО ехать. "
            "Хорошо, я попробую продублировать. Спасибо."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "existing_to_cancellation_not_narrow_to",
                "existing_visit_clarification_not_narrow_to",
                "appointment_cancellation_not_narrow_to",
            ),
        )

    def test_15486_chili_te9_first_to_booking_chery_tenet(self):
        """15486: STT «Чили Te9» — Chery Tiggo 9, запись на первое ТО."""
        t = (
            "Викинги Чери, сервис Андреева, слушаю вас. Можете записать на ТО? Алмаз. "
            "Ранее обслуживались у нас? нет ещё. Автомобиль какой у вас? Чили Te9. "
            "Какое ТО необходимо провести? вроде бы первый, на десяти тысячах. "
            "Первое техническое обслуживание. Стоимость ТО 21 800. Записываю на понедельник."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_27368_chli_pig9_maps_to_chery_tiggo_9_brand(self):
        """27368: STT «Чли Пиg 9» -> Chery Tiggo 9 (марка Chery/Tiggo)."""
        t = (
            "Здравствуйте, у меня Чли Пиg 9, третье ТО по плану подходит 30 000 км. "
            "Хотел бы узнать по стоимости третьего ТО."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")

    def test_15652_repair_arms_status_price_tolko_not_narrow_to(self):
        """15652: статус ремонта/рычаги; «цена только» — не «цена ТО», НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Сегодня мою машину должны были взять в ремонт. "
            "Запчасти пришли, взяли или нет. Соедините с приёмщиком Читаев Владимир. "
            "В работу мы взяли, но неоригинальные рычаги не подошли. "
            "Правый будет стоить 51 500, то есть у нас увеличивается цена только за один рычаг. "
            "Буду ждать сигнала."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "non_reg_service_topic")

    def test_15679_outbound_repeat_parts_arrival_timing_not_narrow_to(self):
        """15679: исх. «ещё раз:», запчасти пришли — уведомление о времени, НЕ_ТО."""
        t = (
            "Алло, Игорь Александрович, ещё раз: Викинги, Юлия, диспетчер сервиа. "
            "Я просто забыл вам сказать, что завтра ещё мы планируем в работу взять по запчасти. "
            "У вас пришёл радар? Блок, ага. "
            "Да, и чтобы просто вы планировали чуть побольше времени, то есть ТО, плюс ещё запчасти будем. "
            "Просто вас оповещаем."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "outbound_to_quote_continuation_not_narrow_to")

    def test_15766_sdelat_to_particle_no_to_topic_not_narrow_to(self):
        """15766: ожидание аккумулятора — нет записи/цены ТО; «сделать, то мы» — НЕ_ТО."""
        t = (
            "Официальный дилер Чери и Тенет Викинги, администратор Диана. "
            "Викинги, Андреева Юлия, диспетчер сервиса, слушаю вам. "
            "Георгий, по поводу машинки. "
            "Пока не поступил аккумулятор. Также ожидаем его. "
            "Отдел по работе с клиентами. Деталь заказана, аккумулятор. "
            "Мы уже ждём месяц машину, мы не можем ничего сделать, то мы в очереди. "
            "Вы можете обратиться в клиентский отдел."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_15767_diagnostics_tiggo4ga_chery_not_narrow_to(self):
        """15767: запись на диагностику, «4га» = Tiggo 4 — НЕ_ТО, chery_tenet, диагностика."""
        t = (
            "Викинги Чери Сервис, Андреева Юлия, слушаю вас. "
            "Мне бы на диагностику записаться, то загорелась неисправность давления в шинах. "
            "Шкода Октавия есть у вас? Нет, шкоду мы продали. "
            "Получается, автомобиль 4га с номера 634. "
            "Записали на 10 июля, 3 часа дня. "
            "Доверенность: Савинова доверяет вас провести техническое обслуживание. "
            "На ТО мы это не просим, если заявляется гарантийное обращение."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "diagnostics")
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_intent"), "diagnostics")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_15790_xcite_komsomol_wrong_phone_not_narrow_to(self):
        """15790: Xcite, перепутала номер (Заставная → Викинги Комсомольский) — НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. "
            "Хотела пройти ТО, узнать стоимость и время. Первое ТО, платно записаться. "
            "У меня Xсаit. Вы на Заставную звоните? "
            "Дело в том, что вас нужно провести ТО на Викингах только которые находятся "
            "в Комсомольском районе. Здесь нельзя, не выполняется тут. "
            "Я телефон найду. Извините, до свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "wrong_department_lada_redirect_not_narrow_to")

    def test_17497_xcite_xcross_gromovaya_redirect_not_narrow_to(self):
        """17497: STT «ИX саit XCross» → Xcite; отправка на Викинги на Громовой — НЕ_ТО."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("xcite", _normalize_text("машине ИX саit XCross 7"))
        t = self._transcript_from_log(17497)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "wrong_department_lada_redirect_not_narrow_to")

    def test_17525_xitepr7_xcite_other_brand_not_nissan(self):
        """17525: STT «XitePr7» → Xcite (прочие), не Nissan из «Чери Ниссан» у диспетчера."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("xcite", _normalize_text("машины XitePr7"))
        t = self._transcript_from_log(17525)
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertNotEqual(dims.get("service_brand"), "nissan")

    def test_17640_chl_t1_stt_chery_tenet_brand(self):
        """17640: STT «Чl Т1» → Chery/Tenet T1, не other_brand после гашения greeting."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        self.assertIn("tenet t1", _normalize_text("ремонтируете такие или нет, Чl Т1. 13го года"))
        t = self._transcript_from_log(17640)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_15502_outbound_tenet_t4_crm_lead_already_booked_not_narrow_to(self):
        """15502: CRM Tenet T4, клиент уже записан на ТО — НЕ_ТО, chery_tenet."""
        t = (
            "авай. Ссмотр Алло. Наталья Викторовна, добрый день, компания Викинги. Меня зовут Юлия. "
            "Удобно разговаривать вам? Вы по поводу ТО? Да, по поводу заявки на автомобиль Тэнет. "
            "Да, по поводу заявки на автомобиль ТННТ-4 764 госномер. Получили заявку. "
            "На 4 июля к нам хотели бы записаться на первое ТО, и указано, что справа что-то трещит у вас. "
            "Ну, я уже записалась на ТО на 4тое июля, только у меня был адрес указан не Викинги, а на Солнечной. "
            "Поэтому я уже записалась. Так, то есть вы на Солнечном, да, будете проводить техническое обслуживание? "
            "Ага. Всё, хорошо тогда. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_13647_seat_cushion_arrived_replacement_not_to(self):
        """13647: поступила подушка сиденья — прочие работы, замена, узкий НЕ_ТО."""
        t = (
            "Алло. Денис Юрьевич, добрый день, компания Викинги. Меня зовут Юлия. "
            "Удобно разговаривать вам? Да, говорите. "
            "Звоню по поводу вашего автомобиля Черепгова 8 ПроМкс. "
            "На ваш автомобиль поступила подушка сиденья переднего левого. "
            "Хотели с вами согласовать день и время для замены. "
            "По времени потребуется ориентировочно 2,5—3 часа. "
            "Если после обеда, то можно на 2 часа дня. "
            "Давайте после обеда, в 11:00 привезу машину 25-го. "
            "25 июня, время 11:00 дня, вас записали."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertEqual((dims.get("evidence") or {}).get("work_hit"), "replacement")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "arrived_part_replacement_not_narrow_to")

    def test_13780_chre_7l_crm_brand_chery_tenet(self):
        """13780: CRM «Чре 7L» / голое «Чре» — chery_tenet (Chery / Tiggo 7 L)."""
        t = (
            "компания Викинги. Меня зовут Юлия. заявку получили по вашему автомобилю "
            "Чре 7L, гономеер 876. пропал звуковой сигнал при блокировке дверей. "
            "завтра на 10:00 подъедем."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

        t_bare = (
            "заявку получили по вашему автомобилю Чре, госномер 876. "
            "пропал звуковой сигнал при блокировке дверей."
        )
        dims_bare = infer_sto_booking_dimensions(t_bare)
        self.assertEqual(dims_bare.get("service_brand"), "chery_tenet")

        t_cher = (
            "заявку получили по вашему автомобилю Чер, госномер 876. "
            "пропал звуковой сигнал."
        )
        dims_cher = infer_sto_booking_dimensions(t_cher)
        self.assertEqual(dims_cher.get("service_brand"), "chery_tenet")
        self.assertEqual((dims_cher.get("evidence") or {}).get("brand_hit"), "чери")

    def test_26326_ceriariza8_maps_to_chery_arrizo8(self):
        """26326: CeriaRiza 8 = Chery Arrizo 8 (Ceria=Chery, Riza/Arizzo=Arrizo)."""
        t = (
            "Алло, добрый день. Я звоню по поводу вашего автомобиля CeriaRiza 8, госномер 417. "
            "На ваш автомобиль поступил экран мультимедийной системы с двумя дисплеями. "
            "Хотели согласовать день и время для замены. "
            "Из ближайших дат могу предложить вторник, 15 сентября, в 16:00. "
            "Хорошо, давайте на вторник в 16:00."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "other_work")

    def test_13785_na_itu_chlyusti_stt_inbound_book_to_sto_to_in(self):
        """13785: STT «на И ТУ» + «Члюсти» в приветствии — СТО_ТО_вх, вид работ ТО."""
        t = (
            "Викинги Члюсти, фирус Андреева, Юлия, слушаю. Здравствуйте. "
            "Здравствуйте, Юлия. А подскажите, пожалуйста, мне бы на И ТУ записаться?"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"), msg=f"expected STO_IN, got {dept}/{ct}")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_12922_post_to_warranty_defect_consult_not_narrow_to(self):
        """12922: после ТО нашли дефект; консультация по гарантии на замену — НЕ_ТО."""
        t = (
            "Официальный дилер Чери и Тенет Викинги. У меня ТО было, и мне там нашли неисправности, "
            "нужно под замену. Но мне кажется, это гарантийный случай. "
            "Викинги Чери, диспетчер сервиса Юлия. я у вас проходила третье ТО, "
            "машине будет три года, она ещё на гарантии. "
            "нашли неисправность люфт правого рулевого наконечника. "
            "я к вам записана на шестнадцатое число на его замену, "
            "а оно не входит в гарантийный случай?"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "warranty")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertFalse(infer_sto_appointment_agreed(t, dims))

    def test_call_13380_fourth_to_warranty_stamp_side_work_narrow_to_in(self):
        """13380: 4-е ТО, смета, слот; гарантийная отметка и стук — СТО_ТО_вх."""
        t = (
            "Викинги, челсти сервис Андреева Юлия, слушаю вас. Здравствуйте. "
            "Э-э, я бы хотел записаться на ТО. Сергей. "
            "Ранее к там заезжали, обслуживались у вас? Да-да-да. "
            "Собственник Сергеева Лариса Петровна. "
            "Автомобиль у вас на механике, 24-го года выпуска. "
            "Пробег какой на автомобиле сейчас? Ну, к вас доеду, будет уже 40 там с небольшим. "
            "Угу. Так, да, подходит у вас получается четвёртое техническое обслуживание. "
            "Меняется масло двигателя, фильтр масляный, фльтр воздушные, фильтр салонная, тормозная жидкость. "
            "По стоимости выходит 20 700 ₽, техническое обслуживание. "
            "Масла-фильтр у меня свои. Тогда по стоимости будет ри 11 350 ₽. "
            "если расходные материалы ваши, отметку о проведении ТО мы вас в книжку гарантийную не поставим. "
            "Сейчас с 1 мая ужесточились требования. Ну ладно. "
            "запись у вас сейчас ведётся на следующей неделе. Ну тогда на четвёртое число. "
            "Можно в любое время, с 8:00 утра до крайнего 12:00—12:30. Ну, давайте к десяти. "
            "У меня что-то там по передней подвесочке застучало. "
            "Да, механик на то осматривает в обязательном порядке ходовую часть. "
            "если запчасти будет в наличии, значит, поменяем, если гарантийный случай. "
            "чтобы инженер по гарантии осмотрел автомобиль. "
            "четвёртое июля, суббота, время 10:00. А да, на 10:00 вас записали."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected STO_TO_IN, got {rub} ({reason})")
        self.assertTrue(infer_sto_appointment_agreed(t, dims))

    def test_12923_arriza8_mixed_script_chery_tenet_brand(self):
        """12923: STT «Aриiзa 8» в CRM — Chery Arrizo 8, не other_brand."""
        t = (
            "викинги члисервиса андреева юлия слушаю записаться на то "
            "филоненко константин анатольевич "
            "автомобиль aриiзa 8 госномер 967 23 года выпуска "
            "пятое техническое обслуживание 20650 18 июня 1530 записали"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_ar8_short_arrizo8_chery_tenet_brand(self):
        """«ар 8» / «ar8» — сокращение Chery Arrizo 8."""
        for frag in ("автомобиль ар 8 госномер", "машина ar8 пробег"):
            with self.subTest(frag=frag):
                dims = infer_sto_booking_dimensions(frag)
                self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_12447_conditional_cancel_new_to_slot_sto_to_in(self):
        """12447: «если поближе — отменю», но слот на 17-е согласован — СТО_ТО_вх."""
        t = (
            "алло игорь викинги диспетчер юлия звоню по вашей заявке tiggo 8 pro max "
            "записать на техническое обслуживание то-4 подходит звуки подвески "
            "четвертое то провести я же телефоном записался 11-е числа ближайшее 17-е "
            "давайте записывайте если есть поближе я тогда у вас отменю запись позвоню "
            "на 17-е числа 11:00 предварительно договорились"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_8924_cancel_existing_to_still_not_narrow_to(self):
        """8924: явная отмена записи на ТО — регрессия НЕ_ТО."""
        t = (
            "администратор салона на 26-е число в 11:30 записано то "
            "мы хотели бы отказаться диспетчер дарья отменяйте да отменяйте"
        )
        dept, ct = classify_auto(t)
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_12462_ocherednoe_to_salnik_sto_to_in(self):
        """12462: «записаться на очередное ТО» + замена сальника — СТО_ТО_вх, не non_reg."""
        t = (
            "викинги чери диспетчер юлия nissan кашкай "
            "хотел записаться на очередное то пробег 100 000 "
            "что-то кроме то потребуется рекомендовали заменить сальник "
            "с предыдущего то рекомендация солинблоки подрамника "
            "давайте по наличию и потом запишем отдел запчастей свяжусь"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertEqual(dims.get("service_brand"), "nissan")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_28645_too_stt_with_besides_to_prompt_stays_sto_to_in(self):
        """28645: STT «ТОО» + «кроме ТО...» в записи на ТО — СТО_ТО_вх, work_type=to."""
        t = self._transcript_from_log(28645)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_12475_to_price_third_diskont_sto_to_in(self):
        """12475: цена 3-го ТО, STT «стого/третья», «дисконт» — СТО_ТО_вх, не non_reg."""
        t = (
            "администратор салон по стоимости стого у кого могу "
            "викинги чери диспетчер юлия в то chery tg7 pro max какую стоимость "
            "полтора вариator какую по стоимости озвучить третья 26000 "
            "дисконта никакого нет скидка 20 процентов запись на 18 июня"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_18590_prefilled_online_application_confirmation_not_narrow_to(self):
        """18590: дата и время уже указаны в заявке; звонок только подтверждает её — НЕ_ТО."""
        t = self._transcript_from_log(18590)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(
            reason,
            "prefilled_online_application_confirmation_not_narrow_to",
        )

    def test_18777_outbound_prefilled_application_confirmation_not_narrow_to(self):
        """18777: исходящий звонок только подтверждает дату и время готовой заявки — НЕ_ТО."""
        t = self._transcript_from_log(18777)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(
            reason,
            "prefilled_online_application_confirmation_not_narrow_to",
        )

    def test_18830_to_scheduling_deferred_until_callback_not_narrow_to(self):
        """18830: график неизвестен, запись отложена до повторного звонка — НЕ_ТО."""
        t = self._transcript_from_log(18830)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "deferred_to_scheduling_callback_without_booking")

    def test_18758_short_tiggo7l_answer_is_chery_tenet(self):
        """18758: ответ «М Семь Л, Тиг» на вопрос об авто — Chery Tiggo 7 L."""
        t = self._transcript_from_log(18758)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=reason)

    def test_18837_fuel_filter_specification_consultation_not_narrow_to(self):
        """18837: наличие топливного фильтра и регламент замены — консультация, НЕ_ТО."""
        t = self._transcript_from_log(18837)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "component_regulation_consultation_not_narrow_to")

    def test_18773_wrong_body_line_then_diagnostic_booking(self):
        """18773: ошибочная линия кузовного, затем запись к диагносту — диагностика."""
        t = self._transcript_from_log(18773)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertEqual(dims.get("work_type"), "diagnostics")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_19059_engine_diagnostics_under_hood_not_body_shop(self):
        t = (
            "Компания Викинги, Юлия, диспетчер сервиса. "
            "Запишите, пожалуйста, меня на диагностику автомобиля. "
            "Какая диагностика необходима? После ремонта цепи ГРМ машина не едет, "
            "плохо набирает скорость и мощность, до ремонта был шум под капотом. "
            "Давайте на 28 июля в два часа. Вас записали."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        self.assertTrue(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "diagnostics")

    def test_21938_whistle_from_engine_is_diagnostics_not_body_shop(self):
        """21938: свист из-под двигателя/капота при газовке — диагностика, не кузовной."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. "
            "У меня свист из-под двигателя, предполагаю натяжной или помповый ролик. "
            "Свист усиливается при газовке. Машина на гарантии. "
            "Когда можно подъехать, чтобы посмотреть? "
            "Есть запись к инженеру по гарантии на 20-е, давайте на 12:30. Вас записали."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        self.assertTrue(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "diagnostics")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_body_damage_inspection_with_diagnostics_word_stays_body_shop(self):
        t = (
            "Викинги, кузовной цех. Запишите на диагностику повреждения крыла: "
            "есть вмятина и царапина, нужна покраска и осмотр мастера."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "body_shop")

    def test_27339_post_visit_body_panel_fit_complaint_is_body_shop(self):
        """27339: после визита жалоба на зазор/крепление крыла-бампера -> кузовной."""
        t = self._transcript_from_log(27339)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "body_shop")
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "post_visit_body_panel_fit_complaint",
        )

    def test_18885_arrived_warranty_parts_replacement_work_type(self):
        """18885: запчасти поступили по гарантии, согласуется замена — гарантия."""
        t = self._transcript_from_log(18885)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "warranty")
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "warranty_parts_replacement",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_21943_arrived_part_phrase_prishla_po_garantii_is_warranty(self):
        """21943: «зашла/пришла запчасть по гарантии» — work_type warranty."""
        t = (
            "Компания Викинги, диспетчер сервиса Юлия. "
            "На ваш автомобиль зашла запчасть, пришла по гарантии — датчик температуры. "
            "Хотели с вами согласовать день и время для замены. "
            "Запись возможна на следующей неделе."
        )
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "warranty")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "warranty_parts_replacement")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_18921_sixth_to_quote_and_new_booking_not_cancellation(self):
        """18921: шестое ТО, смета и новый слот; STT «отмените» в хвосте не отменяет запись."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        for phrase in ("Дачего хотела записаться", "а кого хотела записаться"):
            self.assertIn("на то записаться", _normalize_text(phrase))
        t = self._transcript_from_log(18921)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=reason)

    def test_12476_online_app_technical_sto_to_in(self):
        """12476: входящий после Expo Mobility заявки — СТО_ТО_вх, не STO_OUT/НЕ_ТО."""
        t = (
            "чери викинги на заставной ассистент сервиса андреева юлия слушаю вас "
            "здравствуйте я оставляла заявку expo mobility на техническое посмотреть "
            "меня зовут юлия мы заявку получили вчера скрежет кондиционер плохо "
            "диагностика 18 июня 8:30 будем ожидать"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_12945_first_to_price_not_warranty(self):
        """12945: первое ТO на 10 000 + цена; гарантия — пояснение диспетчера, не work_type."""
        t = (
            "официальный дилер chritент администратор диана я владелец tenet т-4 "
            "другого региона поедем в тольятти подойдет время первого то на 10 000 "
            "могу у вас его сделать викинги чери диспетчер юлия какая модель т4 робот "
            "19750 будет стоимость того если будут вопросы гарантийные нужно согласие "
            "то что по гарантии выполняется проверки тогда доверенность"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        dept, ct = classify_auto(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_12640_diagnostic_slot_first_to_quote_sto_to_out(self):
        """12640: «Алло, Елизавета…» + первое ТО + смета — СТО_ТО_исх (форма набора)."""
        t = (
            "алло елизавета добрый день дилерский центр викинги диспетчер сервиса юля "
            "вы хотели бы автомобиль записать на сервис я бы хотела уточнить по поводу то первого "
            "я на субботу на эту записана на диагностику на 15:30 "
            "можно ли так записаться на диагностику и на первое то плановое "
            "вы записаны к диагносту на 15:30 к сожалению на субботу места у механиков нет "
            "в пятницу будет понятно может быть кто-то перенесёт запись либо отменится "
            "у вас первое да то 10 000 км пробег первое то подходит не ровно 10 но 9800 "
            "есть время на 17-е число 15:30 могу записать вас "
            "по стоимости получается 19 000 рублей перезвоню вас по времени"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_12749_first_to_chery_tig4_nyugas_sto_to_in(self):
        """12749: первое ТО + «Чери Тиг 4 Нюгас» + слот — СТО_ТО_вх, chery_tenet, не car_at_service."""
        t = (
            "администратор салона соедините по то викинги чери диспетчер юлия "
            "хотела узнать подходит ли срок следующего то "
            "автомобиль у вас обслуживался зиланова наталья "
            "у вас чери тиг 4 нюгас номер 820 "
            "первое техническое обслуживание 16 июня 10 000 км пробег "
            "шестнадцатое число могу вас записать 15:30 "
            "по стоимости 19 050 рублей "
            "16 июня в 14:30 сдаёте автомобиль заберёте по готовности вечером"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertFalse(dims.get("evidence", {}).get("car_already_at_service"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_13620_cheretsg_4n_first_to_booking_chery_not_car_at_service(self):
        """13620: «Черецг 4N» + первое ТО + пригоню/заберёте — chery_tenet, СТО_ТО_вх, не car_at_service."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Андреева Юлия, слушаю вас. "
            "Здравствуйте. Хотелось бы записаться. на ТО один. "
            "Ранее к там заезжали, обслуживались у вас? Да, заезжали, делали нулевое там Чери два месяца. "
            "вот теперь надо на первое записаться. Черецг 4N. "
            "автомобиль у вас госномер 233225 года выпуска. "
            "нагу роботе автомобиль у вас. Пробег 6900. "
            "на первом ТО меняется масло в двигателе. По стоимости то выходит 19 050. "
            "А если, например, я с утра её просто пригоню, ключи оставлю, а там в 12:30 её заберёте? "
            "можем в 7:50 принять. Всё, Юль, хорошо, тогда завтра в 7:50 вас записали, будем ожидать."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        self.assertFalse((dims.get("evidence") or {}).get("car_already_at_service"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_12778_to_duration_callback_confirmation_sto_to_in(self):
        """12778: перезвон, подтверждение слота — «ТО будет длиться», кампания при ТО — СТО_ТО_вх."""
        t = (
            "викинги чери диспетчер юлия вы мне сейчас звонили по поводу "
            "23-е на 8:00 утра удобно будет запишем "
            "там то будет длиться 2 3 часа примерно "
            "открыта сервисная кампания проверка и доработка жгута сидений "
            "проводится бесплатно тоже при то все сделаем "
            "нужна доверенность от владельца напомним 22-го числа"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_21903_service_campaign_curtain_recall_is_not_narrow_to(self):
        """21903: сервисная кампания по шторке/жгуту без темы регламентного ТО — НЕ_ТО."""
        t = (
            "Это компания Викинги, Юлия, диспетчер сервиса. "
            "Для вашего автомобиля открыта сервисная кампания, это проводится бесплатно "
            "по проверке, доработке жгута сидения. "
            "Может быть, есть возможность подъехать на ближайшие дни? "
            "Давайте пока на понедельник предварительно, на то. "
            "На какое время вам? На 15:00, давайте запишем."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "service_campaign_recall_not_narrow_to")

    def test_13398_adjacent_dept_existing_to_coordination_not_narrow_to(self):
        """13398 и аналоги: смежный отдел звонит по своей теме; ТО уже у диспетчера — НЕ_ТО."""
        cases = (
            (
                "допоборудование + фаркоп",
                (
                    "Алло? Алло. Александр Владимирович? Да-да, здравствуйте. "
                    "Здвствуйте, это Викинги Тт, отдел допоборудования по поводу установки фаркопа на девятку. "
                    "Да-да-да. Диспетчер сервиса мне передала, это записались на ТО нулевое 25 июня. "
                    "фаркоп и проводка у вас с собой? Давайте вы также всё и оставляйте по нулевому ТО там. "
                    "пока я записал себе 25-26-го на Фарков. всё, договорились."
                ),
            ),
            (
                "отдел запчастей",
                (
                    "Алло, Иван, добрый день. Викинги, отдел запчастей. "
                    "Диспетчер сервиса мне передала: вы записались на ТО на четверг. "
                    "По наконечникам хотел уточнить наличие и стоимость перед визитом."
                ),
            ),
            (
                "кузовной цех",
                (
                    "Алло. Здравствуйте, кузовной цех Викинги. "
                    "Диспетчер сервиса передала, что вы записались на ТО нулевое. "
                    "Хотел уточнить по вмятине на двери и срокам осмотра."
                ),
            ),
            (
                "отдел трейд-ин",
                (
                    "Алло, Сергей, добрый день. Викинги, отдел трейд-ин. "
                    "Диспетчер сервиса мне передала: вы записались на ТО. "
                    "По оценке вашего автомобиля на обмен хотел уточнить детали."
                ),
            ),
        )
        for label, t in cases:
            with self.subTest(case=label):
                dept, ct = classify_auto(t)
                self.assertEqual((dept, ct), ("STO", "STO_OUT"), msg=label)
                dims = infer_sto_booking_dimensions(t)
                rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
                self.assertIsNone(rub, msg=f"{label}: expected НЕ_ТО, got {rub} ({reason})")

    def test_sto_to_out_auto_credits_ask_name_criterion(self):
        """СТО_ТО_исх.: критерий 8 — зачёт без «как обращаться» (имя клиента уже известно)."""
        from analyze_sto_quality import evaluate_sto_by_rules

        t = (
            "Алло, Александр Владимирович, добрый день. Викинги, диспетчер сервиса Юлия. "
            "Завтра записаны на ТО в 10:00. Подъедете? Да, буду. Александр, ждём вас. До свидания."
        )
        self.assertEqual(evaluate_sto_by_rules(t, sto_to_rubric_type="STO_TO_IN").get(8), 0.0)
        self.assertEqual(evaluate_sto_by_rules(t, sto_to_rubric_type="STO_TO_OUT").get(8), 1.0)

    def test_13400_sunroof_diagnostic_not_narrow_to(self):
        """13400: люк не закрывается, запись к диагностам — НЕ_ТО, вид работ диагностика."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Андреева Юлия, Слушаю вас. Здравствуй. "
            "Алло, добрый день. Записаться бы в сервис Люк глянуть, он — Ну сервис Люк глянуть, "
            "он перестал открываться. — К вас по имени Так можно обращаться? Подскажите, пожалуйста. "
            "— Или я? — Мне очень приятно. Ранее к нам заезжали, обслуживались у вас? — Да-да. "
            "— Фамилию собственника подскажите, пожалуйста. — ВГод Сморецкий. — Минутотку. "
            "Контактный телефон на 18:30 заканчивается у вас верно? Да. "
            "Автомобиль ЧеrG 4рога с номеер 242.но. 24-го года выпуска. "
            "Пробег какой на автомобиле сейчас? Э-э, на какой был последний раз? Сейчас. минуточку. "
            "Так, ну у вас указано 23 009 км. Ну, 24, наверное. Угу. Так, ещё раз, что у вас с люком? "
            "Аа, Люк автоматически перестал закрываться. Практически перестал закрываться вплотную. "
            "Ну, то есть, там кнопки, короче, не работают. То есть остаётся какой-то промежуток, "
            "получается? Да-да-да. Примерно сколько? Э-э-э. Ну, миллиметр, наверное. "
            "Ну, вода не Так. —верное. Ну, вода не папнет. Я уже Так-то, ну, приезжал с этой проблемой, "
            "там они называли, что перепграмировали электрика вот Так вот л. он потом начинал выезжать сам. "
            "— Угу. Так, поняла. СЕйчас одну минуточку, подскажу в какой день могу вас ближайший. "
            "Скажу какой день могу вас ближайший озвучить? Сейчас запись вс к диагностам плотненькая. "
            "Один специалист у вас в отпуске. Хмм,то будет только в будний день. "
            "То есть в выходные, к сожалению, не могу предложить, потому что в выходные,г, в выходные. "
            "Ну да, в будний. Сейчас, минуточку. Да бу. Сейчас, минутотку. "
            "Ближайшая — 23 июня, следующая неделя, вторник, есть. Неделя, вторник, э-э, "
            "есть время после обеда, то есть можно 12:30, либо половина второго. Да-да. "
            "Лбо либо.... Либо половина второго. То есть 13:30. Давайте во вторник в 12:30.— "
            "В 12:30. Угу. Хорошо, Илья, вас записали, получается, 23-. Вё, я вас записали. "
            "Получается, 23 июня, следующая неделя, вторник, время 12:30, половина п-го. Да. "
            "Накануне с вами ещё созвонимся, напомним вас о записи. Документыв, пожалуйста, "
            "не забывайте на автомобиль. Хорошо, понятно. Спасибо. Угу, пожалуйста. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_intent"), "diagnostics")
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_13517_primary_defect_diagnostic_not_narrow_to(self):
        """13517: дефект (прикуриватель) + прошлое ТО в CRM — НЕ_ТО, вид работ диагностика."""
        t = (
            "Ало. Викингивисервис Андреева Юлия, слушаю ва. Здравствуйте. "
            "Я бы хотела записаться или, может быть, без записи подъехать. "
            "У меня не работает в автомобиле прикуриватель. "
            "К вас по имени как можно обращаться? Дарья. "
            "Ранее к нам заезжали, обслуживались у нас? Да-да, я нулевое ТО у вас проходила. "
            "Фамилию собственника, подскажите, пожалуйста. Арефьева. "
            "Tenet Т4 белого цвета, госномер 122, 25 года выпуска. Пробег где-то 4,5. "
            "По прикуривателю нужно будет записываться, потому что данные работы проводит "
            "только диагност-Электрик. Ближайшее на 29 июня, понедельник, на 3 часа дня, вас записали."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_intent"), "diagnostics")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "primary_defect_diagnostic_not_narrow_to")

    def test_16673_outbound_breakdown_past_to_diagnostic_not_narrow_to(self):
        """16673: исх. поломка КПП у входа + прошлое «первом то» — НЕ_ТО (нет цены/записи на ТО)."""
        from call_analytics.sto_to_rubric import explicit_narrow_sto_to_topic_hit

        t = (
            "Д. Алло. Алло, Ксения Анатольевна? добрый день. что компания Викинги, Юлия, "
            "диспетчер сервиса. вас удобно говорить? Да, удобно. Ага. Передали, что вы "
            "автомобиль вчера привезли на эвакуатор? Нет, не на эвакуатор. получается, "
            "машина едет на передаче. Она выдаёт ошибку, что неравен ручник КПП. "
            "То есть у вас автомобиль сейчас у нас, да, перед нашим центром? "
            "Автомобиль стоит у вас возле входа. "
            "А-а, смотрите, у нас пока нет информации по записи на диагноты. "
            "Я с вас свяжусь, мы вас на что время пригласим, чтобы вы сдали автомобиль. "
            "Я понимаю, что у вас запись плотная, но буквально две недели назад было на ТО. "
            "Вот. То есть, на Первом ТО. мне сказали, дали заключение, что с машиной всё хорошо, "
            "с ходовкой всё хорошо с машиной на Первом ТО. "
            "Мне надо выяснить причину, чтобы понимать, к кому вас записать: механик или диагност. "
            "Всё, понятно, что нужен диагност. Я с вас свяжусь сегодня в течение дня."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        hit, why = explicit_narrow_sto_to_topic_hit(t)
        self.assertFalse(hit, msg=f"past TO must not be narrow topic, got {why}")
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "primary_defect_diagnostic_not_narrow_to")

    def test_16758_infiniti_ac_diagnostic_capability_to_not_narrow(self):
        """16758: Infiniti, фреон/диагностика; «ТО проводим, масло меняем» — capability, не СТО_ТО_вх."""
        from call_analytics.sto_to_rubric import explicit_narrow_sto_to_topic_hit

        t = (
            "Чери Викинги на Заставной, ассистент сервиса. Юлия. Здравствуйте. "
            "Смотрите, я хотела бы узнать, вы обслуживаете Инфинити? "
            "У меня вытекает фреон кондиционера. Хотела бы загнать Infinity на диагностику, "
            "посмотреть по патрубкам. Начальная диагностика около 3 000. "
            "А Infiniti вы уже обслуживали, да? Ну да, техническое обслуживание проводим, "
            "в принципе. Масло меняем, что-то, какие-то ещё работы. "
            "Посмотрите тогда по датам. Семнадцатого числа пятница есть время на 8:30."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        hit, why = explicit_narrow_sto_to_topic_hit(t)
        self.assertFalse(hit, msg=f"capability TO must not be narrow topic, got {why}")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "primary_defect_diagnostic_not_narrow_to",
                "non_reg_service_topic",
                "no_explicit_to_topic",
            ),
            msg=reason,
        )

    def test_primary_defect_past_to_phrasing_variants(self):
        """Рамка: «вы ТО проходили» + дефект — НЕ_ТО; «хочу на третье ТО» + дефект — остаётся ТО."""
        variants_ok = [
            "Викинги, диспетчер Юлия. Вы ТО проходили, а сейчас прикуриватель не работает. Запишите.",
            "ассистент сервиса. нулевое то проходила. люк перестал закрываться. запись на диагностику.",
        ]
        for t in variants_ok:
            dims = infer_sto_booking_dimensions(t)
            rub, _ = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
            self.assertIsNone(rub, msg=t[:60])
            self.assertEqual(dims.get("work_type"), "diagnostics")
        t_to = (
            "Викинги, ассистент сервиса Юлия. Хочу записаться на третье ТО, пробег 30 тысяч. "
            "И заодно прикуриватель не работает. Записываю на 15 июля, третье ТО 25 тысяч."
        )
        dims = infer_sto_booking_dimensions(t_to)
        rub, _ = infer_sto_to_rubric_type(t_to, "STO_IN", dims, department="STO")
        self.assertEqual(rub, "STO_TO_IN")
        self.assertEqual(dims.get("work_type"), "to")

    def test_13400_cherg_4roga_chery_tiggo_brand(self):
        """13400: STT «ЧеrG 4рога» — Chery Tiggo 4 Pro, не other_brand."""
        dims = infer_sto_booking_dimensions(
            "ассистент сервиса автомобиль ЧеrG 4рога с номеер 242 24-го года"
        )
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_13887_outbound_warranty_deep_diagnostic_not_narrow_to(self):
        """13887: претензия + углублённая диагностика; «то на днях» — союз, не ТО."""
        t = (
            "Д. Алло. Добрый день, компания Викинги. Меня зовут Юлия. Удобно разговаривать? "
            "Да, говорите. Мы получили претензию по автомобилю по вашему Тэнет-4. "
            "автомобиль у вас периодически плохо запускается, дефект снова проявился. "
            "не запускается с первого раза. никакую неисправность. "
            "Удобно будет перезаписаться на более углублённую диагностику? "
            "ближайший день для записи — 29 июля, понедельник 10:30. "
            "Я на днях, если ничего не изменится, то я не буду звонить. "
            "Если изменится, то на днях я вас наберу. "
            "10:30 тогда. Хорошо, вас записали. Контрольный звонок накануне ожидайте."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            ("non_reg_service_topic", "primary_defect_diagnostic_not_narrow_to"),
            msg=reason,
        )

    def test_13828_tm4_tenet_t4_brand_variants(self):
        """13828: TM4 / ТМ4 / TM 4 / TN 4 / Tenet 4 — chery_tenet (Tenet T4)."""
        base = (
            "Чери Викинги на Заставной, ассистент сервиса Андреева Юлия. "
            "сколько у вас стоит первое ТО {model}? Робот. Нулевое ТО 13 600."
        )
        for model in ("ТМ 4", "ТМ4", "TM4", "TM 4", "TN 4", "Tenet 4", "Тэнет-4"):
            dims = infer_sto_booking_dimensions(base.format(model=model))
            self.assertEqual(
                dims.get("service_brand"),
                "chery_tenet",
                msg=f"failed for model={model!r}",
            )

    def test_13862_op_in_sales_consult_not_sto(self):
        """13862: админ → менеджер ОП, покупка Chery/Tenet — ОП_вх, не СТО."""
        t = (
            "Администратор салона. Оксана. Доброе утро. можно у вас поговорить с продажником, "
            "с менеджером? Да, можно. какой автомобиль интересует? Ну, хотелось бы Чери. "
            "Я переключаю вас, Оставайтесь на линии. "
            "Андрей, Доброе утро. меня зовут Анастасия, менеджера отдела продаж Викинги на Заставной. "
            "у вас Чери 7L есть в продаже? в какой комплектации и какова цена? "
            "трейдин возможен, кредит возможен. 2 600 машины будет стоит. тест-драйв. "
            "по анализу обращений на сервис поступали — уточню у сервиса про туманки."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("OP", "OP_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertNotEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_13883_47ruga_chery_tiggo_7_brand(self):
        """13883: STT «47руга» / «7руга» — chery_tenet (Chery Tiggo 7)."""
        for model in ("47руга", "7руга", "7тига", "7tiga", "47тига"):
            dims = infer_sto_booking_dimensions(
                "ассистент сервиса Андреева Юлия. Автомобиль "
                + model
                + " с номером 681 2023 года. Запись на шестое ТО."
            )
            self.assertEqual(
                dims.get("service_brand"),
                "chery_tenet",
                msg=f"failed for model={model!r}",
            )

    def test_13945_existing_to_clarification_not_narrow_to(self):
        """13945: уточнение по ранее сделанной записи на нулевое ТО — НЕ_ТО."""
        t = (
            "Алло, Александр, добрый день, компания Викинги Юлия, вы нам звонили? "
            "Да-да, хотел уточнить, 25 записывалсь. "
            "Хотел уточнить, на двадцапя-е записывался наТ нулевое ТО. "
            "А нам с мастером ещё договаривались по установке фаркопа. "
            "У меня запись нам есть на двадцать шестое. "
            "Авилов. Так, ну смотрите, к нам вы записаны на 25 июня на техническое обслуживание на 10:00. "
            "И комментарий стоит, что дали передать автомобиль на дооборудование для установки фаркопа."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "existing_visit_clarification_not_narrow_to",
                "existing_to_slot_confirmation_not_narrow_to",
                "booking_flow_not_narrow_to",
            ),
            msg=reason,
        )

    def test_19234_existing_to_own_oil_clarification_not_narrow_to(self):
        """19234: уже записался на ТО, вопрос можно ли привезти своё масло — НЕ_ТО."""
        t = (
            "Викинги чери, диспетчер сервиса Юлия. Здравствуйте. "
            "Юлия, здравствуйте. Я на ТО записался 28-го в 15:00. Да, вижу, угу. "
            "У меня вопрос: можно ли привезти масло своё? Для коробки. "
            "Если предоставляете свои расходные материалы, не купленные у официального дилера, "
            "мы отметку о техническом обслуживании не ставим. "
            "Если куплю масло у официального дилера в Москве и привезу с чеком — тогда можно."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_19303_just_booked_to_phone_correction_not_narrow_to(self):
        """19303: только сейчас записавался на ТО — правка телефона, уточнение НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "Я только сейчас записавался на ТО, техническое обслуживание на первое число. "
            "Телефон для связи не тот назвал. Никита, продиктуйте заново телефон, запишу. "
            "Девять два семь… Поправили, да. Спасибо. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "existing_visit_clarification",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_13966_outbound_crm_chrg_4rugu_sto_to_out(self):
        """13966: CRM «ЧrG 4rгу» + заявку получили — СТО_ТО_исх, chery_tenet."""
        t = (
            "Алло. Ольга Алексеевна, добрый день, компания Викинги. Юлия, диспетчер. "
            "заявку получили по вашему автомобилю ЧrG 4rгу, номер 5 27. "
            "Хотите к нам записаться на техническое обслуживание? "
            "По истории посмотрела, у вас уже десятое ТО, да? Пробег около 100 000. "
            "Желаемое время вы указали завтра, время 12:00. "
            "Стоимость десятого ТО 21 500. На завтра есть время на 12:20, либо на 15:30."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

        for model in ("ЧrG 4rгу", "chrg 4rгу", "CHRG 4RGU", "чrg 4rгу"):
            dims_m = infer_sto_booking_dimensions(
                "заявку получили по автомобилю " + model + " номер 527"
            )
            self.assertEqual(
                dims_m.get("service_brand"),
                "chery_tenet",
                msg=f"failed for model={model!r}",
            )

    def test_14525_outbound_callback_to_booking_sto_out(self):
        """14525: перезвон по пропущенному (фон STT + «вы нас звонили») — СТО_ТО_исх."""
        t = (
            "Блин, так неохота вот этот всё. считать, вотво-вот какое нахер Т ТО? вот пробег 85 000. "
            "У Фольксваген Поло Так ечасто Т ТО? Алло? Да. Сергей, добрый день, компания Викинги. "
            "Меня зовут Юлия. Вы нас звонили? Да-да-да, мне бы этот, на ТО записаться. "
            "Фамилию собственника подскажите. ООО Контур. "
            "Автомобиль ЧеретиГ 7 ПромМакс госномер 722? Совершенно верно. "
            "Четвёртый ТО, да, получается, необходимо провести на вашем автомобиле? "
            "давайте на субботу на 11:00. 27 июня в 11 вас записали."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_15849_outbound_callback_to1_quote_sto_out(self):
        """15849: исх. перезвон (мусор STT + «звонили. Слушаю вас»), смета ТО-1 без записи."""
        t = self._transcript_from_log(15849)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_14547_chg7_elgus_chery_tiggo_7l(self):
        """14547: STT «Чг7 Эльгус» — Chery Tiggo 7 L, chery_tenet."""
        t = (
            "Викинги Челисти, сервис Андреева, Юлия, слуша ва. "
            "сколько должно пройти времени между нулевым и первым ТО Чери? "
            "Автомобиль Чг7 Эльгус номер 953. Угу, да, совершенно верно. "
            "первое ТО проводится на пробег 10 000. Запишите на субботу."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")

    def test_16881_tig_semel_chery_tiggo_7l(self):
        """16881: STT «Тиг семель» / «Семь мель» — Chery Tiggo 7L, chery_tenet."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "Проконсультируйте, пожалуйста, меня насчёт ТО-1. Угу Тиг семель. "
            "Перечень работ и по стоимости. Семь мель у вас полноприводный или передний привод? "
            "Нет, передний. ТО-1 имеете в виду на 10 000 км? Да, первый год. "
            "Если установлена дополнительная защита двигателя, то 20 800."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, "STO_IN", dims, department="STO")
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_26371_cheregsemi_maps_to_chery_tiggo_7l(self):
        """26371: STT «череГСеми» → Chery Tiggo 7 L, service_brand=chery_tenet."""
        from call_analytics.sto_booking_dimensions import _normalize_text

        t = (
            "Алло, Александра Валерьевна? Добрый день. Дилерский центр Викинги, Юлия, "
            "диспетчер сервиса. Звоню по вашему череГСеми. "
            "Хотели уточнить, эксплуатируете ли автомобиль? "
            "Первое ТО у вас когда проходит? Давайте записываться тогда."
        )
        self.assertIn("chery tiggo 7 l", _normalize_text(t))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_16895_oil_filter_service_not_narrow_to_other_work(self):
        """16895: запись «масло поменять» + салонный фильтр — НЕ_ТО, вид работ Прочие."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "Скажите, можно у вас записаться масло поменять на следующей неделе? "
            "Замена масла? Нуsan Extrle. Только масло меняем, ещё что-то дополнительно? "
            "Масло, фильтр салонный. Меняем масло, масляный фильтр, фильтр салонный. "
            "Больше ничего? Нет. 16 июля в 16:00. Накануне позвоним, напомним."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_14549_renault_fluence_other_brand(self):
        """14549: STT «Рно Флюн» — Renault Fluence, other_brand (не Чери из приветствия)."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Андреева, Юлия, слушаю вас. "
            "Вот сейчас мне пройти на Рно Флюн ТО, ну, для ТО пройти, маслозамена, фильтр. "
            "Цена вопроса какая и на какое время можно записаться?"
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertEqual(dims.get("work_type"), "to")

    def test_14579_c4rogus_chery_tiggo_4(self):
        """14579: STT «C4рогус» — Chery Tiggo 4, chery_tenet."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Андреева Юлия. Слушаюс. "
            "Мне бы на ТО записаться. Фамилию собственника подскажите. Афанасьева. "
            "автомобиле C4рогус, номер 780 24 года выпуска. "
            "четвёртое ТО. Запишите на завтра на 10:00."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")

    def test_14496_new_to_booking_not_reschedule_narrow_to_in(self):
        """14496: «на ТО записаться» + 6-е ТО + смета — СТО_ТО_вх, не перенос (existing_to_reschedule)."""
        t = (
            "Викинги Чери, ассистент сервиса Андреева Юлия, слушаю вас. "
            "А мне бы вот на ТО записаться. Какой автомобиль у вас? "
            "Ранее к нам заезжали? Да-да. Я обслуживался, но записывался, но не обслуживал. "
            "Автомобиль Черетигова 8 Промаркт. Пробег 43 600. Шестое ТО подошло. "
            "Пятое ТО проводили в апреле. Меняется масло двигателя. "
            "По стоимости выходит 59 200. Запись возможна на 30-е число. "
            "Блин, а как мне приехать? У вас второй Дилерский центр АС-Авто, "
            "можете к ним обратиться, возможно, раньше у них есть время."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_14511_che_serommax_chery_tiggo_7_pro_max(self):
        """14511: STT «че Сероммакс» — Chery Tiggo 7 Pro Max, chery_tenet."""
        t = (
            "Викинги Чери, диспетчер сервиса. "
            "Александрович, автомобиль че Сероммакс, госномер 315. "
            "Какая дата указана у вас? 12 июня на 17:00."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

        for model in ("че Сероммакс", "Сероммакс", "че сероммакс"):
            dims_m = infer_sto_booking_dimensions("автомобиль " + model + " госномер 315")
            self.assertEqual(
                dims_m.get("service_brand"),
                "chery_tenet",
                msg=f"failed for model={model!r}",
            )

    def test_15058_past_to_work_order_copy_samara_not_narrow_to(self):
        """15058: копия заказ-наряда прошлого ТО у дилера; сейчас ТО в Самаре — НЕ_ТО."""
        t = (
            "Василий, добрый день, компания Викинги. Меня зовут Юлия. Вы нам звонили? "
            "Да, я в прошлом году на автомобиле делал у вас в дилерском центре ТО. "
            "Сейчас прохожу третье ТО у нас в Самаре, и они запросили наряд допуск на первое ТО. "
            "Заказ-наряд, что я действительно у вас делал какие работы по регламенту. "
            "В июле 2025 года к нам заезжали на техническое обслуживание. "
            "Электронно можно скан-копию отправить на почту или в Макс."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertFalse(dims.get("is_booking"), msg="document request is not booking")
        self.assertEqual(dims.get("work_type"), "other_work")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_14967_lada_wrong_phone_zastavnaya_redirect_not_narrow_to(self):
        """14967: Лада Гранта, перепутал номер (Заставная Чери → Викинги Лада на Громовой)."""
        t = (
            "Викинги, ассистент сервиса Андреева Юлия, слушаю вас. "
            "Можно на 11 июля на ТО записаться? "
            "Машина какая у вас? Лада Гранта. "
            "Вы позвонили на Заставную, мы обслуживаем автомобили Чери. "
            "Если нужны Викинги Лада, телефон их. Мне надо не на Заставную. "
            "Если нужно вас на Громовой улицу. Позвонить по-другому телефону. "
            "Вы позвонили на Заставную. 63 005."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(dims.get("work_type"), "other_work")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_14948_past_to_service_book_marks_correction_not_narrow_to(self):
        """14948: отметки в сервисной книжке по пройденным ТО — НЕ_ТО."""
        t = (
            "Викинги, ассистент сервиса Андреева Юлия, слушаю вас. "
            "Я прошёл нулевое и первое техническое обслуживание. "
            "Первое отмечено, а второе не отобразилось, хотя я его проходил, 9000 отдал. "
            "Передам в клиентскую службу, они поправят, завтра обновится."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(dims.get("work_type"), "other_work")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_18565_past_to_service_book_stamp_request_not_narrow_to(self):
        """18565: проставить в восстановленной книжке прошлые ТО — НЕ_ТО без новой записи."""
        t = self._transcript_from_log(18565)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "past_to_service_record_correction",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_19618_service_book_discussion_then_confirmed_new_to_booking(self):
        """19618: отметка в книжке не отменяет последующую подтверждённую запись на ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. "
            "ТО 3 на Chery Tiggo 7 Pro Max. Сколько обойдётся, если фильтры мои? "
            "Третье ТО — это 30 000 пробега. Полностью ТО стоит 28 150 рублей. "
            "Если материалы клиента не куплены у официального дилера, отметку ТО "
            "в электронной сервисной книжке не проставим. "
            "Второе ТО вы делали на Солнечной на пробеге 20 150. "
            "А можно записать на следующую неделю? "
            "В среду утром мест нет, в четверг только в 13:30. А пятница, 31-е? "
            "В пятницу можно в 9:30, 10:00 или 11:00. Давайте в пятницу в 9:30. "
            "Всё, записали вас 31-го, в пятницу, в 9:30, будем ждать."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=reason)

    def test_18570_own_consumables_eligibility_consultation_not_narrow_to(self):
        """18570: можно ли пройти ТО со своими расходниками, без цены и слота — НЕ_ТО."""
        t = self._transcript_from_log(18570)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "own_parts_to_eligibility_consultation",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(
            reason,
            "own_parts_to_eligibility_consultation_not_narrow_to",
        )

    def test_19720_own_consumables_after_to_price_calculation_is_narrow_to(self):
        """19720: вопрос о своих расходниках не отменяет расчёт стоимости конкретного ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. "
            "Хотел бы узнать стоимость прохождения ТО четвёртого и перечень работ. "
            "Чери Тигго 7 Про, четвёртое ТО к 40 000. "
            "Замена масла, фильтров, тормозной жидкости и масла в вариаторе. "
            "Стоимость получится 47 300 рублей. "
            "А если я со своими расходниками приезжаю, меняется цена? "
            "Если всё привезёте своё, по работам будет около 15 000 рублей."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(
            rub,
            "STO_TO_IN",
            msg=f"expected СТО_ТО_вх, got {rub} ({reason})",
        )

    def test_own_consumables_with_agreed_slot_still_narrow_to(self):
        """Свои расходники не исключают ТО, если фактическая запись состоялась."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "Хочу записаться на третье ТО со своими расходниками. "
            "Можно со своими запчастями? Да, можно. В среду в 10:00 подходит? "
            "Да, подходит, записывайте. Записали вас на среду в 10:00."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_19255_to_official_dealer_eligibility_consultation_not_narrow_to(self):
        """19255: можно ли пройти ТО у вас или к официальному дилеру — справочный НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "Меня зовут Владимир, владелец автомобиля, пока ещё по гарантии. "
            "Скажите, пожалуйста, ТО у вас можно сейчас проходить или надо к официальному дилеру? "
            "Пока автомобиль на гарантии, советуем к официальному дилеру обращаться, "
            "так как мы не сможем поставить отметку о техническом обслуживании "
            "и будет у вас как непройденное ТО. Всё, понял, спасибо. До свидания."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "to_official_dealer_eligibility_consultation",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(
            reason,
            "to_official_dealer_eligibility_consultation_not_narrow_to",
        )

    def test_19605_jetour_official_dealer_eligibility_not_narrow_to(self):
        """19605: Jetour без цены/слота; ТО рекомендуют у официального дилера — НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "У вас сейчас не обслуживается что ли ДжиТО? "
            "Если автомобиль гарантийный, все вопросы по гарантии и ТО рекомендуем "
            "проходить у официального дилера в Самаре. "
            "То есть я не могу у вас ТО пройти? "
            "Можете, но официальной отметки о техническом обслуживании не будет. "
            "Подскажите телефоны дилеров в Самаре. Спасибо."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(
            reason,
            "to_official_dealer_eligibility_consultation_not_narrow_to",
        )

    def test_to_dealer_eligibility_with_slot_and_price_stays_narrow_to(self):
        """Вопрос про дилера не снимает ТО, если обсуждают цену и слот."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия. Здравствуйте. "
            "ТО у вас можно проходить или только у официального дилера? "
            "Можно у нас. Сколько стоит третье ТО? 18 500. "
            "Запишите на завтра в 10:00. Записали вас на завтра в 10:00."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def _transcript_from_log(self, call_id: int) -> str:
        with open("/home/vikingi/VikingiAll/logs/vikingi_autofetch.log") as f:
            log = f.read()
        return (
            log.split(f"call_id={call_id}")[1]
            .split("--- Транскрипт ---")[1]
            .split("---")[0]
            .strip()
        )

    def test_15791_jetour_x70plus_other_brand(self):
        """15791: Jetour X70+ (STT ЖТУР/Джитуру) — other_brand, не Чери из приветствия."""
        t = self._transcript_from_log(15791)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertEqual(dims.get("work_type"), "to")

    def test_15824_warranty_tail_light_replacement_not_narrow_to(self):
        """15824: поступил фонарь багажника — замена по гарантии, НЕ_ТО, прочие работы."""
        t = self._transcript_from_log(15824)
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "arrived_part_replacement_not_narrow_to")

    def test_15562_jetour_dashing_geturdashing_other_brand(self):
        """15562: STT GeTurdashing = Jetour Dashing — other_brand, не Чери из приветствия."""
        t = self._transcript_from_log(15562)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("OTHER", "OTHER"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")

    def test_16295_jetour_t2_spare_parts_inquiry_not_to(self):
        """16295: Jetour T2 (STT джиtur) — отдел ЗЧ, рулевой наконечник; other_brand, прочие, не ТО."""
        t = TestStoToRubricNarrow()._transcript_from_log(16295)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_intent"), "spare_parts_inquiry")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "jetour_not_narrow_to")

    def test_16318_ford_smax_other_brand_not_nissan_history(self):
        """16318: Ford S-Max (STT «форд», «Форд СМакс») — other_brand; не Nissan из «обслуживали форды»."""
        t = TestStoToRubricNarrow()._transcript_from_log(16318)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        ev = dims.get("evidence") or {}
        self.assertIn("форд", (ev.get("brand_hit") or "").lower())

    def test_16320_insurance_body_inspection_not_to(self):
        """16320: страховой случай, осмотр зеркала/молдинга — кузовной, не ТО от «выходные, то»."""
        t = TestStoToRubricNarrow()._transcript_from_log(16320)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "body_shop")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "insurance_claim_inspection")
        self.assertTrue(dims.get("is_booking"))
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_16387_brake_pad_replacement_booking_not_diagnostics(self):
        """16387: запись на замену тормозных колодок — прочие, не диагностика от «не работает» про сотрудника."""
        t = TestStoToRubricNarrow()._transcript_from_log(16387)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertTrue(dims.get("is_booking"))
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_intent"), "brake_pad_replacement")
        self.assertEqual(dims.get("service_brand"), "nissan")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_27443_opening_to_alias_but_brake_pad_booking_is_not_narrow_to(self):
        """27443: «на техобслуживание» в открытии, но фактически запись на замену колодок — НЕ_ТО/прочие."""
        t = self._transcript_from_log(27443)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "brake_pad_replacement")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "brake_pad_replacement_not_narrow_to")

    def test_27333_new_to_booking_with_side_complaint_stays_sto_to_in(self):
        """27333: есть жалоба, но согласован новый слот на объёмное ТО — остаётся СТО_ТО_вх."""
        t = self._transcript_from_log(27333)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_27557_to_price_composition_and_nearest_slot_offer_is_sto_to_in(self):
        """27557: марка+пробег+состав/цена ТО + ближайшая запись/приглашение -> СТО_ТО_вх."""
        t = self._transcript_from_log(27557)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_16395_nissan_qashqai_about_vehicle_early_brand(self):
        """16395: «звонил по поводу Nissan Кашкай» — nissan, не other_brand после перевода на мастера."""
        t = TestStoToRubricNarrow()._transcript_from_log(16395)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("brand_zone"), "early_client_vehicle")
        self.assertIn("nissan", (ev.get("brand_hit") or "").lower())

    def test_greeting_chery_only_not_service_brand(self):
        """Марка не берётся из приветствия дилера без названия авто клиентом."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса Андреева Юлия, слушаю вас. "
            "Здравствуйте. Подскажите, пожалуйста, режим работы в субботу? "
            "Нет, в субботу мы не работаем. Спасибо, до свидания."
        )
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "other_brand")

    def test_15825_mudguard_install_quote_not_narrow_to(self):
        """15825: стоимость установки брызговиков Tenet 7 — НЕ_ТО, прочие работы."""
        t = self._transcript_from_log(15825)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_15927_missed_master_call_car_at_service_not_narrow_to(self):
        """15927: перезвон по пропущенному, машина на сервисе, мастер звонил по готовности — НЕ_ТО."""
        t = self._transcript_from_log(15927)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertTrue((dims.get("evidence") or {}).get("car_already_at_service"))
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "missed_dealer_call_inquiry_not_narrow_to")

    def test_17498_missed_call_then_zero_to_slot_is_sto_to_in(self):
        """17498: «пропущенный» в начале, затем нулевое ТО Tenet T4 + слот/смета — СТО_ТО_вх."""
        t = self._transcript_from_log(17498)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertNotEqual(reason, "missed_dealer_call_inquiry_not_narrow_to")

    def test_17231_future_to_dropoff_crm_lookup_not_car_at_service(self):
        """17231: загнать на техобслуживание + CRM «там у вас машина» — ТО, не car_at_service."""
        t = self._transcript_from_log(17231)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertFalse((dims.get("evidence") or {}).get("car_already_at_service"))
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_15930_pre_visit_confirmation_not_narrow_to(self):
        """15930: исх. уточнение записи на завтра — НЕ_ТО (pre_visit_confirmation)."""
        t = self._transcript_from_log(15930)
        dept, ct = classify_auto(t)
        self.assertIn(ct, ("STO_IN", "STO_OUT"))
        self.assertEqual(dept, "STO")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_intent"), "pre_visit_confirmation")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            ("pre_visit_confirmation", "pre_visit_confirmation_outbound"),
        )

    def test_16123_employment_recruitment_not_narrow_to(self):
        """16123: звонок по вакансии мастера-приёмщика — НЕ_ТО (ТО-1/ТО-2 = опыт, не запись)."""
        t = self._transcript_from_log(16123)
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        self.assertEqual(ct, "STO_IN")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_intent"), "employment_recruitment")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "employment_recruitment_not_narrow_to")

    def test_16205_zero_to_reschedule_brand_chery_tenet(self):
        """16205: перенос нулевого ТО без названия модели — марка chery_tenet."""
        t = self._transcript_from_log(16205)
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("brand_method"), "zero_to_booking_chery_tenet_default")
        dept, ct = classify_auto(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_15493_admin_routing_complaint_then_seventh_to_booking_is_sto_to_in(self):
        """15493: жалоба на перевод + живая запись на 7-е ТО — СТО_ТО_вх, не no_actual_reception_contact."""
        t = self._transcript_from_log(15493)
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        self.assertEqual(ct, "STO_IN")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        self.assertNotEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "admin_no_dispatcher_callback",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_21254_reception_contact_with_zero_to_quote_is_sto_to_in(self):
        """21254: реальный диалог с диспетчером по нулевому ТО (регламент+стоимость), не no-contact."""
        t = (
            open("/home/vikingi/VikingiAll/logs/vikingi_autofetch.log")
            .read()
            .split("call_id=21254")[1]
            .split("--- Транскрипт ---")[1]
            .split("---")[0]
            .strip()
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertNotEqual(reason, "no_actual_reception_contact")

    def test_21379_outbound_regular_to_price_quote_stays_sto_to_out(self):
        """21379: «вы нам звонили» + стоимость очередного ТО по Nissan — СТО_ТО_исх."""
        t = (
            open("/home/vikingi/VikingiAll/logs/vikingi_autofetch.log")
            .read()
            .split("call_id=21379")[1]
            .split("--- Транскрипт ---")[1]
            .split("---")[0]
            .strip()
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_15513_quality_check_visit_not_work_type_to(self):
        """15513: перенос проверки качества по письму — quality_check, не ТО."""
        t = self._transcript_from_log(15513)
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        self.assertEqual(ct, "STO_IN")
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "quality_check")
        self.assertNotEqual(dims.get("work_type"), "to")
        self.assertFalse(dims.get("is_booking"))
        ev = dims.get("evidence") or {}
        self.assertEqual(ev.get("work_intent"), "quality_check_visit")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_15878_warranty_repair_booking_not_narrow_to(self):
        """15878: «записаться на гарантийный ремонт», стук рулевой — warranty, НЕ_ТО."""
        t = self._transcript_from_log(15878)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "warranty")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "warranty_repair_booking")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "inbound_warranty_complaint_booking_not_narrow_to")
        self.assertTrue(infer_sto_appointment_agreed(t, dims))

    def test_15880_stt_to3_price_quote_tiggo7_pro_narrow_to_in(self):
        """15880: «сколько Т 3 будет» на Chery Tiggo 7 Pro — СТО_ТО_вх."""
        t = self._transcript_from_log(15880)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "to_price_quote")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_15883_to3_mileage_due_volume_to_side_complaint_narrow_to_in(self):
        """15883: «время подошло на ТО-3», объёмное ТО + стук/стоп — СТО_ТО_вх."""
        t = self._transcript_from_log(15883)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")
        self.assertTrue(infer_sto_appointment_agreed(t, dims))

    def test_16146_first_to_price_quote_t8_booking_narrow_to_in(self):
        """16146: «Первое ТО, сколько будет стоить?» Tenet T8 — СТО_ТО_вх, не master follow-up."""
        t = self._transcript_from_log(16146)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "to_price_quote")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")
        self.assertTrue(infer_sto_appointment_agreed(t, dims))

    def test_16148_outbound_big_to_quote_and_booking_sto_to_out(self):
        """16148: исх. «ещё раз здравствуйте», смета большого ТО + запись — СТО_ТО_исх."""
        t = self._transcript_from_log(16148)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")
        self.assertTrue(infer_sto_appointment_agreed(t, dims))

    def test_16159_riza8_mixed_script_warranty_parts_sto_out(self):
        """16159: STT «Рiза 8» + запчасти по гарантии — chery_tenet, warranty, НЕ_ТО."""
        t = self._transcript_from_log(16159)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "warranty")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "arrived_part_replacement_not_narrow_to")

    def test_16199_chr_tg_7prmax_signal_after_to3_other_work(self):
        """16199: «Chr Tg 7PrMaрк», после ТО-3 сигнал не работает — chery_tenet, прочие работы."""
        t = self._transcript_from_log(16199)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "post_to_visit_signal_repair")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "post_to_visit_complaint_not_narrow_to")

    def test_16368_crm_to_lookup_next_to_booking_narrow_to_in(self):
        """16368: «посмотрите ТО», следующее ТО по пробегу + запись — СТО_ТО_вх."""
        t = self._transcript_from_log(16368)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")
        self.assertTrue(infer_sto_appointment_agreed(t, dims))

    def test_19988_first_ttoda_booking_is_narrow_to_in(self):
        """19988: STT «первое ттода» + запись на дату/время — СТО_ТО_вх."""
        t = self._transcript_from_log(19988)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        self.assertIn(
            (dims.get("evidence") or {}).get("work_intent"),
            ("to_booking", "to_price_quote"),
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")
        self.assertTrue(infer_sto_appointment_agreed(t, dims))

    def test_20032_to_105_to_120_volume_booking_is_narrow_to_in(self):
        """20032: «ТО-105 -> ТО-120, объёмное ТО» + запись на слот — СТО_ТО_вх."""
        t = self._transcript_from_log(20032)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")
        self.assertTrue(infer_sto_appointment_agreed(t, dims))

    def test_20065_prignat_na_tehobsluzhivanie_and_book_now_is_narrow_to_in(self):
        """20065: «пригнать на техобслуживание» + «можем сейчас записать» — СТО_ТО_вх."""
        t = self._transcript_from_log(20065)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")
        self.assertTrue(infer_sto_appointment_agreed(t, dims))

    def test_20168_only_just_spoke_repeat_continuation_not_narrow_to(self):
        """20168: «только что говорили» — продолжение оборванного разговора, НЕ_ТО."""
        t = self._transcript_from_log(20168)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_hit"), "immediate_repeat_continuation")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_20243_existing_to_reschedule_is_not_narrow(self):
        """20243: уже записана на ТО, перенос времени/дня — НЕ_ТО."""
        t = self._transcript_from_log(20243)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "existing_visit_clarification")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "existing_to_reschedule_not_narrow_to",
                "existing_visit_clarification_not_narrow_to",
                "appointment_cancellation_not_narrow_to",
            ),
        )

    def test_20688_just_spoke_and_already_booked_slot_is_not_narrow(self):
        """20688: «сейчас с вами общался, записался на 7 августа» — повторный контакт, НЕ_ТО."""
        t = self._transcript_from_log(20688)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_hit"), "immediate_repeat_continuation")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            ("existing_visit_clarification_not_narrow_to", "booking_flow_not_narrow_to"),
        )

    def test_20689_existing_to_reschedule_request_is_not_narrow(self):
        """20689: «записывалась на ТО, можно запись перенести» — перенос существующей записи, НЕ_ТО."""
        t = self._transcript_from_log(20689)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "existing_visit_clarification")
        self.assertEqual((dims.get("evidence") or {}).get("work_hit"), "booking_cancellation_or_reschedule")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "existing_to_reschedule_not_narrow_to")

    def test_23302_existing_to_reschedule_request_is_not_narrow(self):
        """23302: «записывалась на ТО ... можно перенести» — перенос существующей записи, НЕ_ТО."""
        t = self._transcript_from_log(23302)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "existing_to_reschedule_not_narrow_to",
                "past_to_followup_without_new_to_booking",
            ),
        )

    def test_22187_dashboard_key_indicator_without_new_to_booking_is_not_narrow(self):
        """22187: индикация ключа на приборке; без контекста новой записи на ТО — НЕ_ТО."""
        t = self._transcript_from_log(22187)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "non_to_issue_with_incidental_to_reference",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_22188_existing_zero_to_booking_with_certificate_question_is_not_narrow(self):
        """22188: запись на ТО-0 уже есть; звонок уточняет сертификат/бесплатно — НЕ_ТО."""
        t = self._transcript_from_log(22188)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "existing_visit_clarification",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_22191_dashboard_key_indicator_consultation_is_not_narrow(self):
        """22191: обсуждают индикатор ключа на приборке; без записи на ТО — НЕ_ТО."""
        t = self._transcript_from_log(22191)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "non_to_issue_with_incidental_to_reference",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_22275_existing_to_booking_duration_question_is_not_narrow(self):
        """22275: запись уже есть; спрашивают только длительность работ — НЕ_ТО."""
        t = self._transcript_from_log(22275)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "existing_visit_clarification",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_22213_outbound_second_to_callback_is_sto_to_out(self):
        """22213: исходящий перезвон по заявке, согласован слот на второе ТО — СТО_ТО_исх."""
        t = self._transcript_from_log(22213)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")

    def test_22229_warranty_part_expected_delivery_status_is_warranty(self):
        """22229: запчасть по гарантии «должна прийти / пришла-не пришла» — вид работ гарантия."""
        t = self._transcript_from_log(22229)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "warranty")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "warranty_parts_replacement")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_22301_explicit_wanted_to_book_to_is_narrow_to_in(self):
        """22301: «хотел записаться на ТО» — СТО_ТО_вх, не консультация по жидкостям."""
        t = self._transcript_from_log(22301)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_22361_price_only_without_progress_markers_is_not_narrow_to(self):
        """22361: только запрос стоимости ТО без прогресса записи — НЕ_ТО."""
        t = self._transcript_from_log(22361)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "insufficient_sto_to_markers_not_narrow_to")

    def test_26378_to_price_scope_with_own_parts_keeps_to_work_type(self):
        """26378: расчет 4-го ТО + состав/условия по фильтрам — вид работ ТО."""
        t = self._transcript_from_log(26378)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertEqual(dims.get("service_brand"), "chery_tenet")

    def test_26442_service_specs_with_three_plus_to_markers_is_sto_to_in(self):
        """26442: справочная консультация по ТО с >=3 маркерами должна попадать в СТО_ТО_вх."""
        t = self._transcript_from_log(26442)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_26108_to_plus_additional_work_keeps_to_work_type_when_narrow_not_to(self):
        """26108: обсуждают 9-е ТО + мойку радиаторов; узкий НЕ_ТО не должен сбрасывать вид работ ТО."""
        from call_analytics.sto_booking_dimensions import align_sto_work_type_with_narrow_rubric

        t = self._transcript_from_log(26108)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "insufficient_sto_to_markers_not_narrow_to")
        wt, _ = align_sto_work_type_with_narrow_rubric(
            dims.get("work_type"),
            rub,
            t,
            evidence=dims.get("evidence") if isinstance(dims.get("evidence"), dict) else {},
        )
        self.assertEqual(wt, "to")

    def test_22368_repeat_call_existing_application_non_to_work_is_not_narrow(self):
        """22368: повторный звонок по уже найденной заявке и замене дисков/колодок — НЕ_ТО."""
        t = self._transcript_from_log(22368)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "inbound_repeat_existing_non_to_application_not_narrow_to")

    def test_22452_first_to_price_only_is_not_narrow(self):
        """22452: только «сколько стоит первое ТО» без прогресса записи — НЕ_ТО."""
        t = self._transcript_from_log(22452)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "insufficient_sto_to_markers_not_narrow_to")

    def test_22479_warranty_body_defect_booking_not_to_work_type(self):
        """22479: вздулась пленка + инженер гарантии — вид работ гарантия, не to."""
        t = self._transcript_from_log(22479)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "warranty")
        self.assertTrue(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "warranty_repair_booking")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_22457_tcelo_phrase_maps_to_to_and_narrow_to_in(self):
        """22457: STT «ТЦеЛО пройти» должен трактоваться как «ТО пройти»."""
        t = self._transcript_from_log(22457)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")

    def test_21175_outbound_existing_booking_cancellation_not_narrow(self):
        """21175: исходящий перезвон по существующей записи, клиент просит отменить/отбой — НЕ_ТО."""
        t = self._transcript_from_log(21175)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "existing_visit_clarification")
        self.assertEqual((dims.get("evidence") or {}).get("work_hit"), "booking_cancellation_or_reschedule")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "outbound_existing_to_reschedule_not_narrow_to",
                "existing_to_cancellation_not_narrow_to",
                "existing_to_reschedule_not_narrow_to",
            ),
        )

    def test_21227_car_already_in_service_status_with_master_is_not_narrow(self):
        """21227: машина уже на нулевом ТО, клиент уточняет статус у мастера — НЕ_ТО."""
        t = self._transcript_from_log(21227)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "car_pickup_status")
        self.assertEqual((dims.get("evidence") or {}).get("work_hit"), "car_at_service")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_21150_nto_stt_inbound_to_booking_with_side_work_is_narrow_to_in(self):
        """21150: «хотел записать ... НТО» + регламент ТО + слот + доп.работы — СТО_ТО_вх."""
        t = self._transcript_from_log(21150)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_21160_jetour_with_confirmed_new_slot_stays_narrow_to_in(self):
        """21160: Jetour, но подтверждён новый слот ТО в этом звонке — СТО_ТО_вх."""
        t = self._transcript_from_log(21160)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_21212_warranty_engineer_required_sets_warranty_work_type(self):
        """21212: запчасти поступили, нужен инженер по гарантии, согласован слот — вид работ гарантия."""
        t = self._transcript_from_log(21212)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "warranty")
        self.assertTrue(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "warranty_repair_booking")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "arrived_part_replacement_not_narrow_to")

    def test_20317_to_2000_particle_and_past_to_brake_thickness_not_narrow(self):
        """20317: «то 2 000» + «на ТО в марте остаточная толщина» — НЕ_ТО."""
        t = (
            "Викинги Чери, диспетчер сервиса Юлия, здравствуйте. "
            "Смотрите, по тормозам: на ТО в марте измеряли остаточную толщину, "
            "была не такая большая по тормозным дискам. "
            "Тогда сказали, что если менять, то 2 000 рублей по работе. "
            "Я пока просто уточняю, без записи."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertNotEqual(dims.get("work_type"), "to")
        self.assertFalse(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_20165_explicit_zapishite_na_to_with_slot_is_narrow_to_in(self):
        """20165: «Запишите на ТО» + согласованный слот должны давать СТО_ТО_вх."""
        t = self._transcript_from_log(20165)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_20445_outbound_repeat_credit_followup_is_other(self):
        """20445: повторный исходящий ОП по вчерашним расчётам/заявке — Прочие."""
        t = self._transcript_from_log(20445)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("OTHER", "OTHER"))

    def test_20417_direct_op_manager_sales_consult_is_op_in(self):
        """20417: прямой разговор с менеджером ОП про цену/кредит — ОП_вх."""
        t = self._transcript_from_log(20417)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("OP", "OP_IN"))

    def test_20581_rental_context_is_other_not_op_in(self):
        """20581: арендный контекст и завершение без сделки — Прочие."""
        t = self._transcript_from_log(20581)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("OTHER", "OTHER"))

    def test_20662_fragment_without_manager_handoff_or_car_choice_is_other(self):
        """20662: шумный короткий фрагмент без перевода на ОП и без выбора авто — Прочие."""
        t = self._transcript_from_log(20662)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("OTHER", "OTHER"))

    def test_20770_just_spoke_and_line_dropped_continuation_is_other(self):
        """20770: «только что разговаривала» + «связь прервалась» — повторный перезвон, Прочие."""
        t = self._transcript_from_log(20770)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("OTHER", "OTHER"))

    def test_20819_repeat_followup_beginning_of_month_is_other(self):
        """20819: «вы сказали в начале августа… звоню узнать» — повторный исходящий follow-up, Прочие."""
        t = self._transcript_from_log(20819)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("OTHER", "OTHER"))

    def test_20840_admin_handoff_to_op_manager_then_drop_is_other(self):
        """20840: перевод на менеджера ОП с обрывом на «алло/слышно» — Прочие."""
        t = self._transcript_from_log(20840)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("OTHER", "OTHER"))

    def test_20978_credit_department_callback_without_sales_cycle_is_other(self):
        """20978: запрос кредитного отдела + взяли номер, без продажи авто — Прочие."""
        t = self._transcript_from_log(20978)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("OTHER", "OTHER"))

    def test_20877_incidental_to_reference_without_to_booking_is_not_narrow(self):
        """20877: «до ТО ещё 3000» в контексте неисправности — НЕ_ТО."""
        t = self._transcript_from_log(20877)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_hit"),
            "incidental_to_reference_non_booking_issue",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_20889_recall_update_then_do_with_to_later_is_not_narrow(self):
        """20889: отзывная прошивка/акция, «потом вместе с ТО» — НЕ_ТО, марка Tenet."""
        t = self._transcript_from_log(20889)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_hit"),
            "recall_software_update_consultation",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_21324_recall_update_deferred_to_first_to_free_is_warranty_not_to(self):
        """21324: ТО прошло, отзывное бесплатное обновление позже на первом ТО — НЕ_ТО, гарантия."""
        t = (
            "Викинги Чери, диспетчер сервиса. "
            "Можем пока отложить до первого ТО, когда первое ТО у вас будет на пробеге 10 000, "
            "мы вам бесплатно его обновим и будет пользоваться."
        )
        dept, ct = classify_auto(t)
        self.assertEqual(dept, "STO")
        self.assertIn(ct, ("STO_IN", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "warranty")
        self.assertFalse(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_intent"),
            "recall_update_consultation_without_to_booking",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_20919_today_booked_at_two_oclock_is_existing_slot_not_narrow(self):
        """20919: «сегодня записана на 2 часа на ТО» + уточнение длительности — НЕ_ТО."""
        t = self._transcript_from_log(20919)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertFalse(dims.get("is_booking"))
        self.assertIn(
            (dims.get("evidence") or {}).get("work_intent"),
            ("pre_visit_confirmation", "existing_visit_clarification"),
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")

    def test_21023_seo_teo_stt_maps_to_to_and_narrow_to_in(self):
        """21023: «записаться на СЭО ... 50 000 ТЭО» — СТО_ТО_вх."""
        t = self._transcript_from_log(21023)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_20857_crm_new_to_booking_not_cancel_hypothesis_is_narrow_to_out(self):
        """20857: «кто-то перенесет/отменит» как поиск окна — это не отмена записи клиента."""
        t = self._transcript_from_log(20857)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_21455_outbound_zero_to_crm_confirmation_is_sto_to_out(self):
        """21455: исходящий CRM по нулевому ТО с «всё ли верно/актуально» — СТО_ТО_исх."""
        t = self._transcript_from_log(21455)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_20967_inbound_third_to_booking_with_slot_confirmation_is_narrow_to_in(self):
        """20967: заявка на ТО-3 + согласованный слот 11 августа 14:00 — СТО_ТО_вх."""
        t = self._transcript_from_log(20967)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_20895_seven_pro_inbound_to_booking_is_narrow_to_in(self):
        """20895: «Семь Про» + ТО-6 на 60 000 и подтвержденный слот — СТО_ТО_вх."""
        t = self._transcript_from_log(20895)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_20421_own_parts_topic_with_confirmed_slot_is_narrow_to_in(self):
        """20421: даже при вопросе про свои расходники есть подтверждённая запись на слот."""
        t = self._transcript_from_log(20421)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_20420_second_to_price_timing_and_slot_booking_is_narrow_to_in(self):
        """20420: второе ТО, цена/время и итоговая запись на слот — СТО_ТО_вх."""
        t = self._transcript_from_log(20420)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_21568_second_to_20k_with_agreed_slot_is_narrow_to_in(self):
        """21568: «на ТО записаться», второе/20 000 км и согласованный слот 13-е 7:50 — СТО_ТО_вх."""
        t = self._transcript_from_log(21568)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_21590_explicit_to_booking_with_confirmed_slot_is_narrow_to_in(self):
        """21590: явная запись на ТО подтверждена слотом — СТО_ТО_вх."""
        t = self._transcript_from_log(21590)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_21628_to_booking_with_own_filters_question_keeps_narrow_to_in(self):
        """21628: вопрос про свои фильтры после согласования слота не должен уводить в НЕ_ТО."""
        t = self._transcript_from_log(21628)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_25877_outbound_to_slot_then_own_filters_stays_sto_to_out(self):
        """25877: после согласования 11:30 вопрос про фильтры не уводит в НЕ_ТО."""
        t = self._transcript_from_log(25877)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_OUT", msg=f"expected СТО_ТО_исх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_21636_zero_to_hyphen_phrase_is_narrow_to_in(self):
        """21636: «на нулевой-то записаться» — явная запись на нулевое ТО (СТО_ТО_вх)."""
        t = self._transcript_from_log(21636)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО_вх, got {rub} ({reason})")
        self.assertEqual(reason, "ok")

    def test_21653_chi_semel_maps_to_chery_tenet(self):
        """21653: STT «чи семель» (семель -> 7L) должен давать марку Chery/Tenet."""
        t = self._transcript_from_log(21653)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "chery_tenet")
        self.assertEqual(dims.get("work_type"), "diagnostics")

    def test_24184_nissan_extrall_maps_to_nissan_brand(self):
        """24184: STT «Nissan EXtrall / Extreйл» = Nissan X-Trail, не other_brand."""
        t = (
            "Викинги Чери, диспетчер сервис Юлия. Здравствуйте. "
            "Подскажите, пожалуйста, ТО-3 у Nissan EXtrall сколько будет стоить? "
            "Nissan Extreйл, полный привод 2,5 литра."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("service_brand"), "nissan")
        self.assertEqual(dims.get("work_type"), "to")

    def test_24484_existing_slot_reschedule_is_not_narrow_to(self):
        """24484: существующая запись подтверждена накануне, клиент переносит визит."""
        t = (
            "Алло. Викинги Чери, диспетчер сервиса Юлия. "
            "Мы вот, к сожалению, вчера, 27-го числа, должны были приехать на проверку автомобиля. "
            "Запись была. Мы накануне звонили, подтверждали запись. "
            "Есть возможность записать на какие-то ближайшие дни еще раз? "
            "Тогда запишите на третье число на девять часов."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "existing_to_reschedule_not_narrow_to",
                "past_to_followup_without_new_to_booking",
            ),
        )

    def test_27786_existing_to_asks_earlier_today_slot_is_not_narrow_to(self):
        """27786: уже записан на ТО, просит более раннее окно сегодня — НЕ_ТО."""
        t = self._transcript_from_log(27786)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "existing_to_reschedule_not_narrow_to")

    def test_24614_history_to_context_with_defect_is_not_narrow_to(self):
        """24614: «на нулевом ТО» как история + дефект/запись к инженеру — НЕ_ТО."""
        t = (
            "Чери Викинги на Заставной, ассистент сервиса. "
            "У меня на руле пластик разошелся, нужно посмотреть. "
            "Это нужно чтобы инженер по гарантии посмотрел, "
            "гарантийный случай или нет. Нужно записываться. "
            "Ранее у вас обслуживались? На нулевом ТО. "
            "Давайте запишемся на шестое число на одиннадцать."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "historical_to_context_without_new_booking_not_narrow_to",
                "post_to_visit_complaint_not_narrow_to",
                "inbound_warranty_complaint_booking_not_narrow_to",
            ),
        )

    def test_25632_false_ordinal_to_defect_diagnostics_not_narrow_to(self):
        """25632: ложный ordinal+ТО из даты/времени на фоне дефекта — НЕ_ТО."""
        t = (
            "Д Алло? Алло, Валерий Николаевич, здравствуйте. что Дилерский центр Викинги, "
            "диспетчер сервиса Юлия. Ваш телефон передали коллеге. "
            "По аккумуляторной батарее, да, у вас вопрос. "
            "У меня машина, я приезжаю в гараж, открываю дверь, а она никаких огней нет, "
            "и она не заводится. Такое было два раза. "
            "Второй вопрос: задняя скорость плохо включается, то трещит, то не включается. "
            "Давайте запишем. Вы можете в будние дни подъехать к нам с автомобилем? "
            "Ну вот девятого, в среду у вас диагност свободный, в обед, 13:00. "
            "Ой, я уезжаю девятого и приеду неизвестно когда. "
            "Восьмого, седьмого Т Так, у вас какое. Так, седьмого, восьмого мог бы подъехать? "
            "А 7-го числа в понедельник, в 13:30, полвторого, сможете подъехать? "
            "Да. Давайте в понедельник в полвторого приезжайте. Посмотрим, что там. "
            "Всё, записали. В понедельник будем ждать. Полвторого, приезжайте."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_OUT"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        self.assertTrue(dims.get("is_booking"))
        self.assertEqual(
            (dims.get("evidence") or {}).get("work_hit"),
            "седьмогоое то",
        )
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "false_ordinal_to_with_primary_defect_not_narrow_to")

    def test_25572_historical_to_with_defect_repair_booking_not_narrow_to(self):
        """25572: ТО-2 и «предыдущее ТО» — история CRM; запись на ремонт дефектов."""
        t = (
            "Викинги Чери, диспетчер сервис. Юлия. Здравствуйте. Юлия, добрый день. "
            "Хотелось бы записаться на ремонт. Угу, слушаю. Какие работы? "
            "Кнопка массажа у пассажира не работает, не срабатывает. "
            "И ещё память сидений не работает, постоянно приходится заново "
            "регулировать кресла и зеркала. Напомните фамилию владельца автомобиля? "
            "Сейчас минуточку, я посмотрю по записи у вас, где окно. "
            "На предыдущем ТО мы вот вас только что сделали ТО, и на ТО-2, "
            "когда мы делали у вас, эту проблему заявляли, но вам ошибок ничего не выдал. "
            "У вас сейчас один диагност уходит в отпуск. Ближайшее предложу 17-го числа. "
            "Давайте к девяти. Тогда записали на 17 сентября, четверг, в 9:00."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(
            reason, "historical_to_context_without_new_booking_not_narrow_to"
        )

    def test_25572_new_to_booking_with_defect_stays_narrow_to(self):
        """Контроль: запись именно на ТО-2 + попутный дефект остаётся СТО_ТО."""
        t = (
            "Викинги Чери, диспетчер сервис. Юлия. Здравствуйте. "
            "Хотелось бы записаться на ТО-2. Пробег 30 тысяч. "
            "Сколько будет стоить второе ТО? И ещё кнопка массажа не работает. "
            "Записали вас на 17 сентября, четверг, в 9:00."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО, got {rub} ({reason})")

    def test_25632_genuine_ordinal_to_still_stays_narrow_to(self):
        """Подтверждаем, что настоящее 'седьмое ТО' с дефектом остаётся СТО_ТО."""
        t = (
            "Викинги, диспетчер сервиса. Здравствуйте. Хотел записаться на седьмое ТО. "
            "Скажите, пожалуйста, стоимость седьмого ТО. "
            "А ещё у меня аккумулятор сел, машина не заводится. "
            "Давайте запишем на седьмое сентября в десять. Записали."
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "to")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertEqual(rub, "STO_TO_IN", msg=f"expected СТО_ТО, got {rub} ({reason})")

    def test_long_post_goodbye_tail_with_false_to_marker_is_not_narrow_to(self):
        """Длинный хвост после прощания не должен тащить звонок в СТО_ТО."""
        t = (
            "Алло, добрый день. По поводу ремонта, можно согласовать дату? "
            "Давайте на следующую среду, в два часа. "
            "Хорошо, записал вас. Всего доброго, до свидания. "
            "А это Женька там что-то бронирует, блин, пишет чужие фамилии. "
            "Мы тут по складу смотрим, яблоки поели, мыши бегают, дом облетает, новости обсуждаем. "
            "Вообще первое то смотрим по таблице, там цифры пляшут, но это не про запись клиента. "
            "Еще про дачу, страховку, беспилотники и разное рабочее обсуждение."
        )
        dept, ct = classify_auto(t)
        self.assertIn((dept, ct), (("STO", "STO_IN"), ("OTHER", "OTHER")))
        ct_for_rubric = "STO_IN"
        dims = infer_sto_booking_dimensions(t)
        # На полном тексте dimensions может поймать ложный TO-маркер из хвоста.
        self.assertTrue((dims.get("evidence") or {}).get("work_hit"))
        rub, reason = infer_sto_to_rubric_type(t, ct_for_rubric, dims, department="STO")
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertIn(
            reason,
            (
                "no_explicit_to_topic",
                "insufficient_sto_to_markers_not_narrow_to",
                "non_reg_service_topic",
            ),
        )

    def test_26671_retracted_to_then_diagnostics_is_not_narrow_to(self):
        """26671: «хотела на ТО ... потом нет, надо посмотреть» -> НЕ_ТО, диагностика."""
        t = self._transcript_from_log(26671)
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "diagnostics")
        self.assertTrue(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "to_retracted_to_diagnostics")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "to_intent_retracted_non_reg_work")

    def test_retracted_to_then_oil_change_is_other_work_not_narrow_to(self):
        """«Хотел на ТО, но решил масло заменить» -> НЕ_ТО, прочие работы."""
        t = (
            "Викинги, диспетчер сервиса, здравствуйте. "
            "Хотел на ТО записаться, но решил масло заменить и фильтр поменять. "
            "Можно завтра на одиннадцать?"
        )
        dept, ct = classify_auto(t)
        self.assertEqual((dept, ct), ("STO", "STO_IN"))
        dims = infer_sto_booking_dimensions(t)
        self.assertEqual(dims.get("work_type"), "other_work")
        self.assertTrue(dims.get("is_booking"))
        self.assertEqual((dims.get("evidence") or {}).get("work_intent"), "to_retracted_to_other_work")
        rub, reason = infer_sto_to_rubric_type(t, ct, dims, department=dept)
        self.assertIsNone(rub, msg=f"expected НЕ_ТО, got {rub} ({reason})")
        self.assertEqual(reason, "to_intent_retracted_non_reg_work")


if __name__ == "__main__":
    unittest.main()
