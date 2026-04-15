"""Windows auto-start management - add/remove AI Slime Agent from startup."""
import os
import sys
import logging
from pathlib import Path

log = logging.getLogger("sentinel.autostart")

STARTUP_DIR = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
SHORTCUT_NAME = "AISlimeAgent.bat"


def get_startup_script_path() -> Path:
    return STARTUP_DIR / SHORTCUT_NAME


def is_autostart_enabled() -> bool:
    return get_startup_script_path().exists()


def enable_autostart():
    """Add AI Slime Agent to Windows startup."""
    script = get_startup_script_path()
    bat_content = (
        '@echo off\n'
        'cd /d D:\\srbow_bots\\ai-slime-agent\n'
        'call venv\\Scripts\\activate\n'
        'set PYTHONIOENCODING=utf-8\n'
        'start /min python -m sentinel\n'
    )
    try:
        script.write_text(bat_content, encoding='utf-8')
        log.info(f"Auto-start enabled: {script}")
        return True
    except Exception as e:
        log.error(f"Failed to enable auto-start: {e}")
        return False


def disable_autostart():
    """Remove AI Slime Agent from Windows startup."""
    script = get_startup_script_path()
    try:
        if script.exists():
            script.unlink()
        log.info("Auto-start disabled")
        return True
    except Exception as e:
        log.error(f"Failed to disable auto-start: {e}")
        return False
