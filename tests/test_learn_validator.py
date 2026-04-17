from unittest.mock import MagicMock

from trw_mcp.tools._learn_validator import is_high_utility


def test_is_high_utility_valid():
    mock_llm = MagicMock()
    mock_llm.ask_sync.return_value = '{"valid": true, "reason": ""}'

    is_valid, reason = is_high_utility("Good summary", "Good detail", mock_llm)
    assert is_valid is True
    assert reason == ""
    mock_llm.ask_sync.assert_called_once()


def test_is_high_utility_invalid():
    mock_llm = MagicMock()
    mock_llm.ask_sync.return_value = '{"valid": false, "reason": "Too vague"}'

    is_valid, reason = is_high_utility("PRD-123 groomed", "I groomed it", mock_llm)
    assert is_valid is False
    assert reason == "Too vague"


def test_is_high_utility_markdown_fences():
    mock_llm = MagicMock()
    mock_llm.ask_sync.return_value = '```json\n{"valid": false, "reason": "Status update"}\n```'

    is_valid, reason = is_high_utility("Task completed", "Done", mock_llm)
    assert is_valid is False
    assert reason == "Status update"


def test_is_high_utility_fail_open_on_none():
    mock_llm = MagicMock()
    mock_llm.ask_sync.return_value = None

    is_valid, reason = is_high_utility("Summary", "Detail", mock_llm)
    assert is_valid is True
    assert reason == ""


def test_is_high_utility_fail_open_on_exception():
    mock_llm = MagicMock()
    mock_llm.ask_sync.side_effect = Exception("LLM Error")

    is_valid, reason = is_high_utility("Summary", "Detail", mock_llm)
    assert is_valid is True
    assert reason == ""


def test_is_high_utility_empty_summary():
    mock_llm = MagicMock()
    is_valid, reason = is_high_utility("", "Detail", mock_llm)
    assert is_valid is False
    assert reason == "Summary is empty."
    mock_llm.ask_sync.assert_not_called()
