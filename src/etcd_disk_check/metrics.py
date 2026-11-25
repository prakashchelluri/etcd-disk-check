"""Compute etcd disk latency percentiles from Prometheus text-format metrics."""

from __future__ import annotations

import math
import re
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

from .report import Check, Result

# metric name -> (label in the report, p99 target in ms, why it matters)
METRICS = {
    "etcd_disk_wal_fsync_duration_seconds": (
        "WAL fsync p99",
        10.0,
        "every etcd write waits for a WAL fsync; slow fsync delays writes and heartbeats "
        "and causes leader elections and API server timeouts.",
    ),
    "etcd_disk_backend_commit_duration_seconds": (
        "backend commit p99",
        25.0,
        "slow commits of the bbolt backend stall applying writes to the database and "
        "slow down compaction and snapshots.",
    ),
}

_BUCKET = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)_bucket\{(?P<labels>[^}]*)\}\s+(?P<value>\S+)"
)
_LE = re.compile(r'(?:^|,)\s*le="([^"]+)"')


class MetricsError(Exception):
    pass


def load(source: str, timeout: float = 10.0) -> str:
    """Read metrics text from a file, an http(s) URL, or '-' for stdin."""
    if source == "-":
        return sys.stdin.read()
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    return Path(source).read_text(encoding="utf-8")


def parse_buckets(text: str) -> dict[str, dict[float, float]]:
    """Return {histogram name: {le: cumulative count}}.

    Series that differ only in labels other than `le` are summed, like
    `sum by (le) (...)` in PromQL.
    """
    out: dict[str, dict[float, float]] = defaultdict(lambda: defaultdict(float))
    for line in text.splitlines():
        m = _BUCKET.match(line)
        if not m:
            continue
        le = _LE.search(m["labels"])
        if le:
            out[m["name"]][float(le.group(1))] += float(m["value"])
    return out


def histogram_quantile(q: float, buckets: dict[float, float]) -> float | None:
    """Same algorithm as PromQL histogram_quantile(): linear interpolation in a bucket.

    Returns None when the histogram has no observations.
    """
    les = sorted(buckets)
    if not les or not math.isinf(les[-1]):
        raise MetricsError("histogram has no +Inf bucket")
    total = buckets[les[-1]]
    if total == 0:
        return None
    rank = q * total
    prev_le, prev_count = 0.0, 0.0
    for le in les:
        count = buckets[le]
        if count >= rank:
            if math.isinf(le):
                # Prometheus returns the highest finite bucket bound in this case.
                return prev_le
            if count == prev_count:
                return le
            return prev_le + (le - prev_le) * (rank - prev_count) / (count - prev_count)
        prev_le, prev_count = le, count
    return prev_le  # unreachable: the +Inf bucket always satisfies count >= rank


def analyze(text: str, source: str) -> Result:
    histograms = parse_buckets(text)
    checks = []
    for name, (label, target_ms, why) in METRICS.items():
        if name not in histograms:
            raise MetricsError(
                f"{name}_bucket not found in {source}; is this an etcd /metrics endpoint?"
            )
        p99 = histogram_quantile(0.99, histograms[name])
        if p99 is None:
            raise MetricsError(f"{name} has no observations yet")
        checks.append(Check(label, p99 * 1000, target_ms, why))
    return Result(
        mode="metrics",
        source=source,
        checks=checks,
        info={"note": "percentiles are over the etcd process lifetime (cumulative counters)"},
    )
