"""Тесты подбора слотов и разбора части дня."""

import asyncio
import sys
import types
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch

from dialog.bot_logic import (
    _voice_slot_not_suitable_stt,
    _voice_slot_plain_no_stt,
    _voice_slot_reject_stt,
)
from dialog.service_slot_day_part import (
    DAY_PART_WINDOWS,
    extract_day_part,
    resolve_weekday_booking_date,
    slot_needs_next_day_pickup,
    slot_start_in_window,
)
from services.voice.voice_phrases import (
    format_service_slot_offer_tts,
    format_slot_confirm_reprompt_tts,
)


def test_day_part_windows():
    assert DAY_PART_WINDOWS["morning"] == (8, 12)
    assert DAY_PART_WINDOWS["lunch"] == (12, 14)
    assert DAY_PART_WINDOWS["after_lunch"] == (14, 17)
    assert DAY_PART_WINDOWS["evening"] == (17, 20)


def test_extract_day_part_lunch_vs_after_lunch():
    assert extract_day_part("в обед") == "lunch"
    assert extract_day_part("после обеда") == "after_lunch"
    from dialog.service_speech_parse import normalize_stt_booking_date_text

    assert extract_day_part(normalize_stt_booking_date_text("Победа.")) == "after_lunch"
    assert extract_day_part("утром") == "morning"
    assert extract_day_part("утро") == "morning"
    assert extract_day_part("вечером") == "evening"


def test_slot_needs_next_day_pickup():
    assert not slot_needs_next_day_pickup("17:20")
    assert slot_needs_next_day_pickup("17:30")
    assert slot_needs_next_day_pickup("18:00")


def test_slot_offer_includes_next_day_note():
    text = format_service_slot_offer_tts(
        "семн+адцатое", "и+юня", "семь тридцать", slot_hhmm="19:30",
    )
    assert "следующий день" in text
    assert "Запишу Вас" in text


def test_slot_offer_mentions_unavailable_requested_date():
    """Если слот на другой день — сначала «нет свободных слотов»."""
    text = format_service_slot_offer_tts(
        "двадцать тр+етье",
        "и+юля",
        "восемь десять",
        slot_hhmm="08:10",
        unavailable_day_text="двадцать втор+ое",
        unavailable_month_tts="и+юля",
    )
    assert "нет свободных слотов" in text
    assert "двадцать втор+ое" in text
    assert "Запишу Вас на двадцать тр+етье" in text
    assert text.index("нет свободных слотов") < text.index("Запишу Вас")


def test_nearest_slot_offer_tts():
    from services.voice.voice_phrases import format_nearest_slot_offer_tts

    text = format_nearest_slot_offer_tts(
        "двадцать седьм+ое", "и+юля", "двенадцать ноль ноль", slot_hhmm="12:00",
    )
    assert "Ближайшее свободное" in text
    assert "Записать Вас" in text


def test_slot_confirm_reprompt_includes_date_time_when_provided():
    text = format_slot_confirm_reprompt_tts(
        day_text="двадцать дев+ятое",
        month_tts="+августа",
        time_tts="пятн+адцать ноль ноль",
    )
    assert "Запис+ать Вас на двадцать дев+ятое +августа" in text
    assert "в пятн+адцать ноль ноль" in text
    assert "записывай" in text


def test_plain_no_vs_not_suitable():
    assert _voice_slot_plain_no_stt("нет.")
    assert not _voice_slot_plain_no_stt("нет, не подходит")
    assert _voice_slot_not_suitable_stt("не подходит")
    assert _voice_slot_not_suitable_stt("другое время")
    assert not _voice_slot_plain_no_stt("нет, хочу в пятницу")
    assert _voice_slot_reject_stt("нет, не подходит")


def test_weekday_friday_plus_seven_if_today_friday():
    ref = datetime(2026, 6, 19)  # пятница
    assert ref.weekday() == 4
    result = resolve_weekday_booking_date("хочу в пятницу", ref)
    assert result is not None
    assert result.isoformat() == "2026-06-26"


def test_weekday_short_and_stt_forms_are_supported():
    ref = datetime(2026, 8, 9)  # воскресенье
    assert resolve_weekday_booking_date("Ээторник в 8:00", ref).isoformat() == "2026-08-11"
    assert resolve_weekday_booking_date("дельник утром", ref).isoformat() == "2026-08-10"
    assert resolve_weekday_booking_date("реда после обеда", ref).isoformat() == "2026-08-12"
    assert resolve_weekday_booking_date("етверг", ref).isoformat() == "2026-08-13"
    assert resolve_weekday_booking_date("тница", ref).isoformat() == "2026-08-14"
    assert resolve_weekday_booking_date("ббота", ref).isoformat() == "2026-08-15"
    assert resolve_weekday_booking_date("сенье", ref).isoformat() == "2026-08-16"


def test_day_to_ordinal_ru_accusative_for_booking():
    from dialog.bot_logic import day_to_ordinal_ru

    assert day_to_ordinal_ru(20) == "двадц+атое"
    assert day_to_ordinal_ru(24) == "двадцать четв+ертое"
    assert day_to_ordinal_ru(30) == "тридц+атое"


def test_booking_phones_include_caller():
    from dialog.bot_logic import _booking_phones_for_1c

    assert _booking_phones_for_1c(
        phone_input="+79023730101",
        caller_phone="+79023730808",
    ) == "+79023730101,+79023730808"
    assert _booking_phones_for_1c(
        phone_input="89023730101",
        caller_phone="+79023730101",
    ) == "+79023730101"


def test_enter_slot_await_new_date_clears_proposed():
    from dialog.bot_logic import enter_slot_await_new_date

    sd = {
        "proposed_date": "2026-06-25",
        "proposed_time": "08:00",
        "proposed_post": "ВК00000010",
        "offered_times": ["08:00"],
    }
    enter_slot_await_new_date(sd)
    assert sd["proposed_date"] is None
    assert sd["proposed_time"] is None
    assert sd.get("slot_await_new_date") is True
    assert sd.get("offered_times") == []


def test_slots_date_to_exclusive_one_and_two_days():
    from datetime import date
    from telegram_bot.services.ics_alfa_service import slots_date_to_exclusive

    d = date(2026, 2, 21)
    assert slots_date_to_exclusive(d, 1) == "2026-02-22"
    assert slots_date_to_exclusive(d, 2) == "2026-02-23"


def test_format_slots_around_date_tts():
    from datetime import date
    from dialog.service_slot_alternatives import format_slots_around_date_tts

    text = format_slots_around_date_tts(
        date(2026, 7, 2),
        before_date=date(2026, 7, 1),
        before_time="08:00",
        after_date=date(2026, 7, 9),
        after_time="08:00",
    )
    assert "нет свободных слотов" in text
    assert "раньше" in text
    assert "позже" in text

    only_before = format_slots_around_date_tts(
        date(2026, 7, 10),
        before_date=date(2026, 7, 9),
        before_time="08:00",
    )
    assert "нет свободных слотов" in only_before
    assert "Ближайший свободный слот до этой даты" in only_before
    assert "Назовите дату" in only_before


def test_slot_alt_stt_markers():
    from dialog.service_slot_alternatives import is_slot_alt_after_stt, is_slot_alt_before_stt

    assert is_slot_alt_before_stt("раньше")
    assert is_slot_alt_after_stt("позже")
    assert not is_slot_alt_before_stt("позже")
    # «тридцать первое» — это дата, а не «первый вариант».
    assert not is_slot_alt_before_stt("тридцать первое")


def test_slot_alt_entry_preserves_mechanic_name():
    from dialog.service_slot_alternatives import sd_entry_to_picked, slot_info_to_sd_entry

    class _Info:
        start = datetime(2026, 8, 20, 12, 0)
        post_id = "post-1"
        acceptor_id = "acc-1"
        mechanic_name = "Иванов Сергей"

    entry = slot_info_to_sd_entry(_Info())
    picked = sd_entry_to_picked(entry)
    assert picked is not None
    assert picked.mechanic_name == "Иванов Сергей"


def test_slots_around_today_never_searches_past_dates():
    from telegram_bot.services.service_booking_service import (
        find_slots_around_date_1c,
    )

    now = datetime(2026, 7, 27, 8, 30)
    calls: list[date] = []

    async def fake_slots(day, **_kwargs):
        calls.append(day)
        if day == date(2026, 7, 28):
            return [(datetime(2026, 7, 28, 9, 0), "post", "acceptor")]
        return []

    with (
        patch(
            "dialog.dealer_time.dealer_local_now_naive",
            return_value=now,
        ),
        patch(
            "telegram_bot.services.service_booking_service.list_slots_on_day_1c",
            new=AsyncMock(side_effect=fake_slots),
        ),
    ):
        before, after = asyncio.run(
            find_slots_around_date_1c(date(2026, 7, 27), search_days=45)
        )

    assert before is None
    assert after is not None
    assert after.start == datetime(2026, 7, 28, 9, 0)
    assert all(day >= now.date() for day in calls)


def test_slots_around_skips_current_sunday_slots():
    from telegram_bot.services.service_booking_service import find_slots_around_date_1c

    now = datetime(2026, 8, 9, 10, 0)  # воскресенье
    sunday = date(2026, 8, 9)
    monday = date(2026, 8, 10)

    async def fake_slots(day, **_kwargs):
        if day == sunday:
            return [(datetime(2026, 8, 9, 15, 40), "post-sun", "acc-sun")]
        if day == monday:
            return [(datetime(2026, 8, 10, 9, 0), "post-mon", "acc-mon")]
        return []

    with (
        patch("dialog.dealer_time.dealer_local_now_naive", return_value=now),
        patch(
            "telegram_bot.services.service_booking_service.list_slots_on_day_1c",
            new=AsyncMock(side_effect=fake_slots),
        ),
    ):
        before, after = asyncio.run(
            find_slots_around_date_1c(date(2026, 8, 8), search_days=5)
        )

    assert before is None
    assert after is not None
    assert after.start.date() == monday


def test_slots_around_respects_requested_time_and_day_part():
    from telegram_bot.services.service_booking_service import find_slots_around_date_1c

    now = datetime(2026, 8, 9, 10, 0)
    target = date(2026, 8, 10)

    async def fake_slots(day, **_kwargs):
        if day == date(2026, 8, 11):
            return [
                (datetime(2026, 8, 11, 8, 30), "post-am", "acc-am"),
                (datetime(2026, 8, 11, 18, 30), "post-pm", "acc-pm"),
            ]
        return []

    with (
        patch("dialog.dealer_time.dealer_local_now_naive", return_value=now),
        patch(
            "telegram_bot.services.service_booking_service.list_slots_on_day_1c",
            new=AsyncMock(side_effect=fake_slots),
        ),
    ):
        before, after = asyncio.run(
            find_slots_around_date_1c(
                target,
                search_days=5,
                time_window=(17, 20),
                min_time_hhmm="18:00",
            )
        )

    assert before is None
    assert after is not None
    assert after.start == datetime(2026, 8, 11, 18, 30)


def test_find_nearest_slot_1c_skips_current_sunday_slots():
    from telegram_bot.services.service_booking_service import find_nearest_slot_1c

    now = datetime(2026, 8, 9, 10, 0)  # воскресенье

    class _FakeIcsService:
        def __init__(self):
            self.initialized = True

        async def get_service_slots(self, **_kwargs):
            return [
                {
                    "date": "2026-08-09",
                    "acceptance_time": "15:40",
                    "post_id": "post-sun",
                    "acceptor_id": "acc-sun",
                },
                {
                    "date": "2026-08-10",
                    "acceptance_time": "09:00",
                    "post_id": "post-mon",
                    "acceptor_id": "acc-mon",
                },
            ]

        async def close(self):
            return None

    with (
        patch("dialog.dealer_time.dealer_local_now_naive", return_value=now),
        patch("telegram_bot.services.ics_alfa_service.ICSAlfaService", _FakeIcsService),
    ):
        slot, in_priority = asyncio.run(
            find_nearest_slot_1c(date(2026, 8, 8), days_priority=3)
        )

    assert slot is not None
    assert slot.start.date() == date(2026, 8, 10)
    assert in_priority is True


def test_find_nearest_slot_1c_prefers_half_hour_grid():
    from telegram_bot.services.service_booking_service import find_nearest_slot_1c

    now = datetime(2026, 8, 18, 11, 0)

    class _FakeIcsService:
        def __init__(self):
            self.initialized = True

        async def get_service_slots(self, **_kwargs):
            return [
                {
                    "date": "2026-08-18",
                    "acceptance_time": "15:41",
                    "post_id": "post-1",
                    "acceptor_id": "acc-1",
                },
                {
                    "date": "2026-08-18",
                    "acceptance_time": "16:00",
                    "post_id": "post-1",
                    "acceptor_id": "acc-1",
                },
            ]

        async def close(self):
            return None

    fake_ics_module = types.SimpleNamespace(
        ICSAlfaService=_FakeIcsService,
        slots_date_to_exclusive=lambda d, n: (d + timedelta(days=n)).strftime("%Y-%m-%d"),
    )
    with (
        patch("dialog.dealer_time.dealer_local_now_naive", return_value=now),
        patch.dict(sys.modules, {"telegram_bot.services.ics_alfa_service": fake_ics_module}),
    ):
        slot, in_priority = asyncio.run(
            find_nearest_slot_1c(date(2026, 8, 18), days_priority=2)
        )

    assert slot is not None
    assert slot.start == datetime(2026, 8, 18, 16, 0)
    assert in_priority is True


def test_find_nearest_slot_1c_ignores_non_half_hour_slots():
    from telegram_bot.services.service_booking_service import find_nearest_slot_1c

    now = datetime(2026, 8, 18, 11, 0)

    class _FakeIcsService:
        def __init__(self):
            self.initialized = True

        async def get_service_slots(self, **_kwargs):
            return [
                {
                    "date": "2026-08-18",
                    "acceptance_time": "15:41",
                    "post_id": "post-1",
                    "acceptor_id": "acc-1",
                },
                {
                    "date": "2026-08-18",
                    "acceptance_time": "15:50",
                    "post_id": "post-1",
                    "acceptor_id": "acc-1",
                },
            ]

        async def close(self):
            return None

    fake_ics_module = types.SimpleNamespace(
        ICSAlfaService=_FakeIcsService,
        slots_date_to_exclusive=lambda d, n: (d + timedelta(days=n)).strftime("%Y-%m-%d"),
    )
    with (
        patch("dialog.dealer_time.dealer_local_now_naive", return_value=now),
        patch.dict(sys.modules, {"telegram_bot.services.ics_alfa_service": fake_ics_module}),
    ):
        slot, in_priority = asyncio.run(
            find_nearest_slot_1c(date(2026, 8, 18), days_priority=1)
        )

    assert slot is None
    assert in_priority is False


def test_find_nearest_slot_1c_queries_long_range_in_three_day_chunks():
    from telegram_bot.services.service_booking_service import find_nearest_slot_1c

    requests = []

    class _FakeIcsService:
        def __init__(self):
            self.initialized = True

        async def get_service_slots(self, **kwargs):
            requests.append((kwargs["date_from"], kwargs["date_to"]))
            if kwargs["date_from"] == "2026-08-27":
                return [
                    {
                        "date": "2026-08-27",
                        "acceptance_time": "09:00",
                        "post_id": "post-27",
                        "acceptor_id": "acc-27",
                    }
                ]
            return []

        async def close(self):
            return None

    with (
        patch(
            "dialog.dealer_time.dealer_local_now_naive",
            return_value=datetime(2026, 8, 18, 11, 0),
        ),
        patch("telegram_bot.services.ics_alfa_service.ICSAlfaService", _FakeIcsService),
    ):
        slot, _in_priority = asyncio.run(
            find_nearest_slot_1c(date(2026, 8, 18), days_priority=30)
        )

    assert slot is not None
    assert slot.start == datetime(2026, 8, 27, 9, 0)
    assert requests == [
        ("2026-08-18", "2026-08-21"),
        ("2026-08-21", "2026-08-24"),
        ("2026-08-24", "2026-08-27"),
        ("2026-08-27", "2026-08-30"),
    ]


def test_find_nearest_slot_1c_can_distinguish_1c_error_from_no_slots():
    from telegram_bot.services.service_booking_service import find_nearest_slot_1c

    class _BrokenIcsService:
        def __init__(self):
            self.initialized = True

        async def get_service_slots(self, **_kwargs):
            raise RuntimeError("1C timeout")

        async def close(self):
            return None

    with patch(
        "telegram_bot.services.ics_alfa_service.ICSAlfaService",
        _BrokenIcsService,
    ):
        try:
            asyncio.run(
                find_nearest_slot_1c(
                    date(2026, 8, 18),
                    days_priority=30,
                    raise_on_error=True,
                )
            )
        except RuntimeError as exc:
            assert "timeout" in str(exc)
        else:
            raise AssertionError("Ошибка 1С не должна превращаться в пустой список слотов")


def test_create_booking_rejects_past_slot_before_1c_call():
    from telegram_bot.services.service_booking_service import create_1c_booking

    now = datetime(2026, 7, 27, 8, 30)
    with patch(
        "dialog.dealer_time.dealer_local_now_naive",
        return_value=now,
    ):
        result = asyncio.run(
            create_1c_booking(
                fio="Иван Иванов",
                phone="+79990000000",
                car_brand="Chery",
                car_model="Tiggo 7",
                car_year="2023",
                car_mileage="65000",
                work_wishes="ТО",
                slot_start=now - timedelta(days=1),
            )
        )
    assert result is None


def test_slot_start_in_window_after_lunch():
    start = datetime(2026, 6, 18, 14, 0)
    assert slot_start_in_window(start, 150, (14, 17))
    late = datetime(2026, 6, 18, 15, 30)
    assert not slot_start_in_window(late, 150, (14, 17))


def test_pick_slot_for_day_morning_prefers_morning_window():
    from dialog.service_slot_pick import pick_slot_for_day

    target = date(2026, 9, 15)

    async def fake_slots(_day, **_kwargs):
        return [
            (datetime(2026, 9, 15, 12, 0), "post-noon", "acc-noon", "Мех 1"),
            (datetime(2026, 9, 15, 9, 0), "post-morning", "acc-morning", "Мех 2"),
            (datetime(2026, 9, 15, 15, 0), "post-day", "acc-day", "Мех 3"),
        ]

    with patch("dialog.service_slot_pick.list_slots_on_day_1c", new=AsyncMock(side_effect=fake_slots)):
        picked = asyncio.run(
            pick_slot_for_day(
                target,
                150,
                day_part="morning",
            )
        )
    assert picked is not None
    assert picked.time_str == "09:00"
    assert picked.post_id == "post-morning"


def test_pick_slot_for_day_outside_window_no_type_error_and_returns_nearest():
    from dialog.service_slot_pick import pick_slot_for_day

    target = date(2026, 9, 15)

    async def fake_slots(_day, **_kwargs):
        return [
            (datetime(2026, 9, 15, 12, 0), "post-12", "acc-12", "Мех 12"),
            (datetime(2026, 9, 15, 14, 0), "post-14", "acc-14", "Мех 14"),
        ]

    with patch("dialog.service_slot_pick.list_slots_on_day_1c", new=AsyncMock(side_effect=fake_slots)):
        picked = asyncio.run(
            pick_slot_for_day(
                target,
                150,
                day_part="morning",
            )
        )
    assert picked is not None
    assert picked.time_str == "12:00"
    assert picked.outside_requested_window is True


def test_pick_slot_for_day_skips_malformed_rows():
    from dialog.service_slot_pick import pick_slot_for_day

    target = date(2026, 9, 15)

    async def fake_slots(_day, **_kwargs):
        return [
            ("2026-09-15T09:00:00", "post-bad", "acc-bad"),  # bad datetime type
            ("bad-row",),  # too short
            (datetime(2026, 9, 15, 10, 0), "post-ok", "acc-ok"),
        ]

    with patch("dialog.service_slot_pick.list_slots_on_day_1c", new=AsyncMock(side_effect=fake_slots)):
        picked = asyncio.run(
            pick_slot_for_day(
                target,
                150,
                day_part="morning",
            )
        )
    assert picked is not None
    assert picked.time_str == "10:00"
