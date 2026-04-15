"""Entry point: python -m sentinel"""
import sys
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    stream=sys.stdout,
)

print("[AI Slime] Starting...", flush=True)

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
