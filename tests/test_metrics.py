import math
from pathlib import Path

import pytest

from etcd_disk_check.metrics import MetricsError, analyze, histogram_quantile, parse_buckets

FIXTURES = Path(__file__).parent / "fixtures"
INF = math.inf


def test_quantile_interpolates_within_bucket():
    # 100 observations; the 99th falls in (0.004, 0.008] which holds 98 -> 100.
    buckets = {0.002: 50, 0.004: 98, 0.008: 100, INF: 100}
    assert histogram_quantile(0.99, buckets) == pytest.approx(0.004 + 0.004 * (1 / 2))


def test_quantile_first_bucket_starts_at_zero():
    assert histogram_quantile(0.5, {0.01: 10, INF: 10}) == pytest.approx(0.005)


def test_quantile_in_inf_bucket_returns_highest_finite_bound():
    assert histogram_quantile(0.99, {0.1: 50, 0.2: 90, INF: 100}) == 0.2


def test_quantile_without_observations_is_none():
    assert histogram_quantile(0.99, {0.1: 0, INF: 0}) is None


def test_quantile_requires_inf_bucket():
    with pytest.raises(MetricsError):
        histogram_quantile(0.99, {0.1: 1})


def test_parse_sums_series_and_ignores_other_lines():
    text = "\n".join(
        [
            "# TYPE x histogram",
            'x_bucket{instance="a",le="0.5"} 1',
            'x_bucket{instance="b",le="0.5"} 2 1700000000000',
            'x_bucket{le="+Inf",instance="a"} 3',
            'x_bucket{le="+Inf",instance="b"} 4',
            "x_sum 1.5",
            "x_count 7",
            "other_metric 1",
        ]
    )
    assert parse_buckets(text) == {"x": {0.5: 3.0, INF: 7.0}}


def test_analyze_healthy_fixture_passes():
    result = analyze((FIXTURES / "metrics_healthy.prom").read_text(), "fixture")
    wal, backend = result.checks
    assert wal.value_ms == pytest.approx(6.6667, abs=1e-3)
    assert backend.value_ms == pytest.approx(16.0)
    assert result.status == "PASS"


def test_analyze_slow_fixture_fails():
    result = analyze((FIXTURES / "metrics_slow.prom").read_text(), "fixture")
    wal, backend = result.checks
    assert wal.value_ms == pytest.approx(64.0)
    assert backend.value_ms == pytest.approx(118.857, abs=1e-3)
    assert result.status == "FAIL"


def test_analyze_rejects_non_etcd_metrics():
    with pytest.raises(MetricsError, match="not found"):
        analyze("up 1\n", "fixture")
