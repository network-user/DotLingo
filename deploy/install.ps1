# Установка DotLingo для текущего пользователя: установщик, ярлык на рабочем столе и в меню Пуск.
# Если программа уже собрана PyInstaller, заново её не собирает. Inno Setup скачивается сам.

[CmdletBinding()]
param(
    [string]$AppVersion = "",
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$Host.UI.RawUI.WindowTitle = "DotLingo - установка"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $AppVersion) {
    $project = Get-Content -LiteralPath (Join-Path $repo "pyproject.toml") -Raw -Encoding UTF8
    if ($project -match 'version\s*=\s*"([^"]+)"') { $AppVersion = $Matches[1] } else { $AppVersion = "0.1.0" }
}

function Write-Step([string]$Title) {
    Write-Host ""
    Write-Host ("=== " + $Title) -ForegroundColor Cyan
}

function Find-LatestDist([string]$RepoRoot) {
    $root = Join-Path $RepoRoot "build\windows"
    if (-not (Test-Path -LiteralPath $root)) { return $null }
    $dirs = Get-ChildItem -LiteralPath $root -Directory | Sort-Object Name -Descending
    foreach ($dir in $dirs) {
        $exe = Join-Path $dir.FullName "dist\DotLingo\DotLingo.exe"
        if (Test-Path -LiteralPath $exe) { return (Resolve-Path -LiteralPath (Split-Path -Parent $exe)).Path }
    }
    return $null
}

function Test-WebView2 {
    $paths = @(
        "HKCU:\Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        "HKLM:\Software\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    )
    foreach ($path in $paths) {
        $item = Get-ItemProperty -Path $path -Name pv -ErrorAction SilentlyContinue
        if ($item -and $item.pv -and $item.pv -ne "0.0.0.0") { return $true }
    }
    return $false
}

Write-Host ""
Write-Host "  DotLingo - установка" -ForegroundColor White
Write-Host ""

try {
    Write-Step "Ищем собранную программу"
    $appDist = Find-LatestDist $repo
    if (-not $appDist) {
        Write-Host "  Готовой сборки нет. Собираем программу и установщик. Это долгий шаг." -ForegroundColor Yellow
        & (Join-Path $PSScriptRoot "windows\build.ps1") -AppVersion $AppVersion
        if ($LASTEXITCODE -ne 0) { throw "Сборка не удалась." }
        $appDist = Find-LatestDist $repo
        if (-not $appDist) { throw "После сборки DotLingo.exe не найден." }
    }
    Write-Host "  Программа: $appDist"

    $setupRoot = Join-Path (Split-Path -Parent (Split-Path -Parent $appDist)) "installer"
    $setup = Join-Path $setupRoot "DotLingo-$AppVersion-Setup.exe"
    if (-not (Test-Path -LiteralPath $setup)) {
        Write-Step "Собираем установщик"
        Write-Host "  Если компилятора Inno Setup нет, он скачается сам."
        & (Join-Path $PSScriptRoot "windows\build.ps1") -DistPath $appDist -AppVersion $AppVersion
        if ($LASTEXITCODE -ne 0) { throw "Установщик не собрался." }
    }
    if (-not (Test-Path -LiteralPath $setup)) { throw "Нет файла $setup" }
    Write-Host "  Установщик: $setup"

    Write-Step "Устанавливаем и создаём ярлыки"
    Write-Host "  Программа ставится только для текущего пользователя."
    $log = Join-Path $setupRoot "install.log"
    $arguments = @(
        "/SILENT",
        "/NORESTART",
        "/CURRENTUSER",
        "/MERGETASKS=desktopicon",
        "/LOG=$log"
    )
    $proc = Start-Process -FilePath $setup -ArgumentList $arguments -Wait -PassThru
    if ($proc.ExitCode -ne 0) {
        throw "Установщик завершился с кодом $($proc.ExitCode). Журнал: $log"
    }

    $installed = Join-Path $env:LOCALAPPDATA "Programs\DotLingo\DotLingo.exe"
    $desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "DotLingo.lnk"
    if (-not (Test-Path -LiteralPath $installed)) {
        throw "После установки не найден $installed"
    }
    if (-not (Test-Path -LiteralPath $desktop)) {
        throw "Ярлык на рабочем столе не создан: $desktop"
    }

    Write-Step "Готово"
    Write-Host ""
    Write-Host "  Ярлык на рабочем столе: DotLingo" -ForegroundColor Green
    Write-Host "  Меню Пуск: DotLingo" -ForegroundColor Green
    Write-Host "  Программа: $installed" -ForegroundColor DarkGray
    Write-Host "  Проекты и модели: $env:LOCALAPPDATA\DotLingo" -ForegroundColor DarkGray
    if (-not (Test-WebView2)) {
        Write-Host ""
        Write-Host "  Для окна нужен Microsoft Edge WebView2." -ForegroundColor Yellow
        Write-Host "  https://developer.microsoft.com/microsoft-edge/webview2/" -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host "  Откройте ярлык DotLingo на рабочем столе." -ForegroundColor White
    Write-Host ""
}
catch {
    Write-Host ""
    Write-Host "  Ошибка установки:" -ForegroundColor Red
    Write-Host ("  " + $_.Exception.Message) -ForegroundColor Red
    Write-Host ""
    if (-not $NoPause) { Read-Host "Нажмите Enter, чтобы закрыть" }
    exit 1
}

if (-not $NoPause) { Read-Host "Нажмите Enter, чтобы закрыть" }
exit 0
