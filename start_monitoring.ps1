# Script to start Asterisk AMI monitoring after Docker containers start

Write-Host "==================================================" -ForegroundColor Green
Write-Host "  Starting Asterisk AMI Monitoring  " -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Green

# Wait for API to be ready
Write-Host "Checking API availability..." -ForegroundColor Yellow
$maxAttempts = 10
$attempt = 0

while ($attempt -lt $maxAttempts) {
    try {
        $response = Invoke-WebRequest -Uri "http://localhost:8000/healthz" -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            Write-Host "API is available!" -ForegroundColor Green
            break
        }
    } catch {
        $attempt++
        Write-Host "  Attempt $attempt/$maxAttempts..." -ForegroundColor Gray
        Start-Sleep -Seconds 2
    }
}

if ($attempt -eq $maxAttempts) {
    Write-Host "API is not available after $maxAttempts attempts" -ForegroundColor Red
    exit 1
}

# Connect to Asterisk AMI
Write-Host "Connecting to Asterisk AMI..." -ForegroundColor Yellow

try {
    $connectResponse = curl.exe -X POST http://localhost:8000/internal/asterisk/connect 2>&1
    
    if ($connectResponse -match "ok.*true") {
        Write-Host "Successfully connected to Asterisk AMI!" -ForegroundColor Green
        
        # Check status
        $statusResponse = curl.exe http://localhost:8000/internal/asterisk/status 2>&1
        Write-Host "Connection status:" -ForegroundColor Cyan
        Write-Host $statusResponse -ForegroundColor Gray
        
    } else {
        Write-Host "Error connecting to Asterisk AMI" -ForegroundColor Red
        Write-Host $connectResponse -ForegroundColor Red
        exit 1
    }
    
} catch {
    Write-Host "Exception during connection: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "==================================================" -ForegroundColor Green
Write-Host "  Asterisk AMI Monitoring Started!  " -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green
Write-Host ""
Write-Host "To view events, run:" -ForegroundColor Cyan
Write-Host "  docker compose -f docker-compose.test.yml logs api --follow" -ForegroundColor Gray
Write-Host ""
