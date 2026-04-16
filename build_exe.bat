@echo off
title AI Slime - Build EXE
cd /d %~dp0

echo === AI Slime EXE Builder ===
echo.

if not exist venv\Scripts\python.exe (
    echo [ERROR] venv not found. Run: python -m venv venv
    pause
    exit /b 1
)

echo [1/3] Cleaning old build...
if exist build rmdir /S /Q build
if exist dist rmdir /S /Q dist

echo [2/3] Running PyInstaller (this takes 2-3 minutes)...
venv\Scripts\python.exe -m PyInstaller AISlime.spec --noconfirm --clean
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller failed.
    pause
    exit /b 1
)

echo [3/3] Zipping release archive...
if exist AISlime-Windows.zip del AISlime-Windows.zip
powershell -NoProfile -Command "Compress-Archive -Path dist\AISlime\* -DestinationPath AISlime-Windows.zip -Force"
if errorlevel 1 (
    echo [ERROR] Zip step failed.
    pause
    exit /b 1
)

echo.
echo === Build complete ===
echo   EXE folder: dist\AISlime\
echo   Release:    AISlime-Windows.zip
for %%I in (AISlime-Windows.zip) do echo   Size:       %%~zI bytes
echo.
echo Upload AISlime-Windows.zip to GitHub Releases.
pause
