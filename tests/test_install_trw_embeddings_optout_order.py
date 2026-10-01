"""E2E-INC-140: an explicit ``--no-embeddings`` is on record before ``update-project`` runs.

``persist_embeddings_choice`` ran after project setup and the doctor, so ``update-project`` still loaded the embedder,
failed, and warned ``embedder_error`` about a capability the operator had turned off.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

from tests._install_trw_pip_target_contract_support import _load_installer_module

_TEMPLATE = Path(__file__).resolve().parents[2] / "trw-mcp" / "scripts" / "install-trw.template.py"


@pytest.fixture(scope="module")
def installer() -> ModuleType:
    return _load_installer_module(_TEMPLATE)


def test_a_no_embeddings_opt_out_is_persisted_before_the_project_is_updated(
    installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests._install_trw_main_support import drive_main, make_project

    run = drive_main(installer, monkeypatch, make_project(tmp_path), extra_argv=("--no-embeddings",))

    assert "persist_embeddings:false" in run.order
    assert run.order.index("persist_embeddings:false") < run.order.index("project_setup")
    assert run.order.index("persist_embeddings:false") < run.order.index("doctor")


def test_the_wanted_embeddings_choice_is_still_persisted_only_after_the_readiness_check(
    installer: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wanted choice can still be declined at the readiness prompt, so it is not recorded early."""
    from tests._install_trw_main_support import drive_main, make_project

    run = drive_main(installer, monkeypatch, make_project(tmp_path), extra_argv=("--embeddings",))

    assert run.order.index("persist_embeddings:true") > run.order.index("semantic")
    assert run.order.count("persist_embeddings:true") == 1


def test_the_choice_is_never_written_through_a_symlinked_config_or_trw_dir(
    installer: ModuleType, tmp_path: Path
) -> None:
    """The early write runs before update-project's managed-path refusal, so it must refuse symlinks itself."""
    outside = tmp_path / "outside.yaml"
    outside.write_text("keep: me\n", encoding="utf-8")
    trw = tmp_path / "project" / ".trw"
    trw.mkdir(parents=True)
    (trw / "config.yaml").symlink_to(outside)
    installer.persist_embeddings_choice(trw / "config.yaml", False)
    assert outside.read_text(encoding="utf-8") == "keep: me\n"

    real = tmp_path / "real-trw"
    real.mkdir()
    (real / "config.yaml").write_text("keep: me\n", encoding="utf-8")
    linked = tmp_path / "other" / ".trw"
    linked.parent.mkdir()
    linked.symlink_to(real)
    installer.persist_embeddings_choice(linked / "config.yaml", False)
    assert (real / "config.yaml").read_text(encoding="utf-8") == "keep: me\n"
