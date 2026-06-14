# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir build for the dsync CLI (Linux/macOS/Windows).

Produces ``dist/dsync/`` with the ``dsync`` executable and its libraries. The
daemon runs directly from this folder, with no runtime extraction.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

repo_root = os.path.abspath(os.path.join(SPECPATH, os.pardir))
entry = os.path.join(SPECPATH, "dsync_entry.py")

# Backends load dynamically; static analysis misses them.
hiddenimports = collect_submodules("keyring") + collect_submodules("keyrings.alt")

datas = []
binaries = []
zeroconf_datas, zeroconf_binaries, zeroconf_hidden = collect_all("zeroconf")
datas += zeroconf_datas
binaries += zeroconf_binaries
hiddenimports += zeroconf_hidden

if sys.platform == "win32":
    hiddenimports.append("win32timezone")

a = Analysis(
    [entry],
    pathex=[repo_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["ruff", "pre_commit", "mypy", "bandit", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dsync",
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="dsync",
)
