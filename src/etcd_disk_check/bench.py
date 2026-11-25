"""Run fio with etcd's recommended workload and read the fdatasync latency."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from .report import Check, Result

# Sequential 2300-byte writes with fdatasync after each one, which is roughly
# what etcd does when it appends to its write-ahead log (WAL).
FIO_ARGS = [
    "--rw=write",
    "--ioengine=sync",
    "--fdatasync=1",
    "--size=22m",
    "--bs=2300",
    "--name=etcd-check",
    "--output-format=json",
]
TARGET_MS = 10.0
WHY = (
    "etcd fsyncs its WAL before acknowledging every write. When the 99th percentile "
    "is above ~10 ms, heartbeats and writes stall, which shows up as leader elections, "
    "'apply request took too long' warnings and API server timeouts."
)


class BenchError(Exception):
    pass


def run_fio(directory: str) -> dict:
    """Run fio in a temporary sub-directory of `directory` and return its JSON output."""
    fio = shutil.which("fio")
    if fio is None:
        raise BenchError(
            "fio not found in PATH; install it first (e.g. 'dnf install fio' or "
            "'apt-get install fio'), or analyse saved output with --fio-json"
        )
    if not os.path.isdir(directory):
        raise BenchError(f"directory does not exist: {directory}")
    # The test file is created on the disk under test and removed afterwards.
    with tempfile.TemporaryDirectory(prefix="etcd-disk-check-", dir=directory) as work:
        proc = subprocess.run(
            [fio, f"--directory={work}", *FIO_ARGS], capture_output=True, text=True
        )
    if proc.returncode != 0:
        raise BenchError(f"fio failed (exit {proc.returncode}): {proc.stderr.strip()}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise BenchError(f"could not parse fio JSON output: {e}") from e


def parse_fio(data: dict) -> tuple[float, float]:
    """Return (p99 fdatasync latency in ms, write IOPS) from fio JSON output."""
    try:
        job = data["jobs"][0]
        percentiles = job["sync"]["lat_ns"]["percentile"]
        iops = float(job["write"]["iops"])
        p99_ns = float(percentiles["99.000000"])
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise BenchError(
            "fio JSON has no fdatasync latency percentiles (jobs[0].sync.lat_ns); "
            "fio 3.5 or newer is required"
        ) from e
    return p99_ns / 1e6, iops


def bench(data: dict, source: str) -> Result:
    p99_ms, iops = parse_fio(data)
    return Result(
        mode="bench",
        source=source,
        checks=[Check("fdatasync p99 latency", p99_ms, TARGET_MS, WHY)],
        info={"write IOPS": f"{iops:.0f}"},
    )
