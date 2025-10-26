# Настройка голосового бота с GigaChat + Salute Speech

## 🎯 Архитектура

```
Звонок → Asterisk → STT (Salute) → LLM (GigaChat) → TTS (Salute) → Asterisk → Клиент
```

**Время отклика:** < 1 секунда (при оптимизации)

## 📋 Компоненты системы

1. **STT (Speech-to-Text):** Salute Speech (Сбер)
2. **LLM (диалоги):** GigaChat (Сбер)
3. **TTS (Text-to-Speech):** Salute Speech (Сбер)
4. **Безопасность:** все данные остаются в России

---

## 🔧 Настройка GigaChat

### Шаг 1: Создание аккаунта
1. Перейдите на https://developers.sber.ru/
2. Зарегистрируйтесь и подтвердите аккаунт
3. Получите доступ к GigaChat API

### Шаг 2: Создание приложения
```bash
# В Sber Developer Portal:
# 1. Перейдите в "Мои приложения"
# 2. Создайте новое приложение
# 3. Выберите тип: "GigaChat API"
# 4. Получите Client ID и Authorization Key
```

### Шаг 3: Получение ключей
```bash
# В консоли Sber:
# 1. Откройте ваше приложение
# 2. Скопируйте Client ID
# 3. Скопируйте Authorization Key (GUID формат)
```

---

## 🔧 Настройка Salute Speech

### Шаг 1: Создание приложения
1. В том же Sber Developer Portal
2. Создайте новое приложение для Salute Speech
3. Выберите тип: "Salute Speech API"

### Шаг 2: Получение ключей
1. Скопируйте Client ID для Salute Speech
2. Скопируйте Authorization Key для Salute Speech
3. Настройте scope: `SALUTE_SPEECH_PERS`

---

## ⚙️ Конфигурация (.env)

Создайте файл `.env` в корне проекта:

```bash
# Database
DB_NAME=neuralbot
DB_USER=neuralbot
DB_PASS=neuralbot123
DB_HOST=db
DB_PORT=5432

# Redis
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

# Telegram Bot
TELEGRAM_BOT_TOKEN=your_telegram_token

# LLM: GigaChat (Сбер)
LLM_PROVIDER=gigachat
GIGACHAT_CLIENT_ID=your_client_id
GIGACHAT_AUTH_KEY=your_auth_key
GIGACHAT_SCOPE=GIGACHAT_API_PERS

# Speech: Salute Speech (Сбер)
SALUTE_CLIENT_ID=your_client_id
SALUTE_AUTH_KEY=your_auth_key
SALUTE_SCOPE=SALUTE_SPEECH_PERS

# Asterisk AMI Configuration
ASTERISK_HOST=host.docker.internal
ASTERISK_PORT=5038
ASTERISK_USERNAME=admin
ASTERISK_PASSWORD=amp111
```

---

## 🚀 Запуск системы

### Шаг 1: Запуск Docker
```bash
docker compose up -d --build
```

### Шаг 2: Проверка статуса
```bash
curl http://localhost:8000/internal/bot/status
```

Ожидаемый ответ:
```json
{
  "llm_provider": "gigachat",
  "llm_provider_name": "GigaChat",
  "gigachat_configured": true,
  "salute_configured": true,
  "services": {
    "stt": "SaluteSpeech",
    "llm": "GigaChat",
    "tts": "SaluteSpeech"
  }
}
```

### Шаг 3: Тестирование диалога
```bash
curl -X POST "http://localhost:8000/internal/bot/chat" \
  -H "Content-Type: application/json" \
  -d '{"message":"Хочу купить Chery"}'
```

---

## 🧪 Тестирование компонентов

### Тест TTS (синтез речи)
```bash
curl -X POST "http://localhost:8000/internal/bot/tts" \
  -H "Content-Type: application/json" \
  -d '{"text":"Привет, это тест синтеза речи"}' \
  --output test_audio.wav
```

### Тест STT (распознавание речи)
```bash
# Сначала создайте base64 аудио файл
base64 -i your_audio.wav > audio_base64.txt

curl -X POST "http://localhost:8000/internal/bot/stt" \
  -H "Content-Type: application/json" \
  -d '{"audio_base64":"'$(cat audio_base64.txt)'","sample_rate_hz":8000}'
```

### Тест полного голосового диалога
```bash
curl -X POST "http://localhost:8000/internal/bot/voice/chat" \
  -H "Content-Type: application/json" \
  -d '{"message":"Хочу купить автомобиль Chery"}' \
  --output bot_response.wav
```

---

## 🔍 Отладка

### Проверка логов
```bash
# API логи
docker compose logs api --follow

# Проверка подключения к сервисам
curl http://localhost:8000/internal/bot/status
```

### Частые проблемы

1. **Ошибка 401 Unauthorized**
   - Проверьте правильность Client ID и Auth Key
   - Убедитесь, что приложение активировано в Sber Developer Portal

2. **Ошибка подключения**
   - Проверьте интернет-соединение
   - Убедитесь, что API ключи действительны

3. **Проблемы с TTS**
   - Проверьте права доступа для Salute Speech
   - Убедитесь, что scope настроен правильно

---

## 📊 Мониторинг

### Метрики использования
- Время ответа GigaChat
- Качество распознавания Salute STT
- Успешность синтеза Salute TTS
- Общее время обработки звонка

### Логирование
Все запросы к API логируются в базу данных:
- `llm_usage` - использование GigaChat
- `audit_logs` - системные события
- `call_logs` - информация о звонках

---

## 🎯 Преимущества российских сервисов

### Безопасность
- Данные не покидают статистику России
- Соответствие требованиям локализации
- Контроль над персональными данными

### Качество
- Оптимизация для русского языка
- Естественные голоса
- Высокое качество распознавания

### Надежность
- Стабильная работа
- Техническая поддержка на русском языке
- Прозрачное ценообразование

---

## 📞 Поддержка

При возникновении проблем:
1. Проверьте логи API: `docker compose logs api`
2. Убедитесь в правильности конфигурации: `curl http://localhost:8000/internal/bot/status`
3. Проверьте подключение к интернету
4. Обратитесь к документации Sber Developer Portal

**Система готова к работе с российскими ИИ-сервисами!**