"""Tests for trw_mcp.telemetry.anonymizer — PRD-CORE-031, hardened under R2-014.

``redact_secrets`` is the single redaction chokepoint used by telemetry,
OTEL, the LLM client, feedback submissions, and trw_assess. These tests
cover its own coverage surface (credentials, PII) plus idempotency and
``redact_metadata``; wiring proof that each caller actually routes through
it lives in ``test_telemetry_pipeline_core.py`` (enqueue), the otel tests,
and ``test_assess_tool.py``/``test_feedback_redact.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.telemetry.anonymizer import (
    anonymize_installation_id,
    redact_metadata,
    redact_paths,
    redact_secrets,
)

# ---------------------------------------------------------------------------
# anonymize_installation_id
# ---------------------------------------------------------------------------


class TestAnonymizeInstallationId:
    def test_deterministic(self) -> None:
        """Same input always yields same output."""
        result1 = anonymize_installation_id("my-machine-id")
        result2 = anonymize_installation_id("my-machine-id")
        assert result1 == result2

    def test_different_inputs_produce_different_outputs(self) -> None:
        """Different inputs must not collide."""
        a = anonymize_installation_id("machine-a")
        b = anonymize_installation_id("machine-b")
        assert a != b

    def test_output_length_is_16(self) -> None:
        """Output is exactly 16 hex characters."""
        result = anonymize_installation_id("test-id")
        assert len(result) == 16

    def test_output_is_hex(self) -> None:
        """Output contains only lowercase hex characters."""
        result = anonymize_installation_id("test-id-hex")
        assert all(c in "0123456789abcdef" for c in result)

    def test_non_reversible_empty_string(self) -> None:
        """Empty string produces a valid hash, not an error."""
        result = anonymize_installation_id("")
        assert len(result) == 16

    def test_non_reversible_unicode(self) -> None:
        """Unicode input is handled correctly."""
        result = anonymize_installation_id("id-éàü")
        assert len(result) == 16


# ---------------------------------------------------------------------------
# redact_paths
# ---------------------------------------------------------------------------


class TestRedactPaths:
    def test_absolute_path_replaced(self, tmp_path: Path) -> None:
        """Occurrences of the project root are replaced with <project>."""
        text = f"Error in {tmp_path}/src/foo.py line 42"
        result = redact_paths(text, tmp_path)
        assert str(tmp_path) not in result
        assert "<project>/src/foo.py" in result

    def test_no_match_unchanged(self, tmp_path: Path) -> None:
        """Text without the project root is returned unchanged."""
        text = "No paths here"
        result = redact_paths(text, tmp_path)
        assert result == text

    def test_multiple_occurrences(self, tmp_path: Path) -> None:
        """All occurrences are replaced, not just the first."""
        text = f"{tmp_path}/a and {tmp_path}/b"
        result = redact_paths(text, tmp_path)
        assert result.count("<project>") == 2
        assert str(tmp_path) not in result

    def test_empty_text(self, tmp_path: Path) -> None:
        """Empty string is handled without error."""
        result = redact_paths("", tmp_path)
        assert result == ""


# ---------------------------------------------------------------------------
# redact_secrets — the single redaction chokepoint (R2-014)
# ---------------------------------------------------------------------------


class TestRedactSecretsPii:
    def test_email_replaced(self) -> None:
        result = redact_secrets("Contact us at support@example.com for help.")
        assert "support@example.com" not in result
        assert "<email>" in result

    def test_multiple_emails_replaced(self) -> None:
        result = redact_secrets("a@b.com and c@d.org")
        assert "a@b.com" not in result
        assert "c@d.org" not in result

    def test_non_pii_content_preserved(self) -> None:
        text = "Run trw_session_start() to load prior learnings."
        assert redact_secrets(text) == text

    def test_empty_string(self) -> None:
        assert redact_secrets("") == ""


class TestRedactSecretsCredentials:
    """Coverage the weaker per-module redactors lacked before R2-014."""

    def test_jwt_redacted(self) -> None:
        jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I3PlFUP0THsR8U"
        result = redact_secrets(f"token was {jwt} in the request")
        assert jwt not in result
        assert "<REDACTED:jwt>" in result

    def test_pem_private_key_block_redacted(self) -> None:
        pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\n-----END PRIVATE KEY-----"
        result = redact_secrets(f"here is my key:\n{pem}\nthanks")
        assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" not in result
        assert "<REDACTED:private_key>" in result

    def test_authorization_header_bearer_redacted(self) -> None:
        result = redact_secrets("curl -H 'Authorization: Bearer sk-abc123def456ghi789jkl'")
        assert "sk-abc123def456ghi789jkl" not in result
        assert "Bearer <REDACTED:authorization>" in result

    def test_bare_bearer_without_header_redacted(self) -> None:
        token = "abcdefghijklmnopqrstuvwxyz0123"
        result = redact_secrets(f"Bearer {token}")
        assert token not in result

    def test_bearer_prose_not_redacted(self) -> None:
        prose = "Token expiration handling failed; Bearer authentication required"
        assert redact_secrets(prose) == prose

    def test_api_key_sk_prefix_redacted(self) -> None:
        result = redact_secrets("key=sk-abcdefghijklmnopqrstuvwxyz1234")
        assert "sk-abcdefghijklmnopqrstuvwxyz1234" not in result

    def test_env_var_token_assignment_redacted(self) -> None:
        result = redact_secrets("auth token_ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        assert "token_ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in result

    def test_json_secret_value_redacted(self) -> None:
        result = redact_secrets('{"password": "hunter2hunter2"}')
        assert "hunter2hunter2" not in result
        assert '"password"' in result

    def test_connection_string_credentials_redacted(self) -> None:
        result = redact_secrets("postgres://user:sup3rSecr3t@db.example.com/app")
        assert "sup3rSecr3t" not in result
        assert "postgres://<REDACTED:credentials>@db.example.com/app" == result

    def test_idempotent(self) -> None:
        text = (
            "Authorization: Bearer sk-abc123def456ghi789jkl and postgres://user:pw12345678@host/db and user@example.com"
        )
        once = redact_secrets(text)
        twice = redact_secrets(once)
        assert once == twice

    def test_home_dir_replaced(self, monkeypatch: object) -> None:
        import os

        result = redact_secrets(f"path is {os.path.expanduser('~')}/project/file.py")
        assert "$HOME" in result


class TestRedactSecretsJsonHeaders:
    """Cross-vendor R2-014 P1: credential headers serialized as JSON are redacted by value."""

    def test_header_values_are_redacted(self) -> None:
        for key, value in (
            ("Authorization", "Basic dXNlcjpwYXNzd29yZA=="),
            ("authorization", "Basic c2VjcmV0"),
            ("Proxy-Authorization", "Basic cHJveHk6cGFzcw=="),
            ("Cookie", "sessionid=abc123"),
            ("Set-Cookie", "sid=deadbeef; HttpOnly"),
        ):
            out = redact_secrets(f'{{"{key}": "{value}"}}')
            assert value not in out, key
            assert f'"{key}"' in out, key

    def test_lookalike_keys_are_kept(self) -> None:
        assert redact_secrets('{"author": "alice", "authorized": "yes"}') == '{"author": "alice", "authorized": "yes"}'


class TestRedactMetadata:
    def test_none_passthrough(self) -> None:
        assert redact_metadata(None) is None

    def test_empty_dict_passthrough(self) -> None:
        assert redact_metadata({}) == {}

    def test_value_redacted(self) -> None:
        result = redact_metadata({"contact": "user@example.com"})
        assert result is not None
        assert "user@example.com" not in list(result.values())[0]

    def test_key_redacted(self) -> None:
        result = redact_metadata({"sk-abc123def456ghi789jkl": "x"})
        assert result is not None
        assert "sk-abc123def456ghi789jkl" not in "".join(result.keys())


# ---------------------------------------------------------------------------
# TRW platform API keys (REDACT-TRW-PLATFORM-KEYS)
# ---------------------------------------------------------------------------
# Real shapes (as minted by the platform's key-issuing endpoints):
#   ``trw_`` + secrets.token_urlsafe(32)      -> 43 chars of [A-Za-z0-9_-]
#   ``trw_dk_`` + secrets.token_urlsafe(32)   -> device-flow key, same body
# The body is URL-safe base64, so ``-`` and ``_`` occur inside it. Synthetic
# values only; built from a fixed fragment, never a real key.
_BODY = ("Ab3-Zk9_Qw2xYv7LmN4pRs8TuC1dEf" * 2)[:43]
_KEY = "trw_" + _BODY
_DEVICE_KEY = "trw_dk_" + _BODY
_KEYS = pytest.mark.parametrize("key", [_KEY, _DEVICE_KEY], ids=["trw_", "trw_dk_"])


class TestRedactSecretsTrwPlatformKeys:
    @_KEYS
    def test_free_text(self, key: str) -> None:
        out = redact_secrets(f"my key is {key} and it failed")
        assert _BODY not in out
        assert out.startswith("my key is ") and out.endswith(" and it failed")

    @_KEYS
    def test_json(self, key: str) -> None:
        out = redact_secrets('{"platform_api_key": "%s", "ok": true}' % key)
        assert _BODY not in out
        assert '"ok": true' in out

    @_KEYS
    def test_url(self, key: str) -> None:
        out = redact_secrets(f"GET https://api.example.test/v1/x/{key}/y?z=1")
        assert _BODY not in out
        assert "api.example.test" in out

    @_KEYS
    def test_env_line(self, key: str) -> None:
        out = redact_secrets(f"TRW_PLATFORM_API_KEY={key}\nOTHER=1")
        assert _BODY not in out
        assert "OTHER=1" in out

    @_KEYS
    def test_bare_in_prose_after_punctuation(self, key: str) -> None:
        out = redact_secrets(f'("{key}"), then {key}.')
        assert _BODY not in out

    def test_idempotent(self) -> None:
        once = redact_secrets(f"k={_KEY} d {_DEVICE_KEY}")
        assert redact_secrets(once) == once

    def test_tool_and_module_names_intact(self) -> None:
        text = (
            "trw_session_start trw_recall trw_learn trw_build_check trw_prd_validate "
            "trw_mcp.telemetry.anonymizer trw_memory.security.credentials "
            "trw_mcp_telemetry_anonymizer_redact_secrets_helper_function "
            "trw_dk_ and trw_lic_ words; import trw_mcp; from trw_memory import x"
        )
        assert redact_secrets(text) == text
