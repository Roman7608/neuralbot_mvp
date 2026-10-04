# Watchdog SIP: Asterisk ↔ Инфолада

После обрыва сети Asterisk может перестать регистрироваться (статус **Rejected** / попытки прекращены). Контейнер при этом остаётся **healthy**, звонки на бота дают гудки.

## Что сделано в проекте

| Компонент | Назначение |
|-----------|------------|
| `scripts/ensure_asterisk_sip_registration.sh` | Проверка Registered → `pjsip send register` → при неудаче `docker compose restart asterisk` |
| `docker-compose.yml` (asterisk healthcheck) | `unhealthy`, если нет **Registered** |
| `./vikingi_manage.sh sip-status` | Статус регистрации |
| `./vikingi_manage.sh sip-fix` | Ручной запуск watchdog |

## Установка cron (каждые 3 минуты)

На server7 из каталога проекта:

```bash
chmod +x scripts/ensure_asterisk_sip_registration.sh scripts/install_asterisk_sip_watchdog_cron.sh
./scripts/install_asterisk_sip_watchdog_cron.sh
```

Или вручную (`crontab -e`):

```cron
*/3 * * * * /home/vikingi/VikingiAll/scripts/ensure_asterisk_sip_registration.sh >> /var/log/vikingi-sip-watch.log 2>&1
```

Лог без root: `VIKINGI_SIP_WATCH_LOG=$HOME/vikingi-sip-watch.log ./scripts/install_asterisk_sip_watchdog_cron.sh`

## Проверка

```bash
docker exec vikingiall-asterisk-1 asterisk -rx "pjsip show registrations"
./vikingi_manage.sh sip-status
docker compose ps asterisk   # healthy только при Registered
tail -f /var/log/vikingi-sip-watch.log
```

Ожидается строка **Registered** (не Rejected / Unregistered).

## После смены healthcheck

Пересоздать контейнер asterisk, чтобы подхватить healthcheck:

```bash
docker compose up -d asterisk
```

Полная пересборка не нужна.

## Ограничения

- Скрипт не чинит неверный логин/пароль SIP (403 от Инфолады) — только сеть и «застрявшую» регистрацию.
- Маршруты DID в Инфоладе скрипт не проверяет.
- До 3 минут простоя возможны, пока не сработает cron (при обрыве без restart asterisk).
