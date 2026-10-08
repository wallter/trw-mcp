"""Verbatim quotes bound to their source: carried by the tool, checked by code (SPEC R-REC-6, R-RB-8).

Belongs to the :mod:`trw_mcp.handoff` package. A constraint weakened into a paraphrase passes every
schema rule, and the 2026-10-06 skill eval saw a small-tier sender carry 0 of 14 constraints exactly.
Two mechanical helpers close most of that gap without asking any model to be careful:

- :func:`quote_from` (``trw-mcp handoff new --constraint-from <file>#L10-L14``) copies the lines out of
  the file itself and binds them to the file's raw-byte digest, so the sender never retypes them;
- :func:`quote_status` (``trw-mcp handoff check``) re-opens a constraint's digest-bound ``source`` and
  reports whether the recorded text is a verbatim substring of it.

Record content stays data: only ``file:`` sources confined to the repository are opened.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from trw_mcp.handoff._repo import confined_path, file_uri, read_capped

__all__ = ["MAX_TEXT_CHARS", "quote_from", "quote_status", "text_lines"]

JsonDoc = dict[str, Any]
#: A constraint's ``text`` is one schema text field (R-SIZE-1).
MAX_TEXT_CHARS = 2000
_MAX_SOURCE_BYTES = 2 * 1024 * 1024
_RANGE = re.compile(r"^(?P<path>.+)#L(?P<first>[1-9]\d*)(?:-L(?P<last>[1-9]\d*))?$")


def text_lines(text: str) -> list[str]:
    """The lines of ``text`` as an editor or a read tool numbers them: split at ``\\n`` only.

    ``str.splitlines`` also breaks at form feeds and U+2028, which shifts every later line number away
    from the one a person cites. A line keeps a trailing ``\\r`` (CRLF files); callers drop it to compare.
    """
    lines = text.split("\n")
    return lines[:-1] if lines[-1] == "" else lines


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def quote_from(spec: str, root: Path) -> JsonDoc:
    """``<path>#L<first>[-L<last>]`` -> a constraint ``{text, source: {uri, digest}}`` copied from the file.

    The path is relative to the working directory (or ``file:`` relative to ``root``) and must resolve inside
    ``root``. Lines are 1-based and inclusive; the text is those lines exactly, without the final line break.
    """
    match = _RANGE.match(spec)
    if match is None:
        raise ValueError(f"--constraint-from {spec}: expected <path>#L<first> or <path>#L<first>-L<last>")
    raw = match["path"]
    try:
        path = (root / raw[5:] if raw.lower().startswith("file:") else Path(raw).expanduser()).resolve()
    except (OSError, RuntimeError) as exc:  # a symlink loop raises before 3.13
        raise ValueError(f"--constraint-from {spec}: the path does not resolve") from exc
    if not path.is_relative_to(root):
        raise ValueError(f"--constraint-from {spec}: outside the repository root")
    try:
        data = read_capped(path, _MAX_SOURCE_BYTES)
        lines = text_lines(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"--constraint-from {spec}: not a readable UTF-8 file of at most 2 MiB") from exc
    first, last = int(match["first"]), int(match["last"] or match["first"])
    if last < first or last > len(lines):
        raise ValueError(f"--constraint-from {spec}: lines {first}-{last} are not in a file of {len(lines)} lines")
    text = "\n".join(lines[first - 1 : last]).rstrip("\r\n")
    if not text.strip():
        raise ValueError(f"--constraint-from {spec}: the range holds no text")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(
            f"--constraint-from {spec}: {len(text)} characters; split into ranges of at most {MAX_TEXT_CHARS}"
        )
    suffix = f"#L{first}" if last == first else f"#L{first}-L{last}"
    return {"text": text, "source": {"uri": file_uri(path, root) + suffix, "digest": _digest(data)}}


def quote_status(text: object, source: object, root: Path) -> JsonDoc:
    """Is ``text`` a verbatim substring of its digest-bound ``source``?

    ``match``: the source still has the recorded digest and contains the text exactly. ``mismatch``: same
    bytes, but the text is not in them (a paraphrase or an edit). ``drift``: the source changed since the
    sender read it, so the comparison is against other bytes. ``missing`` / ``not_accessed`` (with a
    ``reason``): nothing was opened.
    """
    if not isinstance(source, dict) or not isinstance(source.get("uri"), str) or not isinstance(text, str):
        return {"status": "not_accessed", "reason": "no source uri"}
    uri = str(source["uri"])
    path, reason = confined_path(uri, root)
    if path is None:
        return {"status": "not_accessed", "reason": reason}
    if not path.exists():
        return {"status": "missing"}
    try:
        data = read_capped(path, _MAX_SOURCE_BYTES)
    except OSError:  # trw-fail-silent-allow: unreadable is reported as not_accessed, never as a match
        return {"status": "not_accessed", "reason": "not a readable regular file of at most 2 MiB"}
    observed = _digest(data)
    if observed != source.get("digest"):
        return {"status": "drift", "observed_digest": observed}
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:  # trw-fail-silent-allow: replacement characters could fake a match; report, never match
        return {"status": "not_accessed", "reason": "source is not UTF-8 text"}
    return {"status": "match" if text in content else "mismatch"}
