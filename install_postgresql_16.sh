#!/bin/bash
# Скрипт установки PostgreSQL 16 на Ubuntu

set -e

echo "=========================================="
echo "Установка PostgreSQL 16 на Ubuntu"
echo "=========================================="

# Определение версии Ubuntu
UBUNTU_VERSION=$(lsb_release -rs)
echo "Обнаружена Ubuntu версии: $UBUNTU_VERSION"

# Установка необходимых пакетов
echo ""
echo "Установка необходимых пакетов..."
sudo apt update
sudo apt install -y wget ca-certificates

# Добавление официального репозитория PostgreSQL
echo ""
echo "Добавление официального репозитория PostgreSQL..."

# Добавление ключа репозитория
echo "Импорт ключа репозитория..."
sudo wget --quiet -O - https://www.postgresql.org/media/keys/ACCC4CF8.asc | sudo apt-key add -

# Добавление репозитория
echo "Добавление репозитория в sources.list..."
echo "deb http://apt.postgresql.org/pub/repos/apt/ $(lsb_release -cs)-pgdg main" | sudo tee /etc/apt/sources.list.d/pgdg.list

# Обновление списка пакетов
echo ""
echo "Обновление списка пакетов..."
sudo apt update

# Установка PostgreSQL 16
echo ""
echo "Установка PostgreSQL 16..."
sudo apt install -y postgresql-16 postgresql-contrib-16

# Проверка версии
echo ""
echo "=========================================="
echo "Проверка установки..."
echo "=========================================="
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
echo "PostgreSQL 16 успешно установлен!"
echo "=========================================="
echo ""
echo "Установленная версия:"
psql --version
echo ""
echo "Следующие шаги:"
echo ""
echo "1. Создать базу данных:"
echo "   sudo -u postgres psql"
echo "   CREATE DATABASE vikingi_analytics;"
echo ""
echo "2. Создать пользователей:"
echo "   CREATE USER analytics_user WITH PASSWORD 'your_secure_password';"
echo "   CREATE USER ics_user WITH PASSWORD 'your_secure_password';"
echo ""
echo "3. Выдать права:"
echo "   GRANT ALL PRIVILEGES ON DATABASE vikingi_analytics TO analytics_user;"
echo "   GRANT CONNECT ON DATABASE vikingi_analytics TO ics_user;"
echo ""
echo "4. Выйти из psql:"
echo "   \\q"
echo ""
echo "=========================================="
echo "Готово!"
echo "=========================================="
