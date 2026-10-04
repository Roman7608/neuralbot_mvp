"""Тесты распознавания согласия/отказа в голосовом сценарии записи."""

from dialog.bot_logic import (
    _extract_slot_time_from_text,
    _looks_like_slot_time_attempt,
    _voice_affirmative_stt,
    _voice_slot_booking_confirm_stt,
    _voice_slot_plain_no_stt,
    _voice_slot_reject_stt,
)
from services.voice.voice_phrases import (
    TO_V2_BOOKED_CALLBACK_NOTICE,
    format_service_slot_offer_tts,
)


def test_stt_a_is_confirm():
    assert _voice_slot_booking_confirm_stt("а.")
    assert _voice_slot_booking_confirm_stt("A.")
    assert _voice_affirmative_stt("а")


def test_zhelatelno_is_not_affirmative():
    assert not _voice_affirmative_stt("после обеда желательно.")
    assert not _voice_slot_booking_confirm_stt("после обеда желательно.")


def test_explicit_da_is_affirmative():
    assert _voice_affirmative_stt("да.")
    assert _voice_slot_booking_confirm_stt("да, записывайте.")


def test_verno_is_affirmative():
    assert _voice_affirmative_stt("верно")
    assert _voice_affirmative_stt("Верно.")
    assert _voice_affirmative_stt("правильно")
    assert _voice_affirmative_stt("Вно.")
    assert _voice_affirmative_stt("на.")
    assert _voice_affirmative_stt("на")


def test_poydet_variants_are_affirmative():
    assert _voice_affirmative_stt("Пойдет.")
    assert _voice_affirmative_stt("Пойдёт.")
    assert _voice_affirmative_stt("Пойзет.")
    assert not _voice_affirmative_stt("Не пойдет.")


def test_zapishite_is_confirm():
    assert _voice_slot_booking_confirm_stt("запишите на это время")


def test_sishi_is_confirm():
    assert _voice_slot_booking_confirm_stt("сиши.")


def test_zapyatu_is_confirm():
    assert _voice_slot_booking_confirm_stt("запяту.")


def test_spisyvay_stt_is_confirm():
    assert _voice_slot_booking_confirm_stt("списывай.")
    assert _voice_slot_booking_confirm_stt("пписывай.")
    assert _voice_slot_booking_confirm_stt("писывай.")


def test_home_line_zapis_stt_fragments_are_confirm():
    assert _voice_slot_booking_confirm_stt("Ши.")
    assert _voice_slot_booking_confirm_stt("спиши")
    assert _voice_slot_booking_confirm_stt("запиш")
    assert _voice_slot_booking_confirm_stt("запиши.")


def test_davaj_uzhe_is_confirm():
    assert _voice_slot_booking_confirm_stt("давай уже.")
    assert _voice_slot_booking_confirm_stt("давай.")
    assert _voice_slot_booking_confirm_stt("Нуавайте.")
    assert _voice_slot_booking_confirm_stt("Авайте.")


def test_net_is_reject():
    assert _voice_slot_reject_stt("нет, не подходит")
    assert not _voice_slot_reject_stt("после обеда желательно")
    assert _voice_slot_plain_no_stt("нет.")
    assert not _voice_slot_plain_no_stt("не подходит")


def test_slot_offer_tts_question_stress():
    text = format_service_slot_offer_tts("семн+адцатое", "и+юня", "восемь ноль ноль")
    assert "Запишу Вас" in text
    assert "семн+адцатое" in text


def test_apply_silero_question_intonation():
    from services.voice.voice_phrases import apply_silero_question_intonation

    assert apply_silero_question_intonation("У Вас Ч+ери, верно?").endswith("в+ерно??")
    assert "??" in apply_silero_question_intonation("Спасибо, есть ли у Вас еще вопросы?")
    offer = apply_silero_question_intonation(
        "На п+ервое и+юля в десять ноль ноль есть свободное вр+емя. Могу записать вас?"
    )
    assert "Мог+у записать в+ас??" in offer
    assert "..." not in offer


def test_na_20_iyunya_is_not_time():
    assert _extract_slot_time_from_text("запишите меня на 20 июня") is None


def test_v_20_is_time():
    assert _extract_slot_time_from_text("давайте в 20") == "20:00"


def test_repeated_stt_digits_are_repaired_as_time():
    assert _extract_slot_time_from_text("11 августа, 111:10") == "11:10"
    assert _extract_slot_time_from_text("запишите на 110:330") == "10:30"
    assert _extract_slot_time_from_text("можно в 1110") == "11:10"


def test_common_numeric_and_spoken_time_formats():
    assert _extract_slot_time_from_text("в 9:5") == "09:05"
    assert _extract_slot_time_from_text("в одиннадцать десять") == "11:10"
    assert _extract_slot_time_from_text("к двадцати одному") is None
    assert _extract_slot_time_from_text("в двадцать один") == "21:00"


def test_ambiguous_malformed_time_is_not_guessed():
    assert _extract_slot_time_from_text("11 августа, в 123:10") is None
    assert _looks_like_slot_time_attempt("11 августа, в 123:10")


def test_booking_callback_notice_has_approved_tts_text():
    assert TO_V2_BOOKED_CALLBACK_NOTICE == (
        "в ближ+айшее вр+емя Вам перезвон_ит дисп+етчер и уточн_ит дет_али"
    )
