# Build on macOS with: pyinstaller BobArchive.spec
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
from app import __version__

hiddenimports = collect_submodules("cv2") + collect_submodules("pymupdf")
datas = collect_data_files("pymupdf")

analysis = Analysis(
    ["app/main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(analysis.pure)
executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Bob Archive",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Bob Archive",
)
app = BUNDLE(
    collection,
    name="Bob Archive.app",
    icon=None,
    bundle_identifier="org.bobarchive.desktop",
    version=__version__,
    info_plist={
        "CFBundleDisplayName": "Bob Archive",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.lifestyle",
    },
)
