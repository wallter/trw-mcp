"""The wheel-shipped unread-field set must be real, loadable, and in sync.

PRD-QUAL-131-FR05 / OQ-01. ``trw-eval`` builds ablation arms against the
published ``trw-mcp`` wheel and needs to reject an overlay key that trw-mcp does
not read. It cannot scan monorepo source, so the readership answer travels as
shipped data. That makes three things load-bearing, each pinned below: the
artifact ships and parses, its contents agree with the ratchet that measured
them, and the accessor is reachable from the public package path a wheel
consumer would import.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BASELINE = _REPO_ROOT / ".trw" / "compliance" / "config-field-consumers-baseline.json"


def test_published_set_loads_and_is_non_empty() -> None:
    """The artifact ships in the wheel and parses to a non-empty set.

    Non-emptiness is the whole point: an empty set makes every downstream
    readership check vacuously green, which is the exact shape this artifact
    exists to remove.
    """
    from trw_mcp.models.config import unread_config_fields

    fields = unread_config_fields()
    assert fields, "published unread set is empty — the readership guard would be vacuous"
    assert all(isinstance(name, str) for name in fields)


def test_published_set_names_only_real_config_fields() -> None:
    """Every published name must be a declared field, or the guard misfires.

    A stale name here would make trw-eval reject an overlay key that is
    perfectly legal — a false positive that blocks a legitimate campaign.
    """
    from trw_mcp.models.config import TRWConfig, unread_config_fields

    unknown = sorted(unread_config_fields() - set(TRWConfig.model_fields))
    assert unknown == [], f"published unread set names fields TRWConfig does not declare: {unknown}"


def test_published_set_matches_the_ratchet_baseline() -> None:
    """The shipped copy and the ratchet ledger are one measurement, minus class E.

    They are written together by ``--write-baseline`` and this is what keeps
    them in sync. PRD-CORE-263-FR10 split the single ``fields`` measurement in
    two: the ratchet baseline's ``fields`` is the raw AST-scanned unread set
    (which class-E fields ARE textually, since the Python scan cannot see their
    reader), while the published ``unread_fields`` deliberately excludes the
    class-E fields ``scripts/_config_consumers_citations.py::published_payload``
    carries apart, because setting one of those DOES change behaviour (a shell
    hook or generated file reads it) even though no Python reader exists. So the
    invariant this test protects is baseline fields minus the externally-consumed
    class, not baseline fields verbatim — drift in the permissive direction (a
    field measured unread with no external-consumer citation, but missing from
    the published copy) would silently re-open the null-arm hole the artifact was
    added to close.
    """
    from trw_mcp.models.config import unread_config_fields

    if not _BASELINE.is_file():  # pragma: no cover - baseline ships with the repo
        pytest.skip("compliance baseline not present in this checkout")
    baseline_doc = json.loads(_BASELINE.read_text(encoding="utf-8"))
    baseline = set(baseline_doc["fields"])
    classifications = baseline_doc.get("classifications", {})
    externally_consumed = {name for name, record in classifications.items() if record.get("class") == "E"}
    assert unread_config_fields() == baseline - externally_consumed


def test_a_published_name_is_genuinely_unread_in_production() -> None:
    """Non-vacuity control, run both ways.

    A published field must NOT appear in production source outside the
    declaration tree, and a field that plainly does appear must NOT be published.
    Without the second half this test would pass against a scan that found
    nothing at all.
    """
    from trw_mcp.models.config import unread_config_fields

    src = _REPO_ROOT / "trw-mcp" / "src" / "trw_mcp"
    blob = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in src.rglob("*.py")
        if "__pycache__" not in path.as_posix() and "models/config" not in path.as_posix()
    )
    assert blob, "no production source read — the control is broken, not the repo clean"

    victim = sorted(unread_config_fields())[0]
    assert victim not in blob, f"{victim} is published as unread but appears in production source"
    # The control: a field production demonstrably reads must not be published.
    assert "framework_version" in blob
    assert "framework_version" not in unread_config_fields()


def test_accessor_is_exported_from_the_public_package_path() -> None:
    """A wheel consumer imports ``trw_mcp.models.config``, not a private module.

    trw-eval depends on this name, so the export is part of the contract rather
    than an implementation detail.
    """
    import trw_mcp.models.config as config_pkg

    assert "unread_config_fields" in config_pkg.__all__
    assert callable(config_pkg.unread_config_fields)


def test_malformed_payload_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A corrupt artifact must raise, never degrade to an empty set.

    Returning ``frozenset()`` on a parse failure would turn a packaging defect
    into a silently permissive allowlist — the arm still runs, still gets a
    label, and nothing says the guard stopped working.
    """
    from trw_mcp.models.config import _unread_fields

    bad = tmp_path / "config-unread-fields.json"
    bad.write_text(json.dumps({"fields": ["oops"]}), encoding="utf-8")
    monkeypatch.setattr(_unread_fields, "_pkg_files", lambda _pkg: tmp_path)
    _unread_fields.unread_config_fields.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="unread_fields"):
            _unread_fields.unread_config_fields()
    finally:
        _unread_fields.unread_config_fields.cache_clear()
