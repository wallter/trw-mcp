"""Profile-system config fields — PRD-HPO-PROF-001 §9.

Mixed into ``_TRWConfigFields`` via multiple inheritance. Kept as its own
small mixin (well under the 200-raw-line domain-mixin gate enforced by
``tests/test_config_fields.py::test_domain_mixin_files_under_200_lines``).

``profile_system_enabled`` gates the H2 hierarchical profile resolution at
``trw_session_start``. Default ``True`` per the PRD Phase-1 rollout — the
resolver is fail-open, so a disabled flag (or any resolution error) simply
omits the ``resolved_profile`` block from the session-start payload without
affecting the rest of the ceremony.

``profile_domain_path_map`` is the FR-6 path→domain table. It is a config
field rather than a code constant because a source layout is a property of the
consuming project, not of this package: shipping one project's package tree as
the built-in table makes every other project's inference silently wrong.
"""

from __future__ import annotations

from pydantic import Field

from trw_mcp.models.config._defaults import DEFAULT_DOMAIN_PATH_MAP


class _ProfileFields:
    """Profile-system domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Profile system (PRD-HPO-PROF-001 H2 adaptive surface) --

    profile_system_enabled: bool = True

    # -- Domain inference (PRD-HPO-PROF-001 FR-6) --

    profile_domain_path_map: dict[str, str] = Field(
        default_factory=lambda: dict(DEFAULT_DOMAIN_PATH_MAP),
        description=(
            "Source-path prefix -> profile 'domain' layer name, used by infer_domain when no "
            "explicit domain is supplied. Keys are repo-relative directory prefixes (a trailing "
            "slash is optional); longest matching prefix wins. Setting this in .trw/config.yaml "
            "REPLACES the generic defaults, so a project states its own layout once."
        ),
    )
