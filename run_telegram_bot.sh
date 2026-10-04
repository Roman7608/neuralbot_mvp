#!/bin/bash
# Скрипт для запуска Telegram-бота (Linux)
# Автоматически устанавливает зависимости и запускает бота

echo "========================================"
echo "  Telegram-бот Vikingi"
echo "========================================"
echo ""

# Переходим в директорию скрипта
cd "$(dirname "$0")"

# Проверяем наличие Python
if ! command -v python3 &> /dev/null; then
    echo "ОШИБКА: Python3 не найден!"
    echo "Установите Python 3.8 или выше"
    exit 1
fi

echo "[1/2] Проверка и установка зависимостей..."
python3 -m pip install --upgrade pip --quiet
python3 -m pip install -r requirements_telegram.txt

if [ $? -ne 0 ]; then
    echo "ОШИБКА: Не удалось установить зависимости!"
    exit 1
fi

echo ""
echo "[2/2] Запуск Telegram-бота..."
echo ""
python3 -m telegram_bot.bot

if [ $? -ne 0 ]; then
    echo ""
    echo "ОШИБКА: Бот завершился с ошибкой!"
    exit 1
fi
