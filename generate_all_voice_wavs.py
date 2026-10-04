#!/usr/bin/env python3
"""
Генерация всех WAV-файлов голосового бота.
Голос: xenia, скорость: 1.1

Файлы сохраняются в audio_responses/
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from services.voice.voice_profile import VOICE_SAMPLE_RATE, VOICE_SPEAKER, VOICE_SPEED
from services.voice.voice_phrases import (
    AFTER_HOURS_V2_ASK_NAME_INTRO,
    AFTER_HOURS_V2_CONTACT_DONE,
    DEPARTMENT_CHOICE_CHERY_TENET,
    DEPARTMENT_CLARIFY_V2_CHERY_TENET,
    GREETING_CHERY_TENET,
    GREETING_V2_CHERY_TENET,
    HOLIDAY_AFTER_HOURS_V2_ASK_NAME_INTRO,
    HOLIDAY_AFTER_HOURS_V2_CONTACT_DONE,
    SERVICE_CHOICE,
    SLOTS_SEARCH_FILLER,
    SLOT_CONFIRM_REPROMPT,
    TO_V2_AFTER_PRICE_RESUME,
    TO_V2_ASK_CAR,
    TO_V2_ASK_DATETIME,
    TO_V2_ASK_MILEAGE,
    TO_V2_ASK_NAME,
    TO_V2_ASK_PHONE,
    TO_V2_ASK_WORKS,
    TO_V2_ASK_YEAR,
    TO_V2_ASK_YEAR_MILEAGE,
    TO_V2_GATE5,
    TO_V2_INTERRUPT_BOOKING,
    TO_V2_PRICE_INTRO,
    TO_V2_PRICE_NOT_FOUND,
    TO_V2_PRICE_UNAVAILABLE,
    TO_V2_PRICE_ASK_TRANSMISSION,
    TO_V2_PRICE_ASK_DRIVE,
    TO_V2_PRICE_ASK_VOLUME,
    TO_V2_PRICE_REPEAT_TRANSMISSION,
    TO_V2_PRICE_REPEAT_DRIVE,
    TO_V2_PRICE_REPEAT_VOLUME,
    TRANSFER_SERVICE_ASSISTANT,
)

OUTPUT_DIR = PROJECT_ROOT / "audio_responses"
SPEAKER = VOICE_SPEAKER
SAMPLE_RATE = VOICE_SAMPLE_RATE
SPEED = VOICE_SPEED

# Все фразы для генерации (текст для Silero: + для ударения)
PHRASES = [
    # 01 — единое приветствие (voice_phrases.GREETING_CHERY_TENET)
    ("01_greeting_chery_tenet.wav", GREETING_CHERY_TENET),
    # 01b — приветствие v2 (VOICE_SCENARIO=v2, без «Как Вас зовут?»)
    ("01b_greeting_v2_chery_tenet.wav", GREETING_V2_CHERY_TENET),
    # 22 — первое уточнение отделов v2 (после неясного ответа)
    ("22_department_clarify_v2.wav", DEPARTMENT_CLARIFY_V2_CHERY_TENET),
    # 23–24 — v2 + нерабочее: запрос обращения и подтверждение (без переспрашивания)
    ("23_after_hours_v2_ask_name.wav", AFTER_HOURS_V2_ASK_NAME_INTRO),
    ("24_after_hours_v2_contact_done.wav", AFTER_HOURS_V2_CONTACT_DONE),
    # 23h-24h — v2 + спец-нерабочий день (календарь, 9 мая)
    ("23_after_hours_v2_holiday_ask_name.wav", HOLIDAY_AFTER_HOURS_V2_ASK_NAME_INTRO),
    ("24_after_hours_v2_holiday_contact_done.wav", HOLIDAY_AFTER_HOURS_V2_CONTACT_DONE),
    # 03 - перевод на администратора
    ("03_transfer_admin.wav", "Я перевожу Вас на администратора"),
    # 04-06 - переводы по отделам
    ("04_transfer_used_cars.wav", "Поняла, перевожу вас на отдел автомобилей с пробегом."),
    ("05_transfer_chery_tenet.wav", "Поняла, перевожу вас на отдел автомобилей Ч+ери и Тен+эт."),
    # 09 - прощание
    ("09_goodbye.wav", "Благодарю за обращение в автосалон «Викинги», до свидания!"),
    # 10-13 - переводы
    ("10_transfer_master.wav", "Я перевожу Вас на мастера-консультанта."),
    ("11_transfer_parts.wav", "Я перевожу Вас на отдел запасных частей."),
    ("13_transfer_body_repair.wav", "Я перевожу Вас в цех кузовного ремонта."),
    # 14 - выбор: запись или ассистент (v2)
    ("14_service_choice.wav", SERVICE_CHOICE),
    # 15 - legacy запрос ФИО (архивный сценарий)
    ("15_ask_fio_for_to.wav", (
        "Для записи на техн+ическое обсл+уживание прошу Вас разборчиво назвать Ваши Фамилию, Имя, отчество и номер телефона."
    )),
    # 16 - перевод на ассистента сервиса
    ("16_transfer_service_assistant.wav", TRANSFER_SERVICE_ASSISTANT),
    # 25-34 — запись на ТО v2 (ZapisSTO.txt)
    ("25_to_v2_ask_name.wav", TO_V2_ASK_NAME),
    ("26_to_v2_ask_phone.wav", TO_V2_ASK_PHONE),
    ("27_to_v2_ask_car.wav", TO_V2_ASK_CAR),
    ("28_to_v2_ask_year_mileage.wav", TO_V2_ASK_YEAR_MILEAGE),
    ("28a_to_v2_ask_year.wav", TO_V2_ASK_YEAR),
    ("29_to_v2_gate5.wav", TO_V2_GATE5),
    ("30_to_v2_ask_works.wav", TO_V2_ASK_WORKS),
    ("30a_to_v2_ask_mileage.wav", TO_V2_ASK_MILEAGE),
    ("31_to_v2_ask_datetime.wav", TO_V2_ASK_DATETIME),
    ("32_to_v2_after_price_resume.wav", TO_V2_AFTER_PRICE_RESUME),
    ("33_to_v2_price_intro.wav", TO_V2_PRICE_INTRO),
    ("34_to_v2_interrupt_booking.wav", TO_V2_INTERRUPT_BOOKING),
    # 35-41 — блок D: уточнения и не найдено в регламенте
    ("35_to_v2_price_ask_transmission.wav", TO_V2_PRICE_ASK_TRANSMISSION),
    ("36_to_v2_price_ask_volume.wav", TO_V2_PRICE_ASK_VOLUME),
    ("37_to_v2_price_ask_drive.wav", TO_V2_PRICE_ASK_DRIVE),
    ("38_to_v2_price_repeat_transmission.wav", TO_V2_PRICE_REPEAT_TRANSMISSION),
    ("39_to_v2_price_repeat_volume.wav", TO_V2_PRICE_REPEAT_VOLUME),
    ("40_to_v2_price_repeat_drive.wav", TO_V2_PRICE_REPEAT_DRIVE),
    ("41_to_v2_price_not_found.wav", TO_V2_PRICE_NOT_FOUND),
    ("42_slots_search_filler.wav", SLOTS_SEARCH_FILLER),
    ("43_slot_confirm_reprompt.wav", SLOT_CONFIRM_REPROMPT),
    # 17 - повторить
    ("17_repeat.wav", "Повторите, пожалуйста."),
    # 20 - нерабочие часы (20:00–8:00)
    ("20_after_hours_admin.wav", (
        "Извините, вы позвонили в нерабочее время. "
        "Автосалон Викинги работает с восьми утра до восьми вечера. "
        "Я передаю Ваш контакт администратору, он позвонит Вам в первые 15 минут работы автосалона. "
        "Благодарю Вас за обращение, до свидания."
    )),
    ("20_after_hours_used_cars.wav", (
        "Извините, вы позвонили в нерабочее время. "
        "Автосалон Викинги работает с восьми утра до восьми вечера. "
        "Я передаю Ваш контакт в отдел автомобилей с пробегом, "
        "вам позвонят в первые 15 минут работы автосалона. "
        "Благодарю Вас за обращение, до свидания."
    )),
    ("20_after_hours_chery_tenet.wav", (
        "Извините, вы позвонили в нерабочее время. "
        "Автосалон Викинги работает с восьми утра до восьми вечера. "
        "Я передаю Ваш контакт в отдел продажи Ч+ери и Тен+эт, "
        "вам позвонят в первые 15 минут работы автосалона. "
        "Благодарю Вас за обращение, до свидания."
    )),
    ("20_after_hours_service.wav", (
        "Извините, вы позвонили в нерабочее время. "
        "Автосалон Викинги работает с восьми утра до восьми вечера. "
        "Я передаю Ваш контакт в слесарный цех, "
        "вам позвонят в первые 15 минут работы автосалона. "
        "Благодарю Вас за обращение, до свидания."
    )),
    ("20_after_hours_body.wav", (
        "Извините, вы позвонили в нерабочее время. "
        "Автосалон Викинги работает с восьми утра до восьми вечера. "
        "Я передаю Ваш контакт в кузовной цех, "
        "вам позвонят в первые 15 минут работы автосалона. "
        "Благодарю Вас за обращение, до свидания."
    )),
    ("20_after_hours_parts.wav", (
        "Извините, вы позвонили в нерабочее время. "
        "Автосалон Викинги работает с восьми утра до восьми вечера. "
        "Я передаю Ваш контакт в отдел запасных частей, "
        "вам позвонят в первые 15 минут работы автосалона. "
        "Благодарю Вас за обращение, до свидания."
    )),
    # 20h - спец-нерабочий день (выходной/праздник из календаря)
    ("20_after_hours_holiday_admin.wav", (
        "Извините, вы позвонили в нерабочий день. "
        "Сегодня Автосалон Викинги не работает. "
        "Я передаю Ваш контакт администратору, он позвонит Вам после начала работы автосалона. "
        "Благодарю Вас за обращение, до свидания."
    )),
    ("20_after_hours_holiday_used_cars.wav", (
        "Извините, вы позвонили в нерабочий день. "
        "Сегодня Автосалон Викинги не работает. "
        "Я передаю Ваш контакт в отдел автомобилей с пробегом, "
        "вам позвонят после начала работы автосалона. "
        "Благодарю Вас за обращение, до свидания."
    )),
    ("20_after_hours_holiday_chery_tenet.wav", (
        "Извините, вы позвонили в нерабочий день. "
        "Сегодня Автосалон Викинги не работает. "
        "Я передаю Ваш контакт в отдел продажи Ч+ери и Тен+эт, "
        "вам позвонят после начала работы автосалона. "
        "Благодарю Вас за обращение, до свидания."
    )),
    ("20_after_hours_holiday_service.wav", (
        "Извините, вы позвонили в нерабочий день. "
        "Сегодня Автосалон Викинги не работает. "
        "Я передаю Ваш контакт в слесарный цех, "
        "вам позвонят после начала работы автосалона. "
        "Благодарю Вас за обращение, до свидания."
    )),
    ("20_after_hours_holiday_body.wav", (
        "Извините, вы позвонили в нерабочий день. "
        "Сегодня Автосалон Викинги не работает. "
        "Я передаю Ваш контакт в кузовной цех, "
        "вам позвонят после начала работы автосалона. "
        "Благодарю Вас за обращение, до свидания."
    )),
    ("20_after_hours_holiday_parts.wav", (
        "Извините, вы позвонили в нерабочий день. "
        "Сегодня Автосалон Викинги не работает. "
        "Я передаю Ваш контакт в отдел запасных частей, "
        "вам позвонят после начала работы автосалона. "
        "Благодарю Вас за обращение, до свидания."
    )),
]

# Перечисление отделов — voice_phrases.DEPARTMENT_CHOICE_CHERY_TENET (единый сценарий)
DEPARTMENT_PHRASES = [
    ("12_ask_department_chery_tenet.wav", DEPARTMENT_CHOICE_CHERY_TENET),
]


def _find_model() -> Path | None:
    downloads = Path.home() / "Загрузки"
    for p in [
        downloads / "SileroTTS-model-v5_1_ru" / "data" / "v5_1_ru.pt",
        downloads / "v5_ru.pt",
        Path("/models/tts/v5_1_ru.pt"),
    ]:
        if p.exists():
            return p
    return None


def _apply_speed(audio_np, speed: float):
    """Применяет скорость к аудио (speed > 1 = быстрее)."""
    if speed == 1.0:
        return audio_np
    import numpy as np
    target_len = int(len(audio_np) / speed)
    indices = np.linspace(0, len(audio_np) - 1, target_len).astype(int)
    return audio_np[indices]


def generate_all(only_names: list[str] | None = None):
    """Генерирует WAV-файлы. Если only_names — только перечисленные имена файлов."""
    import torch
    from torch import package
    import soundfile as sf
    import numpy as np

    model_path = _find_model()
    if not model_path:
        print("❌ Модель Silero TTS не найдена!")
        return False

    print(f"Загрузка модели: {model_path}")
    imp = package.PackageImporter(str(model_path))
    model = imp.load_pickle("tts_models", "model")
    model.to("cpu")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    all_phrases = PHRASES + DEPARTMENT_PHRASES
    if only_names:
        want = set(only_names)
        known = {p[0] for p in all_phrases}
        bad = want - known
        if bad:
            print(f"❌ Неизвестные имена файлов (нет в списке генерации): {sorted(bad)}")
            return False
        all_phrases = [pair for pair in all_phrases if pair[0] in want]
        if not all_phrases:
            print("❌ После фильтрации список пуст")
            return False

    for filename, text in all_phrases:
        output_path = OUTPUT_DIR / filename
        print(f"\n🎤 {filename}...")
        print(f"   Текст: {text[:60]}...")

        try:
            audio = model.apply_tts(
                text=text,
                speaker=SPEAKER,
                sample_rate=SAMPLE_RATE,
            )
            if isinstance(audio, torch.Tensor):
                audio_np = audio.cpu().numpy().astype(np.float32)
            else:
                audio_np = np.array(audio, dtype=np.float32)

            audio_np = _apply_speed(audio_np, SPEED)

            if audio_np.max() > 1.0 or audio_np.min() < -1.0:
                audio_np = audio_np / np.max(np.abs(audio_np))

            sf.write(str(output_path), audio_np, SAMPLE_RATE)
            print(f"   ✅ {output_path}")
        except Exception as e:
            print(f"   ❌ Ошибка: {e}")
            import traceback
            traceback.print_exc()
            return False

    print(f"\n✅ Все {len(all_phrases)} файлов сгенерированы (голос {SPEAKER}, скорость {SPEED})")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Генерация WAV для голосового бота (Silero TTS)")
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="FILE.wav",
        help="Сгенерировать только эти файлы (например: 01_greeting_chery_tenet.wav 12_ask_department_chery_tenet.wav)",
    )
    args = parser.parse_args()
    success = generate_all(only_names=args.only)
    sys.exit(0 if success else 1)
