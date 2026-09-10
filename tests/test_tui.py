"""TUI tests: pilot-mode panel flows, sink routing, keyboard-only E2E."""

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from xenopus.persistence.approvals_store import ApprovalStore, persistent_action_hash
from xenopus.persistence.journal import EventJournal
from xenopus.persistence.tasks import TaskStore
from xenopus.runtime.agent import AgentContract, AgentProfile
from xenopus.runtime.agent_pool import AgentPool
from xenopus.runtime.events import EventType
from xenopus.runtime.notifications import (
    Notification,
    NotificationRouter,
    Priority,
    SubscriberPolicy,
)
from xenopus.runtime.scheduler import FireResult, Scheduler, once_entry
from xenopus.tui.app import TuiServices, XenopusApp
from xenopus.tui.sink import TextualSink
from xenopus.tui.views import agent_rows, approval_row, schedule_row, task_row


def _profile(role: str = "researcher") -> AgentProfile:
    return AgentProfile(
        role=role,
        allowed_tools=frozenset({"file_read"}),
        forbidden_tools=frozenset(),
        permissions_subject=f"agent:{role}",
    )


class _Services:
    """Bundle of live engines backing one app instance."""

    def __init__(self, tmp_path: Path, *, with_approvals: bool = True) -> None:
        self.journal = EventJournal(tmp_path / "j.sqlite")
        self.store = TaskStore(tmp_path / "t.sqlite", journal=self.journal)
        self.scheduler = Scheduler(
            store=self.store,
            journal=self.journal,
            clock=lambda: datetime(2026, 9, 9, 12, 0, tzinfo=UTC),
        )
        self.pool = AgentPool()
        self.sink = TextualSink()
        self.router = NotificationRouter(journal=self.journal)
        self.router.subscribe(
            SubscriberPolicy(subscriber="tui", digest=True),
            self.sink,
        )
        self.approvals = ApprovalStore(str(tmp_path / "a.sqlite")) if with_approvals else None

    def services(self, *, refresh_seconds: float = 0.05) -> TuiServices:
        return TuiServices(
            store=self.store,
            journal=self.journal,
            scheduler=self.scheduler,
            pool=self.pool,
            router=self.router,
            sink=self.sink,
            approvals=self.approvals,
            refresh_seconds=refresh_seconds,
        )

    def close(self) -> None:
        if self.approvals is not None:
            self.approvals.close()
        self.store.close()
        self.journal.close()


@pytest.fixture
def services(tmp_path: Path) -> Generator[_Services]:
    bundle = _Services(tmp_path)
    yield bundle
    bundle.close()


@pytest.fixture
def app(services: _Services) -> XenopusApp:
    return XenopusApp(services.services())


class TestSink:
    def test_sink_buffers_before_attach(self) -> None:
        sink = TextualSink()
        notification = Notification(
            notification_id="ntf-1",
            subscriber="tui",
            priority=Priority.NORMAL,
            message="hello",
            event_type=EventType.TASK_COMPLETED.value,
            correlation_id="corr-1",
            created_at=datetime.now(UTC).isoformat(),
        )
        sink.deliver(notification)
        assert sink.buffered_lines == ["[NORMAL] TASK_COMPLETED: hello (corr-1)"]
        assert [n.notification_id for n in sink.delivered] == ["ntf-1"]

    def test_sink_rejects_bad_capacity(self) -> None:
        with pytest.raises(ValueError, match="capacity"):
            TextualSink(capacity=0)

    async def test_router_policy_governs_sink_delivery(self, services: _Services) -> None:
        """Digest subscriber: NORMAL held, CRITICAL delivered (addendum 87)."""
        services.router.route(
            event_type=EventType.TASK_FAILED,
            message="critical failure",
            correlation_id="corr-2",
        )
        services.router.route(
            event_type=EventType.TASK_COMPLETED,
            message="done work",
            correlation_id="corr-3",
        )
        delivered = services.sink.delivered
        assert [n.priority for n in delivered] == [Priority.CRITICAL]
        flushed = services.router.flush_digest("tui")
        assert len(flushed) == 1
        assert flushed[0].message == "done work"


class TestViews:
    def test_task_row_shape(self, services: _Services) -> None:
        record = services.store.create("write tests", correlation_id="c")
        row = task_row(record)
        assert row[0] == record.task_id
        assert row[1] == "QUEUED"
        assert row[2] == "1/3"
        assert row[3] == "write tests"

    def test_approval_row_shape(self, services: _Services) -> None:
        assert services.approvals is not None
        row = services.approvals.create(
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
        assert approval_row(row) == (row.request_id, "file_delete", "agent:coder", row.expires_at)

    def test_schedule_row_shape(self, services: _Services) -> None:
        entry = once_entry("daily report", at=datetime(2026, 9, 9, 12, 0, tzinfo=UTC))
        services.scheduler.add(entry)
        assert schedule_row(entry) == (entry.schedule_id, "ONCE", "daily report", "True")

    async def test_agent_rows_reflect_live_runs(self, services: _Services) -> None:
        contract = AgentContract(mission="explore", task_id="task-1")
        handle = await services.pool.acquire(contract, _profile())
        rows = agent_rows(services.pool)
        assert len(rows) == 1
        assert rows[0][0] == handle.run_id
        assert rows[0][1] == "HEALTHY"
        services.pool.release(handle.run_id, failed=False)
        assert agent_rows(services.pool) == []


class TestPanels:
    async def test_app_boots_with_empty_panels(self, app: XenopusApp) -> None:
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.tasks_table.row_count == 0
            assert app.approvals_table.row_count == 0
            assert app.schedules_table.row_count == 0
            assert app.agents_table.row_count == 0

    async def test_task_appears_in_tasks_panel(self, services: _Services, app: XenopusApp) -> None:
        record = services.store.create("seed task", correlation_id="c1")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.tasks_table.row_count == 1
            assert app.tasks_table.get_row(record.task_id)[3] == "seed task"

    async def test_approval_appears_in_approvals_panel(
        self, services: _Services, app: XenopusApp
    ) -> None:
        assert services.approvals is not None
        row = services.approvals.create(
            tool="file_delete",
            subject="agent:coder",
            action_hash="hash-1",
            reason="cleanup",
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.approvals_table.row_count == 1
            assert app.approvals_table.get_row(row.request_id)[1] == "file_delete"

    async def test_approvals_panel_empty_without_store(
        self, tmp_path: Path, services: _Services
    ) -> None:
        if services.approvals is not None:
            services.approvals.close()
        services.approvals = None
        bare = XenopusApp(services.services())
        async with bare.run_test() as pilot:
            await pilot.pause()
            assert bare.approvals_table.row_count == 0

    async def test_schedule_appears_in_schedules_panel(
        self, services: _Services, app: XenopusApp
    ) -> None:
        entry = once_entry("heartbeat", at=datetime(2026, 9, 9, 12, 0, tzinfo=UTC))
        services.scheduler.add(entry)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.schedules_table.row_count == 1
            assert app.schedules_table.get_row(entry.schedule_id)[2] == "heartbeat"


class TestKeyboardFlows:
    async def test_new_task_via_keys(self, services: _Services, app: XenopusApp) -> None:
        """F2 -> type title -> Enter creates the task (keyboard-only)."""
        async with app.run_test() as pilot:
            await pilot.press("f2")
            await pilot.pause()
            await pilot.press(*"analyze repo")
            await pilot.press("enter")
            await pilot.pause()
            tasks = services.store.list_tasks()
            assert len(tasks) == 1
            assert tasks[0].title == "analyze repo"

    async def test_new_task_cancelled_by_escape(self, services: _Services, app: XenopusApp) -> None:
        async with app.run_test() as pilot:
            await pilot.press("f2")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert services.store.list_tasks() == []

    async def test_task_inspect_via_keys(self, services: _Services, app: XenopusApp) -> None:
        """Enter on the tasks panel opens the inspect overlay; escape closes."""
        services.store.create("inspect me", correlation_id="c2")
        async with app.run_test() as pilot:
            await pilot.pause()
            app.tasks_table.focus()
            await pilot.press("enter")
            await pilot.pause()
            from textual.screen import ModalScreen

            assert any(isinstance(s, ModalScreen) for s in app.screen_stack)
            await pilot.press("escape")
            await pilot.pause()
            assert all(not isinstance(s, ModalScreen) for s in app.screen_stack)

    async def test_approve_via_keys(self, services: _Services, app: XenopusApp) -> None:
        """Keyboard-only approval: focus approvals, select, confirm grant."""
        assert services.approvals is not None
        row = services.approvals.create(
            tool="file_delete",
            subject="agent:coder",
            action_hash="hash-2",
            reason="cleanup",
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            app.approvals_table.focus()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")  # confirm = grant
            await pilot.pause()
            assert services.approvals.get(row.request_id).status == "GRANTED"

    async def test_deny_approval_via_keys(self, services: _Services, app: XenopusApp) -> None:
        """Escape on the confirm screen denies the approval."""
        assert services.approvals is not None
        row = services.approvals.create(
            tool="file_delete",
            subject="agent:coder",
            action_hash="hash-3",
            reason="cleanup",
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            app.approvals_table.focus()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("escape")  # deny
            await pilot.pause()
            assert services.approvals.get(row.request_id).status == "DENIED"

    async def test_killswitch_via_keys_confirmed(
        self, services: _Services, app: XenopusApp
    ) -> None:
        """F4 -> Enter cancels all live tasks with audit trail."""
        services.store.create("live one", correlation_id="c3")
        services.store.create("live two", correlation_id="c4")
        async with app.run_test() as pilot:
            await pilot.press("f4")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            states = [t.state.value for t in services.store.list_tasks()]
            assert states == ["CANCELLED", "CANCELLED"]

    async def test_killswitch_aborted_by_escape(self, services: _Services, app: XenopusApp) -> None:
        services.store.create("still alive", correlation_id="c5")
        async with app.run_test() as pilot:
            await pilot.press("f4")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert services.store.list_tasks()[0].state.value == "QUEUED"


class TestSchedulerHosting:
    async def test_tick_fires_overdue_schedule_into_store(
        self, services: _Services, app: XenopusApp
    ) -> None:
        """The app's refresh interval hosts scheduler ticks (run_loop duty)."""
        past = datetime(2026, 9, 9, 11, 0, tzinfo=UTC)
        services.scheduler.add(once_entry("overdue", at=past))
        async with app.run_test():
            await asyncio.sleep(0.3)
            fired = [t for t in services.store.list_tasks() if t.title.startswith("scheduled:")]
            assert len(fired) == 1

    async def test_tick_failure_never_kills_ui(self, services: _Services, app: XenopusApp) -> None:
        """A crashing scheduler tick surfaces a toast; the app keeps running."""

        class _ExplodingScheduler(Scheduler):
            def tick(self) -> list[FireResult]:
                msg = "boom"
                raise RuntimeError(msg)

        exploding = _ExplodingScheduler(store=services.store, journal=services.journal)
        bundle = TuiServices(
            store=services.store,
            journal=services.journal,
            scheduler=exploding,
            pool=services.pool,
            router=services.router,
            sink=services.sink,
            refresh_seconds=0.05,
        )
        crash_app = XenopusApp(bundle)
        async with crash_app.run_test():
            await asyncio.sleep(0.2)
            assert crash_app.is_running


class TestPalette:
    async def test_palette_opens_and_lists_commands(self, app: XenopusApp) -> None:
        async with app.run_test() as pilot:
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert app.screen.__class__.__name__ == "CommandPalette"


class TestKeyboardOnlyE2E:
    async def test_full_session_path_via_keys_only(
        self, services: _Services, app: XenopusApp
    ) -> None:
        """Start -> create -> inspect -> approve -> killswitch, keys only."""
        assert services.approvals is not None
        approval = services.approvals.create(
            tool="file_delete",
            subject="agent:coder",
            action_hash="hash-e2e",
            reason="e2e cleanup",
        )
        async with app.run_test() as pilot:
            # 1. create a task from keys
            await pilot.press("f2")
            await pilot.pause()
            await pilot.press(*"e2e task")
            await pilot.press("enter")
            await pilot.pause()
            task = services.store.list_tasks()[0]
            assert task.title == "e2e task"

            # 2. inspect it from the tasks panel via keys
            app.tasks_table.focus()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

            # 3. approve the pending approval via keys
            app.approvals_table.focus()
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert services.approvals.get(approval.request_id).status == "GRANTED"

            # 4. killswitch cancels the live task via keys
            await pilot.press("f4")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert services.store.get(task.task_id).state.value == "CANCELLED"
