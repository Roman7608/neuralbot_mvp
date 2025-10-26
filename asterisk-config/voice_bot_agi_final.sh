#!/bin/bash

# Финальный AGI скрипт для голосового бота с HTTP интеграцией
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

# Захватываем аудио от пользователя
echo "VERBOSE \"Recording user audio...\" 1" >&2

# Используем правильную AGI команду для записи
SAFE_CHANNEL=$(echo "$CHANNEL" | sed 's|/|-|g')
TEMP_AUDIO_FILE="/tmp/user_audio_$SAFE_CHANNEL.wav"

# Используем команду RECORD вместо RECORD FILE
echo "RECORD \"$TEMP_AUDIO_FILE\" wav 5000 0"

# Ждем ответа от Asterisk
read response
echo "VERBOSE \"Record response: $response\" 1" >&2

# Проверяем, что запись прошла успешно
if [ -f "$TEMP_AUDIO_FILE" ]; then
    echo "VERBOSE \"Audio recorded successfully\" 1" >&2
    
    # Кодируем аудио в base64
    AUDIO_BASE64=$(base64 -w 0 "$TEMP_AUDIO_FILE")
    echo "VERBOSE \"Audio encoded to base64, length: ${#AUDIO_BASE64}\" 1" >&2
    
    # Удаляем временный файл
    rm -f "$TEMP_AUDIO_FILE"
else
    echo "VERBOSE \"Failed to record audio, using dummy data\" 1" >&2
    AUDIO_BASE64="dGVzdA=="
fi

# Делаем HTTP запрос к API с таймаутом
echo "VERBOSE \"Making HTTP request to API...\" 1" >&2

# Создаем JSON данные с реальным аудио
JSON_DATA='{"audio_data": "'$AUDIO_BASE64'", "caller_id": "'$CALLER_ID'", "sample_rate": 8000, "language": "ru-RU"}'

# Отправляем запрос к API с таймаутом 10 секунд
# Используем wget вместо curl, так как curl может быть недоступен
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
            
            # Сохраняем аудио ответ в временный файл (заменяем слеши в имени канала)
            SAFE_CHANNEL=$(echo "$CHANNEL" | sed 's|/|-|g')
            BOT_AUDIO_FILE="/tmp/bot_response_$SAFE_CHANNEL.wav"
            echo "$AUDIO_B64" | base64 -d > "$BOT_AUDIO_FILE"
            echo "VERBOSE \"Saved audio to: $BOT_AUDIO_FILE\" 1" >&2
            
            if [ -f "$BOT_AUDIO_FILE" ]; then
                echo "VERBOSE \"Audio file exists, size: $(wc -c < "$BOT_AUDIO_FILE")\" 1" >&2
                echo "VERBOSE \"Playing bot audio response...\" 1" >&2
                # Воспроизводим аудио ответ - используем только имя файла без пути
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
