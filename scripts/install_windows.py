"""Repairable source installation using the interpreter selected by the launcher."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import venv

ROOT = Path(__file__).resolve().parents[1]


def fingerprint():
    return hashlib.sha256((ROOT / "requirements.lock").read_bytes() + (ROOT / "pyproject.toml").read_bytes()).hexdigest()


def environment_python(folder):
    return folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def healthy(python):
    if not python.exists():
        return False
    try:
        result = subprocess.run([str(python), "-c", "import sys; raise SystemExit(sys.version_info[:2] not in ((3,12),(3,13)))"],
                                capture_output=True, timeout=20)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--if-needed", action="store_true")
    args = parser.parse_args()
    if sys.version_info[:2] not in ((3, 12), (3, 13)):
        print("Use standard Python 3.12 or 3.13. Your installed Python 3.13 is supported.")
        return 1
    folder = ROOT / ".venv"
    python = environment_python(folder)
    marker = folder / ".paveforge-installed"
    expected = fingerprint()
    if args.if_needed and healthy(python) and marker.exists() and marker.read_text() == expected:
        return 0
    logs = Path(os.environ.get("LOCALAPPDATA", ROOT / ".cache")) / "PaveForge" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "install.log"
    with log_path.open("a", encoding="utf-8") as log:
        def run(command):
            process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8", "PIP_CACHE_DIR": str(ROOT / ".cache/pip")})
            for line in process.stdout:
                print(line, end="")
                log.write(line)
            if process.wait():
                raise RuntimeError(f"Command failed. Full details: {log_path}")
        try:
            if folder.exists() and not healthy(python):
                backup = ROOT / f".venv-backup-{time.time_ns()}"
                folder.rename(backup)
                print(f"Preserved the incomplete environment as {backup.name}.")
            if not healthy(python):
                print(f"Creating the environment with Python {sys.version.split()[0]}…")
                venv.EnvBuilder(with_pip=True).create(folder)
            run([str(python), "-m", "pip", "install", "--require-hashes", "-r", str(ROOT / "requirements.lock")])
            run([str(python), "-m", "pip", "install", "--no-deps", "-e", str(ROOT)])
            run([str(python), "-m", "pip", "check"])
            run([str(python), str(ROOT / "desktop.py"), "--self-test"])
            marker.write_text(expected)
            print("Installation complete. START_WINDOWS.bat opens the PaveForge desktop window.")
            return 0
        except Exception as exc:
            print(f"Installation failed: {exc}\nLog: {log_path}")
            log.write(f"\n{exc}\n")
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
