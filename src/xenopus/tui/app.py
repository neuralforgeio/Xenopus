"""XenopusApp — the Textual surface over the existing runtime engines.

Composition root for Phase 10: hosts the four panels (tasks,
approvals, schedules, agents), the command palette entries, the
keyboard-only flows (task inspect, approval grant/deny, killswitch,
new task), and the scheduler tick loop. All engine interactions go
through the same TaskStore/ApprovalStore/Scheduler/AgentPool/
NotificationRouter APIs the CLI and orchestrator use — the app holds
views and commands, never business rules (ADR-001/021).

Panel widgets are constructed in ``__init__`` and referenced as
attributes (not DOM queries): a mounted DataTable is guaranteed
queryable from ``on_mount`` regardless of nested-container mount
ordering, and refresh paths never race the DOM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from textual import on
from textual.app import App, ComposeResult, get_system_commands_provider
from textual.binding import Binding, BindingType
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Header, Input, Label, TabbedContent, TabPane

from xenopus.observability.correlation import new_correlation_id
from xenopus.persistence.approvals_store import ApprovalStore
from xenopus.persistence.journal import EventJournal
from xenopus.persistence.tasks import TaskError, TaskStore
from xenopus.runtime.agent_pool import AgentPool
from xenopus.runtime.killswitch import KillSwitch
from xenopus.runtime.notifications import NotificationRouter
from xenopus.runtime.scheduler import Scheduler
from xenopus.tui.palette import XenopusCommands
from xenopus.tui.sink import TextualSink
from xenopus.tui.views import (
    AGENT_COLUMNS,
    APPROVAL_COLUMNS,
    SCHEDULE_COLUMNS,
    TASK_COLUMNS,
    agent_rows,
    approval_row,
    schedule_row,
    task_row,
)

REFRESH_INTERVAL_SECONDS = 2.0
TASKS_PANE_ID = "tasks-pane"
APPROVALS_PANE_ID = "approvals-pane"
SCHEDULES_PANE_ID = "schedules-pane"
AGENTS_PANE_ID = "agents-pane"
TITLE_FIELD_ID = "title-field"
PANE_IDS = (TASKS_PANE_ID, APPROVALS_PANE_ID, SCHEDULES_PANE_ID, AGENTS_PANE_ID)


@dataclass(frozen=True, slots=True)
class TuiServices:
    """Engine bundle the app renders (all pre-built, none owned).

    Contract: every field is an existing runtime engine; the TUI adds
    no engine of its own. ``approvals`` is optional because a session
    may run without a persisted approval ledger (the approvals panel
    then renders empty). ``refresh_seconds`` sets the hosting tick
    cadence (scheduler tick + panel refresh).
    """

    store: TaskStore
    journal: EventJournal
    scheduler: Scheduler
    pool: AgentPool
    router: NotificationRouter
    sink: TextualSink
    approvals: ApprovalStore | None = None
    refresh_seconds: float = REFRESH_INTERVAL_SECONDS


class ConfirmScreen(ModalScreen[bool]):
    """Keyboard-only yes/no confirmation (enter=yes, escape=no)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter,y", "confirm", "Yes", show=True),
        Binding("escape,n", "deny", "No", show=True),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        yield Label(self._prompt)

    def action_confirm(self) -> None:
        """Answer yes."""
        self.dismiss(True)

    def action_deny(self) -> None:
        """Answer no."""
        self.dismiss(False)


class TitleInputScreen(ModalScreen[str]):
    """Keyboard-only single-line input (submit=enter, cancel=escape)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        self._prompt = prompt

    def compose(self) -> ComposeResult:
        yield Label(self._prompt)
        yield Input(id=TITLE_FIELD_ID)

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        """Answer with the entered text."""
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        """Answer with no text."""
        self.dismiss("")


class InfoScreen(ModalScreen[None]):
    """Read-only detail overlay (enter/escape dismisses)."""

    BINDINGS: ClassVar[list[BindingType]] = [Binding("enter,escape", "close", "Close", show=True)]

    def __init__(self, detail: str) -> None:
        super().__init__()
        self._detail = detail

    def compose(self) -> ComposeResult:
        yield Label(self._detail)

    def action_close(self) -> None:
        """Dismiss the overlay."""
        self.dismiss()


class XenopusApp(App[None]):
    """Terminal control surface for the Xenopus runtime."""

    TITLE = "Xenopus"
    SUB_TITLE = "agent runtime — local control surface"

    COMMANDS: ClassVar[set[Any]] = {get_system_commands_provider, XenopusCommands}

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("f2", "new_task", "New task"),
        Binding("f3", "refresh", "Refresh"),
        Binding("f4", "killswitch", "Killswitch"),
        Binding("f5", "flush_digest", "Digest"),
    ]

    def __init__(self, services: TuiServices) -> None:
        super().__init__()
        self._services = services
        self._tasks_table = DataTable[str](id="tasks-table", cursor_type="row")
        self._approvals_table = DataTable[str](id="approvals-table", cursor_type="row")
        self._schedules_table = DataTable[str](id="schedules-table", cursor_type="row")
        self._agents_table = DataTable[str](id="agents-table", cursor_type="row")
        self._tabs = TabbedContent()

    # -- composition -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with self._tabs:
            with TabPane("Tasks", id=TASKS_PANE_ID):
                yield self._tasks_table
            with TabPane("Approvals", id=APPROVALS_PANE_ID):
                yield self._approvals_table
            with TabPane("Schedules", id=SCHEDULES_PANE_ID):
                yield self._schedules_table
            with TabPane("Agents", id=AGENTS_PANE_ID):
                yield self._agents_table
        yield Footer()

    def on_mount(self) -> None:
        """Wire table columns, the notification sink, and the tick loop."""
        self._tasks_table.add_columns(*TASK_COLUMNS)
        self._approvals_table.add_columns(*APPROVAL_COLUMNS)
        self._schedules_table.add_columns(*SCHEDULE_COLUMNS)
        self._agents_table.add_columns(*AGENT_COLUMNS)
        self._services.sink.attach(self)
        self.action_refresh()
        self.set_interval(self._services.refresh_seconds, self._tick)

    def on_unmount(self) -> None:
        """Detach the sink on shutdown so deliveries buffer, not crash."""
        self._services.sink.detach()

    # -- refresh path ------------------------------------------------------

    def _tick(self) -> None:
        """Periodic housekeeping: scheduler tick (host duty) + refresh."""
        try:
            self._services.scheduler.tick()
        except Exception as err:  # a tick failure must not kill the UI
            self.notify(f"scheduler tick failed: {err}", severity="error", markup=False)
        self.action_refresh()

    def action_refresh(self) -> None:
        """Reload every panel from the engines (read path only)."""
        self._tasks_table.clear()
        for record in self._services.store.list_tasks():
            self._tasks_table.add_row(*task_row(record), key=record.task_id)

        self._approvals_table.clear()
        approvals = self._services.approvals
        if approvals is not None:
            for row in approvals.list_pending():
                self._approvals_table.add_row(*approval_row(row), key=row.request_id)

        self._schedules_table.clear()
        for entry in self._services.scheduler.entries():
            self._schedules_table.add_row(*schedule_row(entry), key=entry.schedule_id)

        self._agents_table.clear()
        for agent_row_tuple in agent_rows(self._services.pool):
            self._agents_table.add_row(*agent_row_tuple, key=agent_row_tuple[0])

    # -- selection handlers -------------------------------------------------

    @on(DataTable.RowSelected)
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        """Keyboard path from a selected row into its flow."""
        if event.data_table is self._tasks_table:
            self._inspect_task(str(event.row_key.value or ""))
        elif event.data_table is self._approvals_table:
            self._decide_approval(str(event.row_key.value or ""))
        elif event.data_table is self._schedules_table:
            self._show_schedule(str(event.row_key.value or ""))
        elif event.data_table is self._agents_table:
            self._show_agent(str(event.row_key.value or ""))

    def _inspect_task(self, task_id: str) -> None:
        """Show one task's full record as a dismissable overlay."""
        try:
            record = self._services.store.get(task_id)
        except TaskError as err:
            self.notify(f"task error: {err}", severity="error", markup=False)
            return
        detail = (
            f"task:     {record.task_id}\n"
            f"title:    {record.title}\n"
            f"state:    {record.state.value}\n"
            f"attempt:  {record.attempt}/{record.max_attempts}\n"
            f"goal:     {record.goal_ref or '-'}\n"
            f"plan:     {record.plan_ref or '-'}\n"
            f"corr:     {record.correlation_id}\n"
            f"created:  {record.created_at}\n"
            f"updated:  {record.updated_at}"
        )
        self.push_screen(InfoScreen(detail))

    def _decide_approval(self, request_id: str) -> None:
        """Open the grant/deny decision for one pending approval."""
        approvals = self._services.approvals
        if approvals is None:
            return
        try:
            row = approvals.get(request_id)
        except Exception as err:  # surfaced to the operator, never swallowed
            self.notify(f"approval error: {err}", severity="error", markup=False)
            return
        prompt = (
            f"{row.request_id}: {row.tool} on {row.subject}\n"
            f"reason: {row.reason}\n"
            f"expires: {row.expires_at}\n"
            f"grant?"
        )
        self.push_screen(
            ConfirmScreen(prompt),
            callback=lambda granted: self._apply_approval(request_id, granted),
        )

    def _apply_approval(self, request_id: str, granted: bool | None) -> None:
        """Apply the decision made on the confirm screen."""
        approvals = self._services.approvals
        if approvals is None:
            return
        try:
            row = approvals.grant(request_id) if granted else approvals.deny(request_id)
        except Exception as err:  # surfaced to the operator, never swallowed
            self.notify(f"approval error: {err}", severity="error", markup=False)
            return
        self.notify(f"{request_id}: {row.status}", markup=False)
        self.action_refresh()

    def _show_schedule(self, schedule_id: str) -> None:
        """Show one schedule entry's definition."""
        entry = self._services.scheduler.get(schedule_id)
        if entry is None:
            return
        detail = (
            f"schedule: {entry.schedule_id}\n"
            f"name:     {entry.name}\n"
            f"kind:     {entry.kind.value}\n"
            f"enabled:  {entry.enabled}\n"
            f"created:  {entry.created_at.isoformat()}\n"
            f"at_time:  {entry.at_time or '-'}\n"
            f"interval: {entry.interval_seconds or '-'}\n"
            f"daily at: {entry.time_of_day or '-'}\n"
            f"weekday:  {entry.weekday or '-'}\n"
            f"max_runs: {entry.max_executions or 'unbounded'}"
        )
        self.push_screen(InfoScreen(detail))

    def _show_agent(self, run_id: str) -> None:
        """Show one live agent run's health snapshot."""
        health = self._services.pool.health_of(run_id)
        handle = next(
            (h for h in self._services.pool.live_handles if h.run_id == run_id),
            None,
        )
        if handle is None:
            return
        detail = (
            f"run:       {handle.run_id}\n"
            f"task:      {handle.task_id}\n"
            f"depth:     {handle.depth}\n"
            f"role:      {handle.profile.role}\n"
            f"health:    {health.value if health else 'HEALTHY'}\n"
            f"started:   {handle.started_at}\n"
            f"heartbeat: {handle.last_heartbeat}"
        )
        self.push_screen(InfoScreen(detail))

    # -- commands (palette + function keys) ---------------------------------

    def action_new_task(self) -> None:
        """Create a task from a keyboard-entered title."""
        self.push_screen(
            TitleInputScreen("task title:"),
            callback=self._create_task_from_title,
        )

    def _create_task_from_title(self, title: str | None) -> None:
        if not title or not title.strip():
            return
        try:
            record = self._services.store.create(
                title,
                correlation_id=new_correlation_id(),
            )
        except TaskError as err:
            self.notify(f"task error: {err}", severity="error", markup=False)
            return
        self.notify(f"created: {record.task_id}", markup=False)
        self.action_refresh()

    def action_killswitch(self) -> None:
        """Cancel ALL live tasks after an explicit confirmation."""
        self.push_screen(
            ConfirmScreen("cancel ALL live tasks?"),
            callback=self._run_killswitch,
        )

    def _run_killswitch(self, confirmed: bool | None) -> None:
        if not confirmed:
            return
        report = KillSwitch(store=self._services.store, journal=self._services.journal).trigger(
            "tui killswitch command"
        )
        self.notify(
            f"killswitch: {len(report.cancelled)} task(s) cancelled ({report.correlation_id})",
            severity="error",
            markup=False,
        )
        self.action_refresh()

    def action_flush_digest(self) -> None:
        """Flush the TUI subscriber's held digest (addendum 98)."""
        held = self._services.router.flush_digest("tui")
        self.notify(f"digest flushed: {len(held)} held notification(s)", markup=False)

    def action_focus_panel(self, pane_id: str) -> None:
        """Activate one panel by TabPane id (palette/keyboard navigation)."""
        if pane_id in PANE_IDS:
            self._tabs.active = pane_id

    # -- introspection hooks -------------------------------------------------

    @property
    def tasks_table(self) -> DataTable[str]:
        """Tasks panel table (test/introspection access)."""
        return self._tasks_table

    @property
    def approvals_table(self) -> DataTable[str]:
        """Approvals panel table (test/introspection access)."""
        return self._approvals_table

    @property
    def schedules_table(self) -> DataTable[str]:
        """Schedules panel table (test/introspection access)."""
        return self._schedules_table

    @property
    def agents_table(self) -> DataTable[str]:
        """Agents panel table (test/introspection access)."""
        return self._agents_table

    @property
    def services(self) -> TuiServices:
        """Engine bundle (test/introspection access)."""
        return self._services
