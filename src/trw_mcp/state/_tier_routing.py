"""Portability classifier + write-tier router -- PRD-CORE-185 FR05.

The ``trw_learn`` store path historically forced ``namespace="default"`` at
``_memory_transforms._learning_to_memory_entry``. This module adds a small,
pure, heuristic classifier (NO LLM call) that decides whether a learning is
*portable* (cross-cutting; belongs in the machine-local USER tier) or
*project-specific* (file paths / repo-local symbols / this-repo gotchas; stays
in the PROJECT tier).

Promotion is **automatic, not opt-in** (D2, RESOLVED 2026-06-03): when a
machine-local user-scope store is PRESENT/configured, portable learnings route
to it by default; ``scope=`` is an explicit override only. When no user-scope
store is present, routing collapses to project-only -- byte-identical to today
(NFR02). The gate is **presence of the store**, not a user toggle.

Default is PROJECT when signals are ambiguous (conservative: under-promote
rather than over-promote -- truthfulness > velocity).

This is a focused sibling of ``_memory_transforms.py`` (NFR07); all routing
logic lives here so the transforms/adapter modules stay under the 350 gate.
"""

from __future__ import annotations

import re
from typing import Literal

import structlog

logger = structlog.get_logger(__name__)

Scope = Literal["auto", "project", "user"]
Tier = Literal["project", "user"]

# Single-user-box constant identity for the user-space namespace. The PRD
# allows ``<id>`` to be a stable host/user slug; a constant ``local`` is the
# documented single-user default (FR03).
_USER_ID = "local"
USER_NAMESPACE = f"user:{_USER_ID}"

# Portable signal tags: operator directives, priorities, cross-cutting patterns,
# workflow/policy knowledge, raw-context drops from transcript work. Matched
# case-insensitively against the learning's tags AND domain.
_PORTABLE_TAGS: frozenset[str] = frozenset(
    {
        "directive",
        "policy",
        "workflow",
        "cross-cutting",
        "crosscutting",
        "priority",
        "operator",
        "preference",
        "transcript",
        "raw-context",
        "meta",
        "governance",
    }
)

# Project-specific signal tags: tie a learning to a specific package/repo.
_PROJECT_TAGS: frozenset[str] = frozenset(
    {
        "gotcha",
        "repo-local",
        "this-repo",
        "file-path",
        "symbol",
        "bug",
    }
)

# A repo-relative file path or repo-local symbol reference in the content is a
# strong PROJECT signal. Matches ``a/b.py``, ``src/x/y.ts``, ``foo/bar.md:42``,
# dotted module paths like ``trw_mcp.state.foo`` AND two-segment ones like
# ``os.path`` / ``foo.bar``, etc.
#
# The segments are deliberately narrow to avoid false positives on PORTABLE
# directive content. The three alternatives jointly satisfy four constraints
# (core185-1 / core185-URL-OVERMATCH-2 / core185-DOTTED-TWOSEG-4):
#   * dotted-module: each segment starts with an ALPHA/underscore so version
#     strings (``3.11.5``, ``18.20.3``, ``v1.2.3``) are NOT mistaken for modules
#     (a digit-led segment breaks the chain). Repetition is ``{1,}`` so
#     TWO-segment repo-local symbols (``os.path``, ``my_module.HelperClass``) are
#     caught -- the alpha-led guard, NOT the segment count, is what excludes
#     versions, so relaxing the count is safe (core185-DOTTED-TWOSEG-4). A
#     leading ``(?<![\w./-])`` + trailing ``(?![\w-]*[./])`` reject dotted tokens
#     that are part of a URL/path -- ``trwframework.com/install.sh`` (host
#     ``trwframework.com`` is followed by ``/``; file ``install.sh`` is preceded
#     by ``/``) is NOT a project signal (core185-URL-OVERMATCH-2).
#   * path-with-segments: the FIRST directory segment must NOT contain a dot, so
#     bare directory names (``src/x/y.ts``) match while a hostname-led URL path
#     (``trwframework.com/install.sh``) does NOT (core185-URL-OVERMATCH-2). A
#     leading ``(?<!/)`` keeps it from re-attaching to a URL tail.
#   * file:line: requires a ``name.ext`` token BEFORE the colon so bare
#     YAML-style values (``timeout:30``, ``priority:1``) do not trip it.
#   * prose-abbreviation stop-list: the dotted-module alternative led to bare
#     ``e.g`` / ``i.e`` (and trailing-dot ``e.g.`` / ``i.e.``) being read as a
#     dotted module path, mis-classifying portable learnings as project-specific
#     (core185-DOTTED-ABBREV-7). A leading negative lookahead rejects ONLY the
#     exact one-char.one-char abbreviation token; the trailing ``(?![\w.])``
#     keeps a real module that merely starts that way (``e.go``, ``i.eat``,
#     ``e.gc.foo``) matching, and legit 1-char-segment paths (``a.b.c``) are
#     unaffected.
_PATH_RE = re.compile(
    r"(?<![\w./-])[\w_-]+/(?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9]{1,6}"  # dir/.../file.ext (1st seg dot-free, not URL host)
    r"|(?<![\w./-])(?![eiEI]\.[geGE]\.?(?![\w.]))"  # reject prose abbrevs e.g / i.e
    r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*){1,}(?![\w-]*[./])"  # dotted.module (alpha-led, >=2 seg, not in URL/path)
    r"|\b[\w-]+\.[A-Za-z]{1,6}:\d+\b"  # file.ext:line reference
)


def _normalize(values: list[str] | None) -> set[str]:
    """Lowercase + strip a tag/domain list into a comparable set."""
    if not values:
        return set()
    return {v.strip().lower() for v in values if v and v.strip()}


def has_project_signal(
    *,
    tags: list[str] | None = None,
    summary: str = "",
    detail: str = "",
) -> bool:
    """Return True when content carries a strong PROJECT-specific signal.

    A project signal is a project-tying tag, a repo-relative file path, a
    dotted module/symbol reference, or a ``file:line`` reference in the
    content. This is the same detection :func:`classify_tier` uses for its
    step (1) veto; it is factored out so :func:`route_tier` can apply the
    veto even under an explicit ``scope="user"`` override (leak guard).
    """
    if _normalize(tags) & _PROJECT_TAGS:
        return True
    return bool(_PATH_RE.search(f"{summary}\n{detail}"))


def classify_tier(
    *,
    source_type: str = "agent",
    tags: list[str] | None = None,
    domain: list[str] | None = None,
    phase_affinity: list[str] | None = None,
    summary: str = "",
    detail: str = "",
) -> Tier:
    """Heuristically classify a learning as ``"user"`` (portable) or ``"project"``.

    Pure function, no I/O, no LLM. Default = ``"project"`` (conservative) when
    signals are ambiguous. Project-specific signals (repo-relative paths,
    repo-local symbol references, project-tying tags) WIN over portable signals
    so a path-bearing directive stays local rather than polluting the box-wide
    tier.

    Signals (in priority order):
      1. project tags / repo-relative path / dotted-symbol in content -> project
      2. portable tags / portable domain -> user
      3. human-sourced operator directive (source=human + directive-ish) -> user
      4. otherwise -> project (default)
    """
    tagset = _normalize(tags)
    domainset = _normalize(domain)

    # (1) Strong PROJECT signals win outright.
    if has_project_signal(tags=tags, summary=summary, detail=detail):
        return "project"

    # (2) Portable tag/domain signals -> user.
    if (tagset | domainset) & _PORTABLE_TAGS:
        return "user"

    # (3) A human-sourced learning with no project signal is operator/cross-cutting
    # knowledge (directives, priorities) -> user.
    if source_type == "human":
        return "user"

    # (4) Default conservative: project.
    return "project"


# core185-8: cache the user-scope presence probe. The presence of a
# machine-local user store is a BOOT-TIME condition (the config flag or the
# on-disk DB) that does not change within a session, so the per-call config
# read + ``Path.exists()`` on the hot path (every ``trw_learn`` route + every
# ``recall_learnings`` federate) are redundant. ``None`` = not yet probed. The
# cache is cleared by :func:`reset_user_scope_cache` (wired into
# ``reset_user_backend`` for test isolation).
_user_scope_cached: bool | None = None


def reset_user_scope_cache() -> None:
    """Clear the cached :func:`user_scope_present` result (test isolation)."""
    global _user_scope_cached
    _user_scope_cached = None


def user_scope_present() -> bool:
    """Return True when a machine-local user-scope store is PRESENT/configured.

    This is the effective gate for automatic promotion (NFR02): a user-scope
    store counts as present when EITHER

      * ``user_tier_enabled`` is set in the effective config (installer-seeded
        machine-layer knob), OR
      * the user-space memory DB already exists on disk.

    Resolution never creates the directory (``create=False``) so a mere probe
    does not provision a store. Fails closed (project-only) on any error. The
    result is memoized (core185-8); call :func:`reset_user_scope_cache` to
    re-probe (presence is otherwise a stable boot-time condition).
    """
    global _user_scope_cached
    if _user_scope_cached is not None:
        return _user_scope_cached

    result = False
    try:
        from trw_mcp.models.config import get_config

        if get_config().user_tier_enabled:
            result = True
    except Exception:  # justified: fail-closed to project-only on config error
        logger.debug("user_scope_config_probe_failed", exc_info=True)

    if not result:
        try:
            from trw_mcp.state._user_paths import resolve_user_memory_dir

            db_path = resolve_user_memory_dir(create=False) / "memory.db"
            result = db_path.exists()
        except Exception:  # justified: fail-closed to project-only on path error
            logger.debug("user_scope_path_probe_failed", exc_info=True)
            result = False

    _user_scope_cached = result
    return result


def tier_of_entry(entry: object) -> Tier:
    """Read the routed tier off a built :class:`MemoryEntry`'s metadata.

    ``_learning_to_memory_entry`` stamps ``metadata["tier"]`` with the routing
    decision. Falls back to inspecting the namespace, then to ``"project"``.
    """
    meta = getattr(entry, "metadata", None)
    if isinstance(meta, dict):
        tier = meta.get("tier")
        if tier == "user":
            return "user"
        if tier == "project":
            return "project"
    ns = getattr(entry, "namespace", "")
    return "user" if isinstance(ns, str) and ns.startswith("user:") else "project"


def route_tier(
    *,
    scope: Scope = "auto",
    source_type: str = "agent",
    tags: list[str] | None = None,
    domain: list[str] | None = None,
    phase_affinity: list[str] | None = None,
    summary: str = "",
    detail: str = "",
) -> Tier:
    """Decide the destination tier for a ``trw_learn`` write.

    Precedence:
      * No user-scope store present -> ALWAYS ``"project"`` (zero behavior
        change; explicit ``scope`` is irrelevant when there is nowhere to route).
      * Explicit ``scope="user"`` BUT content carries strong project-specific
        signals (repo-relative paths / repo-local symbols / project tags) ->
        HONORED to ``"user"`` but a structured warning is emitted (P2-C, WARN +
        HONOR). The user-tier is box-wide, so routing repo-local detail there
        risks leaking it across same-machine projects; the explicit override is
        nonetheless the user's deliberate choice and the FR07 contract requires
        it be honored. The warning surfaces the cross-project leak risk so the
        caller can re-scope if it was unintended (truthfulness via observability,
        not a silent veto).
      * Explicit ``scope="user"`` / ``scope="project"`` -> that tier (override).
      * ``scope="auto"`` -> heuristic :func:`classify_tier`.
    """
    if not user_scope_present():
        return "project"
    if scope == "user":
        if has_project_signal(tags=tags, summary=summary, detail=detail):
            logger.warning(
                "tier_routing_user_override_project_signal",
                reason="project_specific_signal",
                requested_scope="user",
                routed_tier="user",
            )
        return "user"
    if scope == "project":
        return "project"
    return classify_tier(
        source_type=source_type,
        tags=tags,
        domain=domain,
        phase_affinity=phase_affinity,
        summary=summary,
        detail=detail,
    )
