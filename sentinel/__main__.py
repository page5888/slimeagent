"""Entry point: python -m sentinel"""
import os
import sys
import logging
import traceback

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)

print("[AI Slime] Starting...", flush=True)


def _print_fatal_and_pause(stage: str, exc: BaseException) -> None:
    """If anything raises during early startup before run_gui's own
    try/except can show a popup, print to stdout and pause so the user
    actually sees what blew up — not just '=== Program exited with
    error ===' followed by a closing console.
    """
    print(f"[AI Slime] FATAL during {stage}: {type(exc).__name__}: {exc}",
          flush=True)
    traceback.print_exc()
    try:
        input("Press Enter to exit...")
    except EOFError:
        pass


def _kill_zombie_sentinels() -> None:
    """Kill any other python processes running sentinel before we boot.

    Why this lives here and not just in start.bat: the in-app
    "Update + Restart" button on older builds doesn't hard-exit the
    parent (QApplication.quit alone leaves the tray + overlay holding
    C++ refs that block Python teardown). The new process spawns via
    `python -m sentinel` directly, bypassing start.bat's existing
    kill-by-cmdline step. So the user ends up with two side-by-side
    windows: the new one + the old hung one.

    Doing the same scan here closes that gap regardless of how the
    new process was launched. psutil is already in requirements
    (Pillow/pytttsx3 transitive — see requirements.txt).

    We match strictly: cmdline must contain "-m sentinel" or end in
    "sentinel/__main__.py" (Windows backslash form too). Loose
    matching on the bare word "sentinel" would risk hitting unrelated
    tools.
    """
    try:
        import psutil
    except Exception as e:
        print(f"[AI Slime] zombie scan: psutil missing ({e})", flush=True)
        return

    my_pid = os.getpid()
    killed = 0
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            if p.info["pid"] == my_pid:
                continue
            name = (p.info["name"] or "").lower()
            if "python" not in name:
                continue
            cmdline_parts = p.info["cmdline"] or []
            cmdline = " ".join(cmdline_parts)
            normalized = cmdline.replace("\\", "/")
            if not (
                "-m sentinel" in normalized
                or "/sentinel/__main__" in normalized
            ):
                continue
            print(
                f"[AI Slime] killing zombie sentinel PID {p.info['pid']}",
                flush=True,
            )
            p.kill()
            killed += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception as e:
            print(f"[AI Slime] zombie kill failed for {p}: {e}", flush=True)

    if killed:
        # Give the OS a moment to release tray icon / overlay window
        # so our new instance doesn't fight for them.
        import time
        time.sleep(0.5)


# Zombie kill is opt-in via env var while we figure out why some
# Windows setups exit silently right after `p.kill()` — possibly a
# job-object association we don't understand yet. Without the env var
# we just skip and let start.bat's powershell kill handle stale
# instances (which has been working fine).
if os.environ.get("SLIME_KILL_ZOMBIES") == "1":
    try:
        _kill_zombie_sentinels()
    except BaseException as e:
        _print_fatal_and_pause("zombie scan", e)
        sys.exit(1)
else:
    print("[AI Slime] zombie scan: skipped (opt-in via SLIME_KILL_ZOMBIES=1)",
          flush=True)


print("[AI Slime] After zombie phase, sys.argv:", sys.argv, flush=True)

if "--no-gui" in sys.argv:
    try:
        from sentinel.daemon import main
        main()
    except BaseException as e:
        _print_fatal_and_pause("daemon main", e)
        sys.exit(1)
else:
    print("[AI Slime] Importing GUI...", flush=True)
    try:
        from sentinel.gui import run_gui
        print("[AI Slime] GUI imported, launching...", flush=True)
        run_gui()
    except BaseException as e:
        _print_fatal_and_pause("gui", e)
        sys.exit(1)
