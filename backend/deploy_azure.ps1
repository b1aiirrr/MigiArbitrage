# Deploy and Configure MigiArbitrage Backend on Azure App Service
#
# SECURITY: All secrets are read from environment variables.
# Set them before running, e.g.:
#   $env:BINANCE_API_KEY = "..."
#   $env:BINANCE_API_SECRET = "..."
#   ... etc
$ErrorActionPreference = "Stop"

$RESOURCE_GROUP = "MigiArbitrageRG3"
$APP_NAME = "migiarbitrage-api-oNpGL"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  MigiArbitrage Azure Deployment & Config" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# ── Validate required secrets are present in environment ──
$requiredEnvVars = @(
    "BINANCE_API_KEY", "BINANCE_API_SECRET",
    "OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE",
    "MEXC_API_KEY", "MEXC_API_SECRET",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"
)

$missing = @()
foreach ($var in $requiredEnvVars) {
    if (-not (Test-Path "env:$var")) {
        $missing += $var
    }
}

if ($missing.Count -gt 0) {
    Write-Host "`n[ERROR] The following required environment variables are not set:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "   - $_" -ForegroundColor Red }
    Write-Host "`nSet them before running this script (never hardcode secrets)." -ForegroundColor Yellow
    exit 1
}

Write-Host "`n[OK] All required environment variables present." -ForegroundColor Green

# 1. Enable WebSockets on Azure App Service
Write-Host "`n[1/4] Enabling WebSockets on $APP_NAME..." -ForegroundColor Yellow
az webapp config set `
    --resource-group $RESOURCE_GROUP `
    --name $APP_NAME `
    --web-sockets-enabled true `
    --output none

Write-Host "✅ WebSockets enabled successfully!" -ForegroundColor Green

# 2. Update Application Settings (API Keys & Config) — pulled from env vars
Write-Host "`n[2/4] Updating environment variables and API keys..." -ForegroundColor Yellow
az webapp config appsettings set `
    --resource-group $RESOURCE_GROUP `
    --name $APP_NAME `
    --settings `
        BINANCE_API_KEY="$env:BINANCE_API_KEY" `
        BINANCE_API_SECRET="$env:BINANCE_API_SECRET" `
        OKX_API_KEY="$env:OKX_API_KEY" `
        OKX_API_SECRET="$env:OKX_API_SECRET" `
        OKX_API_PASSPHRASE="$env:OKX_API_PASSPHRASE" `
        MEXC_API_KEY="$env:MEXC_API_KEY" `
        MEXC_API_SECRET="$env:MEXC_API_SECRET" `
        TELEGRAM_BOT_TOKEN="$env:TELEGRAM_BOT_TOKEN" `
        TELEGRAM_CHAT_ID="$env:TELEGRAM_CHAT_ID" `
        TRIANGULAR_ENABLED="true" `
        EXECUTOR_ENABLED="true" `
        EXECUTOR_LIVE="false" `
        MAX_TRADE_USD="5" `
    --output none

Write-Host "✅ Environment variables updated successfully!" -ForegroundColor Green

# 3. Create fresh zip package of backend and deploy
Write-Host "`n[3/4] Creating deployment zip and uploading..." -ForegroundColor Yellow
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptDir

$zipPath = Join-Path $scriptDir "backend.zip"
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

# Compress python files and requirements (exclude local test helpers)
$filesToZip = Get-ChildItem -Path $scriptDir -File | Where-Object { 
    $_.Extension -in @(".py", ".txt", ".env", ".json") -and 
    $_.Name -notlike "*.zip" -and 
    $_.Name -notlike "_test_*.py"
}
Compress-Archive -Path $filesToZip.FullName -DestinationPath $zipPath -Force

Write-Host "Deploying zip package to Azure ($zipPath)..." -ForegroundColor Yellow
az webapp deploy `
    --resource-group $RESOURCE_GROUP `
    --name $APP_NAME `
    --src-path $zipPath `
    --type zip `
    --async true `
    --output none

Write-Host "✅ Code deployed successfully!" -ForegroundColor Green

# 4. Restart Azure WebApp
Write-Host "`n[4/4] Restarting $APP_NAME..." -ForegroundColor Yellow
az webapp restart `
    --resource-group $RESOURCE_GROUP `
    --name $APP_NAME `
    --output none

Write-Host "✅ Azure App Service restarted!" -ForegroundColor Green
Write-Host "`n==========================================" -ForegroundColor Cyan
Write-Host "  Deployment Complete! System is Online." -ForegroundColor Cyan
Write-Host "  URL: wss://migiarbitrage-api-onpgl.azurewebsites.net" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
