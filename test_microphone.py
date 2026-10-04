#!/usr/bin/env python3
"""
Простой тест USB-микрофона: запись 3 секунд и воспроизведение.
"""

import sounddevice as sd
import numpy as np
import sys

def test_microphone(device_id=None, duration=3):
    """Тест записи с микрофона."""
    # Получаем поддерживаемую частоту для устройства
    if device_id is not None:
        device_info = sd.query_devices(device_id)
        sample_rate = int(device_info['default_samplerate'])
        print(f"   Используемая частота: {sample_rate} Hz")
    else:
        sample_rate = 48000  # Стандартная частота для USB-микрофонов
    
    print(f"🎤 Тест микрофона (устройство: {device_id if device_id else 'по умолчанию'})")
    print(f"\n📝 Запись {duration} секунд...")
    print("   Приготовьтесь...")
    
    import time
    for i in range(3, 0, -1):
        print(f"   Начинаем через {i}...", end='\r')
        time.sleep(1)
    print("   ✅ Говорите в микрофон!                    ")
    
    try:
        # Запись
        recording = sd.rec(
            int(duration * sample_rate),
            samplerate=sample_rate,
            channels=1,
            dtype='float32',
            device=device_id
        )
        sd.wait()  # Ждем завершения записи
        
        # Проверка уровня сигнала
        max_level = np.max(np.abs(recording))
        avg_level = np.mean(np.abs(recording))
        
        # Применяем усиление, если сигнал тихий (но не перегружаем)
        if max_level < 0.1:
            gain = min(3.0, 0.1 / max_level if max_level > 0 else 3.0)
            recording = recording * gain
            recording = np.clip(recording, -1.0, 1.0)  # Ограничиваем, чтобы избежать клиппинга
            max_level_after = np.max(np.abs(recording))
            print(f"\n✅ Запись завершена!")
            print(f"   Максимальный уровень (до усиления): {max_level:.3f}")
            print(f"   Применено усиление: {gain:.1f}x")
            print(f"   Максимальный уровень (после усиления): {max_level_after:.3f}")
        else:
            print(f"\n✅ Запись завершена!")
            print(f"   Максимальный уровень: {max_level:.3f}")
        
        print(f"   Средний уровень: {avg_level:.3f}")
        
        if max_level < 0.01:
            print("   ⚠️  ВНИМАНИЕ: Очень тихий сигнал!")
            print("   💡 Рекомендации:")
            print("      - Говорите на переднюю часть микрофона (15-30 см)")
            print("      - Проверьте уровень громкости в настройках системы")
            print("      - Убедитесь, что микрофон подключен напрямую к USB")
        elif max_level < 0.1:
            print("   ⚠️  Сигнал довольно тихий.")
            print("   💡 Попробуйте говорить ближе к микрофону или громче")
        else:
            print("   ✅ Сигнал хороший!")
        
        # Воспроизведение
        print(f"\n🔊 Воспроизведение записи...")
        sd.play(recording, samplerate=sample_rate)
        sd.wait()
        
        print("\n✅ Тест завершен!")
        return True
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        return False

if __name__ == "__main__":
    # Показываем доступные устройства
    print("=" * 60)
    print("Доступные устройства записи:")
    print("=" * 60)
    devices = sd.query_devices()
    input_devices = [(i, d) for i, d in enumerate(devices) if d['max_input_channels'] > 0]
    for i, d in input_devices:
        marker = " ← USB микрофон" if "USB" in d['name'] or "microphone" in d['name'].lower() else ""
        print(f"  {i}: {d['name']} ({d['max_input_channels']} каналов){marker}")
    
    print("\n" + "=" * 60)
    
    # Ищем USB микрофон
    usb_mic_id = None
    for i, d in input_devices:
        if "USB" in d['name'] or "microphone" in d['name'].lower():
            usb_mic_id = i
            break
    
    if usb_mic_id is not None:
        print(f"\n🎤 Найден USB-микрофон: устройство #{usb_mic_id}")
        print("\n" + "=" * 60)
        test_microphone(device_id=usb_mic_id, duration=5)  # Увеличиваем до 5 секунд
    else:
        print("\n⚠️  USB-микрофон не найден автоматически.")
        print("   Тестируем устройство по умолчанию...")
        print("\n" + "=" * 60)
        test_microphone(duration=5)
