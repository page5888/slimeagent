@echo off
title AI Slime Agent
cd /d D:\srbow_bots\ai-slime-agent
call venv\Scripts\activate
set PYTHONIOENCODING=utf-8
echo [AI Slime] Working dir: %CD%
echo [AI Slime] Python:
where python
python -m sentinel
echo.
echo === If you see this, the program exited ===
pause
