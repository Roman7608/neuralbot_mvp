#!/bin/bash
# Скрипт проверки подключения к PostgreSQL

echo "=========================================="
echo "Проверка подключения к PostgreSQL"
echo "=========================================="

echo ""
echo "Проверка подключения от analytics_user..."
if psql -U analytics_user -d vikingi_analytics -c "SELECT version();" > /dev/null 2>&1; then
    echo "✅ Подключение успешно!"
    psql -U analytics_user -d vikingi_analytics -c "SELECT current_database(), current_user, version();"
else
    echo "❌ Ошибка подключения. Проверьте пароль."
    echo "Подключение с запросом пароля:"
    psql -U analytics_user -d vikingi_analytics -c "SELECT version();"
fi

echo ""
echo "Проверка подключения от ics_user..."
if psql -U ics_user -d vikingi_analytics -c "SELECT version();" > /dev/null 2>&1; then
    echo "✅ Подключение успешно!"
    psql -U ics_user -d vikingi_analytics -c "SELECT current_database(), current_user, version();"
else
    echo "❌ Ошибка подключения. Проверьте пароль."
    echo "Подключение с запросом пароля:"
    psql -U ics_user -d vikingi_analytics -c "SELECT version();"
fi

echo ""
echo "=========================================="
echo "Проверка завершена"
echo "=========================================="
