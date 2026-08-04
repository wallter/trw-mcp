"""The debounce key must distinguish two files whose sanitized names collide.

Every bundled edit-hint hook derives its 180-second debounce filename by deleting
each character outside ``[A-Za-z0-9_.-]`` from the path. That is lossy by
construction: ``src/alpha.py`` and ``src/beta.py`` written with Greek letters both
reduce to the same key, as do ``$x.py`` and ``x.py``. After the first file was
hinted, the second DIFFERENT file was suppressed for three minutes — a silent
miss, reported as a normal debounce.

Fixed by appending ``cksum`` of the exact path. POSIX, present wherever these
hooks run, and no interpreter start (which matters: the budget on this path is
2.5s and an interpreter costs ~1s of it).

Found by an independent adversarial review, which verified the collision by
running the sanitizer under POSIX ``sh``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_DATA = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "data"


def _builds_a_path_debounce_key(text: str) -> bool:
    """A hook in scope derives a DEBOUNCE key from the edited FILE PATH.

    Scoped deliberately. ``completion-gate.sh`` and ``helper-idle.sh`` also assign
    ``_safe_name``, but from a helper NAME and with ``tr -c ... '_'`` — a
    replacement, not a deletion, and not a debounce key. Sweeping them in would
    demand a checksum where the collision this test is about cannot occur, which is
    how an over-broad guard gets suppressed instead of fixed.
    """
    return "_debounce" in text and "_safe_name=" in text


#: Every bundled hook that builds a debounce key from a file path. Discovered, not
#: listed — a fourth client copy is covered automatically rather than exempt by
#: omission.
_HOOKS = sorted(
    p for p in _DATA.rglob("*.sh") if _builds_a_path_debounce_key(p.read_text(encoding="utf-8", errors="replace"))
)


def _debounce_key(file_path: str) -> str:
    """Reproduce the hooks' key derivation exactly, in POSIX sh."""
    script = (
        '_file_path="$1"\n'
        "_safe_name=$(printf '%s' \"$_file_path\" | tr '/' '_' | tr -cd 'a-zA-Z0-9_.-')\n"
        "_path_ck=$(printf '%s' \"$_file_path\" | cksum | cut -d' ' -f1)\n"
        'printf "%s-%s" "$_safe_name" "$_path_ck"\n'
    )
    return subprocess.run(["sh", "-c", script, "sh", file_path], capture_output=True, text=True, check=True).stdout


def test_the_hook_set_is_non_empty() -> None:
    """Non-vacuity control. A discovery that matched nothing would make the
    source assertion below vacuous."""
    # Named, not counted. A literal cardinality of a discovered population is
    # census data and `make census-check` rejects it -- and naming them is stronger
    # anyway: three of the WRONG hooks would satisfy a count and fail this.
    names = {p.name for p in _HOOKS}
    expected = {"pre-tool-distill-hint.sh", "trw-before-edit-hint.sh", "trw-copilot-distill-hint.sh"}
    assert expected <= names, f"discovery missed {sorted(expected - names)}; found {sorted(names)}"


@pytest.mark.parametrize("hook", _HOOKS, ids=lambda p: p.name)
def test_every_hook_mixes_the_exact_path_into_its_debounce_key(hook: Path) -> None:
    """The lossy sanitizer must never be the whole key."""
    text = hook.read_text(encoding="utf-8")

    assert "cksum" in text, f"{hook.name} still keys its debounce on the lossy sanitized name alone"


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("src/α.py", "src/β.py"),  # two distinct non-ASCII filenames
        ("$x.py", "x.py"),  # shell-ish character stripped
        ("a/b.py", "a b.py"),  # separator vs space
    ],
    ids=["greek-letters", "dollar-prefix", "slash-vs-space"],
)
def test_two_distinct_paths_do_not_share_a_debounce_key(left: str, right: str) -> None:
    """Two of these three reproduce the defect; the third is a general guard.

    Measured against the pre-fix derivation: the two Greek-letter paths both
    produced ``src_.py``, and ``$x.py`` and ``x.py`` both produced ``x.py`` — those
    two were RED. ``a/b.py`` vs ``a b.py`` already differed (``a_b.py`` vs
    ``ab.py``), so it is a distinctness guard rather than an attribution case, and
    is labelled that way instead of being counted as evidence it is not.
    """
    assert _debounce_key(left) != _debounce_key(right), (
        f"{left!r} and {right!r} share a debounce key — the second is silently suppressed for 180s"
    )


def test_the_same_path_is_stable_across_calls() -> None:
    """Precision control. A key that varied per call would disable debouncing
    entirely, turning a correctness fix into a performance regression."""
    assert _debounce_key("src/module.py") == _debounce_key("src/module.py")
