$ErrorActionPreference = "Stop"

$action = New-ScheduledTaskAction -Execute "C:\AI\start-comfyui-api.cmd"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 10 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName "ComfyUI API" `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "ComfyUI API for the Discord bot; unlimited runtime with automatic restart." `
    -Force | Out-Null

Start-ScheduledTask -TaskName "ComfyUI API"

Get-ScheduledTask -TaskName "ComfyUI API" |
    Select-Object TaskName, State
Get-ScheduledTask -TaskName "ComfyUI API" |
    Select-Object -ExpandProperty Settings |
    Select-Object ExecutionTimeLimit, RestartCount, RestartInterval, StartWhenAvailable
