"""Numerical payloads survive speculative citation-list prefix holding."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.speech import (
    async_streaming_speech_cleanup,
)
from tests.test_speech import _config
from tests.test_speech_scope_regressions import all_splits


@pytest.mark.parametrize(
    "source,expected",
    [
        ("Result\n5.\nDone", "Result\n5.\nDone"),
        ("1.\n2.", "1.\n2."),
        ("1998.", "1998."),
        ("1998.\n", "1998."),
        ("5. \nDone", "5. \nDone"),
        ("5.\t\nDone", "5. \nDone"),
        ("5.\r\nDone", "5.\r\nDone"),
        ("5)\nDone", "5)\nDone"),
        ("> 5.\n", "5."),
        ("## 5.\n", "5."),
        ("* 5.\n", "5."),
        ("+ > 5)\n", "> 5)"),
        ("- 5.\n", "- 5."),
        ("* item\n2.\nDone", "item\nDone"),
        ("* item\n\n2.\nDone", "item\n2.\nDone"),
        ("* item\n> 5.\nDone", "item\n5.\nDone"),
        ("*\n2. https://example.com", ""),
        ("Sources\n> * 5. https://example.com\nDone", "Sources\nDone"),
        ("Sources\n2. [a.com](https://a.com)\nDone", "Sources\nDone"),
    ],
)
def test_numeric_payload_and_list_controls(source, expected):
    all_splits(source, expected)


@pytest.mark.parametrize("indent", ["", "  ", "\t"])
@pytest.mark.parametrize("marker", ["5.", "5)", "1998."])
@pytest.mark.parametrize("tail", ["", " ", "\t", "\n", " \n", "\t\n", "\r\n"])
def test_numeric_eof_and_line_endings(indent, marker, tail):
    all_splits(indent + marker + tail, marker)


@pytest.mark.parametrize("source,expected", [("Result\n5.\nDone", "Result\n5.\nDone"), ("> 5.\n", "5.")])
def test_presentation_listener_preserves_number_and_retained_source(source, expected):
    heard = []
    original = SimpleNamespace(content=source)
    def listener(_log, delta):
        heard.append(delta.get("content", ""))

    log = SimpleNamespace(content=[original], delta_listener=listener)
    with async_streaming_speech_cleanup(log, _config()):
        for char in source:
            log.delta_listener(log, {"content": char})
    assert "".join(heard).strip() == expected
    assert log.content == [original]
    assert original.content == source
    assert log.delta_listener is listener
