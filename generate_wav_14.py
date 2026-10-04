#!/usr/bin/env python3
"""
Скрипт для генерации WAV файла 14_ask_client_data.wav через Silero TTS.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from services.voice.voice_profile import VOICE_SAMPLE_RATE, VOICE_SPEAKER

# Добавляем путь к модулям
sys.path.insert(0, str(PROJECT_ROOT / "Asterisk" / "media_sockets"))

def generate_wav_14():
    """Генерирует 14_ask_client_data.wav через Silero TTS."""
    import torch
    from torch import package
    
    # Текст для файла 14
    text = "Я могу записать Вас на ремонт и обслуживание вашего автомобиля. Если Вы согласны, назовите Ваши фамилию, имя и отчество полностью, а также ваш номер телефона."
    
    # Путь для сохранения
    output_dir = Path(__file__).parent / "Asterisk" / "media_sockets" / "audio_responses"
    output_file = output_dir / "14_ask_client_data.wav"
    
    print(f"Генерация файла: {output_file}")
    print(f"Текст: {text}")
    
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
        
        # Синтезируем речь
        print("Синтез речи...")
        speaker = VOICE_SPEAKER
        sample_rate = VOICE_SAMPLE_RATE
        
        audio = model.apply_tts(
            text=text,
            speaker=speaker,
            sample_rate=sample_rate
        )
        
        # Сохраняем в WAV файл
        print(f"Сохранение в файл: {output_file}")
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Используем метод save_wav модели, если доступен
        if hasattr(model, 'save_wav'):
            model.save_wav(
                text=text,
                speaker=speaker,
                sample_rate=sample_rate,
                audio_path=str(output_file)
            )
        else:
            # Или сохраняем вручную через soundfile
            import soundfile as sf
            import numpy as np
            
            # Преобразуем в numpy массив
            if isinstance(audio, torch.Tensor):
                audio_np = audio.cpu().numpy()
            else:
                audio_np = np.array(audio)
            
            # Нормализуем до диапазона [-1, 1] если нужно
            if audio_np.dtype != np.float32:
                audio_np = audio_np.astype(np.float32)
                if audio_np.max() > 1.0 or audio_np.min() < -1.0:
                    audio_np = audio_np / np.max(np.abs(audio_np))
            
            sf.write(str(output_file), audio_np, sample_rate)
        
        print(f"✅ Файл успешно создан: {output_file}")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при генерации файла: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = generate_wav_14()
    sys.exit(0 if success else 1)
