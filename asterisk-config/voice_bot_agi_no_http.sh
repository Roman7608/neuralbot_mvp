#!/bin/bash

# AGI скрипт без HTTP запросов - только воспроизведение
CHANNEL="$1"
CALLER_ID="$2"

# Логирование
echo "VERBOSE \"No-HTTP AGI Script started for channel: $CHANNEL, caller: $CALLER_ID\" 1" >&2

# Читаем AGI переменные
while read line; do
    if [ -z "$line" ]; then
        break
    fi
done

# Отвечаем на звонок
echo "ANSWER"
echo ""

# Воспроизводим приветствие
echo "STREAM FILE \"hello-world\" \"\""
echo ""

# Ждем ответа от Asterisk
read response
echo "VERBOSE \"Playback response: $response\" 1" >&2

# Просто ждем немного и воспроизводим заранее подготовленный ответ
echo "VERBOSE \"Waiting for user to speak...\" 1" >&2
echo "WAIT FOR DIGIT \"2000\""
echo ""

# Ждем ответа от Asterisk
read response
echo "VERBOSE \"Wait response: $response\" 1" >&2

# Воспроизводим заранее подготовленный ответ бота
echo "VERBOSE \"Playing bot response...\" 1" >&2

# Создаем простой ответ через TTS (если есть файл) или используем готовый
if [ -f "/usr/share/asterisk/sounds/bot_response.wav" ]; then
    echo "STREAM FILE \"bot_response\" \"\""
else
    # Используем системные звуки
    echo "STREAM FILE \"hello-world\" \"\""
fi

read response
echo "VERBOSE \"Bot response playback: $response\" 1" >&2

# Завершаем AGI
echo "VERBOSE \"No-HTTP AGI Script completed successfully\" 1" >&2
echo "HANGUP"
echo ""
