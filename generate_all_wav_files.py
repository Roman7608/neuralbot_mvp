#!/usr/bin/env python3
"""
Скрипт для генерации всех необходимых WAV файлов теми же
параметрами, что использует live TTS сервис.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from services.voice.voice_profile import VOICE_SAMPLE_RATE, VOICE_SPEAKER, VOICE_SPEED

def generate_all_wav_files():
    """Генерирует WAV-файлы тем же голосом и скоростью, что и live TTS."""
    import torch
    from torch import package
    import soundfile as sf
    import numpy as np
    
    # Тексты ключевых WAV-фраз
    texts = {
        "01_greeting.wav": "Д+обрый день! Голосов+ой пом+ощник Автосал+она «В+икинги», офици+ального д+илера м+арки T+enet, прив+етствую Вас. Обращ+аю В+аше вним+ание: разгов+ор зап+исывается в ц+елях контр+оля и повыш+ения к+ачества обсл+уживания. Как я мог+у к Вам обращ+аться?",
        "02_ask_name.wav": "Прошу назвать ваше имя, как к Вам обращаться",
        "03_transfer_consultant.wav": "Я перевожу Вас на консультанта",
        "04_transfer_used_cars.wav": "Поняла, перевожу Вас на отдел продажи автомобилей с пробегом.",
        "05_transfer_chery_tenet.wav": "Поняла, перевожу Вас на отдел продажи Ч+ери и Т+энет.",
        "09_goodbye.wav": "Благодарю за обращение в автосалон «Викинги», до свидания!",
        "10_transfer_master.wav": "Я перевожу Вас на мастера-консультанта слесарного цеха.",
        "13_transfer_body_repair.wav": "Я перевожу Вас на мастера-консультанта кузовного цеха.",
        # 12_transfer_accounting.wav / 15_transfer_sales.wav — не используются в сценарии
        "17_repeat.wav": "Повторите, пожалуйста.",
        "18_ask_yes_no.wav": "Я Вас не совсем поняла. Скажите, пожалуйста, «да» или «нет».",
        "19_pleasant_help.wav": "Очень приятно, чем я могу Вам помочь?",
    }
    
    # Ищем модель в папке Загрузки
    downloads_dir = Path.home() / "Загрузки"
    model_file = downloads_dir / "SileroTTS-model-v5_1_ru" / "data" / "v5_1_ru.pt"
    model_file_alt = downloads_dir / "v5_ru.pt"
    
    model_path = None
    if model_file.exists():
        model_path = model_file
        print(f"Найдена модель: {model_path}")
    elif model_file_alt.exists():
        model_path = model_file_alt
        print(f"Найдена модель: {model_path}")
    else:
        print("❌ Модель не найдена в папке Загрузки!")
        return False
    
    try:
        # Загружаем модель
        print("Загрузка модели...")
        imp = package.PackageImporter(str(model_path))
        model = imp.load_pickle("tts_models", "model")
        model.to('cpu')
        
        sample_rate = int(os.getenv("SILERO_SAMPLE_RATE", str(VOICE_SAMPLE_RATE)))
        speaker = os.getenv("SILERO_SPEAKER", VOICE_SPEAKER)
        speed = float(os.getenv("SILERO_SPEED", str(VOICE_SPEED)))
        
        # Путь для сохранения
        output_dir = Path(__file__).parent / "audio_responses"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        print(f"\n🎤 Генерация всех WAV файлов голосом '{speaker}'...")
        print(f"⏩ Скорость: {speed}")
        print(f"📁 Директория: {output_dir}\n")
        
        for wav_file, text in texts.items():
            output_file = output_dir / wav_file
            
            print(f"  [{wav_file}]")
            print(f"    Текст: {text[:60]}...")
            
            # Синтезируем речь
            audio = model.apply_tts(
                text=text,
                speaker=speaker,
                sample_rate=sample_rate
            )
            
            # Преобразуем в numpy массив
            if isinstance(audio, torch.Tensor):
                audio_np = audio.cpu().numpy()
            else:
                audio_np = np.array(audio)
            
            if speed != 1.0:
                target_len = int(len(audio_np) / speed)
                indices = np.linspace(0, len(audio_np) - 1, target_len).astype(int)
                audio_np = audio_np[indices]

            # Нормализуем до диапазона [-1, 1] если нужно
            if audio_np.dtype != np.float32:
                audio_np = audio_np.astype(np.float32)
                if audio_np.max() > 1.0 or audio_np.min() < -1.0:
                    audio_np = audio_np / np.max(np.abs(audio_np))
            
            # Сохраняем в WAV файл
            sf.write(str(output_file), audio_np, sample_rate)
            print(f"    ✅ Создан: {output_file}\n")
        
        print(f"✅ Все файлы успешно созданы голосом '{speaker}'!")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при генерации файлов: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = generate_all_wav_files()
    sys.exit(0 if success else 1)
