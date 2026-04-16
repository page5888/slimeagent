@echo off
title AI Slime Relay Server (Dev)
cd /d D:\srbow_bots\ai-slime-agent
call venv\Scripts\activate

echo [Relay] Installing server dependencies...
pip install -q -r server\requirements.txt

echo.
echo [Relay] Starting relay server (DEBUG mode)...
echo [Relay] URL: http://localhost:8000
echo [Relay] Dev login: POST http://localhost:8000/auth/dev-login
echo [Relay] Health: http://localhost:8000/health
echo.

set RELAY_DEBUG=1
set PYTHONIOENCODING=utf-8
python -m uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload

if errorlevel 1 (
    echo.
    echo === Relay server exited with error ===
    pause
)
