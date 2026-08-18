# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller packaging/organizador-pdf.spec
#
# SPECPATH é a pasta deste arquivo (injetada pelo PyInstaller em tempo de
# execução do spec) — usar caminhos relativos a ela em vez de à CWD deixa o
# build reprodutível não importa de onde `pyinstaller` é chamado.
import os

from PyInstaller.utils.hooks import collect_data_files

aqui = SPECPATH
raiz = os.path.dirname(aqui)

# `pymupdf` (motor de layout: modelos ONNX em layout/resources/) e
# `pymupdf4llm` (decisão de quando vale a pena OCR: ocr/ocr_decision_model.onnx)
# carregam arquivo de dados em tempo de execução via caminho no disco — a
# análise estática do PyInstaller não os enxerga sozinha (só código Python é
# rastreado por import), então precisam ser coletados explicitamente. Sem
# isso, o binário cai silenciosamente para extração de texto simples (sem
# layout, sem tabela, sem OCR) em todo PDF, sem erro nenhum.
datas = collect_data_files("pymupdf") + collect_data_files("pymupdf4llm")

a = Analysis(
    [os.path.join(aqui, "entrypoint.py")],
    pathex=[os.path.join(raiz, "src")],
    binaries=[],
    datas=datas,
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
