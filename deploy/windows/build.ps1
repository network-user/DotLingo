param(
    [string]$Python = "python",
    [string]$AppVersion = "0.1.0",
    [string]$DistPath = ""
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$InnoVersion = "6.7.3"
$InnoSha256 = "9C73C3BAE7ED48D44112A0F48E66742C00090BDB5BEF71D9D3C056C66E97B732"
$InnoUrl = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-$InnoVersion.exe"

function Find-Iscc([string]$RepoRoot) {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
        (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe"),
        (Join-Path $RepoRoot "build\tools\innosetup\ISCC.exe")
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

function Install-InnoSetup([string]$RepoRoot) {
    $tools = Join-Path $RepoRoot "build\tools"
    $target = Join-Path $tools "innosetup"
    $installer = Join-Path $tools "innosetup-$InnoVersion.exe"
    New-Item -ItemType Directory -Path $tools -Force | Out-Null

    $valid = $false
    if (Test-Path -LiteralPath $installer) {
        $hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
        $valid = ($hash -eq $InnoSha256)
        if (-not $valid) {
            Write-Host "Файл Inno Setup не совпал с контрольной суммой, скачиваем заново."
            Remove-Item -LiteralPath $installer -Force
        }
    }
    if (-not $valid) {
        Write-Host "Скачиваем Inno Setup $InnoVersion..."
        & curl.exe -L --fail --progress-bar -o $installer $InnoUrl
        if ($LASTEXITCODE -ne 0) { throw "Не удалось скачать Inno Setup." }
        $hash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash
        if ($hash -ne $InnoSha256) { throw "Контрольная сумма Inno Setup не совпала." }
    }

    Write-Host "Ставим компилятор Inno Setup в $target"
    $installed = Join-Path $target "ISCC.exe"
    if (Test-Path -LiteralPath $installed) { return $installed }

    $arguments = @(
        "/VERYSILENT",
        "/SUPPRESSMSGBOXES",
        "/NORESTART",
        "/CURRENTUSER",
        "/DIR=$target"
    )
    $proc = Start-Process -FilePath $installer -ArgumentList $arguments -Wait -PassThru
    if (-not (Test-Path -LiteralPath $installed) -and $proc.ExitCode -ne 0) {
        Write-Host "Установка в папку пользователя не вышла (код $($proc.ExitCode)). Повторяем обычную установку."
        $proc = Start-Process -FilePath $installer -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=$target") -Wait -PassThru
    }
    if (-not (Test-Path -LiteralPath $installed)) {
        throw "Inno Setup не поставил ISCC.exe (код $($proc.ExitCode))."
    }
    return $installed
}

function Resolve-Iscc([string]$RepoRoot) {
    $found = Find-Iscc $RepoRoot
    if ($found) { return $found }
    return Install-InnoSetup $RepoRoot
}

function Resolve-AppDist([string]$Path) {
    if (-not $Path) { throw "Путь к собранной программе не указан." }
    if (Test-Path -LiteralPath (Join-Path $Path "DotLingo.exe")) {
        return (Resolve-Path -LiteralPath $Path).Path
    }
    $nested = Join-Path $Path "DotLingo"
    if (Test-Path -LiteralPath (Join-Path $nested "DotLingo.exe")) {
        return (Resolve-Path -LiteralPath $nested).Path
    }
    throw "В $Path нет DotLingo.exe."
}

function Compile-Setup([string]$AppDist, [string]$SetupRoot, [string]$Iscc) {
    New-Item -ItemType Directory -Path $SetupRoot -Force | Out-Null
    Write-Host "Собираем установщик..."
    & $Iscc "/DAppVersion=$AppVersion" "/DAppDist=$AppDist" "/DSetupOut=$SetupRoot" (Join-Path $repo "deploy\windows\installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Сборка установщика не удалась." }
    $setup = Join-Path $SetupRoot "DotLingo-$AppVersion-Setup.exe"
    if (-not (Test-Path -LiteralPath $setup)) { throw "Установщик не появился: $setup" }
    Write-Host "Installer: $setup"
    return $setup
}

$iscc = Resolve-Iscc $repo

if ($DistPath) {
    $appDist = Resolve-AppDist $DistPath
    $setupRoot = Join-Path (Split-Path -Parent (Split-Path -Parent $appDist)) "installer"
    Compile-Setup $appDist $setupRoot $iscc | Out-Null
    Write-Host "No model files are included. User data stays under LOCALAPPDATA\DotLingo."
    return
}

$tag = Get-Date -Format "yyyyMMdd-HHmmss"
$buildRoot = Join-Path $repo (Join-Path "build\windows" $tag)
$appDistRoot = Join-Path $buildRoot "dist"
$workRoot = Join-Path $buildRoot "pyinstaller-work"
$setupRoot = Join-Path $buildRoot "installer"
$venv = Join-Path $buildRoot "build-venv"

if (Test-Path -LiteralPath $buildRoot) {
    throw "Build path already exists; choose a new tag by running the script later. Existing files were not changed."
}
foreach ($directory in @($buildRoot, $appDistRoot, $workRoot, $setupRoot)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}

Push-Location $repo
try {
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create the build virtual environment." }
    $buildPython = Join-Path $venv "Scripts\python.exe"
    & $buildPython -m pip install --disable-pip-version-check ".[pdf,build]"
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. See the llama-cpp-python Windows constraints in docs/BUILD_WINDOWS.md." }
    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = Join-Path $repo "src"
    try {
        & $buildPython -c "from dotlingo.runtime_install import run_install; run_install('cpu')"
        if ($LASTEXITCODE -ne 0) { throw "Hashed CPU wheel install failed. See docs/BUILD_WINDOWS.md." }
    } finally {
        if ($null -eq $previousPythonPath) {
            Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
        } else {
            $env:PYTHONPATH = $previousPythonPath
        }
    }

    $appDist = Join-Path $appDistRoot "DotLingo"
    & $buildPython -m PyInstaller --noconfirm --clean --distpath $appDistRoot --workpath $workRoot (Join-Path $repo "deploy\windows\DotLingo.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
    if (-not (Test-Path (Join-Path $appDist "DotLingo.exe"))) { throw "PyInstaller did not create DotLingo.exe." }

    Compile-Setup $appDist $setupRoot $iscc | Out-Null
    Write-Host "Build created under: $buildRoot"
    Write-Host "No model files are included. User data stays under LOCALAPPDATA\DotLingo."
} finally {
    Pop-Location
}
