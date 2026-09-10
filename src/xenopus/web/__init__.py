"""Xenopus Web dashboard (Phase 11) — local browser control surface.

Starlette + Uvicorn render the SAME engine bundle the TUI consumes
(TaskStore, ApprovalStore, Scheduler, AgentPool, NotificationRouter)
as server-side HTML on 127.0.0.1 ONLY. The web layer adds no runtime
logic (ADR-001/022): every action delegates to the engines, exactly
like the CLI and TUI.
"""
