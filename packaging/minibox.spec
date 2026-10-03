# PyInstaller 打包配置：pyinstaller packaging/minibox.spec
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs

ROOT = Path(SPECPATH).parent
IS_MAC = sys.platform == "darwin"
icon = str(ROOT / "packaging" / ("icon.png" if IS_MAC else "icon.ico"))

datas = [(str(ROOT / "static"), "static")]
# 有自己编译的精简 ffmpeg（packaging/bin/）就用它，不再带 imageio_ffmpeg 里 80 多 MB 的完整版
OWN_FF = [f for f in (ROOT / "packaging" / "bin").glob("ffmpeg*")] if (ROOT / "packaging" / "bin").is_dir() else []
binaries = [(str(f), "bin") for f in OWN_FF]
if not OWN_FF:
    datas += collect_data_files("imageio_ffmpeg", includes=["binaries/*"])
binaries += collect_dynamic_libs("pillow_heif")

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=["waitress", "media", "engine", "pypdfium2", "pypdfium2_raw", "pillow_heif", "py7zr", "rarfile",
                   "send2trash", "mutagen"] + ([] if OWN_FF else ["imageio_ffmpeg"]),
    excludes=(["imageio_ffmpeg"] if OWN_FF else []) + ["pymupdf", "fitz", "tkinter", "matplotlib", "numpy", "pandas", "scipy", "IPython", "PyQt5", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="MiniBox",
    console=False,
    icon=icon,
    version=str(ROOT / "packaging" / "version.txt") if sys.platform == "win32" else None,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="MiniBox", upx=False)
if IS_MAC:
    app = BUNDLE(coll, name="MiniBox.app", icon=icon, bundle_identifier="app.minibox",
                 info_plist={"CFBundleShortVersionString": "1.7.0", "NSHighResolutionCapable": True})
