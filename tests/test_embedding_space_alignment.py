"""trw-mcp follows trw-memory's embedding-model change (bge-small-en-v1.5).

Two contracts, each driven through the production call site with fake
embedders (no model download):

1. the default retrieval model IS trw-memory's default, and user-facing text
   names the configured model;
2. no embedder in the trw-mcp process (PRD-CORE-302 NFR01): a source scan of
   the whole tree and a fresh-interpreter run of the four hot tools. Learn dedup
   sends text to the daemon, and recall dedup takes the daemon's vectors and
   calibrated threshold from one answer. Space selection is the daemon's, pinned in trw-memory's
   ``test_tools_similar.py`` and ``test_tools_recall_support.py``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from tests._layout import MONOREPO_ROOT, PACKAGE_ROOT
from tests._memory_fixtures import FAKE_NAMESPACE, DaemonCheckout
from tests._memory_store_fake import FakeMemoryStore
from trw_mcp.state._store_selection import VectorSet
from trw_mcp.tools._recall_impl import _dedup_ranked_learnings

# -- 1. default model --------------------------------------------------------


def test_default_retrieval_model_is_the_trw_memory_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_memory._model_pin import DEFAULT_EMBEDDING_MODEL

    from trw_mcp.state._retrieval_capability import daemon_embedding_model

    monkeypatch.delenv("MEMORY_EMBEDDING_MODEL", raising=False)
    assert daemon_embedding_model() == DEFAULT_EMBEDDING_MODEL == "BAAI/bge-small-en-v1.5"


# -- 2. no embedder in the trw-mcp process (PRD-CORE-302 NFR01) -------------------

#: Modules whose import means a model runtime is in the process.
_MODEL_RUNTIMES = ("torch", "sentence_transformers")
#: Names that construct or fetch trw-memory's local embedder.
_EMBEDDER_NAMES = frozenset({"LocalEmbeddingProvider", "get_local_embedder"})


def _embedder_uses(tree: ast.AST) -> list[str]:
    """Imports of a model runtime, and any reference to an embedder constructor, in *tree*."""
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] in _MODEL_RUNTIMES]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in _MODEL_RUNTIMES:
                found.append(node.module or "")
            found += [a.name for a in node.names if a.name in _EMBEDDER_NAMES]
        elif isinstance(node, ast.Attribute) and node.attr in _EMBEDDER_NAMES:
            found.append(node.attr)
        elif isinstance(node, ast.Name) and node.id in _EMBEDDER_NAMES:
            found.append(node.id)
    return found


def test_no_trw_mcp_module_imports_a_model_runtime_or_an_embedder() -> None:
    """The source scan: an AST walk, so a ``find_spec`` probe or a logger name is not a use."""
    import trw_mcp

    root = Path(trw_mcp.__file__).parent
    offenders = {
        str(path.relative_to(root)): uses
        for path in sorted(root.rglob("*.py"))
        if (uses := _embedder_uses(ast.parse(path.read_text(encoding="utf-8"))))
    }

    assert offenders == {}


def test_the_scan_catches_each_kind_of_use() -> None:
    """Planted violations: the scan is not vacuous."""
    planted = (
        "import torch\n"
        "from sentence_transformers import SentenceTransformer\n"
        "from trw_memory.embeddings.local import LocalEmbeddingProvider\n"
        "import trw_memory.embeddings as e\n"
        "e.get_local_embedder()\n"
    )
    clean = (
        "from importlib.util import find_spec\nfind_spec('sentence_transformers')\nNAMES = ('sentence_transformers',)\n"
    )

    assert _embedder_uses(ast.parse(planted)) == [
        "torch",
        "sentence_transformers",
        "LocalEmbeddingProvider",
        "get_local_embedder",
    ]
    assert _embedder_uses(ast.parse(clean)) == []


def _requirement_names(requirements: list[str]) -> set[str]:
    return {re.split(r"[\s;<>=!~\[]", requirement, maxsplit=1)[0].lower() for requirement in requirements}


def test_neither_package_requires_a_model_runtime() -> None:
    """The wheel's Requires-Dist is these pyproject tables, extras included (release gate C16).

    trw-memory is checked too: it is trw-mcp's one first-party dependency, so a
    runtime it required would arrive with every trw-mcp install.
    """
    from importlib.metadata import requires

    import tomllib

    runtimes = {"torch", "sentence-transformers", "sentence_transformers"}
    project = tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    extras = [r for rs in project.get("optional-dependencies", {}).values() for r in rs]
    assert not _requirement_names([*project["dependencies"], *extras]) & runtimes, "trw-mcp"

    # trw-memory's base requirements only: its extras are opt-in. The public
    # layout is trw-mcp alone, so there the installed trw-memory's metadata
    # (the build paired with this run) stands in for the sibling pyproject.
    if MONOREPO_ROOT is not None:
        memory_base = tomllib.loads((MONOREPO_ROOT / "trw-memory" / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]["dependencies"]
    else:
        memory_base = [r for r in requires("trw-memory") or [] if "extra==" not in r.replace(" ", "")]
    assert not _requirement_names(memory_base) & runtimes, "trw-memory"


def test_trw_mcps_resolved_dependencies_carry_no_model_runtime() -> None:
    """PRD-CORE-302 W05: the locked closure of trw-mcp, every extra included, resolves no torch.

    The declared-dependency check above cannot see a transitive requirement; the
    lock can. The daemon, which runs in the same environment, gets the embeddings
    stack from ``trw-memory[embeddings]``, which the installer adds, never trw-mcp.
    """
    import tomllib

    lock = tomllib.loads((Path(__file__).resolve().parents[1] / "uv.lock").read_text(encoding="utf-8"))
    packages = {package["name"]: package for package in lock["package"]}
    root = packages["trw-mcp"]
    pending = [(dep["name"], tuple(dep.get("extra", ()))) for dep in root["dependencies"]]
    pending += [
        (dep["name"], tuple(dep.get("extra", ()))) for deps in root["optional-dependencies"].values() for dep in deps
    ]
    closure: set[str] = set()
    while pending:
        name, extras = pending.pop()
        package = packages[name]
        for dep in package.get("dependencies", []) + [
            dep for extra in extras for dep in package.get("optional-dependencies", {}).get(extra, [])
        ]:
            if dep["name"] not in closure:
                pending.append((dep["name"], tuple(dep.get("extra", ()))))
        closure.add(name)

    assert "trw-memory" in closure and len(closure) > 20  # the walk reached the tree
    assert not closure & {"torch", "sentence-transformers", "transformers"}


_TOOL_RUN = """
import json, sys

def _refuse(*_args, **_kwargs):
    raise AssertionError("trw-mcp constructed an embedder")

import trw_memory.embeddings as embeddings
import trw_memory.embeddings.local as local
local.LocalEmbeddingProvider.__init__ = _refuse
embeddings.get_local_embedder = _refuse

import asyncio
from fastmcp import FastMCP
from trw_mcp.tools import _deferred_state
from trw_mcp.tools.ceremony import register_ceremony_tools
from trw_mcp.tools.learning import register_learning_tools

server = FastMCP("nfr01")
register_ceremony_tools(server)
register_learning_tools(server)
tools = {tool.name: tool.fn for tool in asyncio.run(server.list_tools())}
outcomes = {
    "trw_session_start": tools["trw_session_start"](ctx=None, query="*"),
    "trw_learn": tools["trw_learn"](summary="NFR01 runtime row", detail="no model here", impact=0.6),
    "trw_recall": tools["trw_recall"](query="NFR01 runtime"),
    "trw_deliver": tools["trw_deliver"](skip_reflect=True, skip_index_sync=True),
}
if _deferred_state._deferred_thread is not None:
    _deferred_state._deferred_thread.join(timeout=120)
print(json.dumps({
    "ran": sorted(outcomes),
    "learned": outcomes["trw_learn"].get("status"),
    "recalled": [row.get("claim") for row in outcomes["trw_recall"].get("learnings", [])],
    "loaded": [name for name in ("torch", "sentence_transformers") if name in sys.modules],
}, default=str))
"""


@pytest.mark.slow
def test_the_four_hot_tools_run_without_loading_a_model(daemon_checkout: DaemonCheckout) -> None:
    """A fresh interpreter: learn, recall, session start and deliver never construct an embedder.

    The daemon under test is keyword-only, so no answer carries vectors; the
    embedder constructors raise in the child, and afterwards neither torch nor
    sentence_transformers is in its module table.
    """
    import json
    import os
    import subprocess
    import sys

    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    run = subprocess.run(
        [sys.executable, "-c", _TOOL_RUN],
        cwd=daemon_checkout.trw_dir.parent,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert run.returncode == 0, run.stderr[-4000:]
    report = json.loads(run.stdout.strip().splitlines()[-1])
    assert report["ran"] == ["trw_deliver", "trw_learn", "trw_recall", "trw_session_start"]
    # The tools did their work through the daemon, not merely returned an error.
    assert report["learned"] == "recorded"
    assert "NFR01 runtime row" in report["recalled"], report["recalled"]
    assert report["loaded"] == []


class _VectorStore:
    """The store seam answering one ``memory_vectors`` call: identical vectors, the daemon's threshold."""

    def __init__(self, answer: VectorSet | None) -> None:
        self.answer = answer
        self.asked: list[list[str]] = []

    def vectors(self, ids: list[str]) -> VectorSet | None:
        self.asked.append(ids)
        return self.answer


def _ranked(*ids: str) -> list[dict[str, object]]:
    return [{"id": entry_id, "summary": f"summary {entry_id}"} for entry_id in ids]


@pytest.mark.parametrize(
    ("answer", "kept"),
    [
        (VectorSet({"L-1": [1.0, 0.0], "L-2": [1.0, 0.0]}, 0.94), ["L-1"]),
        (None, ["L-1", "L-2"]),  # no daemon embedder: exact-content collapse only
    ],
)
def test_recall_dedup_uses_the_daemons_vectors_and_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: VectorSet | None, kept: list[str]
) -> None:
    store = _VectorStore(answer)
    monkeypatch.setattr("trw_mcp.state._store_selection.selected_store", lambda _trw_dir: (store, "project:t"))

    survivors, _collapsed = _dedup_ranked_learnings(tmp_path, _ranked("L-1", "L-2"))

    assert [entry["id"] for entry in survivors] == kept
    assert store.asked == [["L-1", "L-2"]]


# -- 4. update-project asks the daemon, never the checkout's own store ------------


@pytest.mark.parametrize(
    ("embedder", "warnings"),
    [
        ({"available": True, "model": "m", "loaded": False, "reason": None}, []),
        (
            {
                "available": False,
                "model": "m",
                "loaded": False,
                "reason": "model_not_cached",
                "fix": "trw-mcp models fetch",
            },
            ["Memory daemon cannot encode: model_not_cached \u2014 run: trw-mcp models fetch"],
        ),
    ],
    ids=["can-encode", "model-not-cached"],
)
def test_update_project_reads_the_daemons_embedder_and_never_opens_the_checkout_store(
    tmp_path: Path, fake_memory_store: FakeMemoryStore, embedder: dict[str, object], warnings: list[str]
) -> None:
    """PRD-CORE-302 FR05 and PRD-CORE-298 FR01.

    The daemon holds the model, so ``memory_status``'s embedder block is what
    update-project reports; the checkout's own memory.db stays byte-identical.
    """
    from trw_mcp.bootstrap._update_project import _run_auto_maintenance

    store = tmp_path / ".trw" / "memory" / "memory.db"
    store.parent.mkdir(parents=True)
    store.write_bytes(b"SQLite format 3\x00 unmigrated checkout store")
    before = store.read_bytes()
    fake_memory_store.embedder = embedder  # type: ignore[assignment]

    def refuse_open(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("update-project opened a store")

    result: dict[str, list[str]] = {"updated": [], "warnings": [], "created": [], "preserved": []}
    with patch("sqlite3.connect", side_effect=refuse_open):
        _run_auto_maintenance(tmp_path, result)  # type: ignore[arg-type]

    assert result["warnings"] == warnings
    assert ("embedder_status", FAKE_NAMESPACE) in fake_memory_store.calls
    assert store.read_bytes() == before


# -- platform publishing sends no vectors (OD6) -------------------------------


def test_publisher_body_has_no_embedding_key(tmp_path: Path) -> None:
    """OD6: the telemetry publisher never embeds; the published body has no "embedding" key.

    Full payload-shape coverage (impact filtering, tags, status) lives in
    ``test_telemetry_publisher_filtering.py``; this test pins the specific
    embedding-space-alignment contract: nothing that resembles a vector is
    ever attached to the published learning, regardless of ``embeddings_enabled``.
    """
    from tests._test_telemetry_publisher_support import _make_config, _make_learning, _write_learning
    from trw_mcp.telemetry import publisher

    trw_dir = tmp_path / ".trw"
    entries_dir = trw_dir / "learnings" / "entries"
    _write_learning(entries_dir, "vectorless.yaml", _make_learning(impact=0.9))

    captured_payloads: list[dict[str, object]] = []

    def _fake_post(url: str, payload: dict[str, object], api_key: str = "") -> bool:
        captured_payloads.append(payload)
        return True

    cfg = _make_config().model_copy(update={"embeddings_enabled": True})
    with (
        patch.object(publisher, "get_config", return_value=cfg),
        patch.object(publisher, "resolve_trw_dir", return_value=trw_dir),
        patch.object(publisher, "_post_learning", side_effect=_fake_post),
    ):
        result = publisher.publish_learnings()

    assert result["published"] == 1
    assert len(captured_payloads) == 1
    assert "embedding" not in captured_payloads[0]
