# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:/andy/codebase/soul_buddy/build/sidecar_entry.py'],
    pathex=['C:/andy/codebase/soul_buddy'],
    binaries=[],
    datas=[],
    hiddenimports=['soul_buddy.providers.deepseek', 'soul_buddy.providers.anthropic', 'soul_buddy.providers.openai_chat', 'soul_buddy.providers.offline', 'soul_buddy.memory.db', 'soul_buddy.mcp.connector', 'soul_buddy.skills.registry'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tiktoken', 'numpy', 'pandas', 'scipy', 'torch', 'transformers', 'matplotlib', 'pytest', 'PyQt5', 'PySide2', 'tkinter', 'unittest'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='soul_sidecar',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
