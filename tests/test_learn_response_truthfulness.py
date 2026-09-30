"""trw_learn responses say what happened (INC-119 a, d, e).

A rate-limited write is NOT stored yet; a redaction is named; a queued write is not reported as "nothing written".
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._learn_impl import execute_learn


@pytest.fixture
def trw_dir(tmp_path: Path) -> Path:
    path = tmp_path / ".trw"
    (path / "learnings" / "entries").mkdir(parents=True)
    return path


def _learn(trw_dir: Path, store: Callable[..., dict[str, object]], detail: str = "plain detail") -> dict[str, object]:
    return dict(
        execute_learn(
            summary="truthfulness probe summary that is long enough",
            detail=detail,
            trw_dir=trw_dir,
            config=TRWConfig(),
            _adapter_store=store,
            _generate_learning_id=lambda: "L-probe001",
            _save_learning_entry=lambda d, e: d / "learnings" / "entries" / "x.yaml",
            _update_analytics=lambda *a: None,
            _list_active_learnings=lambda d: [],
            _check_and_handle_dedup=lambda *a, **k: None,
        )
    )


def test_a_rate_limited_learn_says_it_was_not_stored_and_when_to_retry(trw_dir: Path) -> None:
    def store(*_a: object, **_k: object) -> dict[str, object]:
        return {"learning_id": "L-probe001", "status": "rate_limited", "retry_after": 12, "path": "sqlite://L-probe001"}

    result = _learn(trw_dir, store)
    assert result["status"] == "rate_limited"
    message = str(result.get("message", ""))
    assert "NOT stored" in message and "retry" in message.lower()
    assert result.get("retry_after") == 12
    assert "sqlite://" not in str(result.get("path", ""))  # a stored-looking locator for a row that does not exist


def _recorded(*_a: object, **_k: object) -> dict[str, object]:
    return {"learning_id": "L-probe001", "status": "recorded", "path": "sqlite://L-probe001"}


def test_a_masked_credential_is_named_in_the_response_without_its_value(trw_dir: Path) -> None:
    secret = "hunter2hunter2"
    result = _learn(trw_dir, _recorded, detail=f"config password={secret} and more")
    assert result["status"] == "recorded"
    note = str(result.get("redaction_note", ""))
    assert "env" in note and "masked" in note
    assert secret not in str(result) and "14" not in note  # kinds and a count only: never the value or its length


def test_an_unredacted_learning_carries_no_redaction_note(trw_dir: Path) -> None:
    assert "redaction_note" not in _learn(trw_dir, _recorded)


def test_a_replayed_already_masked_learning_does_not_claim_a_fresh_redaction(trw_dir: Path) -> None:
    result = _learn(trw_dir, _recorded, detail="config password=<REDACTED:env> already masked")
    assert "redaction_note" not in result


def test_a_learn_queued_while_the_daemon_is_down_does_not_claim_nothing_was_written(trw_dir: Path) -> None:
    from trw_memory.exceptions import DaemonUnreachableError

    def down(*_a: object, **_k: object) -> dict[str, object]:
        raise DaemonUnreachableError(
            "the trw-memory daemon is unreachable (x). No memory was read or written, and no local store was created."
        )

    with pytest.raises(DaemonUnreachableError) as caught:
        _learn(trw_dir, down)
    text = str(caught.value)
    assert (trw_dir / "learnings" / "pending" / "L-probe001.json").is_file()  # the fact the old text contradicted
    assert "No memory was read or written" not in text
    assert "L-probe001" in text and "pending" in text and "session" in text


def test_a_daemon_error_is_unchanged_when_nothing_was_journaled(trw_dir: Path) -> None:
    from trw_memory.exceptions import DaemonUnreachableError

    def down(*_a: object, **_k: object) -> dict[str, object]:
        raise DaemonUnreachableError("the trw-memory daemon is unreachable (x). No memory was read or written.")

    with pytest.raises(DaemonUnreachableError) as caught:
        execute_learn(
            summary="truthfulness probe summary that is long enough",
            detail="d",
            trw_dir=trw_dir,
            config=TRWConfig(learn_journal_enabled=False),
            _adapter_store=down,
            _generate_learning_id=lambda: "L-probe002",
            _save_learning_entry=lambda d, e: d / "x",
            _update_analytics=lambda *a: None,
            _list_active_learnings=lambda d: [],
            _check_and_handle_dedup=lambda *a, **k: None,
        )
    assert "No memory was read or written" in str(caught.value)


def test_tags_the_store_added_are_named_in_the_response(trw_dir: Path) -> None:
    def store(*_a: object, **_k: object) -> dict[str, object]:
        return {**_recorded(), "auto_added_tags": ["database"]}

    result = _learn(trw_dir, store)
    assert result.get("auto_added_tags") == ["database"]


def test_the_adapter_reports_the_topic_tags_it_inferred(trw_dir: Path, fake_memory_store: object) -> None:
    from trw_mcp.state.memory_adapter import store_learning

    result = store_learning(trw_dir, "L-tagprobe", "database index rebuild is slow", "detail", tags=["mine"])
    assert result["status"] == "recorded" and "database" in list(result.get("auto_added_tags", []))  # type: ignore[call-overload]
    plain = store_learning(trw_dir, "L-tagprobe2", "zzz qqq", "detail", tags=["mine"])
    assert "auto_added_tags" not in plain
