# PowerShell скрипт полного перезапуска голосового бота
# Останавливает контейнеры, очищает кеш, пересобирает и запускает заново

Write-Host "🔄 Полный перезапуск голосового бота" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan

# Останавливаем все контейнеры
Write-Host "⏹️ Останавливаем контейнеры..." -ForegroundColor Yellow
docker compose down

# Очищаем кеш Docker
Write-Host "🧹 Очищаем кеш Docker..." -ForegroundColor Yellow
docker system prune -f
docker volume prune -f

# Удаляем старые образы (опционально)
$response = Read-Host "🗑️ Удалить старые образы? (y/N)"
if ($response -eq "y" -or $response -eq "Y") {
    docker image prune -f
}

# Очищаем временные файлы
Write-Host "🧹 Очищаем временные файлы..." -ForegroundColor Yellow
Remove-Item -Path "C:\tmp\user_audio_*.wav" -Force -ErrorAction SilentlyContinue
Remove-Item -Path "C:\tmp\bot_response_*.wav" -Force -ErrorAction SilentlyContinue
Remove-Item -Path "C:\tmp\temp_*.wav" -Force -ErrorAction SilentlyContinue
Remove-Item -Path "test_*.wav" -Force -ErrorAction SilentlyContinue

# Проверяем .env файл
Write-Host "🔍 Проверяем .env файл..." -ForegroundColor Yellow
if (-not (Test-Path ".env")) {
    Write-Host "❌ Файл .env не найден!" -ForegroundColor Red
    Write-Host "Создайте файл .env с необходимыми переменными:" -ForegroundColor Red
    Write-Host "   GIGACHAT_CLIENT_ID=your_client_id" -ForegroundColor Red
    Write-Host "   GIGACHAT_AUTH_KEY=your_auth_key" -ForegroundColor Red
    Write-Host "   SALUTE_CLIENT_ID=your_client_id" -ForegroundColor Red
    Write-Host "   SALUTE_AUTH_KEY=your_auth_key" -ForegroundColor Red
    Write-Host "   ASTERISK_HOST=localhost" -ForegroundColor Red
    Write-Host "   ASTERISK_PORT=5038" -ForegroundColor Red
    Write-Host "   ASTERISK_USERNAME=admin" -ForegroundColor Red
    Write-Host "   ASTERISK_PASSWORD=amp111" -ForegroundColor Red
    exit 1
}

# Проверяем наличие ключей в .env
Write-Host "🔑 Проверяем ключи в .env..." -ForegroundColor Yellow
$envContent = Get-Content ".env" -Raw
if ($envContent -notmatch "GIGACHAT_CLIENT_ID" -or $envContent -notmatch "SALUTE_CLIENT_ID") {
    Write-Host "⚠️ Предупреждение: не все ключи настроены в .env" -ForegroundColor Yellow
}

# Пересобираем и запускаем контейнеры
Write-Host "🔨 Пересобираем и запускаем контейнеры..." -ForegroundColor Yellow
docker compose up -d --build

# Ждем запуска
Write-Host "⏳ Ждем запуска сервисов..." -ForegroundColor Yellow
Start-Sleep -Seconds 10

# Проверяем статус контейнеров
Write-Host "📊 Статус контейнеров:" -ForegroundColor Green
docker compose ps

# Проверяем логи
Write-Host "📋 Последние логи API:" -ForegroundColor Green
docker compose logs --tail=20 api

Write-Host "📋 Последние логи Asterisk:" -ForegroundColor Green
docker compose logs --tail=20 asterisk

# Проверяем доступность API
Write-Host "🔍 Проверяем доступность API..." -ForegroundColor Yellow
Start-Sleep -Seconds 5

try {
    $response = Invoke-RestMethod -Uri "http://localhost:8000/internal/bot/status" -TimeoutSec 10
    Write-Host "✅ API доступен" -ForegroundColor Green
    
    # Получаем статус
    Write-Host "📊 Статус бота:" -ForegroundColor Green
    $response | ConvertTo-Json -Depth 3
} catch {
    Write-Host "❌ API недоступен" -ForegroundColor Red
    Write-Host "Проверьте логи: docker compose logs api" -ForegroundColor Red
}

Write-Host ""
Write-Host "🎯 Следующие шаги:" -ForegroundColor Cyan
Write-Host "   1. Запустите диагностику: python diagnose_audio.py" -ForegroundColor White
Write-Host "   2. Проверьте настройки MicroSIP (кодек G.711)" -ForegroundColor White
Write-Host "   3. Сделайте тестовый звонок на extension 001" -ForegroundColor White
Write-Host "   4. Проверьте записи в /var/spool/asterisk/monitor/" -ForegroundColor White

Write-Host ""
Write-Host "✅ Перезапуск завершен!" -ForegroundColor Green
