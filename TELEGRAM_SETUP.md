# Настройка Telegram-бота

## 1. Бот уже создан ✅

- **Имя бота:** Neuro_auto_bot
- **Username:** @Neuro_auto_bot
- **Токен:** `7518879111:AAEn2B40GGwWfQv1qS7dqavc3b7K2ycPVAI`
- **Ссылка:** https://t.me/Neuro_auto_bot

## 2. Токен уже добавлен в .env ✅

Токен автоматически добавлен в файл `.env`:
```
TELEGRAM_BOT_TOKEN=7518879111:AAEn2B40GGwWfQv1qS7dqavc3b7K2ycPVAI
```

## 3. Настройка webhook (опционально)

Для продакшена настройте webhook:
```bash
curl -X POST "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook" \
     -H "Content-Type: application/json" \
     -d '{"url": "https://yourdomain.com/internal/telegram/webhook"}'
```

Для разработки можно использовать ngrok:
```bash
ngrok http 8000
# Скопируйте HTTPS URL и используйте его в webhook
```

## 4. Настройка чатов отделов

В файле `config/departments.yml` укажите реальные `tg_chat_id`:

```yaml
departments:
  - code: sales_chery_tlt
    name: "Продажи Chery Тольятти"
    tg_chat_id: -1001234567890  # ID чата отдела
    # ... остальные поля
```

### Как получить chat_id:

1. Добавьте бота в группу/канал
2. Отправьте сообщение в группу
3. Откройте: `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates`
4. Найдите `chat.id` в ответе

## 5. Тестирование

1. Перезапустите API:
   ```bash
   docker compose restart api
   ```

2. Найдите бота в Telegram по username
3. Отправьте `/start`
4. Попробуйте команды:
   - `/leads` - создать заявку
   - `/sales` - продажи
   - `/service` - сервис
   - `/spares` - запчасти

4. Создайте заявку через веб-форму: http://localhost:8000/widget/
5. Проверьте, что уведомление пришло в чат отдела

## 6. Команды бота

- `/start` - приветствие и меню
- `/leads` - инструкция по созданию заявки
- `/sales` - информация о продажах
- `/service` - информация о сервисе
- `/spares` - информация о запчастях

Бот также понимает естественную речь:
- "Хочу купить Chery" → создаст заявку в отдел продаж Chery
- "Нужен ремонт в Тольятти" → создаст заявку в СТО Тольятти
- "Ищу запчасти" → создаст заявку в отдел запчастей
