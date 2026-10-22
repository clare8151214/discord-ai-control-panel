$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvDir = Join-Path $env:USERPROFILE ".venvs\ollama-discord-bot"
$Python = Join-Path $VenvDir "Scripts\python.exe"

if (-not (Test-Path $Python)) {
    Write-Host "Creating Windows virtual environment: $VenvDir"
    & py.exe -3 -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3 is not installed. Install Python 3.12, then run this script again."
    }
}

Write-Host "Installing dependencies..."
& $Python -m pip install -r (Join-Path $ProjectDir "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed."
}

$EnvFile = Join-Path $ProjectDir ".env"
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $ProjectDir ".env.example") $EnvFile
    Write-Host "Created $EnvFile"
}

Write-Host "Setup complete. Add DISCORD_TOKEN to .env, then run run-windows.ps1."
