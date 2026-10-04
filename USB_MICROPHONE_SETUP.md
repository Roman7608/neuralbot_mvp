# Настройка USB-микрофона

## Текущая конфигурация

USB-микрофон настроен в `config.py`:
- **Устройство**: #8 "USB microphone: Audio (hw:4,0)"
- **Частота дискретизации**: 44100 Hz (автоматически определяется)
- **Каналы**: 1 (моно)

## Изменения в config.py

```python
INPUT_DEVICE: int | None = 8  # USB микрофон
```

## Как проверить работу

1. Запустите тест микрофона:
   ```bash
   python3 test_microphone.py
   ```

2. Запустите бота:
   ```bash
   ./Vikingi_demo.sh
   ```

## Если нужно вернуться к устройству по умолчанию

В `config.py` установите:
```python
INPUT_DEVICE: int | None = None
```

## Если USB-микрофон не работает

1. Проверьте, что микрофон подключен:
   ```bash
   python3 -c "import sounddevice as sd; print(sd.query_devices())"
   ```

2. Проверьте уровень громкости в настройках системы:
   - Ubuntu: Настройки → Звук → Вход
   - Или через терминал:
     ```bash
     pactl list sources | grep -A 10 "USB microphone"
     ```

3. Убедитесь, что микрофон не заблокирован другими приложениями
