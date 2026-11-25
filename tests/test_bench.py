import json
from pathlib import Path

import pytest

from etcd_disk_check import bench as bench_mod
from etcd_disk_check.bench import BenchError, bench, parse_fio, run_fio
from etcd_disk_check.report import Check

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_parse_fio_fast():
    p99_ms, iops = parse_fio(load("fio_fast.json"))
    assert p99_ms == pytest.approx(1.4336)
    assert iops == pytest.approx(812.375)


def test_bench_slow_fails():
    result = bench(load("fio_slow.json"), "fixture")
    assert result.checks[0].value_ms == pytest.approx(25.296896)
    assert result.status == "FAIL"
    assert result.info["write IOPS"] == "61"


def test_parse_fio_without_sync_block():
    data = load("fio_fast.json")
    del data["jobs"][0]["sync"]
    with pytest.raises(BenchError, match="fio 3.5"):
        parse_fio(data)


def test_run_fio_reports_missing_fio(monkeypatch, tmp_path):
    monkeypatch.setattr(bench_mod.shutil, "which", lambda _: None)
    with pytest.raises(BenchError, match="fio not found"):
        run_fio(str(tmp_path))


@pytest.mark.parametrize(
    ("value", "status"), [(7.99, "PASS"), (8.0, "WARN"), (9.99, "WARN"), (10.0, "FAIL")]
)
def test_grading_boundaries(value, status):
    assert Check("x", value, 10.0, "").status == status
