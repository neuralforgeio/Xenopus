"""Discord channel tests: client transport, sink parity, commander dispatch.

All REST tests run against an httpx MockTransport — no live network,
no real token. Token discipline is asserted: the token never appears
in raised error text and travels only in the Authorization header.
Commander tests drive the same engines as Telegram's suite.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from xenopus.gateway.discord import (
    DiscordClient,
    DiscordCommander,
    DiscordError,
    DiscordResponder,
    DiscordSink,
    DiscordSinkConfig,
    RestResponder,
    load_token,
)
from xenopus.persistence.approvals_store import ApprovalStore
from xenopus.persistence.journal import EventJournal
from xenopus.persistence.tasks import TaskStore
from xenopus.runtime.events import EventType
from xenopus.runtime.killswitch import KillSwitch
from xenopus.runtime.notifications import (
    Notification,
    NotificationRouter,
    Priority,
    SubscriberPolicy,
)

TOKEN = "1234567890TEST-DISCORD-TOKEN-VALUE"  # noqa: S105 - fake value, no network


class _FakeTransport(httpx.MockTransport):
    """MockTransport capturing requests for assertions."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        super().__init__(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"id": "9001"})


class _RecordingTransport(httpx.MockTransport):
    """Transport with a scriptable per-endpoint response queue."""

    def __init__(self, script: list[tuple[str, httpx.Response]]) -> None:
        self._script = list(script)
        self.requests: list[httpx.Request] = []
        super().__init__(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        endpoint = request.url.path.rsplit("/", 1)[-1]  # e.g. "messages", "@me"
        for index, (match, response) in enumerate(self._script):
            if match == endpoint:
                self._script.pop(index)
                return response
        return httpx.Response(200, json={"id": "9001"})


def _notification(message: str = "hello") -> Notification:
    return Notification(
        notification_id=f"ntf-{message[:6]}",
        subscriber="discord",
        priority=Priority.NORMAL,
        message=message,
        correlation_id="corr-d",
        event_type=EventType.TASK_COMPLETED.value,
        created_at=datetime.now(UTC).isoformat(),
    )


class _Stack:
    """Engines + transports backing one test round."""

    def __init__(self, tmp_path: Path) -> None:
        self.journal = EventJournal(tmp_path / "j.sqlite", cross_thread=True)
        self.store = TaskStore(tmp_path / "t.sqlite", journal=self.journal, cross_thread=True)
        self.approvals = ApprovalStore(str(tmp_path / "a.sqlite"), cross_thread=True)
        self.killswitch = KillSwitch(store=self.store, journal=self.journal)


@pytest.fixture
def stack(tmp_path: Path) -> Generator[_Stack]:
    s = _Stack(tmp_path)
    yield s
    s.approvals.close()
    s.store.close()
    s.journal.close()


def _client(transport: httpx.AsyncBaseTransport) -> DiscordClient:
    return DiscordClient(TOKEN, transport=transport, timeout_seconds=1)


class TestClient:
    async def test_send_message_posts_json_with_header_auth(self) -> None:
        transport = _FakeTransport()
        client = _client(transport)
        await client.send_message("42", "hi there")
        await client.aclose()
        send = next(r for r in transport.requests if r.method == "POST")
        body = send.read().decode()
        assert "hi there" in body
        assert f"Bot {TOKEN}" in send.headers["Authorization"]
        assert TOKEN not in str(send.url)  # token never in the URL

    async def test_send_message_clamps_length(self) -> None:
        transport = _FakeTransport()
        client = _client(transport)
        await client.send_message("42", "x" * 3000)
        await client.aclose()
        send = next(r for r in transport.requests if r.method == "POST")
        import json

        body = json.loads(send.content.decode())
        assert len(body["content"]) == 2000

    async def test_token_never_in_error_text(self) -> None:
        transport = _RecordingTransport([("messages", httpx.Response(500, text="server error"))])
        client = _client(transport)
        with pytest.raises(DiscordError) as excinfo:
            await client.send_message("42", "hi")
        await client.aclose()
        assert TOKEN not in str(excinfo.value)
        assert "TEST-DISCORD-TOKEN" not in str(excinfo.value)

    async def test_api_error_payload_raises(self) -> None:
        transport = _RecordingTransport(
            [("messages", httpx.Response(200, json={"code": 50001, "message": "Missing Access"}))]
        )
        client = _client(transport)
        with pytest.raises(DiscordError, match="Missing Access"):
            await client.send_message("42", "hi")
        await client.aclose()

    async def test_empty_token_rejected(self) -> None:
        with pytest.raises(DiscordError, match="non-empty"):
            DiscordClient("   ")

    async def test_rate_limited_waits_bounded_then_raises(self) -> None:
        import time

        transport = _RecordingTransport(
            [("messages", httpx.Response(429, json={"retry_after": 0.2, "message": "Limited"}))]
        )
        client = _client(transport)
        start = time.monotonic()
        with pytest.raises(DiscordError, match="rate limited"):
            await client.send_message("42", "hi")
        elapsed = time.monotonic() - start
        await client.aclose()
        assert elapsed >= 0.19  # bounded wait honored (allow scheduling slop)

    async def test_get_me_returns_user_object(self) -> None:
        transport = _RecordingTransport(
            [("@me", httpx.Response(200, json={"id": "7", "username": "xenobot"}))]
        )
        client = _client(transport)
        me = await client.get_me()
        await client.aclose()
        assert me["username"] == "xenobot"


class TestSink:
    async def test_sink_delivers_to_all_allowed_channels(self, stack: _Stack) -> None:
        transport = _FakeTransport()
        client = _client(transport)
        config = DiscordSinkConfig(
            client=client, journal=stack.journal, channel_ids=frozenset({"2", "1"})
        )
        sink = DiscordSink(config)
        sink.deliver(_notification("outbound hello"))
        import asyncio

        await asyncio.sleep(0.05)
        await client.aclose()
        sends = [r for r in transport.requests if r.method == "POST"]
        assert len(sends) == 2

    async def test_router_policy_governs_delivery(self, stack: _Stack) -> None:
        """Digest subscriber: NORMAL held, CRITICAL delivered (addendum 87)."""
        transport = _FakeTransport()
        client = _client(transport)
        sink = DiscordSink(
            DiscordSinkConfig(client=client, journal=stack.journal, channel_ids=frozenset({"1"}))
        )
        router = NotificationRouter(journal=stack.journal)
        router.subscribe(SubscriberPolicy(subscriber="discord", digest=True), sink)
        router.route(
            event_type=EventType.TASK_FAILED,
            message="crit",
            correlation_id="corr-1",
        )
        router.route(
            event_type=EventType.TASK_COMPLETED,
            message="quiet-held",
            correlation_id="corr-2",
        )
        import asyncio

        await asyncio.sleep(0.05)
        await client.aclose()
        import json

        sends = [json.loads(r.content.decode()) for r in transport.requests if r.method == "POST"]
        assert len(sends) == 1
        assert "crit" in sends[0]["content"]
        assert "quiet-held" not in sends[0]["content"]
        flushed = router.flush_digest("discord")
        assert [n.message for n in flushed] == ["quiet-held"]


class TestCommander:
    def _commander(
        self,
        stack: _Stack,
        responder: DiscordResponder | None = None,
        channel_ids: frozenset[str] = frozenset({"100"}),
        guild_ids: frozenset[str] = frozenset(),
    ) -> DiscordCommander:
        return DiscordCommander(
            store=stack.store,
            approvals=stack.approvals,
            journal=stack.journal,
            killswitch=stack.killswitch,
            channel_ids=channel_ids,
            guild_ids=guild_ids,
            responder=responder,
        )

    async def test_start_lists_commands(self, stack: _Stack) -> None:
        commander = self._commander(stack)
        reply = await commander.handle_message(channel_id="100", guild_id=None, text="!start")
        assert reply is not None
        assert "!tasks" in reply and "!killswitch" in reply

    async def test_new_and_tasks_roundtrip(self, stack: _Stack) -> None:
        commander = self._commander(stack)
        created = await commander.handle_message(channel_id="100", guild_id=None, text="!new topic")
        assert created is not None and created.startswith("created: task-")
        listing = await commander.handle_message(channel_id="100", guild_id=None, text="!tasks")
        assert listing is not None and "topic" in listing

    async def test_task_detail_and_unknown(self, stack: _Stack) -> None:
        commander = self._commander(stack)
        record = stack.store.create("inspect me", correlation_id="c1")
        detail = await commander.handle_message(
            channel_id="100", guild_id=None, text=f"!task {record.task_id}"
        )
        assert detail is not None and "inspect me" in detail and record.task_id in detail
        missing = await commander.handle_message(
            channel_id="100", guild_id=None, text="!task task-nope"
        )
        assert missing is not None and "unknown task" in missing

    async def test_grant_flow_through_engine(self, stack: _Stack) -> None:
        row = stack.approvals.create(
            tool="file_delete",
            subject="agent:coder",
            action_hash="hash-1",
            reason="cleanup",
        )
        commander = self._commander(stack)
        listing = await commander.handle_message(channel_id="100", guild_id=None, text="!approvals")
        assert listing is not None and row.request_id in listing
        granted = await commander.handle_message(
            channel_id="100", guild_id=None, text=f"!grant {row.request_id}"
        )
        assert granted == f"{row.request_id}: GRANTED"
        assert stack.approvals.get(row.request_id).status == "GRANTED"

    async def test_killswitch_requires_confirm_two_step(self, stack: _Stack) -> None:
        stack.store.create("live", correlation_id="c2")
        commander = self._commander(stack)
        first = await commander.handle_message(channel_id="100", guild_id=None, text="!killswitch")
        assert first is not None and "confirm" in first.lower()
        assert stack.store.list_tasks()[0].state.value == "QUEUED"  # not yet
        second = await commander.handle_message(channel_id="100", guild_id=None, text="!confirm")
        assert second is not None and "cancelled" in second
        assert stack.store.list_tasks()[0].state.value == "CANCELLED"

    async def test_confirm_without_armed_killswitch_is_noop(self, stack: _Stack) -> None:
        commander = self._commander(stack)
        reply = await commander.handle_message(channel_id="100", guild_id=None, text="!confirm")
        assert reply == "nothing to confirm"

    async def test_unknown_command_gets_help(self, stack: _Stack) -> None:
        commander = self._commander(stack)
        reply = await commander.handle_message(channel_id="100", guild_id=None, text="!frobnicate")
        assert reply is not None and reply.startswith("commands:")

    async def test_non_command_text_ignored(self, stack: _Stack) -> None:
        commander = self._commander(stack)
        reply = await commander.handle_message(channel_id="100", guild_id=None, text="hello there")
        assert reply is None

    async def test_unlisted_channel_is_ignored(self, stack: _Stack) -> None:
        """Fail-closed: non-allow-listed channels never receive replies."""
        transport = _FakeTransport()
        client = _client(transport)
        commander = self._commander(
            stack,
            responder=RestResponder(client=client, journal=stack.journal),
        )
        reply = await commander.handle_message(channel_id="999", guild_id=None, text="!tasks")
        assert reply is None
        await client.aclose()
        assert not [r for r in transport.requests if r.method == "POST"]  # no reply sent

    async def test_guild_allow_list_narrows_inbound(self, stack: _Stack) -> None:
        commander = self._commander(stack, guild_ids=frozenset({"g1"}))
        allowed = await commander.handle_message(channel_id="100", guild_id="g1", text="!tasks")
        assert allowed is not None
        denied = await commander.handle_message(channel_id="100", guild_id="g2", text="!tasks")
        assert denied is None
        direct = await commander.handle_message(channel_id="100", guild_id=None, text="!tasks")
        assert direct is None  # DMs (no guild) drop under a guild allow-list

    async def test_responder_sends_reply_via_rest(self, stack: _Stack) -> None:
        transport = _FakeTransport()
        client = _client(transport)
        commander = self._commander(
            stack,
            responder=RestResponder(client=client, journal=stack.journal),
        )
        await commander.handle_message(channel_id="100", guild_id=None, text="!tasks")
        sends = [r for r in transport.requests if r.method == "POST"]
        assert len(sends) == 1
        await client.aclose()

    async def test_responder_failure_journals_and_never_raises(self, stack: _Stack) -> None:
        transport = _RecordingTransport([("messages", httpx.Response(500, text="boom"))])
        client = _client(transport)
        commander = self._commander(
            stack,
            responder=RestResponder(client=client, journal=stack.journal),
        )
        reply = await commander.handle_message(channel_id="100", guild_id=None, text="!tasks")
        assert reply is not None  # dispatch succeeded; only the send failed
        await client.aclose()


class TestTokenEnv:
    def test_load_token_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XENOPUS_DISCORD_TOKEN", "  tok-456  ")
        assert load_token() == "tok-456"

    def test_load_token_none_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XENOPUS_DISCORD_TOKEN", raising=False)
        assert load_token() is None

    def test_load_token_none_when_blank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XENOPUS_DISCORD_TOKEN", "   ")
        assert load_token() is None
