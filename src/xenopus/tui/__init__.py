"""Xenopus TUI (Textual) — Phase 10.

A keyboard-first terminal surface over the EXISTING runtime engines
(ADR-021): the durable TaskStore, the persistent ApprovalStore, the
Scheduler, the AgentPool watchdog, and the NotificationRouter. The TUI
adds no runtime logic of its own — every action delegates to the same
engines the CLI uses (ADR-001 boundary: surfaces consume, never own).
"""
