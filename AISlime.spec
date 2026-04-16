# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AI Slime Agent desktop client.

Build:
    pyinstaller AISlime.spec --noconfirm

Output: dist/AISlime/AISlime.exe (onedir, fast startup, ~zip ~150MB)

We use onedir (not onefile) because:
  - PySide6 onefile extracts ~300MB to %TEMP% on every launch (10-20s wait)
  - onedir launches instantly
  - Ship as ZIP, user extracts once and double-clicks AISlime.exe
"""
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# Sprite images and any data files inside sentinel/
datas = []
datas += collect_data_files('sentinel', includes=['assets/sprites/**/*.png',
                                                  'assets/*.md',
                                                  'skills/*.md'])

# LLM SDKs are imported lazily inside try/except in sentinel/llm.py.
# PyInstaller can't see those, so list them explicitly.
hidden_lazy_llm = [
    'google.genai',
    'google.generativeai',
    'anthropic',
    'openai',
    'groq',
]

# python-telegram-bot pulls in submodules dynamically
hidden_telegram = collect_submodules('telegram')

# pynput uses platform-specific backends loaded by name
hidden_pynput = [
    'pynput.keyboard._win32',
    'pynput.mouse._win32',
]

# watchdog likewise
hidden_watchdog = [
    'watchdog.observers.read_directory_changes',
    'watchdog.observers.winapi',
]

# Make sure the entire sentinel package is collected (some submodules are
# referenced only via importlib at runtime — e.g. self_evolution loading
# auto-generated skills).
hidden_sentinel = collect_submodules('sentinel')

hiddenimports = (
    hidden_lazy_llm
    + hidden_telegram
    + hidden_pynput
    + hidden_watchdog
    + hidden_sentinel
)

a = Analysis(
    ['sentinel\\__main__.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Drop unused PySide6 modules to shrink size.
        'PySide6.Qt3DAnimation',
        'PySide6.Qt3DCore',
        'PySide6.Qt3DExtras',
        'PySide6.Qt3DInput',
        'PySide6.Qt3DLogic',
        'PySide6.Qt3DRender',
        'PySide6.QtCharts',
        'PySide6.QtDataVisualization',
        'PySide6.QtMultimedia',
        'PySide6.QtMultimediaWidgets',
        'PySide6.QtBluetooth',
        'PySide6.QtNfc',
        'PySide6.QtPositioning',
        'PySide6.QtLocation',
        'PySide6.QtSerialPort',
        'PySide6.QtSql',
        'PySide6.QtTest',
        'PySide6.QtWebEngine',
        'PySide6.QtWebEngineCore',
        'PySide6.QtWebEngineWidgets',
        'PySide6.QtWebChannel',
        'PySide6.QtWebSockets',
        'PySide6.QtQuick',
        'PySide6.QtQuick3D',
        'PySide6.QtQuickWidgets',
        'PySide6.QtQml',
        # Test/dev deps
        'tkinter',
        'unittest',
        'pydoc',
        'pytest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AISlime',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,         # UPX often breaks Qt DLLs and triggers AV false positives
    console=False,     # GUI app — no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='slime.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='AISlime',
)
