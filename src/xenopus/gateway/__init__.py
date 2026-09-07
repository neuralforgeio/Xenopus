"""Gateway layer: transport multi-channel — Telegram, Discord, webhook (ADR-028).

Fase implementasi: Phase 9 (notification router), Phase 12-14 (channel adapters).
Channel TIDAK pernah masuk ke core runtime; adapter mengonsumsi RuntimeDriver.
"""
