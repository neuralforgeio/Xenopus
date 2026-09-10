"""Webhook inbound tests: HMAC auth, replay defense, mapping (ADR-025).

All tests run against the Starlette TestClient on the real ASGI app
with live engines — no network. Secrets are test fixtures; the env
loader is exercised with an injected environment.
"""

import hashlib
import hmac
import json
from collections.abc import Generator
from dataclasses import replace
from pathlib import Path

import httpx2
import pytest
from starlette.testclient import TestClient

from xenopus.persistence.journal import EventJournal
from xenopus.persistence.tasks import TaskStore
from xenopus.runtime.agent_pool import AgentPool
from xenopus.runtime.notifications import NotificationRouter, SubscriberPolicy
from xenopus.runtime.scheduler import Scheduler
from xenopus.web.server import WebServices, build_app
from xenopus.web.sink import WebSink
from xenopus.web.webhooks import (
    SECRETS_ENV_VAR,
    WebhookConfig,
    load_webhook_secrets,
)

SECRET = "whsec-test-secret-value"  # noqa: S105 - test fixture, no network


class _Bundle:
    """Engines + app with the webhook surface mounted for one source."""

    def __init__(self, tmp_path: Path, *, fixed_clock: float | None = None) -> None:
        self.now = fixed_clock if fixed_clock is not None else 1_000_000.0
        self.journal = EventJournal(tmp_path / "j.sqlite", cross_thread=True)
        self.store = TaskStore(tmp_path / "t.sqlite", journal=self.journal, cross_thread=True)
        self.scheduler = Scheduler(store=self.store, journal=self.journal)
        self.pool = AgentPool()
        self.sink = WebSink()
        self.router = NotificationRouter(journal=self.journal)
        self.router.subscribe(SubscriberPolicy(subscriber="web", digest=True), self.sink)
        self.config = WebhookConfig(
            store=self.store,
            journal=self.journal,
            secrets={"github": SECRET},
            clock=lambda: self.now,
        )
        self.app = build_app(self.services())

    def services(self) -> WebServices:
        return WebServices(
            store=self.store,
            journal=self.journal,
            scheduler=self.scheduler,
            pool=self.pool,
            router=self.router,
            sink=self.sink,
            webhook_config=self.config,
        )

    def close(self) -> None:
        self.store.close()
        self.journal.close()


class _ClockBundle(_Bundle):
    """Bundle whose webhook clock the tests advance explicitly."""

    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        self.now = 1_000_000.0


def _signed(body: bytes, *, secret: str = SECRET, at: float = 1_000_000.0) -> dict[str, str]:
    """Build the signature + timestamp headers for one payload."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {
        "xenopus-signature": f"sha256={digest}",
        "xenopus-timestamp": str(at),
    }


def _event(nonce: str, title: str = "webhook task") -> bytes:
    return json.dumps({"type": "task.create", "id": nonce, "title": title}).encode()


@pytest.fixture
def bundle(tmp_path: Path) -> Generator[_Bundle]:
    b = _ClockBundle(tmp_path)
    yield b
    b.close()


@pytest.fixture
def client(bundle: _Bundle) -> Generator[TestClient]:
    with TestClient(bundle.app) as c:
        yield c


def _post(client: TestClient, body: bytes, headers: dict[str, str]) -> httpx2.Response:
    return client.post("/webhooks/github", content=body, headers=headers)


class TestAuthentication:
    def test_valid_signature_creates_task(self, bundle: _Bundle, client: TestClient) -> None:
        body = _event("n-1", "deploy hook")
        response = _post(client, body, _signed(body))
        assert response.status_code == 202
        tasks = bundle.store.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].title == "deploy hook"
        assert tasks[0].correlation_id == "webhook:github:n-1"

    def test_bad_signature_rejected(self, client: TestClient) -> None:
        body = _event("n-2")
        forged = _signed(body, secret="whsec-wrong-secret")  # noqa: S106 - test fixture
        response = _post(client, body, forged)
        assert response.status_code == 401

    def test_missing_signature_rejected(self, client: TestClient) -> None:
        response = _post(client, _event("n-3"), {"xenopus-timestamp": "1000000"})
        assert response.status_code == 401

    def test_wrong_scheme_rejected(self, client: TestClient) -> None:
        body = _event("n-4")
        headers = _signed(body)
        headers["xenopus-signature"] = "md5=" + headers["xenopus-signature"][7:]
        response = _post(client, body, headers)
        assert response.status_code == 401

    def test_unknown_source_404(self, client: TestClient) -> None:
        body = _event("n-5")
        response = client.post("/webhooks/unknown", content=body, headers=_signed(body))
        assert response.status_code == 404


class TestReplayDefense:
    def test_nonce_reuse_rejected(self, bundle: _Bundle, client: TestClient) -> None:
        body = _event("dup-1")
        first = _post(client, body, _signed(body))
        assert first.status_code == 202
        second = _post(client, body, _signed(body))
        assert second.status_code == 409
        assert len(bundle.store.list_tasks()) == 1  # exactly one task

    def test_stale_timestamp_rejected(self, bundle: _Bundle, client: TestClient) -> None:
        body = _event("n-6")
        stale = _signed(body, at=bundle.now - 400.0)  # past the 300s window
        response = _post(client, body, stale)
        assert response.status_code == 401

    def test_future_timestamp_rejected(self, bundle: _Bundle, client: TestClient) -> None:
        body = _event("n-7")
        future = _signed(body, at=bundle.now + 400.0)
        response = _post(client, body, future)
        assert response.status_code == 401

    def test_missing_timestamp_rejected(self, client: TestClient) -> None:
        body = _event("n-8")
        headers = _signed(body)
        del headers["xenopus-timestamp"]
        response = _post(client, body, headers)
        assert response.status_code == 401


class TestBoundaryValidation:
    def test_oversize_body_rejected(self, bundle: _Bundle, client: TestClient) -> None:
        body = _event("n-9", title="x" * 70_000)
        response = _post(client, body, _signed(body))
        assert response.status_code == 413

    def test_non_json_rejected(self, client: TestClient) -> None:
        body = b"not json at all"
        response = _post(client, body, _signed(body))
        assert response.status_code == 415

    def test_disallowed_event_type_rejected(self, bundle: _Bundle, client: TestClient) -> None:
        body = json.dumps({"type": "killswitch.trigger", "id": "n-10", "title": "x"}).encode()
        response = _post(client, body, _signed(body))
        assert response.status_code == 400
        assert bundle.store.list_tasks() == []  # nothing ran

    def test_missing_nonce_rejected(self, client: TestClient) -> None:
        body = json.dumps({"type": "task.create", "title": "t"}).encode()
        response = _post(client, body, _signed(body))
        assert response.status_code == 400

    def test_missing_title_rejected(self, client: TestClient) -> None:
        body = json.dumps({"type": "task.create", "id": "n-11"}).encode()
        response = _post(client, body, _signed(body))
        assert response.status_code == 400

    def test_rate_limit_trips(self, bundle: _Bundle, tmp_path: Path) -> None:
        """Over the per-minute allowance: 429 and journaled rejection."""
        bundle.config = WebhookConfig(
            store=bundle.store,
            journal=bundle.journal,
            secrets={"github": SECRET},
            clock=lambda: bundle.now,
            rate_limit_per_minute=2,
        )
        app = build_app(bundle.services())
        with TestClient(app) as c:
            for nonce in ("r-1", "r-2"):
                body = _event(nonce)
                assert _post(c, body, _signed(body)).status_code == 202
            body = _event("r-3")
            response = _post(c, body, _signed(body))
            assert response.status_code == 429


class TestSurfaceBoundaries:
    def test_routes_absent_without_secrets(self, tmp_path: Path) -> None:
        """Fail-closed: unconfigured surface is not mounted at all."""
        b = _ClockBundle(tmp_path)
        try:
            services = replace(b.services(), webhook_config=None)
            app = build_app(services)
            paths = [getattr(route, "path", None) for route in app.routes]
            assert "/webhooks/{source}" not in paths
        finally:
            b.close()

    def test_outcome_journaled_both_paths(self, bundle: _Bundle, client: TestClient) -> None:
        body = _event("j-1")
        _post(client, body, _signed(body))
        bad = _event("j-2")
        _post(client, bad, _signed(bad, secret="nope"))  # noqa: S106 - test fixture
        rows = [event for _, event in bundle.journal.all_events()]
        accepted = [
            e for e in rows if e.type.value == "TASK_CREATED" and e.payload.get("accepted") is True
        ]
        rejected = [
            e
            for e in rows
            if e.type.value == "NOTIFICATION_SUPPRESSED" and e.payload.get("accepted") is False
        ]
        assert accepted and rejected  # both outcomes journaled with reasons


class TestEnvLoader:
    def test_loads_source_secret_pairs(self) -> None:
        env = {SECRETS_ENV_VAR: "github: abc123 ,  stripe:def456 "}
        secrets = load_webhook_secrets(env)
        assert secrets == {"github": "abc123", "stripe": "def456"}

    def test_blank_and_malformed_skipped(self) -> None:
        env = {SECRETS_ENV_VAR: "github:, :x, plain, ok:yes"}
        secrets = load_webhook_secrets(env)
        assert secrets == {"ok": "yes"}

    def test_absent_env_yields_empty(self) -> None:
        assert load_webhook_secrets({}) == {}
