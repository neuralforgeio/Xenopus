"""Dashboard ASGI server — app factory over the engine bundle.

Builds the Starlette application the CLI serves via Uvicorn on
127.0.0.1 ONLY (ADR-022). Mutations are POST + CSRF token; GET is
always safe. The lifespan hosts the scheduler tick (the same duty the
TUI refresh interval performs); a tick failure is journaled, never
fatal to the server.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route

from xenopus.observability.correlation import new_correlation_id
from xenopus.persistence.approvals_store import ApprovalStore
from xenopus.persistence.journal import EventJournal
from xenopus.persistence.tasks import TaskError, TaskState, TaskStore
from xenopus.runtime.agent_pool import AgentPool
from xenopus.runtime.events import Event, EventType
from xenopus.runtime.killswitch import KillSwitch
from xenopus.runtime.notifications import NotificationRouter
from xenopus.runtime.scheduler import Scheduler
from xenopus.tui.views import agent_rows as agent_view_rows
from xenopus.web import views
from xenopus.web.sink import WebSink

TICK_INTERVAL_SECONDS = 2.0
LIVE_STATES = (
    TaskState.QUEUED,
    TaskState.RUNNING,
    TaskState.WAITING,
    TaskState.PAUSED,
    TaskState.RESUMABLE,
)


@dataclass(frozen=True, slots=True)
class WebServices:
    """Engine bundle the dashboard renders (all pre-built, none owned).

    Contract: every field is an existing runtime engine; the web layer
    adds no engine of its own (ADR-001/022). ``approvals`` is optional
    because a session may run without a persisted approval ledger (the
    approvals page then renders empty).
    """

    store: TaskStore
    journal: EventJournal
    scheduler: Scheduler
    pool: AgentPool
    router: NotificationRouter
    sink: WebSink
    approvals: ApprovalStore | None = None
    tick_interval_seconds: float = TICK_INTERVAL_SECONDS


class _Csrf:
    """Per-boot CSRF token; POST mutations must present it exactly."""

    def __init__(self) -> None:
        self._token: str | None = None

    @property
    def token(self) -> str:
        """Current token, minted lazily at first use."""
        if self._token is None:
            self._token = secrets.token_urlsafe(32)
        return self._token

    def check(self, presented: str | None) -> bool:
        """Constant-time comparison against the minted token."""
        if presented is None or self._token is None:
            return False
        return secrets.compare_digest(presented, self._token)


def _forbidden() -> PlainTextResponse:
    """403 for missing/invalid CSRF tokens (never 200-in-place)."""
    return PlainTextResponse("forbidden", status_code=403)


def _html(body: str) -> Response:
    """HTML response with nosniff + no-store (dashboard defense headers)."""
    return Response(
        body,
        media_type="text/html; charset=utf-8",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        },
    )


class Dashboard:
    """Route handlers bound to one engine bundle.

    Handlers are thin: parse request -> engine call (or view render)
    -> escaped HTML. No state beyond the CSRF token lives here; all
    mutable state is in the engines.
    """

    def __init__(self, services: WebServices) -> None:
        self._services = services
        self._csrf = _Csrf()

    # -- safe pages (GET) ---------------------------------------------------

    async def tasks(self, request: Request) -> Response:
        """Tasks panel."""
        return _html(views.tasks_page(self._services.store.list_tasks()))

    async def task_detail(self, request: Request) -> Response:
        """One task's record page."""
        task_id = request.path_params["task_id"]
        try:
            record = self._services.store.get(task_id)
        except TaskError:
            return PlainTextResponse("not found", status_code=404)
        return _html(views.task_detail(record))

    async def new_task_form(self, request: Request) -> Response:
        """New-task entry form (GET renders; POST creates)."""
        return _html(views.new_task_page(csrf_token=self._csrf.token))

    async def approvals(self, request: Request) -> Response:
        """Approvals panel with grant/deny forms."""
        rows = self._services.approvals.list_pending() if self._services.approvals else []
        return _html(views.approvals_page(rows, csrf_token=self._csrf.token))

    async def schedules(self, request: Request) -> Response:
        """Schedules panel."""
        return _html(views.schedules_page(self._services.scheduler.entries()))

    async def agents(self, request: Request) -> Response:
        """Agent health panel (watchdog view)."""
        return _html(views.agents_page(agent_view_rows(self._services.pool)))

    async def events(self, request: Request) -> Response:
        """Recent router-approved notifications (bounded feed)."""
        lines = [
            f"[{n.priority.value}] {n.event_type}: {n.message} ({n.correlation_id})"
            for n in self._services.sink.recent
        ]
        return _html(views.events_page(lines))

    async def killswitch_form(self, request: Request) -> Response:
        """Killswitch confirmation page (POST-only mutation)."""
        live = sum(1 for t in self._services.store.list_tasks(limit=1000) if t.state in LIVE_STATES)
        return _html(views.killswitch_page(live_count=live, csrf_token=self._csrf.token))

    # -- mutations (POST + CSRF) ----------------------------------------------

    async def new_task_submit(self, request: Request) -> Response:
        """Create a task from the form title."""
        if not self._csrf.check(await _csrf_from(request)):
            return _forbidden()
        form = await request.form()
        title = form.get("title")
        if not isinstance(title, str) or not title.strip():
            return PlainTextResponse("title required", status_code=400)
        record = self._services.store.create(title.strip(), correlation_id=new_correlation_id())
        return _html(views.new_task_page(csrf_token=self._csrf.token, done_id=record.task_id))

    async def approval_decide(self, request: Request) -> Response:
        """Grant or deny one pending approval (single-use engine call)."""
        if not self._csrf.check(await _csrf_from(request)):
            return _forbidden()
        approvals = self._services.approvals
        if approvals is None:
            return PlainTextResponse("not found", status_code=404)
        request_id = str(request.path_params["request_id"])
        decision = str(request.path_params["decision"])
        error: str | None = None
        try:
            if decision == "grant":
                approvals.grant(request_id)
            elif decision == "deny":
                approvals.deny(request_id)
            else:
                return PlainTextResponse("not found", status_code=404)
        except Exception as err:  # surfaced on the page, never swallowed
            error = str(err)
        rows = approvals.list_pending()
        return _html(views.approvals_page(rows, csrf_token=self._csrf.token, error=error))

    async def killswitch_submit(self, request: Request) -> Response:
        """Trigger the confirmed killswitch; render the outcome."""
        if not self._csrf.check(await _csrf_from(request)):
            return _forbidden()
        report = KillSwitch(store=self._services.store, journal=self._services.journal).trigger(
            "web killswitch command"
        )
        return _html(
            views.killswitch_page(
                live_count=len(report.cancelled),
                csrf_token=self._csrf.token,
                done=True,
            )
        )


async def _csrf_from(request: Request) -> str | None:
    """CSRF token from the form body."""
    form = await request.form()
    value = form.get("csrf")
    return str(value) if isinstance(value, str) else None


async def _tick_loop(services: WebServices, stop: asyncio.Event) -> None:
    """Tick the scheduler until stopped; failures journal, never kill."""
    while not stop.is_set():
        try:
            services.scheduler.tick()
        except Exception as err:  # a tick failure must not kill the server
            services.journal.append(
                Event(
                    type=EventType.SCHEDULE_FIRED,
                    correlation_id="web-tick",
                    payload={"web_tick_error": str(err)},
                )
            )
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=services.tick_interval_seconds)


def build_app(services: WebServices) -> Starlette:
    """Compose the ASGI app: routes + CSRF-guarded handlers + lifespan."""

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        """Host the scheduler tick for the server's lifetime (ADR-022)."""
        stop = asyncio.Event()
        task = asyncio.create_task(_tick_loop(services, stop))
        try:
            yield
        finally:
            stop.set()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    return Starlette(routes=_routes(Dashboard(services)), lifespan=lifespan)


def _routes(handlers: Dashboard) -> list[Route]:
    """URL map: GET safe pages, POST mutations (ADR-022 table)."""
    return [
        Route("/", handlers.tasks, name="tasks"),
        Route("/tasks/new", handlers.new_task_form, methods=["GET"], name="new-task-form"),
        Route("/tasks/new", handlers.new_task_submit, methods=["POST"], name="new-task"),
        Route("/tasks/{task_id}", handlers.task_detail, name="task-detail"),
        Route("/approvals", handlers.approvals, name="approvals"),
        Route(
            "/approvals/{request_id}/{decision}",
            handlers.approval_decide,
            methods=["POST"],
            name="approval-decide",
        ),
        Route("/schedules", handlers.schedules, name="schedules"),
        Route("/agents", handlers.agents, name="agents"),
        Route("/events", handlers.events, name="events"),
        Route("/killswitch", handlers.killswitch_form, methods=["GET"], name="killswitch-form"),
        Route(
            "/killswitch",
            handlers.killswitch_submit,
            methods=["POST"],
            name="killswitch",
        ),
    ]
