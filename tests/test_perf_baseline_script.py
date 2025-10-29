from pathlib import Path

from scripts.compute_perf_baseline import _aggregate, _load_rows


def test_perf_baseline_aggregation_uses_sample_fixture():
    fixture_path = Path("tests/fixtures/perf_harness_sample.jsonl")
    rows = _load_rows(fixture_path)
    baseline = _aggregate(rows)

    assert baseline["batches"] == 20
    assert baseline["messages_attempted"] == 200
    assert baseline["created"] == 170
    assert baseline["deduped"] == 30
    assert baseline["status_histogram"] == {"200": 20}
    assert baseline["latency_seconds"]["p50"] == 0.935
    assert baseline["latency_seconds"]["p95"] == 1.08
    assert baseline["dedupe_ratio"] == 0.15
    assert baseline["success_ratio"] == 0.85
