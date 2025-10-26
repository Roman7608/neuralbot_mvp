#!/bin/bash

# Простой AGI скрипт для голосового бота
# Получаем аргументы от Asterisk
CHANNEL="$1"
CALLER_ID="$2"

# Логирование
echo "VERBOSE \"AGI Script started for channel: $CHANNEL, caller: $CALLER_ID\" 1" >&2

# Читаем AGI переменные
while read line; do
    echo "VERBOSE \"AGI Input: $line\" 1" >&2
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

# Делаем HTTP запрос к API
echo "VERBOSE \"Making HTTP request to API...\" 1" >&2

# Создаем JSON данные
JSON_DATA='{"audio_data": "dGVzdA==", "caller_id": "'$CALLER_ID'", "sample_rate": 8000, "language": "ru-RU"}'

# Отправляем запрос к API
API_RESPONSE=$(/usr/bin/curl -s -X POST \
  -H "Content-Type: application/json" \
  -d "$JSON_DATA" \
  http://api:8000/internal/bot/voice/call)

echo "VERBOSE \"API Response: $API_RESPONSE\" 1" >&2

# Проверяем ответ
if echo "$API_RESPONSE" | grep -q '"ok":true'; then
    echo "VERBOSE \"API call successful\" 1" >&2
    
    # Извлекаем аудио ответ (если есть)
    AUDIO_B64=$(echo "$API_RESPONSE" | grep -o '"audio_response":"[^"]*"' | cut -d'"' -f4)
    
    if [ ! -z "$AUDIO_B64" ] && [ "$AUDIO_B64" != "null" ]; then
        echo "VERBOSE \"Audio response received, length: ${#AUDIO_B64}\" 1" >&2
        # Здесь можно было бы сохранить аудио и воспроизвести его
        # Но для простоты пока просто логируем
    fi
    
    # Извлекаем текстовый ответ
    BOT_TEXT=$(echo "$API_RESPONSE" | grep -o '"bot_response":"[^"]*"' | cut -d'"' -f4)
    if [ ! -z "$BOT_TEXT" ]; then
        echo "VERBOSE \"Bot response: $BOT_TEXT\" 1" >&2
    fi
else
    echo "VERBOSE \"API call failed\" 1" >&2
fi

# Завершаем AGI
echo "VERBOSE \"AGI Script completed\" 1" >&2
echo "HANGUP"
echo ""
