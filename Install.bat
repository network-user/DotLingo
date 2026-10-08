@echo off
chcp 65001 >nul
title DotLingo - install
cd /d "%~dp0"

echo.
echo   DotLingo - install
echo   Keep this window open to see progress.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\install.ps1" -NoPause
set ERR=%ERRORLEVEL%

if not "%ERR%"=="0" (
  echo.
  echo   Install failed. Exit code: %ERR%
  echo.
  pause
  exit /b %ERR%
)

echo.
echo   Shortcut: DotLingo on the desktop.
echo.
pause
exit /b 0
