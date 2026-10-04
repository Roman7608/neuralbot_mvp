# Руководство по формированию бота в MAX

> **Проект Vikingi (server7):** актуальный журнал настроек, `.env`, Docker и отличий от Telegram — в **`docs/MAX_BOT_SETUP.md`** (его и обновлять по MAX).

> MAX — российский мессенджер (85+ млн пользователей), резидент реестра российского ПО.

---

## 1. Требования

### Организация
- Юридическое лицо или ИП, резидент РФ
- ИНН (10 или 12 цифр)
- Корпоративный номер телефона или email
- Верификация через Госуслуги, Alfa ID, Т-Business ID или СберБизнес ID

### Ограничения
- До 5 ботов на одну организацию
- Модерация бота перед публичным доступом
- Webhook — только HTTPS (HTTP не поддерживается)

---

## 2. Регистрация и получение токена

1. Перейти на [MAX для партнёров](https://business.max.ru/) → войти/зарегистрироваться
2. Создать организацию, пройти верификацию
3. **Чат-боты** → создать бота → заполнить: название, аватар, описание
4. После модерации: **Чат-боты** → **Интеграция** → **Получить токен**
5. Токен хранить в секрете; при компрометации — обновить

**Ник бота** формируется автоматически: `idИНН_bot` (например, `id1234567890_bot`).

---

## 3. API MAX

### Базовые параметры
| Параметр | Значение |
|----------|----------|
| Базовый URL | `https://platform-api.max.ru/` |
| Авторизация | Заголовок `Authorization: <token>` |
| Лимит | 30 запросов в секунду |

### Проверка подключения
```bash
curl -H "Authorization: YOUR_BOT_TOKEN" https://platform-api.max.ru/me
```

---

## 4. Получение уведомлений

### Вариант A: Webhook (для production)
- POST `/subscriptions` — регистрация URL для входящих событий
- MAX сам отправляет POST на ваш сервер при событиях
- Требуется HTTPS (допускаются самоподписанные сертификаты)

### Вариант B: Long Polling (для разработки)
- GET `/updates` — периодические запросы к API
- Используется при отладке и тестировании

> Одновременно Webhook и Long Polling использовать нельзя.

---

## 5. Типы событий (update_type)

| Событие | Описание |
|---------|----------|
| `bot_started` | Пользователь запустил бота (в т.ч. по диплинку) |
| `message_created` | Новое сообщение |
| `message_edited` | Редактирование сообщения |
| `message_removed` | Удаление сообщения |
| `message_callback` | Нажатие callback-кнопки |
| `bot_added` | Бот добавлен в групповой чат |
| `bot_removed` | Бот удалён из чата |
| `user_added`, `user_removed` | Изменение участников |
| `chat_title_changed` | Изменение названия чата |

---

## 6. Диплинки

Формат ссылки для запуска бота с параметрами:
```
https://max.ru/<botName>?start=<payload>
```
- `<botName>` — ник бота
- `<payload>` — до 128 символов (параметры, реферальные метки и т.п.)

Примеры:
- `https://max.ru/id1234567890_bot?start=promo_summer2025`
- `https://max.ru/SupportBot?start=ref_user456789`

При переходе бот получает событие `bot_started` с полем `payload`.

---

## 7. Отправка сообщений

### Основные методы
- `sendMessageToUser(userId, text)` — пользователю
- `sendMessageToChat(chatId, text)` — в чат

### Форматирование
- Markdown: `**жирный**`, `_курсив_`, `[ссылка](url)`
- HTML: `<b>`, `<i>`, `<a href="...">`

### Вложения
- Изображения, видео, аудио, файлы
- Загрузка через `uploadImage`, `uploadVideo`, `uploadAudio`, `uploadFile`

### Клавиатура
- Inline-кнопки (callback, link, request_contact, request_geo_location, open_app)
- До 210 кнопок, до 7 в ряду (до 3 для link/open_app/request_*)

---

## 8. Разработка на Python

Официальной Python-библиотеки нет. Используйте REST API:

1. **Приём событий**
   - Webhook: FastAPI/Flask эндпоинт, принимающий POST
   - Long Polling: цикл с GET `/updates`

2. **Отправка**
   ```python
   import httpx
   response = httpx.post(
       "https://platform-api.max.ru/...",  # метод из доки
       headers={"Authorization": BOT_TOKEN},
       json={"chat_id": chat_id, "text": "Привет!"}
   )
   ```

3. **Документация API** — раздел «API» на dev.max.ru (полный список методов и параметров)

---

## 9. Адаптация логики из Telegram-бота (Vikingi)

Для повторного использования логики Vikingi:

1. **Общая бизнес-логика**
   - Вынести в отдельный модуль: лиды, RAG, классификация потребностей, working hours
   - Один модуль вызывается и из Telegram-, и из MAX-обработчиков

2. **Транспортный слой**
   - Telegram: aiogram (или python-telegram-bot)
   - MAX: собственный слой над HTTP API (FastAPI webhook или polling + httpx)

3. **Маппинг событий**
   | Telegram | MAX |
   |----------|-----|
   | message.text | message_created → body.text |
   | callback_query | message_callback |
   | /start | bot_started или message_created с командой |

4. **Общее**
   - База данных (PostgreSQL), лицензирование, конфигурация

---

## 10. Полезные ссылки

| Ресурс | URL |
|--------|-----|
| MAX для бизнеса | https://business.max.ru/ |
| Документация для разработчиков | https://dev.max.ru/ |
| Подключение к платформе | https://dev.max.ru/docs/maxbusiness/connection |
| Подготовка и настройка бота | https://dev.max.ru/docs/chatbots/bots-coding/prepare |
| Hello Bot (JavaScript) | https://dev.max.ru/docs/chatbots/bots-coding/hellobot/js |
| Библиотека JavaScript | https://dev.max.ru/docs/chatbots/bots-coding/library/js |
| npm: @maxhub/max-bot-api | https://www.npmjs.com/package/@maxhub/max-bot-api |

---

## 11. Рекомендации

- **Production:** только Webhook с HTTPS
- **Токен:** хранить в переменных окружения, не в коде
- **Резерв на оператора:** предусмотреть кнопку связи с человеком
- **Модерация:** соблюдать [правила размещения ботов](https://dev.max.ru/docs/...) — запрещён спам, сбор персональных данных без согласия и т.п.
