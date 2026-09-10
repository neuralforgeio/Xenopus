"""Gateway layer: multi-channel transport — Telegram, Discord, webhooks (ADR-028).

Implementation phase: Phase 9 (notification router), Phase 12-14 (channel
adapters). Channels never anchor to the core runtime; adapters consume the
RuntimeDriver contract.
"""

from xenopus.gateway.telegram import TelegramClient, TelegramCommander, TelegramSink

__all__ = ["TelegramClient", "TelegramCommander", "TelegramSink"]
