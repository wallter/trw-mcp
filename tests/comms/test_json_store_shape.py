"""The candidate registry and worktree records are ONE store shape (ledger N12).

Both are ``{<section>: {key: record}}`` JSON under ``.trw/runtime/``, owner-only,
written temp-then-replace, with "absent is empty, unparseable is a refusal". They
carried two copies of that, so a fix to one reached only half the stores. These
tests hold the shared rules at the primitive and check both real stores obey them.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from trw_mcp.formation import FormationError
from trw_mcp.formation._store import read_json_store, write_json_store


def test_absent_is_empty_but_unparseable_is_a_refusal(tmp_path: Path) -> None:
    """Treating a corrupt store as empty would drop live records and re-admit what they guarded."""
    missing = tmp_path / "runtime" / "nothing.json"
    assert read_json_store(missing, section="records", label="test store") == {}

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    with pytest.raises(FormationError, match="test store"):
        read_json_store(corrupt, section="records", label="test store")

    wrong_shape = tmp_path / "shape.json"
    wrong_shape.write_text(json.dumps({"records": ["a", "b"]}), encoding="utf-8")
    with pytest.raises(FormationError, match="malformed"):
        read_json_store(wrong_shape, section="records", label="test store")


def test_a_written_store_is_owner_only_and_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "runtime" / "store.json"
    write_json_store(path, section="records", records={"k": {"v": 1}})
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600, "these stores hold pin keys and run paths"
    assert read_json_store(path, section="records", label="test store") == {"k": {"v": 1}}
    assert not list(path.parent.glob("*.tmp")), "temp file replaced, not left behind"


def test_both_real_stores_use_the_shared_primitive(formation_env: FormationFixture) -> None:
    """A second implementation of the shape is the drift this pins against."""
    from trw_mcp.formation import _candidates, _coordination

    for module in (_candidates, _coordination):
        source = Path(str(module.__file__)).read_text(encoding="utf-8")
        assert "read_json_store" in source and "write_json_store" in source
        assert "json.loads" not in source, f"{module.__name__} must not re-open a store itself"
        assert "write_owner_only" not in source, f"{module.__name__} must not re-implement the write"


def test_the_candidate_registry_still_refuses_a_record_this_build_cannot_read(
    formation_env: FormationFixture,
) -> None:
    """Field drift is a separate failure from a corrupt file, and keeps its own message."""
    from trw_mcp.formation._candidates import _path, _read

    path = _path(formation_env.trw_dir)
    write_json_store(path, section="candidates", records={"c1": {"unknown_field": True}})
    with pytest.raises(FormationError, match="candidate registry"):
        _read(formation_env.trw_dir)
