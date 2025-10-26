# Установка Asterisk локально для тестирования

## 📦 Требования

- **Docker Desktop** должен быть запущен
- **Свободное место на диске:** ~2-3 GB
- **Свободная RAM:** ~1 GB

## 🚀 Запуск Asterisk

### Шаг 1: Запустите Docker контейнеры

```powershell
docker compose -f docker-compose.test.yml up -d
```

Это запустит:
- ✅ PostgreSQL (база данных)
- ✅ Redis (кэш)
- ✅ **Asterisk** (телефония)
- ✅ API (ваш бот)
- ✅ Worker

### Шаг 2: Проверьте, что Asterisk запустился

```powershell
docker compose -f docker-compose.test.yml ps
```

Должны увидеть все контейнеры в статусе `Up`:
```
NAME                        STATUS
neuralbot_asterisk_test     Up (healthy)
neuralbot_api_test          Up
neuralbot_db_test           Up (healthy)
neuralbot_redis_test        Up
neuralbot_worker_test       Up
```

### Шаг 3: Проверьте логи Asterisk

```powershell
docker compose -f docker-compose.test.yml logs asterisk
```

Должны увидеть:
```
Asterisk Ready.
AMI enabled on port 5038
```

### Шаг 4: Подключите бота к Asterisk

```powershell
curl.exe -X POST http://localhost:8000/internal/asterisk/connect
```

**Ожидаемый ответ:**
```json
{
  "ok": true,
  "message": "Подключен к Asterisk AMI"
}
```

### Шаг 5: Проверьте статус подключения

```powershell
curl.exe http://localhost:8000/internal/asterisk/status
```

**Ожидаемый ответ:**
```json
{
  "connected": true,
  "host": "asterisk",
  "port": 5038,
  "username": "admin"
}
```

## 🧪 Тестирование

### Тест 1: Симуляция входящего звонка

```powershell
curl.exe --% -X POST "http://localhost:8000/internal/asterisk/events" -H "Content-Type: application/json" -d "{\"Event\":\"Newchannel\",\"Channel\":\"SIP/test-00000001\",\"CallerIDNum\":\"+79991234567\",\"Context\":\"from-pstn\"}"
```

**Что произойдет:**
1. Создастся запись в `call_logs`
2. Создастся новый лид с номером `+79991234567`
3. Лид будет маршрутизирован в соответствующий отдел

### Тест 2: Проверка созданного лида

```powershell
curl.exe http://localhost:8000/admin/leads
```

Должен появиться новый лид:
```json
{
  "id": 1,
  "name": "Звонящий +79991234567",
  "phone": "+79991234567",
  "source": "phone",
  "department_code": "consultant_fallback",
  "status": "new"
}
```

### Тест 3: Проверка логов звонков

```powershell
curl.exe http://localhost:8000/admin/call_logs
```

Должна появиться запись о звонке:
```json
{
  "id": 1,
  "caller_id": "+79991234567",
  "channel": "SIP/test-00000001",
  "direction": "inbound",
  "status": "ringing",
  "started_at": "2025-10-08T11:30:00"
}
```

### Тест 4: Статистика звонков

```powershell
curl.exe http://localhost:8000/admin/call_stats
```

## 🔧 Полезные команды

### Посмотреть логи всех сервисов:
```powershell
docker compose -f docker-compose.test.yml logs -f
```

### Перезапустить только Asterisk:
```powershell
docker compose -f docker-compose.test.yml restart asterisk
```

### Войти в контейнер Asterisk:
```powershell
docker exec -it neuralbot_asterisk_test bash
```

Внутри контейнера можно запустить CLI Asterisk:
```bash
asterisk -rvvv
```

Команды Asterisk CLI:
```
core show channels    # Показать активные каналы
manager show users    # Показать пользователей AMI
sip show peers        # Показать SIP пиры
dialplan show         # Показать dial plan
```

### Остановить все контейнеры:
```powershell
docker compose -f docker-compose.test.yml down
```

### Остановить и удалить все данные:
```powershell
docker compose -f docker-compose.test.yml down -v
```

## 📊 Проверка в админке

Откройте браузер: http://localhost:8000/admin/

Вы должны увидеть:
- **Лиды** - с источником "phone"
- **Звонки** - в разделе call logs (если добавить в интерфейс)
- **Метрики** - статистика звонков

## ❗ Устранение проблем

### Asterisk не запускается

**Проблема:** `Error starting userland proxy`

**Решение:** Порт 5038 уже занят. Остановите другие сервисы или измените порт в `docker-compose.test.yml`:
```yaml
ports:
  - "5039:5038"  # Используйте другой порт
```

### AMI не подключается

**Проблема:** `connection refused`

**Решение:**
1. Проверьте, что Asterisk запущен: `docker compose -f docker-compose.test.yml ps`
2. Проверьте логи: `docker compose -f docker-compose.test.yml logs asterisk`
3. Проверьте конфигурацию: `asterisk-test-config/manager.conf`

### API не видит Asterisk

**Проблема:** `ASTERISK_HOST` неправильный

**Решение:** В контейнерах используйте имя сервиса `asterisk`, а не `host.docker.internal`

## 💾 Размер на диске

После запуска проверьте размер:
```powershell
docker system df
```

**Ожидаемое потребление:**
- Images: ~1.5 GB
- Containers: ~500 MB
- Volumes: ~200 MB
- **Итого: ~2-3 GB**

## 🔄 Следующие шаги

После успешного тестирования локально:
1. Настройте production на VPS
2. Подключите реальный SIP-транк
3. Настройте виртуальные номера
4. Добавьте мониторинг

## 📚 Дополнительно

- Asterisk документация: https://wiki.asterisk.org/
- FreePBX (для production): https://www.freepbx.org/
- Issabel (альтернатива): https://www.issabel.org/

