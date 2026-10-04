# Следующие шаги после установки PostgreSQL

## ✅ Что уже сделано

- [x] PostgreSQL 16 установлен
- [x] База данных `vikingi_analytics` создана
- [x] Пользователи `analytics_user` и `ics_user` созданы
- [x] Права доступа настроены

## 📝 Что нужно сделать дальше

### 1. Сохранить пароли в конфигурации

Отредактируйте файл `postgresql_config.py` и укажите пароли:

```python
POSTGRESQL_ANALYTICS_PASSWORD: str = "ваш_пароль_analytics"
POSTGRESQL_ICS_PASSWORD: str = "ваш_пароль_ics"
```

⚠️ **Важно:** Не коммитьте файл с паролями в Git! Добавьте в `.gitignore`:
```
postgresql_config.py
```

Или используйте переменные окружения для паролей.

### 2. Проверить подключение

Запустите скрипт проверки:
```bash
./test_postgresql_connection.sh
```

Или проверьте вручную:
```bash
psql -U analytics_user -d vikingi_analytics -c "SELECT version();"
psql -U ics_user -d vikingi_analytics -c "SELECT version();"
```

### 3. Настроить доступ с сервера 1С (если нужно)

Если сервер 1С находится на другом компьютере:

#### 3.1. Настроить файрвол
```bash
# Разрешить доступ только с IP сервера 1С
sudo ufw allow from <IP_сервера_1С> to any port 5432

# Или разрешить доступ со всей сети (менее безопасно)
sudo ufw allow 5432/tcp
```

#### 3.2. Настроить PostgreSQL для удаленного доступа

Отредактируйте `/etc/postgresql/16/main/postgresql.conf`:
```bash
sudo nano /etc/postgresql/16/main/postgresql.conf
```

Найдите и измените:
```
#listen_addresses = 'localhost'
```
на:
```
listen_addresses = '*'  # или конкретный IP
```

#### 3.3. Настроить права доступа

Отредактируйте `/etc/postgresql/16/main/pg_hba.conf`:
```bash
sudo nano /etc/postgresql/16/main/pg_hba.conf
```

Добавьте в конец файла:
```
# Доступ для 1С с сервера 1С
host    vikingi_analytics    ics_user    <IP_сервера_1С>/32    md5
```

#### 3.4. Перезапустить PostgreSQL
```bash
sudo systemctl restart postgresql
```

### 4. Создать схему базы данных

Следующий шаг - создать таблицы для:
- Аналитики звонков
- Telegram-бота (лиды, оценки)
- Напоминаний о записях

### 5. Интеграция с 1С

Настроить подключение в 1С Альфа 6.0:
- Администрирование → Подключение к внешним источникам данных
- Тип: PostgreSQL
- Параметры:
  - Сервер: IP сервера 3
  - Порт: 5432
  - База данных: vikingi_analytics
  - Пользователь: ics_user
  - Пароль: [ваш пароль]

## 📚 Документация

- `postgresql_connection_info.md` - подробная информация о подключении
- `POSTGRESQL_VERSION_GUIDE.md` - информация о версиях и обновлениях

## 🔒 Безопасность

- Используйте сложные пароли
- Ограничьте доступ по IP (через файрвол и pg_hba.conf)
- Используйте SSL для удаленных подключений (рекомендуется)
- Регулярно обновляйте PostgreSQL

## ✅ Чек-лист

- [ ] Пароли сохранены в `postgresql_config.py`
- [ ] Подключение проверено (`test_postgresql_connection.sh`)
- [ ] Файрвол настроен (если нужен удаленный доступ)
- [ ] PostgreSQL настроен для удаленного доступа (если нужно)
- [ ] Права доступа в `pg_hba.conf` настроены
- [ ] Подключение из 1С протестировано (если нужно)
