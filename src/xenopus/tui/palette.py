"""Command palette provider exposing XenopusApp actions.

Feeds the app's own commands (new task, refresh, killswitch, digest
flush, panel focus) into the Textual palette (ctrl+p). The provider is
a view-layer adapter: it only lists and dispatches actions the app
already exposes — no engine calls happen here (ADR-021).

The provider deliberately does NOT import XenopusApp: app.py imports
this module to register the provider in ``COMMANDS``, so a reverse
import would be circular. Commands are resolved from the running app
instance at call time.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from textual.command import DiscoveryHit, Hit, Hits, Provider

CommandSpec = tuple[str, str, Callable[[], Any]]


def app_commands(app: Any) -> list[CommandSpec]:
    """(title, help, action) triples for the palette, deterministic order."""
    return [
        ("new task", "create a durable task from a typed title", app.action_new_task),
        ("refresh panels", "reload tasks/approvals/schedules/agents", app.action_refresh),
        ("killswitch", "cancel ALL live tasks (confirmed)", app.action_killswitch),
        ("flush digest", "deliver held digest notifications", app.action_flush_digest),
        ("focus tasks", "switch to the tasks panel", lambda: app.action_focus_panel("tasks-pane")),
        (
            "focus approvals",
            "switch to the approvals panel",
            lambda: app.action_focus_panel("approvals-pane"),
        ),
        (
            "focus schedules",
            "switch to the schedules panel",
            lambda: app.action_focus_panel("schedules-pane"),
        ),
        (
            "focus agents",
            "switch to the agents panel",
            lambda: app.action_focus_panel("agents-pane"),
        ),
    ]


class XenopusCommands(Provider):
    """Palette provider for the Xenopus control actions."""

    async def search(self, query: str) -> Hits:
        """Yield palette hits matching the query (fuzzy)."""
        matcher = self.matcher(query)
        for title, help_text, action in app_commands(self.app):
            if (match := matcher.match(title)) > 0:
                yield Hit(
                    match,
                    matcher.highlight(title),
                    action,
                    help=help_text,
                )

    async def discover(self) -> Hits:
        """Yield all commands for the empty palette (browse mode)."""
        for title, help_text, action in app_commands(self.app):
            yield DiscoveryHit(title, action, help=help_text)
