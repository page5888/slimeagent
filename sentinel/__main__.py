"""Entry point: python -m sentinel"""
import os
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)

print("[AI Slime] Starting...", flush=True)


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


_kill_zombie_sentinels()


if "--no-gui" in sys.argv:
    from sentinel.daemon import main
    main()
else:
    print("[AI Slime] Importing GUI...", flush=True)
    try:
        from sentinel.gui import run_gui
        print("[AI Slime] GUI imported, launching...", flush=True)
        run_gui()
    except Exception as e:
        print(f"[AI Slime] FATAL ERROR: {e}", flush=True)
        import traceback
        traceback.print_exc()
        input("Press Enter to exit...")
