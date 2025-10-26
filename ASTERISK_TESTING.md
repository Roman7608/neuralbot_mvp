# Тестирование Asterisk интеграции

## Предварительные требования

Убедитесь, что в вашем `.env` файле есть настройки Asterisk:

```env
ASTERISK_HOST=host.docker.internal
ASTERISK_PORT=5038
ASTERISK_USERNAME=admin
ASTERISK_PASSWORD=amp111
```

## 1. Перезапуск API с новыми настройками

```powershell
docker compose up -d --force-recreate
```

## 2. Проверка статуса подключения к Asterisk

```powershell
curl.exe http://localhost:8000/internal/asterisk/status
```

**Ожидаемый ответ:**
```json
{
  "connected": false,
  "host": "host.docker.internal",
  "port": 5038,
  "username": "admin"
}
```

## 3. Подключение к Asterisk AMI

```powershell
curl.exe -X POST http://localhost:8000/internal/asterisk/connect
```

**Если Asterisk установлен и настроен:**
```json
{
  "ok": true,
  "message": "Подключен к Asterisk AMI"
}
```

**Если Asterisk не установлен:**
```json
{
  "detail": "Ошибка подключения к Asterisk"
}
```

## 4. Тестирование без реального Asterisk

Если у вас нет Asterisk, можно симулировать звонки через API:

### Создание тестового звонка
```powershell
$headers = @{"Content-Type"="application/json"}
$body = @{
    Event = "Newchannel"
    Channel = "SIP/test-00000001"
    CallerIDNum = "+79991234567"
    Context = "from-pstn"
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "http://localhost:8000/internal/asterisk/events" -Headers $headers -Body $body
```

### Создание события завершения звонка
```powershell
$body = @{
    Event = "Hangup"
    Channel = "SIP/test-00000001"
    Cause = "16"
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri "http://localhost:8000/internal/asterisk/events" -Headers $headers -Body $body
```

## 5. Проверка логов звонков в админке

### Получить список звонков
```powershell
curl.exe http://localhost:8000/admin/call_logs
```

### Получить статистику звонков
```powershell
curl.exe http://localhost:8000/admin/call_stats
```

**Ожидаемый ответ:**
```json
{
  "total_calls": 1,
  "today_calls": 1,
  "status_stats": {"ringing": 1},
  "direction_stats": {"inbound": 1},
  "week_stats": [
    {"date": "2025-10-08", "count": 1}
  ]
}
```

## 6. Проверка создания лидов из звонков

### Получить список лидов
```powershell
curl.exe http://localhost:8000/admin/leads
```

После симуляции звонка должен появиться новый лид с:
- `source: "phone"`
- `phone: "+79991234567"`
- `name: "Звонящий +79991234567"`

## 7. Проверка в веб-админке

Откройте в браузере: http://localhost:8000/admin/

Должны быть видны:
- **Лиды** из звонков
- **Звонки** в разделе call logs (если добавить в интерфейс)
- **Метрики** со статистикой звонков

## Возможные проблемы

### Ошибка подключения к Asterisk
- Проверьте, что Asterisk установлен и запущен
- Убедитесь, что AMI настроен в `/etc/asterisk/manager.conf`
- Проверьте логин и пароль в `.env`

### Звонки не создают лиды
- Проверьте логи API: `docker compose logs api --tail=50`
- Убедитесь, что `Context = "from-pstn"` в событии

### Не видно статистики
- Убедитесь, что миграции применены: `docker compose exec api alembic upgrade head`
- Проверьте, что таблица `call_logs` существует в БД

## Следующие шаги

После успешного тестирования:
1. Настройте реальный Asterisk для production
2. Добавьте визуализацию звонков в админку
3. Настройте уведомления в Telegram для звонков
4. Добавьте интеграцию с записями звонков
















