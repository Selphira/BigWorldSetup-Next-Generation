# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

import os, sys
from pathlib import Path

# Récupérer le chemin de base
base_path = Path('.').resolve()

# Collecter tous les modules Python du projet
hidden_imports = []
for root, dirs, files in os.walk(base_path):
    # Ignorer les dossiers spéciaux
    if any(x in root for x in ['__pycache__', '.git']):
        continue

    for file in files:
        if file.endswith('.py') and file != 'main.py':
            # Construire le nom du module
            rel_path = os.path.relpath(os.path.join(root, file), base_path)
            module_name = rel_path.replace(os.sep, '.').replace('.py', '')
            hidden_imports.append(module_name)

# Collecter les fichiers de données
datas = []

print(">>>>>DEBUG<<<<<")
print("SYS.PATH:", sys.path)
print("CWD:", os.getcwd())
print("PATHEX:", str(base_path))
print(str(hidden_imports))

a = Analysis(
    ['main.py'],
    pathex=[str(base_path)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='BigWorldSetup',
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
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='BigWorldSetup',
)