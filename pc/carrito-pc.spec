# -*- mode: python ; coding: utf-8 -*-
# Compila la app como carpeta: uv run pyinstaller --noconfirm carrito-pc.spec
# Resultado en dist/carrito-pc/. config.toml va junto a los .exe.
from PyInstaller.utils.hooks import collect_all

datas = [('ui', 'ui')]
binaries = []
hiddenimports = []
tmp_ret = collect_all('webview')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['src/carrito_pc/__main__.py'],
    pathex=['src'],
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


def make_exe(name, console):
    return EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=console,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )


# La interfaz abre sin consola. La versión con consola sirve para --calibrar, --listar y --consola.
app = make_exe('carrito-pc', console=False)
console_app = make_exe('carrito-pc-consola', console=True)

coll = COLLECT(
    app,
    console_app,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='carrito-pc',
)
