"""Feedback-reporting section renderer (PRD-INFRA-132 FR02).

Injects a marker-wrapped "Reporting Issues to TRW" block into the bundled
CLAUDE.md / AGENTS.md so AI agents have a single direct action when the
operator asks how to report a TRW bug. Light-mode profiles
(``ceremony_mode == "light"``) receive a one-line variant capped at
120 characters per PRD-INFRA-132 NFR03 to preserve their constrained
instruction budget.

Belongs to the ``_static_sections.py`` facade family. Re-exported from
``sections/__init__.py`` for back-compat with the public renderer API.
"""

from __future__ import annotations

from trw_mcp.models.config._client_profile import ClientProfile

# Marker sentinels — smart-merge preserves operator edits OUTSIDE this block
# while rewriting the inside on every sync. Tests assert both sentinels are
# present in the full-mode output.
FEEDBACK_MARKER_START = "<!-- BEGIN: feedback-reporting -->"
FEEDBACK_MARKER_END = "<!-- END: feedback-reporting -->"

# Public llms.txt anchor — single source of truth for the light-mode link.
_LLMS_TXT_ANCHOR = "https://trwframework.com/llms.txt#reporting-issues-to-trw"

# The 6 SubmissionCategory enum values (PRD-CORE-182 — canonical backend
# enum at backend/routers/submissions.py::SubmissionCategory). The full-mode
# block names each one so an agent answering "how do I file a bug?" can
# quote the valid categories without a second lookup.
_CATEGORIES: tuple[str, ...] = (
    "bugfix",
    "installation",
    "feedback",
    "feature_request",
    "question",
    "other",
)


def render_feedback_reporting(profile: ClientProfile) -> str:
    """Render the feedback-reporting fragment for the given client profile.

    Returns an empty string when the profile opts out of the skill
    (``profile.feedback_skill is None``); the bootstrapper still installs the
    llms.txt section elsewhere (FR01) so the operator retains a discovery
    surface.

    Light-mode profiles (``ceremony_mode == "light"``) get a single-line
    variant capped at 120 characters (NFR03). All other profiles get the full
    marker-wrapped block naming the MCP tool, the skill, the 5 categories,
    and the auth note.
    """
    if profile.feedback_skill is None:
        return ""

    skill_name = profile.feedback_skill
    skill_invocation = f"/{skill_name}"

    if profile.ceremony_mode == "light":
        # NFR03 hard cap: 120 chars. Tests enforce this.
        return f"TRW issues: see {_LLMS_TXT_ANCHOR} or call {skill_invocation}.\n"

    categories_csv = ", ".join(f"`{c}`" for c in _CATEGORIES)
    return (
        f"{FEEDBACK_MARKER_START}\n"
        "### Reporting Issues to TRW\n"
        "\n"
        f"When the operator asks how to report a TRW bug, install issue, or feature request, "
        f"surface the `{skill_invocation}` skill or call the `trw_submit_feedback` MCP tool "
        "directly. Both wrap the authenticated `POST /v1/submissions` channel "
        "(PRD-CORE-182).\n"
        "\n"
        f"Valid `category` values: {categories_csv}.\n"
        "\n"
        f"The channel is auth-gated via the operator's `platform_api_key` from "
        "`.trw/config.yaml`. PII redaction (license keys, API key prefixes, "
        "`$HOME` paths, sensitive env vars) runs before the network call. "
        f"Canonical operator-facing description: {_LLMS_TXT_ANCHOR}.\n"
        f"{FEEDBACK_MARKER_END}\n"
    )
