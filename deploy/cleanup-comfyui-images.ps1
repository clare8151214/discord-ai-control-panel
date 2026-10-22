param(
    [ValidateRange(1, 365)]
    [int]$RetentionDays = 2
)

$ErrorActionPreference = "Stop"
$Cutoff = (Get-Date).AddDays(-$RetentionDays)
$Targets = @(
    "C:\AI\ComfyUI_temp",
    "C:\AI\ComfyUI_windows_portable\ComfyUI\input\discord_character_refs",
    "C:\AI\ComfyUI_windows_portable\ComfyUI\output"
)
$LogDirectory = "C:\AI\logs"
$LogFile = Join-Path $LogDirectory "image-cleanup.log"
$Deleted = 0
$FreedBytes = [int64]0

New-Item -ItemType Directory -Force -Path $LogDirectory | Out-Null

foreach ($Target in $Targets) {
    if (-not (Test-Path -LiteralPath $Target)) {
        continue
    }
    Get-ChildItem -LiteralPath $Target -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -lt $Cutoff } |
        ForEach-Object {
            try {
                $FreedBytes += $_.Length
                Remove-Item -LiteralPath $_.FullName -Force -ErrorAction Stop
                $Deleted++
            }
            catch {
                Add-Content -LiteralPath $LogFile -Encoding UTF8 -Value (
                    "{0:u} WARN failed={1} error={2}" -f (Get-Date), $_.FullName, $_.Exception.Message
                )
            }
        }
}

Add-Content -LiteralPath $LogFile -Encoding UTF8 -Value (
    "{0:u} retention_days={1} deleted={2} freed_bytes={3}" -f `
        (Get-Date), $RetentionDays, $Deleted, $FreedBytes
)
