"""Session changelog markdown rendering (PRD-LOCAL-049).

Belongs to the ``trw_mcp.state._session_changelog`` builder. Split out to keep
the builder under the 350 effective-LOC gate. Holds the pure
markdown-rendering helpers and the ``PackageChangelogCoverage`` dataclass
(shared between the builder and the renderer).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class PackageChangelogCoverage:
    """Per-package changelog coverage status for the FR03 advisory."""

    package_root: str
    changed_files: int
    changelog_path: str | None
    changelog_updated: bool


def render_minimal(run_path: Path, warnings: list[str]) -> str:
    """Minimal degraded report used when the builder fails internally."""
    lines = [
        "# Session Changelog",
        "",
        f"- Run: `{run_path}`",
        "",
        "## Summary",
        "",
        "Session changelog generation degraded; evidence could not be fully assembled.",
        "",
    ]
    if warnings:
        lines += ["## Residual Risks", ""]
        lines.extend(f"- {w}" for w in warnings)
        lines.append("")
    return "\n".join(lines)


def _extract_followups(events: list[dict[str, object]]) -> list[str]:
    """Pull explicit follow-up / TODO signals from durable run events."""
    followups: list[str] = []
    for event in events:
        if str(event.get("event", "")) in {"followup", "follow_up", "todo"}:
            detail = str(event.get("detail", "") or event.get("message", "")).strip()
            if detail:
                followups.append(detail[:240])
    return followups


def _section_summary(
    commits: list[dict[str, str]], by_package: dict[str, list[str]], learnings_recorded: int
) -> list[str]:
    commit_phrase = f"{len(commits)} commit(s)" if commits else "no commits"
    file_count = sum(len(v) for v in by_package.values())
    return [
        "## Summary",
        "",
        (
            f"Session recorded {commit_phrase}, {file_count} changed file(s) across "
            f"{len(by_package)} package root(s), and {learnings_recorded} learning(s)."
        ),
        "",
    ]


def _section_commits(commits: list[dict[str, str]]) -> list[str]:
    lines = ["## Commits Made", ""]
    if commits:
        lines.extend(f"- `{c['sha']}` {c['subject']} ({c['author']}, {c['date']})" for c in commits)
    else:
        lines.append("No commits were recorded for this session.")
    lines.append("")
    return lines


def _section_files(by_package: dict[str, list[str]]) -> list[str]:
    lines = ["## Files Changed (by package/root)", ""]
    if by_package:
        for package_root, paths in sorted(by_package.items()):
            label = "(repo root)" if package_root == "." else package_root
            lines.append(f"### {label}")
            lines.extend(f"- `{p}`" for p in sorted(paths))
            lines.append("")
    else:
        lines += ["No working-tree changes detected (or git unavailable).", ""]
    return lines


def _section_validation(build: dict[str, object] | None) -> list[str]:
    lines = ["## Validation Evidence", ""]
    if build is not None:
        lines.append("- Source: `.trw/context/build-status.yaml`")
        lines.append(
            f"- scope={build.get('scope', 'unknown')} "
            f"tests_passed={build.get('tests_passed', 'unknown')} "
            f"coverage_pct={build.get('coverage_pct', 'unknown')}"
        )
    else:
        lines.append("- Build status: unknown (no `.trw/context/build-status.yaml`).")
    lines.append("")
    return lines


def _section_review(review: dict[str, object] | None, run_path: Path) -> list[str]:
    lines = ["## Review Evidence", ""]
    if review is not None:
        lines.append(f"- Source: `{run_path.name}/meta/review.yaml`")
        lines.append(f"- verdict={review.get('verdict', 'unknown')}")
        findings = review.get("findings")
        if isinstance(findings, list) and findings:
            lines.append(f"- findings: {len(findings)}")
    else:
        lines.append("- Review: unknown (no `meta/review.yaml` for this run).")
    lines.append("")
    return lines


def _section_advisory(advisory: list[PackageChangelogCoverage]) -> list[str]:
    lines = ["## Package Changelog Advisory", ""]
    if advisory:
        for cov in advisory:
            label = "(repo root)" if cov.package_root == "." else cov.package_root
            if cov.changelog_path is None:
                status = "no CHANGELOG.md found (advisory; no failure)"
            elif cov.changelog_updated:
                status = f"covered (`{cov.changelog_path}` updated)"
            else:
                status = f"NOT updated (`{cov.changelog_path}` exists but unchanged)"
            lines.append(f"- {label}: {cov.changed_files} changed file(s) — {status}")
    else:
        lines.append("- No changed package roots, or git unavailable.")
    lines.append("")
    return lines


def render_markdown(
    *,
    run_path: Path,
    commits: list[dict[str, str]],
    by_package: dict[str, list[str]],
    review: dict[str, object] | None,
    build: dict[str, object] | None,
    events: list[dict[str, object]],
    learnings_recorded: int,
    advisory: list[PackageChangelogCoverage],
    advisory_enabled: bool,
    warnings: list[str],
) -> str:
    """Render the full session changelog markdown from assembled evidence."""
    lines: list[str] = ["# Session Changelog", "", f"- Run: `{run_path}`", f"- Run id: `{run_path.name}`", ""]
    lines += _section_summary(commits, by_package, learnings_recorded)
    lines += _section_commits(commits)
    lines += _section_files(by_package)
    lines += _section_validation(build)
    lines += _section_review(review, run_path)
    lines += [
        "## Learnings Recorded",
        "",
        f"- {learnings_recorded} learning event(s) found in `meta/events.jsonl`.",
        "",
    ]
    if advisory_enabled:
        lines += _section_advisory(advisory)

    lines += ["## Residual Risks", ""]
    if warnings:
        lines.extend(f"- {w}" for w in warnings)
    else:
        lines.append("- None recorded.")
    lines.append("")

    lines += ["## Follow-ups", ""]
    followups = _extract_followups(events)
    if followups:
        lines.extend(f"- {f}" for f in followups)
    else:
        lines.append("- None recorded in run events.")
    lines.append("")
    return "\n".join(lines)
