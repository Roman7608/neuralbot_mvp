# NeuralBot — Starter Repository (MVP skeleton) v1.1

Каркас по ТЗ: FastAPI + Postgres + Alembic + Redis + aiogram + AMI/LLM адаптеры.
Запуск:
1) `cp .env.example .env` и заполнить
2) `docker compose up -d --build`
3) `docker compose exec api alembic upgrade head`

Эндпойнты:
- GET /healthz
- GET /api/departments
- POST /api/leads
- POST /internal/telegram/webhook
 - POST /internal/llm/classify
 - POST /internal/asterisk/events

Админка (MVP JSON endpoints):
- GET /admin/leads, PATCH /admin/leads/{id}
- GET /admin/call_logs
- GET/POST /admin/work_schedule
- GET /admin/logs
- GET /admin/metrics

Конфиг отделов/часов: `config/departments.yml`

Пример `.env` (скопируйте в `.env`):
```
# Database
DB_HOST=db
DB_PORT=5432
DB_NAME=neuralbot
DB_USER=neuralbot
DB_PASSWORD=neuralbot123

# Redis
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

# Telegram Bot
TELEGRAM_BOT_TOKEN=7518879111:AAEn2B40GGwWfQv1qS7dqavc3b7K2ycPVaI

# LLM: GigaChat
LLM_PROVIDER=gigachat
GIGACHAT_CLIENT_ID=your_client_id
GIGACHAT_AUTH_KEY=your_auth_key
GIGACHAT_SCOPE=GIGACHAT_API_PERS

# Speech: Salute (Sber)
SALUTE_CLIENT_ID=your_client_id
SALUTE_AUTH_KEY=your_auth_key
SALUTE_SCOPE=SALUTE_SPEECH_PERS

# Asterisk AMI Configuration
ASTERISK_HOST=host.docker.internal
ASTERISK_PORT=5038
ASTERISK_USERNAME=admin
ASTERISK_PASSWORD=amp111

# Optional: Cloud LLM (if you want to use cloud instead of local)
# LLM_PROVIDER_URL=https://api.openai.com/v1/chat/completions
# LLM_API_KEY=your_api_key_here
```

## Настройка Asterisk

Для работы с телефонией необходимо настроить Asterisk:

1. **Установите Asterisk** на вашем сервере
2. **Настройте AMI** в `/etc/asterisk/manager.conf`:
```ini
[general]
enabled = yes
port = 5038
bind = 0.0.0.0

[admin]
secret = amp111
deny = 0.0.0.0/0.0.0.0
permit = 0.0.0.0/0.0.0.0
read = system,call,log,verbose,agent,user,config,dtmf,reporting,cdr,dialplan
write = system,call,agent,user,config,command,reporting,originate
```

3. **Настройте контексты** в `/etc/asterisk/extensions.conf`:
```ini
[from-pstn]
exten => s,1,NoOp(Входящий звонок от ${CALLERID(num)})
exten => s,n,Set(CALLERID(num)=${CALLERID(num)})
exten => s,n,Set(CALLERID(name)=Звонящий ${CALLERID(num)})
exten => s,n,AGI(agi://localhost:8000/internal/asterisk/events)
exten => s,n,Hangup()

[from-internal]
exten => 001,1,Dial(SIP/001,20)
exten => 002,1,Dial(SIP/002,20)
exten => 003,1,Dial(SIP/003,20)
; ... остальные номера отделов
```

4. **Перезапустите Asterisk**:
```bash
systemctl restart asterisk
```

## API Endpoints для Asterisk

- `POST /internal/asterisk/connect` - подключение к AMI
- `POST /internal/asterisk/disconnect` - отключение от AMI  
- `GET /internal/asterisk/status` - статус подключения
- `POST /internal/asterisk/originate` - инициация звонка
- `POST /internal/asterisk/recording/start` - начало записи
- `POST /internal/asterisk/events` - обработка событий
