# Веб-админка аналитики звонков Vikingi

## Запуск

Из **корня проекта** (Vikingi):

```bash
# Установка зависимостей (если ещё не стоят)
pip install -r requirements-admin.txt

# Миграция БД (таблица admin_users)
psql -h localhost -U analytics_user -d vikingi_analytics -f database/migrations/005_admin_users.sql

# Создать первого пользователя (логин, пароль, роль 1–8; 6=директор, 8=суперадмин admin1)
python -m admin_panel.create_admin_user admin secret 6

# Запуск
uvicorn admin_panel.main:app --reload --host 0.0.0.0 --port 8000
```

Откройте в браузере: **http://localhost:8000**

## Страницы

- **/** — список звонков с фильтрами (дата, отдел, номер)
- **/call/{id}** — карточка звонка: аудио, транскрипция, оценки
- **/upload** — ручная загрузка аудио (ОП/СТО)

## API

- `GET /api/calls` — список (query: date_from, date_to, department, internal_number, status, limit, offset)
- `GET /api/calls/{id}` — детали звонка
- `GET /api/calls/{id}/audio` — скачать/прослушать аудио
- `POST /api/upload` — загрузка файла (form: file, department, call_date?, internal_number?)

## База данных

- Используется PostgreSQL из `postgresql_config.py`, БД `vikingi_analytics`.
- **Единая БД:** при Docker postgres проброшен на хост как порт 5433. Скрипты на хосте (retranscribe и др.) подключаются к той же БД автоматически или через `./run_local.sh`.
- Таблицы: `calls`, `call_transcriptions`, `call_quality_scores` (см. `create_database_schema.sql`).
- Для фильтров по отделу и статусу выполните миграцию:
  ```bash
  psql -U analytics_user -d vikingi_analytics -f database/migrations/003_add_calls_department_source_status.sql
  ```

## Варианты фронтенда

Текущая реализация — **простой HTML + CSS + JS (vanilla)** без сборки:

- Быстро править, не нужен Node/npm.
- Подходит для внутренней админки и демо.

При необходимости позже можно:

1. **HTMX + Jinja2** — подменять фрагменты страницы с сервера, минимум своего JS.
2. **Vue / React** — если понадобится SPA с сложными формами и состоянием.
3. **Tailwind / Bootstrap** — подключить через CDN для более аккуратного вида без переписывания логики.
