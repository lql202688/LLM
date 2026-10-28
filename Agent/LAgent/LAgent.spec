# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=['core', 'core.agent', 'core.config', 'core.events', 'core.llm_client', 'core.tools', 'core.tools.registry', 'core.tools.file_ops', 'core.tools.datetime_tools', 'core.tools.search_tools', 'core.tools.web_tools', 'core.tools.cli_tools', 'ui', 'ui.base', 'ui.cli', 'ui.tui', 'ui.tui.app', 'ui.tui.widgets', 'ui.tui.help_screen', 'trafilatura', 'bs4', 'textual', 'textual.widgets._text_area'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'tkinter', 'matplotlib', 'scipy', 'pandas', 'numpy', 'PIL', 'IPython', 'jupyter', 'notebook'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LAgent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='LAgent',
)
