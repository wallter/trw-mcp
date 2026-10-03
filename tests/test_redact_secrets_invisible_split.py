"""PII-INVISIBLE-SPLIT through trw-mcp's egress chokepoint (telemetry, feedback, assess, LLM previews)."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ("key AKIA\u200bIOSFODNN7EXAMPLE here", "IOSFODNN7EXAMPLE"),
        ("token ghp_a1B2c3D4e5\u00adF6g7H8i9J0\u202ea1B2c3D4e5F6g7H8i9J0", "F6g7H8i9J0a1B2c3"),
        ("mail jane\u200d.doe@exam\u2060ple.com now", "jane.doe@example.com"),
    ],
    ids=["aws-zero-width", "github-soft-hyphen-and-bidi", "email-zwj"],
)
def test_redact_secrets_masks_a_secret_split_by_an_invisible_character(text: str, secret: str) -> None:
    from trw_mcp.telemetry.anonymizer import redact_secrets

    out = redact_secrets(text)
    assert secret not in out, out
    assert "\u200b" not in out and "\u00ad" not in out and "\u202e" not in out and "\u200d" not in out


def test_redact_secrets_leaves_ordinary_non_ascii_prose_alone() -> None:
    from trw_mcp.telemetry.anonymizer import redact_secrets

    assert redact_secrets("José met 東京's résumé team") == "José met 東京's résumé team"


# --- codex PII-INVISIBLE-SPLIT r1: normalising must never unmask what the raw scan masks -----------------

GH_TOKEN = "ghp_" + "a1B2c3D4e5F6g7H8i9J0" * 2


def test_a_separator_before_a_token_cannot_erase_its_word_boundary() -> None:
    """r1 P0-1: stripping the zero-width space joined `x` to the token, so the token no longer matched."""
    from trw_mcp.telemetry.anonymizer import redact_secrets

    out = redact_secrets("x\u200b" + GH_TOKEN)
    assert GH_TOKEN[4:20] not in out, out


@pytest.mark.parametrize(
    "text",
    ['PASSWORD="first＂SensitiveSuffixABC"', '{"password": "first＂SensitiveSuffixABC"}'],
    ids=["env-assignment", "json"],
)
def test_a_compatibility_quote_cannot_end_a_masked_value_early(text: str) -> None:
    """r1 P0-2: NFKC turned a fullwidth quote into an ASCII delimiter and the password tail leaked."""
    from trw_mcp.telemetry.anonymizer import redact_secrets

    assert "SensitiveSuffixABC" not in redact_secrets(text)


def test_a_split_before_a_quoted_value_keeps_the_whole_value_masked() -> None:
    """codex r2 P0: per-token masking consumed the opening quote and left the password tail visible."""
    from trw_mcp.telemetry.anonymizer import redact_secrets

    out = redact_secrets('PASSWORD\u200b="first SensitiveSuffixABC"')
    assert "SensitiveSuffixABC" not in out and "first" not in out, out


def test_a_split_inside_a_bearer_value_does_not_hide_an_ssn_from_redact_secrets() -> None:
    """codex r3 P0 upstream: mask_credentials ran its own union before strip_pii's SSN pass."""
    from trw_mcp.telemetry.anonymizer import redact_secrets

    assert "45 6789" not in redact_secrets("Bearer abcde\u200b123 45 6789")


def test_redact_secrets_masks_every_character_its_as_written_pipeline_masks() -> None:
    """Auditor M2: the character-level monotone property for the fourth entry point (trw-mcp's chokepoint)."""
    import random

    from tests._invisible_split_monotone import _case, _exposed
    from trw_mcp.telemetry.anonymizer import _redact_secrets_as_written, redact_secrets

    rng = random.Random(20261003)
    for _ in range(500):
        text = _case(rng)
        exposed = _exposed(text, _redact_secrets_as_written, redact_secrets)
        assert not exposed, (text, _redact_secrets_as_written(text), redact_secrets(text), sorted(exposed))
