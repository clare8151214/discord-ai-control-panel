$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $env:USERPROFILE ".venvs\ollama-discord-bot\Scripts\python.exe"

if (-not (Test-Path $Python)) {
    throw "Windows virtual environment not found. Run setup-windows.ps1 first."
}

$Existing = @(
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -like "*ollama-discord-bot*bot.py*"
        }
)
if ($Existing.Count -gt 0) {
    $Ids = ($Existing.ProcessId -join ", ")
    throw "Bot is already running (PID: $Ids). Run stop-windows.ps1 before restarting."
}

& $Python (Join-Path $ProjectDir "bot.py")
exit $LASTEXITCODE
