# ADR-016: Orchestrator — Bounded Multi-Agent Execution Plane

## Status
Accepted

## Context
Phase 7 executes plan DAGs with multiple agents (addendum 2-15, 59,
103, 110, 120). The non-negotiables: bounded concurrency, structured
agent contracts/results, failure isolation, recursion limits, a
stuck-detection watchdog, and a cost gate that refuses gratuitous
parallelism.

## Decision
1. **Agent model (runtime/agent.py):** `AgentProfile` (role, tool
   bounds, permission subject, budget, depth/children limits,
   heartbeat timeout — overlapping allowed/forbidden tool sets are
   rejected at construction); `AgentContract` (mission + task + inputs
   + success criteria — never a raw prompt, addendum 10);
   `AgentResult` (status/artifacts/evidence/confidence factories —
   never raw text, addendum 11).
2. **Agent pool (runtime/agent_pool.py):** admission control via
   asyncio.Semaphore (MAX_CONCURRENT_AGENTS=4 default — [I], to be
   benchmarked); session limits MAX_DEPTH=2, MAX_CHILDREN=4,
   MAX_TOTAL=12 (addendum 15) enforced at acquire with typed
   AdmissionError; heartbeat tracking with a stuck-detection watchdog
   that transitions health (HEALTHY -> STUCK -> RECOVERING on
   heartbeat, addendum 19/59) and NEVER kills silently (addendum 58).
3. **Orchestrator (runtime/orchestrator.py):** Kahn-layer scheduling
   (deterministic, reuses the plan's validated topological order);
   within a layer, independent nodes run concurrently via
   asyncio.gather — one node's failure/crash/timeout NEVER cancels
   siblings (addendum 14; exceptions convert to structured FAILED/
   TIMEOUT results). Downstream nodes whose dependencies did not
   complete are marked FAILED with a dependency-loss error instead of
   running on missing evidence. Overall: COMPLETED (all) / PARTIAL
   (some; confidence = completed/total, incomplete evidence listed) /
   FAILED (none).
4. **Cost gate (addendum 103/110):** deterministic overhead/benefit
   model — parallelism refused when every layer has fanout 1 or when
   the ratio exceeds 10. Callers may re-run with force_sequential.
5. **Tool quarantine (addendum 120):** registry-level gate —
   quarantined tools remain registered (auditable) but get() raises
   ToolQuarantineError and the gateway maps it to a structured
   `tool_quarantined` failure; lift() is explicit. The executor treats
   it as a permanent error (never retried).

## Reversal Criteria
If Phase 8 (teams/aggregation) needs cross-agent messaging, add a
broker module — never relax admission limits or isolation to do it.

## Sunset Review
Phase 8 (aggregation consumes OrchestrationReport), Phase 18 (the
MAX_CONCURRENT_AGENTS benchmark lands here).

## Consequences
### Positive
- Unbounded spawn is structurally impossible; silent stuck agents are
  detectable; parallelism pays its way or is refused.
- Partial results carry explicit confidence and evidence markers.
### Negative
- Semaphore waits serialize bursts (intended backpressure).
- Pool watchdog checks are caller-driven (scheduler hooks arrive in 9).
### Neutral
- The runner is injected (async callable) — the agent-brain integration
  (provider-driven reasoning) lands in Phase 8+ without changing this
  plane's contracts.

## Alternatives Considered
- Spawn-per-task without a pool — rejected: unbounded (addendum 6-7).
- Retry failed agents inside the orchestrator — deferred to Phase 8
  (retry policy already exists; orchestration-level substitution needs
  reliability data first).

## References
- Addendum 2-15, 19, 58-59, 103-104, 110, 120; ADR-003 pattern.
