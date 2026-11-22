"""The arithmetic behind the lab 10 report (no AWS needed)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "monitoring" / "scripts"))

from report import bucketize, summarize  # noqa: E402


def test_bucketize_puts_points_on_a_regular_grid_and_fills_gaps_with_zero() -> None:
    grid = bucketize({100.0: 5.0, 120.0: 7.0}, start=100.0, end=140.0, period=10)
    assert grid == [(100.0, 5.0), (110.0, 0.0), (120.0, 7.0), (130.0, 0.0)]


def test_summarize_computes_rps_error_rate_and_weighted_mean() -> None:
    phase = {"name": "errors", "start": 100.0, "end": 120.0}
    requests = [(100.0, 100.0), (110.0, 100.0), (120.0, 999.0)]  # last is outside
    errors = [(100.0, 30.0), (110.0, 50.0), (120.0, 999.0)]
    dur_sum = [(100.0, 1000.0), (110.0, 3000.0), (120.0, 9.0)]
    dur_count = [(100.0, 100.0), (110.0, 100.0), (120.0, 999.0)]

    result = summarize(phase, requests, errors, dur_sum, dur_count)

    assert result["requests"] == 200
    assert result["rps"] == 10.0
    assert result["errors"] == 80
    assert result["error_rate_pct"] == 40.0
    assert result["avg_ms"] == 20.0  # (1000 + 3000) / 200, not the mean of means


def test_summarize_an_idle_phase_does_not_divide_by_zero() -> None:
    phase = {"name": "idle", "start": 0.0, "end": 10.0}
    result = summarize(phase, [], [], [], [])
    assert result["error_rate_pct"] == 0.0 and result["avg_ms"] == 0.0


def test_bucketize_snaps_points_that_are_not_aligned_to_the_period() -> None:
    # a server aligns buckets to the query's StartTime (here ...06), not to the period
    grid = bucketize({106.0: 5.0, 116.0: 7.0}, start=100.0, end=130.0, period=10)
    assert grid == [(100.0, 5.0), (110.0, 7.0), (120.0, 0.0)]
