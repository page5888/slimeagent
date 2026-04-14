"""Entry point: python -m sentinel"""
import sys

if "--no-gui" in sys.argv:
    from sentinel.daemon import main
    main()
else:
    from sentinel.gui import run_gui
    run_gui()
