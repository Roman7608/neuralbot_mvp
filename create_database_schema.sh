#!/bin/bash
# Скрипт создания схемы базы данных PostgreSQL

set -e

echo "=========================================="
echo "Создание схемы базы данных vikingi_analytics"
echo "=========================================="

# Проверка подключения
echo ""
echo "Проверка подключения к базе данных..."

# Читаем пароль из конфига
ANALYTICS_PASSWORD=$(grep "POSTGRESQL_ANALYTICS_PASSWORD" postgresql_config.py | head -1 | sed -n "s/.*=.*[\"']\(.*\)[\"'].*/\1/p")

if [ -z "$ANALYTICS_PASSWORD" ]; then
    echo "Введите пароль для пользователя analytics_user:"
    read -sp "Пароль: " ANALYTICS_PASSWORD
    echo ""
fi

# Установка переменной окружения для пароля
export PGPASSWORD="$ANALYTICS_PASSWORD"

# Проверка подключения с явным указанием хоста
if ! psql -h localhost -U analytics_user -d vikingi_analytics -c "SELECT version();" > /dev/null 2>&1; then
    echo "❌ Ошибка подключения к базе данных!"
    echo "Проверьте правильность пароля"
    unset PGPASSWORD
    exit 1
fi

echo "✅ Подключение успешно!"

# Создание схемы с явным указанием хоста
echo ""
echo "Создание таблиц и схемы..."
psql -h localhost -U analytics_user -d vikingi_analytics -f create_database_schema.sql

# Очистка переменной окружения
unset PGPASSWORD

if [ $? -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "✅ Схема базы данных успешно создана!"
    echo "=========================================="
    echo ""
    echo "Созданные таблицы:"
    echo "  - calls (записи звонков)"
    echo "  - call_transcriptions (транскрипции)"
    echo "  - call_quality_scores (оценки качества)"
    echo "  - call_metadata (метаданные звонков)"
    echo "  - telegram_leads (лиды из Telegram)"
    echo "  - telegram_photos (фото от клиентов)"
    echo "  - appointment_reminders (напоминания)"
    echo "  - post_visit_feedback (оценки после визита)"
    echo "  - call_processing_logs (логи обработки)"
    echo "  - processing_statistics (статистика)"
    echo ""
    echo "Проверка таблиц:"
    export PGPASSWORD="$ANALYTICS_PASSWORD"
    psql -h localhost -U analytics_user -d vikingi_analytics -c "\dt"
    unset PGPASSWORD
else
    echo ""
    echo "❌ Ошибка при создании схемы!"
    exit 1
fi
