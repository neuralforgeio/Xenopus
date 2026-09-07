"""Runtime layer: the core AgentRuntime — owner of the agent lifecycle (ADR-001).

Implementation phase: Phase 2 (FSM, Goal, Plan). This module marks the
ownership boundary so dependents never cross it (see ``xenopus.gateway``).
"""
