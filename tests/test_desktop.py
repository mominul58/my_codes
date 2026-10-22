import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import ProxyHandler, build_opener

import desktop


def test_desktop_self_test_exercises_packaged_resources(tmp_path):
    report = tmp_path / "self-test.json"
    run = subprocess.run([sys.executable, str(Path(desktop.__file__)), "--self-test", "--self-test-report", str(report)],
                         capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    result = json.loads(report.read_text())
    assert result["status"] == "ok"
    assert result["nodes"] > 0 and result["elements"] > 0
    assert result["gap_area"] == 0 and result["duplicate_nodes"] == 0


def test_desktop_starts_working_dashboard_and_stops_owned_process(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "log_directory", lambda: tmp_path)
    server = desktop.DashboardServer()
    process = None
    try:
        server.start()
        process = server.process
        server.start()
        assert server.process is process
        deadline = time.monotonic() + 30
        while not server.ready() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.1)
        assert server.ready(), server.log_path.read_text()
        opener = build_opener(ProxyHandler({}))
        with opener.open(server.url, timeout=5) as reply:
            assert reply.status == 200 and b"Streamlit" in reply.read()
    finally:
        server.stop()
    assert process is not None and process.poll() is not None
    assert not server.ready()
