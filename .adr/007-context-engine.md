# ADR-007: Context Engine — Budgeted Assembly with Protected Segments

## Status
Accepted

## Context
Context assembly must respect token budgets (master prompt 51-52),
never silently drop system/goal constraints, and never let untrusted
tool output masquerade as instruction (100/131).

## Decision
1. `ContextEngine.assemble()` takes typed `ContextSegment`s (message +
   protected flag + estimated tokens) and a hard token budget.
2. Protected segments (system prompt, goal, constraints, decisions,
   current state) are never dropped or truncated. If protected segments
   ALONE exceed the budget, the engine raises `ContextBudgetError` —
   failing loudly beats silently shipping a context without its spine.
3. Droppable segments fill remaining budget NEWEST-first (recent context
   outweighs old history), then results are restored to original order.
4. Estimation: len(content) // 4 (chars-per-token heuristic) — consistent
   with the rest of the runtime; exact tokenization is a provider concern.
5. `wrap_untrusted()` wraps tool/web output in explicit
   `<untrusted-content>` boundaries — defense-in-depth for injection.
6. A Hypothesis property test pins the core invariant: for ANY segment
   list and budget, protected segments are always kept and the assembled
   total never exceeds the budget.

## Reversal Criteria
If a provider's tokenizer makes char-based estimates wildly wrong (>2x),
switch estimation to a per-provider calibration table.

## Sunset Review
Phase 5 (memory retrieval ranks context candidates) and Phase 13
(compaction policies, master prompt 52).

## Consequences
### Positive
- Budget violations are impossible to miss; protected context is a
  structural guarantee, not a convention.
### Negative
- Greedy fill is not globally optimal (knapsack); acceptable — context
  selection values recency, not optimality.
### Neutral
- The engine is pure: same inputs, same outputs, trivially testable.

## Alternatives Considered
- Silent truncation of protected segments — rejected: destroys agent
  grounding without any signal (P5 violation).
- Provider-native context APIs — rejected: not portable, provider-specific.

## References
- Master prompt 51-52, 100, 131; Protocol v9 4.3.
