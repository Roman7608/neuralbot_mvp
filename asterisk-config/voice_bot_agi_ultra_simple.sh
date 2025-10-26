#!/bin/bash

# Максимально простой AGI скрипт - только воспроизведение
CHANNEL="$1"
CALLER_ID="$2"

# Логирование
echo "VERBOSE \"Ultra Simple AGI Script started for channel: $CHANNEL, caller: $CALLER_ID\" 1" >&2

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

# Сразу воспроизводим ответ бота (без ожидания)
echo "VERBOSE \"Playing bot response immediately...\" 1" >&2
echo "STREAM FILE \"hello-world\" \"\""
echo ""

# Ждем ответа от Asterisk
read response
echo "VERBOSE \"Bot response playback: $response\" 1" >&2

# Завершаем AGI
echo "VERBOSE \"Ultra Simple AGI Script completed successfully\" 1" >&2
echo "HANGUP"
echo ""
