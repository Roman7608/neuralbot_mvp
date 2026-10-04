#!/bin/bash
# Скрипт для проверки и сброса пароля пользователя

echo "=========================================="
echo "Проверка пароля PostgreSQL"
echo "=========================================="

echo ""
echo "Выберите действие:"
echo "1. Проверить подключение с паролем из конфига"
echo "2. Изменить пароль пользователя analytics_user"
echo "3. Изменить пароль пользователя ics_user"
echo ""
read -p "Выберите (1/2/3): " CHOICE

case $CHOICE in
    1)
        echo ""
        echo "Пароль из postgresql_config.py:"
        grep "POSTGRESQL_ANALYTICS_PASSWORD" postgresql_config.py | head -1
        echo ""
        echo "Попробуйте подключиться вручную:"
        echo "psql -U analytics_user -d vikingi_analytics"
        ;;
    2)
        echo ""
        read -sp "Введите новый пароль для analytics_user: " NEW_PASSWORD
        echo ""
        read -sp "Повторите пароль: " NEW_PASSWORD_CONFIRM
        echo ""
        
        if [ "$NEW_PASSWORD" != "$NEW_PASSWORD_CONFIRM" ]; then
            echo "❌ Пароли не совпадают!"
            exit 1
        fi
        
        echo ""
        echo "Изменение пароля..."
        sudo -u postgres psql <<EOF
ALTER USER analytics_user WITH PASSWORD '$NEW_PASSWORD';
EOF
        
        if [ $? -eq 0 ]; then
            echo "✅ Пароль изменен!"
            echo ""
            echo "Обновите пароль в postgresql_config.py:"
            echo "POSTGRESQL_ANALYTICS_PASSWORD: str = \"$NEW_PASSWORD\""
        else
            echo "❌ Ошибка при изменении пароля"
        fi
        ;;
    3)
        echo ""
        read -sp "Введите новый пароль для ics_user: " NEW_PASSWORD
        echo ""
        read -sp "Повторите пароль: " NEW_PASSWORD_CONFIRM
        echo ""
        
        if [ "$NEW_PASSWORD" != "$NEW_PASSWORD_CONFIRM" ]; then
            echo "❌ Пароли не совпадают!"
            exit 1
        fi
        
        echo ""
        echo "Изменение пароля..."
        sudo -u postgres psql <<EOF
ALTER USER ics_user WITH PASSWORD '$NEW_PASSWORD';
EOF
        
        if [ $? -eq 0 ]; then
            echo "✅ Пароль изменен!"
            echo ""
            echo "Обновите пароль в postgresql_config.py:"
            echo "POSTGRESQL_ICS_PASSWORD: str = \"$NEW_PASSWORD\""
        else
            echo "❌ Ошибка при изменении пароля"
        fi
        ;;
    *)
        echo "Неверный выбор"
        exit 1
        ;;
esac
