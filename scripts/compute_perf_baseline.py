#!/usr/bin/env python3
"""Aggregate WhatsApp harness runs into a persisted performance baseline.

The input is a newline-delimited JSON file where each row represents a harness
batch execution with the following shape:

    {
        "batch": 1,
        "latency_seconds": 0.84,
        "status_code": 200,
        "created": 8,
        "deduped": 2,
        "skipped": 0
    }

Usage:

    python scripts/compute_perf_baseline.py \
        --input tests/fixtures/perf_harness_sample.jsonl \
        --output docs/perf_baseline.json

The script computes latency percentiles, success ratios, and a dedupe rate so
we can detect regressions between releases.
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


def _load_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {number}: {exc}") from exc
    if not rows:
        raise ValueError(f"No rows found in {path}")
    return rows


def _percentile(values: Sequence[float], quantile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    index = max(0, min(len(values) - 1, int(round(quantile * (len(values) - 1)))))
    return values[index]


def _aggregate(rows: Iterable[dict]) -> dict:
    latencies: list[float] = []
    status_histogram: dict[str, int] = {}
    created = 0
    deduped = 0
    skipped = 0
    batches = 0

    for row in rows:
        batches += 1
        latencies.append(float(row.get("latency_seconds") or 0.0))
        status = str(row.get("status_code") or 0)
        status_histogram[status] = status_histogram.get(status, 0) + 1
        created += int(row.get("created") or 0)
        deduped += int(row.get("deduped") or 0)
        skipped += int(row.get("skipped") or 0)

    latencies.sort()
    total_attempted = created + deduped
    total_messages = total_attempted + skipped
    dedupe_ratio = (deduped / total_attempted) if total_attempted else 0.0
    success_ratio = (created / total_attempted) if total_attempted else 0.0

    return {
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "batches": batches,
        "messages_attempted": total_attempted,
        "messages_total": total_messages,
        "latency_seconds": {
            "min": min(latencies) if latencies else 0.0,
            "p50": statistics.median(latencies) if latencies else 0.0,
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies) if latencies else 0.0,
        },
        "status_histogram": status_histogram,
        "created": created,
        "deduped": deduped,
        "skipped": skipped,
        "dedupe_ratio": round(dedupe_ratio, 4),
        "success_ratio": round(success_ratio, 4),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute WhatsApp ingest performance baseline")
    parser.add_argument("--input", type=Path, required=True, help="NDJSON file of harness batches")
    parser.add_argument("--output", type=Path, help="Path to write aggregated baseline JSON")
    args = parser.parse_args(argv)

    rows = _load_rows(args.input)
    baseline = _aggregate(rows)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8") as handle:
            json.dump(baseline, handle, indent=2)
            handle.write("\n")
    else:
        print(json.dumps(baseline, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
