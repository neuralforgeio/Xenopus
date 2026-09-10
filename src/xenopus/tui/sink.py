"""Textual notification sink — a delivery surface, not a policy maker.

The SAME NotificationRouter policy governs TUI delivery as everywhere
else (addendum 87): priorities, quiet hours, digests, and rate limits
are decided by the router BEFORE this sink is called. The sink only
renders what the router already approved — the TUI can never become a
policy bypass.

Thread-safety: ``App.notify`` posts a message and is documented
thread-safe by Textual; ``deliver`` may therefore be called from any
thread. Before an app is attached (or after detach) deliveries buffer,
so the sink is also usable in pure router-wiring tests.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from textual.app import App

from xenopus.runtime.notifications import Notification, Sink

DEFAULT_BUFFER_CAPACITY = 1000


class TextualSink(Sink):
    """Renders router-approved notifications as Textual toasts."""

    def __init__(self, *, capacity: int = DEFAULT_BUFFER_CAPACITY) -> None:
        if capacity < 1:
            msg = "capacity must be >= 1"
            raise ValueError(msg)
        # App is generic-invariant in its return type; any App instance
        # is accepted — only notify() (non-generic) is ever called.
        self._app: App[Any] | None = None
        self._buffer: deque[str] = deque(maxlen=capacity)
        self._delivered: deque[Notification] = deque(maxlen=capacity)

    def attach(self, app: App[Any]) -> None:
        """Bind a live app; buffered lines flush into it on attach."""
        self._app = app
        for line in list(self._buffer):
            self._toast(line)

    def detach(self) -> None:
        """Unbind the app (shutdown path); deliveries buffer again."""
        self._app = None

    def deliver(self, notification: Notification) -> None:
        """Render one router-approved notification."""
        line = (
            f"[{notification.priority.value}] {notification.event_type}: "
            f"{notification.message} ({notification.correlation_id})"
        )
        self._delivered.append(notification)
        if self._app is None:
            self._buffer.append(line)
            return
        self._toast(line)

    @property
    def delivered(self) -> list[Notification]:
        """Delivered notifications, oldest first (test/inspection hook)."""
        return list(self._delivered)

    @property
    def buffered_lines(self) -> list[str]:
        """Lines buffered while no app is attached (test/inspection hook)."""
        return list(self._buffer)

    def _toast(self, line: str) -> None:
        """Emit one toast; markup disabled (lines carry task titles)."""
        app = self._app
        if app is None:  # detached between check and call — buffer instead
            self._buffer.append(line)
            return
        # markup=False: notification text may embed task titles etc.
        # Rich markup in those strings would be an injection vector.
        app.notify(line, severity="warning", markup=False)
