[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Executable,
    [string]$ScreenshotPath = ""
)

$ErrorActionPreference = "Stop"
$resolvedExecutable = (Resolve-Path -LiteralPath $Executable).Path
$launched = Start-Process -FilePath $resolvedExecutable -PassThru
$windowProcess = $null
try {
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        $windowProcess = Get-Process -Name DiscordAIControlPanel -ErrorAction SilentlyContinue |
            Where-Object { $_.StartTime -ge $launched.StartTime.AddSeconds(-1) -and $_.MainWindowHandle -ne [IntPtr]::Zero } |
            Select-Object -First 1
        if ($windowProcess) { break }
        Start-Sleep -Milliseconds 250
    }
    if (-not $windowProcess) { throw "The native desktop window did not appear within 60 seconds." }
    if ($windowProcess.MainWindowTitle -ne "Discord AI Control Panel") {
        throw "Unexpected native window: $($windowProcess.MainWindowTitle)"
    }
    # Permit initial navigation and rendering before recording the window.
    Start-Sleep -Seconds 3
    if ($ScreenshotPath) {
        Add-Type -AssemblyName System.Drawing
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class DesktopSmokeWindow {
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
    [DllImport("user32.dll")]
    public static extern bool GetWindowRect(IntPtr handle, out RECT rect);
    [DllImport("user32.dll")]
    public static extern bool PrintWindow(IntPtr handle, IntPtr context, uint flags);
}
"@
        $rect = New-Object DesktopSmokeWindow+RECT
        if (-not [DesktopSmokeWindow]::GetWindowRect($windowProcess.MainWindowHandle, [ref]$rect)) {
            throw "Could not locate the desktop window."
        }
        $bitmap = New-Object System.Drawing.Bitmap ($rect.Right - $rect.Left), ($rect.Bottom - $rect.Top)
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        try {
            # Render the target window directly so an unrelated foreground
            # window or a disconnected desktop cannot obscure the screenshot.
            $context = $graphics.GetHdc()
            try { $rendered = [DesktopSmokeWindow]::PrintWindow($windowProcess.MainWindowHandle, $context, 2) }
            finally { $graphics.ReleaseHdc($context) }
            if (-not $rendered) { $graphics.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bitmap.Size) }
            $bitmap.Save($ScreenshotPath, [System.Drawing.Imaging.ImageFormat]::Png)
        } finally {
            $graphics.Dispose()
            $bitmap.Dispose()
        }
    }
    $windowTitle = $windowProcess.MainWindowTitle
    if (-not $windowProcess.CloseMainWindow()) { throw "The desktop window refused a normal close request." }
    if (-not $launched.WaitForExit(45000)) { throw "Closing the desktop window did not stop its executable." }
    @{ ok = $true; native_window_title = $windowTitle; normal_close = $true; exit_code = $launched.ExitCode } | ConvertTo-Json
} finally {
    if (-not $launched.HasExited) {
        & taskkill.exe /PID $launched.Id /T /F | Out-Null
    }
}
