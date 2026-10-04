"""Тесты распознавания запроса ближайшего слота (ASAP) на шаге даты."""

import pytest

from dialog.bot_logic import (
    _is_asap_slot_request,
    _is_step7_slot_availability_request,
    _is_within_week_request,
    _is_until_end_of_week_request,
)


@pytest.mark.parametrize(
    "phrase",
    [
        "а когда есть",
        "когда можно",
        "можно когда",
        "без разницы",
        "не важно",
        "в любое время",
        "хотелось бы попасть",
        "сейчас",
        "быстро",
        "быстрее",
        "скорее",
        "сразу",
        "срочно",
        "как можно быстрее",
        "что есть по времени",
        "на этой неделе",
        "на неделе",
        "на текущей неделе",
    ],
)
def test_asap_slot_request_synonyms(phrase: str) -> None:
    assert _is_asap_slot_request(phrase.lower()), repr(phrase)


@pytest.mark.parametrize(
    "phrase",
    [
        "на 5 июля",
        "пятое июля",
        "первое июля в десять",
        "нет",
    ],
)
def test_asap_slot_request_not_date_phrases(phrase: str) -> None:
    assert not _is_asap_slot_request(phrase.lower()), repr(phrase)


@pytest.mark.parametrize(
    "phrase",
    [
        "Можно.",
        "А можно.",
        "когда можно",
        "когда",
        "а когда",
        "без разницы",
        "не важно",
        "как можно скорее",
    ],
)
def test_step7_slot_availability_stt_truncations(phrase: str) -> None:
    low = phrase.lower()
    assert _is_step7_slot_availability_request(phrase, low), repr(phrase)


def test_step7_slot_availability_not_bare_no() -> None:
    assert not _is_step7_slot_availability_request("нет", "нет")


@pytest.mark.parametrize(
    "phrase",
    [
        "до конца недели",
        "до конца этой недели",
        "до конца текущей недели",
        "до воскресенья",
    ],
)
def test_until_end_of_week_request_synonyms(phrase: str) -> None:
    assert _is_until_end_of_week_request(phrase.lower()), repr(phrase)


@pytest.mark.parametrize(
    "phrase",
    [
        "в течение недели",
        "в течении недели",
        "в ближайшую неделю",
        "в ближайшие 7 дней",
        "в ближайшие семь дней",
    ],
)
def test_within_week_request_synonyms(phrase: str) -> None:
    low = phrase.lower()
    assert _is_within_week_request(low), repr(phrase)
    assert not _is_until_end_of_week_request(low), repr(phrase)
