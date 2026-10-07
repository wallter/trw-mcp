"""The typed ``config.yaml`` writer behind ``trw-mcp config set`` (PRD-INFRA-210 FR04).

``set_config_value`` changes one key in one layer. The key is a public ``TRWConfig`` field, or
``FIELD.SUBKEY`` for a dict-typed field (one entry, siblings kept). Credentials are refused: they live in
the credentials file, not a ``config.yaml`` (PRD-SEC-005). The value is one YAML scalar or flow node,
validated with the production loader's own cascade before anything is written. The write is a ruamel
round-trip published through ``write_beneath`` under a directory lock, so comments, key order, quotes,
indentation and the file mode survive, a concurrent edit is not lost, and a symlink is refused rather than
followed. Presentation lives in ``_config_cli``; nothing here prints.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import partial
from io import StringIO
from pathlib import Path
from typing import Any, NamedTuple, get_origin

from trw_mcp.tools._config_edit_guard import check_only_target_changed, remove_key_lines

__all__ = ["ConfigSetRefusedError", "SetResult", "set_config_value", "unset_config_value"]


class ConfigSetRefusedError(Exception):
    """A refusal (exit 2): the message is the one stderr line, and nothing was written."""


class SetResult(NamedTuple):
    path: Path
    changed: bool


def _refuse(message: str) -> ConfigSetRefusedError:
    return ConfigSetRefusedError(message)


def _known_fields() -> set[str]:
    from trw_mcp.models.config import TRWConfig

    return set(TRWConfig.model_fields)


def _plain(node: Any) -> Any:
    """A ruamel node as plain dict/list/scalars (for validation and comparison)."""
    if isinstance(node, dict):
        return {k: _plain(v) for k, v in node.items()}
    if isinstance(node, list):
        return [_plain(v) for v in node]
    return node


def _field_for(key: str, *, allow_unknown: bool = False) -> tuple[str, str | None]:
    """``(field, subkey)`` for a settable key; raises a refusal for unknown, secret or non-dict dotted keys.

    *allow_unknown* (``config unset``) lets a retired or misspelt key through so it can be removed; it is
    never settable.
    """
    from pydantic import SecretStr

    from trw_mcp.models.config import TRWConfig

    field, dot, sub = key.partition(".")
    info = TRWConfig.model_fields.get(field)
    if info is None or field.startswith("_"):
        if allow_unknown and not field.startswith("_") and (bool(dot) is bool(sub)):
            return field, (sub if dot else None)
        raise _refuse(f"unknown config key {field!r}; `trw-mcp config-reference` lists the public keys")
    extra = info.json_schema_extra
    if (isinstance(extra, dict) and extra.get("secret")) or SecretStr in _annotation_types(info.annotation):
        raise _refuse(f"{field} is a secret and is never written to a config.yaml; use the credentials file")
    if dot:
        if get_origin(info.annotation) is not dict and info.annotation is not dict:
            raise _refuse(f"{field} is not a mapping, so {key!r} has no sub-key")
        if not sub:
            raise _refuse(f"{key!r} has an empty sub-key")
    return field, (sub if dot else None)


def _annotation_types(annotation: object) -> set[object]:
    from typing import get_args

    found: set[object] = {annotation}
    for arg in get_args(annotation):
        found |= _annotation_types(arg)
    return found


def _has_non_string_key(node: Any) -> bool:
    if isinstance(node, dict):
        return any(not isinstance(k, str) or _has_non_string_key(v) for k, v in node.items())
    return isinstance(node, list) and any(_has_non_string_key(v) for v in node)


def _parse_value(raw: str) -> Any:
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    if not raw.strip():
        raise _refuse("empty value; pass one YAML scalar or flow value")
    if "\n" in raw:
        raise _refuse("value must be one YAML scalar or flow node on a single line")
    try:
        value = YAML().load(raw)
    except YAMLError as exc:
        raise _refuse(f"value is not valid YAML ({type(exc).__name__})") from None
    if value is None:
        raise _refuse("value parsed as null; pass a concrete value")
    if _has_non_string_key(value):
        raise _refuse("map keys must be strings (a non-string key would not load back)")
    return value


def _layer_path(scope: str, target_dir: Path, home: Path) -> tuple[Path, Path]:
    """``(root, config path)`` for *scope*; refuses a project without ``.trw/`` or a symlinked path."""
    if scope == "machine":
        root = home
    else:
        root = target_dir
        trw = root / ".trw"
        if not trw.is_dir():
            raise _refuse(f"{trw} does not exist; run in a TRW project or use --scope machine")
    path = root / ".trw" / "config.yaml"
    if path.is_symlink() or path.parent.is_symlink():
        raise _refuse(f"{path} is a symlink path; refusing to write through it")
    return root, path


def _identity(info: os.stat_result) -> tuple[int, ...]:
    """What makes the leaf "the very file that was read": device, inode, size and both timestamps."""
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class _Held:
    """The config's parent directory held by descriptor (POSIX), so the leaf is read and rechecked by name beneath it."""

    def __init__(self, dfd: int | None, leaf: str, path: Path) -> None:
        self.dfd, self.leaf, self.path = dfd, leaf, path

    def _stat(self) -> os.stat_result | None:
        try:
            if self.dfd is None:
                return os.stat(self.path, follow_symlinks=False)
            return os.stat(self.leaf, dir_fd=self.dfd, follow_symlinks=False)
        except (
            FileNotFoundError
        ):  # trw-fail-silent-allow: an absent leaf is a value here (None), compared by unchanged()
            return None

    def read(self) -> tuple[str | None, tuple[int, ...] | None, int]:
        """``(text, identity, mode)`` of the leaf read through a no-follow open; ``(None, None, 0o644)`` if absent.

        The identity is ``(st_dev, st_ino, st_size, st_mtime_ns, st_ctime_ns)``: an inode number alone can be
        reused by a file created after the original was unlinked (Linux does), and an in-place edit or chmod
        keeps the inode, so size and both timestamps are compared too.
        """
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            if self.dfd is None:  # no dir_fd on this platform: best effort, like the safe writer's own Windows branch
                if self.path.is_symlink():
                    raise _refuse(f"{self.path} is a symlink; refusing to read through it")
                fd = os.open(self.path, flags)
            else:
                fd = os.open(self.leaf, flags, dir_fd=self.dfd)
        except FileNotFoundError:  # trw-fail-silent-allow: no file yet is the new-layer case, not an error
            return None, None, 0o644
        except OSError as exc:
            if exc.errno in {errno.ELOOP, getattr(errno, "EMLINK", -1)}:
                raise _refuse(f"{self.path} is a symlink; refusing to read through it") from None
            raise
        with os.fdopen(fd, "r", encoding="utf-8", newline="") as handle:  # newline="": keep CRLF bytes as they are
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise _refuse(f"{self.path} is not a regular file")
            return handle.read(), _identity(info), stat.S_IMODE(info.st_mode)

    def unchanged(self, identity: tuple[int, ...] | None) -> bool:
        """Whether the leaf is still the very file that was read (or still absent)."""
        info = self._stat()
        if info is None or identity is None:
            return info is None and identity is None
        return stat.S_ISREG(info.st_mode) and _identity(info) == identity


@contextmanager
def _held_layer(root: Path, path: Path) -> Iterator[_Held]:
    from trw_memory.exceptions import UnsafeWriteError
    from trw_memory.safe_fs import anchored_removal_supported, open_parent_beneath

    if not anchored_removal_supported():
        yield _Held(None, path.name, path)
        return
    try:
        dfd, leaf = open_parent_beneath(root, ".trw/config.yaml")
    except UnsafeWriteError as exc:
        raise _refuse(f"{path} refused: {exc.reason}") from None
    try:
        yield _Held(dfd, leaf, path)
    finally:
        os.close(dfd)


def _parse_layer(text: str | None, path: Path) -> tuple[Any, str, int, int]:
    """``(data, original text, mapping indent, sequence offset)`` of a layer's text; refuses a non-mapping."""
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError
    from ruamel.yaml.util import load_yaml_guess_indent

    if text is None:
        return None, "", 2, 0
    try:
        loader = YAML()
        loader.preserve_quotes = True
        data = loader.load(text)
        _, indent, seq = load_yaml_guess_indent(text)  # indent guess only; yaml= needs ruamel.yaml 0.18.11
    except (YAMLError, ValueError) as exc:
        raise _refuse(f"{path} could not be parsed ({type(exc).__name__})") from None
    if data is not None and not isinstance(data, dict):
        raise _refuse(f"{path} is not a YAML mapping; refusing to rewrite it")
    return data, text, indent or 2, seq or 0


def _mapping_indent(text: str) -> int:
    """The file's own nested-mapping indent (first indented ``key:`` line), default 2."""
    found = re.search(r"^( +)[^\s#-][^:\n]*:", text, re.MULTILINE)
    return len(found.group(1)) if found else 2


def _candidate_layer(rendered: str) -> dict[str, Any]:
    """The candidate layer as the production reader returns it (str keys, nulls dropped), via a scratch file."""
    from trw_mcp.state._namespace_pin_read import read_config_layer

    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "config.yaml"
        path.write_text(rendered, encoding="utf-8")
        return read_config_layer(path)


def _production_build(machine: dict[str, Any], project: dict[str, Any]) -> None:
    """Build a config from these layers the way ``_build_config_unguarded`` does; raises ``ValidationError``."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.config._loader import _deep_merge, exclude_env_shadowed_keys

    merged = _deep_merge(dict(machine), dict(project))
    for secret in ("platform_api_key", "backend_api_key"):  # a tracked layer is never a credential source
        merged.pop(secret, None)
    filtered = exclude_env_shadowed_keys(merged)
    filtered.pop("platform_contact_enabled", None)
    TRWConfig(**filtered)  # type: ignore[arg-type]


def _field_build(field: str, value: Any) -> None:
    from trw_mcp.models.config import TRWConfig

    TRWConfig(_env_file=None, **{field: value})


def _validate(field: str, machine_path: Path, project_path: Path, scope: str, rendered: str, value: Any) -> None:
    """Refuse a candidate the production loader would reject, using the loader's own functions.

    1. The candidate field alone (type and Field constraints), for a clear message; it ignores env, so a
       ``TRW_*`` variable shadowing the key cannot hide a bad value.
    2. The effective config for this project: machine layer + project layer (the candidate in its own),
       read by the loader's layer reader, merged, env-shadowed, built by ``TRWConfig``.
    3. A machine-scope write also builds the machine layer alone, which is what a project with no
       project layer loads.
    Only errors on the candidate field or model-level (cross-field) errors refuse; a failure on an
    unrelated field is production's to report and does not block this write.
    """
    from pydantic import ValidationError

    from trw_mcp.state._namespace_pin_read import read_config_layer

    candidate = _candidate_layer(rendered)
    if scope == "machine":  # the project layer is "the other"; the assess caller passes home as its project
        other = {} if project_path == machine_path else read_config_layer(project_path)
        effective: Callable[[], None] = partial(_production_build, candidate, other)
    else:
        effective = partial(_production_build, read_config_layer(machine_path), candidate)
    builds: list[tuple[str, Callable[[], object]]] = []
    if field in candidate:
        builds.append(("field", partial(_field_build, field, candidate[field])))
    builds.append(("effective", effective))
    if scope == "machine":
        builds.append(("machine layer", partial(_production_build, candidate, {})))
    for name, build in builds:
        try:
            build()
        except ValidationError as exc:
            bad = [e for e in exc.errors() if e["loc"][:1] == (field,) or (name != "field" and not e["loc"])]
            if bad:
                shown = "; ".join(f"{'.'.join(map(str, e['loc'])) or field}: {e['msg']}" for e in bad[:2])
                where = "" if name in {"field", "effective"} else f" (in the {name} alone)"
                raise _refuse(f"{field} rejected{where}: {shown}") from None


@contextmanager
def _layer_lock(directory: Path) -> Iterator[None]:
    """Exclusive advisory lock on the config's directory, so two read-modify-write cycles serialise.

    The lock is on the already-checked directory itself (no lock file in a tracked tree). Where
    ``fcntl`` is unavailable the write is still atomic, only unserialised.
    """
    try:
        import fcntl
    except ImportError:  # trw-fail-silent-allow: no flock on this platform; the atomic replace still holds
        yield
        return
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing releases the flock


def set_config_value(key: str, raw_value: str, *, scope: str, target_dir: Path, home: Path | None = None) -> SetResult:
    """Write ``key = raw_value`` into one layer; raises :class:`ConfigSetRefusedError` and writes nothing on refusal.

    The one typed writer: ``config set``, the ``config dispatch`` step and ``assess`` all call it. The
    read, the edit and the replace happen under :func:`_layer_lock`, re-reading the file inside it, so a
    sibling edit made meanwhile is kept.
    """
    field, sub = _field_for(key)
    value = _parse_value(raw_value)
    home_dir = Path.home() if home is None else home
    root, path = _layer_path(scope, target_dir, home_dir)
    path.parent.mkdir(parents=True, exist_ok=True)  # machine scope creates ~/.trw; project's exists (checked)
    with _layer_lock(path.parent):
        return _write_locked(
            field,
            sub,
            value,
            scope,
            root,
            path,
            (home_dir / ".trw" / "config.yaml", target_dir / ".trw" / "config.yaml"),
        )


#: Passed as the value to remove the key instead of setting it.
_REMOVE: Any = object()


def unset_config_value(key: str, *, scope: str, target_dir: Path, home: Path | None = None) -> SetResult:
    """Remove ``key`` (or one ``FIELD.SUBKEY`` entry) from one layer; ``changed`` is False when it was already absent.

    Same lock, no-follow read, identity recheck and layout preservation as :func:`set_config_value`. A
    retired or misspelt key may be removed (``config set`` refuses it); secrets are refused the same way.
    An absent file is reported absent and never created.
    """
    field, sub = _field_for(key, allow_unknown=True)
    home_dir = Path.home() if home is None else home
    root, path = _layer_path(scope, target_dir, home_dir)
    if not path.parent.is_dir():
        return SetResult(path, False)
    with _layer_lock(path.parent):
        return _write_locked(
            field,
            sub,
            _REMOVE,
            scope,
            root,
            path,
            (home_dir / ".trw" / "config.yaml", target_dir / ".trw" / "config.yaml"),
        )


def _removal_plan(layer: Any, field: str, sub: str | None, path: Path) -> tuple[bool, bool]:
    """``(present, drop_field)``: whether the key exists, and whether removing it empties (so drops) its map."""
    if field not in layer:
        return False, False
    if sub is None:
        return True, False
    holder = layer[field]
    if not isinstance(holder, dict):
        raise _refuse(f"{field} in {path} is not a mapping; refusing to rewrite it")
    if sub not in holder:
        return False, False
    only_key = len(holder) == 1 and not getattr(holder, "merge", None)  # a map that still merges others is not empty
    return True, only_key


def _round_trip_remove(layer: Any, field: str, sub: str | None, drop_field: bool) -> None:
    """Flow-style fallback: delete through the round-trip (comments inside flow containers are not preserved)."""
    if sub is None or drop_field:
        del layer[field]
    else:
        del layer[field][sub]


def _write_locked(
    field: str, sub: str | None, value: Any, scope: str, root: Path, path: Path, layers: tuple[Path, Path]
) -> SetResult:
    with _held_layer(root, path) as held:
        return _edit_and_publish(held, field, sub, value, scope, root, path, layers)


def _edit_and_publish(
    held: _Held, field: str, sub: str | None, value: Any, scope: str, root: Path, path: Path, layers: tuple[Path, Path]
) -> SetResult:
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap
    from trw_memory.exceptions import UnsafeWriteError
    from trw_memory.safe_fs import write_beneath

    raw, identity, existing_mode = held.read()
    data, text, indent, seq_offset = _parse_layer(raw, path)
    layer = data if data is not None else CommentedMap()
    surgery: str | None = None
    if value is _REMOVE:
        changed, drop_field = _removal_plan(layer, field, sub, path)
        if changed:
            surgery = remove_key_lines(text, layer, field, sub, drop_field=drop_field)
            if surgery is None:
                _round_trip_remove(layer, field, sub, drop_field)
    else:
        holder, name = layer, field
        if sub is not None:
            holder = layer.get(field)
            if holder is None:
                holder = CommentedMap()
            if not isinstance(holder, dict):
                raise _refuse(f"{field} in {path} is not a mapping; refusing to rewrite it")
            name = sub
        changed = name not in holder or _plain(holder[name]) != _plain(value)
        if changed:
            holder[name] = value
            if sub is not None:
                layer[field] = holder
    if not changed:
        return SetResult(path, False)
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=_mapping_indent(text), sequence=max(indent, seq_offset + 2), offset=seq_offset)
    buf = StringIO()
    yaml.dump(layer, buf)
    rendered = buf.getvalue()
    if data is None and text.strip():  # comment-only file: keep its comments, append the new key
        rendered = text if text.endswith("\n") else text + "\n"
        rendered += buf.getvalue()
    if surgery is not None:
        rendered = surgery
    elif value is _REMOVE and not layer:  # a flow-style fallback emptied the map: no literal `{}`
        rendered = ""
    problem = check_only_target_changed(rendered, raw, field, sub, value, removing=value is _REMOVE)
    if problem:
        raise _refuse(f"cannot change {field}{'.' + sub if sub else ''} alone in {path}: {problem}; edit it by hand")
    if value is not _REMOVE or field in _known_fields():  # a retired key has no field to validate
        _validate(field, layers[0], layers[1], scope, rendered, value)
    if not held.unchanged(identity):  # re-checked under the lock: never publish over a file we did not read
        raise _refuse(f"{path} changed while it was being edited; nothing was written")
    try:  # an existing file keeps its exact mode; a new one is 0644 narrowed by the umask, never wider
        write_beneath(
            root, ".trw/config.yaml", rendered.encode("utf-8"), mode=existing_mode, exact_mode=identity is not None
        )
    except UnsafeWriteError as exc:
        raise _refuse(f"{path} refused: {exc.reason}") from None
    return SetResult(path, True)
