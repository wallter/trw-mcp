"""FR16 smart-merge of TRW config into an existing ``opencode.json``.

Belongs to the ``_opencode.py`` facade. Re-exported there for back-compat.
"""

from __future__ import annotations

from trw_mcp.models.typed_dicts._opencode import OpencodeConfig, OpencodeServerEntry

_DEFAULT_PERMISSIONS: dict[str, str] = {"bash": "ask", "write": "ask", "edit": "ask"}

#: The TRW-owned instruction file opencode must be told to load. Single source
#: for both the fresh-install seed and the merge path, so the two cannot drift
#: into referencing different files (PRD-CORE-240-FR05).
OPENCODE_INSTRUCTIONS_ENTRY = ".opencode/INSTRUCTIONS.md"


def merge_opencode_json(
    existing: OpencodeConfig,
    trw_entry: OpencodeServerEntry,
) -> OpencodeConfig:
    """Smart merge TRW config into existing opencode.json (FR16).

    Rules:
    - Add/update "trw" entry under "mcp" without removing other servers.
    - Add "permission" defaults only if "permission" key doesn't exist.
    - NEVER overwrite user's "model", "small_model", "agent".
    - APPEND the TRW instructions artifact to "instructions", preserving every
      user entry at its original index (PRD-CORE-240-FR05).
    - Preserve all other keys.
    """
    # Start from existing config via unpacking — preserves all user keys
    # (model, small_model, agent, instructions) without dynamic key access.
    result: OpencodeConfig = {**existing}

    # Update "mcp" section: add/update "trw" key, preserve others
    mcp: dict[str, OpencodeServerEntry] = dict(result.get("mcp", {}))
    mcp["trw"] = trw_entry
    result["mcp"] = mcp

    # Add default permissions only when the key is absent
    if "permission" not in result:
        result["permission"] = dict(_DEFAULT_PERMISSIONS)

    result["instructions"] = _merge_instructions(result.get("instructions"))

    return result


def _merge_instructions(existing: list[str] | None) -> list[str]:
    """Return the user's ``instructions`` array with the TRW artifact appended once.

    OpenCode has no in-file include syntax; ``opencode.json``'s ``instructions``
    array is how a file gets loaded. TRW writes ``.opencode/INSTRUCTIONS.md``
    unconditionally, but only the FRESH-INSTALL branch ever seeded the array —
    this merge path documented that it "never overwrites the user's instructions
    key" and, correctly, never did, but it never *added* to it either. So for any
    project that had an ``opencode.json`` before TRW was installed, the
    instructions file was written and referenced by nothing: a file loaded by
    nobody, with every surface reporting success (PRD-CORE-240-FR05).

    Appending is safe precisely because the array is multi-valued — unlike
    codex's single-valued ``model_instructions_file``, which is why that client
    is deliberately NOT repointed. User entries keep their original order and
    index; the TRW entry goes last; a re-run appends nothing.
    """
    entries = list(existing) if existing else []
    if OPENCODE_INSTRUCTIONS_ENTRY not in entries:
        entries.append(OPENCODE_INSTRUCTIONS_ENTRY)
    return entries
