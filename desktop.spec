# -*- mode: python ; coding: utf-8 -*-

# pandas/numpy/AkShare/TuShare are discovered through normal imports and their
# PyInstaller hooks. Kafka is optional at runtime and imported dynamically.
from PyInstaller.utils.hooks import collect_submodules

datas, binaries = [], []
hiddenimports = ["keyring.backends.Windows"] + collect_submodules("kafka")

a = Analysis(
    ["desktop_launcher.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        "stock_monitor.data_sources.akshare_source",
        "stock_monitor.data_sources.tushare_source",
        "stock_monitor.data_sources.ifind_source",
        "stock_monitor.data_sources.cls_source",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=["matplotlib", "pandas.tests", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="A股量化监控",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
