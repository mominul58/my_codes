# Build on Windows with Python 3.13: python -m PyInstaller PaveForge.spec
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

root = Path(SPECPATH)
datas = [
    (str(root / "app.py"), "."),
    (str(root / "examples"), "examples"),
    (str(root / ".streamlit"), ".streamlit"),
]
binaries = []
hiddenimports = collect_submodules("paveforge")
for package in ("streamlit", "plotly", "triangle", "shapely", "pyproj"):
    package_datas, package_binaries, package_hidden = collect_all(
        package, filter_submodules=lambda name: ".tests" not in name and ".testing" not in name
    )
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden
hiddenimports += ["numpy", "shapefile", "pyarrow", "streamlit.web.bootstrap", "tkinter"]

analysis = Analysis(
    [str(root / "desktop.py")], pathex=[str(root)], binaries=binaries,
    datas=datas, hiddenimports=hiddenimports,
    excludes=["pytest", "meshio", "py2dm", "IPython", "notebook"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive, analysis.scripts, [], exclude_binaries=True, name="PaveForge",
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False,
)
bundle = COLLECT(executable, analysis.binaries, analysis.datas, strip=False, upx=False, name="PaveForge")
