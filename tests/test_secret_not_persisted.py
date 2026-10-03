"""A credential in a learning is blocked and never left on disk (INC-036, INC-037).

Covers the one credential detector (``trw_memory.security.credentials``) shared by the memory
write gate and ``redact_secrets``, the masked dead-letter record of a refused learning, and the
``.trw/.gitignore`` rules for the journal directories. Fixtures are built by concatenation so
no scanner sees a literal key; the ``trw_`` shape carries ``FAKE`` for the tracked-key gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from trw_memory.security.credentials import mask_credentials, mask_low_confidence
from trw_memory.security.pii import PIIType, detect_pii

import trw_mcp
from tests._memory_fixtures import MemoryDaemon, attach_checkout
from trw_mcp.bootstrap._gitignore_merge import _REQUIRED_RULES
from trw_mcp.models.config import TRWConfig
from trw_mcp.telemetry.anonymizer import redact_secrets

_PEM_BODY = "MIIB" * 20
# (case id, secret text, the substring that must not survive anywhere on disk)
_BLOCKED: list[tuple[str, str, str]] = [
    ("sk-ant", "sk-" + "ant-api03-" + "A" * 93, "ant-api03-" + "A" * 93),
    ("sk-ant-invisible-split", "sk-" + "\u200b" + "ant-api03-" + "A" * 93, "ant-api03-" + "A" * 93),
    ("trw", "trw_" + "FAKE" + "a" * 30, "FAKE" + "a" * 30),
    ("aiza", "AIza" + "B" * 35, "B" * 35),
    (
        "pem",
        "-----BEGIN RSA PRIVATE" + " KEY-----\n" + _PEM_BODY + "\n-----END RSA PRIVATE" + " KEY-----",
        _PEM_BODY,
    ),
    ("ghp", "ghp_" + "a1B2" * 9, "a1B2" * 9),
]
_IDS = [case[0] for case in _BLOCKED]
# Placeholder-prone shapes: STORED, value masked (paraphrases of real-store learnings).
_MASKED: list[tuple[str, str, str]] = [
    ("password", "set password=" + "hunter2" + "hunter2 in the config", "hunter2hunter2"),
    ("env-assignment", "export API_KEY=" + "x9y8z7w6v5", "x9y8z7w6v5"),
    ("openrouter-line", "put an OPENROUTER_API_KEY= " + "zzzplaceholder1 line in .env", "zzzplaceholder1"),
    ("postgres-url", "connect with postgres://" + "appuser:pw" + "s3cretpw@db.internal:5432/app", "s3cretpw"),
    ("json-secret", 'payload {"client_secret": "' + "jsonsecretvalue1" + '"}', "jsonsecretvalue1"),
    ("query-token", "GET https://x.test/cb?token=" + "querytokenvalue1" + "&a=1", "querytokenvalue1"),
]
_MASKED_IDS = [case[0] for case in _MASKED]


@pytest.mark.unit
@pytest.mark.parametrize(("text", "needle"), [(c[1], c[2]) for c in _BLOCKED], ids=_IDS)
def test_one_detector_gate_and_redactor_agree(text: str, needle: str) -> None:
    """The write gate calls it API_KEY (blocks) and redact_secrets masks the same text."""
    assert any(m.pii_type == PIIType.API_KEY for m in detect_pii(f"note {text} end"))
    assert needle not in redact_secrets(f"note {text} end")
    assert needle not in mask_credentials(text)


@pytest.mark.unit
@pytest.mark.parametrize(("text", "needle"), [(c[1], c[2]) for c in _MASKED], ids=_MASKED_IDS)
def test_low_confidence_shapes_mask_but_do_not_block(text: str, needle: str) -> None:
    assert not [m for m in detect_pii(text) if m.pii_type == PIIType.API_KEY]
    assert "<REDACTED:" in mask_low_confidence(text)
    assert needle not in mask_low_confidence(text)
    assert needle not in redact_secrets(text)


@pytest.mark.unit
def test_benign_text_is_neither_blocked_nor_masked() -> None:
    prose = "the token verification_service rejects expired sessions; see docs/auth.md and set retries=3"
    assert not [m for m in detect_pii(prose) if m.pii_type == PIIType.API_KEY]
    assert mask_credentials("plain text") == "plain text"


@pytest.mark.unit
def test_bare_bearer_prose_is_masked_but_does_not_block() -> None:
    text = "the Token authentication_middleware failed"
    assert not [m for m in detect_pii(text) if m.pii_type == PIIType.API_KEY]
    assert "<REDACTED:bearer>" in mask_credentials(text)


@pytest.mark.parametrize("rule", ["learnings/pending/", "learnings/dead_letter/"])
def test_journal_directories_are_gitignored(rule: str) -> None:
    assert rule in {r for r, _comment in _REQUIRED_RULES}
    bundled = Path(trw_mcp.__file__).parent / "data" / "gitignore.txt"
    assert rule in bundled.read_text(encoding="utf-8").splitlines()


def _tree_hits(trw_dir: Path, needle: str) -> list[str]:
    return [
        str(p.relative_to(trw_dir))
        for p in sorted(trw_dir.rglob("*"))
        if p.is_file() and needle.encode() in p.read_bytes()
    ]


@pytest.mark.parametrize(("text", "needle"), [(c[1], c[2]) for c in _BLOCKED], ids=_IDS)
def test_blocked_learning_leaves_no_secret_on_disk(
    text: str,
    needle: str,
    tmp_path: Path,
    memory_daemon: MemoryDaemon,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.export import export_data
    from trw_mcp.tools._learn_impl import execute_learn

    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    attach_checkout(trw_dir, memory_daemon)

    result = execute_learn(
        summary="credential rotation note that is long enough to clear the noise filter",
        detail=f"the value we used was\n{text}\nrotate it after the incident review",
        trw_dir=trw_dir,
        config=TRWConfig(embeddings_enabled=False),
    )

    assert result["status"] == "rejected"
    assert "PII policy" in str(result.get("message", ""))
    assert _tree_hits(trw_dir, needle) == []
    exported = json.dumps(export_data(tmp_path, "all"), default=str)
    assert needle not in exported
    # A learning refused for a credential is decided before the journal: nothing of it is kept.
    assert not list((trw_dir / "learnings" / "pending").glob("*.json"))
    assert not list((trw_dir / "learnings" / "dead_letter").glob("*.json"))


@pytest.mark.parametrize(("text", "needle"), [(c[1], c[2]) for c in _MASKED], ids=_MASKED_IDS)
def test_masked_learning_is_stored_masked_everywhere(
    text: str,
    needle: str,
    tmp_path: Path,
    memory_daemon: MemoryDaemon,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.export import export_data
    from trw_mcp.state.memory_adapter import list_active_learnings
    from trw_mcp.tools._learn_impl import execute_learn

    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    attach_checkout(trw_dir, memory_daemon)

    result = execute_learn(
        summary="config example note that is long enough to clear the noise filter",
        detail=f"how to configure it: {text}. keep real values out of git",
        trw_dir=trw_dir,
        config=TRWConfig(embeddings_enabled=False),
    )

    assert result["status"] == "recorded"
    assert _tree_hits(trw_dir, needle) == []
    assert _tree_hits(memory_daemon.user_dir, needle) == []
    readback = json.dumps(list_active_learnings(trw_dir), default=str)
    assert "config example note" in readback
    assert "<REDACTED:" in readback
    assert needle not in readback
    assert needle not in json.dumps(export_data(tmp_path, "all"), default=str)
    assert not list((trw_dir / "learnings" / "pending").glob("*.json"))


def test_masked_shape_in_a_journal_replay_and_dead_letter_stays_masked(tmp_path: Path) -> None:
    """A record dead-lettered from a raw pending file (older journal) is masked on the way."""
    from trw_mcp.state import learn_journal

    trw_dir = tmp_path / ".trw"
    payload = {
        "summary": "s" * 40,
        "detail": "postgres://appuser:" + "s3cretpw@db/app and password=" + "hunter2hunter2",
    }
    learn_journal.journal_pending(trw_dir, "L-raw", payload)
    learn_journal.drain_pending(trw_dir, lambda *_a: "rejected", limit=5, max_attempts=3)
    assert _tree_hits(trw_dir, "s3cretpw") == []
    assert _tree_hits(trw_dir, "hunter2hunter2") == []
    assert len(list((trw_dir / "learnings" / "dead_letter").glob("*.json"))) == 1


def test_credential_inside_an_assignment_is_refused_not_stored_masked(
    tmp_path: Path, memory_daemon: MemoryDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_mcp.tools._learn_impl import execute_learn

    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    attach_checkout(trw_dir, memory_daemon)
    token = "sk-" + "ant-api03-" + "C" * 60

    result = execute_learn(
        summary="assignment note that is long enough to clear the noise filter",
        detail=f"run with API_KEY={token} exported",
        trw_dir=trw_dir,
        config=TRWConfig(embeddings_enabled=False),
    )

    assert result["status"] == "rejected"
    assert _tree_hits(trw_dir, "C" * 60) == []
    assert not list((trw_dir / "learnings" / "entries").glob("*.yaml"))


@pytest.mark.parametrize(
    "detail",
    [
        "The Token authentication_middleware rejected the request, see Bearer-ish handling notes",
        "set password=" + "hunter2hunter2 in the config",
    ],
    ids=["benign-prose", "low-confidence-shape"],
)
def test_replay_from_journal_stores_what_the_original_call_would_store(
    detail: str, tmp_path: Path, memory_daemon: MemoryDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trw_memory.security.credentials import mask_low_confidence

    from trw_mcp.state import learn_journal
    from trw_mcp.state.memory_adapter import list_active_learnings
    from trw_mcp.tools._learn_impl import execute_learn

    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    attach_checkout(trw_dir, memory_daemon)
    config = TRWConfig(embeddings_enabled=False)
    summary = "journal fidelity note that is long enough to clear the noise filter"

    def failing_store(*_a: object, **_k: object) -> dict[str, object]:
        return {"status": "error", "error": "simulated crash"}

    execute_learn(summary=summary, detail=detail, trw_dir=trw_dir, config=config, _adapter_store=failing_store)
    (record,) = list(learn_journal.iter_pending(trw_dir))
    learning_id, payload = record
    assert payload["detail"] == mask_low_confidence(detail)  # journal == store-bound text, no extra masking
    assert payload["summary"] == summary

    execute_learn(
        summary=str(payload["summary"]),
        detail=str(payload["detail"]),
        trw_dir=trw_dir,
        config=config,
        _replay_learning_id=learning_id,
        _from_journal=True,
    )
    stored = [row for row in list_active_learnings(trw_dir) if row.get("id") == learning_id]
    assert len(stored) == 1
    assert stored[0]["detail"] == mask_low_confidence(detail)
    if "authentication_middleware" in detail:
        assert stored[0]["detail"] == detail  # benign prose is stored verbatim
    else:
        assert "hunter2hunter2" not in json.dumps(stored, default=str)
        assert _tree_hits(trw_dir, "hunter2hunter2") == []


_TOKEN = "sk-" + "ant-api03-" + "D" * 60
_LIST_FIELDS = {"tags", "evidence", "consolidated_from", "domain", "phase_affinity"}
_ASSERTION_FIELDS = ["last_evidence", "target", "pattern", "type", "extra"]


def _payload_string_fields() -> list[str]:
    """Every string-bearing field the journal snapshot carries (the sweep inventory)."""
    from trw_mcp.tools._learn_journal_wiring import JOURNAL_ARG_KEYS

    return sorted(JOURNAL_ARG_KEYS - {"impact"})


def _planted(field: str, value: str) -> dict[str, object]:
    if field == "assertions":
        return {field: [{"type": "glob_exists", "target": "src/main.py", "last_evidence": value}]}
    return {field: [value] if field in _LIST_FIELDS else value}


def _learn_with(trw_dir: Path, **fields: object) -> dict[str, object]:
    from trw_mcp.tools._learn_impl import execute_learn

    kwargs: dict[str, object] = {
        "summary": "payload sweep note that is long enough to clear the noise filter",
        "detail": "benign detail about the payload sweep",
    }
    kwargs.update(fields)
    return dict(execute_learn(trw_dir=trw_dir, config=TRWConfig(embeddings_enabled=False), **kwargs))  # type: ignore[arg-type]


@pytest.fixture
def _attached(tmp_path: Path, memory_daemon: MemoryDaemon, monkeypatch: pytest.MonkeyPatch) -> Path:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    attach_checkout(trw_dir, memory_daemon)
    return trw_dir


@pytest.mark.parametrize("field", _payload_string_fields())
def test_token_in_any_payload_field_is_refused_with_nothing_on_disk(field: str, _attached: Path) -> None:
    trw_dir = _attached

    def crashing_store(*_a: object, **_k: object) -> dict[str, object]:
        raise RuntimeError("interrupted after the journal write")

    result = _learn_with(trw_dir, _adapter_store=crashing_store, **_planted(field, _TOKEN))

    assert result["status"] == "rejected"
    assert _tree_hits(trw_dir, "D" * 60) == []
    assert not list((trw_dir / "learnings" / "pending").glob("*.json"))
    assert not list((trw_dir / "learnings" / "dead_letter").glob("*.json"))
    assert not list((trw_dir / "learnings" / "entries").glob("*.yaml"))


@pytest.mark.parametrize("key", _ASSERTION_FIELDS)
def test_token_in_any_assertion_key_is_refused(key: str, _attached: Path) -> None:
    assertion = {"type": "glob_exists", "target": "src/main.py", key: _TOKEN}
    result = _learn_with(_attached, assertions=[assertion])
    assert result["status"] == "rejected"
    assert _tree_hits(_attached, "D" * 60) == []


@pytest.mark.parametrize("field", ["source_identity", "model_id", "task_type"])
def test_credential_shape_in_a_metadata_field_is_refused(field: str, _attached: Path) -> None:
    result = _learn_with(_attached, **{field: "password=" + "hunter2hunter2"})
    assert result["status"] == "rejected"
    assert _tree_hits(_attached, "hunter2hunter2") == []


def test_low_confidence_value_in_an_assertion_is_journaled_and_stored_masked(_attached: Path) -> None:
    from trw_mcp.state import learn_journal
    from trw_mcp.state.memory_adapter import list_active_learnings
    from trw_mcp.tools._learn_impl import execute_learn

    trw_dir = _attached
    config = TRWConfig(embeddings_enabled=False)
    assertion = {"type": "glob_exists", "target": "src/main.py", "last_evidence": "password=" + "hunter2hunter2"}
    summary = "assertion masking note that is long enough to clear the noise filter"

    def crashing_store(*_a: object, **_k: object) -> dict[str, object]:
        return {"status": "error", "error": "interrupted after the journal write"}

    execute_learn(
        summary=summary,
        detail="benign",
        assertions=[assertion],
        trw_dir=trw_dir,
        config=config,
        _adapter_store=crashing_store,
    )
    ((learning_id, payload),) = list(learn_journal.iter_pending(trw_dir))
    assert _tree_hits(trw_dir, "hunter2hunter2") == []
    assert "<REDACTED:" in json.dumps(payload["assertions"])

    execute_learn(
        summary=str(payload["summary"]),
        detail=str(payload["detail"]),
        assertions=payload["assertions"],  # type: ignore[arg-type]
        trw_dir=trw_dir,
        config=config,
        _replay_learning_id=learning_id,
        _from_journal=True,
    )
    stored = json.dumps([r for r in list_active_learnings(trw_dir) if r.get("id") == learning_id], default=str)
    assert "hunter2hunter2" not in stored
    assert _tree_hits(trw_dir, "hunter2hunter2") == []


_MASKABLE_KEYS = ["password=" + "hunter2hunter2", "postgres://app:" + "hunter2hunter2" + "@db.internal/x"]


@pytest.mark.parametrize("key", _MASKABLE_KEYS)
def test_store_bound_text_masks_low_confidence_shapes_in_assertion_keys(key: str) -> None:
    from trw_mcp.tools._learn_journal_wiring import store_bound_text

    payload: dict[str, object] = {"summary": "s", "assertions": [{"type": "glob_exists", key: "v"}]}
    assert store_bound_text(payload) is None
    dumped = json.dumps(payload["assertions"])
    assert "hunter2hunter2" not in dumped
    assert "<REDACTED:" in dumped
    assert "glob_exists" in dumped


def test_two_assertion_keys_masked_to_one_placeholder_keep_both_fields() -> None:
    """Masking keys must not collapse two fields into one (a dropped assertion value is silent data loss)."""
    from trw_mcp.tools._learn_journal_wiring import store_bound_text

    first, second = "password=" + "hunter2hunter2", "password=" + "hunter3hunter3"
    payload: dict[str, object] = {"summary": "s", "assertions": [{"type": "glob_exists", first: "a", second: "b"}]}
    assert store_bound_text(payload) is None
    (assertion,) = payload["assertions"]  # type: ignore[misc]
    assert sorted(v for k, v in assertion.items() if k != "type") == ["a", "b"]
    assert "hunter" not in json.dumps(assertion)


@pytest.mark.parametrize("key", _MASKABLE_KEYS)
def test_low_confidence_shape_in_an_assertion_key_is_masked_on_disk(key: str, _attached: Path) -> None:
    def crashing_store(*_a: object, **_k: object) -> dict[str, object]:
        return {"status": "error", "error": "interrupted after the journal write"}

    _learn_with(
        _attached,
        _adapter_store=crashing_store,
        assertions=[{"type": "glob_exists", "target": "src/main.py", key: "v"}],
    )
    # Non-vacuity (codex r1 KI): the journaled record exists and carries the masked key.
    assert _tree_hits(_attached, "<REDACTED:") != []
    assert _tree_hits(_attached, "hunter2hunter2") == []
