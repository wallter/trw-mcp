"""Profile-system config fields — PRD-HPO-PROF-001 §9 (feature flag).

Mixed into ``_TRWConfigFields`` via multiple inheritance. Kept as its own
small mixin (well under the 200-raw-line domain-mixin gate enforced by
``tests/test_config_fields.py::test_domain_mixin_files_under_200_lines``).

``profile_system_enabled`` gates the H2 hierarchical profile resolution at
``trw_session_start``. Default ``True`` per the PRD Phase-1 rollout — the
resolver is fail-open, so a disabled flag (or any resolution error) simply
omits the ``resolved_profile`` block from the session-start payload without
affecting the rest of the ceremony.
"""

from __future__ import annotations


class _ProfileFields:
    """Profile-system domain mixin — mixed into _TRWConfigFields via MI."""

    # -- Profile system (PRD-HPO-PROF-001 H2 adaptive surface) --

    profile_system_enabled: bool = True
