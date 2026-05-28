"""PRD-INFRA-132 FR04a — unit tests for the PII redactor.

Pure-function tests; no filesystem I/O. Covers each pattern class + the
idempotency requirement called out in the FR acceptance criteria.
"""

from __future__ import annotations

import pytest

from trw_mcp.tools.submit_feedback import _redact_pii


# ---------------------------------------------------------------------------
# License key — trw_lic_*
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "license is trw_lic_abc123XYZ in config",
        "trw_lic_short",
        "prefix trw_lic_very-long_with-dashes_42 suffix",
    ],
)
def test_redacts_license_key(raw: str) -> None:
    redacted = _redact_pii(raw)
    assert "trw_lic_" not in redacted
    assert "<REDACTED:license_key>" in redacted


# ---------------------------------------------------------------------------
# API keys — sk_, pk_, AKIA
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,must_contain",
    [
        ("token sk_live_abcDEF123", "sk_live_"),
        ("token sk_test_xyz789", "sk_test_"),
        ("token pk_live_pubkey42", "pk_live_"),
        ("token pk_test_pubkey99", "pk_test_"),
        ("AWS access AKIAIOSFODNN7EXAMPLE here", "AKIA"),
    ],
)
def test_redacts_api_keys(raw: str, must_contain: str) -> None:
    redacted = _redact_pii(raw)
    assert must_contain not in redacted
    assert "<REDACTED:api_key>" in redacted


# ---------------------------------------------------------------------------
# $HOME paths
# ---------------------------------------------------------------------------


def test_redacts_home_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/operator")
    raw = "config lives at /home/operator/.trw/config.yaml ok"
    redacted = _redact_pii(raw)
    assert "/home/operator" not in redacted
    assert "$HOME/.trw/config.yaml" in redacted


def test_skips_home_substitution_when_home_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force expanduser to return literal "~" so the resolver short-circuits
    # the path-substitution branch.
    monkeypatch.setattr(
        "trw_mcp.tools.submit_feedback.os.path.expanduser",
        lambda _: "~",
    )
    raw = "path is /home/operator/.trw"
    redacted = _redact_pii(raw)
    # Path is left untouched when HOME cannot be resolved.
    assert "/home/operator/.trw" in redacted


def test_home_substitution_strips_trailing_slash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/op/")
    raw = "see /home/op/config.yaml"
    redacted = _redact_pii(raw)
    assert "$HOME/config.yaml" in redacted


# ---------------------------------------------------------------------------
# Env-var KEY=value
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "PASSWORD=hunter2",
        "SECRET=topsecret",
        "TOKEN=abcdef",
        "API_KEY=keyval",
        "API-KEY=keyval-dash",
        "AWS_ACCESS_KEY=AKIAfoo",
        "AWS_SECRET_KEY=barbaz",
        "password=lowercase",  # case-insensitive
    ],
)
def test_redacts_env_kv(raw: str) -> None:
    redacted = _redact_pii(raw)
    assert "<REDACTED:env>" in redacted
    assert "=" not in redacted or redacted.count("=") < raw.count("=")


def test_env_redaction_preserves_surrounding_text() -> None:
    raw = "context PASSWORD=secret123 and more after"
    redacted = _redact_pii(raw)
    assert redacted.startswith("context ")
    assert redacted.endswith(" and more after")
    assert "<REDACTED:env>" in redacted


# ---------------------------------------------------------------------------
# Clean input — no false positives
# ---------------------------------------------------------------------------


def test_clean_input_unchanged() -> None:
    raw = "Just an ordinary bug report with no secrets in it at all."
    assert _redact_pii(raw) == raw


def test_empty_string_unchanged() -> None:
    assert _redact_pii("") == ""


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_idempotent_double_application(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", "/home/me")
    raw = (
        "license trw_lic_ABC123 "
        "key sk_live_XYZ999 "
        "path /home/me/.trw "
        "env PASSWORD=secret"
    )
    once = _redact_pii(raw)
    twice = _redact_pii(once)
    assert once == twice


def test_idempotent_on_already_redacted_markers() -> None:
    raw = "<REDACTED:license_key> and <REDACTED:api_key>"
    assert _redact_pii(raw) == raw
