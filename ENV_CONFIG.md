# Конфигурация ENV переменных

Создайте файл `.env` в корне проекта со следующими переменными:

## Database
```bash
DB_NAME=neuralbot
DB_USER=neuralbot
DB_PASS=neuralbot123
DB_HOST=db
DB_PORT=5432
```

## Asterisk
```bash
ASTERISK_HOST=asterisk
ASTERISK_PORT=5038
ASTERISK_USERNAME=admin
ASTERISK_PASSWORD=amp111
```

## GigaChat + SaluteSpeech (Sber)

В `.env` укажите ключи GigaChat (LLM) и SaluteSpeech (TTS/STT):

```bash
# LLM: GigaChat
LLM_PROVIDER=gigachat
GIGACHAT_CLIENT_ID=your_client_id
GIGACHAT_AUTH_KEY=your_auth_key
GIGACHAT_SCOPE=GIGACHAT_API_PERS

# Speech: Salute (Sber) — TTS/STT
SALUTE_CLIENT_ID=your_client_id
SALUTE_AUTH_KEY=your_auth_key
SALUTE_SCOPE=SALUTE_SPEECH_PERS
```

Быстрое тестирование:

```bash
docker compose up -d --build
curl http://localhost:8000/healthz
curl http://localhost:8000/internal/bot/status
```


