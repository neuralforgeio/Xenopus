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
from collections.abc import Callable
from pathlib import Path

import xenopus
from xenopus.bootstrap import bootstrap_runtime
from xenopus.config import load_config
from xenopus.gateway.discord import (
    TOKEN_ENV_VAR as DISCORD_TOKEN_ENV_VAR,
)
from xenopus.gateway.discord import (
    DiscordClient,
    DiscordCommander,
    DiscordSink,
    DiscordSinkConfig,
    RestResponder,
)
from xenopus.gateway.discord import (
    load_channel_ids as discord_load_channel_ids,
)
from xenopus.gateway.discord import (
    load_guild_ids as discord_load_guild_ids,
)
from xenopus.gateway.discord import (
    load_token as discord_load_token,
)
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
from xenopus.memory.store import MemoryStore
from xenopus.observability.correlation import new_correlation_id
from xenopus.persistence.approvals_store import ApprovalStore
from xenopus.persistence.journal import EventJournal
from xenopus.persistence.reliability import ReliabilityStore
from xenopus.persistence.tasks import TaskError, TaskState, TaskStore
from xenopus.runtime.agent_pool import AgentPool
from xenopus.runtime.killswitch import KillSwitch
from xenopus.runtime.learning import ReflectionLoop
from xenopus.runtime.notifications import NotificationRouter, SubscriberPolicy
from xenopus.runtime.recovery import CrashRecovery
from xenopus.runtime.scheduler import Scheduler
from xenopus.skills.registry import SkillRegistry
from xenopus.web.run import run_dashboard
from xenopus.web.server import WebServices, build_app
from xenopus.web.sink import WebSink
from xenopus.web.webhooks import load_webhook_secrets

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

    discord_cmd = sub.add_parser(
        "discord", help="Host the Discord channel (outbound sink + command gateway)"
    )
    discord_cmd.add_argument(
        "--no-listen", action="store_true", help="outbound-only (no inbound command gateway)"
    )

    sub.add_parser("reflect", help="Run the self-improvement loop once (consolidate experience)")

    resident = sub.add_parser(
        "resident", help="Host the desktop-shell resident mode (engines + optional web UI)"
    )
    resident.add_argument(
        "--web", action="store_true", help="also serve the loopback dashboard and open the browser"
    )
    resident.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="scheduler tick interval in seconds (default 5, bounds 1-3600)",
    )
    resident.add_argument(
        "--reflect-every",
        type=float,
        default=3600.0,
        help="self-improvement loop cadence in seconds (default 3600)",
    )
    resident.add_argument("--port", type=int, default=8765, help="dashboard port (default 8765)")

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


def _run_resident(args: argparse.Namespace) -> int:
    """Host the resident mode: engines + optional dashboard (ADR-027)."""
    from webbrowser import open as open_browser

    from xenopus.runtime.resident import ResidentHost, install_signal_handlers

    config = load_config()
    bootstrap_runtime(config)
    host = ResidentHost(
        home=config.home,
        tick_interval_seconds=args.interval,
        reflect_every_seconds=args.reflect_every,
    )
    install_signal_handlers(host)
    stop_dashboard: Callable[[], None] | None = None
    if args.web:
        url = f"http://127.0.0.1:{args.port}"
        print(f"xenopus resident: dashboard at {url} (opening browser)")
        print("xenopus resident: ctrl+c to stop")
        _, stop_dashboard = _start_dashboard_thread(config.home, args.port)
        open_browser(url)
    else:
        print("xenopus resident: hosting engines (ctrl+c to stop)")
    try:
        report = asyncio.run(host.run())
        print(
            f"resident stopped: {report.ticks} tick(s), "
            f"{report.reflection_runs} reflection run(s) — {report.correlation_id}"
        )
        return 0
    finally:
        if stop_dashboard is not None:
            stop_dashboard()


def _start_dashboard_thread(home: Path, port: int) -> tuple[object, Callable[[], None]]:
    """Serve the loopback dashboard on a daemon thread; return (server, stop)."""
    import threading

    import uvicorn

    db = home / "runtime.sqlite"
    journal = EventJournal(db, cross_thread=True)
    store = TaskStore(db, journal=journal, cross_thread=True)
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
        approvals=ApprovalStore(str(db), cross_thread=True),
    )
    server = uvicorn.Server(
        uvicorn.Config(
            build_app(services),
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
        )
    )

    def stop() -> None:
        server.should_exit = True

    thread = threading.Thread(target=server.run, name="xenopus-resident-web", daemon=True)
    thread.start()
    return server, stop


def _run_reflect() -> int:
    """Run the ReflectionLoop once over the local state (ADR-026)."""
    config = load_config()
    bootstrap_runtime(config)
    journal = EventJournal(config.home / "runtime.sqlite", cross_thread=True)
    reliability = ReliabilityStore(str(config.home / "runtime.sqlite"))
    memory = MemoryStore(config.home / "memory" / "memories.sqlite")
    skills = SkillRegistry(config.home / "skills" / "skills.sqlite")
    try:
        loop = ReflectionLoop(
            reliability=reliability,
            memory=memory,
            skills=skills,
            journal=journal,
        )
        report = loop.run()
        if report.aborted is not None:
            print(f"reflection aborted: {report.aborted}", file=sys.stderr)
            return 1
        print(f"reflection run: {report.correlation_id}")
        print(f"memory candidates proposed: {len(report.proposed)}")
        print(f"promoted (gate passed):      {len(report.promoted)}")
        print(f"gate refusals (recorded):    {len(report.gate_refused)}")
        print(f"duplicates skipped:          {report.skipped_duplicates}")
        print(f"skill evaluations appended:  {len(report.skill_evaluations)}")
        for refused in report.gate_refused:
            print(f"refused: {refused}", file=sys.stderr)
        return 0
    finally:
        skills.close()
        memory.close()
        reliability.close()
        journal.close()


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
    """Serve the local web dashboard on 127.0.0.1 (Phase 11).

    Webhook inbound (Phase 14, ADR-025) mounts only when
    XENOPUS_WEBHOOK_SECRETS configures one or more sources.
    """
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
        secrets = load_webhook_secrets()
        if secrets:
            print(f"webhook inbound: {len(secrets)} source(s) mounted at /webhooks/{{source}}")
        else:
            print("webhook inbound: off (XENOPUS_WEBHOOK_SECRETS not set)")
        services = WebServices(
            store=store,
            journal=journal,
            scheduler=scheduler,
            pool=pool,
            router=router,
            sink=sink,
            approvals=ApprovalStore(str(db), cross_thread=True),
            webhook_secrets=secrets or None,
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


def _run_discord(args: argparse.Namespace) -> int:
    """Host the Discord channel: outbound sink + command gateway (ADR-024)."""
    token = discord_load_token()
    if token is None:
        print(
            f"FAILED: {DISCORD_TOKEN_ENV_VAR} is not set — refusing to start "
            "the Discord channel (fail-fast, no silent no-op)",
            file=sys.stderr,
        )
        return 1
    channel_ids = discord_load_channel_ids()
    guild_ids = discord_load_guild_ids()
    config = load_config()
    bootstrap_runtime(config)
    db = config.home / "runtime.sqlite"
    journal = EventJournal(db, cross_thread=True)
    store = TaskStore(db, journal=journal, cross_thread=True)
    approvals = ApprovalStore(str(db), cross_thread=True)
    client = DiscordClient(token)
    sink = DiscordSink(DiscordSinkConfig(client=client, journal=journal, channel_ids=channel_ids))
    router = NotificationRouter(journal=journal)
    router.subscribe(SubscriberPolicy(subscriber="discord"), sink)
    scheduler = Scheduler(store=store, journal=journal)
    try:
        asyncio.run(
            _discord_channel(
                token=token,
                client=client,
                store=store,
                approvals=approvals,
                journal=journal,
                scheduler=scheduler,
                channel_ids=channel_ids,
                guild_ids=guild_ids,
                listen=not args.no_listen,
            )
        )
        return 0
    finally:
        store.close()
        journal.close()
        approvals.close()


async def _discord_channel(
    *,
    token: str,
    client: DiscordClient,
    store: TaskStore,
    approvals: ApprovalStore,
    journal: EventJournal,
    scheduler: Scheduler,
    channel_ids: frozenset[str],
    guild_ids: frozenset[str],
    listen: bool,
) -> None:
    """One loop: startup verification, scheduler ticks, inbound gateway."""
    from xenopus.gateway.discord import DiscordGatewayHost

    me = await client.get_me()
    username = me.get("username", "bot")
    print(f"xenopus discord channel online: {username} (ctrl+c to stop)")
    if not listen:
        try:
            while True:
                scheduler.tick()
                await asyncio.sleep(1.0)
        finally:
            await client.aclose()
        return
    commander = DiscordCommander(
        store=store,
        approvals=approvals,
        journal=journal,
        killswitch=KillSwitch(store=store, journal=journal),
        channel_ids=channel_ids,
        guild_ids=guild_ids,
        responder=RestResponder(client=client, journal=journal),
    )
    host = DiscordGatewayHost(
        token=token,
        commander=commander,
        journal=journal,
        scheduler_tick=scheduler.tick,
    )
    try:
        await host.run()
    finally:
        await client.aclose()


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
    if args.command == "discord":
        return _run_discord(args)
    if args.command == "reflect":
        return _run_reflect()
    if args.command == "resident":
        return _run_resident(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
