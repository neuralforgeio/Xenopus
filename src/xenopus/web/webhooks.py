"""Inbound webhook surface: HMAC-verified machine events (ADR-025).

Routes mounted on the EXISTING dashboard ASGI app (ADR-022 seam),
only when per-source secrets are configured. Every request is
authenticated (HMAC-SHA256 over the raw body), replay-checked
(timestamp window + signed nonce registry), rate-limited
(per-source bucket), size-capped, and schema-validated before the
ONE allow-listed event mapping runs: ``task.create`` into the SAME
durable TaskStore every other surface uses. Webhooks never execute
tools, never touch approvals, never reach the killswitch — machine
input stays below the operator trust bar.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from xenopus.persistence.journal import EventJournal
from xenopus.persistence.tasks import TaskStore
from xenopus.runtime.events import Event, EventType

SECRETS_ENV_VAR = "XENOPUS_WEBHOOK_SECRETS"  # env var NAME, not a secret
SIGNATURE_HEADER = "xenopus-signature"
TIMESTAMP_HEADER = "xenopus-timestamp"
SIGNATURE_PREFIX = "sha256="
MAX_BODY_BYTES = 64 * 1024
MAX_CLOCK_SKEW_SECONDS = 300.0
RATE_LIMIT_PER_MINUTE = 30
ALLOWED_EVENT_TYPES = frozenset({"task.create"})
NONCE_TTL_SECONDS = 2 * MAX_CLOCK_SKEW_SECONDS


class WebhookError(Exception):
    """Raised internally for rejected inbound attempts (never surfaced raw)."""


@dataclass(frozen=True, slots=True)
class _Verdict:
    """Result of one inbound attempt (for journaling)."""

    accepted: bool
    reason: str


class _NonceRegistry:
    """Per-source seen-nonce set with TTL sweep (replay defense)."""

    def __init__(self, *, ttl_seconds: float = NONCE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._seen: dict[str, dict[str, float]] = defaultdict(dict)

    def fresh(self, source: str, nonce: str, *, now: float) -> bool:
        """Record and check one nonce; True when first-seen."""
        seen = self._seen[source]
        stale = [n for n, at in seen.items() if now - at > self._ttl]
        for n in stale:
            del seen[n]
        if nonce in seen:
            return False
        seen[nonce] = now
        return True


class _RateBucket:
    """Per-source one-minute window counter (bounded work per source)."""

    def __init__(self, *, limit_per_minute: int = RATE_LIMIT_PER_MINUTE) -> None:
        self._limit = limit_per_minute
        self._hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, source: str, *, now: float) -> bool:
        """True when the source is under its per-minute allowance."""
        hits = [t for t in self._hits[source] if now - t < 60.0]
        if len(hits) >= self._limit:
            self._hits[source] = hits
            return False
        hits.append(now)
        self._hits[source] = hits
        return True


@dataclass(slots=True)
class WebhookConfig:
    """Wiring for the webhook surface (all pre-built engines)."""

    store: TaskStore
    journal: EventJournal
    secrets: dict[str, str]
    clock: Any = time.time
    max_clock_skew_seconds: float = MAX_CLOCK_SKEW_SECONDS
    max_body_bytes: int = MAX_BODY_BYTES
    rate_limit_per_minute: int = RATE_LIMIT_PER_MINUTE
    nonces: _NonceRegistry = field(default_factory=_NonceRegistry)
    bucket: _RateBucket | None = None

    def __post_init__(self) -> None:
        if self.bucket is None:
            self.bucket = _RateBucket(limit_per_minute=self.rate_limit_per_minute)


def _parse_secrets(raw: str) -> dict[str, str]:
    """Parse `source:secret,...` pairs; blank values yield no sources."""
    secrets: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        source, secret = pair.split(":", 1)
        source, secret = source.strip(), secret.strip()
        if source and secret:
            secrets[source] = secret
    return secrets


def load_webhook_secrets(env: dict[str, str] | None = None) -> dict[str, str]:
    """Source->secret map from the environment (absent = surface off)."""
    import os

    source = os.environ if env is None else env
    return _parse_secrets(source.get(SECRETS_ENV_VAR, ""))


class WebhookHandlers:
    """Route handlers for /webhooks/{source} (ADR-025 table).

    Order of checks (fail-closed at every step): source known ->
    rate limit -> body size -> HMAC over raw bytes -> timestamp
    window -> schema -> nonce freshness -> event allow-list ->
    TaskStore enqueue. Every outcome journals with a reason.
    """

    def __init__(self, config: WebhookConfig) -> None:
        self._config = config

    async def receive(self, request: Request) -> Response:
        """One inbound attempt; status-code-only responses."""
        source = str(request.path_params["source"])
        verdict = await self._adjudicate(request, source)
        self._journal(source, verdict)
        if not verdict.accepted:
            return self._status(verdict.reason)
        return PlainTextResponse("accepted", status_code=202)

    async def _adjudicate(self, request: Request, source: str) -> _Verdict:
        """Run the full ADR-025 check sequence for one request."""
        cfg = self._config
        now = float(cfg.clock())
        if source not in cfg.secrets:
            return _Verdict(False, "unknown-source")
        bucket = cfg.bucket
        if bucket is None:  # defensive: __post_init__ guarantees a bucket
            return _Verdict(False, "misconfigured")
        if not bucket.allow(source, now=now):
            return _Verdict(False, "rate-limited")
        body = await request.body()
        if len(body) > cfg.max_body_bytes:
            return _Verdict(False, "body-too-large")
        if not self._signature_ok(cfg.secrets[source], request, body):
            return _Verdict(False, "bad-signature")
        if not self._timestamp_ok(request, now, cfg.max_clock_skew_seconds):
            return _Verdict(False, "stale-timestamp")
        try:
            payload = json.loads(body)
        except ValueError:
            return _Verdict(False, "non-json")
        if not isinstance(payload, dict):
            return _Verdict(False, "non-json")
        event_type = payload.get("type")
        nonce = payload.get("id")
        if event_type not in ALLOWED_EVENT_TYPES:
            return _Verdict(False, "event-type-not-allowed")
        if not isinstance(nonce, str) or not nonce:
            return _Verdict(False, "missing-nonce")
        if not cfg.nonces.fresh(source, nonce, now=now):
            return _Verdict(False, "replayed-nonce")
        record = self._create_task(payload, source, nonce)
        if record is None:
            return _Verdict(False, "invalid-payload")
        return _Verdict(True, f"task:{record.task_id}")

    def _create_task(self, payload: dict[str, Any], source: str, nonce: str) -> Any:
        """Map the allow-listed task.create event into the TaskStore."""
        title = payload.get("title")
        if not isinstance(title, str) or not title.strip():
            return None
        record = self._config.store.create(
            title.strip()[:500],
            correlation_id=f"webhook:{source}:{nonce}",
        )
        return record

    @staticmethod
    def _signature_ok(secret: str, request: Request, body: bytes) -> bool:
        """Constant-time HMAC-SHA256 check over the raw body bytes."""
        presented = request.headers.get(SIGNATURE_HEADER, "")
        if not presented.startswith(SIGNATURE_PREFIX):
            return False
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(presented[len(SIGNATURE_PREFIX) :], expected)

    @staticmethod
    def _timestamp_ok(request: Request, now: float, skew: float) -> bool:
        """Timestamp header within the bounded clock-skew window."""
        raw = request.headers.get(TIMESTAMP_HEADER, "")
        try:
            stamped = float(raw)
        except ValueError:
            return False
        return abs(now - stamped) <= skew

    @staticmethod
    def _status(reason: str) -> PlainTextResponse:
        """Map rejection reasons to status codes (no content echo)."""
        table = {
            "unknown-source": 404,
            "rate-limited": 429,
            "body-too-large": 413,
            "bad-signature": 401,
            "stale-timestamp": 401,
            "non-json": 415,
            "missing-nonce": 400,
            "invalid-payload": 400,
            "event-type-not-allowed": 400,
            "replayed-nonce": 409,
        }
        return PlainTextResponse("rejected", status_code=table.get(reason, 400))

    def _journal(self, source: str, verdict: _Verdict) -> None:
        """Evidence for both outcomes (no request content echoed)."""
        self._config.journal.append(
            Event(
                type=(
                    EventType.TASK_CREATED
                    if verdict.accepted
                    else EventType.NOTIFICATION_SUPPRESSED
                ),
                correlation_id=f"webhook:{source}",
                payload={
                    "accepted": verdict.accepted,
                    "reason": verdict.reason,
                },
            )
        )


def webhook_routes(config: WebhookConfig) -> list[Route]:
    """Routes for the webhook surface (mounted only when configured)."""
    handlers = WebhookHandlers(config)
    return [
        Route(
            "/webhooks/{source}",
            handlers.receive,
            methods=["POST"],
            name="webhook-receive",
        )
    ]
