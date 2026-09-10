# ADR-026: Self-Improvement Loop = Deterministic Consolidation Through Existing Gates

## Status
Accepted (2026-09-10, Phase 15)

## Context
Xenopus's namesake loop ends with Reflect → Learn → Consolidate →
Improve → Reuse, but Phases 1-14 built only the acting half: the
engines record evidence (journal, ADR-004), empirical performance
(reliability records, ADR-018), and offer gated knowledge stores
(memory ADR-011, skills ADR-012) — nothing yet consolidates
experience INTO them. Phase 15 closes the gap under one constraint:
the runtime improving itself is precisely what the governance
gates exist for. The loop must therefore be *less* autonomous than
the acting engines, never more.

User scope decision (2026-09-10): Phase 15 = self-improvement
loops (desktop 16, release 19 deferred).

## Decision
Implement the ReflectionLoop as DETERMINISTIC consolidation — no
model calls, no free-form "reflections", no self-modification:

- **Evidence source**: the ReliabilityStore (ADR-018) and event
  journal — recorded, empirical data only. The loop never treats
  model prose or agent claims as facts. Poisoning defense is
  structural: there is no prose input path.
- **Write path**: ONLY the existing store APIs. Memory insights
  enter as CANDIDATE via MemoryStore.create() and pass the SAME
  PromotionGate (evidence + confidence, ADR-011). Skill usage
  appends SkillEvaluation rows via record_evaluation() feeding
  the SAME 7-stage ladder (ADR-012). No gate is modified, no
  promotion is forced, and a gate refusal aborts that item — the
  loop NEVER overrides a refusal.
- **Trigger**: deterministic. The loop runs when invoked
  (`xenopus reflect` now; a Scheduler self-maintenance job wiring
  arrives with hosting surfaces that want periodic consolidation —
  register_job, addendum 39/100, keeps it bounded and journaled).
  Model agency over WHEN to learn is deliberately absent.
- **Insight rules (closed set, conservative thresholds)**:
  1. tool reliability: failures >= 3 and success rate < 0.9 over
     recorded invocations -> one FAILURE-kind memory candidate.
  2. agent-role reliability: same rule over kind="agent" -> one
     SEMANTIC-kind candidate.
  3. retry-heavy tools: avg duration or failure pattern flags the
     tool name for the planner's reliability ranking (informational
     memory; ranking itself stays ADR-018's).
  4. skill evaluation: a STAGED skill with recorded successes/
     failures gets one SkillEvaluation appended — nothing more.
  Thresholds are constants, documented, tunable by future ADR;
  they are NOT model-decided.
- **Scope discipline**: insights land in MemoryScope.WORKSPACE with
  scope_ref "xenopus-self" — one inspectable, purgeable scope.
  Never GLOBAL, never USER: the blast radius of a wrong insight is
  a single workspace scope, and `xenopus reflect` output names
  every write.
- **Budget**: fixed per-run caps (MAX_MEMORY_CANDIDATES_PER_RUN,
  MAX_SKILL_EVALUATIONS_PER_RUN). The loop cannot write unboundedly.
- **Idempotence**: before proposing, the loop fingerprints each
  insight and checks existing CANDIDATE/ACTIVE items in the
  xenopus-self scope — a repeat run over unchanged data proposes
  nothing. Re-consolidation is safe by construction.
- **Failure isolation**: any store error aborts the run cleanly
  with a journaled event; when hosted as a scheduler job the tick
  survives (the scheduler already isolates job failures).

## Reversal Criteria
Revisit when (a) model-generated memory content is proposed — that
requires a NEW ADR with stricter evidence gates (the deliberate
exclusion here); (b) insight rules grow past ~10 or need context
sensitivity — then rules-as-data (config-driven) deserves its own
design; (c) consolidation volume demands streaming rather than
per-run batching.

## Sunset Review
Phase 16 planning (desktop shell + inbound-security review of
ADR-022..025), together with the first review of accumulated
xenopus-self insights (quality audit of the loop's own output).

## Consequences
### Positive
- The loop closes with zero new trust paths: every write passes
  gates that existed and were tested since Phase 5.
- Learning is auditable end-to-end: journal run entry -> evidence
  correlation -> gated candidate -> gate decision.
- Idempotent, budgeted, scope-contained — safe to run repeatedly.
### Negative
- Insights are statistical summaries, not deep reasoning — the
  deterministic scope is narrow by design (a feature, priced as a
  limitation).
- Two reliability thresholds (>=3 failures, <0.9 rate) are
  starting points; mis-calibration means noisy or silent
  candidates until tuned.
### Neutral
- `xenopus reflect` joins the CLI as the manual trigger; hosting
  surfaces decide cadence later.

## Alternatives Considered
- LLM-written reflections into memory — REJECTED for this phase:
  model-generated content as memory is a poisoning surface; needs
  adversarial evaluation gates that do not exist yet (reversal
  criterion (a)).
- Auto-promoting insights (skip the gate for self-generated
  content) — REJECTED: any fast path around PromotionGate violates
  ADR-011's "auto-trust is structurally impossible" invariant.
- Learning inline during task execution — REJECTED: temporal
  coupling of acting and learning muddies both audit trails; the
  scheduler/trigger separation keeps them distinct.

## References
- Master prompt 17-30 (loop stages; memory 22-27; skills 28-30),
  133-134 (promotion gates), addendum 39/100 (bounded jobs), 88/107
  (reliability records)
- ADR-004 (journal), ADR-011 (memory), ADR-012 (skills), ADR-018
  (reliability), ADR-019 (scheduler)
- User scope decision 2026-09-10: self-improvement loops at 15
