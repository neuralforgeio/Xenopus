"""Telegram channel adapter: raw Bot API over the httpx seam (ADR-023).

Three pieces, all consuming EXISTING engines — no new runtime logic:

- ``TelegramClient``: the HTTP face (sendMessage/getMe/getUpdates)
  with explicit timeouts, 429 Retry-After respect, and a redacted
  token discipline (never logged, never in error text).
- ``TelegramSink``: the SAME ``Sink`` interface the router already
  governs — outbound delivery is policy-decided BEFORE this sink is
  called (addendum 87 parity with stdout/web/TUI sinks).
- ``TelegramCommander``: inbound long-polling with an allow-listed
  command table and a chat allow-list; inbound never executes tools
  and never bypasses the approval engines (ADR-023).
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

import httpx

from xenopus.observability.correlation import new_correlation_id
from xenopus.persistence.approvals_store import ApprovalStore
from xenopus.persistence.journal import EventJournal
from xenopus.persistence.tasks import TaskError, TaskStore
from xenopus.runtime.events import Event, EventType
from xenopus.runtime.killswitch import KillSwitch
from xenopus.runtime.notifications import Notification, Sink

TOKEN_ENV_VAR = "XENOPUS_TELEGRAM_TOKEN"  # noqa: S105 - env var NAME, not a secret
CHAT_IDS_ENV_VAR = "XENOPUS_TELEGRAM_CHAT_IDS"
API_BASE = "https://api.telegram.org"
DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_POLL_TIMEOUT_SECONDS = 25.0
MAX_MESSAGE_LENGTH = 4096
SLOW_DOWN_CEILING_SECONDS = 30.0


class TelegramError(Exception):
    """Raised for Telegram transport failures (token/redaction-safe)."""


def load_token() -> str | None:
    """Bot token from the environment, or None (never a default)."""
    token = os.environ.get(TOKEN_ENV_VAR)
    if token is not None and not token.strip():
        return None
    return token.strip() if token else None


def load_chat_ids() -> frozenset[str]:
    """Allowed chat ids from the environment (fail-closed default: none)."""
    raw = os.environ.get(CHAT_IDS_ENV_VAR, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


class TelegramClient:
    """Raw Bot API over httpx (ADR-023) — the only network face.

    Contract:
        send_message()/get_me()/get_updates(): one JSON call each,
        with explicit timeouts; 429 responses wait up to the server's
        Retry-After (bounded) once, then surface TelegramError — the
        router owns retry semantics, never a loop here.

    Failure modes:
        TelegramError on transport/HTTP/API failure. The token is
        never embedded in any raised message (redaction discipline).
    """

    def __init__(
        self,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        poll_timeout_seconds: float = DEFAULT_POLL_TIMEOUT_SECONDS,
    ) -> None:
        if not token.strip():
            msg = "telegram token must be non-empty"
            raise TelegramError(msg)
        self._token = token.strip()
        self._poll_timeout = poll_timeout_seconds
        self._http = httpx.AsyncClient(
            base_url=f"{API_BASE}/bot{self._token}",
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def get_me(self) -> dict[str, Any]:
        """Call getMe — cheap startup token/identity verification."""
        return await self._call("GET", "/getMe")

    async def send_message(self, chat_id: str, text: str) -> dict[str, Any]:
        """Send one text message (length-clamped to the API limit)."""
        return await self._call(
            "POST",
            "/sendMessage",
            json={"chat_id": chat_id, "text": text[:MAX_MESSAGE_LENGTH]},
        )

    async def get_updates(self, *, offset: int | None) -> list[dict[str, Any]]:
        """Long-poll for updates (offset advances the confirmation cursor)."""
        params: dict[str, Any] = {"timeout": int(self._poll_timeout)}
        if offset is not None:
            params["offset"] = offset
        body = await self._call(
            "GET",
            "/getUpdates",
            params=params,
            timeout=httpx.Timeout(self._poll_timeout + DEFAULT_TIMEOUT_SECONDS),
        )
        updates = body.get("result", [])
        return [u for u in updates if isinstance(u, dict)]

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> dict[str, Any]:
        """One API call; 429 waits bounded-once; errors are token-free."""
        try:
            response = await self._http.request(
                method, path, json=json, params=params, timeout=timeout
            )
        except httpx.HTTPError as err:
            msg = f"telegram transport failure: {type(err).__name__}"
            raise TelegramError(msg) from err
        if response.status_code == 429:
            await self._respect_retry_after(response)
            raise TelegramError("telegram rate limited (429) after bounded wait")
        if response.status_code != 200:
            raise TelegramError(f"telegram API error: HTTP {response.status_code}")
        try:
            body: dict[str, Any] = response.json()
        except ValueError as err:
            msg = "telegram API returned a non-JSON body"
            raise TelegramError(msg) from err
        if not body.get("ok", False):
            # description may echo request data, but never the token
            # (the token lives in the URL only, which we never include).
            raise TelegramError(f"telegram API error: {body.get('description', 'unknown')}")
        return body

    async def _respect_retry_after(self, response: httpx.Response) -> None:
        """Honor 429 Retry-After once, bounded by a ceiling (ADR-023)."""
        try:
            wait = int(response.headers.get("Retry-After", "0"))
        except ValueError:
            wait = 0
        wait = min(max(wait, 0), int(SLOW_DOWN_CEILING_SECONDS))
        if wait > 0:
            await asyncio.sleep(wait)


@dataclass(frozen=True, slots=True)
class TelegramSinkConfig:
    """Delivery target for the sink (chat allow-list + journal)."""

    client: TelegramClient
    journal: EventJournal | None = None
    chat_ids: frozenset[str] = frozenset()

    async def deliver(self, chat_id: str, text: str) -> None:
        """Send and journal the outcome (evidence for both paths)."""
        try:
            await self.client.send_message(chat_id, text)
        except TelegramError as err:
            if self.journal is not None:
                self.journal.append(
                    Event(
                        type=EventType.NOTIFICATION_SUPPRESSED,
                        correlation_id="telegram",
                        payload={"error": str(err), "chat_id": chat_id},
                    )
                )
            return
        if self.journal is not None:
            self.journal.append(
                Event(
                    type=EventType.NOTIFICATION_DELIVERED,
                    correlation_id="telegram",
                    payload={"chat_id": chat_id},
                )
            )


class TelegramSink(Sink):
    """Router-governed Telegram delivery (ADR-023, addendum 87).

    The NotificationRouter decides priorities, quiet hours, digests,
    and rate limits BEFORE calling ``deliver`` — this sink only
    renders approved notifications into each allowed chat. Sends are
    scheduled on the running loop and referenced until done (a bare
    create_task could be garbage-collected mid-send); outcomes are
    journaled by the config's deliver path, never swallowed.
    """

    def __init__(self, config: TelegramSinkConfig) -> None:
        self._config = config
        self._pending: set[asyncio.Task[None]] = set()

    def deliver(self, notification: Notification) -> None:
        """Render one approved notification into every allowed chat."""
        line = (
            f"[{notification.priority.value}] {notification.event_type}: "
            f"{notification.message} ({notification.correlation_id})"
        )
        loop = asyncio.get_running_loop()
        for chat_id in sorted(self._config.chat_ids):
            task = loop.create_task(self._config.deliver(chat_id, line))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)


COMMANDS_HELP = (
    "commands: /start /tasks /task <id> /new <title> "
    "/approvals /grant <id> /deny <id> /killswitch /confirm"
)


class TelegramCommander:
    """Inbound allow-listed command surface over long polling.

    Contract:
        poll_once(): one getUpdates round; dispatches ONLY the
        allow-listed commands to the EXISTING engines; only
        allow-listed chats are served (others are silently dropped —
        fail-closed, no oracle for strangers).

    Invariants:
        inbound NEVER executes tools; grant/deny go through the SAME
        persistent ApprovalStore as every other surface; killswitch
        requires the explicit /confirm two-step.
    """

    def __init__(
        self,
        *,
        client: TelegramClient,
        store: TaskStore,
        approvals: ApprovalStore,
        journal: EventJournal,
        killswitch: KillSwitch,
        chat_ids: frozenset[str],
    ) -> None:
        self._client = client
        self._store = store
        self._approvals = approvals
        self._journal = journal
        self._killswitch = killswitch
        self._chat_ids = chat_ids
        self._offset: int | None = None
        self._pending_killswitch: str | None = None

    async def poll_once(self) -> list[str]:
        """One polling round; returns the commands executed."""
        try:
            updates = await self._client.get_updates(offset=self._offset)
        except TelegramError as err:
            self._journal.append(
                Event(
                    type=EventType.NOTIFICATION_SUPPRESSED,
                    correlation_id="telegram",
                    payload={"poll_error": str(err)},
                )
            )
            return []
        executed: list[str] = []
        for update in updates:
            self._advance_offset(update)
            chat_id, text = self._extract(update)
            if chat_id is None or text is None:
                continue
            if self._chat_ids and chat_id not in self._chat_ids:
                continue  # fail-closed: unlisted chats get no response
            reply = self._dispatch(chat_id, text)
            if reply is not None:
                await self._reply(chat_id, reply)
            executed.append(text)
        return executed

    def _advance_offset(self, update: dict[str, Any]) -> None:
        """Move the confirmation cursor past this update."""
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            self._offset = update_id + 1

    @staticmethod
    def _extract(update: dict[str, Any]) -> tuple[str | None, str | None]:
        """Pull (chat_id, text) from an update; None when not a message."""
        message = update.get("message")
        if not isinstance(message, dict):
            return None, None
        chat = message.get("chat")
        text = message.get("text")
        if not isinstance(chat, dict) or not isinstance(text, str):
            return None, None
        chat_id = chat.get("id")
        if not isinstance(chat_id, int):
            return None, None
        return (str(chat_id), text)

    def _dispatch(self, chat_id: str, text: str) -> str | None:
        """Map one allow-listed command to its engine call + reply."""
        parts = text.strip().split(maxsplit=1)
        command = parts[0].lower() if parts else ""
        argument = parts[1].strip() if len(parts) > 1 else ""

        if command == "/start":
            return f"xenopus online.\n{COMMANDS_HELP}"
        if command == "/tasks":
            records = self._store.list_tasks(limit=10)
            if not records:
                return "no tasks"
            return "\n".join(f"{r.task_id}  {r.state.value:<10}  {r.title}" for r in records)
        if command == "/task":
            if not argument:
                return "usage: /task <id>"
            try:
                record = self._store.get(argument)
            except TaskError:
                return f"unknown task: {argument}"
            return (
                f"{record.task_id}\nstate: {record.state.value}\n"
                f"attempt: {record.attempt}/{record.max_attempts}\n{record.title}"
            )
        if command == "/new":
            if not argument:
                return "usage: /new <title>"
            record = self._store.create(argument, correlation_id=new_correlation_id())
            return f"created: {record.task_id}"
        if command == "/approvals":
            rows = self._approvals.list_pending()
            if not rows:
                return "no pending approvals"
            return "\n".join(
                f"{r.request_id}: {r.tool} on {r.subject} (expires {r.expires_at})" for r in rows
            )
        if command in ("/grant", "/deny"):
            if not argument:
                return f"usage: {command} <id>"
            try:
                if command == "/grant":
                    row = self._approvals.grant(argument)
                else:
                    row = self._approvals.deny(argument)
            except Exception as err:  # surfaced to the operator chat
                return f"error: {err}"
            return f"{argument}: {row.status}"
        if command == "/killswitch":
            self._pending_killswitch = chat_id
            return "cancel ALL live tasks? reply /confirm to execute"
        if command == "/confirm":
            if self._pending_killswitch != chat_id:
                return "nothing to confirm"
            self._pending_killswitch = None
            report = self._killswitch.trigger("telegram killswitch command")
            return (
                f"killswitch: {len(report.cancelled)} task(s) cancelled ({report.correlation_id})"
            )
        return COMMANDS_HELP

    async def _reply(self, chat_id: str, text: str) -> None:
        """Best-effort reply send; failures journal via sink-config path."""
        try:
            await self._client.send_message(chat_id, text)
        except TelegramError as err:
            self._journal.append(
                Event(
                    type=EventType.NOTIFICATION_SUPPRESSED,
                    correlation_id="telegram",
                    payload={"reply_error": str(err), "chat_id": chat_id},
                )
            )
