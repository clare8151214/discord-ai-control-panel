[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$VenvDir = (Join-Path $env:USERPROFILE ".venvs\discord-ai-desktop-build"),
    [string]$BuildRoot = (Join-Path $env:LOCALAPPDATA "DiscordAIControlPanelBuild"),
    [string]$OutputDir = "",
    [string]$FFmpegPath = "",
    [string]$FFmpegLicense = "",
    [switch]$DownloadFFmpeg,
    [switch]$SkipInstall,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$projectRoot = Split-Path $PSScriptRoot -Parent
if (-not $OutputDir) { $OutputDir = Join-Path $projectRoot "dist" }
if ($env:OS -ne "Windows_NT") { throw "Build this Windows executable on Windows with 64-bit Python 3.12." }
New-Item -ItemType Directory -Force -Path $BuildRoot, $OutputDir | Out-Null
$runDirectory = Join-Path $BuildRoot ("run-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $runDirectory | Out-Null
$resources = Join-Path $runDirectory "resources"
$pythonExe = Join-Path $VenvDir "Scripts\python.exe"

function Invoke-Python {
    param([string[]]$Arguments)
    & $pythonExe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python command failed ($LASTEXITCODE): $($Arguments[0])" }
}

function Test-FrozenExecutable {
    param([string]$Executable, [string]$Mode, [string]$ResultFile)
    $testData = Join-Path $runDirectory ("selftest-" + $Mode.TrimStart("-"))
    New-Item -ItemType Directory -Path $testData | Out-Null
    $env:STATE_FILE = Join-Path $testData "state.json"
    $env:DATABASE_FILE = Join-Path $testData "history.sqlite3"
    $env:CHARACTER_ASSET_DIR = Join-Path $testData "characters"
    $env:BOT_LOG_FILE = Join-Path $testData "bot.log"
    $arguments = @($Mode, "--result-file", ('"' + $ResultFile + '"'))
    $process = Start-Process -FilePath $Executable -ArgumentList $arguments -PassThru
    if (-not $process.WaitForExit(120000)) {
        # A one-file EXE has a bootloader parent and a Python worker. Stop the
        # entire test-owned tree so a failed smoke test cannot leave a worker.
        & taskkill.exe /PID $process.Id /T /F | Out-Null
        throw "$Mode timed out after 120 seconds."
    }
    # A windowed executable may fail without useful console output. Never accept
    # its exit code alone; require the explicit result from its tested code path.
    if ($process.ExitCode -ne 0) { throw "$Mode failed with exit code $($process.ExitCode). See $testData" }
    if (-not (Test-Path -LiteralPath $ResultFile)) { throw "$Mode did not produce $ResultFile" }
    $result = Get-Content -LiteralPath $ResultFile -Raw | ConvertFrom-Json
    if ($result.ok -ne $true) { throw "$Mode returned an unsuccessful JSON result." }
    if ($Mode -eq "--bot-self-test") {
        if ($result.module_loaded -ne "bot") { throw "The frozen Bot module was not imported." }
        if (-not $result.config_isolated -or $result.ffmpeg_path -notmatch '[\\/]bin[\\/]ffmpeg\.exe$') {
            throw "The frozen Bot did not verify isolated configuration and bundled FFmpeg."
        }
        if ($result.opus_loaded -ne $true -or -not $result.opus_version -or $result.opus_encoded_bytes -le 0 -or $result.opus_path -notmatch '[\\/]discord[\\/]bin[\\/]libopus-0\.x64\.dll$') {
            throw "The frozen Bot did not load its bundled Opus DLL and encode offline PCM audio."
        }
        foreach ($field in @("state_file", "database_file", "character_asset_dir")) {
            if (-not $result.$field.StartsWith($testData + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Bot self-test wrote a profile path outside its isolated build directory: $field"
            }
        }
    } else {
        foreach ($check in @("loopback_one_time_bootstrap_session", "bundled_web_assets", "supervised_child_dispatch_environment_and_log_reader", "isolated_profile_crud_without_bot_or_provider_calls")) {
            if ($result.checks -notcontains $check) { throw "The frozen desktop did not verify: $check" }
        }
    }
    Write-Host "$Mode passed: $ResultFile"
}

try {
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        & $Python -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) { throw "Could not create isolated build venv: $VenvDir" }
    }
    $env:PYTHONPATH = $projectRoot
    $env:PYTHONUTF8 = "1"
    Invoke-Python -Arguments @("-c", "import struct,sys; assert sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8, 'Use 64-bit Python 3.12 for the Windows x64 release build'")
    if (-not $SkipInstall) {
        Invoke-Python -Arguments @("-m", "pip", "install", "-r", (Join-Path $projectRoot "requirements-desktop.txt"), "-r", (Join-Path $projectRoot "requirements-dev.txt"), "pytest-asyncio")
    }
    if (-not $SkipTests) {
        Invoke-Python -Arguments @("-m", "pytest", "-q", (Join-Path $projectRoot "tests"))
    }

    $provenance = "User-provided or existing local FFmpeg binary"
    if (-not $FFmpegPath -and -not $DownloadFFmpeg) {
        $installed = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
        if ($installed) { $FFmpegPath = $installed.Source }
        if (-not $FFmpegPath) {
            $wingetDirectory = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
            if (Test-Path -LiteralPath $wingetDirectory) {
                $installed = Get-ChildItem -LiteralPath $wingetDirectory -Filter ffmpeg.exe -File -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($installed) { $FFmpegPath = $installed.FullName }
            }
        }
    }
    if (-not $FFmpegPath) {
        # Official FFmpeg download page links to Gyan's Windows builds. Pin both
        # a specific release and the vendor's independently checked SHA-256.
        $archiveUrl = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-essentials_build.zip"
        $expectedHash = "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec"
        $archivePath = Join-Path $BuildRoot "ffmpeg-8.1.2-essentials_build.zip"
        $ffmpegCache = Join-Path $BuildRoot "ffmpeg-8.1.2"
        if (-not (Test-Path -LiteralPath $archivePath)) {
            # curl avoids Windows PowerShell 5's slow progress rendering for
            # large binary downloads and is present on supported Windows.
            & curl.exe --fail --location --silent --show-error --output $archivePath $archiveUrl
            if ($LASTEXITCODE -ne 0) { throw "FFmpeg download failed: $archiveUrl" }
        }
        if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
            throw "FFmpeg download SHA-256 verification failed. Cached archive: $archivePath"
        }
        Expand-Archive -LiteralPath $archivePath -DestinationPath $ffmpegCache -Force
        $FFmpegPath = Join-Path $ffmpegCache "ffmpeg-8.1.2-essentials_build\bin\ffmpeg.exe"
        $provenance = "$archiveUrl (SHA256 $expectedHash)"
    }
    $FFmpegPath = (Resolve-Path -LiteralPath $FFmpegPath).Path
    $ffmpegRoot = Split-Path (Split-Path $FFmpegPath -Parent) -Parent
    if (-not $FFmpegLicense) {
        foreach ($candidate in @((Join-Path $ffmpegRoot "LICENSE"), (Join-Path $ffmpegRoot "LICENSE.txt"))) {
            if (Test-Path -LiteralPath $candidate) { $FFmpegLicense = $candidate; break }
        }
    }
    if (-not $FFmpegLicense -or -not (Test-Path -LiteralPath $FFmpegLicense)) {
        throw "Supply the unmodified FFmpeg license using -FFmpegLicense; a binary without its license is not packaged."
    }
    $noticeArguments = @((Join-Path $projectRoot "packaging\collect_notices.py"), "--output", $resources,
        "--ffmpeg", $FFmpegPath, "--ffmpeg-license", $FFmpegLicense, "--provenance", $provenance)
    $readme = Join-Path $ffmpegRoot "README.txt"
    if (Test-Path -LiteralPath $readme) { $noticeArguments += @("--ffmpeg-readme", $readme) }
    Invoke-Python -Arguments $noticeArguments

    $env:DISCORD_AI_SOURCE_ROOT = $projectRoot
    $env:DISCORD_AI_BUNDLE_RESOURCES = $resources
    # PyInstaller's cache and binary extraction run on NTFS even when the source
    # is in WSL. This avoids UNC-specific build and executable launch failures.
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $BuildRoot "pyinstaller-cache"
    Push-Location $runDirectory
    try {
        Invoke-Python -Arguments @("-m", "PyInstaller", "--noconfirm", "--clean", "--distpath", (Join-Path $runDirectory "dist"), "--workpath", (Join-Path $runDirectory "build"), (Join-Path $projectRoot "packaging\DiscordAIControlPanel.spec"))
    } finally { Pop-Location }

    $executable = Join-Path $runDirectory "dist\DiscordAIControlPanel.exe"
    Invoke-Python -Arguments @((Join-Path $projectRoot "packaging\verify_bundle.py"), "--executable", $executable, "--source-root", $projectRoot, "--result-file", (Join-Path $runDirectory "bundle-verification.json"))
    Test-FrozenExecutable -Executable $executable -Mode "--self-test" -ResultFile (Join-Path $runDirectory "desktop-self-test.json")
    Test-FrozenExecutable -Executable $executable -Mode "--bot-self-test" -ResultFile (Join-Path $runDirectory "bot-self-test.json")

    $releaseDirectory = Join-Path $runDirectory "release"
    New-Item -ItemType Directory -Path $releaseDirectory | Out-Null
    Copy-Item -LiteralPath $executable -Destination $releaseDirectory
    Copy-Item -LiteralPath (Join-Path $resources "licenses") -Destination $releaseDirectory -Recurse
    foreach ($filename in @("ThirdPartyNotices.txt", "build-info.json")) {
        Copy-Item -LiteralPath (Join-Path $resources $filename) -Destination $releaseDirectory
    }
    Copy-Item -LiteralPath (Join-Path $projectRoot "docs\windows-desktop.md") -Destination (Join-Path $releaseDirectory "README.txt")
    foreach ($filename in @("desktop-self-test.json", "bot-self-test.json", "bundle-verification.json")) {
        Copy-Item -LiteralPath (Join-Path $runDirectory $filename) -Destination $releaseDirectory
    }
    $zipPath = Join-Path $runDirectory "DiscordAIControlPanel-Windows-x64.zip"
    Compress-Archive -Path (Join-Path $releaseDirectory "*") -DestinationPath $zipPath
    Copy-Item -LiteralPath $executable, $zipPath -Destination $OutputDir -Force
    $hashLines = foreach ($file in @($executable, $zipPath)) {
        (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant() + "  " + (Split-Path $file -Leaf)
    }
    # ASCII is intentionally used: sha256sum accepts this file on every platform.
    $hashLines | Set-Content -LiteralPath (Join-Path $OutputDir "SHA256SUMS.txt") -Encoding ascii
    foreach ($filename in @("desktop-self-test.json", "bot-self-test.json", "bundle-verification.json")) {
        Copy-Item -LiteralPath (Join-Path $runDirectory $filename) -Destination $OutputDir -Force
    }
    Write-Host "Built and verified: $(Join-Path $OutputDir 'DiscordAIControlPanel.exe')"
    Write-Host "Release ZIP with dependency licenses: $(Join-Path $OutputDir 'DiscordAIControlPanel-Windows-x64.zip')"
    Write-Host "Build diagnostics: $runDirectory"
} finally {
    foreach ($name in @("STATE_FILE", "DATABASE_FILE", "CHARACTER_ASSET_DIR", "BOT_LOG_FILE", "DISCORD_AI_SOURCE_ROOT", "DISCORD_AI_BUNDLE_RESOURCES")) {
        Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
    }
}
