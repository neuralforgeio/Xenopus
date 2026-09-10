"""Web notification sink — renders router-approved events to a feed.

The SAME NotificationRouter policy governs web delivery as everywhere
else (addendum 87): priorities, quiet hours, digests, and rate limits
are decided by the router BEFORE this sink is called. The sink only
records what the router already approved — the web UI can never
become a policy bypass. The feed is an in-memory bounded ring
(local-first surface; persistence lives in the journal, which the
router already writes).
"""

from __future__ import annotations

from collections import deque

from xenopus.runtime.notifications import Notification, Sink

DEFAULT_FEED_CAPACITY = 200


class WebSink(Sink):
    """Bounded recent-notification feed for the dashboard."""

    def __init__(self, *, capacity: int = DEFAULT_FEED_CAPACITY) -> None:
        if capacity < 1:
            msg = "capacity must be >= 1"
            raise ValueError(msg)
        self._feed: deque[Notification] = deque(maxlen=capacity)

    def deliver(self, notification: Notification) -> None:
        """Record one router-approved notification."""
        self._feed.append(notification)

    @property
    def recent(self) -> list[Notification]:
        """Newest-first feed snapshot (dashboard rendering source)."""
        return list(reversed(self._feed))

    @property
    def delivered_count(self) -> int:
        """Total deliveries recorded (capacity-bounded memory, exact while small)."""
        return len(self._feed)
