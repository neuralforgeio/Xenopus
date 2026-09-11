"""Journal benchmarks (assumption #7): SQLite WAL append/read at scale.

On-demand performance evidence (`pytest -m benchmark`). Guardrails
assert only true pathologies at RUNTIME-REALISTIC scales; timings
are REPORTED for the assumption ledger — no CI-flaky thresholds.

Measured envelope (i5-8350U/8GB, NVMe, 2026-09-10):
- Unbatched append: ~263 ops/s — each append() commits+fsyncs
  individually (the crash-recovery durability contract, Phase 6).
  Runtime event flow is burst-scale (tens of events per task), so
  the unbatched guardrail runs at 1k with a generous budget.
- Batched append (maintenance pattern): ~254k ops/s at 20k.
- events_for() read: 2.2ms at 20k rows. WAL: 3.9 MB at 20k events.
Bulk paths MUST batch; the runtime never bulk-writes unbatched.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from xenopus.persistence.journal import JOURNAL_TABLE_DDL, EventJournal
from xenopus.runtime.events import Event, EventType

pytestmark = pytest.mark.benchmark

UNBATCHED_SCALES = (200, 1_000)
UNBATCHED_BUDGET_SECONDS = 10.0  # 1k at measured 3.8ms/append = 3.8s
BATCHED_SCALES = (1_000, 5_000, 20_000)
BATCHED_BUDGET_SECONDS = 30.0
READ_BUDGET_SECONDS = 1.0


def _event(i: int) -> Event:
    return Event(
        type=EventType.TASK_COMPLETED,
        correlation_id=f"bench-{i % 100}",
        payload={"i": i, "note": "benchmark event"},
    )


@pytest.mark.parametrize("count", UNBATCHED_SCALES)
def test_unbatched_append_throughput(tmp_path: Path, count: int) -> None:
    """Individual appends (durability contract) at burst scale."""
    journal = EventJournal(tmp_path / "bench.sqlite")
    try:
        start = time.perf_counter()
        for i in range(count):
            journal.append(_event(i))
        elapsed = time.perf_counter() - start
        ops = count / elapsed if elapsed else float("inf")
        print(f"\n[journal unbatched append] n={count}: {elapsed:.3f}s -> {ops:,.0f} ops/s")
        assert elapsed < UNBATCHED_BUDGET_SECONDS, (
            f"unbatched appends too slow at burst scale: {elapsed:.1f}s"
        )
    finally:
        journal.close()


@pytest.mark.parametrize("count", BATCHED_SCALES)
def test_batched_append_throughput(tmp_path: Path, count: int) -> None:
    """Single-transaction batched appends (bulk/maintenance pattern)."""
    conn = sqlite3.connect(tmp_path / "bench-batch.sqlite")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(JOURNAL_TABLE_DDL)
    conn.commit()
    try:
        start = time.perf_counter()
        conn.execute("BEGIN")
        for i in range(count):
            conn.execute(
                "INSERT OR IGNORE INTO event_journal "
                "(event_id, type, correlation_id, payload, schema_version, occurred_at) "
                "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (f"bench-{i}", "TASK_COMPLETED", f"bench-{i % 100}", "{}", 1),
            )
        conn.commit()
        elapsed = time.perf_counter() - start
        ops = count / elapsed if elapsed else float("inf")
        print(f"\n[journal batched append] n={count}: {elapsed:.3f}s -> {ops:,.0f} ops/s")
        assert elapsed < BATCHED_BUDGET_SECONDS
    finally:
        conn.close()


def test_read_latency_at_scale(tmp_path: Path) -> None:
    """events_for() at 20k rows; guardrail: bounded read."""
    journal = EventJournal(tmp_path / "bench-read.sqlite")
    try:
        target = 20_000
        for i in range(target):
            journal.append(_event(i))
        start = time.perf_counter()
        events = journal.events_for("bench-42")
        elapsed = time.perf_counter() - start
        print(
            f"\n[journal read] events_for at {target:,} rows: "
            f"{elapsed * 1000:.1f}ms -> {len(events)} events"
        )
        assert elapsed < READ_BUDGET_SECONDS
        assert len(events) == target // 100
    finally:
        journal.close()


def test_wal_growth_bounded(tmp_path: Path) -> None:
    """WAL sidecar size after 20k batched events (reported, loose cap)."""
    conn = sqlite3.connect(tmp_path / "bench-wal.sqlite")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(JOURNAL_TABLE_DDL)
    conn.execute("BEGIN")
    for i in range(20_000):
        conn.execute(
            "INSERT OR IGNORE INTO event_journal "
            "(event_id, type, correlation_id, payload, schema_version, occurred_at) "
            "VALUES (?, ?, ?, ?, ?, datetime('now'))",
            (f"bench-{i}", "TASK_COMPLETED", f"bench-{i % 100}", "{}", 1),
        )
    conn.commit()
    wal = Path(str(tmp_path / "bench-wal.sqlite") + "-wal")
    size_mb = wal.stat().st_size / (1024 * 1024) if wal.exists() else 0.0
    conn.close()
    print(f"\n[journal WAL] size after 20k events: {size_mb:.1f} MB")
    assert size_mb < 512, "WAL runaway growth"
