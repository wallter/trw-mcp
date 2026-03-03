"""Tests for trw_mcp.telemetry.anonymizer — PRD-CORE-031."""

from __future__ import annotations

from pathlib import Path

from trw_mcp.telemetry.anonymizer import anonymize_installation_id, redact_paths, strip_pii

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
        result = anonymize_installation_id("id-\u00e9\u00e0\u00fc")
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
# strip_pii
# ---------------------------------------------------------------------------


class TestStripPii:
    def test_email_replaced(self) -> None:
        """Email addresses are replaced with <email>."""
        result = strip_pii("Contact us at support@example.com for help.")
        assert "support@example.com" not in result
        assert "<email>" in result

    def test_multiple_emails_replaced(self) -> None:
        """All email addresses in the text are replaced."""
        result = strip_pii("a@b.com and c@d.org")
        assert "a@b.com" not in result
        assert "c@d.org" not in result
        assert result.count("<email>") == 2

    def test_api_key_sk_prefix(self) -> None:
        """sk- prefixed API keys are redacted."""
        result = strip_pii("key=sk-abcdefghijklmnopqrstuvwxyz1234")
        assert "sk-abcdefghijklmnopqrstuvwxyz1234" not in result
        assert "<api_key>" in result

    def test_api_key_token_prefix(self) -> None:
        """token_ prefixed secrets are redacted."""
        result = strip_pii("auth token_ABCDEFGHIJKLMNOPQRSTUVWXYZ")
        assert "token_ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in result
        assert "<api_key>" in result

    def test_non_pii_content_preserved(self) -> None:
        """Regular text without PII is left unchanged."""
        text = "Run trw_session_start() to load prior learnings."
        result = strip_pii(text)
        assert result == text

    def test_combined_email_and_key(self) -> None:
        """Both email and API key in same string are both redacted."""
        text = "user@example.com used key sk-12345678901234567890"
        result = strip_pii(text)
        assert "user@example.com" not in result
        assert "sk-12345678901234567890" not in result
        assert "<email>" in result
        assert "<api_key>" in result

    def test_empty_string(self) -> None:
        """Empty string is handled without error."""
        result = strip_pii("")
        assert result == ""

    def test_short_key_not_redacted(self) -> None:
        """Secrets shorter than 20 chars after prefix are NOT redacted."""
        result = strip_pii("sk-short12345")
        assert "<api_key>" not in result
        assert "sk-short12345" in result
