"""Tool-catalogue composition for the generated protocol block (PRD-CORE-247-FR08).

Belongs to the ``_renderer.py`` facade, which keeps ``ProtocolRenderer`` and is
itself under a tighter 350 RAW-line ceiling than the repo default
(``tests/test_module_loc_gate.py``). The decision this module owns is small but
has two independent inputs — the client profile and a config field — so it is
worth naming rather than inlining as a conditional expression.

**The rule.** A full-ceremony client gets a POINTER to the two tools that
enumerate the live surface. A hand-copied table of 2,777 characters is 53 percent
of the block and can drift from what the server actually exposes; a pointer
cannot. A light-ceremony client keeps the VERBATIM table unconditionally,
whatever ``instruction_catalogue_mode`` says, because for those clients the
generated instruction file IS the protocol carrier
(``.trw/frameworks/FRAMEWORK-CORE.md``, RIGID / FLEXIBLE TOOL CLASSIFICATION) and
such a client may not be able to make the discovery call at all. The profile
wins; the config field decides only the full-ceremony case.
"""

from __future__ import annotations

#: The pointer that replaces the verbatim catalogue. Named tools, not a vague
#: "ask the server": a directive an agent cannot execute is the failure mode this
#: whole PRD exists to remove.
CEREMONY_POINTER = (
    "### Tool Lifecycle\n"
    "\n"
    "Call `trw_skill_discovery()` for the live tool and skill surface, and `trw_status()` for "
    "the phase you are in and what it expects next. Both read the running server, so neither "
    "can drift from what is actually exposed to you.\n"
    "\n"
)


def catalogue_is_verbatim(ceremony_mode: str) -> bool:
    """Whether this profile should receive the verbatim tool table.

    Args:
        ceremony_mode: The resolved client profile's ``ceremony_mode``.

    Returns:
        ``True`` for every light-ceremony profile, and for a full-ceremony
        profile only when ``instruction_catalogue_mode`` is ``"verbatim"``.
    """
    if ceremony_mode == "light":
        return True
    from trw_mcp.models.config import get_config

    return str(getattr(get_config(), "instruction_catalogue_mode", "pointer")) == "verbatim"


__all__ = ["CEREMONY_POINTER", "catalogue_is_verbatim"]
