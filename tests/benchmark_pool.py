"""Agent-pool benchmarks (assumption #6): concurrency curve.

On-demand performance evidence (`pytest -m benchmark`). Measures
whether MAX_CONCURRENT_AGENTS=4 fits the target hardware
(i5-8350U/8GB) by timing blocking agent work at concurrency
1/2/4/8 with INJECTED workers running in a thread pool — the way
a real executor would offload tool/model I/O.

Decision rule (ADR ledger #6): if 4->8 shows no wall-time
improvement AND per-agent overhead grows, the constant 4 stands;
if 8 beats 4 materially (>20%), record a tuning recommendation
(constant changes still need user sign-off).
"""

from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from xenopus.runtime.agent import AgentContract, AgentProfile
from xenopus.runtime.agent_pool import AgentPool, PoolLimits

pytestmark = pytest.mark.benchmark

CONCURRENCIES = (1, 2, 4, 8)
AGENTS_PER_LEVEL = 16
WORK_UNITS_PER_AGENT = 2_000_000
MATERIAL_IMPROVEMENT = 1.2  # 8 must beat 4 by >20% to recommend a change
_EXECUTOR = ThreadPoolExecutor(max_workers=8)


def _profile(role: str = "worker") -> AgentProfile:
    return AgentProfile(
        role=role,
        allowed_tools=frozenset(),
        forbidden_tools=frozenset(),
        permissions_subject="bench:worker",
    )


def _contract(task_id: str) -> AgentContract:
    return AgentContract(mission="bench", task_id=task_id)


def _blocking_work(units: int) -> int:
    """CPU-bound work executed in a worker thread (the I/O shape)."""
    total = 0
    for i in range(units):
        total += i ^ (i // 3)
    return total


async def _run_pool_level(max_concurrent: int) -> tuple[float, int, int]:
    """Run AGENTS_PER_LEVEL agents under one concurrency bound.

    Returns (wall_seconds, violations, peak_live). Agents hold their
    pool slot while their blocking work runs in the thread pool —
    admission interleaving is real, so live counts vary.
    """
    pool = AgentPool(limits=PoolLimits(max_concurrent=max_concurrent, max_total=100))
    loop = asyncio.get_running_loop()
    violations = 0
    peak = 0

    async def one_agent(index: int) -> None:
        nonlocal violations, peak
        handle = await pool.acquire(_contract(f"bench-{index}"), _profile())
        peak = max(peak, pool.live_count)
        if pool.live_count > max_concurrent:
            violations += 1
        await loop.run_in_executor(_EXECUTOR, _blocking_work, WORK_UNITS_PER_AGENT)
        pool.release(handle.run_id, failed=False)

    start = time.perf_counter()
    await asyncio.gather(*(one_agent(i) for i in range(AGENTS_PER_LEVEL)))
    elapsed = time.perf_counter() - start
    print(
        f"  concurrency={max_concurrent}: {elapsed:.3f}s "
        f"(peak live={peak}, violations={violations})"
    )
    return elapsed, violations, peak


def test_concurrency_curve() -> None:
    """Time the 1/2/4/8 curve; assert guardrails; report the verdict."""
    print(
        f"\n[pool curve] agents={AGENTS_PER_LEVEL}, "
        f"blocking work={WORK_UNITS_PER_AGENT:,} units/agent (thread pool)"
    )
    results: dict[int, float] = {}
    violations_total = 0
    for level in CONCURRENCIES:
        elapsed, violations, peak = asyncio.run(_run_pool_level(level))
        results[level] = elapsed
        violations_total += violations
        assert peak == level or peak <= level, "peak live above bound"
    assert violations_total == 0, "pool admitted above max_concurrent"

    speedup_4_to_8 = results[4] / results[8]
    verdict = (
        "8 WINS materially — tuning candidate"
        if speedup_4_to_8 > MATERIAL_IMPROVEMENT
        else "4 SUFFICES (no material 8-gain)"
    )
    print(f"\n[pool verdict] 4->8 speedup factor: {speedup_4_to_8:.2f} ({verdict})")
    # Guardrail: parallel levels must not be dramatically SLOWER than 1
    # (would indicate admission overhead dominating).
    for level in CONCURRENCIES[1:]:
        assert results[level] < results[1] * 1.5, f"concurrency {level} regressed vs 1"
