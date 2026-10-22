"""Desktop launcher and frozen executable entry point. Standard library at startup."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser


def resource_root() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def log_directory() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "state"))
    folder = base / "PaveForge" / "logs"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        folder = Path(tempfile.gettempdir()) / "PaveForge" / "logs"
        folder.mkdir(parents=True, exist_ok=True)
    return folder


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def worker_command(port: int) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--serve", "--port", str(port)]
    return [sys.executable, str(Path(__file__).resolve()), "--serve", "--port", str(port)]


class DashboardServer:
    """Own exactly one server process; never stop another user's listener."""

    def __init__(self):
        self.process = None
        self.port = None
        self.log_path = log_directory() / "dashboard.log"
        self._log = None

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        self.port = available_port()
        self._log = self.log_path.open("a", encoding="utf-8", buffering=1)
        self._log.write(f"\n--- Starting dashboard {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            self.process = subprocess.Popen(worker_command(self.port), cwd=resource_root(),
                                            stdout=self._log, stderr=subprocess.STDOUT, **options)
        except Exception:
            self._log.close()
            self._log = None
            raise

    def ready(self):
        if self.process is None or self.process.poll() is not None:
            return False
        try:
            # Loopback must stay local even if the machine uses an HTTP proxy.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(self.url + "/_stcore/health", timeout=.35) as reply:
                return reply.status == 200 and reply.read().strip() == b"ok"
        except (OSError, urllib.error.URLError):
            return False

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self._log is not None:
            self._log.close()
            self._log = None
        self.process = None


def serve(port: int):
    # PyInstaller --windowed processes have no console streams. Give logging and
    # Streamlit valid writable streams even when Windows supplies None.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    os.chdir(resource_root())
    from streamlit.web import bootstrap
    options = {
        "global.developmentMode": False,
        "server.address": "127.0.0.1", "server.port": port,
        "server.headless": True, "server.fileWatcherType": "none",
        "server.maxUploadSize": 250, "browser.gatherUsageStats": False,
    }
    bootstrap.load_config_options(options)
    bootstrap.run(str(resource_root() / "app.py"), False, [], options)


def write_report(path, report):
    if path:
        Path(path).write_text(json.dumps(report, indent=2), encoding="utf-8")


def self_test(report_path=None):
    """Exercise bundled native libraries, CRS data, examples, meshing and exports."""
    from paveforge import read_basepoly, read_2dm, generate_basepoly, write_2dm, write_medit
    root = resource_root()
    base = read_basepoly((root / "examples/basepoly-shapefile.zip").read_bytes(), "basepoly.zip")
    structure = read_2dm((root / "examples/structure.2dm").read_text())
    mesh = generate_basepoly(base, structure=structure, minimum_angle=0)
    sms = read_2dm(write_2dm(mesh))
    if len(sms.nodes) != len(mesh.nodes) or len(sms.cells) != len(mesh.cells):
        raise RuntimeError("Bundled SMS export did not round-trip.")
    if "MeshVersionFormatted" not in write_medit(mesh):
        raise RuntimeError("Bundled Medit export failed.")
    report = {"status": "ok", "nodes": len(mesh.nodes), "elements": len(mesh.cells),
              "python": sys.version.split()[0], "frozen": bool(getattr(sys, "frozen", False)),
              "gap_area": mesh.report["gap_area"], "duplicate_nodes": mesh.report["duplicate_nodes"]}
    write_report(report_path, report)
    if sys.stdout is not None:
        print(json.dumps(report))
    return 0


def desktop(smoke_report=None):
    import tkinter as tk
    from tkinter import messagebox

    window = tk.Tk()
    window.title("PaveForge — Mesh Studio")
    window.geometry("640x430")
    window.minsize(600, 410)
    window.configure(bg="#0b1020")
    server = DashboardServer()
    status = tk.StringVar(value="Ready to start your mesh dashboard.")
    detail = tk.StringVar(value="The dashboard opens in your browser. Keep this window open.")
    running = False
    closed = False
    started_at = 0.
    smoke_status = 1

    tk.Label(window, text="PAVEFORGE / MESH STUDIO", fg="#5eead4", bg="#0b1020",
             font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=32, pady=(30, 10))
    tk.Label(window, text="Build a connected mesh.", fg="#eef4ff", bg="#0b1020",
             font=("Segoe UI", 25, "bold")).pack(anchor="w", padx=32)
    tk.Label(window, text="Basepoly → triangular paving → structure merge", fg="#a9b8d1", bg="#0b1020",
             font=("Segoe UI", 12)).pack(anchor="w", padx=32, pady=(8, 24))
    tk.Label(window, textvariable=status, fg="#5eead4", bg="#0b1020",
             font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=32)
    tk.Label(window, textvariable=detail, fg="#a9b8d1", bg="#0b1020", wraplength=565,
             justify="left", font=("Segoe UI", 10)).pack(anchor="w", padx=32, pady=(8, 20))
    buttons = tk.Frame(window, bg="#0b1020")
    buttons.pack(anchor="w", padx=32)

    def close():
        nonlocal closed
        if closed:
            return
        closed = True
        server.stop()
        window.destroy()

    def fail(message):
        nonlocal running
        running = False
        server.stop()
        start_button.configure(state="normal")
        open_button.configure(state="disabled")
        status.set("Dashboard could not start.")
        detail.set(f"{message} Log: {server.log_path}")
        if smoke_report:
            log_tail = server.log_path.read_text(encoding="utf-8", errors="replace")[-8000:] if server.log_path.exists() else ""
            worker_error_path = log_directory() / "launcher-error.log"
            worker_error = worker_error_path.read_text(encoding="utf-8", errors="replace")[-8000:] if worker_error_path.exists() else ""
            write_report(smoke_report, {"status": "failed", "error": message, "server_log": log_tail, "worker_error": worker_error})
            close()
        else:
            messagebox.showerror("PaveForge startup", f"{message}\n\nOpen logs for details.")

    def poll():
        nonlocal smoke_status
        if closed or not running:
            return
        if server.process is None or server.process.poll() is not None:
            fail("The dashboard process exited. Run INSTALL_WINDOWS.bat to repair a source install.")
            return
        if server.ready():
            status.set("Your dashboard is ready.")
            detail.set("Create an unstructured mesh or merge your 2DM files. Downloads are saved by your browser.")
            open_button.configure(state="normal")
            if smoke_report:
                write_report(smoke_report, {"status": "ok", "desktop": "tkinter", "http_health": "ok"})
                smoke_status = 0
                window.after(300, close)
            else:
                webbrowser.open(server.url)
            return
        if time.monotonic() - started_at > 90:
            fail("Startup timed out after 90 seconds.")
            return
        window.after(300, poll)

    def start():
        nonlocal running, started_at
        if running:
            return
        try:
            server.start()
            running = True
            started_at = time.monotonic()
            start_button.configure(state="disabled")
            status.set("Starting your dashboard…")
            detail.set("Loading the mesh engine and browser interface. This may take a moment.")
            window.after(200, poll)
        except Exception as exc:
            fail(str(exc))

    def stop():
        nonlocal running
        running = False
        server.stop()
        status.set("Dashboard stopped.")
        detail.set("Click Start dashboard to open it again.")
        start_button.configure(state="normal")
        open_button.configure(state="disabled")

    def open_logs():
        if os.name == "nt":
            os.startfile(str(server.log_path.parent))
        else:
            webbrowser.open(server.log_path.parent.as_uri())

    style = {"font": ("Segoe UI", 10), "relief": "flat", "padx": 14, "pady": 9}
    start_button = tk.Button(buttons, text="Start dashboard", command=start, bg="#2dd4bf", fg="#09201e", **style)
    start_button.pack(side="left", padx=(0, 8))
    open_button = tk.Button(buttons, text="Open dashboard", command=lambda: webbrowser.open(server.url),
                            state="disabled", bg="#26354e", fg="white", **style)
    open_button.pack(side="left", padx=(0, 8))
    tk.Button(buttons, text="Stop", command=stop, bg="#26354e", fg="white", **style).pack(side="left")
    tk.Button(window, text="Open logs", command=open_logs, bg="#0b1020", fg="#a9b8d1",
              relief="flat", font=("Segoe UI", 10)).pack(anchor="w", padx=25, pady=(18, 0))
    window.protocol("WM_DELETE_WINDOW", close)
    window.after(100, start)
    try:
        window.mainloop()
    finally:
        server.stop()
    return smoke_status if smoke_report else 0


def main():
    parser = argparse.ArgumentParser(description="PaveForge desktop dashboard")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--self-test-report")
    parser.add_argument("--desktop-smoke-report")
    args = parser.parse_args()
    try:
        if args.serve:
            serve(args.port)
            return 0
        if args.self_test:
            return self_test(args.self_test_report)
        return desktop(args.desktop_smoke_report)
    except Exception as exc:
        import traceback
        report = args.self_test_report or args.desktop_smoke_report
        write_report(report, {"status": "failed", "error": str(exc), "traceback": traceback.format_exc()})
        if report:
            return 1
        folder = log_directory()
        (folder / "launcher-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        if os.name == "nt" and not args.serve:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, f"{exc}\n\nDetails: {folder / 'launcher-error.log'}", "PaveForge", 0x10)
        elif sys.stderr is not None:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())
