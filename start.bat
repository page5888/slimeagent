@echo off
title AI Slime Agent
cd /d D:\srbow_bots\ai-slime-agent
call venv\Scripts\activate
set PYTHONIOENCODING=utf-8
echo [AI Slime] Working dir: %CD%
echo [AI Slime] Python:
where python
python -m sentinel
rem Only pause if the program crashed (non-zero exit), so normal quit closes CMD
if errorlevel 1 (
    echo.
    echo === Program exited with error ===
    pause
)
