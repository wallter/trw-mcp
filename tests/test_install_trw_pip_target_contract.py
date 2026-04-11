"""Source-level contracts for install-trw.py --pip-target hardening."""

from pathlib import Path

_INSTALLER_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "install-trw.template.py"


def test_install_trw_template_validates_pip_target() -> None:
    content = _INSTALLER_TEMPLATE.read_text(encoding="utf-8")
    assert "def validate_pip_target" in content
    assert 're.fullmatch(r"[A-Za-z0-9_./-]+"' in content


def test_install_trw_template_avoids_shell_true_for_import_check() -> None:
    content = _INSTALLER_TEMPLATE.read_text(encoding="utf-8")
    assert "shell=True" not in content
    assert '[python, "-c", f"import {mod}"]' in content


def test_install_trw_template_writes_pythonpath_wrapper_for_pip_target() -> None:
    content = _INSTALLER_TEMPLATE.read_text(encoding="utf-8")
    assert 'wrapper = Path("/usr/local/bin/trw-mcp")' in content
    assert 'export PYTHONPATH={validated_target}:$PYTHONPATH' in content
