"""Runtime layer: the core AgentRuntime — owner of the agent lifecycle (ADR-001).

Shipped in Phase 2: FSM, budget primitives, event vocabulary, Goal manager,
Plan engine (DAG data plane). Later phases add the durable task runtime,
orchestrator, and execution planes. Dependents never cross this boundary
into gateway concerns (see ``xenopus.gateway``).
"""
