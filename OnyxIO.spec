# -*- mode: python ; coding: utf-8 -*-
import re
from PyInstaller.utils.hooks import collect_all
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct, StringTable,
                                                 VarFileInfo, VarStruct, VSVersionInfo)

# product name + version shown under Properties -> Details; VERSION in deck.py is the single source
VERSION = re.search(r"^VERSION = '([\d.]+)'", open('deck.py', encoding='utf-8').read(), re.M)[1]
nums = tuple(map(int, (VERSION + '.0.0.0').split('.')[:4]))
version = VSVersionInfo(
    ffi=FixedFileInfo(filevers=nums, prodvers=nums),
    kids=[StringFileInfo([StringTable('040904B0', [StringStruct(k, v) for k, v in {
        'CompanyName': 'OnyxIO contributors', 'FileDescription': 'OnyxIO',
        'ProductName': 'OnyxIO', 'FileVersion': VERSION, 'ProductVersion': VERSION,
        'OriginalFilename': 'OnyxIO.exe', 'InternalName': 'OnyxIO',
        'LegalCopyright': 'MIT License'}.items()])]),
          VarFileInfo([VarStruct('Translation', [0x0409, 1200])])])

datas = [('icon.ico', '.')]
binaries = []
hiddenimports = []
tmp_ret = collect_all('winrt')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['deck.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='OnyxIO',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
    version=version,
)
