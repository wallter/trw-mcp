"""Copilot distill channel bootstrap — install entry-point.

Installs all Copilot distill channel artifacts at ``init-project``
and ``update-project`` time. Called from ``bootstrap/_init_project_ide.py``
and ``bootstrap/_ide_targets.py``.

Artifacts written:
  - .github/copilot-instructions.md segment   (C1 T0 beacon, marker_replace)
  - .github/instructions/trw-distill-hotspots.instructions.md  (C2 stub)
  - .vscode/mcp.json                           (C3 json_key_merge)
  - .github/hooks/trw-copilot-distill-hint.sh  (C5 preToolUse hint hook)
  - .github/hooks/lib-copilot-distill-hint.sh  (C5 shared hook library)
  - .trw/channels/manifest.yaml               (three copilot channel entries merged)

C4 (copilot-mcp-tool-return) is a pull channel — no file written.

C5 (copilot-pretooluse-hint, PRD-DIST-2459 FR-5) chains the trw-distill
before-edit hint AFTER the authoritative deliver-gate inside the single
preToolUse slot, via trw-copilot-adapter.sh. The hint is opt-in
(cc03_hook_enabled), advisory only, and can never flip an allow into a deny.

IP boundary: nothing here imports ``trw_distill``. The hook crosses the
boundary only through the distill-unaware ``compute_before_edit_hint`` sidecar
reader.

PRD-DIST-2406 FR41-FR43; PRD-DIST-2459 FR-5.

PRD-CORE-239 FR01 removed this client's instruction-file segment channel(s);
the counts above are the post-removal reality. Prose that outlives the code it
describes is defect pattern P7 — the class this whole removal was about.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.bootstrap._distill_channel_manifest import merge_distill_channel_manifest
from trw_mcp.bootstrap._file_ops import _new_result
from trw_mcp.channels._manifest_loader import ManifestValidationError

log = structlog.get_logger(__name__)

__all__ = [
    "bootstrap_copilot_channel_manifest",
    "install_copilot_distill_channels",
]

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent / "data" / "copilot" / "channels"
_MANIFEST_DATA = _DATA_DIR / "manifest-copilot.yaml"

# C5 hook scripts (PRD-DIST-2459 FR-5). Installed to .github/hooks/ alongside
# trw-copilot-adapter.sh so the adapter can locate the hint hook next to itself.
_HOOKS_DATA_DIR = Path(__file__).parent.parent / "data" / "copilot" / "hooks"
_C5_HOOK_NAMES: tuple[str, ...] = (
    "trw-copilot-distill-hint.sh",
    "lib-copilot-distill-hint.sh",
)

# ---------------------------------------------------------------------------
# Manifest bootstrap
# ---------------------------------------------------------------------------


def bootstrap_copilot_channel_manifest(repo_root: Path) -> dict[str, object]:
    """Add Copilot channel entries while preserving other clients."""
    added, total = merge_distill_channel_manifest(repo_root, _MANIFEST_DATA, "copilot")
    log.debug(
        "copilot_manifest_bootstrapped",
        added=added,
        total=total,
        outcome="ok",
    )
    return {"status": "ok", "count": added}


# ---------------------------------------------------------------------------
# C5 hook-script install (PRD-DIST-2459 FR-5)
# ---------------------------------------------------------------------------


def _install_c5_hook(
    repo_root: Path,
    hook_name: str,
    result: dict[str, list[str]],
) -> None:
    """Install a bundled C5 hook script to .github/hooks/ if the source exists.

    Idempotent: identical content is reported as ``preserved``. The hint hook
    is chmod +x. Fail-soft on any OSError.
    """
    src = _HOOKS_DATA_DIR / hook_name
    if not src.exists():
        log.debug("copilot_c5_hook_source_absent", hook=hook_name, outcome="skipped")
        return

    content = src.read_text(encoding="utf-8")
    hooks_dir = repo_root / ".github" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    dest = hooks_dir / hook_name
    rel = f".github/hooks/{hook_name}"

    try:
        existed = dest.exists()
        if existed and dest.read_text(encoding="utf-8") == content:
            result["preserved"].append(rel)
            return
        dest.write_text(content, encoding="utf-8")
        dest.chmod(dest.stat().st_mode | 0o111)
        result["updated" if existed else "created"].append(rel)
    except OSError as exc:
        result["errors"].append(f"Failed to install {hook_name}: {exc}")


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------


def install_copilot_distill_channels(
    target_dir: Path,
    force: bool = False,
) -> dict[str, list[str]]:
    """Install all Copilot distill channel artifacts.

    Installs C1 T0 beacon, C2 path-instructions stub, C3 vscode mcp.json,
    and merges channel manifest entries.

    Args:
        target_dir: Repository root directory.
        force: When True, overwrite existing artifacts unconditionally.

    Returns:
        Dict with ``created``, ``updated``, ``preserved``, ``errors`` lists.
    """
    result = _new_result()

    # 1. C3: .vscode/mcp.json (json_key_merge)
    try:
        from trw_mcp.channels.copilot import generate_vscode_mcp_config

        vscode_result = generate_vscode_mcp_config(target_dir, force=force)
        result["created"].extend(vscode_result.get("created", []))
        result["updated"].extend(vscode_result.get("updated", []))
        result["preserved"].extend(vscode_result.get("preserved", []))
        result["errors"].extend(vscode_result.get("errors", []))
    except Exception as exc:  # justified: fail-open, vscode config is best-effort
        log.warning("copilot_vscode_mcp_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"Copilot .vscode/mcp.json install failed: {exc}")

    # 2. PRD-CORE-239: the C2 path-instructions stub is NO LONGER WRITTEN.
    #    It planted `run `trw-distill self-improve risk-report`` into
    #    .github/instructions/ for EVERY Copilot project, including the
    #    overwhelming majority whose owners do not license trw-distill — they
    #    were handed an instruction that yields "command not found". The
    #    channel it seeded (copilot-instructions-distill) never rendered once
    #    in production (wiring gate: NEVER_FIRED, ledger UF-010), so nothing of
    #    value is lost. Copilot users keep the .vscode/mcp.json config above and
    #    the C5 hint hooks below, both of which reach the free MCP tools.

    # 3. C5: install preToolUse distill-hint hook scripts to .github/hooks/
    #    (PRD-DIST-2459 FR-5). Chained by trw-copilot-adapter.sh after the
    #    authoritative deliver-gate. Opt-in via cc03_hook_enabled; advisory only.
    for hook_name in _C5_HOOK_NAMES:
        try:
            _install_c5_hook(target_dir, hook_name, result)
        except Exception as exc:  # justified: fail-open, hook install is best-effort
            log.warning("copilot_c5_hook_install_failed", hook=hook_name, error=str(exc), outcome="warning")
            result["errors"].append(f"Copilot C5 hook {hook_name} install failed: {exc}")

    # 4. Bootstrap channel manifest (three copilot channel entries)
    try:
        bootstrap_copilot_channel_manifest(target_dir)
    except ManifestValidationError as exc:
        log.warning(
            "copilot_manifest_validation_error",
            error=str(exc),
            outcome="warning",
        )
        result["errors"].append(f"Copilot manifest bootstrap failed: {exc}")
    except Exception as exc:  # justified: fail-open, manifest is best-effort
        log.warning("copilot_manifest_bootstrap_failed", error=str(exc), outcome="warning")
        result["errors"].append(f"Copilot manifest bootstrap failed: {exc}")

    log.debug(
        "copilot_distill_channels_installed",
        repo_root=str(target_dir),
        created=len(result["created"]),
        updated=len(result["updated"]),
        errors=len(result["errors"]),
        outcome="ok",
    )
    return result
