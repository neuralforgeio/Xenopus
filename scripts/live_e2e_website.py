"""Supervised live E2E: the agent builds a Next.js site via npx (ADR-029).

The full runtime stack runs against a REAL model (tokenrouter,
z-ai/glm-5.3-free): GoalManager -> PlanEngine -> Orchestrator ->
AgentPool -> provider -> ToolGateway -> terminal_run (allow-listed,
cwd-bounded). Every external-effect tool call follows the REAL
approval flow; this harness grants each request and logs the grant
(the human-in-the-loop stand-in while the operator supervises).

Workspace: C:/Users/Dearly Febriano/Documents/xenopus-agent-web
API key: XENOPUS_TOKENROUTER_KEY env var (never hardcoded here).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from xenopus.observability.correlation import new_correlation_id
from xenopus.persistence.journal import EventJournal
from xenopus.persistence.reliability import ReliabilityStore
from xenopus.provider.openai_compat import OpenAICompatProvider
from xenopus.provider.types import CompletionRequest, Message, Role
from xenopus.runtime.agent import AgentContract, AgentProfile, AgentResult
from xenopus.runtime.agent_pool import AgentPool, PoolLimits
from xenopus.runtime.approval import ApprovalLedger
from xenopus.runtime.executor import Executor
from xenopus.runtime.goal import GoalDraft, GoalManager
from xenopus.runtime.observer import ObservationLog
from xenopus.runtime.orchestrator import Orchestrator
from xenopus.runtime.permission import PermissionDecision, PermissionEngine, PermissionRule
from xenopus.runtime.plan import PlanEngine, TaskNode
from xenopus.runtime.risk import RiskEngine
from xenopus.tools.files import PathPolicy
from xenopus.tools.gateway import ToolGateway
from xenopus.tools.registry import ToolRegistry

API_BASE = "https://api.tokenrouter.com/v1"
MODEL = "z-ai/glm-5.3-free"
DOCS = Path.home() / "Documents"
WORKSPACE = DOCS / "xenopus-agent-web"
PROJECT_DIR = WORKSPACE / "my-nextjs-site"
STEP_FILE = WORKSPACE / "agent-steps.json"

approvals_log: list[dict[str, Any]] = []


def ask_model_sync(messages: tuple[Message, ...], *, temperature: float = 0.2) -> str:
    """One synchronous model call (helper for harness-level decisions)."""
    key = os.environ.get("XENOPUS_TOKENROUTER_KEY", "")
    provider = OpenAICompatProvider(
        base_url=API_BASE,
        api_key=key,
        model=MODEL,
        name="tokenrouter",
        timeout_seconds=300.0,
    )
    request = CompletionRequest(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        max_output_tokens=2048,
    )
    return asyncio.run(provider.complete(request)).content.strip()


async def main() -> int:
    print("=" * 72)
    print("XENOPUS SUPERVISED E2E — agent builds a Next.js website via npx")
    print(f"provider: tokenrouter / {MODEL}")
    print(f"workspace: {WORKSPACE}")
    print("=" * 72)

    key = os.environ.get("XENOPUS_TOKENROUTER_KEY", "")
    if not key:
        print("FAILED: XENOPUS_TOKENROUTER_KEY is not set (supervisor session env)")
        return 1
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    # -- engines ------------------------------------------------------------
    journal = EventJournal(WORKSPACE / "runtime.sqlite")
    reliability = ReliabilityStore(str(WORKSPACE / "runtime.sqlite"))
    observations = ObservationLog()
    registry = ToolRegistry()
    registry.register_builtin_files()
    registry.register_builtin_terminal()
    permissions = PermissionEngine()
    permissions.add_rule(
        PermissionRule(decision=PermissionDecision.ALLOW, subject="agent", action="files.read")
    )
    permissions.add_rule(
        PermissionRule(decision=PermissionDecision.ALLOW, subject="agent", action="files.write")
    )
    permissions.add_rule(
        PermissionRule(decision=PermissionDecision.ALLOW, subject="agent", action="terminal.run")
    )
    risk = RiskEngine()
    approvals = ApprovalLedger()
    gateway = ToolGateway(
        registry=registry,
        permissions=permissions,
        risk=risk,
        approvals=approvals,
        subject="agent",
    )
    executor = Executor(
        gateway=gateway,
        journal=journal,
        observations=observations,
        reliability=reliability,
    )
    path_policy = PathPolicy(WORKSPACE)

    provider = OpenAICompatProvider(
        base_url=API_BASE,
        api_key=key,
        model=MODEL,
        name="tokenrouter",
        timeout_seconds=600.0,
    )

    # -- the agent runner: model decides argv; gateway+approvals govern ------
    async def runner(contract: AgentContract, profile: AgentProfile) -> AgentResult:
        correlation = f"e2e-{new_correlation_id()}"
        try:
            request = CompletionRequest(
                model=MODEL,
                messages=(
                    Message(
                        role=Role.SYSTEM,
                        content=(
                            "You are a build agent controlling a terminal on Windows. "
                            "Available commands: node, npm, npx, git. Reply with ONE "
                            "JSON object only, no markdown fences, with keys: "
                            '"command" (string), "args" (list of strings), '
                            '"description" (what this step does). '
                            "Think briefly; keep the JSON under 30 words."
                        ),
                    ),
                    Message(role=Role.USER, content=contract.mission),
                ),
                temperature=0.2,
                max_output_tokens=4096,
            )
            raw = ""
            for attempt in (1, 2):  # reasoning models can spend a budget on thinking
                response = await provider.complete(request)
                raw = response.content.strip()
                if raw:
                    break
                print(f"    [model] attempt {attempt}: empty content, retrying")
            if not raw:
                return AgentResult.failed(
                    task_id=contract.task_id,
                    role=profile.role,
                    error="model returned empty content twice",
                    confidence=0.0,
                )
            try:
                parsed = json.loads(re.sub(r"^```(?:json)?|```$", "", raw, flags=re.M).strip())
            except json.JSONDecodeError:
                return AgentResult.failed(
                    task_id=contract.task_id,
                    role=profile.role,
                    error=f"model returned non-JSON: {raw[:120]!r}",
                    confidence=0.0,
                )
            command = str(parsed.get("command", ""))
            args = [str(a) for a in parsed.get("args", [])]
            print(f"    [agent] {contract.task_id}: {command} {' '.join(args)}")

            arguments: dict[str, Any] = {
                "command": command,
                "args": args,
                "cwd": str(PROJECT_DIR.relative_to(WORKSPACE)) if PROJECT_DIR.exists() else "",
            }
            result = await executor.execute(
                "terminal_run",
                arguments,
                correlation_id=correlation,
                path_policy=path_policy,
            )
            if result.error_code == "approval_required":
                request_id = result.error_message.split("approval ")[1].split(" ")[0]
                print(f"    [approval] {request_id} for {command} — SUPERVISOR GRANTS")
                approvals.grant(request_id)
                result = await executor.execute(
                    "terminal_run",
                    arguments,
                    correlation_id=correlation,
                    approval_id=request_id,
                    path_policy=path_policy,
                )
            if not result.ok:
                return AgentResult.failed(
                    task_id=contract.task_id,
                    role=profile.role,
                    error=(f"terminal failed ({result.error_code}): {result.error_message[-300:]}"),
                    confidence=0.0,
                )
            output = result.data.get("output", "")
            print(f"    [terminal] exit {result.data['returncode']}: {output[-200:]!r}")
            return AgentResult.completed(
                task_id=contract.task_id,
                role=profile.role,
                summary=f"{command} exit {result.data['returncode']}",
                artifacts={"command": command, "args": args},
                evidence=(correlation,),
                confidence=0.9,
            )
        except Exception as err:
            return AgentResult.failed(
                task_id=contract.task_id,
                role=profile.role,
                error=f"runner exception: {err}",
                confidence=0.0,
            )

    # -- plan: scaffold Next.js via npx, then verify -------------------------
    goals = GoalManager()
    goal = goals.create(
        GoalDraft(
            objective="Build a Next.js website in ~/Documents via npx (latest)",
            success_criteria=["scaffold complete", "dev build verified"],
        )
    )
    goals.activate(goal.id)
    nodes = [
        TaskNode(
            id="scaffold",
            summary=(
                "Scaffold a Next.js project using npx create-next-app with the "
                "latest version, non-interactively, TypeScript, no ESLint, "
                "no Tailwind, src directory, import alias '@/*', app router. "
                "Command: npx. Target directory name: my-nextjs-site. "
                "Run in the workspace root."
            ),
        ),
        TaskNode(
            id="verify",
            summary=(
                "Inside the my-nextjs-site directory, run a production build "
                "to verify the scaffold (npm run build)."
            ),
            dependencies=frozenset({"scaffold"}),
        ),
    ]
    plan = PlanEngine(goals).create(goal.id, nodes)
    orchestrator = Orchestrator(
        pool=AgentPool(limits=PoolLimits(max_concurrent=2, max_total=6)),
        runner=runner,
    )

    correlation = f"e2e-run-{new_correlation_id()}"
    print(f"\nplan: {len(plan.nodes)} nodes; correlation: {correlation}\n")
    report = await orchestrator.execute(plan, correlation_id=correlation, force_sequential=True)

    # -- verdict ---------------------------------------------------------------
    print("\n" + "=" * 72)
    print(f"ORCHESTRATION: {report.overall} (confidence {report.confidence:.2f})")
    for layer in report.layers:
        for result in layer.results:
            summary = result.summary or "; ".join(result.errors) or "no summary"
            print(f"  [{result.status}] {result.task_id}: {summary[:120]}")

    pkg = PROJECT_DIR / "package.json"
    scaffold_ok = pkg.is_file()
    deps = ""
    if scaffold_ok:
        data = json.loads(pkg.read_text(encoding="utf-8"))
        deps = str(data.get("dependencies", {}))
        print(f"\npackage.json found: next in deps: {'next' in data.get('dependencies', {})}")
        print(f"dependencies: {deps[:200]}")
    build_marker = (PROJECT_DIR / ".next").exists()
    print(f"build output .next exists: {build_marker}")

    stats = reliability.stats_for(kind="tool", subject="terminal_run")
    print(
        f"terminal_run reliability: {stats.invocations} invocations,"
        f" success rate {stats.success_rate:.2f}"
    )
    events = journal.events_for(correlation)
    print(f"journal events this run: {len(events)}")

    journal.close()
    reliability.close()

    if report.overall == "COMPLETED" and scaffold_ok and build_marker:
        print("\nE2E VERDICT: PASS — Next.js site built by the agent via npx")
        return 0
    print("\nE2E VERDICT: INCOMPLETE — inspect the steps above")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
