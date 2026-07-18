"""Behavior tests for the modular CLI subparser registration.

The operational subcommands (build-release / channel-doctor / session-changelog /
tendencies / version-status / tier) and the project-management subcommands
(init-project / update-project / audit / export / import-learnings) were extracted
into sibling modules (``_cli_argparse_operational`` / ``_cli_argparse_project``) to
keep the parser builder under the 350 effective-LOC module gate.

These tests assert the parser BEHAVIOR — that each subcommand and its
representative arguments parse to the expected ``argparse.Namespace`` values —
not merely that the registration functions exist. This is the regression guard
that the wiring (the ``add_*_subcommands`` calls) stays in place.
"""

from __future__ import annotations

import pytest

from trw_mcp.server._cli_argparse import _build_arg_parser


@pytest.fixture()
def parser():  # type: ignore[no-untyped-def]
    return _build_arg_parser()


# ── Operational subcommands ──────────────────────────────────────────


def test_build_release_parses_representative_args(parser) -> None:  # type: ignore[no-untyped-def]
    ns = parser.parse_args(
        [
            "build-release",
            "--version",
            "1.2.3",
            "--output-dir",
            "/tmp/out",
            "--push",
            "--backend-url",
            "https://api",
            "--api-key",
            "k",
        ]
    )
    assert ns.command == "build-release"
    assert ns.version == "1.2.3"
    assert ns.output_dir == "/tmp/out"
    assert ns.push is True
    assert ns.backend_url == "https://api"
    assert ns.api_key == "k"


def test_build_release_defaults(parser) -> None:  # type: ignore[no-untyped-def]
    ns = parser.parse_args(["build-release"])
    assert ns.command == "build-release"
    assert ns.output_dir == "."
    assert ns.push is False


def test_channel_doctor_scan_parses(parser) -> None:  # type: ignore[no-untyped-def]
    ns = parser.parse_args(["channel-doctor", "--project-dir", "/repo", "scan", "--max-age-hours", "48"])
    assert ns.command == "channel-doctor"
    assert ns.project_dir == "/repo"
    assert ns.channel_doctor_command == "scan"
    assert ns.max_age_hours == 48
    # scan defaults dry_run True
    assert ns.dry_run is True


def test_channel_doctor_throttle_apply_parses(parser) -> None:  # type: ignore[no-untyped-def]
    ns = parser.parse_args(["channel-doctor", "throttle", "--window-hours", "3", "--apply"])
    assert ns.channel_doctor_command == "throttle"
    assert ns.window_hours == 3
    assert ns.apply is True


def test_session_changelog_parses(parser) -> None:  # type: ignore[no-untyped-def]
    ns = parser.parse_args(["session-changelog", "/runs/abc", "--write", "--advisory"])
    assert ns.command == "session-changelog"
    assert ns.run_path == "/runs/abc"
    assert ns.write is True
    assert ns.advisory is True


def test_tendencies_parses(parser) -> None:  # type: ignore[no-untyped-def]
    ns = parser.parse_args(["tendencies", "--corpus", "/docs", "--json"])
    assert ns.command == "tendencies"
    assert ns.corpus == "/docs"
    assert ns.as_json is True


def test_version_status_parses(parser) -> None:  # type: ignore[no-untyped-def]
    ns = parser.parse_args(["version-status", "--project-root", "/repo", "--check"])
    assert ns.command == "version-status"
    assert ns.project_root == "/repo"
    assert ns.check is True


def test_tier_issue_parses_required_args(parser) -> None:  # type: ignore[no-untyped-def]
    ns = parser.parse_args(
        ["tier", "issue", "--tier", "pro", "--issued-to", "ops@example.com", "--expires", "2027-01-01", "--print-only"]
    )
    assert ns.command == "tier"
    assert ns.tier_command == "issue"
    assert ns.tier == "pro"
    assert ns.issued_to == "ops@example.com"
    assert ns.expires == "2027-01-01"
    assert ns.print_only is True


def test_tier_issue_rejects_bad_tier_choice(parser) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(SystemExit):
        parser.parse_args(["tier", "issue", "--tier", "platinum", "--issued-to", "x", "--expires", "2027-01-01"])


def test_tier_status_parses(parser) -> None:  # type: ignore[no-untyped-def]
    ns = parser.parse_args(["tier", "status", "--trw-dir", "/proj/.trw"])
    assert ns.tier_command == "status"
    assert ns.trw_dir == "/proj/.trw"


# ── Project-management subcommands ───────────────────────────────────


def test_init_project_parses(parser) -> None:  # type: ignore[no-untyped-def]
    ns = parser.parse_args(["init-project", "/proj", "--force", "--ide", "codex", "--runs-root", ".trw/r"])
    assert ns.command == "init-project"
    assert ns.target_dir == "/proj"
    assert ns.force is True
    assert ns.ide == "codex"
    assert ns.runs_root == ".trw/r"


def test_update_project_parses(parser) -> None:  # type: ignore[no-untyped-def]
    ns = parser.parse_args(["update-project", "/proj", "--pip-install", "--dry-run", "--ide", "all"])
    assert ns.command == "update-project"
    assert ns.pip_install is True
    assert ns.dry_run is True
    assert ns.ide == "all"


def test_export_parses(parser) -> None:  # type: ignore[no-untyped-def]
    ns = parser.parse_args(["export", "/proj", "--scope", "learnings", "--format", "csv", "--min-impact", "0.5"])
    assert ns.command == "export"
    assert ns.scope == "learnings"
    assert ns.format == "csv"
    assert ns.min_impact == 0.5


def test_import_learnings_parses(parser) -> None:  # type: ignore[no-untyped-def]
    ns = parser.parse_args(["import-learnings", "data.json", "/proj", "--tags", "a,b", "--dry-run"])
    assert ns.command == "import-learnings"
    assert ns.source_file == "data.json"
    assert ns.target_dir == "/proj"
    assert ns.tags == "a,b"
    assert ns.dry_run is True


def test_audit_parses(parser) -> None:  # type: ignore[no-untyped-def]
    ns = parser.parse_args(["audit", "/proj", "--format", "json", "--fix"])
    assert ns.command == "audit"
    assert ns.format == "json"
    assert ns.fix is True


def test_all_operational_and_project_subcommands_registered(parser) -> None:  # type: ignore[no-untyped-def]
    """The full set of subcommands must be reachable from the top-level parser."""
    import argparse

    choices: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            choices = set(action.choices.keys())
            break

    expected = {
        "build-release",
        "channel-doctor",
        "session-changelog",
        "tendencies",
        "version-status",
        "tier",
        "init-project",
        "update-project",
        "audit",
        "export",
        "import-learnings",
    }
    assert expected.issubset(choices), f"missing: {expected - choices}"


# ── Deprecated --transport shim (2026-07-11) ─────────────────────────


def test_transport_stdio_accepted_as_noop(parser) -> None:  # type: ignore[no-untyped-def]
    """Installer scripts in the wild probe with `--transport stdio serve`
    while installing the LATEST PyPI trw-mcp; the flag must stay accepted
    (as a no-op) after the HTTP-transport removal (a0673d9765)."""
    args = parser.parse_args(["--transport", "stdio", "serve"])
    assert args.command == "serve"
    assert args.transport == "stdio"


def test_transport_non_stdio_rejected(parser) -> None:  # type: ignore[no-untyped-def]
    """Only stdio is a valid transport; anything else must still error."""
    with pytest.raises(SystemExit):
        parser.parse_args(["--transport", "http", "serve"])
