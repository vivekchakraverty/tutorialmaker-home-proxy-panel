# PyInstaller spec for the TutorialMaker Home Proxy Panel (PyInstaller 6+).
#
# Build (on the SAME OS you want to target — PyInstaller does NOT cross-compile):
#     pip install pyinstaller proxy.py huggingface_hub requests
#     pyinstaller home_proxy_panel.spec
# Output: dist/HomeProxyPanel(.exe)
#
# proxy.py is bundled and launched by the app re-invoking itself with the --run-proxy
# sentinel (see the __main__ guard in home_proxy_panel.py). `bore` is downloaded at
# runtime; to bundle it, drop a `bore`/`bore.exe` next to this spec and it's picked up.
import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# SPECPATH is injected by PyInstaller = the directory holding this spec.
_here = SPECPATH
_script = os.path.join(_here, "home_proxy_panel.py")
_bore = "bore.exe" if os.name == "nt" else "bore"
_bore_path = os.path.join(_here, _bore)

# NOTE: collect_data_files / collect_submodules return the 2-tuple / name forms that
# Analysis() expects — pass them THROUGH Analysis, never append to a.datas (a 3-tuple TOC).
datas = collect_data_files("proxy")
if os.path.exists(_bore_path):
    datas.append((_bore_path, "."))
hiddenimports = ["proxy"] + collect_submodules("proxy")

a = Analysis(
    [_script],
    pathex=[_here],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="HomeProxyPanel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed GUI app (no console)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
