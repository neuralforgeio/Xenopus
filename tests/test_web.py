"""Web dashboard tests: ASGI integration flows, CSRF, sink, XSS escape."""

import asyncio
import re
import time
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from xenopus.persistence.approvals_store import ApprovalStore, persistent_action_hash
from xenopus.persistence.journal import EventJournal
from xenopus.persistence.tasks import TaskStore
from xenopus.runtime.agent import AgentContract, AgentProfile
from xenopus.runtime.agent_pool import AgentPool
from xenopus.runtime.events import EventType
from xenopus.runtime.notifications import (
    NotificationRouter,
    Priority,
    SubscriberPolicy,
)
from xenopus.runtime.scheduler import Scheduler, once_entry
from xenopus.web.server import WebServices, build_app
from xenopus.web.sink import WebSink


def _profile(role: str = "researcher") -> AgentProfile:
    return AgentProfile(
        role=role,
        allowed_tools=frozenset({"file_read"}),
        forbidden_tools=frozenset(),
        permissions_subject=f"agent:{role}",
    )


class _Bundle:
    """Live engines + built app backing one test client."""

    def __init__(self, tmp_path: Path, *, with_approvals: bool = True) -> None:
        # cross_thread=True: the ASGI test client serves requests from a
        # worker thread (production uvicorn is single-loop; WAL guards
        # concurrent readers either way).
        self.journal = EventJournal(tmp_path / "j.sqlite", cross_thread=True)
        self.store = TaskStore(tmp_path / "t.sqlite", journal=self.journal, cross_thread=True)
        self.scheduler = Scheduler(
            store=self.store,
            journal=self.journal,
            clock=lambda: datetime(2026, 9, 10, 12, 0, tzinfo=UTC),
        )
        self.pool = AgentPool()
        self.sink = WebSink()
        self.router = NotificationRouter(journal=self.journal)
        self.router.subscribe(
            SubscriberPolicy(subscriber="web", digest=True),
            self.sink,
        )
        self.approvals = (
            ApprovalStore(str(tmp_path / "a.sqlite"), cross_thread=True) if with_approvals else None
        )
        self.app = build_app(self.services())

    def services(self) -> WebServices:
        return WebServices(
            store=self.store,
            journal=self.journal,
            scheduler=self.scheduler,
            pool=self.pool,
            router=self.router,
            sink=self.sink,
            approvals=self.approvals,
            tick_interval_seconds=0.05,
        )

    def close(self) -> None:
        if self.approvals is not None:
            self.approvals.close()
        self.store.close()
        self.journal.close()


@pytest.fixture
def bundle(tmp_path: Path) -> Generator[_Bundle]:
    b = _Bundle(tmp_path)
    yield b
    b.close()


@pytest.fixture
def client(bundle: _Bundle) -> Generator[TestClient]:
    with TestClient(bundle.app) as c:
        yield c


def _csrf_from(client: TestClient) -> str:
    """Extract the CSRF token from the new-task form page."""
    page = client.get("/tasks/new").text
    match = re.search(r"name='csrf' value='([^']+)'", page)
    assert match is not None
    return match.group(1)


class TestSafePages:
    def test_tasks_page_renders_rows(self, bundle: _Bundle, client: TestClient) -> None:
        record = bundle.store.create("web task", correlation_id="c1")
        response = client.get("/")
        assert response.status_code == 200
        assert "web task" in response.text
        assert record.task_id in response.text

    def test_tasks_page_escapes_untrusted_title(self, bundle: _Bundle, client: TestClient) -> None:
        bundle.store.create("<script>alert(1)</script>", correlation_id="c2")
        response = client.get("/")
        assert response.status_code == 200
        assert "<script>" not in response.text
        assert "&lt;script&gt;" in response.text

    def test_task_detail_found_and_missing(self, bundle: _Bundle, client: TestClient) -> None:
        record = bundle.store.create("detail me", correlation_id="c3")
        ok = client.get(f"/tasks/{record.task_id}")
        assert ok.status_code == 200
        assert "detail me" in ok.text
        missing = client.get("/tasks/task-doesnotexist")
        assert missing.status_code == 404

    def test_approvals_page_lists_pending(self, bundle: _Bundle, client: TestClient) -> None:
        assert bundle.approvals is not None
        row = bundle.approvals.create(
            tool="file_delete",
            subject="agent:coder",
            action_hash="hash-1",
            reason="cleanup",
        )
        response = client.get("/approvals")
        assert response.status_code == 200
        assert row.request_id in response.text
        assert "file_delete" in response.text

    def test_schedules_page_lists_entries(self, bundle: _Bundle, client: TestClient) -> None:
        entry = once_entry("web heartbeat", at=datetime(2026, 9, 10, 12, 0, tzinfo=UTC))
        bundle.scheduler.add(entry)
        response = client.get("/schedules")
        assert response.status_code == 200
        assert "web heartbeat" in response.text

    def test_agents_page_lists_live_runs(self, bundle: _Bundle, client: TestClient) -> None:
        contract = AgentContract(mission="explore", task_id="task-1")

        async def acquire() -> None:
            await bundle.pool.acquire(contract, _profile())

        asyncio.run(acquire())
        response = client.get("/agents")
        assert response.status_code == 200
        assert "HEALTHY" in response.text
        assert "task-1" in response.text

    def test_agents_page_empty_when_no_runs(self, client: TestClient) -> None:
        response = client.get("/agents")
        assert response.status_code == 200
        assert "(none)" in response.text

    def test_events_page_lists_delivered(self, bundle: _Bundle, client: TestClient) -> None:
        bundle.router.route(
            event_type=EventType.TASK_FAILED,
            message="web critical",
            correlation_id="corr-1",
        )
        response = client.get("/events")
        assert response.status_code == 200
        assert "web critical" in response.text

    def test_security_headers_present(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"


class TestCsrf:
    def test_new_task_without_token_is_forbidden(self, bundle: _Bundle, client: TestClient) -> None:
        response = client.post("/tasks/new", data={"title": "no token"})
        assert response.status_code == 403
        assert bundle.store.list_tasks() == []

    def test_new_task_with_bad_token_is_forbidden(
        self, bundle: _Bundle, client: TestClient
    ) -> None:
        response = client.post("/tasks/new", data={"title": "bad token", "csrf": "wrong"})
        assert response.status_code == 403
        assert bundle.store.list_tasks() == []

    def test_killswitch_without_token_is_forbidden(
        self, bundle: _Bundle, client: TestClient
    ) -> None:
        bundle.store.create("alive", correlation_id="c4")
        response = client.post("/killswitch", data={})
        assert response.status_code == 403
        assert bundle.store.list_tasks()[0].state.value == "QUEUED"

    def test_approval_decision_without_token_is_forbidden(
        self, bundle: _Bundle, client: TestClient
    ) -> None:
        assert bundle.approvals is not None
        row = bundle.approvals.create(
            tool="file_delete",
            subject="agent:coder",
            action_hash="hash-2",
            reason="cleanup",
        )
        response = client.post(f"/approvals/{row.request_id}/grant", data={})
        assert response.status_code == 403
        assert bundle.approvals.get(row.request_id).status == "PENDING"


class TestMutations:
    def test_new_task_with_valid_token_creates(self, bundle: _Bundle, client: TestClient) -> None:
        token = _csrf_from(client)
        response = client.post("/tasks/new", data={"title": "via web", "csrf": token})
        assert response.status_code == 200
        tasks = bundle.store.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].title == "via web"
        assert "created:" in response.text

    def test_new_task_empty_title_rejected(self, bundle: _Bundle, client: TestClient) -> None:
        token = _csrf_from(client)
        response = client.post("/tasks/new", data={"title": "   ", "csrf": token})
        assert response.status_code == 400
        assert bundle.store.list_tasks() == []

    def test_grant_flow(self, bundle: _Bundle, client: TestClient) -> None:
        assert bundle.approvals is not None
        row = bundle.approvals.create(
            tool="file_delete",
            subject="agent:coder",
            action_hash=persistent_action_hash(
                tool="file_delete",
                arguments={"path": "x"},
                subject="agent:coder",
                task_id="task-1",
            ),
            reason="cleanup",
        )
        token = _csrf_from(client)
        response = client.post(f"/approvals/{row.request_id}/grant", data={"csrf": token})
        assert response.status_code == 200
        assert bundle.approvals.get(row.request_id).status == "GRANTED"

    def test_deny_flow(self, bundle: _Bundle, client: TestClient) -> None:
        assert bundle.approvals is not None
        row = bundle.approvals.create(
            tool="file_delete",
            subject="agent:coder",
            action_hash="hash-3",
            reason="cleanup",
        )
        token = _csrf_from(client)
        response = client.post(f"/approvals/{row.request_id}/deny", data={"csrf": token})
        assert response.status_code == 200
        assert bundle.approvals.get(row.request_id).status == "DENIED"

    def test_double_grant_surfaces_error_not_crash(
        self, bundle: _Bundle, client: TestClient
    ) -> None:
        assert bundle.approvals is not None
        row = bundle.approvals.create(
            tool="file_delete",
            subject="agent:coder",
            action_hash="hash-4",
            reason="cleanup",
        )
        token = _csrf_from(client)
        first = client.post(f"/approvals/{row.request_id}/grant", data={"csrf": token})
        assert first.status_code == 200
        second = client.post(f"/approvals/{row.request_id}/grant", data={"csrf": token})
        assert second.status_code == 200
        assert "already" in second.text

    def test_killswitch_confirmed_cancels_all(self, bundle: _Bundle, client: TestClient) -> None:
        bundle.store.create("one", correlation_id="c5")
        bundle.store.create("two", correlation_id="c6")
        form_page = client.get("/killswitch")
        assert form_page.status_code == 200
        assert "2 live task(s)" in form_page.text
        token = _csrf_from(client)
        response = client.post("/killswitch", data={"csrf": token})
        assert response.status_code == 200
        states = [t.state.value for t in bundle.store.list_tasks()]
        assert states == ["CANCELLED", "CANCELLED"]

    def test_unknown_decision_404s(self, bundle: _Bundle, client: TestClient) -> None:
        token = _csrf_from(client)
        response = client.post("/approvals/appr-000001/maybe", data={"csrf": token})
        assert response.status_code == 404


class TestSchedulerHosting:
    def test_lifespan_ticks_overdue_schedule(self, bundle: _Bundle, client: TestClient) -> None:
        """The server lifespan hosts scheduler ticks (ADR-022 duty)."""
        past = datetime(2026, 9, 10, 11, 0, tzinfo=UTC)
        bundle.scheduler.add(once_entry("overdue", at=past))
        deadline = time.monotonic() + 2.0
        fired: list[object] = []
        while time.monotonic() < deadline:
            fired = [t for t in bundle.store.list_tasks() if t.title.startswith("scheduled:")]
            if fired:
                break
            time.sleep(0.05)
        assert len(fired) == 1


class TestWebSink:
    def test_sink_feed_bounded_and_newest_first(self) -> None:
        from xenopus.runtime.notifications import Notification

        sink = WebSink(capacity=3)
        for i in range(5):
            sink.deliver(
                Notification(
                    notification_id=f"ntf-{i}",
                    subscriber="web",
                    priority=Priority.NORMAL,
                    message=f"m{i}",
                    event_type=EventType.TASK_COMPLETED.value,
                    correlation_id=f"corr-{i}",
                    created_at=datetime.now(UTC).isoformat(),
                )
            )
        recent = sink.recent
        assert len(recent) == 3
        assert [n.message for n in recent] == ["m4", "m3", "m2"]

    def test_sink_rejects_bad_capacity(self) -> None:
        with pytest.raises(ValueError, match="capacity"):
            WebSink(capacity=0)

    def test_router_policy_governs_web_delivery(self, bundle: _Bundle) -> None:
        """Digest subscriber: NORMAL held, CRITICAL delivered (addendum 87)."""
        bundle.router.route(
            event_type=EventType.TASK_FAILED,
            message="web crit",
            correlation_id="corr-9",
        )
        bundle.router.route(
            event_type=EventType.TASK_COMPLETED,
            message="web normal",
            correlation_id="corr-10",
        )
        delivered = [n.message for n in bundle.sink.recent]
        assert delivered == ["web crit"]
        flushed = bundle.router.flush_digest("web")
        assert [n.message for n in flushed] == ["web normal"]
