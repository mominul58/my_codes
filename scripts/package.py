"""Build a portable source ZIP, excluding virtual environments, caches, and Git data."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
import argparse


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    names = ["README.md", "QUICKSTART.txt", "app.py", "pyproject.toml", "requirements.lock", ".gitignore",
             ".streamlit/config.toml", "INSTALL_WINDOWS.bat", "START_WINDOWS.bat", "START_MAC_LINUX.sh",
             "desktop.py", "PaveForge.spec", "BUILD_WINDOWS.bat", "PORTABLE_README.txt", "requirements-dev.lock", "requirements-build.in", "requirements-build.lock"]
    for directory in ("paveforge", "examples", "scripts", "tests"):
        names += [str(p.relative_to(root)) for p in sorted((root / directory).rglob("*"))
                  if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    with ZipFile(args.output, "w", ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(root / name, "PaveForge/" + name)
    with ZipFile(args.output) as archive:
        assert archive.testzip() is None
        print(f"Packaged {len(archive.namelist())} files into {args.output}")


if __name__ == "__main__":
    main()
