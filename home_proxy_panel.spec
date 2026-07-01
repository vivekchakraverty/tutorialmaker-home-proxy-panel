# PyInstaller spec for the TutorialMaker Home Proxy Panel.
#
# Build (on the SAME OS you want to target — PyInstaller does NOT cross-compile):
#     pip install pyinstaller proxy.py huggingface_hub requests
#     pyinstaller tools/home_proxy_panel.spec
# Output: dist/HomeProxyPanel(.exe)
#
# The forward proxy (proxy.py) is bundled and launched by the app re-invoking itself with
# the --run-proxy sentinel (see the __main__ guard in home_proxy_panel.py). `bore` is NOT
# bundled here — the app downloads the correct release binary for the host on first use;
# to ship it inside the exe instead, drop a `bore`/`bore.exe` next to this spec and add it
# to `datas` below.
import os

block_cipher = None

# SPECPATH is injected by PyInstaller = the directory holding this spec (…/tools).
_here = SPECPATH
_script = os.path.join(_here, "home_proxy_panel.py")
_bore = "bore.exe" if os.name == "nt" else "bore"
_bore_path = os.path.join(_here, _bore)
datas = [(_bore_path, ".")] if os.path.exists(_bore_path) else []

a = Analysis(
    [_script],
    pathex=[_here],
    binaries=[],
    datas=datas,
    # proxy.py loads its plugins by dotted path at runtime, so PyInstaller's static
    # analysis misses them — pull the whole package in.
    hiddenimports=["proxy"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
# Ensure proxy.py's data/plugin modules are collected.
try:
    from PyInstaller.utils.hooks import collect_submodules, collect_data_files
    a.hiddenimports += collect_submodules("proxy")
    a.datas += collect_data_files("proxy")
except Exception:
    pass

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="HomeProxyPanel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,  # windowed GUI app (no console)
)
