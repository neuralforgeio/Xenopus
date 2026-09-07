"""Checkpoint manager + structured logger tests."""

import json
from collections.abc import Generator
from io import StringIO
from pathlib import Path

import pytest

from xenopus.observability.logs import LogLevel, StructuredLogger, redact
from xenopus.persistence.checkpoints import CheckpointError, CheckpointManager


@pytest.fixture
def checkpoints(tmp_path: Path) -> Generator[CheckpointManager]:
    mgr = CheckpointManager(tmp_path / "checkpoints.sqlite")
    yield mgr
    mgr.close()


class TestCheckpointManager:
    def test_create_and_get_round_trip(self, checkpoints: CheckpointManager) -> None:
        cp = checkpoints.create(
            correlation_id="corr-1",
            label="before-execution",
            fsm_state="READY",
            snapshot={"task": "t1", "plan": "p1", "tool_calls": 3},
        )
        loaded = checkpoints.get(cp.checkpoint_id)
        assert loaded.snapshot == {"task": "t1", "plan": "p1", "tool_calls": 3}
        assert loaded.fsm_state == "READY"

    def test_latest_for_returns_newest(self, checkpoints: CheckpointManager) -> None:
        first = checkpoints.create(correlation_id="c", label="a", fsm_state="IDLE", snapshot={})
        checkpoints.create(correlation_id="c", label="b", fsm_state="READY", snapshot={})
        latest = checkpoints.latest_for("c")
        assert latest is not None and latest.checkpoint_id != first.checkpoint_id
        assert latest.label == "b"

    def test_list_for_chronological(self, checkpoints: CheckpointManager) -> None:
        checkpoints.create(correlation_id="c", label="first", fsm_state="A", snapshot={})
        checkpoints.create(correlation_id="c", label="second", fsm_state="B", snapshot={})
        labels = [cp.label for cp in checkpoints.list_for("c")]
        assert labels == ["first", "second"]

    def test_latest_for_unknown_returns_none(self, checkpoints: CheckpointManager) -> None:
        assert checkpoints.latest_for("ghost") is None

    def test_non_serializable_snapshot_refused(self, checkpoints: CheckpointManager) -> None:
        with pytest.raises(CheckpointError, match="not JSON-serializable"):
            checkpoints.create(
                correlation_id="c",
                label="bad",
                fsm_state="A",
                snapshot={"oops": object()},
            )

    def test_empty_label_refused(self, checkpoints: CheckpointManager) -> None:
        with pytest.raises(CheckpointError, match="non-empty"):
            checkpoints.create(correlation_id="c", label="  ", fsm_state="A", snapshot={})

    def test_unknown_checkpoint_raises(self, checkpoints: CheckpointManager) -> None:
        with pytest.raises(CheckpointError, match="unknown checkpoint"):
            checkpoints.get("ckpt-nope")


class TestRedaction:
    def test_github_token_redacted(self) -> None:
        assert redact("token ghp_AbCdEf0123456789AbCdEf01") == "[REDACTED]"

    def test_api_key_value_redacted(self) -> None:
        assert redact("api_key = supersecretvalue123") == "[REDACTED]"

    def test_private_key_block_redacted(self) -> None:
        assert redact("-----BEGIN RSA PRIVATE KEY-----") == "[REDACTED]"

    def test_normal_text_passes(self) -> None:
        assert redact("all good here") == "all good here"

    def test_dict_secret_key_redacts_value(self) -> None:
        result = redact({"password": "hunter2x9", "note": "fine"})
        assert result["password"] == "[REDACTED]"  # noqa: S105 — test fixture string
        assert result["note"] == "fine"

    def test_nested_lists_redacted(self) -> None:
        result = redact({"logs": ["ok", "sk-abcdefghijklmnopqrst"]})
        assert result["logs"][1] == "[REDACTED]"

    def test_non_string_values_pass(self) -> None:
        assert redact({"count": 5, "ratio": 0.5}) == {"count": 5, "ratio": 0.5}


class TestStructuredLogger:
    def test_record_shape(self) -> None:
        sink = StringIO()
        logger = StructuredLogger(sink=sink)
        record = logger.info("task started", correlation_id="c-1", task="t-1")
        assert record["level"] == "INFO"
        assert record["service"] == "xenopus"
        assert record["correlation_id"] == "c-1"
        line = json.loads(sink.getvalue().strip())
        assert line["message"] == "task started"
        assert "timestamp" in line

    def test_minimum_level_filters_debug(self) -> None:
        sink = StringIO()
        logger = StructuredLogger(sink=sink, minimum_level=LogLevel.INFO)
        logger.debug("dropped")
        logger.info("kept")
        lines = sink.getvalue().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["message"] == "kept"

    def test_secret_never_reaches_sink(self) -> None:
        sink = StringIO()
        logger = StructuredLogger(sink=sink)
        record = logger.info(
            "auth attempt",
            credential="ghp_AbCdEf0123456789AbCdEf01",
        )
        assert record["credential"] == "[REDACTED]"
        assert "ghp_" not in sink.getvalue()

    def test_error_level_writes(self) -> None:
        sink = StringIO()
        logger = StructuredLogger(sink=sink)
        logger.error("boom", correlation_id="c")
        assert json.loads(sink.getvalue())["level"] == "ERROR"
