import json
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

from etcd_disk_check import bench as bench_mod
from etcd_disk_check.cli import main

FIXTURES = Path(__file__).parent / "fixtures"


def test_metrics_pass(capsys):
    assert main(["metrics", str(FIXTURES / "metrics_healthy.prom")]) == 0
    out = capsys.readouterr().out
    assert "Overall: PASS" in out
    assert "Recommendations" not in out


def test_metrics_fail_explains_and_recommends(capsys):
    assert main(["metrics", str(FIXTURES / "metrics_slow.prom")]) == 2
    out = capsys.readouterr().out
    assert "FAIL  WAL fsync p99" in out
    assert "Why it matters:" in out
    assert "dedicated SSD or NVMe" in out


def test_bench_json_output(capsys):
    assert main(["bench", "--fio-json", str(FIXTURES / "fio_slow.json"), "--json"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "FAIL"
    assert report["checks"][0]["value_ms"] == pytest.approx(25.297)
    assert len(report["recommendations"]) == 4


def test_bench_without_fio_exits_3(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(bench_mod.shutil, "which", lambda _: None)
    assert main(["bench", "--dir", str(tmp_path)]) == 3
    assert "fio not found" in capsys.readouterr().err


def test_missing_file_exits_3(capsys):
    assert main(["metrics", "does-not-exist.prom", "--json"]) == 3
    assert json.loads(capsys.readouterr().out)["status"] == "ERROR"


def test_usage_error_exits_3():
    with pytest.raises(SystemExit) as exc:
        main(["bench"])
    assert exc.value.code == 3


def test_metrics_over_http(capsys):
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = partial(Quiet, directory=str(FIXTURES))
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/metrics_slow.prom"
        assert main(["metrics", url]) == 2
    finally:
        server.shutdown()
    assert "backend commit p99" in capsys.readouterr().out
