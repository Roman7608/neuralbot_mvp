"""Тесты местного времени дилера (Europe/Samara)."""

from dialog.dealer_time import dealer_local_now, dealer_local_now_iso, dealer_tz_name


def test_dealer_tz_is_samara():
    assert "Samara" in dealer_tz_name()


def test_dealer_local_offset_plus_four():
    now = dealer_local_now()
    assert now.utcoffset() is not None
    assert now.utcoffset().total_seconds() == 4 * 3600


def test_dealer_local_iso_has_offset():
    iso = dealer_local_now_iso()
    assert "+04:00" in iso or "+04:" in iso
