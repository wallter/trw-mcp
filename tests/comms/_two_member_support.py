"""Two-process formation plumbing for the WP4 A1/A2 proof.

WHY THIS MODULE EXISTS. ``tests/_stdio_harness.py`` spawns real ``trw-mcp``
stdio servers but binds every child to ONE identity contract: ``spawn(label)``
uses ``label`` as the session id, ``cwd=self.project_root`` and
``child_env(label)`` with no additive mapping. The WP4 scenario needs the two
axes that contract fixes -- a per-child working directory (each worker is meant
to run inside its own leased worktree) and a per-child ``TRW_PROJECT_ROOT``
(membership binds to the COORDINATION root, not the worktree) -- so the spawn is
subclassed here rather than the shared harness being widened for one test.

WHY THE CALL PATH IS NOT ``StdioServerHarness.call``. That method folds a
JSON-RPC error and an ``isError`` tool result into ``HarnessError``. Two of the
three properties under proof here ARE refusals (an undeclared session cannot
join; a misdirected project root cannot see the formation), so the refusal has
to arrive as data. :meth:`MemberHarness.call_result` returns the whole result
object and the caller decides which shape it expected.

WHY THE PAYLOAD IS READ FROM CONTENT AS WELL AS ``structuredContent``.
``trw_init`` is registered with ``output_schema=None``, so FastMCP emits no
structured block for it while the comms tools (``trw_send``/``trw_inbox``) do.
A reader that only looked at ``structuredContent`` would silently see ``{}``
for every ``trw_init`` and the test would assert nothing about the join.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests import _stdio_harness
from tests._formation_test_support import make_run_dir, open_slot
from tests._stdio_harness import HarnessError, ServerProcess, StdioServerHarness

__all__ = [
    "Coordination",
    "MemberHarness",
    "build_coordination",
    "error_text_of",
    "payload_of",
    "write_project_root",
]

#: Same bound the shared harness uses for a framed reply.
_READ_TIMEOUT_S = 100.0

#: The minimum project config that turns the comms surface ON. Written by the
#: test, never copied from the repository's own ``.trw/config.yaml``:
#: ``comms_enabled`` defaults to false and ``ctx_isolation_enabled`` is what
#: keys a pin on ``TRW_SESSION_ID``, and both are stated here so a machine-level
#: ``~/.trw/config.yaml`` (which merges BENEATH the project file) cannot change
#: what this test measures.
_CONFIG_YAML = "comms_enabled: true\nctx_isolation_enabled: true\n"


def write_project_root(root: Path) -> Path:
    """Create *root* as a TRW project with comms enabled; return its ``.trw``."""
    trw_dir = root / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)
    (trw_dir / "config.yaml").write_text(_CONFIG_YAML, encoding="utf-8")
    return trw_dir


@dataclass(frozen=True)
class Coordination:
    """One disposable coordination project holding one formation."""

    root: Path
    trw_dir: Path
    orchestrator_run: Path
    formation_id: str

    def pins(self) -> dict[str, Any]:
        """The pin store's current contents, or ``{}`` when nothing pinned."""
        path = self.trw_dir / "runtime" / "pins.json"
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise HarnessError(f"pin store at {path} is not an object: {type(data)}")
        return data

    def manifest_members(self) -> dict[str, dict[str, Any]]:
        """Member rows as currently persisted, keyed by member id."""
        from trw_mcp.formation import load

        context = load(self.orchestrator_run, trw_dir=self.trw_dir)
        if context is None:
            raise HarnessError(f"no formation resolved for {self.orchestrator_run}")
        return {m.member_id: m.model_dump() for m in context.manifest.members}


def build_coordination(
    root: Path,
    members: Sequence[tuple[str, str]],
    *,
    formation_id: str = "wp4-interop",
) -> Coordination:
    """Create the coordination project and its formation manifest.

    *members* is ``(member_id, client)`` pairs, declared PENDING. No ``prd_ids``
    are allocated: allocation is validated against a PRDs directory resolved
    from the CALLING process's project root, and this fixture has no business
    reaching into the developer's checkout to find one.
    """
    from trw_mcp.formation import create

    trw_dir = write_project_root(root)
    orchestrator_run = make_run_dir(trw_dir / "runs", "orchestrator")
    payload: dict[str, object] = {
        "formation_id": formation_id,
        "members": [
            open_slot(member_id, client, role="implementer", owned_paths=[f"src/{member_id}"])
            for member_id, client in members
        ],
    }
    manifest = create(orchestrator_run, payload, trw_dir=trw_dir)
    return Coordination(root, trw_dir, orchestrator_run, manifest.formation_id)


def payload_of(result: Mapping[str, Any]) -> dict[str, Any]:
    """The tool's own object payload, from either MCP result shape."""
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and structured:
        return structured
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            decoded = json.loads(str(block.get("text")))
            if isinstance(decoded, dict):
                return decoded
    return {}


def error_text_of(result: Mapping[str, Any]) -> str:
    """The concatenated text of an ``isError`` result, for refusal assertions."""
    parts = [
        str(block.get("text"))
        for block in (result.get("content") or [])
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    return "\n".join(parts)


class MemberHarness(StdioServerHarness):
    """The shared stdio harness plus per-child cwd, project root and session id.

    Teardown, child registry, writer-lock pruning and leak detection are all
    inherited unchanged -- this subclass adds spawn knobs and a non-raising call,
    nothing else.
    """

    def spawn_member(
        self,
        label: str,
        *,
        session_id: str,
        project_root: Path,
        cwd: Path,
    ) -> ServerProcess:
        """Spawn one server with its OWN cwd, project root and session id.

        The three differ on purpose: *cwd* is the worker's leased worktree,
        *project_root* is the coordination project whose ``.trw`` holds the
        formation and the pin store, and *session_id* is the pin key the
        manifest records at join. A worker whose project root followed its cwd
        could never join (``comms/_identity.py`` derives the group from the
        canonical project root), which is exactly what one of the mutation
        tests demonstrates.
        """
        stderr_path = self.stderr_dir / f"{label}.stderr"
        handle = stderr_path.open("wb")
        try:
            proc = subprocess.Popen(
                [sys.executable, *_stdio_harness._SERVER_ARGV],
                cwd=str(cwd),
                env=self.child_env(session_id, {"TRW_PROJECT_ROOT": str(project_root)}),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=handle,
            )
        finally:
            handle.close()
        if proc.stdout is None or proc.stdin is None:  # pragma: no cover - Popen always gives us both
            raise HarnessError(f"{label}: child has no stdio pipes")
        server = ServerProcess(
            label=label,
            proc=proc,
            reader=_stdio_harness._LineReader(proc.stdout.fileno()),
            stderr_path=stderr_path,
        )
        self._children.append(server)
        self._spawned_pids.append(proc.pid)
        return server

    def ready_member(
        self,
        label: str,
        *,
        session_id: str,
        project_root: Path,
        cwd: Path,
    ) -> ServerProcess:
        """Spawn and complete the MCP handshake."""
        server = self.spawn_member(label, session_id=session_id, project_root=project_root, cwd=cwd)
        self.initialize(server, started_at=time.monotonic())
        return server

    def call_result(self, server: ServerProcess, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Call *tool* and return the raw MCP result object, errors included."""
        request_id = server.next_id()
        self._send(
            server,
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": tool, "arguments": dict(arguments)},
            },
        )
        reply = self._await_id(server, request_id, time.monotonic() + _READ_TIMEOUT_S)
        if "error" in reply:
            raise HarnessError(f"{server.label}: {tool} failed at the protocol level: {reply['error']}")
        result = reply.get("result")
        if not isinstance(result, dict):
            raise HarnessError(f"{server.label}: {tool} returned no result object")
        return result

    def call_ok(self, server: ServerProcess, tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Call *tool*, refusing anything but a successful tool result."""
        result = self.call_result(server, tool, arguments)
        if result.get("isError"):
            raise HarnessError(f"{server.label}: {tool} reported isError: {error_text_of(result)}")
        return payload_of(result)
