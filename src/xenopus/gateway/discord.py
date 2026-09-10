"""Discord channel adapter: raw REST outbound + py-cord inbound (ADR-024).

Three pieces, all consuming EXISTING engines — no new runtime logic:

- ``DiscordClient``: the outbound HTTP face (POST channel messages,
  GET current bot user) over the hardened httpx seam, with the token
  in the Authorization header (never the URL), explicit timeouts,
  and Discord 429s mapped into ONE bounded wait.
- ``DiscordSink``: the SAME ``Sink`` interface the router already
  governs — outbound delivery is policy-decided BEFORE this sink is
  called (addendum 87 parity with stdout/web/TUI/Telegram sinks).
- ``DiscordCommander``: the inbound command table (allow-listed,
  ``!``-prefixed) wired to the same engines as Telegram's; the
  py-cord gateway client feeds it message events. Inbound never
  executes tools and never bypasses the approval engines.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
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

TOKEN_ENV_VAR = "XENOPUS_DISCORD_TOKEN"  # noqa: S105 - env var NAME, not a secret
CHANNEL_IDS_ENV_VAR = "XENOPUS_DISCORD_CHANNEL_IDS"
GUILD_IDS_ENV_VAR = "XENOPUS_DISCORD_GUILD_IDS"
API_BASE = "https://discord.com/api/v10"
DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_MESSAGE_LENGTH = 2000
RATE_LIMIT_CEILING_SECONDS = 30.0


class DiscordError(Exception):
    """Raised for Discord transport failures (token/redaction-safe)."""


def load_token() -> str | None:
    """Bot token from the environment, or None (never a default)."""
    token = os.environ.get(TOKEN_ENV_VAR)
    if token is not None and not token.strip():
        return None
    return token.strip() if token else None


def load_channel_ids() -> frozenset[str]:
    """Outbound channel ids from the environment (fail-closed: none)."""
    raw = os.environ.get(CHANNEL_IDS_ENV_VAR, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def load_guild_ids() -> frozenset[str]:
    """Inbound guild allow-list from the environment (empty = all)."""
    raw = os.environ.get(GUILD_IDS_ENV_VAR, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


class DiscordClient:
    """Raw Discord REST over httpx (ADR-024) — the outbound face.

    Contract:
        send_message()/get_me(): one JSON call each, with explicit
        timeouts; the token travels ONLY in the Authorization header.
        A 429 waits up to the body's retry_after (bounded once),
        then surfaces DiscordError — the router owns retry
        semantics, never a loop here.

    Failure modes:
        DiscordError on transport/HTTP/API failure. The token is
        never embedded in any raised message (redaction discipline).
    """

    def __init__(
        self,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        if not token.strip():
            msg = "discord token must be non-empty"
            raise DiscordError(msg)
        self._token = token.strip()
        self._http = httpx.AsyncClient(
            base_url=API_BASE,
            headers={"Authorization": f"Bot {self._token}"},
            timeout=httpx.Timeout(timeout_seconds),
            transport=transport,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()

    async def get_me(self) -> dict[str, Any]:
        """Fetch the current bot user — startup identity verification."""
        return await self._call("GET", "/users/@me")

    async def send_message(self, channel_id: str, text: str) -> dict[str, Any]:
        """Send one channel message (length-clamped to the API limit)."""
        return await self._call(
            "POST",
            f"/channels/{channel_id}/messages",
            json={"content": text[:MAX_MESSAGE_LENGTH]},
        )

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One API call; 429 waits bounded-once; errors are token-free."""
        try:
            response = await self._http.request(method, path, json=json)
        except httpx.HTTPError as err:
            msg = f"discord transport failure: {type(err).__name__}"
            raise DiscordError(msg) from err
        if response.status_code == 429:
            await self._respect_retry_after(response)
            raise DiscordError("discord rate limited (429) after bounded wait")
        if response.status_code != 200:
            # The URL embeds the channel id (public data), never the
            # token (header-only) — safe to describe by status alone.
            raise DiscordError(f"discord API error: HTTP {response.status_code}")
        try:
            body: dict[str, Any] = response.json()
        except ValueError as err:
            msg = "discord API returned a non-JSON body"
            raise DiscordError(msg) from err
        if body.get("code") is not None and "message" in body:
            # Discord error payloads echo a code + message; the token
            # (header-only) can never appear in them, but keep the
            # text bounded regardless.
            description = str(body.get("message", "unknown"))[:200]
            raise DiscordError(f"discord API error: {description}")
        return body

    async def _respect_retry_after(self, response: httpx.Response) -> None:
        """Honor 429 retry_after once, bounded by a ceiling (ADR-024)."""
        wait = 0.0
        try:
            body = response.json()
            wait = float(body.get("retry_after", 0))
        except (ValueError, TypeError):
            wait = 0.0
        wait = min(max(wait, 0.0), RATE_LIMIT_CEILING_SECONDS)
        if wait > 0:
            await asyncio.sleep(wait)


@dataclass(frozen=True, slots=True)
class DiscordSinkConfig:
    """Delivery target for the sink (channel allow-list + journal)."""

    client: DiscordClient
    journal: EventJournal | None = None
    channel_ids: frozenset[str] = frozenset()

    async def deliver(self, channel_id: str, text: str) -> None:
        """Send and journal the outcome (evidence for both paths)."""
        try:
            await self.client.send_message(channel_id, text)
        except DiscordError as err:
            if self.journal is not None:
                self.journal.append(
                    Event(
                        type=EventType.NOTIFICATION_SUPPRESSED,
                        correlation_id="discord",
                        payload={"error": str(err), "channel_id": channel_id},
                    )
                )
            return
        if self.journal is not None:
            self.journal.append(
                Event(
                    type=EventType.NOTIFICATION_DELIVERED,
                    correlation_id="discord",
                    payload={"channel_id": channel_id},
                )
            )


class DiscordSink(Sink):
    """Router-governed Discord delivery (ADR-024, addendum 87).

    The NotificationRouter decides priorities, quiet hours, digests,
    and rate limits BEFORE calling ``deliver`` — this sink only
    renders approved notifications into each allowed channel. Sends
    are scheduled on the running loop and referenced until done (a
    bare create_task could be garbage-collected mid-send); outcomes
    are journaled by the config's deliver path, never swallowed.
    """

    def __init__(self, config: DiscordSinkConfig) -> None:
        self._config = config
        self._pending: set[asyncio.Task[None]] = set()

    def deliver(self, notification: Notification) -> None:
        """Render one approved notification into every allowed channel."""
        line = (
            f"[{notification.priority.value}] {notification.event_type}: "
            f"{notification.message} ({notification.correlation_id})"
        )
        loop = asyncio.get_running_loop()
        for channel_id in sorted(self._config.channel_ids):
            task = loop.create_task(self._config.deliver(channel_id, line))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)


COMMANDS_HELP = (
    "commands: !start !tasks !task <id> !new <title> "
    "!approvals !grant <id> !deny <id> !killswitch !confirm"
)


class DiscordCommander:
    """Inbound allow-listed command surface (ADR-024).

    Contract:
        handle_message(): one inbound message event from the py-cord
        gateway; dispatches ONLY the allow-listed ``!``-commands to
        the EXISTING engines. Unlisted channels are silently dropped
        (fail-closed); an optional guild allow-list narrows further.

    Invariants:
        inbound NEVER executes tools; grant/deny go through the SAME
        persistent ApprovalStore as every other surface; killswitch
        requires the explicit !confirm two-step.
    """

    def __init__(
        self,
        *,
        store: TaskStore,
        approvals: ApprovalStore,
        journal: EventJournal,
        killswitch: KillSwitch,
        channel_ids: frozenset[str],
        guild_ids: frozenset[str] = frozenset(),
        responder: DiscordResponder | None = None,
    ) -> None:
        self._store = store
        self._approvals = approvals
        self._journal = journal
        self._killswitch = killswitch
        self._channel_ids = channel_ids
        self._guild_ids = guild_ids
        self._responder = responder
        self._pending_killswitch: str | None = None

    async def handle_message(
        self,
        *,
        channel_id: str,
        guild_id: str | None,
        text: str,
    ) -> str | None:
        """One inbound event; returns the reply text, or None when
        the message is dropped (not a command, or unlisted source)."""
        if self._channel_ids and channel_id not in self._channel_ids:
            return None  # fail-closed: unlisted channels get no response
        if self._guild_ids and (guild_id is None or guild_id not in self._guild_ids):
            return None  # optional guild allow-list narrows further
        reply = self._dispatch(channel_id, text)
        if reply is not None and self._responder is not None:
            await self._responder.reply(channel_id, reply)
        return reply

    def _dispatch(self, channel_id: str, text: str) -> str | None:
        """Map one allow-listed command to its engine call + reply."""
        parts = text.strip().split(maxsplit=1)
        command = parts[0].lower() if parts else ""
        argument = parts[1].strip() if len(parts) > 1 else ""
        if not command.startswith("!"):
            return None  # not a command — ignore silently

        if command == "!start":
            return f"xenopus online.\n{COMMANDS_HELP}"
        if command == "!tasks":
            records = self._store.list_tasks(limit=10)
            if not records:
                return "no tasks"
            return "\n".join(f"{r.task_id}  {r.state.value:<10}  {r.title}" for r in records)
        if command == "!task":
            if not argument:
                return "usage: !task <id>"
            try:
                record = self._store.get(argument)
            except TaskError:
                return f"unknown task: {argument}"
            return (
                f"{record.task_id}\nstate: {record.state.value}\n"
                f"attempt: {record.attempt}/{record.max_attempts}\n{record.title}"
            )
        if command == "!new":
            if not argument:
                return "usage: !new <title>"
            record = self._store.create(argument, correlation_id=new_correlation_id())
            return f"created: {record.task_id}"
        if command == "!approvals":
            rows = self._approvals.list_pending()
            if not rows:
                return "no pending approvals"
            return "\n".join(
                f"{r.request_id}: {r.tool} on {r.subject} (expires {r.expires_at})" for r in rows
            )
        if command in ("!grant", "!deny"):
            if not argument:
                return f"usage: {command} <id>"
            try:
                if command == "!grant":
                    row = self._approvals.grant(argument)
                else:
                    row = self._approvals.deny(argument)
            except Exception as err:  # surfaced to the operator channel
                return f"error: {err}"
            return f"{argument}: {row.status}"
        if command == "!killswitch":
            self._pending_killswitch = channel_id
            return "cancel ALL live tasks? reply !confirm to execute"
        if command == "!confirm":
            if self._pending_killswitch != channel_id:
                return "nothing to confirm"
            self._pending_killswitch = None
            report = self._killswitch.trigger("discord killswitch command")
            return (
                f"killswitch: {len(report.cancelled)} task(s) cancelled ({report.correlation_id})"
            )
        return COMMANDS_HELP


class DiscordResponder:
    """Reply interface the commander needs (implemented by the gateway host)."""

    async def reply(self, channel_id: str, text: str) -> None:
        """Send one reply; failures are journaled by the host."""
        raise NotImplementedError


class RestResponder(DiscordResponder):
    """Replies via the raw REST client (outbound seam reuse)."""

    def __init__(self, client: DiscordClient, journal: EventJournal) -> None:
        self._client = client
        self._journal = journal

    async def reply(self, channel_id: str, text: str) -> None:
        """Best-effort reply send; failures journal (never raised)."""
        try:
            await self._client.send_message(channel_id, text)
        except DiscordError as err:
            self._journal.append(
                Event(
                    type=EventType.NOTIFICATION_SUPPRESSED,
                    correlation_id="discord",
                    payload={"reply_error": str(err), "channel_id": channel_id},
                )
            )


class DiscordGatewayHost:
    """py-cord gateway host feeding the commander (ADR-024 inbound).

    A thin ``discord.Client`` wiring on_message events into
    ``DiscordCommander.handle_message``; the bot's own message
    loopback is ignored. Scheduler ticks ride the same loop so one
    process hosts the full channel (same duty as the Telegram loop).

    The gateway runs in a background task; ``run()`` drives the
    scheduler tick + REST heartbeat loop until cancelled, mirroring
    the Telegram host's single-process duty cycle.
    """

    def __init__(
        self,
        *,
        token: str,
        commander: DiscordCommander,
        journal: EventJournal,
        scheduler_tick: Callable[[], object] | None = None,
        scheduler_interval_seconds: float = 1.0,
    ) -> None:
        self._token = token
        self._commander = commander
        self._journal = journal
        self._scheduler_tick = scheduler_tick
        self._scheduler_interval = scheduler_interval_seconds

    async def run(self) -> None:
        """Host duty cycle: scheduler ticks while the gateway runs."""
        gateway = asyncio.create_task(self._run_gateway())
        try:
            while not gateway.done():
                if self._scheduler_tick is not None:
                    self._scheduler_tick()
                await asyncio.sleep(self._scheduler_interval)
        finally:
            gateway.cancel()
            await asyncio.gather(gateway, return_exceptions=True)

    async def _run_gateway(self) -> None:
        """Bridge py-cord on_message into the commander (single import site)."""
        import discord

        intents = discord.Intents.default()
        intents.message_content = True  # bot-portal prerequisite (README)
        client = discord.Client(intents=intents)

        @client.event
        async def on_message(message: discord.Message) -> None:
            if message.author == client.user:
                return  # never react to our own replies
            await self._commander.handle_message(
                channel_id=str(message.channel.id),
                guild_id=str(message.guild.id) if message.guild else None,
                text=message.content or "",
            )

        await client.start(self._token)
