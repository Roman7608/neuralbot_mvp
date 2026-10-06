"""Тесты фильтра MAX-уведомлений о записи на ТО из голосового бота."""

import os
import unittest

from max_bot.voice_service_booking_notify import (
    _compose_booking_need_text,
    _compose_car_label,
    should_skip_voice_booking_max_notify,
)


class VoiceServiceBookingMaxNotifySkipTest(unittest.TestCase):
    def setUp(self) -> None:
        self._env_backup = os.environ.get("VOICE_MAX_SKIP_NOTIFY_PHONES")

    def tearDown(self) -> None:
        if self._env_backup is None:
            os.environ.pop("VOICE_MAX_SKIP_NOTIFY_PHONES", None)
        else:
            os.environ["VOICE_MAX_SKIP_NOTIFY_PHONES"] = self._env_backup

    def test_skip_when_client_named_test_phone(self) -> None:
        self.assertTrue(should_skip_voice_booking_max_notify("+79023730808"))
        self.assertTrue(should_skip_voice_booking_max_notify("89023730808"))
        self.assertTrue(should_skip_voice_booking_max_notify("9023730808"))

    def test_no_skip_when_client_named_other_phone(self) -> None:
        self.assertFalse(should_skip_voice_booking_max_notify("+79272627370"))
        self.assertFalse(should_skip_voice_booking_max_notify("9272627370"))

    def test_no_skip_when_phone_empty(self) -> None:
        self.assertFalse(should_skip_voice_booking_max_notify(None))
        self.assertFalse(should_skip_voice_booking_max_notify(""))

    def test_custom_skip_list_from_env(self) -> None:
        os.environ["VOICE_MAX_SKIP_NOTIFY_PHONES"] = "+79991112233"
        self.assertTrue(should_skip_voice_booking_max_notify("89991112233"))
        self.assertFalse(should_skip_voice_booking_max_notify("+79023730808"))


class VoiceServiceBookingCarLabelTest(unittest.TestCase):
    def test_compose_car_label_with_brand_and_model(self) -> None:
        self.assertEqual(_compose_car_label("Chery", "Tiggo 7 Pro"), "Chery Tiggo 7 Pro")

    def test_compose_car_label_ignores_empty_parts(self) -> None:
        self.assertEqual(_compose_car_label("Chery", ""), "Chery")
        self.assertEqual(_compose_car_label("", "Tiggo 7"), "Tiggo 7")
        self.assertEqual(_compose_car_label("", ""), "")

    def test_need_text_uses_single_mileage_value(self) -> None:
        msg = _compose_booking_need_text(
            car_label="Tenet T7",
            mileage_value="4300",
            slot_str="29.08 в 15:30",
            work_wishes="ТО-6",
        )
        self.assertIn("авто: Tenet T7", msg)
        self.assertIn("пробег: 4300", msg)
        self.assertNotIn("пробег raw:", msg)

    def test_need_text_uses_raw_car_label_when_norm_is_absent(self) -> None:
        msg = _compose_booking_need_text(
            car_label="Тена7",
            mileage_value="4300",
            slot_str="29.08 в 15:30",
            work_wishes="ТО-6",
        )
        self.assertIn("авто: Тена7", msg)


if __name__ == "__main__":
    unittest.main()
