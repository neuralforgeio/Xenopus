"""Package import tests — every module boundary must load without errors."""

import importlib

MODULES = [
    "xenopus",
    "xenopus.config",
    "xenopus.bootstrap",
    "xenopus.cli",
    "xenopus.runtime",
    "xenopus.runtime.budget",
    "xenopus.runtime.events",
    "xenopus.runtime.fsm",
    "xenopus.runtime.goal",
    "xenopus.runtime.plan",
    "xenopus.runtime.context",
    "xenopus.runtime.permission",
    "xenopus.runtime.risk",
    "xenopus.runtime.approval",
    "xenopus.runtime.observer",
    "xenopus.runtime.executor",
    "xenopus.runtime.verifier",
    "xenopus.persistence",
    "xenopus.persistence.journal",
    "xenopus.persistence.sessions",
    "xenopus.gateway",
    "xenopus.tools",
    "xenopus.tools.contracts",
    "xenopus.tools.files",
    "xenopus.tools.registry",
    "xenopus.tools.gateway",
    "xenopus.memory",
    "xenopus.memory.types",
    "xenopus.memory.store",
    "xenopus.skills",
    "xenopus.skills.types",
    "xenopus.skills.registry",
    "xenopus.persistence.checkpoints",
    "xenopus.persistence.tasks",
    "xenopus.persistence.approvals_store",
    "xenopus.tools.recycle",
    "xenopus.runtime.killswitch",
    "xenopus.runtime.recovery",
    "xenopus.runtime.agent",
    "xenopus.runtime.agent_pool",
    "xenopus.runtime.orchestrator",
    "xenopus.observability.logs",
    "xenopus.provider",
    "xenopus.provider.types",
    "xenopus.provider.protocol",
    "xenopus.provider.echo",
    "xenopus.provider.openai_compat",
    "xenopus.provider.registry",
    "xenopus.provider.health",
    "xenopus.provider.router",
    "xenopus.observability",
    "xenopus.observability.correlation",
]


def test_import_all_modules() -> None:
    """Every boundary module must be importable without errors."""
    for name in MODULES:
        importlib.import_module(name)


def test_version_available_and_not_unknown() -> None:
    """The package version must be read from metadata (not the 'unknown' fallback)."""
    import xenopus

    assert xenopus.__version__ != "unknown"
