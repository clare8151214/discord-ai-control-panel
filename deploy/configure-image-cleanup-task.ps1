$ErrorActionPreference = "Stop"

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File C:\AI\cleanup-comfyui-images.ps1 -RetentionDays 2"
$Trigger = New-ScheduledTaskTrigger -Daily -At "03:15"
$Principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest
$Settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName "ComfyUI Image Cleanup" `
    -Action $Action `
    -Trigger $Trigger `
    -Principal $Principal `
    -Settings $Settings `
    -Description "Delete ComfyUI temporary and Discord bot upload copies older than 2 days." `
    -Force | Out-Null

Get-ScheduledTask -TaskName "ComfyUI Image Cleanup" |
    Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName "ComfyUI Image Cleanup" |
    Select-Object LastRunTime, LastTaskResult, NextRunTime
