#!/usr/bin/env python3
"""Build self-contained installer from template + wheels.

Usage:
    python scripts/build_installer.py [--wheel WHEEL] [--memory-wheel WHEEL] [--format py]

Reads the template, finds the latest trw-mcp and trw-memory wheels in
``dist/``, base64-encodes them, substitutes placeholders, and writes the
installer to ``dist/``.

Build wheels first:
    python -m build --wheel              # trw-mcp
    cd ../trw-memory && python -m build --wheel  # trw-memory
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import stat
import sys
import zipfile
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
DIST_DIR = PROJECT_ROOT / "dist"

# Template paths
TEMPLATES = {
    "py": (SCRIPT_DIR / "install-trw.template.py", "install-trw.py"),
}
DEFAULT_FORMAT = "py"

# PRD-INFRA-123 FR01 + PRD-INFRA-126 FR06 — the public installer MUST NOT
# embed proprietary wheels. The build process fails closed if any of these
# wheel filenames appears in the embed step.
_PROPRIETARY_WHEEL_PREFIXES: tuple[str, ...] = (
    "trw_distill-",
    "trw_metaharness-",  # PRD-INFRA-128: cross-monorepo dep of trw-distill (renamed from trw-harness 2026-06-10)
    "trw_harness-",  # legacy wheel name pre-2026-06-10 rename; kept so stale wheels stay fail-closed
    "trw_loop-",
    "trw_swarm-",
)


# Normalized dist names derived from the filename prefixes so the two checks
# can never drift apart (PEP 503 normalization: lowercase, '-' for '_').
_PROPRIETARY_DIST_NAMES: frozenset[str] = frozenset(
    prefix.rstrip("-").replace("_", "-") for prefix in _PROPRIETARY_WHEEL_PREFIXES
)


def _wheel_metadata_name(wheel_path: Path) -> str:
    """Return the normalized ``Name:`` from the wheel's dist-info METADATA.

    Empty string when unreadable — the filename-prefix check remains the
    first line of defense; this is the rename-proof second line.
    """
    try:
        with zipfile.ZipFile(wheel_path) as zf:
            for entry in zf.namelist():
                if entry.endswith(".dist-info/METADATA") and entry.count("/") == 1:
                    metadata = zf.read(entry).decode("utf-8", errors="replace")
                    for line in metadata.splitlines():
                        if line.lower().startswith("name:"):
                            return line.split(":", 1)[1].strip().lower().replace("_", "-")
    except (OSError, zipfile.BadZipFile, KeyError):
        return ""
    return ""


def _refuse_proprietary_wheel(wheel_path: Path) -> None:
    """Raise SystemExit if wheel_path is one of the proprietary packages.

    The public installer is BSL-1.1 and ships embedded wheel bytes for the
    open packages only. Embedding a proprietary wheel would leak the IP
    boundary defined in PRD-INFRA-123. Checks BOTH the filename prefix and
    the dist-info METADATA ``Name:`` so a renamed/re-tagged wheel cannot
    slip past the guard.
    """
    matched = next(
        (p for p in _PROPRIETARY_WHEEL_PREFIXES if wheel_path.name.startswith(p)), None
    )
    if matched is None and _wheel_metadata_name(wheel_path) in _PROPRIETARY_DIST_NAMES:
        matched = _wheel_metadata_name(wheel_path)
    if matched is not None:
        print(
            f"ERROR: refusing to embed proprietary wheel into public installer: "
            f"{wheel_path.name}",
            file=sys.stderr,
        )
        print(
            "       Proprietary packages ship via the PRD-INFRA-126 entitlement "
            "endpoint, NOT embedded in install-trw.py.",
            file=sys.stderr,
        )
        sys.exit(2)


def find_latest_wheel(dist_dir: Path, pattern: str, label: str) -> Path:
    """Find the most recent .whl file matching *pattern* in *dist_dir*."""
    wheels = sorted(dist_dir.glob(pattern), key=lambda p: p.stat().st_mtime)
    if not wheels:
        print(f"ERROR: No {pattern} found in {dist_dir}", file=sys.stderr)
        print(f"Run:  python -m build --wheel  (for {label})", file=sys.stderr)
        sys.exit(1)
    return wheels[-1]


def extract_version(wheel_path: Path) -> str:
    """Extract version from wheel filename (PEP 427 format)."""
    match = re.match(r"trw_mcp-([^-]+)-", wheel_path.name)
    if not match:
        print(f"ERROR: Cannot extract version from {wheel_path.name}", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


def _format_b64_for_python(b64: str, line_width: int = 76) -> str:
    """Wrap base64 string into comment-prefixed lines for Python embedding."""
    return "\n".join(f"# {b64[i:i+line_width]}" for i in range(0, len(b64), line_width))


def _read_template(template_path: Path) -> str:
    """Read the installer template text (seam for tests)."""
    return template_path.read_text(encoding="utf-8")


def _compute_sha256(data: bytes) -> str:
    """Return the lowercase 64-hex SHA-256 of *data* (PRD-SEC-006 FR01).

    Must hash the raw wheel bytes that are base64-embedded, so the installer's
    ``_verify_checksum`` (which hashes the decoded bytes) matches at install time.
    """
    return hashlib.sha256(data).hexdigest()


def _assert_checksums_substituted(output: str) -> None:
    """Fail the build unless both wheel checksum placeholders are 64-hex values.

    A claimed security control (install-time checksum verification) must not ship
    as a dead no-op: the template's ``_verify_checksum`` skips while the value
    starts with ``{{``. This guard makes a missing substitution a hard build
    failure rather than a silently-disabled verification (PRD-SEC-006 NFR03).
    """
    errors: list[str] = []
    for placeholder, var in (
        ("{{WHEEL_SHA256}}", "WHEEL_SHA256"),
        ("{{MEMORY_WHEEL_SHA256}}", "MEMORY_WHEEL_SHA256"),
    ):
        if placeholder in output:
            errors.append(f"checksum placeholder {placeholder} was not substituted")
            continue
        match = re.search(rf'^{var} = "([0-9a-f]{{64}})"$', output, re.MULTILINE)
        if match is None:
            errors.append(
                f"{var} is not a 64-hex SHA-256 in the generated installer "
                f"(checksum verification would be a dead no-op)"
            )
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        sys.exit(1)


# ── PRD-INFRA-150 FR01: template->dist drift gate ────────────────────
# The deterministic substitution fields that build_installer.py writes into the
# dist artifact. These — and ONLY these — are excluded from the drift comparison,
# because they legitimately differ between a generic template and a versioned
# build. Named here (NFR05: no inline literals) so the exclusion list is
# auditable and DRY with the substitution logic in build_installer().
DRIFT_SUBSTITUTION_FIELDS: tuple[str, ...] = (
    "TRW_VERSION",
    "WHEEL_FILENAME",
    "MEMORY_WHEEL_FILENAME",
    "WHEEL_SHA256",
    "MEMORY_WHEEL_SHA256",
)

# Canonical sentinel a substituted value is normalized to before comparison.
_DRIFT_CANON = "<<SUBSTITUTED>>"

# The embedded base64 wheel-data markers. The comment-prefixed blocks BETWEEN
# these markers are wheel bytes (template placeholder vs real base64) and are
# normalized away — they are part of the deterministic substitution, not logic.
_DRIFT_WHEEL_MARKERS: tuple[str, ...] = ("__WHEEL_DATA__", "__MEMORY_WHEEL_DATA__")


def _normalize_for_drift(text: str) -> str:
    """Normalize installer text so only NON-substitution divergence remains.

    Replaces each substitution field's assigned value with a canonical sentinel
    and collapses the embedded base64 wheel-data comment blocks to a single
    marker. Pure text — never imports/exec's the installer (NFR06).
    """
    lines = text.splitlines()
    out: list[str] = []
    in_wheel_block = False
    field_assign = re.compile(
        r'^(' + "|".join(re.escape(f) for f in DRIFT_SUBSTITUTION_FIELDS) + r') = ".*"$'
    )
    marker_re = re.compile(r"^# (__[A-Z_]+__)$")
    # The module docstring carries a "Version: <value>" line ({{VERSION}} in the
    # template, the build version in dist) — a substitution field, not logic.
    docstring_version_re = re.compile(r"^Version: .*$")
    for line in lines:
        stripped = line.strip()
        if in_wheel_block:
            # Wheel data lines are comment-prefixed base64 (or a "# " blank);
            # the block ends at the NEXT "# __MARKER__" line. A wheel-data marker
            # both ENDS the prior block and STARTS the next one (the embedded
            # blocks are adjacent: MEMORY data then WHEEL data).
            m = marker_re.match(stripped)
            if m is not None:
                out.append(stripped)
                in_wheel_block = m.group(1) in _DRIFT_WHEEL_MARKERS
                continue
            # Skip every line inside the embedded-wheel block.
            continue
        m = marker_re.match(stripped)
        if m is not None and m.group(1) in _DRIFT_WHEEL_MARKERS:
            in_wheel_block = True
            out.append(stripped)
            continue
        fm = field_assign.match(line)
        if fm is not None:
            out.append(f"{fm.group(1)} = {_DRIFT_CANON}")
            continue
        if docstring_version_re.match(line):
            out.append(f"Version: {_DRIFT_CANON}")
            continue
        out.append(line)
    return "\n".join(out)


def render_template_for_drift(template_text: str) -> str:
    """Render the raw template into a drift-normalized form.

    The template carries ``{{...}}`` placeholders for the substitution fields and
    the embedded wheel blocks; normalizing collapses those exactly like a built
    dist normalizes its substituted values, so a faithful render compares equal.
    """
    return _normalize_for_drift(template_text)


def verify_dist_matches_template(
    template_path: Path | None = None,
    dist_path: Path | None = None,
) -> tuple[int, str, list[str]]:
    """Verify the committed dist is a faithful render of the template (FR01).

    Returns ``(returncode, message, drifted_symbols)``. ``returncode`` is 0 when
    the dist matches the template modulo the deterministic substitution fields,
    non-zero on drift. ``drifted_symbols`` names ``def``/``class`` symbols that
    exist in one file but not the other (best-effort, for the operator message).
    """
    if template_path is None:
        template_path = TEMPLATES["py"][0]
    if dist_path is None:
        dist_path = DIST_DIR / TEMPLATES["py"][1]

    template_text = _read_template(template_path)
    dist_text = dist_path.read_text(encoding="utf-8")

    norm_template = render_template_for_drift(template_text)
    norm_dist = _normalize_for_drift(dist_text)

    if norm_template == norm_dist:
        return 0, "OK: dist/install-trw.py matches install-trw.template.py", []

    drifted = _drifted_symbols(template_text, dist_text)
    detail = f" (drifted symbols: {', '.join(drifted)})" if drifted else ""
    message = (
        "DRIFT: dist/install-trw.py is stale vs install-trw.template.py "
        f"— run 'make installer'{detail}"
    )
    return 1, message, drifted


_SYMBOL_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def _drifted_symbols(template_text: str, dist_text: str) -> list[str]:
    """Return def/class names present in one file but absent from the other."""
    template_syms = set(_SYMBOL_RE.findall(template_text))
    dist_syms = set(_SYMBOL_RE.findall(dist_text))
    return sorted(template_syms.symmetric_difference(dist_syms))


# ── PRD-INFRA-150 FR02: S3 artifact version-currency check ────────────
_VERSION_CHECK_MODES: tuple[str, ...] = ("block", "advisory")
_DEFAULT_VERSION_CHECK_MODE = "block"
_VERSION_CHECK_MODE_ENV = "TRW_INSTALLER_VERSION_CHECK_MODE"


def version_check_mode() -> str:
    """Resolve the FR02 block-vs-advisory mode from the env knob (NFR05).

    ``TRW_INSTALLER_VERSION_CHECK_MODE`` in {block, advisory}; unset or invalid
    falls back to ``block`` (the conservative release-CI default).
    """
    import os

    raw = os.environ.get(_VERSION_CHECK_MODE_ENV, "").strip().lower()
    if raw in _VERSION_CHECK_MODES:
        return raw
    return _DEFAULT_VERSION_CHECK_MODE


def verify_artifact_version_currency(
    artifact_version: str,
    pypi_version: str | None,
    mode: str | None = None,
) -> tuple[int, dict[str, str]]:
    """Verify the served installer artifact is not older than the PyPI version.

    Returns ``(returncode, record)`` where ``record`` carries ONLY
    ``pypi``/``artifact``/``status`` (NFR06: no auth token / pre-signed URL).
    ``status`` is one of ``current`` (artifact >= pypi), ``stale-artifact``
    (artifact < pypi), or ``pypi-unreachable`` (lookup unavailable). In
    ``block`` mode a stale artifact returns rc 1; ``advisory`` always returns 0.
    PEP 440 semantics via ``packaging.version`` (FR04).
    """
    resolved_mode = mode if mode in _VERSION_CHECK_MODES else version_check_mode()

    if pypi_version is None:
        # NFR02: an unreachable index is advisory regardless of mode.
        return 0, {"pypi": "(unreachable)", "artifact": artifact_version, "status": "pypi-unreachable"}

    from packaging.version import Version

    stale = Version(artifact_version) < Version(pypi_version)
    status = "stale-artifact" if stale else "current"
    record = {"pypi": pypi_version, "artifact": artifact_version, "status": status}
    if stale and resolved_mode == "block":
        return 1, record
    return 0, record


def build_installer(
    wheel_path: Path | None = None,
    memory_wheel_path: Path | None = None,
    fmt: str = DEFAULT_FORMAT,
) -> Path:
    """Build the self-contained installer script.

    Args:
        wheel_path: Path to trw-mcp wheel. Auto-finds latest if None.
        memory_wheel_path: Path to trw-memory wheel. Auto-finds latest if None.
        fmt: Output format — "py" (Python).

    Returns:
        Path to the generated installer.
    """
    template_path, output_name = TEMPLATES[fmt]

    if not template_path.exists():
        print(f"ERROR: Template not found: {template_path}", file=sys.stderr)
        sys.exit(1)
    template = _read_template(template_path)

    # Find wheels
    if wheel_path is None:
        wheel_path = find_latest_wheel(DIST_DIR, "trw_mcp-*.whl", "trw-mcp")
    elif not wheel_path.exists():
        print(f"ERROR: Wheel not found: {wheel_path}", file=sys.stderr)
        sys.exit(1)
    _refuse_proprietary_wheel(wheel_path)

    version = extract_version(wheel_path)
    print(f"Format:             {fmt}")
    print(f"Wheel (trw-mcp):    {wheel_path}")
    print(f"Version:            {version}")

    if memory_wheel_path is None:
        memory_wheel_path = find_latest_wheel(DIST_DIR, "trw_memory-*.whl", "trw-memory")
    elif not memory_wheel_path.exists():
        print(f"ERROR: Memory wheel not found: {memory_wheel_path}", file=sys.stderr)
        sys.exit(1)
    _refuse_proprietary_wheel(memory_wheel_path)

    print(f"Wheel (trw-memory): {memory_wheel_path}")

    # Read wheel bytes once (used for size, base64 embedding, and checksums).
    wheel_bytes = wheel_path.read_bytes()
    memory_bytes = memory_wheel_path.read_bytes()

    # Base64-encode wheels
    wheel_b64 = base64.b64encode(wheel_bytes).decode("ascii")
    memory_b64 = base64.b64encode(memory_bytes).decode("ascii")

    # PRD-SEC-006 FR01: SHA-256 of the RAW wheel bytes (matches install-time
    # _verify_checksum, which hashes the decoded bytes).
    wheel_sha256 = _compute_sha256(wheel_bytes)
    memory_sha256 = _compute_sha256(memory_bytes)

    wheel_mb = len(wheel_bytes) / (1024 * 1024)
    mem_mb = len(memory_bytes) / (1024 * 1024)
    print(f"Size (trw-mcp):     {wheel_mb:.1f} MB")
    print(f"Size (trw-memory):  {mem_mb:.1f} MB")
    print(f"SHA256 (trw-mcp):   {wheel_sha256}")
    print(f"SHA256 (trw-memory):{memory_sha256}")

    # Substitute placeholders
    output = template.replace("{{VERSION}}", version)
    output = output.replace("{{WHEEL_FILENAME}}", wheel_path.name)
    output = output.replace("{{MEMORY_WHEEL_FILENAME}}", memory_wheel_path.name)

    # PRD-SEC-006 FR01: substitute the wheel checksum placeholders so install-time
    # verification actually executes instead of skipping on a ``{{`` sentinel.
    output = output.replace("{{WHEEL_SHA256}}", wheel_sha256)
    output = output.replace("{{MEMORY_WHEEL_SHA256}}", memory_sha256)

    # Python template: wheel data is in comment-prefixed lines
    output = output.replace("# {{MEMORY_WHEEL_BASE64}}", _format_b64_for_python(memory_b64))
    output = output.replace("# {{WHEEL_BASE64}}", _format_b64_for_python(wheel_b64))

    # PRD-SEC-006 FR01: fail the build if either checksum placeholder remains
    # (dead verification is itself an audit finding).
    _assert_checksums_substituted(output)

    # Write output
    DIST_DIR.mkdir(exist_ok=True)
    output_path = DIST_DIR / output_name
    output_path.write_text(output, encoding="utf-8")

    # Make executable
    current = output_path.stat().st_mode
    output_path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    print(f"Output:             {output_path} ({size_mb:.1f} MB)")

    # Validate round-trip: verify embedded wheels can be extracted
    if fmt == "py":
        _validate_py_installer(output_path, wheel_path, memory_wheel_path)

    return output_path


def _validate_py_installer(
    installer_path: Path,
    expected_mcp_wheel: Path,
    expected_memory_wheel: Path,
) -> None:
    """Verify the built Python installer can extract valid wheels."""
    text = installer_path.read_text(encoding="utf-8", errors="replace")

    for marker, expected_wheel, label in [
        ("__MEMORY_WHEEL_DATA__", expected_memory_wheel, "trw-memory"),
        ("__WHEEL_DATA__", expected_mcp_wheel, "trw-mcp"),
    ]:
        collecting = False
        chunks: list[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == f"# {marker}":
                collecting = True
                continue
            if collecting:
                if stripped.startswith("# __") and stripped.endswith("__"):
                    break
                if stripped.startswith("# "):
                    chunks.append(stripped[2:])
                elif stripped == "#":
                    continue
                else:
                    break

        if not chunks:
            print(f"ERROR: No base64 data found for {marker}", file=sys.stderr)
            sys.exit(1)

        joined = "".join(chunks)
        try:
            decoded = base64.b64decode(joined)
        except Exception as exc:
            print(f"ERROR: base64 decode failed for {label} ({marker}): {exc}", file=sys.stderr)
            sys.exit(1)

        expected_size = expected_wheel.stat().st_size
        if len(decoded) != expected_size:
            print(
                f"ERROR: {label} size mismatch: decoded {len(decoded)} vs expected {expected_size}",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"Verified:           {label} wheel round-trip OK ({len(decoded)} bytes)")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Build self-contained TRW installer from template + wheels",
    )
    parser.add_argument(
        "--wheel", type=Path, default=None,
        help="Path to trw-mcp .whl file (default: latest in dist/)",
    )
    parser.add_argument(
        "--memory-wheel", type=Path, default=None,
        help="Path to trw-memory .whl file (default: latest in dist/)",
    )
    parser.add_argument(
        "--format", choices=["py"], default=DEFAULT_FORMAT,
        help="Output format (default: py)",
    )
    args = parser.parse_args()

    output = build_installer(args.wheel, args.memory_wheel, args.format)
    print(f"\nDone! Distribute: {output}")


if __name__ == "__main__":
    main()
