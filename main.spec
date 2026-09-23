# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

# --- Hidden imports: módulos carregados dinamicamente que o PyInstaller não detecta sozinho ---
hiddenimports = (
    collect_submodules('reportlab')
    + collect_submodules('customtkinter')
    + collect_submodules('mysql.connector')
    + [
        'mysql.connector.locales.eng',
        'mysql.connector.locales.eng.client_error',
    ]
)

# --- Dados extras (arquivos internos das libs, ex: temas/fonts do customtkinter) ---
datas = (
    collect_data_files('customtkinter')
    + [
        ('assets', 'assets'),
        ('secret.key', '.'),
        ('config.ini.enc', '.'),
    ]
)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='SistemaDeGestao',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icon.ico',
    version='file_version_info.txt',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='SistemaDeGestao',
)
