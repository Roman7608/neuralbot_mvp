@echo off
REM Скрипт для запуска Telegram-бота (Windows)
REM Автоматически устанавливает зависимости и запускает бота

echo ========================================
echo   Telegram-бот Vikingi
echo ========================================
echo.

REM Переходим в директорию скрипта
cd /d "%~dp0"

REM Проверяем наличие Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ОШИБКА: Python не найден!
    echo Установите Python 3.8 или выше
    pause
    exit /b 1
)

echo [1/2] Проверка и установка зависимостей...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements_telegram.txt
if errorlevel 1 (
    echo ОШИБКА: Не удалось установить зависимости!
    pause
    exit /b 1
)

echo.
echo [2/2] Запуск Telegram-бота...
echo.
python -m telegram_bot.bot

if errorlevel 1 (
    echo.
    echo ОШИБКА: Бот завершился с ошибкой!
    pause
    exit /b 1
)

pause
