"""PRD-CORE-275-FR01/FR02: advisory ownership precheck.

FR02 is the requirement most easily eroded: the precheck must be CONTEXT, not
control. These tests assert it mutates nothing, and that its own output states
what it cannot promise.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.plan.conftest import Scene
from trw_mcp.plan import ADVISORY_NOTE, PlanError, PlanRefusal, precheck


def _tree_digest(root: Path) -> str:
    entries = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    payload = "\n".join(f"{entry}:{(root / entry).stat().st_size}" for entry in entries)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_a_declared_path_resolves_to_its_owner(scene: Scene) -> None:
    rows = precheck(scene.manifest, ["src/a.py"], scene.project_root)
    assert (rows[0].member_id, rows[0].glob, rows[0].is_test_path) == ("alpha", "src/a.py", False)


def test_a_peer_declared_glob_resolves_to_the_peer(scene: Scene) -> None:
    rows = precheck(scene.manifest, ["src/b/thing.py"], scene.project_root)
    assert rows[0].member_id == "beta"


def test_an_undeclared_path_is_reported_absent_not_permitted(scene: Scene) -> None:
    rows = precheck(scene.manifest, ["src/unclaimed.py"], scene.project_root)
    assert rows[0].member_id is None
    assert "no declaration" in rows[0].render()


@pytest.mark.parametrize(
    ("path", "refusal"),
    [
        ("/etc/passwd", PlanRefusal.ABSOLUTE_PATH),
        ("../outside.py", PlanRefusal.TRAVERSAL_PATH),
        ("", PlanRefusal.EMPTY_PATH),
    ],
)
def test_bad_path_shapes_refuse_rather_than_reporting_unowned(scene: Scene, path: str, refusal: PlanRefusal) -> None:
    """The reused resolver returns UNOWNED for these by documented design.

    Inheriting that would let "outside the project" read as "free", so this
    slice pre-filters and refuses instead.
    """
    with pytest.raises(PlanError) as excinfo:
        precheck(scene.manifest, [path], scene.project_root)
    assert excinfo.value.refusal is refusal


def test_an_absolute_path_inside_the_project_is_also_refused(scene: Scene) -> None:
    """Deliberately stricter than formation.relative_to_root, which normalizes it.

    A peer must be able to recompute the digest without knowing the sender's
    project root, so absolute is refused whatever it points at.
    """
    with pytest.raises(PlanError) as excinfo:
        precheck(scene.manifest, [str(scene.root / "src" / "a.py")], scene.project_root)
    assert excinfo.value.refusal is PlanRefusal.ABSOLUTE_PATH


def test_one_bad_path_refuses_the_whole_batch(scene: Scene) -> None:
    """Partial results invite acting on the half that resolved."""
    with pytest.raises(PlanError):
        precheck(scene.manifest, ["src/a.py", "../escape.py"], scene.project_root)


def test_precheck_mutates_nothing(scene: Scene) -> None:
    """FR02: advisory means no file written, no manifest changed, no lock taken."""
    before = _tree_digest(scene.root)

    precheck(scene.manifest, ["src/a.py", "src/b/x.py", "src/unclaimed.py"], scene.project_root)

    assert _tree_digest(scene.root) == before


def test_the_output_states_its_own_limits() -> None:
    """An agent reading a bare owner name will otherwise over-trust it."""
    assert "not live intent" in ADVISORY_NOTE
    assert "not permission" in ADVISORY_NOTE
