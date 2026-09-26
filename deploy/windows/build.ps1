param(
    [string]$Python = "python",
    [string]$AppVersion = "0.1.0"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
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
    & $buildPython -m pip install --disable-pip-version-check --extra-index-url "https://abetlen.github.io/llama-cpp-python/whl/cpu" ".[inference,pdf,build]"
    if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed. See the llama-cpp-python Windows constraints in docs/BUILD_WINDOWS.md." }

    $appDist = Join-Path $appDistRoot "DotLingo"
    & $buildPython -m PyInstaller --noconfirm --clean --distpath $appDistRoot --workpath $workRoot (Join-Path $repo "deploy\windows\DotLingo.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
    if (-not (Test-Path (Join-Path $appDist "DotLingo.exe"))) { throw "PyInstaller did not create DotLingo.exe." }

    $iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
    if (-not $iscc) {
        $candidate = Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"
        if (Test-Path $candidate) { $iscc = $candidate }
    }
    if (-not $iscc) { throw "Inno Setup 6 ISCC.exe is required to build Setup.exe." }
    & $iscc "/DAppVersion=$AppVersion" "/DAppDist=$appDist" "/DSetupOut=$setupRoot" (Join-Path $repo "deploy\windows\installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }
    Write-Host "Build created under: $buildRoot"
    Write-Host "Installer: $setupRoot\DotLingo-$AppVersion-Setup.exe"
    Write-Host "No model files are included. User data stays under LOCALAPPDATA\DotLingo."
} finally {
    Pop-Location
}
