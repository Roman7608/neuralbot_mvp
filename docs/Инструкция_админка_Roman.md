# Роль Roman (админка): переводы голосового бота

> **Устарело (2026):** отдельная роль 7 снята с поддержки. Журнал переводов голосового бота и API `/api/voice-bot/*` доступны только **суперадмину (роль 8, логин admin1)**. См. `docs/инструкции/00_ИНСТРУКЦИЯ_ADMIN1.md`, миграция `database/migrations/017_retire_admin_role7.sql`.

## Назначение (исторически)

- Раньше **роль 7** давала доступ только к **«Переводы голосового бота»** (`/voice-transfers`) и `GET /api/voice-bot/*`.
- Сейчас используйте **admin1 (роль 8)**.

## Миграция PostgreSQL

Выполнить на БД аналитики:

```bash
psql -U ... -d vikingi_analytics -f database/migrations/011_voice_bot_analytics_and_role7.sql
```

После миграции допустимы `role_id` от 1 до 7.

## Создание пользователя Roman (пароль не в git)

На сервере задать пароль только через окружение:

```bash
export ROMAN_ADMIN_PASSWORD='сложный_пароль'
# опционально: export ROMAN_ADMIN_LOGIN='roman'
python scripts/create_roman_admin_user.py
```

Либо вручную: `python -m admin_panel.create_admin_user <логин> <пароль> 7`.

## Очистка данных старше 14 суток

Скрипт удаляет строки в `voice_bot_sessions` (каскадом — реплики и события перевода) и пытается удалить файлы/каталоги по полю `audio_storage_path`.

```bash
# при необходимости: export VOICE_BOT_RETENTION_DAYS=14
python scripts/cleanup_voice_bot_analytics.py
```

Рекомендуется не пересекаться по смыслу с другими задачами очистки: этот скрипт затрагивает только таблицы `voice_bot_*`.

## Запись разговора (скачивание из админки)

Эндпоинт `GET /api/voice-bot/sessions/detail/{call_uuid}/recording` отдаёт файл только если:

1. В сессии заполнен `audio_storage_path`.
2. Задана переменная **`VOICE_BOT_RECORDINGS_ROOT`** — абсолютный каталог, внутри которого должен лежать файл (защита от выхода за пределы корня).

Пример:

```bash
export VOICE_BOT_RECORDINGS_ROOT=/var/lib/vikingi/voice_recordings
```

## Контейнер голосового бота и БД

В образ `Dockerfile.voice` добавлены каталог `database/` и `postgresql_config.py`, зависимость `psycopg2-binary`. Для записи в PostgreSQL задайте те же переменные **`POSTGRESQL_*`**, что и у остальных сервисов (хост, порт, БД, пользователь, пароль), и при необходимости `VOICE_BOT_DB_LOG=0` для отключения логирования.

## Страница «Переводы бота»

Файл: `admin_panel/static/voice_transfers.html`.

- Сводка по дням и список сессий.
- Деталка: события перевода (категория, причина админа, WAV, exten) и полный журнал реплик.
- Пагинация и ссылки на экспорт CSV/Excel.

Доработки UI/логики страницы при необходимости можно вынести на потом — базовый функционал уже в репозитории.

---

## Реализовано в репозитории (справочно)

### Уже было / доработано

- **`services/voice/session.py`** — перед `process_client_text("")` в двух ветках (пустой VAD/STT после выбора отдела) добавлен `log_client_text("")`, чтобы пустая реплика попадала в журнал.
- **`database/postgresql_manager.py`** — в `list_sessions` добавлены поля `client_turns` и `bot_turns`.

### Админка и роль 7 (Roman)

- **`admin_panel/auth.py`** — `can_access_voice_transfers` (роли 6 и 7), `is_voice_bot_only_role` (только 7), обновлён комментарий к ролям.
- **`admin_panel/main.py`** — middleware: у роли 7 доступны только статика, логин, `/voice-transfers`, `/api/voice-bot/*`, остальное — редирект на переводы или 403. Редиректы с `/` и `/login` для роли 7. API: список сессий, деталка по `call_uuid`, дневная сводка, экспорт CSV/xlsx, раздача записи при `VOICE_BOT_RECORDINGS_ROOT` и безопасном пути из `audio_storage_path`.
- Навигация «Переводы бота» для ролей 6 и 7: `index.html`, `leads.html`, `analytics.html`, `upload.html`, `sto.html`.
- **`login.html`** — после входа роль 7 сразу на `/voice-transfers`.
- **`admin_panel/static/style.css`** — классы `.mono`, `.button-link`, `.detail-section`.

### БД и скрипты

- **`admin_panel/create_admin_user.py`**, **`create_database_schema.sql`**, комментарий в **`011_voice_bot_analytics_and_role7.sql`** — поддержка `role_id` 1–7.
- **`scripts/create_roman_admin_user.py`** — пароль из `ROMAN_ADMIN_PASSWORD`, логин из `ROMAN_ADMIN_LOGIN` (по умолчанию `roman`).
- **`scripts/cleanup_voice_bot_analytics.py`** — `VoiceBotAnalyticsDB.delete_older_than_days` + удаление файлов/каталогов по путям; срок через `VOICE_BOT_RETENTION_DAYS` (по умолчанию 14).

### Голосовой бот в Docker

- **`Dockerfile.voice`** — копируются `database/` и `postgresql_config.py`.
- **`services/voice/requirements.txt`** — `psycopg2-binary`.

### Документация и compose

- Этот файл — миграция, пользователь, cron-очистка, `VOICE_BOT_RECORDINGS_ROOT`, заметка про `POSTGRESQL_*` для voice.
- **`docker-compose.yml` в репозитории не менялся** (политика проекта). Переменные **`POSTGRESQL_*`** для контейнера voice нужно задать на сервере или отдельно согласовать правку compose.

---

## На вашей стороне (при внедрении)

Когда будете готовы выкатить:

1. Применить миграцию `011_voice_bot_analytics_and_role7.sql`.
2. При необходимости задать **`VOICE_BOT_RECORDINGS_ROOT`** (скачивание записи из админки).
3. Завести пользователя Roman (`scripts/create_roman_admin_user.py` или вручную).
4. Повесить cron на **`scripts/cleanup_voice_bot_analytics.py`**.
5. Для контейнера голосового бота — проброс **`POSTGRESQL_*`** в окружение (если не через правку compose — то иначе, как принято на server7).
