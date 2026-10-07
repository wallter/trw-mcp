"""P2a: inside one install, a YAML text is parsed once; the memo never serves a stale or shared value.

``trw_mcp._yaml_memo`` memoises ``FileStateReader.read_yaml`` and the channel-manifest ``load`` by content
hash for the length of one install binding. The wiring tests pin that an ``update_project(ide="all")`` no
longer re-parses the manifests and config layers (a counting wrapper on the two parse functions); the
staleness tests pin that a changed file is re-read, that callers get private copies, and that nothing
survives from one ``update_project`` call into the next.
"""

from __future__ import annotations

import hashlib
import os
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.usefixtures("no_memory_daemon")

#: Measured after the change (2026-10-06) for update_project(ide="all") on a fresh init: parses by
#: read_yaml and the channel-manifest load, inside and outside the install binding. Before it: 66.
_UPDATE_ALL_MAX_PARSES = 6


@pytest.fixture
def parses(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[tuple[str, str, bool]]]:
    """(loader kind, sha256 of the text, inside an install) for every parse at the memoised sites."""
    from trw_mcp.channels import _manifest_loader
    from trw_mcp.state import persistence
    from trw_mcp.state._project_root_binding import install_shared

    seen: list[tuple[str, str, bool]] = []

    def record(kind: str, real: Callable[..., Any]) -> Callable[..., Any]:
        def counted(text: str, *args: Any) -> Any:
            # read_yaml's second argument picks the loader: the config-layer and plain loaders are memoised apart.
            loader = f"{kind}:{args[0]}" if kind == "read_yaml" else kind
            seen.append((loader, hashlib.sha256(text.encode()).hexdigest(), install_shared() is not None))
            return real(text, *args)

        return counted

    monkeypatch.setattr(persistence, "_parse_yaml", record("read_yaml", persistence._parse_yaml))
    monkeypatch.setattr(_manifest_loader, "_load_safe", record("channels", _manifest_loader._load_safe))
    yield seen


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    return root


def test_update_all_never_parses_the_same_text_twice_inside_the_install(
    tmp_path: Path, parses: list[tuple[str, str, bool]]
) -> None:
    from trw_mcp.bootstrap import init_project, update_project

    root = _repo(tmp_path)
    assert not init_project(root, ide="all")["errors"]
    parses.clear()
    assert not update_project(root, ide="all")["errors"]

    inside = Counter((kind, digest) for kind, digest, bound in parses if bound)
    assert inside, "control: the counting wrapper saw no parse inside the install"
    assert max(inside.values()) == 1, inside.most_common(3)
    assert len(parses) <= _UPDATE_ALL_MAX_PARSES, Counter(kind for kind, _, _ in parses)


def test_init_all_never_parses_the_same_text_twice_inside_the_install(
    tmp_path: Path, parses: list[tuple[str, str, bool]]
) -> None:
    from trw_mcp.bootstrap import init_project

    assert not init_project(_repo(tmp_path), ide="all")["errors"]
    inside = Counter((kind, digest) for kind, digest, bound in parses if bound)
    assert inside, "control: the counting wrapper saw no parse inside the install"
    assert max(inside.values()) == 1, inside.most_common(3)


def test_outside_an_install_every_read_parses(tmp_path: Path, parses: list[tuple[str, str, bool]]) -> None:
    from trw_mcp.state.persistence import FileStateReader

    path = tmp_path / "a.yaml"
    path.write_text("key: value\n", encoding="utf-8")
    FileStateReader().read_yaml(path)
    FileStateReader().read_yaml(path)
    assert len(parses) == 2


def test_a_file_rewritten_inside_the_install_is_read_again(tmp_path: Path) -> None:
    """Same size, same mtime: only the content changed. A stat-keyed cache would serve the old value."""
    from trw_mcp.state._project_root_binding import installing_into
    from trw_mcp.state.persistence import FileStateReader

    path = tmp_path / "config.yaml"
    path.write_text("flag: aaa\n", encoding="utf-8")
    before = path.stat()
    with installing_into(tmp_path):
        assert FileStateReader().read_yaml(path) == {"flag": "aaa"}
        path.write_text("flag: bbb\n", encoding="utf-8")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert path.stat().st_size == before.st_size
        assert path.stat().st_mtime_ns == before.st_mtime_ns
        assert FileStateReader().read_yaml(path) == {"flag": "bbb"}


def test_each_caller_gets_its_own_copy(tmp_path: Path) -> None:
    from trw_mcp.state._project_root_binding import installing_into
    from trw_mcp.state.persistence import FileStateReader

    path = tmp_path / "m.yaml"
    path.write_text("skills:\n- a\nnested:\n  k: v\n", encoding="utf-8")
    with installing_into(tmp_path):
        first = FileStateReader().read_yaml(path)
        first["skills"].append("injected")  # type: ignore[attr-defined]
        first["nested"]["k"] = "changed"  # type: ignore[index]
        second = FileStateReader().read_yaml(path)
        assert second == {"skills": ["a"], "nested": {"k": "v"}}
        assert second["skills"] is not first["skills"]


def test_a_channel_manifest_rewritten_inside_the_install_is_loaded_again(tmp_path: Path) -> None:
    from trw_mcp.channels._manifest_loader import auto_recreate_empty, load
    from trw_mcp.state._project_root_binding import installing_into

    path = tmp_path / ".trw" / "channels" / "manifest.yaml"
    path.parent.mkdir(parents=True)
    auto_recreate_empty(path, reason="missing")
    with installing_into(tmp_path):
        assert load(path).channels == []
        empty = path.read_text(encoding="utf-8")
        path.write_text(empty + "unexpected_top_level_key: 1\n", encoding="utf-8")
        with pytest.raises(Exception, match="unexpected_top_level_key"):
            load(path)


def test_a_malformed_channel_manifest_still_names_its_path(tmp_path: Path) -> None:
    """The parse reads a string now; its error must still name the file, as the open-file parse did."""
    from trw_mcp.channels._manifest_loader import ManifestValidationError, load
    from trw_mcp.state._project_root_binding import installing_into

    path = tmp_path / "manifest.yaml"
    path.write_text("channels: [unclosed\n", encoding="utf-8")
    for bound in (False, True):
        with installing_into(tmp_path) if bound else _nothing():
            with pytest.raises(ManifestValidationError, match="not valid YAML") as caught:
                load(path)
            assert str(path) in str(caught.value)


class _nothing:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


def test_the_memo_does_not_survive_into_the_next_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two updates in one process: each gets its own memo, none is left behind, and the second reads the
    config.yaml edited between them."""
    from trw_mcp import _yaml_memo
    from trw_mcp.bootstrap import _update_project, init_project, update_project

    root = _repo(tmp_path)
    assert not init_project(root, ide="claude-code")["errors"]
    memos: list[object] = []  # the dicts themselves: an id() can be reused once the first is freed
    real_record = _update_project._update_config_target_platforms

    def record_memo(*args: Any, **kwargs: Any) -> None:
        memo = _yaml_memo._memo()
        assert memo is not None
        memos.append(memo)
        real_record(*args, **kwargs)

    monkeypatch.setattr(_update_project, "_update_config_target_platforms", record_memo)
    assert not update_project(root, ide="claude-code")["errors"]
    assert _yaml_memo._memo() is None
    flags = root / ".trw" / "runtime" / "hook-flags"
    assert "hooks_enabled=true" in flags.read_text(encoding="utf-8")

    config = root / ".trw" / "config.yaml"
    config.write_text(config.read_text(encoding="utf-8").rstrip("\n") + "\nhooks_enabled: false\n", encoding="utf-8")
    assert not update_project(root, ide="claude-code")["errors"]

    assert "hooks_enabled=false" in flags.read_text(encoding="utf-8")
    assert len(memos) == 2
    assert memos[0] is not memos[1]
    assert _yaml_memo._memo() is None


@pytest.mark.parametrize(
    ("text", "state_value"),
    [("", {}), ("format_version: 1\nchannels: []\n", {"format_version": 1, "channels": []})],
    ids=["empty", "two-key-mapping"],
)
@pytest.mark.parametrize("manifest_first", [True, False], ids=["manifest-first", "state-first"])
def test_one_text_through_both_loaders_in_one_install_gets_each_loaders_result(
    tmp_path: Path, text: str, state_value: dict[str, object], manifest_first: bool
) -> None:
    """read_yaml memoises a (data, tolerated) tuple and the manifest loader a raw mapping: identical text read
    through both inside one install must not hand either loader the other's shape (an empty file made
    read_yaml raise StateError instead of returning {})."""
    from trw_mcp.channels._manifest_loader import ManifestValidationError, load
    from trw_mcp.state._project_root_binding import installing_into
    from trw_mcp.state.persistence import FileStateReader

    path = tmp_path / "shared.yaml"
    path.write_text(text, encoding="utf-8")

    def via_manifest() -> None:
        # Neither text is a valid manifest; each must fail as a manifest, with the manifest loader's message.
        expected = "Manifest must be a YAML mapping" if not text else "format_version must be"
        with pytest.raises(ManifestValidationError, match=expected):
            load(path)

    def via_state() -> None:
        assert FileStateReader().read_yaml(path) == state_value
        assert FileStateReader().read_yaml(path, tolerate_identical_duplicates=True) == state_value

    with installing_into(tmp_path):
        for step in (via_manifest, via_state) if manifest_first else (via_state, via_manifest):
            step()
        for step in (via_manifest, via_state):  # second reads come from the memo
            step()


def test_a_kind_reused_by_two_parsers_never_shares_an_entry(tmp_path: Path) -> None:
    """The key includes the parse callable, so a future call site that copies a kind stays isolated."""
    from trw_mcp._yaml_memo import memo_parse
    from trw_mcp.state._project_root_binding import installing_into

    def as_tuple(body: str) -> tuple[str, int]:
        return (body, 1)

    def as_mapping(body: str) -> dict[str, str]:
        return {"body": body}

    with installing_into(tmp_path):
        assert memo_parse("same-kind", "x", as_tuple) == ("x", 1)
        assert memo_parse("same-kind", "x", as_mapping) == {"body": "x"}
        assert memo_parse("same-kind", "x", as_tuple) == ("x", 1)
