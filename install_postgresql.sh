#!/bin/bash
# Скрипт установки PostgreSQL на Ubuntu

set -e

echo "=========================================="
echo "Установка PostgreSQL на Ubuntu"
echo "=========================================="

# Определение версии Ubuntu
UBUNTU_VERSION=$(lsb_release -rs)
echo "Обнаружена Ubuntu версии: $UBUNTU_VERSION"

# Выбор версии PostgreSQL
echo ""
echo "Доступные варианты:"
echo "1. PostgreSQL 14 (из репозиториев Ubuntu) - ⚠️ поддержка до ноября 2026 (осталось ~10 месяцев)"
echo "2. PostgreSQL 15 (официальный репозиторий) - ✅ рекомендуется, поддержка до ноября 2027"
echo "3. PostgreSQL 16 (официальный репозиторий) - последняя версия, поддержка до ноября 2028"
echo ""
echo "⚠️ ВНИМАНИЕ: PostgreSQL 14 теряет поддержку через ~10 месяцев!"
echo "Рекомендуется выбрать версию 15 или 16."
echo ""
read -p "Выберите версию (1/2/3, по умолчанию 2): " PG_VERSION
PG_VERSION=${PG_VERSION:-2}

if [ "$PG_VERSION" = "2" ] || [ "$PG_VERSION" = "3" ]; then
    # Установка из официального репозитория PostgreSQL
    echo ""
    echo "Добавление официального репозитория PostgreSQL..."
    
    if [ "$PG_VERSION" = "2" ]; then
        PG_VER="15"
    else
        PG_VER="16"
    fi
    
    # Установка необходимых пакетов
    sudo apt install -y wget ca-certificates
    
    # Добавление ключа репозитория
    sudo wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -
    
    # Добавление репозитория
    echo "deb http://apt.postgresql.org/pub/repos/apt/ $(lsb_release -cs)-pgdg main" | sudo tee /etc/apt/sources.list.d/pgdg.list
    
    # Обновление списка пакетов
    sudo apt update
    
    # Установка PostgreSQL выбранной версии
    echo ""
    echo "Установка PostgreSQL $PG_VER..."
    sudo apt install -y postgresql-$PG_VER postgresql-contrib-$PG_VER
else
    # Установка из репозиториев Ubuntu (PostgreSQL 14)
    echo ""
    echo "Установка PostgreSQL 14 из репозиториев Ubuntu..."
    
    # Обновление списка пакетов
    sudo apt update
    
    # Установка PostgreSQL и дополнительных компонентов
    sudo apt install -y postgresql postgresql-contrib
fi

# Проверка версии
echo ""
echo "Проверка установки..."
psql --version

# Проверка статуса службы
echo ""
echo "Проверка статуса службы PostgreSQL..."
sudo systemctl status postgresql --no-pager | head -10

# Включение автозапуска
echo ""
echo "Включение автозапуска PostgreSQL..."
sudo systemctl enable postgresql

# Информация о следующем шаге
echo ""
echo "=========================================="
echo "PostgreSQL успешно установлен!"
echo "=========================================="
echo ""
echo "Установленная версия:"
psql --version
echo ""
echo "Следующие шаги:"
echo "1. Создать базу данных:"
echo "   sudo -u postgres psql"
echo "   CREATE DATABASE vikingi_analytics;"
echo ""
echo "2. Создать пользователей:"
echo "   CREATE USER analytics_user WITH PASSWORD 'your_password';"
echo "   CREATE USER ics_user WITH PASSWORD 'your_password';"
echo ""
echo "3. Выдать права:"
echo "   GRANT ALL PRIVILEGES ON DATABASE vikingi_analytics TO analytics_user;"
echo "   GRANT CONNECT ON DATABASE vikingi_analytics TO ics_user;"
echo ""
echo "4. Выйти из psql:"
echo "   \\q"
echo ""
echo "Пароль по умолчанию для пользователя postgres можно установить командой:"
echo "   sudo passwd postgres"
echo ""
