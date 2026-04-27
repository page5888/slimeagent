@echo off
title AI Slime Agent
cd /d D:\srbow_bots\ai-slime-agent

rem ── Kill any previously-running AI Slime process before launching.
rem
rem Without this, double-clicking the shortcut twice (or running it
rem after "update" that didn't cleanly restart) leaves multiple
rem python.exe instances fighting over the same ~/.hermes/ state,
rem tray icon, and Qt event loop — results look like "my changes
rem don't show up" because an older instance is still rendering the
rem window you see. We filter by command line so we only target the
rem sentinel instance, not every python.exe on the system.
rem
rem PowerShell stderr is swallowed (>nul 2>&1) so a "nothing to kill"
rem case doesn't scare the user with red text.
echo [AI Slime] Checking for existing instances...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name = 'python.exe' OR Name = 'pythonw.exe'\" | Where-Object { $_.CommandLine -like '*sentinel*' } | ForEach-Object { Write-Host ('  killing PID ' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force }" 2>nul

rem ── Pull the latest code so we never run stale ──────────────────
rem
rem Without this step the user's "I clicked the shortcut and it
rem looks the same as yesterday" reports are usually because a PR
rem merged on GitHub but no one ran `git pull` locally. We do a
rem fast-forward-only pull so we never accidentally rewrite a dirty
rem working tree; if local has uncommitted changes, the pull is
rem skipped (printed for visibility but not fatal). Network failures
rem also skip silently — offline launch should still work.
echo [AI Slime] Checking for updates...
git fetch --quiet origin main 2>nul
if errorlevel 1 (
    echo   ^(offline or no network — skipping update^)
) else (
    git merge --ff-only origin/main 2>nul
    if errorlevel 1 (
        echo   ^(local has changes — skip auto-update^)
    ) else (
        echo   up to date.
    )
)

call venv\Scripts\activate
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
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
