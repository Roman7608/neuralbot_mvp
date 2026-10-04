"""Тесты парсера дат из русской речи."""

from datetime import datetime

from dialog.date_parser import DateParser


def test_parse_18_june():
    ref = datetime(2026, 6, 16)
    p = DateParser()
    d = p.parse_date("На 18 июня есть свободное время?", ref)
    assert d is not None
    assert d.strftime("%Y-%m-%d") == "2026-06-18"


def test_parse_genitive_19_june():
    ref = datetime(2026, 6, 16)
    p = DateParser()
    d = p.parse_date("Девятнадцатого июня есть?", ref)
    assert d is not None
    assert d.strftime("%Y-%m-%d") == "2026-06-19"


def test_parse_dates_or_two_days():
    ref = datetime(2026, 6, 16)
    p = DateParser()
    dates = p.parse_dates("18 или 19 июня", ref)
    assert [d.strftime("%Y-%m-%d") for d in dates] == ["2026-06-18", "2026-06-19"]


def test_parse_dates_or_ordinals():
    ref = datetime(2026, 6, 16)
    p = DateParser()
    dates = p.parse_dates(
        "на семнадцатое июня или на девятнадцатое июня",
        ref,
    )
    assert [d.strftime("%Y-%m-%d") for d in dates] == ["2026-06-17", "2026-06-19"]


def test_parse_thirtieth_stt_distortions_with_time():
    ref = datetime(2026, 7, 26)
    p = DateParser()
    for phrase in (
        "Т ридцатое августа с утра",
        "3цатое августа 9:00",
        "3-цатого августа в 9:00",
    ):
        d = p.parse_date(phrase, ref)
        assert d is not None, phrase
        assert d.strftime("%Y-%m-%d") == "2026-08-30"


def test_time_before_context_month_is_not_parsed_as_date():
    ref = datetime(2026, 7, 26)
    p = DateParser()
    assert p.parse_date("9:00 июля", ref) is None


def test_explicit_past_day_month_is_detected_without_rolling_year():
    ref = datetime(2026, 7, 27, 8, 30)
    p = DateParser()
    past = p.parse_explicit_past_date("двадцать первое июля", ref)
    assert past is not None
    assert past.strftime("%Y-%m-%d") == "2026-07-21"
    assert p.parse_explicit_past_date("двадцать восьмое июля", ref) is None
    assert p.parse_explicit_past_date("двадцать первое июля 2027", ref) is None


def test_parse_short_3c_july_and_yo_fourth_august():
    ref = datetime(2026, 7, 27)
    p = DateParser()
    d1 = p.parse_date("3ц июля 8:00", ref)
    assert d1 is not None
    assert d1.strftime("%Y-%m-%d") == "2026-07-30"
    d2 = p.parse_date("Четвёртое августа 8:00", ref)
    assert d2 is not None
    assert d2.strftime("%Y-%m-%d") == "2026-08-04"


def test_parse_day_without_month_prefers_current_month_if_not_passed():
    ref = datetime(2026, 7, 27, 11, 23)
    p = DateParser()
    d = p.parse_date("двадцать восьмое утро", ref)
    assert d is not None
    assert d.strftime("%Y-%m-%d") == "2026-07-28"


def test_parse_day_without_month_rolls_to_next_month_if_passed():
    ref = datetime(2026, 7, 27, 11, 23)
    p = DateParser()
    d = p.parse_date("двадцать шестое утром", ref)
    assert d is not None
    assert d.strftime("%Y-%m-%d") == "2026-08-26"


def test_explicit_day_month_beats_weekday_in_same_phrase():
    ref = datetime(2026, 8, 3, 18, 8)
    p = DateParser()
    d = p.parse_date("Понедельник, 17 августа, 10:00", ref)
    assert d is not None
    assert d.strftime("%Y-%m-%d") == "2026-08-17"


def test_parse_short_weekday_forms_as_next_same_weekday():
    p = DateParser()
    ref = datetime(2026, 8, 9, 9, 40)  # воскресенье
    d = p.parse_date("Ээторник в 8:00 утра", ref)
    assert d is not None
    assert d.strftime("%Y-%m-%d") == "2026-08-11"

    ref_same_day = datetime(2026, 8, 11, 10, 0)  # вторник
    d_same = p.parse_date("торник в 9:00", ref_same_day)
    assert d_same is not None
    assert d_same.strftime("%Y-%m-%d") == "2026-08-18"


def test_parse_stt_2tsat_devyaye_chislo():
    ref = datetime(2026, 8, 12, 10, 0)
    p = DateParser()
    d = p.parse_date("2цать девя-е число", ref)
    assert d is not None
    assert d.strftime("%Y-%m-%d") == "2026-08-29"


def test_parse_feminine_ordinal_without_month_in_current_month():
    ref = datetime(2026, 8, 14, 10, 0)
    p = DateParser()
    d = p.parse_date("двадцать шестая", ref)
    assert d is not None
    assert d.strftime("%Y-%m-%d") == "2026-08-26"


def test_parse_typo_dvadct_shestaya_without_month():
    ref = datetime(2026, 8, 14, 10, 0)
    p = DateParser()
    d = p.parse_date("двадцть шестая", ref)
    assert d is not None
    assert d.strftime("%Y-%m-%d") == "2026-08-26"


def test_parse_compound_genitive_without_month():
    ref = datetime(2026, 9, 17, 10, 0)
    p = DateParser()
    d = p.parse_date("Двадцать третьего с утра", ref)
    assert d is not None
    assert d.strftime("%Y-%m-%d") == "2026-09-23"
