"""PRD-SEC-013: the shared ``--root`` argument parser in ``paths.py``.

``_parse_root_arg`` was byte-identical in ``ledger.py`` and ``enrollment.py``.
Both CLIs already pin the end-to-end effect through their own ``main()``; these
pin the argument-shredding contract itself, which neither CLI test exercises —
``--root`` in the middle of the argv, a trailing ``--root`` with no value, and
the requirement that every OTHER argument survives in order.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.security.intent_contract.paths import parse_root_arg, stray_options


def test_root_is_pulled_out_and_the_command_survives() -> None:
    assert parse_root_arg(["verify", "--root", "/tmp/x"]) == (Path("/tmp/x"), ["verify"])


def test_root_is_recognized_before_the_command_too() -> None:
    assert parse_root_arg(["--root", "/tmp/x", "enroll"]) == (Path("/tmp/x"), ["enroll"])


def test_absent_root_leaves_the_arguments_untouched() -> None:
    assert parse_root_arg(["status", "extra"]) == (None, ["status", "extra"])


def test_trailing_root_without_a_value_is_kept_as_a_plain_argument() -> None:
    """Not silently swallowed — it is handed back so the CLI can REFUSE it.

    The refusal is the CLI's job, not this parser's, and it did not happen: the
    docstring here used to assert that a trailing ``--root`` made the CLI exit 2,
    and it did not. ``enrollment.main(["enroll", "--root"])`` dispatched on
    ``args[0] == "enroll"``, ignored the dangling flag, and enrolled under
    ``repo_root()`` — the default root the operator was trying to override —
    exiting 0 (finding F5, 2026-07-25). :func:`stray_options` is what closes it;
    see the CLI-level tests in test_intent_contract_enrollment.py.
    """
    assert parse_root_arg(["verify", "--root"]) == (None, ["verify", "--root"])


def test_stray_options_flags_a_dangling_root_and_any_other_unknown_flag() -> None:
    assert stray_options(["verify", "--root"]) == ["--root"]
    assert stray_options(["enroll", "--nope", "-x"]) == ["--nope", "-x"]


def test_stray_options_is_empty_for_a_fully_consumed_argv() -> None:
    """Non-vacuous counterpart: the refusal must not fire on legitimate commands."""
    root, remaining = parse_root_arg(["enroll", "--root", "/tmp/x"])
    assert root == Path("/tmp/x")
    assert stray_options(remaining) == []


def test_the_last_root_wins_and_no_argument_order_is_lost() -> None:
    root, remaining = parse_root_arg(["a", "--root", "/one", "b", "--root", "/two", "c"])
    assert root == Path("/two")
    assert remaining == ["a", "b", "c"]


def test_empty_argv_is_a_clean_no_op() -> None:
    assert parse_root_arg([]) == (None, [])
