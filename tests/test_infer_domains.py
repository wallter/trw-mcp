"""Tests for infer_domains() and _extract_path_stems() (PRD-CORE-102, Task 2)."""



def test_python_paths() -> None:
    """['src/auth/middleware.py'] → includes 'auth', 'middleware'."""
    from trw_mcp.scoring._recall import infer_domains

    result = infer_domains(modified_files=["src/auth/middleware.py"])
    assert "auth" in result
    assert "middleware" in result


def test_js_paths() -> None:
    """['lib/utils/api.js'] → includes 'utils', 'api'."""
    from trw_mcp.scoring._recall import infer_domains

    result = infer_domains(modified_files=["lib/utils/api.js"])
    assert "utils" in result
    assert "api" in result


def test_excludes_structural() -> None:
    """'src', 'test', 'lib' are excluded from domain inference."""
    from trw_mcp.scoring._recall import infer_domains

    result = infer_domains(modified_files=["src/lib/test/helpers/foo.py"])
    assert "src" not in result
    assert "lib" not in result
    assert "test" not in result
    assert "helpers" not in result
    assert "foo" in result


def test_deduplicates() -> None:
    """Same stem from two paths appears only once."""
    from trw_mcp.scoring._recall import infer_domains

    result = infer_domains(
        modified_files=[
            "src/auth/middleware.py",
            "tests/auth/test_middleware.py",
        ]
    )
    assert "auth" in result
    assert "middleware" in result


def test_empty_input() -> None:
    """infer_domains() with no args returns []."""
    from trw_mcp.scoring._recall import infer_domains

    result = infer_domains()
    assert result == set()


def test_none_input() -> None:
    """infer_domains(modified_files=None, query=None) returns []."""
    from trw_mcp.scoring._recall import infer_domains

    result = infer_domains(modified_files=None, query=None)
    assert result == set()


def test_single_char_excluded() -> None:
    """Path segments like 'a', 'x' are filtered out."""
    from trw_mcp.scoring._recall import infer_domains

    result = infer_domains(modified_files=["a/x/y/scoring.py"])
    assert "a" not in result
    assert "x" not in result
    # 'y' is a single char but... wait 'y' is 1 char
    assert "y" not in result
    assert "scoring" in result


def test_query_tokens_included() -> None:
    """Query tokens are also extracted as domains."""
    from trw_mcp.scoring._recall import infer_domains

    result = infer_domains(query="auth scoring middleware")
    assert "auth" in result
    assert "scoring" in result
    assert "middleware" in result


def test_query_deduplicates_with_files() -> None:
    """A token already from files doesn't appear twice from query."""
    from trw_mcp.scoring._recall import infer_domains

    result = infer_domains(
        modified_files=["src/auth/login.py"],
        query="auth security",
    )
    assert "auth" in result
    assert "security" in result


def test_query_structural_excluded() -> None:
    """Structural stems in query are excluded."""
    from trw_mcp.scoring._recall import infer_domains

    result = infer_domains(query="src test lib foo")
    assert "src" not in result
    assert "test" not in result
    assert "lib" not in result
    assert "foo" in result


def test_query_punctuation_stripped() -> None:
    """Punctuation is stripped from query tokens."""
    from trw_mcp.scoring._recall import infer_domains

    result = infer_domains(query="auth, scoring.")
    assert "auth" in result
    assert "scoring" in result


def test_extract_path_stems_deduplicates_within_single_path() -> None:
    """Duplicate parts in a single path are deduplicated."""
    from trw_mcp.scoring._recall import _extract_path_stems

    result = _extract_path_stems(["foo/foo/bar.py"])
    assert result.count("foo") == 1
    assert "bar" in result
