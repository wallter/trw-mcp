"""Filesystem contract for the internal, narrow PRD section updater."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("tail", ["", "\n", "  "])
def test_preserves_outside_bytes(tmp_path: Path, newline: str, tail: str) -> None:
    from trw_mcp.state.prd_sections import update_execution_plan

    prefix = (
        "---\nexample: |\n  ## Execution plan\n---\n# PRD café\n"
        "```md\n## Execution plan\n```\n~~~\n## Execution plan\n~~~\n\n"
    ).replace("\n", newline)
    suffix = ("## Verification\nunchanged" + tail).replace("\n", newline)
    original = (prefix + "## Execution plan\nold\n".replace("\n", newline) + suffix).encode()
    path = tmp_path / "prd.md"
    path.write_bytes(original)
    replacement = "## Execution plan\n### Step\nnew\n```\n## Decoy\n```\n".replace("\n", newline)
    digest = update_execution_plan(path, replacement, expected_sha256=_digest(original))
    assert path.read_bytes() == (prefix + replacement + suffix).encode()
    assert digest == _digest(path.read_bytes())
    assert path.with_name("prd.md.lock").exists()


def test_eof_section_without_final_newline(tmp_path: Path) -> None:
    from trw_mcp.state.prd_sections import update_execution_plan

    path = tmp_path / "prd.md"
    original = b"# PRD\n## Execution plan\nold"
    path.write_bytes(original)
    replacement = "## Execution plan\nnew"
    update_execution_plan(path, replacement, expected_sha256=_digest(original))
    assert path.read_bytes() == b"# PRD\n## Execution plan\nnew"


@pytest.mark.parametrize(
    "original",
    [
        b"# PRD\n## Other\nnone\n",
        b"## Execution plan\nold\n## Execution plan\nduplicate\n",
        b"```\n## Execution plan\n```\n",
        b"---\n## Execution plan\n",
        b"## Execution plan\n```\nunclosed\n",
        b"## Execution plan\n\xff\n",
        b"Execution plan\n---\nold\n",
    ],
)
def test_invalid_target_content_unchanged(tmp_path: Path, original: bytes) -> None:
    from trw_mcp.exceptions import StateError
    from trw_mcp.state.prd_sections import update_execution_plan

    path = tmp_path / "prd.md"
    path.write_bytes(original)
    with pytest.raises(StateError):
        update_execution_plan(path, "## Execution plan\nnew\n", expected_sha256=_digest(original))
    assert path.read_bytes() == original


@pytest.mark.parametrize(
    "replacement",
    [
        "",
        "new",
        "intro\n## Execution plan\nnew\n",
        "## Execution plan\n## Execution plan\n",
        "## Execution plan\n## Acceptance\nescape\n",
        "## Execution plan\n# Escaped\n",
        "## Execution plan\nEscaped\n===\n",
        "## Execution plan\nEscaped\n---\n",
        "## Execution plan\n```\nunclosed\n",
        "## Execution plan\n~~~\nunclosed\n",
        "---\nx: 1\n---\n## Execution plan\nnew\n",
        "## Execution plan\nno separator",
        "Execution plan\n---\nnew\n",
        "## Execution plan\n\ud800\n",
    ],
)
def test_invalid_replacement_never_calls_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: str
) -> None:
    from trw_mcp.exceptions import StateError
    from trw_mcp.state import prd_sections
    from trw_mcp.state.prd_sections import update_execution_plan

    path = tmp_path / "prd.md"
    original = b"## Execution plan\nold\n## Acceptance\nfixed\n"
    path.write_bytes(original)

    def fail_write(*args: object) -> None:
        pytest.fail("writer reached for invalid replacement")

    monkeypatch.setattr(prd_sections.FileStateWriter, "write_text", fail_write)
    with pytest.raises(StateError):
        update_execution_plan(path, replacement, expected_sha256=_digest(original))
    assert path.read_bytes() == original


def test_stale_hash_never_calls_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.exceptions import StateError
    from trw_mcp.state import prd_sections
    from trw_mcp.state.prd_sections import update_execution_plan

    path = tmp_path / "prd.md"
    original = b"## Execution plan\nold\n"
    path.write_bytes(original)

    def fail_write(*args: object) -> None:
        pytest.fail("writer reached with stale hash")

    monkeypatch.setattr(prd_sections.FileStateWriter, "write_text", fail_write)
    with pytest.raises(StateError, match="Stale"):
        update_execution_plan(path, "## Execution plan\nnew\n", expected_sha256="0" * 64)
    assert path.read_bytes() == original


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink"])
def test_requires_regular_nonsymlink_existing_target(tmp_path: Path, kind: str) -> None:
    from trw_mcp.exceptions import StateError
    from trw_mcp.state.prd_sections import update_execution_plan

    path = tmp_path / "prd.md"
    if kind == "directory":
        path.mkdir()
    if kind == "symlink":
        target = tmp_path / "real.md"
        target.write_bytes(b"## Execution plan\nold\n")
        path.symlink_to(target)
    with pytest.raises(StateError):
        update_execution_plan(path, "## Execution plan\nnew\n", expected_sha256="0" * 64)
    assert not path.with_name("prd.md.lock").exists()


def test_atomic_replace_failure_preserves_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.exceptions import StateError
    from trw_mcp.state.prd_sections import update_execution_plan

    path = tmp_path / "prd.md"
    original = b"## Execution plan\nold\n"
    path.write_bytes(original)

    def fail_replace(*args: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr("trw_mcp.state.persistence.os.replace", fail_replace)
    with pytest.raises(StateError, match="injected replace failure"):
        update_execution_plan(path, "## Execution plan\nnew\n", expected_sha256=_digest(original))
    assert path.read_bytes() == original
    assert set(tmp_path.iterdir()) == {path, path.with_name("prd.md.lock")}


def test_immediate_prewrite_recheck(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.exceptions import StateError
    from trw_mcp.state import prd_sections
    from trw_mcp.state.prd_sections import update_execution_plan

    path = tmp_path / "prd.md"
    original = b"## Execution plan\nold\n"
    edited = original + b"manual edit\n"
    path.write_bytes(original)
    real_section = prd_sections._section

    def edit_during_scan(text: str) -> tuple[int, int]:
        path.write_bytes(edited)
        return real_section(text)

    monkeypatch.setattr(prd_sections, "_section", edit_during_scan)
    with pytest.raises(StateError, match="immediately before write"):
        update_execution_plan(path, "## Execution plan\nnew\n", expected_sha256=_digest(original))
    assert path.read_bytes() == edited


def test_cooperating_stale_writers_only_one_succeeds(tmp_path: Path) -> None:
    from trw_mcp.exceptions import StateError
    from trw_mcp.state.prd_sections import update_execution_plan

    path = tmp_path / "prd.md"
    original = b"## Execution plan\nold\n"
    path.write_bytes(original)
    barrier = Barrier(2)

    def update(label: str) -> str:
        barrier.wait(timeout=5)
        try:
            return update_execution_plan(path, f"## Execution plan\n{label}\n", expected_sha256=_digest(original))
        except StateError as exc:
            assert "Stale" in str(exc)
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["first", "second"]))
    assert results.count("stale") == 1
    assert _digest(path.read_bytes()) in results
    assert path.read_bytes() in {b"## Execution plan\nfirst\n", b"## Execution plan\nsecond\n"}


def test_arbitrary_utf8_outside_section_is_preserved(tmp_path: Path) -> None:
    from trw_mcp.state.prd_sections import update_execution_plan

    path = tmp_path / "prd.md"

    @settings(max_examples=30)
    @given(st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc", "Zl", "Zp")), max_size=80))
    def check(payload: str) -> None:
        prefix = f"# PRD\nopaque: {payload}\n\n"
        suffix = f"## Acceptance\nopaque: {payload}"
        original = (prefix + "## Execution plan\nold\n" + suffix).encode()
        path.write_bytes(original)
        replacement = "## Execution plan\nnew\n"
        update_execution_plan(path, replacement, expected_sha256=_digest(original))
        assert path.read_bytes() == (prefix + replacement + suffix).encode()

    check()


def test_long_fences_and_setext_suffix_are_preserved(tmp_path: Path) -> None:
    from trw_mcp.state.prd_sections import update_execution_plan

    path = tmp_path / "prd.md"
    prefix = "---\nexample: |\n  ## Execution plan\n...\n````md\n```\n## Execution plan\n`````\n"
    suffix = "Acceptance\n==========\nfixed"
    original = (prefix + "## Execution Plan ##\nold\n" + suffix).encode()
    path.write_bytes(original)
    replacement = "## Execution plan\n~~~ python\n## Decoy\n~~~~\n"
    update_execution_plan(path, replacement, expected_sha256=_digest(original))
    assert path.read_bytes() == (prefix + replacement + suffix).encode()


@pytest.mark.parametrize("body", ["```\ncode\n```\n---\n", "~~~\ncode\n~~~\n---\n", "### Step\n---\n"])
def test_nonparagraph_before_rule_does_not_truncate_section(tmp_path: Path, body: str) -> None:
    from trw_mcp.state.prd_sections import update_execution_plan

    path = tmp_path / "prd.md"
    prefix = "# PRD\n"
    suffix = "## Acceptance\nfixed\n"
    original = (prefix + "## Execution plan\n" + body + suffix).encode()
    path.write_bytes(original)
    replacement = "## Execution plan\nnew\n"
    update_execution_plan(path, replacement, expected_sha256=_digest(original))
    assert path.read_bytes() == (prefix + replacement + suffix).encode()
