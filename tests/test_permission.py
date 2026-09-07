"""Permission engine tests: rule order, deny-by-default, dimensions."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from xenopus.runtime.permission import (
    PermissionDecision,
    PermissionDeniedError,
    PermissionEngine,
    PermissionRequest,
    PermissionRule,
)


def request(
    subject: str = "agent",
    action: str = "files.read",
    resource: str | None = None,
    tool: str | None = None,
) -> PermissionRequest:
    return PermissionRequest(subject=subject, action=action, resource=resource, tool=tool)


class TestPermissionEngine:
    def test_empty_ruleset_denies_everything(self) -> None:
        engine = PermissionEngine()
        assert engine.decide(request()) is PermissionDecision.DENY

    def test_allow_rule_matches(self) -> None:
        engine = PermissionEngine(
            [PermissionRule(decision=PermissionDecision.ALLOW, action="files.read")]
        )
        assert engine.decide(request()) is PermissionDecision.ALLOW

    def test_first_match_wins(self) -> None:
        engine = PermissionEngine(
            [
                PermissionRule(decision=PermissionDecision.DENY, subject="bad"),
                PermissionRule(decision=PermissionDecision.ALLOW, action="files.read"),
            ]
        )
        assert engine.decide(request(subject="bad")) is PermissionDecision.DENY
        assert engine.decide(request(subject="ok")) is PermissionDecision.ALLOW

    def test_check_raises_on_deny(self) -> None:
        engine = PermissionEngine()
        with pytest.raises(PermissionDeniedError, match="files.read"):
            engine.check(request())

    def test_rule_field_mismatch_does_not_apply(self) -> None:
        engine = PermissionEngine(
            [
                PermissionRule(
                    decision=PermissionDecision.ALLOW,
                    subject="agent",
                    action="files.read",
                    tool="file_read",
                )
            ]
        )
        assert engine.decide(request(tool="file_read")) is PermissionDecision.ALLOW
        assert engine.decide(request(tool="file_write")) is PermissionDecision.DENY

    def test_prepend_takes_precedence(self) -> None:
        engine = PermissionEngine([PermissionRule(decision=PermissionDecision.ALLOW, action="x")])
        engine.add_rule(PermissionRule(decision=PermissionDecision.DENY, action="x"), prepend=True)
        assert engine.decide(request(action="x")) is PermissionDecision.DENY


class TestPermissionProperty:
    @given(
        subject=st.text(min_size=0, max_size=12),
        action=st.text(min_size=0, max_size=12),
    )
    def test_empty_engine_is_always_deny(self, subject: str, action: str) -> None:
        """Deny-by-default holds for ANY request shape."""
        engine = PermissionEngine()
        assert engine.decide(request(subject=subject, action=action)) is (PermissionDecision.DENY)
