#!/bin/bash
# Простое тестирование подключения

echo "Тест подключения к PostgreSQL"
echo "==============================="
echo ""

# Вариант 1: С переменной окружения
echo "Вариант 1: С переменной PGPASSWORD"
export PGPASSWORD="TOP"
psql -U analytics_user -d vikingi_analytics -c "SELECT current_user, current_database(), version();"
unset PGPASSWORD

echo ""
echo "Вариант 2: С явным указанием хоста"
export PGPASSWORD="TOP"
psql -h localhost -U analytics_user -d vikingi_analytics -c "SELECT current_user, current_database();"
unset PGPASSWORD

echo ""
echo "Вариант 3: Через sudo от postgres (без пароля)"
sudo -u postgres psql -d vikingi_analytics -c "SELECT current_user, current_database();"
