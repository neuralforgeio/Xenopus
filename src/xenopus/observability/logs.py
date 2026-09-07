"""Structured logging with mandatory secret redaction.

Levels follow Protocol v9 12.1 (DEBUG/INFO/WARN/ERROR). Every record is
a JSON object with timestamp (UTC ISO-8601), level, message, service,
and correlation id. Redaction strips credential-shaped values BEFORE
formatting — secrets must never reach sinks (master prompt 101/140).
"""

from __future__ import annotations

import json
import re
import sys
from contextlib import suppress
from datetime import UTC, datetime
from enum import StrEnum
from io import TextIOBase
from typing import Any

SERVICE_NAME = "xenopus"

# Credential shapes that must never reach a sink.
_SECRET_PATTERNS = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{16,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)(api[_-]?key|password|secret|token)\s*[:=]\s*\S+"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_REDACTED = "[REDACTED]"


def redact(value: Any) -> Any:
    """Recursively redact credential-shaped content in any JSON value.

    Strings matching known secret shapes are replaced entirely; nested
    dicts/lists are walked. This runs before serialization so no sink
    (file, stdout, future remote) can leak.
    """
    if isinstance(value, str):
        for pattern in _SECRET_PATTERNS:
            if pattern.search(value):
                return _REDACTED
        return value
    if isinstance(value, dict):
        return {k: _REDACTED if _looks_secret(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _looks_secret(key: str) -> bool:
    """True when a dict key itself marks the value as sensitive."""
    return bool(re.search(r"(?i)(key|password|secret|token|credential)", key))


class LogLevel(StrEnum):
    """Log levels per Protocol v9 12.1."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"


_LEVEL_RANK = {LogLevel.DEBUG: 0, LogLevel.INFO: 1, LogLevel.WARN: 2, LogLevel.ERROR: 3}


class StructuredLogger:
    """JSON-lines structured logger with redaction and level filtering.

    Contract:
        log(): writes one JSON object per line to the sink, with
        timestamp, level, service, correlation_id, message, and extra
        fields — all redacted.
        minimum_level: records below it are dropped (DEBUG off in prod).

    Failure modes: never raises on logging — a broken sink degrades to
    stderr (logging must not take the runtime down).
    """

    def __init__(
        self,
        *,
        sink: TextIOBase | None = None,
        minimum_level: LogLevel = LogLevel.INFO,
        service: str = SERVICE_NAME,
    ) -> None:
        self._sink = sink or sys.stdout
        self._minimum = _LEVEL_RANK[minimum_level]
        self._service = service

    def log(
        self,
        level: LogLevel,
        message: str,
        *,
        correlation_id: str = "",
        **extra: Any,
    ) -> dict[str, Any]:
        """Emit one redacted JSON record; returns the record (testable)."""
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level.value,
            "service": self._service,
            "correlation_id": correlation_id,
            "message": message,
        }
        record.update(redact(extra))
        record = redact(record)  # message + top-level redaction pass
        if _LEVEL_RANK[level] >= self._minimum:
            try:
                self._sink.write(json.dumps(record) + "\n")
            except OSError:
                with suppress(OSError):
                    sys.stderr.write(json.dumps(record) + "\n")
                # last resort: drop silently — logging must never crash the runtime
        return dict(record)

    def debug(self, message: str, **extra: Any) -> dict[str, Any]:
        """DEBUG-level record."""
        return self.log(LogLevel.DEBUG, message, **extra)

    def info(self, message: str, **extra: Any) -> dict[str, Any]:
        """INFO-level record."""
        return self.log(LogLevel.INFO, message, **extra)

    def warn(self, message: str, **extra: Any) -> dict[str, Any]:
        """WARN-level record."""
        return self.log(LogLevel.WARN, message, **extra)

    def error(self, message: str, **extra: Any) -> dict[str, Any]:
        """ERROR-level record."""
        return self.log(LogLevel.ERROR, message, **extra)
