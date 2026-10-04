#!/bin/bash
# Диагностика подключения к PostgreSQL

echo "=========================================="
echo "Диагностика подключения PostgreSQL"
echo "=========================================="

echo ""
echo "1. Проверка существования пользователя:"
sudo -u postgres psql -c "\du analytics_user"

echo ""
echo "2. Проверка существования базы данных:"
sudo -u postgres psql -c "\l" | grep vikingi_analytics

echo ""
echo "3. Проверка прав пользователя на базу данных:"
sudo -u postgres psql -d vikingi_analytics -c "\du analytics_user"

echo ""
echo "4. Попытка подключения от postgres:"
sudo -u postgres psql -d vikingi_analytics -c "SELECT current_user, current_database();"

echo ""
echo "5. Проверка настроек pg_hba.conf:"
echo "Последние строки из pg_hba.conf:"
sudo tail -5 /etc/postgresql/16/main/pg_hba.conf

echo ""
echo "6. Попытка подключения с явным указанием хоста:"
echo "Введите пароль 'TOP' когда попросит:"
PGPASSWORD="TOP" psql -h localhost -U analytics_user -d vikingi_analytics -c "SELECT current_user, current_database();" 2>&1

echo ""
echo "7. Проверка, слушает ли PostgreSQL на localhost:"
sudo netstat -tlnp | grep 5432 || sudo ss -tlnp | grep 5432
