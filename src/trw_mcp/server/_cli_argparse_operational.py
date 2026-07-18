"""Operational CLI subparser registration helpers."""

from __future__ import annotations

import argparse

__all__ = ["add_operational_subcommands"]


def add_operational_subcommands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register build/channel/changelog/tendency/version/tier subcommands."""
    # build-release
    build_parser = subparsers.add_parser(
        "build-release",
        help="Build a release bundle (.tar.gz) of bundled data",
    )
    build_parser.add_argument(
        "--version",
        help="Release version (default: read from pyproject.toml)",
    )
    build_parser.add_argument(
        "--output-dir",
        default=".",
        help="Output directory for the bundle (default: current directory)",
    )
    build_parser.add_argument(
        "--push",
        action="store_true",
        help="Push release to backend after building",
    )
    build_parser.add_argument(
        "--backend-url",
        help="Backend API base URL (required with --push)",
    )
    build_parser.add_argument(
        "--api-key",
        help="API key for backend authentication (required with --push)",
    )

    # prepare-candidate + commit-candidate (PRD-CORE-219 production callers)
    prepare_parser = subparsers.add_parser(
        "prepare-candidate",
        help="Persist a verified pre-edit ownership claim for an isolated candidate commit",
    )
    prepare_parser.add_argument(
        "--path",
        action="append",
        required=True,
        dest="paths",
        help="Repository-relative path to claim before editing (repeatable)",
    )
    prepare_parser.add_argument("--run-dir", required=True, help="Active repository-local TRW run directory")
    prepare_parser.add_argument("--transaction-id", default="", help="Explicit transaction id (default: generated)")
    prepare_parser.add_argument("--repo-root", default=".", help="Repository root (default: current directory)")

    commit_parser = subparsers.add_parser(
        "commit-candidate",
        help=(
            "Publish an isolated candidate commit (review-bound, CAS ref, "
            "native-integration handoff) without touching shared checkout state"
        ),
    )
    commit_parser.add_argument(
        "--message-file",
        required=True,
        help="File containing the commit message (avoids shell-quoting hazards)",
    )
    commit_parser.add_argument(
        "--transaction-id",
        required=True,
        help="Transaction id returned by prepare-candidate",
    )
    commit_parser.add_argument(
        "--repo-root",
        default=".",
        help="Repository root (default: current directory)",
    )
    commit_parser.add_argument(
        "--run-dir",
        required=True,
        help="Active repository-local TRW run directory used during preparation",
    )
    commit_parser.add_argument(
        "--require-signature",
        action="store_true",
        help="Require a verifiable commit signature before publication",
    )

    # prd-state (PRD-QUAL-121-FR04 production caller)
    prd_state_parser = subparsers.add_parser(
        "prd-state",
        help="Transition a PRD's execution state through the WIP-limited scheduling ledger",
    )
    prd_state_parser.add_argument("--prd-id", required=True, help="PRD identifier (PRD-XXX-NNN)")
    prd_state_parser.add_argument(
        "--state",
        required=True,
        help="Target execution state (candidate|queued|active|blocked_external|...)",
    )
    prd_state_parser.add_argument(
        "--receipt",
        required=True,
        help="Authorization receipt for the scheduling action (required, non-empty)",
    )
    prd_state_parser.add_argument("--actor", required=True, help="Acting identity")
    prd_state_parser.add_argument("--owner", default="", help="Owner consuming the WIP slot")
    prd_state_parser.add_argument("--project-root", default=".", help="Project root (default: current directory)")
    prd_state_parser.add_argument(
        "--prds-dir",
        default="docs/requirements-aare-f/prds",
        help="PRD directory relative to the project root",
    )

    # channel-doctor (PRD-DIST-2400 FR18)
    cd_parser = subparsers.add_parser(
        "channel-doctor",
        help="Channel manifest hygiene: validate, init, scan locks, clean stale",
    )
    cd_parser.add_argument(
        "--project-dir",
        dest="project_dir",
        default=".",
        help="Project root directory (default: current directory)",
    )
    cd_sub = cd_parser.add_subparsers(dest="channel_doctor_command")

    cd_sub.add_parser(
        "init",
        help="Create .trw/channels/ directory and empty manifest if absent",
    )

    cd_sub.add_parser(
        "validate",
        help="Validate .trw/channels/manifest.yaml schema (exits 1 on error)",
    )

    cd_scan = cd_sub.add_parser(
        "scan",
        help="Scan for orphaned locks and stale state files",
    )
    cd_scan.add_argument(
        "--max-age-hours",
        type=int,
        default=24,
        dest="max_age_hours",
        help="Age threshold in hours for orphaned locks (default: 24)",
    )
    cd_scan.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        dest="dry_run",
        help="Report without removing (default: True for scan)",
    )

    cd_clean = cd_sub.add_parser(
        "clean",
        help="Remove orphaned locks older than --max-age-hours",
    )
    cd_clean.add_argument(
        "--max-age-hours",
        type=int,
        default=24,
        dest="max_age_hours",
        help="Age threshold in hours for orphaned locks (default: 24)",
    )
    cd_clean.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Preview removals without deleting",
    )

    # channel-doctor stats (meta-tune consumer)
    cd_stats = cd_sub.add_parser(
        "stats",
        help="Show per-channel correlation + throttle stats (meta-tune consumer)",
    )
    cd_stats.add_argument(
        "--window-hours",
        type=int,
        default=1,
        dest="window_hours",
        help="Correlation time window in hours (default: 1)",
    )
    cd_stats.add_argument(
        "--json",
        action="store_true",
        dest="json",
        help="Output stats as JSON instead of a human table",
    )

    # channel-doctor throttle (meta-tune consumer)
    cd_throttle = cd_sub.add_parser(
        "throttle",
        help="Evaluate (and optionally apply) throttle decisions for all channels",
    )
    cd_throttle.add_argument(
        "--window-hours",
        type=int,
        default=1,
        dest="window_hours",
        help="Correlation time window in hours (default: 1)",
    )
    cd_throttle.add_argument(
        "--apply",
        action="store_true",
        dest="apply",
        help="Execute tier changes (default: dry-run)",
    )
    cd_throttle.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Preview throttle decisions without applying (default mode)",
    )

    # session-changelog (PRD-LOCAL-049 FR04) — regenerate/print a run's changelog
    changelog_parser = subparsers.add_parser(
        "session-changelog",
        help="Regenerate or print the session changelog for a run path (read-only unless --write)",
    )
    changelog_parser.add_argument(
        "run_path",
        help="Path to the run directory (the dir containing meta/).",
    )
    changelog_parser.add_argument(
        "--write",
        action="store_true",
        help="Persist the report to <run>/reports/session-changelog.md and print its path.",
    )
    changelog_parser.add_argument(
        "--advisory",
        action="store_true",
        help="Include the package-changelog coverage advisory (FR03).",
    )

    # tendencies (PRD-QUAL-109 FR-03) — advisory AI-development tendency report
    tendencies_parser = subparsers.add_parser(
        "tendencies",
        help="Advisory scan for AI-development tendencies (PRD-count uniformity, stub-closure chains, "
        "benchmark saturation, status-flip-only PRDs). Exit 0 always; never blocks.",
    )
    tendencies_parser.add_argument(
        "--corpus",
        default=None,
        help="Corpus root to scan (default: .trw/distill/handoff-archive + the PRD catalogue when present).",
    )
    tendencies_parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit findings as JSON for CI/telemetry ingestion instead of a human report.",
    )

    # version-status
    version_parser = subparsers.add_parser(
        "version-status",
        help="Print authoritative package/framework/live-server version status.",
    )
    version_parser.add_argument(
        "--project-root",
        default=".",
        help="Project root containing package manifests and .trw/frameworks/VERSION.yaml.",
    )
    version_parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero when authoritative version surfaces drift.",
    )

    # tier (PRD-DIST-1996, c748): operator entitlement provisioning
    tier_parser = subparsers.add_parser(
        "tier",
        help="Manage TRW tier entitlements (.trw/entitlements.yaml)",
    )
    tier_sub = tier_parser.add_subparsers(dest="tier_command")

    # tier issue
    issue_parser = tier_sub.add_parser(
        "issue",
        help="Generate a signed entitlement YAML",
    )
    issue_parser.add_argument(
        "--tier",
        choices=("free", "team", "pro", "enterprise", "beta"),
        required=True,
        help="Tier to issue (beta = tester-program bridge)",
    )
    issue_parser.add_argument(
        "--issued-to",
        required=True,
        help="Operator identifier (email, username, or org name)",
    )
    issue_parser.add_argument(
        "--expires",
        required=True,
        help="Expiry date ISO-8601 (e.g. 2027-01-01 or 2027-01-01T00:00:00+00:00)",
    )
    issue_parser.add_argument(
        "--trw-dir",
        default=".trw",
        help="Target .trw/ directory (default: ./.trw)",
    )
    issue_parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print the YAML to stdout instead of writing to disk",
    )

    # tier show
    tier_sub.add_parser(
        "show",
        help="Print resolved tier + status from .trw/entitlements.yaml",
    )
    status_parser = tier_sub.add_parser(
        "status",
        help="Print tier entitlement status as an auditable table",
    )
    status_parser.add_argument(
        "--trw-dir",
        default=".trw",
        help="Target .trw/ directory (default: ./.trw)",
    )
