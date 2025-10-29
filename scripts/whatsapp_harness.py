#!/usr/bin/env python3
"""Utility to exercise the WhatsApp ingest endpoint for smoke and load tests.

Examples:
    python scripts/whatsapp_harness.py smoke \
        --url http://localhost:8000/integrations/whatsapp/ingest \
        --token test-token

    python scripts/whatsapp_harness.py load \
        --url https://api.pricebot.test/integrations/whatsapp/ingest \
        --token $WHATSAPP_INGEST_TOKEN \
        --hmac-secret $WHATSAPP_INGEST_HMAC_SECRET \
        --count 200 --batch-size 25
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import random
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional

import requests

DEFAULT_SAMPLE_PATH = Path("docs/whatsapp_ingest_contract_sample.json")
METRIC_FIELDS = ("accepted", "created", "deduped", "extracted", "errors")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_messages(path: Path) -> List[dict]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    messages = payload.get("messages")
    if not messages:
        raise ValueError(f"Expected sample payload with 'messages', got keys: {list(payload.keys())}")
    return messages


def sign_body(secret: Optional[str], timestamp: str, body: bytes) -> Optional[str]:
    if not secret:
        return None
    digest = hmac.new(secret.encode("utf-8"), timestamp.encode("utf-8") + b"." + body, hashlib.sha256)
    return digest.hexdigest()


def prepare_batch(messages: Iterable[dict], *, client_id: str, jitter: bool = False) -> dict:
    batch: List[dict] = []
    for index, message in enumerate(messages, start=1):
        cloned = dict(message)
        if jitter:
            cloned = dict(cloned)
            cloned["message_id"] = f"seed-{index}-{random.randrange(1_000_000)}"
        batch.append(cloned)
    return {"client_id": client_id, "messages": batch}


def send_batch(
    session: requests.Session,
    *,
    url: str,
    token: str,
    hmac_secret: Optional[str],
    payload: dict,
    timeout: float = 10.0,
) -> tuple[float, requests.Response]:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    timestamp = _utcnow_iso()
    headers = {
        "Content-Type": "application/json",
        "X-Ingest-Token": token,
    }
    signature = sign_body(hmac_secret, timestamp, body)
    if signature:
        headers["X-Signature"] = signature
        headers["X-Signature-Timestamp"] = timestamp
    started = time.perf_counter()
    response = session.post(url, data=body, headers=headers, timeout=timeout)
    elapsed = time.perf_counter() - started
    return elapsed, response


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _aggregate_metrics(entries: list[dict]) -> dict:
    totals = {field: 0 for field in METRIC_FIELDS}
    last_event: Optional[datetime] = None
    for entry in entries:
        for field in METRIC_FIELDS:
            totals[field] += int(entry.get(field, 0) or 0)
        event_at = _parse_iso_datetime(entry.get("last_event_at"))
        if event_at and (last_event is None or event_at > last_event):
            last_event = event_at
    totals["last_event_at"] = last_event.isoformat() if last_event else None
    totals["entries"] = entries
    return totals


@dataclass
class DiagnosticsData:
    totals: dict
    fetched_at: float


class DiagnosticsError(RuntimeError):
    """Raised when diagnostics verification fails."""


class DiagnosticsTimeoutError(DiagnosticsError):
    def __init__(self, message: str, diff: dict, snapshot: DiagnosticsData) -> None:
        super().__init__(message)
        self.diff = diff
        self.snapshot = snapshot


class DiagnosticsTracker:
    def __init__(
        self,
        session: requests.Session,
        *,
        url: str,
        client_id: str,
        chat_filters: Optional[list[str]] = None,
        timeout: float = 10.0,
    ) -> None:
        self.session = session
        self.url = url
        self.client_id = (client_id or "").lower()
        self.chat_filters = [value.lower() for value in (chat_filters or [])]
        self.timeout = timeout

    def snapshot(self) -> DiagnosticsData:
        response = self.session.get(self.url, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        metrics = payload.get("whatsapp_metrics") or []
        relevant: list[dict] = []
        for entry in metrics:
            client = (entry.get("client_id") or "").lower()
            if client != self.client_id:
                continue
            if self.chat_filters and not self._matches_chat(entry):
                continue
            relevant.append(entry)
        totals = _aggregate_metrics(relevant)
        return DiagnosticsData(totals=totals, fetched_at=time.perf_counter())

    def diff(self, baseline: DiagnosticsData | None, current: DiagnosticsData) -> dict:
        diff: dict[str, int | str | list] = {}
        base_totals = baseline.totals if baseline else {field: 0 for field in METRIC_FIELDS}
        for field in METRIC_FIELDS:
            start = int(base_totals.get(field, 0) or 0)
            end = int(current.totals.get(field, 0) or 0)
            diff[field] = end - start
        diff["last_event_at"] = current.totals.get("last_event_at")
        diff["entries"] = current.totals.get("entries", [])
        return diff

    def wait_for(
        self,
        baseline: DiagnosticsData | None,
        *,
        expect_created: int = 0,
        expect_extracted: int = 0,
        timeout: float = 30.0,
        interval: float = 2.0,
    ) -> dict:
        deadline = time.perf_counter() + max(timeout, 0.0)
        created_mark: Optional[float] = None
        extracted_mark: Optional[float] = None

        snapshot = self.snapshot()
        diff = self.diff(baseline, snapshot)
        created_mark, extracted_mark = self._update_markers(
            diff,
            snapshot.fetched_at,
            created_mark,
            extracted_mark,
            expect_created,
            expect_extracted,
        )
        if self._targets_met(diff, expect_created, expect_extracted):
            return {
                "diff": diff,
                "final": snapshot.totals,
                "created_reached_at": created_mark,
                "extracted_reached_at": extracted_mark,
            }

        while time.perf_counter() < deadline:
            time.sleep(max(interval, 0.1))
            snapshot = self.snapshot()
            diff = self.diff(baseline, snapshot)
            created_mark, extracted_mark = self._update_markers(
                diff,
                snapshot.fetched_at,
                created_mark,
                extracted_mark,
                expect_created,
                expect_extracted,
            )
            if self._targets_met(diff, expect_created, expect_extracted):
                return {
                    "diff": diff,
                    "final": snapshot.totals,
                    "created_reached_at": created_mark,
                    "extracted_reached_at": extracted_mark,
                }

        raise DiagnosticsTimeoutError(
            "timed out waiting for diagnostics counters",
            diff,
            snapshot,
        )

    def _matches_chat(self, entry: dict) -> bool:
        title = (entry.get("chat_title") or "").lower()
        chat_id = (entry.get("chat_id") or "").lower()
        for filter_value in self.chat_filters:
            if filter_value in title or filter_value == chat_id:
                return True
        return False

    @staticmethod
    def _targets_met(diff: dict, expect_created: int, expect_extracted: int) -> bool:
        return diff.get("created", 0) >= expect_created and diff.get("extracted", 0) >= expect_extracted

    @staticmethod
    def _update_markers(
        diff: dict,
        timestamp: float,
        created_mark: Optional[float],
        extracted_mark: Optional[float],
        expect_created: int,
        expect_extracted: int,
    ) -> tuple[Optional[float], Optional[float]]:
        if expect_created and diff.get("created", 0) >= expect_created and created_mark is None:
            created_mark = timestamp
        if expect_extracted and diff.get("extracted", 0) >= expect_extracted and extracted_mark is None:
            extracted_mark = timestamp
        return created_mark, extracted_mark


def command_smoke(args: argparse.Namespace) -> int:
    sample_path = Path(args.sample) if args.sample else DEFAULT_SAMPLE_PATH
    messages = load_messages(sample_path)[: args.batch_size]
    payload = prepare_batch(messages, client_id=args.client_id, jitter=args.jitter)

    diagnostics_tracker: DiagnosticsTracker | None = None
    diagnostics_baseline: DiagnosticsData | None = None
    diagnostics_result: dict | None = None
    ingest_delay: float | None = None
    extract_delay: float | None = None
    diagnostics_failed = False

    with requests.Session() as session:
        if args.diagnostics_url:
            diagnostics_tracker = DiagnosticsTracker(
                session,
                url=args.diagnostics_url,
                client_id=args.client_id,
                chat_filters=args.diagnostics_chat,
                timeout=args.diagnostics_http_timeout,
            )
            try:
                diagnostics_baseline = diagnostics_tracker.snapshot()
            except requests.RequestException as exc:
                print(f"Failed to fetch diagnostics baseline: {exc}", file=sys.stderr)
                return 2

        timeout = getattr(args, "timeout", None) or 10.0
        request_started = time.perf_counter()
        latency, response = send_batch(
            session,
            url=args.url,
            token=args.token,
            hmac_secret=args.hmac_secret,
            payload=payload,
            timeout=timeout,
        )
        request_completed = request_started + latency

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            print(f"Smoke test failed: {exc}; body={response.text}", file=sys.stderr)
            return 1

        data = response.json()
        created = int(data.get("created", 0))
        deduped = int(data.get("deduped", 0))
        decisions = len(data.get("decisions", []))

        if diagnostics_tracker:
            expected_created = created if args.expect_created is None else int(args.expect_created)
            expected_extracted = int(args.expect_extracted) if args.expect_extracted is not None else 0
            try:
                diagnostics_result = diagnostics_tracker.wait_for(
                    diagnostics_baseline,
                    expect_created=expected_created,
                    expect_extracted=expected_extracted,
                    timeout=args.diagnostics_timeout,
                    interval=args.diagnostics_interval,
                )
                if diagnostics_result.get("created_reached_at") is not None:
                    ingest_delay = diagnostics_result["created_reached_at"] - request_started
                if diagnostics_result.get("extracted_reached_at") is not None:
                    extract_delay = diagnostics_result["extracted_reached_at"] - request_started
            except DiagnosticsTimeoutError as exc:
                diagnostics_failed = True
                diagnostics_result = {
                    "diff": exc.diff,
                    "final": exc.snapshot.totals,
                    "created_reached_at": None,
                    "extracted_reached_at": None,
                }
                print(
                    "Diagnostics verification timed out: created={} extracted={}".format(
                        exc.diff.get("created", 0), exc.diff.get("extracted", 0)
                    ),
                    file=sys.stderr,
                )
            except requests.RequestException as exc:
                diagnostics_failed = True
                print(f"Failed to poll diagnostics: {exc}", file=sys.stderr)

    status_label = "passed" if not diagnostics_failed else "completed (diagnostics alert)"
    print(f"Smoke test {status_label}")
    print(f"  latency: {latency:.3f}s")
    print(f"  created: {created} | deduped: {deduped} | decisions: {decisions}")
    if diagnostics_result:
        diff = diagnostics_result.get("diff", {})
        print(
            "  diagnostics delta: accepted={accepted} created={created} extracted={extracted} errors={errors}".format(
                accepted=diff.get("accepted", 0),
                created=diff.get("created", 0),
                extracted=diff.get("extracted", 0),
                errors=diff.get("errors", 0),
            )
        )
        if ingest_delay is not None:
            print(f"  ingest lag: {ingest_delay:.3f}s")
        if extract_delay is not None:
            print(f"  extract lag: {extract_delay:.3f}s")

    if args.report_file:
        report = {
            "mode": "smoke",
            "timestamp": _utcnow_iso(),
            "batch_size": len(payload.get("messages", [])),
            "request": {
                "url": args.url,
                "latency_seconds": latency,
                "status_code": response.status_code,
                "completed_perf_counter": request_completed,
            },
            "response": data,
            "diagnostics": None,
        }
        if diagnostics_tracker:
            report["diagnostics"] = {
                "baseline": diagnostics_baseline.totals if diagnostics_baseline else None,
                "final": diagnostics_result.get("final") if diagnostics_result else None,
                "diff": diagnostics_result.get("diff") if diagnostics_result else None,
                "ingest_delay_seconds": ingest_delay,
                "extraction_delay_seconds": extract_delay,
            }
        try:
            Path(args.report_file).write_text(json.dumps(report, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"Warning: failed to write report file {args.report_file}: {exc}", file=sys.stderr)

    if diagnostics_failed:
        print("  ALERT: diagnostics expectations not met", file=sys.stderr)
        return 2

    return 0


def command_load(args: argparse.Namespace) -> int:
    sample_path = Path(args.sample) if args.sample else DEFAULT_SAMPLE_PATH
    base_messages = load_messages(sample_path)
    if not base_messages:
        print("Sample message payload is empty", file=sys.stderr)
        return 1

    batches: List[dict] = []
    for _ in range(args.count):
        random.shuffle(base_messages)
        chunk = base_messages[: args.batch_size]
        batches.append(prepare_batch(chunk, client_id=args.client_id, jitter=args.jitter))

    latencies: List[float] = []
    status_counts: dict[int, int] = {}
    created_total = 0
    deduped_total = 0
    errors_total = 0
    overall_start: float | None = None

    diagnostics_tracker: DiagnosticsTracker | None = None
    diagnostics_baseline: DiagnosticsData | None = None
    diagnostics_result: dict | None = None
    diagnostics_failed = False
    ingest_delay: float | None = None
    extract_delay: float | None = None

    with requests.Session() as session:
        if args.diagnostics_url:
            diagnostics_tracker = DiagnosticsTracker(
                session,
                url=args.diagnostics_url,
                client_id=args.client_id,
                chat_filters=args.diagnostics_chat,
                timeout=args.diagnostics_http_timeout,
            )
            try:
                diagnostics_baseline = diagnostics_tracker.snapshot()
            except requests.RequestException as exc:
                print(f"Failed to fetch diagnostics baseline: {exc}", file=sys.stderr)
                return 2

        for index, batch in enumerate(batches, start=1):
            batch_start = time.perf_counter()
            latency, response = send_batch(
                session,
                url=args.url,
                token=args.token,
                hmac_secret=args.hmac_secret,
                payload=batch,
                timeout=args.timeout,
            )
            if overall_start is None:
                overall_start = batch_start
            latencies.append(latency)
            status_counts[response.status_code] = status_counts.get(response.status_code, 0) + 1
            if response.ok:
                data = response.json()
                created_total += int(data.get("created", 0))
                deduped_total += int(data.get("deduped", 0))
                errors_total += sum(1 for d in data.get("decisions", []) if d.get("status") == "skipped")
            else:
                print(f"Batch {index} failed with {response.status_code}: {response.text[:200]}", file=sys.stderr)
            if args.sleep:
                time.sleep(args.sleep)

        if diagnostics_tracker:
            expected_created = created_total if args.expect_created is None else int(args.expect_created)
            if args.expect_extracted is not None:
                expected_extracted = int(args.expect_extracted)
            elif args.slo_extracted_ratio is not None and created_total:
                expected_extracted = int(created_total * args.slo_extracted_ratio)
            else:
                expected_extracted = 0
            expected_extracted = min(expected_extracted, expected_created)
            try:
                diagnostics_result = diagnostics_tracker.wait_for(
                    diagnostics_baseline,
                    expect_created=expected_created,
                    expect_extracted=expected_extracted,
                    timeout=args.diagnostics_timeout,
                    interval=args.diagnostics_interval,
                )
                reference_start = overall_start if overall_start is not None else time.perf_counter()
                if diagnostics_result.get("created_reached_at") is not None:
                    ingest_delay = diagnostics_result["created_reached_at"] - reference_start
                if diagnostics_result.get("extracted_reached_at") is not None:
                    extract_delay = diagnostics_result["extracted_reached_at"] - reference_start
            except DiagnosticsTimeoutError as exc:
                diagnostics_failed = True
                diagnostics_result = {
                    "diff": exc.diff,
                    "final": exc.snapshot.totals,
                    "created_reached_at": None,
                    "extracted_reached_at": None,
                }
                print(
                    "Diagnostics verification timed out: created={} extracted={}".format(
                        exc.diff.get("created", 0), exc.diff.get("extracted", 0)
                    ),
                    file=sys.stderr,
                )
            except requests.RequestException as exc:
                print(f"Failed to poll diagnostics: {exc}", file=sys.stderr)
                return 2

    print("Load run summary")
    print(f"  batches: {len(batches)} | batch_size: {args.batch_size}")
    print(f"  status histogram: {status_counts}")

    sorted_lat = sorted(latencies)
    latency_stats = None
    if sorted_lat:
        p95_index = max(0, int(len(sorted_lat) * 0.95) - 1)
        latency_stats = {
            "min": sorted_lat[0],
            "p50": statistics.median(sorted_lat),
            "p95": sorted_lat[p95_index],
            "max": sorted_lat[-1],
        }
        print(
            "  latency (s): min={min:.3f} p50={p50:.3f} p95={p95:.3f} max={max:.3f}".format(**latency_stats)
        )

    print(f"  created: {created_total} | deduped: {deduped_total} | skipped: {errors_total}")

    total_decisions = created_total + deduped_total + errors_total
    error_rate = (errors_total / total_decisions) if total_decisions else 0.0
    dedupe_ratio = (deduped_total / total_decisions) if total_decisions else 0.0

    slo_breaches: list[str] = []
    if status_counts.get(429):
        slo_breaches.append("429 responses detected")
    if status_counts.get(500):
        slo_breaches.append("5xx responses detected")
    if latency_stats and args.slo_p95_seconds is not None and latency_stats["p95"] > args.slo_p95_seconds:
        slo_breaches.append(
            f"p95 latency {latency_stats['p95']:.3f}s exceeds threshold {args.slo_p95_seconds:.3f}s"
        )
    if args.slo_error_rate is not None and error_rate > args.slo_error_rate:
        slo_breaches.append(
            f"error rate {error_rate:.3f} exceeds threshold {args.slo_error_rate:.3f}"
        )

    offer_success_rate = None
    if diagnostics_result:
        diff = diagnostics_result.get("diff", {})
        created_diff = diff.get("created", 0) or 0
        extracted_diff = diff.get("extracted", 0) or 0
        if created_diff:
            offer_success_rate = extracted_diff / created_diff
        print(
            "  diagnostics delta: accepted={accepted} created={created} extracted={extracted} errors={errors}".format(
                accepted=diff.get("accepted", 0),
                created=created_diff,
                extracted=extracted_diff,
                errors=diff.get("errors", 0),
            )
        )
        if ingest_delay is not None:
            print(f"  ingest lag: {ingest_delay:.3f}s")
        if extract_delay is not None:
            print(f"  extract lag: {extract_delay:.3f}s")
        if (
            offer_success_rate is not None
            and args.slo_extracted_ratio is not None
            and offer_success_rate < args.slo_extracted_ratio
        ):
            slo_breaches.append(
                f"offer success {offer_success_rate:.3f} below threshold {args.slo_extracted_ratio:.3f}"
            )

    if slo_breaches or diagnostics_failed:
        if slo_breaches:
            print("  ALERT: " + "; ".join(slo_breaches), file=sys.stderr)
        if diagnostics_failed:
            print("  ALERT: diagnostics expectations not met", file=sys.stderr)
        exit_code = 2 if not slo_breaches else 3
    else:
        exit_code = 0

    if args.report_file:
        report = {
            "mode": "load",
            "timestamp": _utcnow_iso(),
            "config": {
                "count": args.count,
                "batch_size": args.batch_size,
                "timeout": args.timeout,
                "sleep": args.sleep,
            },
            "latency": latency_stats,
            "totals": {
                "created": created_total,
                "deduped": deduped_total,
                "skipped": errors_total,
                "error_rate": error_rate,
                "dedupe_ratio": dedupe_ratio,
                "status_counts": status_counts,
            },
            "diagnostics": None,
            "slo": {
                "breaches": slo_breaches,
                "p95_threshold": args.slo_p95_seconds,
                "error_rate_threshold": args.slo_error_rate,
                "extracted_ratio_threshold": args.slo_extracted_ratio,
                "offer_success_rate": offer_success_rate,
            },
        }
        if diagnostics_tracker:
            report["diagnostics"] = {
                "baseline": diagnostics_baseline.totals if diagnostics_baseline else None,
                "final": diagnostics_result.get("final") if diagnostics_result else None,
                "diff": diagnostics_result.get("diff") if diagnostics_result else None,
                "ingest_delay_seconds": ingest_delay,
                "extraction_delay_seconds": extract_delay,
                "failed": diagnostics_failed,
            }
        try:
            Path(args.report_file).write_text(json.dumps(report, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"Warning: failed to write report file {args.report_file}: {exc}", file=sys.stderr)

    return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="WhatsApp ingest smoke/load harness")
    parser.add_argument("--url", required=True, help="POST endpoint for /integrations/whatsapp/ingest")
    parser.add_argument("--token", required=True, help="X-Ingest-Token shared secret")
    parser.add_argument("--hmac-secret", help="Optional HMAC secret (WHATSAPP_INGEST_HMAC_SECRET)")
    parser.add_argument("--client-id", default=os.getenv("CLIENT_ID", "harness-smoke"), help="Client ID used in batches")
    parser.add_argument("--sample", help="Path to sample payload JSON (defaults to docs/whatsapp_ingest_contract_sample.json)")
    parser.add_argument("--batch-size", type=int, default=10, help="Messages per batch")
    parser.add_argument("--jitter", action="store_true", help="Add random message IDs to avoid dedupe")

    subparsers = parser.add_subparsers(dest="command", required=True)

    smoke = subparsers.add_parser("smoke", help="Send a single batch and exit")
    smoke.add_argument("--timeout", type=float, default=10.0, help="Request timeout in seconds")
    smoke.add_argument("--report-file", help="Optional path to write a JSON summary")
    smoke.add_argument("--diagnostics-url", help="Optional /chat/tools/diagnostics endpoint for verification")
    smoke.add_argument(
        "--diagnostics-chat",
        action="append",
        help="Restrict diagnostics checks to chats matching this title substring or chat_id (repeatable)",
    )
    smoke.add_argument(
        "--diagnostics-timeout",
        type=float,
        default=30.0,
        help="Seconds to wait for diagnostics counters to reach expectations",
    )
    smoke.add_argument(
        "--diagnostics-interval",
        type=float,
        default=2.0,
        help="Polling interval (seconds) for diagnostics",
    )
    smoke.add_argument(
        "--diagnostics-http-timeout",
        type=float,
        default=10.0,
        help="HTTP timeout (seconds) when calling diagnostics",
    )
    smoke.add_argument(
        "--expect-created",
        type=int,
        help="Override expected created count reported by diagnostics (defaults to API response)",
    )
    smoke.add_argument(
        "--expect-extracted",
        type=int,
        help="Minimum extracted offers expected via diagnostics (default: skip assertion)",
    )
    smoke.set_defaults(func=command_smoke)

    load = subparsers.add_parser("load", help="Replay batches to baseline throughput")
    load.add_argument("--count", type=int, default=50, help="Number of batches to send")
    load.add_argument("--timeout", type=float, default=10.0, help="Request timeout in seconds")
    load.add_argument("--sleep", type=float, default=0.0, help="Optional pause between batches (seconds)")
    load.add_argument("--report-file", help="Optional path to write a JSON summary")
    load.add_argument("--diagnostics-url", help="Optional /chat/tools/diagnostics endpoint for verification")
    load.add_argument(
        "--diagnostics-chat",
        action="append",
        help="Restrict diagnostics checks to chats matching this title substring or chat_id (repeatable)",
    )
    load.add_argument(
        "--diagnostics-timeout",
        type=float,
        default=120.0,
        help="Seconds to wait for diagnostics counters to reach expectations",
    )
    load.add_argument(
        "--diagnostics-interval",
        type=float,
        default=5.0,
        help="Polling interval (seconds) for diagnostics",
    )
    load.add_argument(
        "--diagnostics-http-timeout",
        type=float,
        default=10.0,
        help="HTTP timeout (seconds) when calling diagnostics",
    )
    load.add_argument(
        "--expect-created",
        type=int,
        help="Expected diagnostics created delta (default: total from API responses)",
    )
    load.add_argument(
        "--expect-extracted",
        type=int,
        help="Expected diagnostics extracted delta (default: derived from slo-extracted-ratio)",
    )
    load.add_argument(
        "--slo-p95-seconds",
        type=float,
        default=60.0,
        help="Maximum acceptable p95 request latency (seconds)",
    )
    load.add_argument(
        "--slo-error-rate",
        type=float,
        default=0.02,
        help="Maximum acceptable skipped/error decision ratio",
    )
    load.add_argument(
        "--slo-extracted-ratio",
        type=float,
        default=0.95,
        help="Minimum extracted/created ratio expected (diagnostics required)",
    )
    load.set_defaults(func=command_load)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
