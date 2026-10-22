$ErrorActionPreference = "Stop"

$Processes = @(
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq "python.exe" -and
            $_.CommandLine -like "*ollama-discord-bot*bot.py*"
        }
)

if ($Processes.Count -eq 0) {
    Write-Host "Bot is not running."
    exit 0
}

$Ids = @($Processes.ProcessId)
Stop-Process -Id $Ids -Force
Write-Host ("Stopped Bot process(es): " + ($Ids -join ", "))
