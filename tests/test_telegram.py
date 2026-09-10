"""Telegram channel tests: client transport, sink parity, poller dispatch.

All tests run against an httpx MockTransport — no live network, no
real token. Token discipline is asserted: the token never appears in
raised error text.
"""

from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from xenopus.gateway.telegram import (
    TelegramClient,
    TelegramCommander,
    TelegramError,
    TelegramSink,
    TelegramSinkConfig,
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


def _reply(commander: TelegramCommander, chat: str, text: str) -> str:
    """Dispatch and require a reply (None replies are test failures)."""
    reply = commander._dispatch(chat, text)
    assert reply is not None
    return reply


def _profile_of() -> str:
    return "unused"


class _FakeTransport(httpx.MockTransport):
    """MockTransport capturing requests for assertions."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        super().__init__(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {}})


class _RecordingTransport(httpx.MockTransport):
    """Transport with a scriptable per-path response queue."""

    def __init__(self, script: list[tuple[str, httpx.Response]]) -> None:
        self._script = list(script)
        self.requests: list[httpx.Request] = []
        super().__init__(self._handler)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.rsplit("/", 1)[-1]
        for index, (match_path, response) in enumerate(self._script):
            if match_path == path:
                self._script.pop(index)
                return response
        return httpx.Response(200, json={"ok": True, "result": {}})


def _notification(message: str = "hello") -> Notification:
    return Notification(
        notification_id=f"ntf-{message[:6]}",
        subscriber="telegram",
        priority=Priority.NORMAL,
        message=message,
        event_type=EventType.TASK_COMPLETED.value,
        correlation_id="corr-t",
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


def _client(transport: httpx.AsyncBaseTransport) -> TelegramClient:
    return TelegramClient("123456:TEST-TOKEN-VALUE", transport=transport, timeout_seconds=1)


class TestClient:
    async def test_send_message_posts_json(self) -> None:
        transport = _FakeTransport()
        client = _client(transport)
        await client.send_message("42", "hi there")
        await client.aclose()
        send = next(r for r in transport.requests if "sendMessage" in str(r.url))
        import json

        body = json.loads(send.content.decode())
        assert body == {"chat_id": "42", "text": "hi there"}

    async def test_send_message_clamps_length(self) -> None:
        transport = _FakeTransport()
        client = _client(transport)
        await client.send_message("42", "x" * 5000)
        await client.aclose()
        send = next(r for r in transport.requests if "sendMessage" in str(r.url))
        import json

        body = json.loads(send.content.decode())
        assert len(body["text"]) == 4096

    async def test_token_never_in_error_text(self) -> None:
        # HTTP 500: error text must not embed the token
        transport = _RecordingTransport([("sendMessage", httpx.Response(500, text="server error"))])
        client = _client(transport)
        with pytest.raises(TelegramError) as excinfo:
            await client.send_message("42", "hi")
        await client.aclose()
        assert "123456:TEST-TOKEN-VALUE" not in str(excinfo.value)
        assert "TEST-TOKEN" not in str(excinfo.value)

    async def test_api_not_ok_raises(self) -> None:
        transport = _RecordingTransport(
            [("sendMessage", httpx.Response(200, json={"ok": False, "description": "bad chat"}))]
        )
        client = _client(transport)
        with pytest.raises(TelegramError, match="bad chat"):
            await client.send_message("42", "hi")
        await client.aclose()

    async def test_empty_token_rejected(self) -> None:
        with pytest.raises(TelegramError, match="non-empty"):
            TelegramClient("   ")

    async def test_get_updates_returns_result_list(self) -> None:
        transport = _RecordingTransport(
            [
                (
                    "getUpdates",
                    httpx.Response(
                        200,
                        json={
                            "ok": True,
                            "result": [
                                {"update_id": 7, "message": {"chat": {"id": 1}, "text": "x"}}
                            ],
                        },
                    ),
                )
            ]
        )
        client = _client(transport)
        updates = await client.get_updates(offset=None)
        await client.aclose()
        assert len(updates) == 1
        assert updates[0]["update_id"] == 7


class TestSink:
    async def test_sink_delivers_to_all_allowed_chats(self, stack: _Stack) -> None:
        transport = _FakeTransport()
        client = _client(transport)
        config = TelegramSinkConfig(
            client=client, journal=stack.journal, chat_ids=frozenset({"2", "1"})
        )
        sink = TelegramSink(config)
        sink.deliver(_notification("outbound hello"))
        # allow the created task to run
        import asyncio

        await asyncio.sleep(0.05)
        await client.aclose()
        sends = [r for r in transport.requests if "sendMessage" in str(r.url)]
        assert len(sends) == 2

    async def test_router_policy_governs_delivery(self, stack: _Stack) -> None:
        """Digest subscriber: NORMAL held, CRITICAL delivered (addendum 87)."""
        transport = _FakeTransport()
        client = _client(transport)
        sink = TelegramSink(
            TelegramSinkConfig(client=client, journal=stack.journal, chat_ids=frozenset({"1"}))
        )
        router = NotificationRouter(journal=stack.journal)
        router.subscribe(SubscriberPolicy(subscriber="telegram", digest=True), sink)
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
        assert "crit" in sends[0]["text"]
        assert "quiet-held" not in sends[0]["text"]
        flushed = router.flush_digest("telegram")
        assert [n.message for n in flushed] == ["quiet-held"]


class TestCommander:
    def _commander(
        self,
        stack: _Stack,
        transport: httpx.AsyncBaseTransport,
        chat_ids: frozenset[str] = frozenset({"100"}),
    ) -> TelegramCommander:
        return TelegramCommander(
            client=_client(transport),
            store=stack.store,
            approvals=stack.approvals,
            journal=stack.journal,
            killswitch=stack.killswitch,
            chat_ids=chat_ids,
        )

    @staticmethod
    def _update(update_id: int, chat_id: int, text: str) -> dict[str, Any]:
        return {
            "update_id": update_id,
            "message": {"chat": {"id": chat_id}, "text": text},
        }

    async def test_start_lists_commands(self, stack: _Stack) -> None:
        transport = _RecordingTransport(
            [("getUpdates", httpx.Response(200, json={"ok": True, "result": []}))]
        )
        commander = self._commander(stack, transport)
        # feed /start directly through dispatch (unit level)
        reply = _reply(commander, "100", "/start")
        assert "/tasks" in reply and "/killswitch" in reply

    async def test_new_and_tasks_roundtrip(self, stack: _Stack) -> None:
        commander = self._commander(stack, _FakeTransport())
        created = _reply(commander, "100", "/new research topic")
        assert created.startswith("created: task-")
        listing = _reply(commander, "100", "/tasks")
        assert "research topic" in listing

    async def test_task_detail_and_unknown(self, stack: _Stack) -> None:
        commander = self._commander(stack, _FakeTransport())
        record = stack.store.create("inspect me", correlation_id="c1")
        detail = _reply(commander, "100", f"/task {record.task_id}")
        assert "inspect me" in detail and record.task_id in detail
        missing = _reply(commander, "100", "/task task-nope")
        assert "unknown task" in missing

    async def test_grant_flow_through_engine(self, stack: _Stack) -> None:
        row = stack.approvals.create(
            tool="file_delete",
            subject="agent:coder",
            action_hash="hash-1",
            reason="cleanup",
        )
        commander = self._commander(stack, _FakeTransport())
        listing = _reply(commander, "100", "/approvals")
        assert row.request_id in listing
        granted = _reply(commander, "100", f"/grant {row.request_id}")
        assert granted == f"{row.request_id}: GRANTED"
        assert stack.approvals.get(row.request_id).status == "GRANTED"

    async def test_killswitch_requires_confirm_two_step(self, stack: _Stack) -> None:
        stack.store.create("live", correlation_id="c2")
        commander = self._commander(stack, _FakeTransport())
        first = _reply(commander, "100", "/killswitch")
        assert "confirm" in first.lower()
        assert stack.store.list_tasks()[0].state.value == "QUEUED"  # not yet
        second = _reply(commander, "100", "/confirm")
        assert "cancelled" in second
        assert stack.store.list_tasks()[0].state.value == "CANCELLED"

    async def test_confirm_without_armed_killswitch_is_noop(self, stack: _Stack) -> None:
        commander = self._commander(stack, _FakeTransport())
        reply = _reply(commander, "100", "/confirm")
        assert reply == "nothing to confirm"

    async def test_unknown_command_gets_help(self, stack: _Stack) -> None:
        commander = self._commander(stack, _FakeTransport())
        reply = _reply(commander, "100", "/frobnicate")
        assert reply.startswith("commands:")

    async def test_unlisted_chat_is_ignored(self, stack: _Stack) -> None:
        """Fail-closed: non-allow-listed chats never receive replies."""
        updates = [
            self._update(1, 999, "/tasks"),  # unauthorized chat
            self._update(2, 100, "/tasks"),  # authorized chat
        ]
        script_transport = _RecordingTransport(
            [("getUpdates", httpx.Response(200, json={"ok": True, "result": updates}))]
        )
        commander = TelegramCommander(
            client=_client(script_transport),
            store=stack.store,
            approvals=stack.approvals,
            journal=stack.journal,
            killswitch=stack.killswitch,
            chat_ids=frozenset({"100"}),
        )
        executed = await commander.poll_once()
        assert executed == ["/tasks"]  # only the authorized chat's command ran
        sends = [r for r in script_transport.requests if "sendMessage" in str(r.url)]
        assert len(sends) == 1  # unauthorized chat got no reply

    async def test_offset_advances(self, stack: _Stack) -> None:
        updates = [self._update(10, 100, "/tasks"), self._update(11, 100, "/tasks")]
        transport = _RecordingTransport(
            [("getUpdates", httpx.Response(200, json={"ok": True, "result": updates}))]
        )
        commander = self._commander(stack, transport)
        await commander.poll_once()
        assert commander._offset == 12

    async def test_poll_error_journals_and_continues(self, stack: _Stack) -> None:
        transport = _RecordingTransport([("getUpdates", httpx.Response(500, text="boom"))])
        commander = self._commander(stack, transport)
        executed = await commander.poll_once()  # must not raise
        assert executed == []


class TestTokenEnv:
    def test_load_token_reads_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XENOPUS_TELEGRAM_TOKEN", "  tok-123  ")
        assert load_token() == "tok-123"

    def test_load_token_none_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("XENOPUS_TELEGRAM_TOKEN", raising=False)
        assert load_token() is None

    def test_load_token_none_when_blank(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XENOPUS_TELEGRAM_TOKEN", "   ")
        assert load_token() is None
