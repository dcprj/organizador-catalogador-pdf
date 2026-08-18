# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller packaging/organizador-pdf.spec
#
# SPECPATH é a pasta deste arquivo (injetada pelo PyInstaller em tempo de
# execução do spec) — usar caminhos relativos a ela em vez de à CWD deixa o
# build reprodutível não importa de onde `pyinstaller` é chamado.
import os

aqui = SPECPATH
raiz = os.path.dirname(aqui)

a = Analysis(
    [os.path.join(aqui, "entrypoint.py")],
    pathex=[os.path.join(raiz, "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name="organizador-pdf",
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
