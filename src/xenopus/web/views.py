"""Dashboard HTML rendering — escaped, server-side, stdlib-only.

Every engine string (task titles, subjects, reasons...) is UNTRUSTED
content: it is passed through ``html.escape`` at EVERY interpolation
(Protocol 11.1 XSS gate; no template engine, no exceptions). Rows and
pages are pure functions over engine records — no engine calls here.
"""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from xenopus.persistence.approvals_store import ApprovalRow
from xenopus.persistence.tasks import TaskRecord
from xenopus.runtime.scheduler import ScheduleEntry


def _esc(value: object, *, quote: bool = True) -> str:
    """Escape any value for HTML text/attribute content."""
    return escape(str(value), quote=quote)


def page(title: str, body: str) -> str:
    """Minimal HTML5 document shell for one dashboard page."""
    return (
        "<!doctype html>"
        "<html lang='en'>"
        "<head><meta charset='utf-8'>"
        f"<meta http-equiv='refresh' content='5'>"
        f"<title>{_esc(title)}</title></head>"
        "<body>"
        "<nav>"
        "<a href='/'>tasks</a> | "
        "<a href='/tasks/new'>new task</a> | "
        "<a href='/approvals'>approvals</a> | "
        "<a href='/schedules'>schedules</a> | "
        "<a href='/agents'>agents</a> | "
        "<a href='/events'>events</a> | "
        "<a href='/killswitch'>killswitch</a>"
        "</nav>"
        "<hr>"
        f"{body}"
        "</body></html>"
    )


def table(headers: tuple[str, ...], rows: Sequence[tuple[str, ...]]) -> str:
    """Render an escaped table; empty placeholder when no rows."""
    if not rows:
        return "<p>(none)</p>"
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{_esc(cell)}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f"<table border='1'><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def tasks_page(records: list[TaskRecord]) -> str:
    """Tasks panel: id, state, attempts, title, inspect link."""
    body_rows = "".join(
        "<tr>"
        f"<td>{_esc(r.task_id)}</td>"
        f"<td>{_esc(r.state.value)}</td>"
        f"<td>{_esc(r.attempt)}/{_esc(r.max_attempts)}</td>"
        f"<td>{_esc(r.title)}</td>"
        f"<td><a href='/tasks/{_esc(r.task_id)}'>inspect</a></td>"
        "</tr>"
        for r in records
    )
    return page(
        "Xenopus — tasks",
        "<h1>Tasks</h1><table border='1'><thead><tr>"
        "<th>task</th><th>state</th><th>attempt</th><th>title</th><th></th>"
        f"</tr></thead><tbody>{body_rows}</tbody></table>",
    )


def task_detail(record: TaskRecord) -> str:
    """One task's full record (escaped key/value list)."""
    fields = [
        ("task", record.task_id),
        ("title", record.title),
        ("state", record.state.value),
        ("attempt", f"{record.attempt}/{record.max_attempts}"),
        ("goal", record.goal_ref or "-"),
        ("plan", record.plan_ref or "-"),
        ("corr", record.correlation_id),
        ("created", record.created_at),
        ("updated", record.updated_at),
    ]
    body = "<h1>Task</h1><dl>" + "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>" for k, v in fields)
    return page("Xenopus — task", body + "</dl><p><a href='/'>back</a></p>")


def approvals_page(rows: list[ApprovalRow], *, csrf_token: str, error: str | None = None) -> str:
    """Pending approvals with grant/deny forms (CSRF-bound POST)."""
    body = ["<h1>Approvals</h1>"]
    if error:
        body.append(f"<p style='color:red'>{_esc(error)}</p>")
    if not rows:
        body.append("<p>(none)</p>")
    else:
        for row in rows:
            body.append(
                "<div class='approval' style='border:1px solid; margin:1em; padding:0.5em'>"
                f"<p><b>{_esc(row.request_id)}</b>: {_esc(row.tool)} on {_esc(row.subject)}</p>"
                f"<p>reason: {_esc(row.reason)}</p>"
                f"<p>expires: {_esc(row.expires_at)}</p>"
                f"<form method='post' action='/approvals/{_esc(row.request_id)}/grant'>"
                f"<input type='hidden' name='csrf' value='{_esc(csrf_token)}'>"
                "<button type='submit'>grant</button></form>"
                f"<form method='post' action='/approvals/{_esc(row.request_id)}/deny'>"
                f"<input type='hidden' name='csrf' value='{_esc(csrf_token)}'>"
                "<button type='submit'>deny</button></form>"
                "</div>"
            )
    return page("Xenopus — approvals", "".join(body))


def schedules_page(entries: list[ScheduleEntry]) -> str:
    """Schedules panel."""
    headers = ("schedule", "kind", "name", "enabled", "max runs")
    rows = [
        (
            e.schedule_id,
            e.kind.value,
            e.name,
            str(e.enabled),
            str(e.max_executions) if e.max_executions is not None else "unbounded",
        )
        for e in entries
    ]
    return page("Xenopus — schedules", f"<h1>Schedules</h1>{table(headers, rows)}")


def agents_page(rows: list[tuple[str, str, str, str]]) -> str:
    """Agent health panel (watchdog view)."""
    headers = ("run", "health", "task", "heartbeat")
    return page("Xenopus — agents", f"<h1>Agents</h1>{table(headers, rows)}")


def events_page(lines: list[str]) -> str:
    """Recent router-approved notifications (escaped feed)."""
    body = "".join(f"<li>{_esc(line)}</li>" for line in lines)
    return page("Xenopus — events", f"<h1>Events</h1><ul>{body}</ul>")


def killswitch_page(
    *, live_count: int, csrf_token: str, done: bool = False, error: str | None = None
) -> str:
    """Killswitch confirm page — POST-only mutation, CSRF-bound."""
    body = ["<h1>Killswitch</h1>"]
    if error:
        body.append(f"<p style='color:red'>{_esc(error)}</p>")
    if done:
        body.append(f"<p>killswitch triggered; {_esc(live_count)} live task(s) were running.</p>")
    else:
        body.append(
            f"<p>{_esc(live_count)} live task(s). Cancel ALL live tasks?</p>"
            "<form method='post' action='/killswitch'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf_token)}'>"
            "<button type='submit'>cancel all live tasks</button></form>"
        )
    return page("Xenopus — killswitch", "".join(body))


def new_task_page(*, csrf_token: str, done_id: str | None = None) -> str:
    """New-task form (CSRF-bound POST)."""
    body = ["<h1>New task</h1>"]
    if done_id:
        body.append(f"<p>created: {_esc(done_id)}</p>")
    body.append(
        "<form method='post' action='/tasks/new'>"
        f"<input type='hidden' name='csrf' value='{_esc(csrf_token)}'>"
        "<input type='text' name='title' maxlength='200' required>"
        "<button type='submit'>create</button></form>"
    )
    return page("Xenopus — new task", "".join(body))
