#!/bin/bash
# Скрипт для синхронизации пароля в базе данных с конфигом

echo "=========================================="
echo "Синхронизация пароля PostgreSQL"
echo "=========================================="

# Читаем пароль из конфига
ANALYTICS_PASSWORD=$(grep "POSTGRESQL_ANALYTICS_PASSWORD" postgresql_config.py | head -1 | sed -n "s/.*=.*[\"']\(.*\)[\"'].*/\1/p")

if [ -z "$ANALYTICS_PASSWORD" ]; then
    echo "❌ Не удалось прочитать пароль из postgresql_config.py"
    exit 1
fi

echo ""
echo "Пароль из конфига: $ANALYTICS_PASSWORD"
echo ""
echo "Обновляю пароль в базе данных..."

# Обновляем пароль в базе данных
sudo -u postgres psql <<EOF
ALTER USER analytics_user WITH PASSWORD '$ANALYTICS_PASSWORD';
EOF

if [ $? -eq 0 ]; then
    echo "✅ Пароль обновлен в базе данных!"
    echo ""
    echo "Проверка подключения..."
    export PGPASSWORD="$ANALYTICS_PASSWORD"
    if psql -U analytics_user -d vikingi_analytics -c "SELECT current_user, current_database();" > /dev/null 2>&1; then
        echo "✅ Подключение успешно!"
        unset PGPASSWORD
    else
        echo "❌ Подключение все еще не работает"
        echo "Попробуйте подключиться вручную:"
        echo "psql -U analytics_user -d vikingi_analytics"
        unset PGPASSWORD
        exit 1
    fi
else
    echo "❌ Ошибка при обновлении пароля"
    exit 1
fi
