"""Тесты сетки времени слотов (шаг 30 минут)."""

from datetime import datetime

from dialog.service_slot_time import (
    round_slot_start_to_half_hour_if_available,
    round_slot_start_to_half_hour_if_close,
    snap_hhmm_to_step,
    snap_slot_start_to_step,
)


def test_snap_12_21_to_12_00():
    dt = datetime(2026, 6, 20, 12, 21)
    assert snap_slot_start_to_step(dt).strftime("%H:%M") == "12:00"


def test_snap_15_40_to_15_30():
    dt = datetime(2026, 6, 20, 15, 40)
    assert snap_slot_start_to_step(dt).strftime("%H:%M") == "15:30"


def test_snap_09_11_to_09_00():
    dt = datetime(2026, 6, 20, 9, 11)
    assert snap_slot_start_to_step(dt).strftime("%H:%M") == "09:00"


def test_normalize_1c_slots_snaps_and_dedupes():
    from dialog.service_slot_time import normalize_1c_slots

    raw = [
        {"date": "2026-06-20", "time_start": "09:11", "post_id": "ВК00000010"},
        {"date": "2026-06-20", "time_start": "09:15", "post_id": "ВК00000010"},
        {"date": "2026-06-20", "time_start": "12:21", "post_id": "ВК00000010"},
    ]
    out = normalize_1c_slots(raw)
    times = [s["time_start"] for s in out]
    assert times == ["09:00", "12:00"]


def test_normalize_1c_slots_acceptance_time_format():
    from dialog.service_slot_time import normalize_1c_slots, slot_time_hhmm_from_raw

    raw = [
        {
            "date": "2026-06-20",
            "acceptance_time": "09:11",
            "acceptor_id": "ВК00000001",
            "acceptor_name": "Иванов",
            "post_id": "ВК00000010",
            "post_name": "Пост 1",
        },
    ]
    out = normalize_1c_slots(raw)
    assert len(out) == 1
    assert slot_time_hhmm_from_raw(out[0]) == "09:00"
    assert out[0]["acceptor_id"] == "ВК00000001"


def test_snap_hhmm():
    assert snap_hhmm_to_step("12:21") == "12:00"
    assert snap_hhmm_to_step("15:20") == "15:00"


def test_round_half_hour_close_10_50_to_11_00():
    dt = datetime(2026, 6, 20, 10, 50)
    assert round_slot_start_to_half_hour_if_close(dt).strftime("%H:%M") == "11:00"


def test_round_half_hour_close_11_10_to_11_00():
    dt = datetime(2026, 6, 20, 11, 10)
    assert round_slot_start_to_half_hour_if_close(dt).strftime("%H:%M") == "11:00"


def test_round_half_hour_close_11_20_to_11_30():
    dt = datetime(2026, 6, 20, 11, 20)
    assert round_slot_start_to_half_hour_if_close(dt).strftime("%H:%M") == "11:30"


def test_round_half_hour_far_11_12_unchanged():
    dt = datetime(2026, 6, 20, 11, 12)
    assert round_slot_start_to_half_hour_if_close(dt).strftime("%H:%M") == "11:12"


def test_round_half_hour_applied_only_when_slot_exists_same_post_acceptor():
    start = datetime(2026, 9, 17, 9, 10)
    slots = [
        (datetime(2026, 9, 17, 9, 10), "ВК1", "ЦБ1", "Мастер 1"),
        (datetime(2026, 9, 17, 9, 0), "ВК1", "ЦБ1", "Мастер 1"),
    ]
    out = round_slot_start_to_half_hour_if_available(
        start,
        available_slots=slots,
        post_id="ВК1",
        acceptor_id="ЦБ1",
    )
    assert out.strftime("%H:%M") == "09:00"


def test_round_half_hour_rejected_when_no_matching_slot_for_same_post_acceptor():
    start = datetime(2026, 9, 17, 9, 10)
    slots = [
        (datetime(2026, 9, 17, 9, 10), "ВК1", "ЦБ1", "Мастер 1"),
        # 09:00 есть, но у другого поста/приемщика — округлять нельзя.
        (datetime(2026, 9, 17, 9, 0), "ВК2", "ЦБ2", "Мастер 2"),
    ]
    out = round_slot_start_to_half_hour_if_available(
        start,
        available_slots=slots,
        post_id="ВК1",
        acceptor_id="ЦБ1",
    )
    assert out.strftime("%H:%M") == "09:10"
