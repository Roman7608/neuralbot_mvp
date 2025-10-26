#!/bin/bash

# Упрощенный AGI скрипт для голосового бота без команды RECORD
# Использует MixMonitor для записи аудио
CHANNEL="$1"
CALLER_ID="$2"

# Логирование
echo "VERBOSE \"AGI Script started for channel: $CHANNEL, caller: $CALLER_ID\" 1" >&2

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

# Ждем 3 секунды для записи аудио через MixMonitor
echo "VERBOSE \"Waiting for user speech (MixMonitor will record)...\" 1" >&2
echo "WAIT FOR DIGIT \"3000\""
echo ""

# Ждем ответа от Asterisk
read response
echo "VERBOSE \"Wait response: $response\" 1" >&2

# Делаем HTTP запрос к API с тестовыми данными
echo "VERBOSE \"Making HTTP request to API...\" 1" >&2

# Создаем JSON данные с тестовым аудио (base64 encoded "test")
JSON_DATA='{"audio_data": "dGVzdA==", "caller_id": "'$CALLER_ID'", "sample_rate": 8000, "language": "ru-RU"}'

# Отправляем запрос к API с таймаутом 10 секунд
API_RESPONSE=$(timeout 10 wget -qO- --post-data="$JSON_DATA" \
  --header="Content-Type: application/json" \
  http://api:8000/internal/bot/voice/call 2>&1)

WGET_EXIT_CODE=$?

if [ $WGET_EXIT_CODE -eq 0 ]; then
    echo "VERBOSE \"API Response: $API_RESPONSE\" 1" >&2
    
    # Проверяем ответ
    if echo "$API_RESPONSE" | grep -q '"ok":true'; then
        echo "VERBOSE \"API call successful\" 1" >&2
        
        # Извлекаем аудио ответ (если есть)
        AUDIO_B64=$(echo "$API_RESPONSE" | grep -o '"audio_response":"[^"]*"' | cut -d'"' -f4)
        
        if [ ! -z "$AUDIO_B64" ] && [ "$AUDIO_B64" != "null" ]; then
            echo "VERBOSE \"Audio response received, length: ${#AUDIO_B64}\" 1" >&2
            
            # Сохраняем аудио ответ в временный файл
            SAFE_CHANNEL=$(echo "$CHANNEL" | sed 's|/|-|g')
            BOT_AUDIO_FILE="/tmp/bot_response_$SAFE_CHANNEL.wav"
            echo "$AUDIO_B64" | base64 -d > "$BOT_AUDIO_FILE"
            echo "VERBOSE \"Saved audio to: $BOT_AUDIO_FILE\" 1" >&2
            
            if [ -f "$BOT_AUDIO_FILE" ]; then
                echo "VERBOSE \"Audio file exists, size: $(wc -c < "$BOT_AUDIO_FILE")\" 1" >&2
                echo "VERBOSE \"Playing bot audio response...\" 1" >&2
                
                # Воспроизводим аудио ответ
                BOT_AUDIO_NAME=$(basename "$BOT_AUDIO_FILE")
                echo "STREAM FILE \"$BOT_AUDIO_NAME\" \"\""
                read response
                echo "VERBOSE \"Bot audio playback response: $response\" 1" >&2
                
                # Удаляем временный файл
                rm -f "$BOT_AUDIO_FILE"
            else
                echo "VERBOSE \"Failed to save bot audio file\" 1" >&2
            fi
        fi
        
        # Извлекаем текстовый ответ
        BOT_TEXT=$(echo "$API_RESPONSE" | grep -o '"bot_response":"[^"]*"' | cut -d'"' -f4)
        if [ ! -z "$BOT_TEXT" ]; then
            echo "VERBOSE \"Bot response: $BOT_TEXT\" 1" >&2
        fi
    else
        echo "VERBOSE \"API call failed: $API_RESPONSE\" 1" >&2
    fi
elif [ $WGET_EXIT_CODE -eq 124 ]; then
    echo "VERBOSE \"API request timed out after 10 seconds\" 1" >&2
else
    echo "VERBOSE \"API request failed with exit code: $WGET_EXIT_CODE\" 1" >&2
fi

# Завершаем AGI
echo "VERBOSE \"AGI Script completed\" 1" >&2
echo "HANGUP"
echo ""