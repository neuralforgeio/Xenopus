"""Live integration test: Xenopus engines against a real llama.cpp model.

Composes the FULL stack — GoalManager -> PlanEngine (DAG) ->
Orchestrator -> AgentPool -> live OpenAI-compat provider (llama.cpp
server at 127.0.0.1:8080) -> ToolGateway -> file tools — and runs a
3-node project-scaffold plan whose nodes call the MODEL to decide
file content, then write real files into the workspace.

Workspace: C:/Users/Dearly Febriano/xenopus-test-projects
This is a manual verification script (not part of the CI suite).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from xenopus.observability.correlation import new_correlation_id
from xenopus.persistence.journal import EventJournal
from xenopus.persistence.reliability import ReliabilityStore
from xenopus.provider.openai_compat import OpenAICompatProvider
from xenopus.provider.protocol import ModelProvider
from xenopus.provider.types import CompletionRequest, Message, Role
from xenopus.runtime.agent import AgentContract, AgentProfile, AgentResult
from xenopus.runtime.agent_pool import AgentPool, PoolLimits
from xenopus.runtime.approval import ApprovalLedger
from xenopus.runtime.goal import GoalDraft, GoalManager
from xenopus.runtime.observer import ObservationLog
from xenopus.runtime.orchestrator import Orchestrator
from xenopus.runtime.permission import PermissionEngine
from xenopus.runtime.plan import PlanEngine, TaskNode
from xenopus.runtime.risk import RiskEngine
from xenopus.tools.files import PathPolicy
from xenopus.tools.gateway import ToolGateway
from xenopus.tools.registry import ToolRegistry

LLAMA_BASE_URL = "http://127.0.0.1:8080/v1"
LLAMA_MODEL = "./Ornith-1.5-9B-Q4_K_M.gguf"
WORKSPACE = Path(r"C:\Users\Dearly Febriano\xenopus-test-projects")
PROJECT_NAME = "demo-showcase"
PROJECT_ROOT = WORKSPACE / PROJECT_NAME


async def make_live_runner(
    provider: ModelProvider,
    gateway: ToolGateway,
    executor: object,
    path_policy: PathPolicy,
) -> object:
    """Build the agent runner: model call -> tool execution -> result."""

    async def runner(contract: AgentContract, profile: AgentProfile) -> AgentResult:
        correlation = f"live-{new_correlation_id()}"
        try:
            request = CompletionRequest(
                model=LLAMA_MODEL,
                messages=(
                    Message(
                        role=Role.SYSTEM,
                        content=(
                            "You are a software project scaffolding agent. "
                            "Reply with ONLY the requested file content. "
                            "No markdown fences, no commentary."
                        ),
                    ),
                    Message(
                        role=Role.USER,
                        content=(
                            f"Task: {contract.mission}\n"
                            "Produce the exact text content for this file."
                        ),
                    ),
                ),
                temperature=0.4,
                max_output_tokens=2048,
                correlation_id=correlation,
            )
            response = await provider.complete(request)
            content = response.content.strip()
            if not content:
                return AgentResult.failed(
                    task_id=contract.task_id,
                    role=profile.role,
                    error="model returned empty content",
                    confidence=0.0,
                )
            # The orchestrator composes inputs; the mission names the file.
            import re

            match = re.search(r"\b([A-Z][A-Z0-9_]*\.(?:md|txt|py|json))\b", contract.mission)
            if match is None:
                return AgentResult.failed(
                    task_id=contract.task_id,
                    role=profile.role,
                    error=f"mission names no file: {contract.mission!r}",
                    confidence=0.0,
                )
            relative = match.group(1)
            result = await executor.execute(  # type: ignore[attr-defined]
                "file_write",
                {"path": relative, "content": content},
                correlation_id=correlation,
                path_policy=path_policy,
            )
            if not result.ok:
                return AgentResult.failed(
                    task_id=contract.task_id,
                    role=profile.role,
                    error=f"file_write failed: {result.error_code}",
                    confidence=0.0,
                )
            return AgentResult.completed(
                task_id=contract.task_id,
                role=profile.role,
                summary=f"wrote {relative} ({result.data.get('bytes_written')} bytes)",
                artifacts={"path": relative, "bytes": result.data.get("bytes_written")},
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

    return runner


async def main() -> int:
    print("=" * 70)
    print("XENOPUS LIVE INTEGRATION TEST — llama.cpp (Ornith-1.5-9B)")
    print("=" * 70)

    # -- engines -----------------------------------------------------------
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    journal = EventJournal(WORKSPACE / "runtime.sqlite")
    reliability = ReliabilityStore(str(WORKSPACE / "runtime.sqlite"))
    observations = ObservationLog()
    registry = ToolRegistry()
    registry.register_builtin_files()
    permissions = PermissionEngine()  # deny-by-default -> allow the file tools
    from xenopus.runtime.permission import PermissionDecision, PermissionRule

    permissions.add_rule(
        PermissionRule(decision=PermissionDecision.ALLOW, subject="agent", action="files.read")
    )
    permissions.add_rule(
        PermissionRule(decision=PermissionDecision.ALLOW, subject="agent", action="files.write")
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

    from xenopus.runtime.executor import Executor

    executor = Executor(
        gateway=gateway,
        journal=journal,
        observations=observations,
        reliability=reliability,
    )
    path_policy = PathPolicy(PROJECT_ROOT)

    provider = OpenAICompatProvider(
        base_url=LLAMA_BASE_URL,
        api_key="llamacpp-no-key",
        model=LLAMA_MODEL,
        name="llamacpp",
        timeout_seconds=300.0,
    )

    # -- plan: 3-node DAG (two independent docs + a README that depends) ---
    goals = GoalManager()
    goal = goals.create(
        GoalDraft(
            objective="Scaffold a small demo project with model-written docs",
            success_criteria=["README.md exists", "docs exist"],
        )
    )
    goals.activate(goal.id)
    nodes = [
        TaskNode(
            id="architecture", summary="Write ARCHITECTURE.md describing a layered CLI design"
        ),
        TaskNode(id="usage", summary="Write USAGE.md with install and run instructions"),
        TaskNode(
            id="readme",
            summary="Write README.md referencing the two docs",
            dependencies=frozenset({"architecture", "usage"}),
        ),
    ]
    plan = PlanEngine(goals).create(goal.id, nodes)

    runner = await make_live_runner(provider, gateway, executor, path_policy)
    orchestrator = Orchestrator(
        pool=AgentPool(limits=PoolLimits(max_concurrent=2, max_total=6)),
        runner=runner,
    )

    correlation = f"live-run-{new_correlation_id()}"
    print(f"\nplan: {len(plan.nodes)} nodes; correlation: {correlation}")
    print(f"workspace: {PROJECT_ROOT}\n")

    report = await orchestrator.execute(plan, correlation_id=correlation, force_sequential=False)

    # -- verdict ------------------------------------------------------------
    print("\n" + "=" * 70)
    print(f"ORCHESTRATION: {report.overall} (confidence {report.confidence:.2f})")
    for layer in report.layers:
        for result in layer.results:
            status = result.status
            summary = result.summary or "; ".join(result.errors) or "no summary"
            print(f"  [{status}] {result.task_id}: {summary[:100]}")

    files = sorted(p.name for p in PROJECT_ROOT.iterdir()) if PROJECT_ROOT.exists() else []
    print(f"\nfiles created: {files}")
    for name in files:
        path = PROJECT_ROOT / name
        print(f"  {name}: {path.stat().st_size} bytes")

    # -- journal + reliability evidence --------------------------------------
    events = journal.events_for(correlation)
    print(f"\njournal events for this run: {len(events)}")
    tool_stats = reliability.stats_for(kind="tool", subject="file_write")
    rate = tool_stats.success_rate
    print(f"file_write reliability: {tool_stats.invocations} invocations, success rate {rate:.2f}")

    journal.close()
    reliability.close()

    if report.overall == "COMPLETED" and len(files) >= 3:
        print("\nLIVE TEST VERDICT: PASS")
        return 0
    print("\nLIVE TEST VERDICT: INCOMPLETE — see above")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
