from pathlib import Path

from automation.infra.metrics import RequestMetricsRecorder


def test_record_and_summarize_metrics(tmp_path: Path) -> None:
    log_path = tmp_path / "metrics.jsonl"
    recorder = RequestMetricsRecorder(log_file=str(log_path))

    recorder.record(
        method="GET",
        endpoint="/console/api/apps",
        status_code=200,
        duration_ms=42.5,
        trace_id="trace_a",
        success=True
    )
    recorder.record(
        method="POST",
        endpoint="/console/api/apps/imports",
        status_code=401,
        duration_ms=88.0,
        trace_id="trace_b",
        success=False,
        error_type="AuthError"
    )

    summary = recorder.summarize()

    assert summary["total_requests"] == 2
    assert summary["success_rate"] == 50.0
    assert summary["status_distribution"]["200"] == 1
    assert summary["status_distribution"]["401"] == 1
    assert summary["failure_types"]["AuthError"] == 1
    assert summary["avg_duration_ms"] > 0
    assert summary["p95_duration_ms"] > 0


def test_tail_returns_recent_entries(tmp_path: Path) -> None:
    log_path = tmp_path / "metrics.jsonl"
    recorder = RequestMetricsRecorder(log_file=str(log_path))

    for idx in range(3):
        recorder.record(
            method="GET",
            endpoint=f"/endpoint/{idx}",
            status_code=200,
            duration_ms=idx + 1.0,
            trace_id=f"trace_{idx}",
            success=True
        )

    tail = recorder.tail(limit=2)
    assert len(tail) == 2
    assert tail[0]["endpoint"].endswith("/1")
    assert tail[1]["endpoint"].endswith("/2")

    recent_summary = recorder.summarize(window=1)
    assert recent_summary["total_requests"] == 1
    assert recent_summary["status_distribution"]["200"] == 1
