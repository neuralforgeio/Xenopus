"""Xenopus command-line interface.

Surfaces: version/diagnostics (Phase 1) and the durable task
remote-control commands (Phase 6): task create/list/inspect/start/
pause/resume/cancel/retry/recover plus the kill switch. The CLI is a
thin adapter over durable stores — no runtime agent logic lives here
(ADR-001; Rev.3 dependency graph: control-surface capability precedes
TUI/Web, which consume the same stores).
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import xenopus
from xenopus.bootstrap import bootstrap_runtime
from xenopus.config import load_config
from xenopus.gateway.telegram import (
    TOKEN_ENV_VAR,
    TelegramClient,
    TelegramCommander,
    TelegramSink,
    TelegramSinkConfig,
)
from xenopus.gateway.telegram import (
    load_chat_ids as telegram_load_chat_ids,
)
from xenopus.gateway.telegram import (
    load_token as telegram_load_token,
)
from xenopus.observability.correlation import new_correlation_id
from xenopus.persistence.approvals_store import ApprovalStore
from xenopus.persistence.journal import EventJournal
from xenopus.persistence.tasks import TaskError, TaskState, TaskStore
from xenopus.runtime.agent_pool import AgentPool
from xenopus.runtime.killswitch import KillSwitch
from xenopus.runtime.notifications import NotificationRouter, SubscriberPolicy
from xenopus.runtime.recovery import CrashRecovery
from xenopus.runtime.scheduler import Scheduler
from xenopus.web.run import run_dashboard
from xenopus.web.server import WebServices
from xenopus.web.sink import WebSink

STATE_COLORS = {
    "COMPLETED": "green",
    "FAILED": "red",
    "RUNNING": "cyan",
    "CANCELLED": "yellow",
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xenopus",
        description="Xenopus — self-improving autonomous AI agent runtime.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {xenopus.__version__}")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="Run environment and local-state diagnostics")

    web = sub.add_parser("web", help="Serve the local web dashboard (127.0.0.1)")
    web.add_argument("--port", type=int, default=8765, help="loopback port (default 8765)")

    telegram = sub.add_parser(
        "telegram", help="Host the Telegram channel (outbound sink + command poller)"
    )
    telegram.add_argument(
        "--no-poll", action="store_true", help="outbound-only (no inbound command polling)"
    )

    task = sub.add_parser("task", help="Durable task control")
    task_sub = task.add_subparsers(dest="task_command")

    create = task_sub.add_parser("create", help="Create a queued task")
    create.add_argument("title", help="Task title")
    create.add_argument("--goal", help="Owning goal reference", default=None)
    create.add_argument("--plan", help="Owning plan reference", default=None)

    list_cmd = task_sub.add_parser("list", help="List tasks")
    list_cmd.add_argument("--state", choices=[s.value for s in TaskState], default=None)
    list_cmd.add_argument("--limit", type=int, default=20)

    inspect = task_sub.add_parser("inspect", help="Show one task")
    inspect.add_argument("task_id")

    start = task_sub.add_parser("start", help="Start a queued task")
    start.add_argument("task_id")

    pause = task_sub.add_parser("pause", help="Pause a running task")
    pause.add_argument("task_id")

    resume = task_sub.add_parser("resume", help="Resume a paused/resumable task")
    resume.add_argument("task_id")

    cancel = task_sub.add_parser("cancel", help="Cancel a live task")
    cancel.add_argument("task_id")

    retry = task_sub.add_parser("retry", help="Re-queue a failed task")
    retry.add_argument("task_id")

    task_sub.add_parser("recover", help="Flag interrupted tasks as resumable")

    kill = sub.add_parser("killswitch", help="Emergency: cancel ALL live tasks")
    kill.add_argument("--reason", default="manual kill switch")
    return parser


def _run_doctor() -> int:
    """Run diagnostics; return exit code 0 on success, 1 on failure."""
    print(f"Xenopus {xenopus.__version__}")
    try:
        config = load_config()
        report = bootstrap_runtime(config)
    except (ValueError, OSError) as err:
        print(f"FAILED: {err}", file=sys.stderr)
        return 1
    print(f"home: {config.home}")
    print(f"created: {report.created}")
    print(f"already present: {report.existed}")
    return 0


def _open_stores() -> tuple[TaskStore, EventJournal]:
    """Open the durable stores under the Xenopus home directory."""
    config = load_config()
    bootstrap_runtime(config)
    journal = EventJournal(config.home / "runtime.sqlite")
    store = TaskStore(config.home / "runtime.sqlite", journal=journal)
    return store, journal


def _close_stores(store: TaskStore, journal: EventJournal) -> None:
    store.close()
    journal.close()


def _run_task_command(args: argparse.Namespace) -> int:
    """Dispatch task subcommands over the durable store."""
    store, journal = _open_stores()
    try:
        if args.task_command == "create":
            record = store.create(
                args.title,
                correlation_id=new_correlation_id(),
                goal_ref=args.goal,
                plan_ref=args.plan,
            )
            print(f"created: {record.task_id}")
            return 0
        if args.task_command == "list":
            state = TaskState(args.state) if args.state else None
            for record in store.list_tasks(state=state, limit=args.limit):
                print(f"{record.task_id}  {record.state.value:<10}  {record.title}")
            return 0
        if args.task_command == "inspect":
            record = store.get(args.task_id)
            print(f"task:      {record.task_id}")
            print(f"title:     {record.title}")
            print(f"state:     {record.state.value}")
            print(f"goal:      {record.goal_ref or '-'}")
            print(f"plan:      {record.plan_ref or '-'}")
            print(f"attempt:   {record.attempt}/{record.max_attempts}")
            print(f"corr:      {record.correlation_id}")
            print(f"created:  {record.created_at}")
            print(f"updated:  {record.updated_at}")
            return 0

        targets = {
            "start": TaskState.RUNNING,
            "pause": TaskState.PAUSED,
            "resume": TaskState.RUNNING,
            "cancel": TaskState.CANCELLED,
        }
        if args.task_command in targets:
            record = store.transition(args.task_id, targets[args.task_command])
            print(f"{args.task_id}: {record.state.value}")
            return 0
        if args.task_command == "retry":
            record = store.retry(args.task_id)
            print(f"{args.task_id}: {record.state.value} (attempt {record.attempt})")
            return 0
        if args.task_command == "recover":
            report = CrashRecovery(store).run()
            for record in report.interrupted:
                print(f"interrupted -> resumable: {record.task_id}")
            if not report.has_work:
                print("no interrupted tasks")
            return 0
        print("unknown task command", file=sys.stderr)
        return 1
    except TaskError as err:
        print(f"FAILED: {err}", file=sys.stderr)
        return 1
    finally:
        _close_stores(store, journal)


def _run_killswitch(args: argparse.Namespace) -> int:
    """Trigger the emergency kill switch."""
    store, journal = _open_stores()
    try:
        report = KillSwitch(store=store, journal=journal).trigger(args.reason)
        print(f"killswitch: {len(report.cancelled)} task(s) cancelled")
        print(f"reason: {report.reason}")
        print(f"audit: {report.correlation_id}")
        return 0
    finally:
        _close_stores(store, journal)


def _run_web(args: argparse.Namespace) -> int:
    """Serve the local web dashboard on 127.0.0.1 (Phase 11)."""
    config = load_config()
    bootstrap_runtime(config)
    db = config.home / "runtime.sqlite"
    journal = EventJournal(db, cross_thread=True)
    store = TaskStore(db, journal=journal, cross_thread=True)
    try:
        scheduler = Scheduler(store=store, journal=journal)
        pool = AgentPool()
        sink = WebSink()
        router = NotificationRouter(journal=journal)
        router.subscribe(SubscriberPolicy(subscriber="web", digest=True), sink)
        services = WebServices(
            store=store,
            journal=journal,
            scheduler=scheduler,
            pool=pool,
            router=router,
            sink=sink,
        )
        print(f"xenopus web dashboard: http://127.0.0.1:{args.port} (ctrl+c to stop)")
        run_dashboard(services, port=args.port)
        return 0
    finally:
        store.close()
        journal.close()


def _run_telegram(args: argparse.Namespace) -> int:
    """Host the Telegram channel: outbound sink + command poller (ADR-023)."""
    token = telegram_load_token()
    if token is None:
        print(
            f"FAILED: {TOKEN_ENV_VAR} is not set — refusing to start "
            "the Telegram channel (fail-fast, no silent no-op)",
            file=sys.stderr,
        )
        return 1
    chat_ids = telegram_load_chat_ids()
    config = load_config()
    bootstrap_runtime(config)
    db = config.home / "runtime.sqlite"
    journal = EventJournal(db, cross_thread=True)
    store = TaskStore(db, journal=journal, cross_thread=True)
    approvals = ApprovalStore(str(db), cross_thread=True)
    client = TelegramClient(token)
    commander = TelegramCommander(
        client=client,
        store=store,
        approvals=approvals,
        journal=journal,
        killswitch=KillSwitch(store=store, journal=journal),
        chat_ids=chat_ids,
    )
    sink = TelegramSink(TelegramSinkConfig(client=client, journal=journal, chat_ids=chat_ids))
    router = NotificationRouter(journal=journal)
    router.subscribe(SubscriberPolicy(subscriber="telegram"), sink)
    scheduler = Scheduler(store=store, journal=journal)
    try:
        asyncio.run(
            _telegram_loop(
                client=client,
                commander=commander,
                scheduler=scheduler,
                poll=not args.no_poll,
            )
        )
        return 0
    finally:
        store.close()
        journal.close()
        approvals.close()


async def _telegram_loop(
    *,
    client: TelegramClient,
    commander: TelegramCommander,
    scheduler: Scheduler,
    poll: bool,
) -> None:
    """One loop: startup verification, scheduler ticks, inbound polls."""
    me = await client.get_me()
    username = me.get("result", {}).get("username", "bot")
    print(f"xenopus telegram channel online: @{username} (ctrl+c to stop)")
    try:
        while True:
            scheduler.tick()
            if poll:
                await commander.poll_once()
            else:
                await asyncio.sleep(1.0)
    finally:
        await client.aclose()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return _run_doctor()
    if args.command == "task":
        return _run_task_command(args)
    if args.command == "killswitch":
        return _run_killswitch(args)
    if args.command == "web":
        return _run_web(args)
    if args.command == "telegram":
        return _run_telegram(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
