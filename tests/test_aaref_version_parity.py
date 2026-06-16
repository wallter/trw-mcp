"""AARE-F version-parity + decontamination invariants.

Regression guard for the v1.1.0-body-under-v2.0.0-stamp drift: ``trw_init`` stamps
``.trw/frameworks/VERSION.yaml`` from ``config.aaref_version`` while deploying the
bundled ``data/aaref.md`` body verbatim (see ``_deploy_frameworks``). If the two
disagree, every project gets a provable version-vs-content truthfulness violation
(the symptom that shipped for ~3.5 months). The cross-copy byte-identity gate lives
in ``scripts/check-aaref-sync.py``; these tests pin the in-package invariants.
"""

from __future__ import annotations

import re

from trw_mcp.tools._orchestration_helpers import _get_bundled_file

_BODY_VERSION_RE = re.compile(r"^\*\*Version\*\*:\s*([0-9]+\.[0-9]+\.[0-9]+)\s*$", re.MULTILINE)


def test_bundled_aaref_body_version_matches_config_default(config) -> None:
    """The bundled AARE-F body version must equal ``config.aaref_version`` ('vX.Y.Z').

    Prevents trw_init from stamping VERSION.yaml with a version the deployed body
    does not match.
    """
    body = _get_bundled_file("aaref.md")
    assert body is not None, "bundled aaref.md must be discoverable via _get_bundled_file"

    match = _BODY_VERSION_RE.search(body)
    assert match is not None, "bundled aaref.md must declare '**Version**: X.Y.Z' in its header"
    body_version = f"v{match.group(1)}"

    assert body_version == config.aaref_version, (
        f"AARE-F version drift: bundled body is {body_version} but config.aaref_version "
        f"is {config.aaref_version}. trw_init would stamp VERSION.yaml with "
        f"{config.aaref_version} over a {body_version} body — a version-vs-content lie. "
        f"Bump both together (data/aaref.md header + _fields_ceremony.py aaref_version)."
    )


def test_bundled_aaref_is_project_agnostic() -> None:
    """The portable framework body must not carry legacy single-project contamination."""
    body = _get_bundled_file("aaref.md")
    assert body is not None
    lowered = body.lower()
    for forbidden in ("ai_v7", "psychological analysis"):
        assert forbidden not in lowered, (
            f"AARE-F body contains legacy contamination {forbidden!r}; the portable edition must be project-agnostic."
        )
