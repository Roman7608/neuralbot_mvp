#!/usr/bin/env bash

# Скрипт полного перезапуска голосового бота
# Останавливает контейнеры, очищает кеш, пересобирает и запускает заново

echo "🔄 Полный перезапуск голосового бота"
echo "=================================="

# Останавливаем все контейнеры
echo "⏹️ Останавливаем контейнеры..."
docker compose down

# Очищаем кеш Docker
echo "🧹 Очищаем кеш Docker..."
docker system prune -f
docker volume prune -f

# Удаляем старые образы (опционально)
read -p "🗑️ Удалить старые образы? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    docker image prune -f
fi

# Очищаем временные файлы
echo "🧹 Очищаем временные файлы..."
rm -f /tmp/user_audio_*.wav
rm -f /tmp/bot_response_*.wav
rm -f /tmp/temp_*.wav
rm -f test_*.wav

# Проверяем .env файл
echo "🔍 Проверяем .env файл..."
if [ ! -f ".env" ]; then
    echo "❌ Файл .env не найден!"
    echo "Создайте файл .env с необходимыми переменными:"
    echo "   GIGACHAT_CLIENT_ID=your_client_id"
    echo "   GIGACHAT_AUTH_KEY=your_auth_key"
    echo "   SALUTE_CLIENT_ID=your_client_id"
    echo "   SALUTE_AUTH_KEY=your_auth_key"
    echo "   ASTERISK_HOST=localhost"
    echo "   ASTERISK_PORT=5038"
    echo "   ASTERISK_USERNAME=admin"
    echo "   ASTERISK_PASSWORD=amp111"
    exit 1
fi

# Проверяем наличие ключей в .env
echo "🔑 Проверяем ключи в .env..."
if ! grep -q "GIGACHAT_CLIENT_ID" .env || ! grep -q "SALUTE_CLIENT_ID" .env; then
    echo "⚠️ Предупреждение: не все ключи настроены в .env"
fi

# Пересобираем и запускаем контейнеры
echo "🔨 Пересобираем и запускаем контейнеры..."
docker compose up -d --build

# Ждем запуска
echo "⏳ Ждем запуска сервисов..."
sleep 10

# Проверяем статус контейнеров
echo "📊 Статус контейнеров:"
docker compose ps

# Проверяем логи
echo "📋 Последние логи API:"
docker compose logs --tail=20 api

echo "📋 Последние логи Asterisk:"
docker compose logs --tail=20 asterisk

# Проверяем доступность API
echo "🔍 Проверяем доступность API..."
sleep 5

if curl -s http://localhost:8000/internal/bot/status > /dev/null; then
    echo "✅ API доступен"
    
    # Получаем статус
    echo "📊 Статус бота:"
    curl -s http://localhost:8000/internal/bot/status | jq '.' 2>/dev/null || curl -s http://localhost:8000/internal/bot/status
else
    echo "❌ API недоступен"
    echo "Проверьте логи: docker compose logs api"
fi

echo ""
echo "🎯 Следующие шаги:"
echo "   1. Запустите диагностику: python diagnose_audio.py"
echo "   2. Проверьте настройки MicroSIP (кодек G.711)"
echo "   3. Сделайте тестовый звонок на extension 001"
echo "   4. Проверьте записи в /var/spool/asterisk/monitor/"

echo ""
echo "✅ Перезапуск завершен!"
