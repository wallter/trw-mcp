"""Derive channel-render contracts from ``.trw/channels/manifest.yaml`` (FR01/OQ-01).

The registry is **derived, never authored**. OQ-01 is resolved by measurement:
across the PRD corpus, zero PRDs carried an optional ``consumer:`` /
``wiring_test:`` / ``surface:`` field before 2026-07-24. Optional authoring work
does not happen, so a hand-filled manifest would recreate the exact failure this
detector exists to catch. Every channel contract below is read out of a file the
``channels`` subsystem already maintains for its own purposes.

``PyYAML`` is used to parse it. That is not a new dependency: it is already a
declared trw-mcp runtime dependency (``trw-mcp/pyproject.toml``), and
hand-rolling a YAML subset parser for a 34 KB manifest would trade a real
correctness risk for a nominal one (NFR01's intent is "nothing new to install").
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

MANIFEST_RELATIVE_PATH = ".trw/channels/manifest.yaml"


class ManifestUnavailableError(RuntimeError):
    """The channel manifest is missing or unreadable.

    Raised, never swallowed: a detector that silently passes when its input
    disappeared is the failure mode this whole PRD is built against
    (``test_missing_manifest_fails_loudly``).
    """


MARKER_STRATEGY = "MARKER_REPLACE"


@dataclass(frozen=True)
class ChannelDeclaration:
    """One entry of ``.trw/channels/manifest.yaml``, reduced to its observable claim."""

    channel_id: str
    client: str
    status: str
    target_file: str
    lock_file: str
    marker: str
    write_strategy: str
    activation_gate: str

    @property
    def declares_dormant(self) -> bool:
        """True when the manifest itself says this channel is not meant to fire.

        OQ-02's resolution in its cleanest form: dormancy is declared by the
        *subject*, in a field the subject already maintains
        (``status`` / ``activation_gate``), and the detector *verifies the
        claim* rather than suppressing the check. A dormant channel is still
        checked — with the assertion inverted (see ``expects_output``).
        """
        return self.status.strip().lower() != "active" or bool(self.activation_gate)

    @property
    def is_observable(self) -> bool:
        """True when this channel declares an artifact that is unambiguously its own.

        Deliberately narrow. Only ``MARKER_REPLACE`` channels qualify, because a
        marker string is written by the channel and by nothing else — its
        presence or absence is a fact about the channel.

        ``FULL_REWRITE`` and ``APPEND`` targets are excluded even though they are
        easy to stat: ``.claude/hooks/pre-tool-distill-hint.sh`` exists because
        the installer bundles it, not because a channel rendered it, so file
        existence would prove the wrong thing in both directions. Absence is
        equally ambiguous — a missing ``.vscode/mcp.json`` usually means Copilot
        is not installed here, and "install another client" is not a remedy
        anyone can act on. NFR04 makes that a false positive by definition.
        ``NONE``/``EPHEMERAL_STDOUT`` surfaces leave nothing on disk at all.
        """
        if self.write_strategy.strip().upper() != MARKER_STRATEGY or not self.target_file:
            return False
        return "{" not in self.target_file and not self.target_file.startswith("~")


def _as_str(raw: object) -> str:
    return raw.strip() if isinstance(raw, str) else ""


def load_channel_declarations(repo_root: Path) -> tuple[ChannelDeclaration, ...]:
    """Parse every channel entry from the manifest, in manifest order.

    Raises:
        ManifestUnavailableError: the manifest is absent, unreadable, malformed,
            or declares no channels.
    """
    manifest_path = repo_root / MANIFEST_RELATIVE_PATH
    if not manifest_path.is_file():
        raise ManifestUnavailableError(
            f"channel manifest not found at {manifest_path} — the wiring detector cannot "
            "verify channel render contracts without it. This is a hard failure, not a skip."
        )
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ManifestUnavailableError(f"channel manifest at {manifest_path} could not be parsed: {exc}") from exc

    channels = raw.get("channels") if isinstance(raw, dict) else None
    if not isinstance(channels, list) or not channels:
        raise ManifestUnavailableError(f"channel manifest at {manifest_path} declares no 'channels' list")

    declarations: list[ChannelDeclaration] = []
    for entry in channels:
        if not isinstance(entry, dict):
            continue
        markers = entry.get("markers")
        marker_start = _as_str(markers.get("start")) if isinstance(markers, dict) else ""
        channel_id = _as_str(entry.get("id"))
        if not channel_id:
            continue
        declarations.append(
            ChannelDeclaration(
                channel_id=channel_id,
                client=_as_str(entry.get("client")) or "unknown",
                status=_as_str(entry.get("status")) or "unknown",
                target_file=_as_str(entry.get("file")),
                lock_file=_as_str(entry.get("lock_file")),
                marker=marker_start,
                write_strategy=_as_str(entry.get("write_strategy")) or "UNKNOWN",
                activation_gate=_as_str(entry.get("activation_gate")),
            )
        )
    if not declarations:
        raise ManifestUnavailableError(f"channel manifest at {manifest_path} yielded zero usable channel entries")
    return tuple(declarations)
