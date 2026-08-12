"""Tests for TTS-only assistant response processing."""

import logging
from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.speech import (
    DEFAULT_STREAMING_BUFFER_LIMIT,
    StreamingSpeechSanitizer,
    async_streaming_speech_cleanup,
    has_custom_speech_replacements,
    process_speech_text,
)
from homeassistant.components import conversation


def _config(**updates):
    return {
        "speech_processing_enabled": True,
        "speech_strip_markdown": True,
        "speech_strip_urls": True,
        "speech_regex_replacements": [],
        **updates,
    }


def _stream(chunks: list[str], **kwargs) -> str:
    sanitizer = StreamingSpeechSanitizer(**kwargs)
    return "".join([*(sanitizer.feed(chunk) for chunk in chunks), sanitizer.finish()])


def test_markdown_citation_and_bare_urls_are_removed_without_mutating_original() -> (
    None
):
    original = "Inside Iran ([apnews.com](https://apnews.com/article/example)) See https://example.com/news."
    spoken = process_speech_text(original, _config())
    assert spoken == "Inside Iran See"
    assert "apnews.com" in original
    assert "https://example.com" in original


def test_common_markdown_is_simplified_conservatively() -> None:
    original = "## Update\n- **Heating** is `on`\n- Temperature is 20 C"
    assert (
        process_speech_text(original, _config())
        == "Update\nHeating is on\nTemperature is 20 C"
    )


def test_custom_replacements_run_in_order_and_allow_empty_replacement() -> None:
    config = _config(
        speech_regex_replacements=[
            {"pattern": r"\[\d+\]", "replacement": ""},
            {"pattern": r"\bHA\b", "replacement": "Home Assistant"},
            {"pattern": "Home Assistant", "replacement": "the smart home"},
        ]
    )
    assert process_speech_text("HA [12] is ready", config) == "the smart home is ready"


def test_runtime_invalid_regex_is_skipped_safely(caplog) -> None:
    config = _config(
        speech_strip_markdown=False,
        speech_strip_urls=False,
        speech_regex_replacements=[
            {"pattern": "[", "replacement": ""},
            {"pattern": "HA", "replacement": "Home Assistant"},
        ],
    )
    with caplog.at_level(logging.WARNING):
        assert process_speech_text("HA works", config) == "Home Assistant works"
    assert "Skipping invalid speech regex replacement" in caplog.text


def test_disabled_processing_and_ordinary_text_are_unchanged() -> None:
    text = "Ordinary spoken text."
    assert process_speech_text(text, {"speech_processing_enabled": False}) == text
    assert process_speech_text(text, _config()) == text


def test_streaming_citation_in_one_delta() -> None:
    assert (
        _stream(["The answer is 42. ", "([example.com](https://example.com/page))"])
        == "The answer is 42."
    )


def test_streaming_citation_split_before_outer_parenthesis() -> None:
    assert (
        _stream(
            [
                "The answer is 42. ([example.com](https://example.com/page)",
                ") Next sentence.",
            ]
        )
        == "The answer is 42. Next sentence."
    )


def test_streaming_link_split_at_every_position() -> None:
    link = "[example.com](https://example.com/page)"
    for split in range(len(link) + 1):
        assert _stream([link[:split], link[split:]]) == ""
    assert (
        _stream(["[", "example.com", "](", "https://", "example.com/", "page", ")"])
        == ""
    )


def test_streaming_outer_parentheses_split_separately() -> None:
    assert (
        _stream(
            ["Before ", "(", "[example.com](https://example.com/page)", ")", " after"]
        )
        == "Before after"
    )
    assert (
        _stream(["Before ([example.com](https://example.com/page", ")", ") After"])
        == "Before After"
    )


def test_streaming_image_link_matches_existing_cleanup_semantics() -> None:
    assert _stream(["Before ![alt text](https://example.com/image.png) after"]) == (
        "Before after"
    )


def test_streaming_preserves_word_boundaries_and_normal_brackets() -> None:
    assert _stream(["cease", "fire or peace agreement."]) == (
        "ceasefire or peace agreement."
    )
    assert _stream(["The value is [approximately ", "10]."]) == (
        "The value is [approximately 10]."
    )
    assert _stream(["This (ordinary parenthesis", ") survives."]) == (
        "This (ordinary parenthesis) survives."
    )


def test_streaming_malformed_markdown_degrades_with_bounded_memory() -> None:
    malformed = "[label](https://example.com/" + "x" * (
        DEFAULT_STREAMING_BUFFER_LIMIT + 10
    )
    sanitizer = StreamingSpeechSanitizer()
    emitted = sanitizer.feed(malformed)
    assert emitted == malformed
    assert sanitizer.buffered_chars == 0
    assert sanitizer.finish() == ""
    assert _stream(["Keep [unfinished](https://example.com"], urls=False) == (
        "Keep [unfinished](https://example.com"
    )


def test_streaming_bare_url_complete_split_and_punctuation() -> None:
    # Match the established completed-response behavior: terminal punctuation is
    # part of the bare URL match and is therefore suppressed with it.
    assert _stream(["See https://example.com/test. Next."]) == "See Next."
    assert _stream(["See https://exa", "mple.com/test! Then."]) == "See Then."


def test_streaming_multiple_adjacent_and_terminal_citations() -> None:
    citation_a = "([a.example](https://a.example/article))"
    citation_b = "([b.example](https://b.example/article))"
    assert _stream([f"Answer. {citation_a} {citation_b}"]) == "Answer."
    assert _stream([f"Answer. {citation_a}\n\nMore. {citation_b}"]) == ("Answer. More.")


def test_streaming_real_trace_shape() -> None:
    chunks = [
        "As of August 12, 2026, the war remains active with no durable cease",
        "fire or peace agreement.\n\nLarge-scale attacks continue. ",
        "([example.com](https://example.com/complete?utm_source=openai))\n\nOn the battlefield, progress is limited. ",
        "Allies are responding independently. ([example.org](https://example.org/split?utm_source=openai)",
        ")\n\nUkraine has increasingly adapted.",
    ]
    assert _stream(chunks) == (
        "As of August 12, 2026, the war remains active with no durable ceasefire "
        "or peace agreement.\n\nLarge-scale attacks continue. On the battlefield, "
        "progress is limited. Allies are responding independently. Ukraine has "
        "increasingly adapted."
    )


def test_streaming_common_markdown_formatting() -> None:
    assert (
        _stream(["# Head", "ing\n- **First** item\n2", ". `Second` item"])
        == "Heading\nFirst item\nSecond item"
    )


async def test_chat_log_retains_original_while_listener_gets_safe_deltas() -> None:
    heard: list[dict] = []
    chat_log = conversation.ChatLog(SimpleNamespace(data={}), "conversation-id")
    chat_log.delta_listener = lambda _chat_log, delta: heard.append(delta)

    async def stream():
        yield {"role": "assistant"}
        yield {"content": "Answer. ([example.com](https://example.com/page))"}

    with async_streaming_speech_cleanup(chat_log, _config()):
        contents = [
            content
            async for content in chat_log.async_add_delta_content_stream(
                "conversation.test", stream()
            )
        ]

    assert contents[-1].content == ("Answer. ([example.com](https://example.com/page))")
    assert heard == [{"role": "assistant"}, {"content": "Answer."}]


def test_custom_regex_requires_completed_response_processing() -> None:
    assert has_custom_speech_replacements(
        _config(
            speech_regex_replacements=[{"pattern": "begin.*end", "replacement": ""}]
        )
    )


def test_custom_regex_disables_agent_progressive_streaming() -> None:
    subentry = SimpleNamespace(
        subentry_id="agent-id",
        title="Agent",
        data=_config(
            speech_regex_replacements=[{"pattern": "begin.*end", "replacement": ""}]
        ),
    )
    entity = ExtendedOpenAIAgentEntity(SimpleNamespace(), subentry)
    assert entity.supports_streaming is False

    subentry.data = _config()
    entity = ExtendedOpenAIAgentEntity(SimpleNamespace(), subentry)
    assert entity.supports_streaming is True
