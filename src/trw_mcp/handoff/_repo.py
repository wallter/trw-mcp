"""Repository facts for drafting and checking handoffs: the git-state record, URI confinement and raw digests.

Belongs to the :mod:`trw_mcp.handoff` package. Stdlib only and no subprocess or network module
(the package is a leaf; ``test_ahr_security`` pins that): the git queries that fill ``GitState``
live in ``server/_handoff_git.py`` and are passed in. Pointer digests are SHA-256 over the file's
raw bytes (R-INT-6, ``kind`` file/artifact/section). Nothing here evaluates file content.

A ``file:`` URI comes from a record, which is untrusted data (R-SEC-1/R-SEC-2): ``confined_path``
admits only a relative path that stays inside the repository root after symlinks resolve, and
says why it refused anything else.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

__all__ = ["UNKNOWN_GIT", "GitState", "confined_path", "file_uri", "raw_digest", "read_capped"]

_CHUNK = 1 << 16
_UNRESERVED = re.compile(rb"[A-Za-z0-9/._~-]")
_PERCENT = re.compile(r"(?:%[0-9A-Fa-f]{2})+")
_ENCODED_DOT = re.compile(r"%2e", re.IGNORECASE)


@dataclass(frozen=True)
class GitState:
    """HEAD and tree state of one checkout. ``tree_state`` is ``unknown`` outside git."""

    commit: str | None
    tree_state: str
    changed: tuple[str, ...]  # repo-relative paths git reports as changed, the record's own files excluded
    branch: str | None = None  # None when detached or outside git (informational, R-REC-8)


UNKNOWN_GIT = GitState(None, "unknown", ())


def _open_regular(path: Path) -> int:
    """A read descriptor on a regular file: ``os.stat`` + ``S_ISREG`` before ``open``, ``fstat`` after."""
    if not stat.S_ISREG(os.stat(path).st_mode):
        raise OSError(f"{path} is not a regular file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))  # a FIFO swapped in must not hang
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise OSError(f"{path} is not a regular file")
    return fd


def raw_digest(path: Path) -> str:
    """``sha256:<hex>`` of the raw bytes of one regular file; ``OSError`` when it cannot be read as one."""
    with os.fdopen(_open_regular(path), "rb") as handle:
        sha = hashlib.sha256()
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            sha.update(chunk)
    return f"sha256:{sha.hexdigest()}"


def read_capped(path: Path, cap: int) -> bytes:
    """The bytes of one regular file of at most ``cap`` bytes; ``OSError`` otherwise."""
    with os.fdopen(_open_regular(path), "rb") as handle:
        data = handle.read(cap + 1)
    if len(data) > cap:
        raise OSError(f"{path} is larger than {cap} bytes")
    return data


def _encode(text: str) -> str:
    return "".join(chr(b) if _UNRESERVED.fullmatch(bytes([b])) else f"%{b:02X}" for b in text.encode("utf-8"))


def _decode(text: str) -> str:
    return _PERCENT.sub(lambda m: bytes.fromhex(m.group(0).replace("%", "")).decode("utf-8", "replace"), text)


def file_uri(path: Path, root: Path) -> str:
    """``file:<repo-relative path>``; ``ValueError`` when ``path`` resolves outside ``root``.

    Records never carry absolute local paths: they break on another machine and leak a home directory.
    """
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{path} is outside the repository root {root}")
    return "file:" + _encode(resolved.relative_to(root).as_posix())


def confined_path(uri: str, root: Path) -> tuple[Path | None, str]:
    """``(resolved path, "")`` for a ``file:`` URI naming a place inside ``root``; ``(None, reason)`` otherwise.

    Refused: other schemes (never fetched), ``file://`` authorities, absolute paths, ``..`` segments
    (plain or percent-encoded), control characters, and anything whose ``resolve()`` leaves ``root``
    (a symlink pointing out). Only the path is computed: nothing is opened or hashed here.
    """
    scheme, _, rest = uri.partition(":")
    if scheme.lower() != "file":
        return None, f"{scheme.lower() or 'schemeless'} URI: never fetched by check"
    rest = rest.split("#", 1)[0].split("?", 1)[0]
    if _ENCODED_DOT.search(rest):
        return None, "percent-encoded dot in a file URI"
    rest = _decode(rest)
    if rest.startswith("/"):
        return None, "absolute path: only repository-relative file: URIs are opened"
    if any(ord(ch) < 0x20 or ch in "\x7f\\" for ch in rest):
        return None, "control character or backslash in a file URI"
    if not rest or ".." in PurePosixPath(rest).parts:
        return None, "empty path or parent-directory segment"
    base = root.resolve()
    try:
        resolved = (base / rest).resolve()
    except (OSError, RuntimeError):  # trw-fail-silent-allow: a symlink loop raises before 3.13; refused with a reason
        return None, "unresolvable path (symlink loop)"
    if not resolved.is_relative_to(base):
        return None, "resolves outside the repository (symlink)"
    return resolved, ""
