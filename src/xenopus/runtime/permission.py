"""Permission engine: ALLOW / ASK / DENY across declared dimensions.

Deny-by-default. Every dimension (tool, resource, path, workspace,
agent, environment) contributes a decision; the most restrictive wins
(master prompt 68). Rules are explicit data — no heuristic inference.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PermissionDecision(StrEnum):
    """Per-dimension decisions; the engine combines them deterministically."""

    ALLOW = "ALLOW"
    ASK = "ASK"
    DENY = "DENY"


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """One permission query: a subject requesting an action on a resource.

    Contract:
        subject: the acting identity (agent id or 'user').
        action: permission dimension being exercised (e.g. 'files.write').
        resource: optional resource id (path, session, provider).
        tool: optional tool name for tool-scoped rules.
    """

    subject: str
    action: str
    resource: str | None = None
    tool: str | None = None


@dataclass(frozen=True, slots=True)
class PermissionRule:
    """One explicit rule. First match wins; rules are ordered.

    A rule matches when every non-None field matches the request.
    An all-None rule matches everything (use sparingly — the default
    rule set ends with an explicit DENY-all fallback).
    """

    decision: PermissionDecision
    subject: str | None = None
    action: str | None = None
    resource: str | None = None
    tool: str | None = None

    def matches(self, request: PermissionRequest) -> bool:
        """True when this rule applies to the request."""
        if self.subject is not None and self.subject != request.subject:
            return False
        if self.action is not None and self.action != request.action:
            return False
        if self.resource is not None and self.resource != request.resource:
            return False
        return self.tool is None or self.tool == request.tool


class PermissionDeniedError(Exception):
    """Raised (or returned as DENY) when a request is not permitted."""

    def __init__(self, request: PermissionRequest) -> None:
        self.request = request
        super().__init__(
            f"Permission DENIED for {request.subject!r} -> {request.action!r}"
            f" (resource={request.resource!r}, tool={request.tool!r})"
        )


class PermissionEngine:
    """Rule-ordered permission evaluation with a DENY-all fallback.

    Contract:
        decide(): evaluates rules first-match-wins; with no rules at all
        the engine DENIES (deny-by-default, master prompt 68).
        check(): same evaluation, raising PermissionDeniedError on DENY.

    Invariants:
        the fallback decision is always DENY — an empty rule set denies
        everything, which property tests pin.
    """

    def __init__(self, rules: list[PermissionRule] | None = None) -> None:
        self._rules = list(rules) if rules else []

    def decide(self, request: PermissionRequest) -> PermissionDecision:
        """First matching rule wins; no match -> DENY."""
        for rule in self._rules:
            if rule.matches(request):
                return rule.decision
        return PermissionDecision.DENY

    def check(self, request: PermissionRequest) -> None:
        """Raise PermissionDeniedError unless the decision is ALLOW."""
        if self.decide(request) is not PermissionDecision.ALLOW:
            raise PermissionDeniedError(request)

    def add_rule(self, rule: PermissionRule, *, prepend: bool = False) -> None:
        """Append (default) or prepend a rule at runtime."""
        if prepend:
            self._rules.insert(0, rule)
        else:
            self._rules.append(rule)
