# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules


project_root = Path(SPECPATH).resolve().parent
hiddenimports = collect_submodules("akshare") + collect_submodules("feedparser")
datas = [
    (str(project_root / "packaging" / "default_config" / "watchlist.yaml"), "config"),
    (str(project_root / ".env.example"), "."),
    (str(project_root / "assets" / "app_icon_1024.png"), "assets"),
]
icon_path = str(project_root / "assets" / "app_icon.icns")

a = Analysis(
    [str(project_root / "gui.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["streamlit"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Stock Watch Assistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
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
    upx=False,
    upx_exclude=[],
    name="Stock Watch Assistant",
)

app = BUNDLE(
    coll,
    name="Stock Watch Assistant.app",
    icon=icon_path,
    bundle_identifier="com.local.stockwatchassistant",
    info_plist={
        "NSHighResolutionCapable": "True",
        "LSApplicationCategoryType": "public.app-category.finance",
    },
)
