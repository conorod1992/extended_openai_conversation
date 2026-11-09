"""Tests for TTS-only assistant response processing."""

import logging
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import speech
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.speech import (
    COMPLETED_SPEECH_EXECUTOR_THRESHOLD,
    DEFAULT_STREAMING_BUFFER_LIMIT,
    StreamingSpeechSanitizer,
    _SpeechDeltaListener,
    async_streaming_speech_cleanup,
    has_custom_speech_replacements,
    needs_async_speech_cleanup,
    process_speech_text,
    streaming_speech_processing_enabled,
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


@pytest.mark.parametrize(
    "text",
    [
        "sensor.kitchen__temperature",
        "mailboxhttps://example.com",
        "mailboxhttp://example.com",
        "mailboxwww.example.com",
        "mail@www.example.com",
        "sensor.café__temperature",
        "ordinary_under_scores",
    ],
)
def test_literal_speech_is_invariant_at_every_provider_split(text):
    assert process_speech_text(text, _config()) == text
    for split in range(1, len(text)):
        assert _stream([text[:split], text[split:]]) == text, split
    assert _stream(list(text)) == text


def test_completed_cleanup_worker_threshold_and_disabled_processing():
    limit = COMPLETED_SPEECH_EXECUTOR_THRESHOLD
    assert not needs_async_speech_cleanup("x" * (limit - 1), _config())
    assert needs_async_speech_cleanup("x" * limit, _config())
    assert not needs_async_speech_cleanup(
        "x" * limit, _config(speech_processing_enabled=False)
    )
    assert needs_async_speech_cleanup(
        "short", _config(speech_regex_replacements=[{"pattern": "x", "replacement": "y"}])
    )


def test_markdown_citation_and_bare_urls_are_removed_without_mutating_original() -> (
    None
):
    original = "Inside Iran ([apnews.com](https://apnews.com/article/example)) See https://example.com/news."
    spoken = process_speech_text(original, _config())
    assert spoken == "Inside Iran See."
    assert "apnews.com" in original
    assert "https://example.com" in original


def test_common_markdown_is_simplified_conservatively() -> None:
    original = "## Update\n- **Heating** is `on`\n- Temperature is 20 C"
    assert (
        process_speech_text(original, _config())
        == "Update\nHeating is on\nTemperature is 20 C"
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("*one* _two_ **three** __four__", "one two three four"),
        ("(*one*), _two_!", "(one), two!"),
        ("*an ordinary longer sentence with emphasis*", "an ordinary longer sentence with emphasis"),
        (
            "sensor.kitchen_temperature and ordinary_under_scores",
            "sensor.kitchen_temperature and ordinary_under_scores",
        ),
        ("2 * 3 and _ unmatched *", "2 * 3 and _ unmatched *"),
        (r"\*literal\* and \_literal\_", r"\*literal\* and \_literal\_"),
        ("*one* https://example.com/a_b _two_", "one two"),
    ],
)
def test_single_emphasis_preserves_prose_and_stream_boundaries(text, expected):
    assert process_speech_text(text, _config()) == expected
    assert _stream(list(text)).strip() == expected


def test_unresolved_single_emphasis_is_bounded_and_preserves_word_boundary():
    sanitizer = StreamingSpeechSanitizer(max_buffer_chars=64)
    malformed = "*" + "x" * 65
    assert sanitizer.feed(malformed) == malformed
    assert sanitizer.buffered_chars == 0
    assert sanitizer.feed("_tail_") + sanitizer.finish() == "_tail_"


@pytest.mark.parametrize("text,expected", [
    ("***hello***", "hello"),
    ("___hello___", "hello"),
    ("*outer **inner** outer*", "outer inner outer"),
    ("*outer __inner__ outer*", "outer inner outer"),
    ("_outer *inner* outer_", "outer inner outer"),
    ("*outer `inner` outer*", "outer inner outer"),
    ("**Hello _world_**!", "Hello world!"),
    ("*Hello __世界__*", "Hello 世界"),
])
def test_nested_formatting_has_one_completed_and_streaming_contract(text, expected):
    assert process_speech_text(text, _config()) == expected
    assert _stream(list(text)) == expected
    for split in range(len(text) + 1):
        assert _stream([text[:split], text[split:]]) == expected


def test_custom_replacements_run_in_order_and_allow_empty_replacement() -> None:
    config = _config(
        speech_regex_replacements=[
            {"pattern": r"\[\d+\]", "replacement": ""},
            {"pattern": r"\bHA\b", "replacement": "Home Assistant"},
            {"pattern": "Home Assistant", "replacement": "the smart home"},
        ]
    )
    assert process_speech_text("HA [12] is ready", config) == "the smart home is ready"


def test_url_cleanup_precedes_custom_replacements() -> None:
    config = _config(
        speech_strip_urls=True,
        speech_regex_replacements=[
            {"pattern": r"https?://\S+", "replacement": "web link"}
        ],
    )
    assert process_speech_text("Read https://example.com now", config) == "Read now"


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
    assert _stream(["See https://example.com/test. Next."]) == "See. Next."
    assert _stream(["See https://exa", "mple.com/test! Then."]) == "See! Then."


@pytest.mark.parametrize(
    "original,expected",
    [
        (
            "Check https://example.com. Then restart Home Assistant.",
            "Check. Then restart Home Assistant.",
        ),
        (
            "Docs are at https://example.com, and the forum too.",
            "Docs are at, and the forum too.",
        ),
        ("See the docs (https://example.com) for details.", "See the docs for details."),
        ("Link: <https://example.com>", "Link:"),
        ("Link: <www.example.com> and more", "Link: and more"),
        (
            "Read (https://en.wikipedia.org/wiki/Function_(mathematics)) next.",
            "Read next.",
        ),
        ("See https://example.com/a(b) now.", "See now."),
        ("Try https://example.com/a?x=1&y=2! Next.", "Try! Next."),
        ("Try https://example.com/a]), then continue.", "Try]), then continue."),
        ("This (is not a URL) and <ordinary text>.", "This (is not a URL) and <ordinary text>."),
        ("Code: `<https://example.com>` stays literal.", "Code: <https://example.com> stays literal."),
    ],
)
def test_bare_url_punctuation_wrappers_and_code_at_every_split(original, expected):
    assert process_speech_text(original, _config()) == expected
    assert _stream([original]) == expected
    assert _stream(list(original)) == expected
    for split in range(len(original) + 1):
        assert _stream([original[:split], original[split:]]) == expected, split


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


@pytest.mark.parametrize(
    "original,expected",
    [
        (
            "Summary.\n\n[Source](https://example.com)\n\n## Next steps\n- Take a coat.",
            "Summary.\nNext steps\nTake a coat.",
        ),
        (
            "Answer.\n[Source](https://example.com)\n- First\n2. Second\n> Note",
            "Answer.\nFirst\nSecond\nNote",
        ),
        (
            "The result is **[here](https://example.com)**.",
            "The result is.",
        ),
        (
            "The result is *[here](https://example.com)*.",
            "The result is.",
        ),
        (
            "Answer. [Source](https://example.com)\n\nMore.",
            "Answer. More.",
        ),
        (
            "Answer. [Source](https://example.com) ## literal inline heading",
            "Answer. ## literal inline heading",
        ),
        (
            "Answer. [Source](https://example.com)\n#hashtag stays literal",
            "Answer. #hashtag stays literal",
        ),
        (
            "The answer is 2**3. [Source](https://example.com)\n2**3 again",
            "The answer is 2**3. 2**3 again",
        ),
        (
            "Read **details [here](https://example.com) now**.",
            "Read details now.",
        ),
    ],
)
def test_link_suppression_preserves_real_markdown_boundaries_at_all_splits(
    original, expected
):
    assert process_speech_text(original, _config()) == expected
    # Progressive speech intentionally does not run the completed response's
    # punctuation/blank-line whitespace normalizer.
    streamed = [_stream([original]), _stream(list(original))]
    streamed.extend(
        _stream([original[:split], original[split:]])
        for split in range(len(original) + 1)
    )
    for index, result in enumerate(streamed):
        assert speech._final_whitespace_cleanup(result) == expected, index
        if "## Next steps" in original or "\n- First" in original:
            assert result == expected, index


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


def test_streaming_buffer_limit_rejects_too_small_values() -> None:
    with pytest.raises(ValueError, match="at least 32"):
        StreamingSpeechSanitizer(max_buffer_chars=31)


def test_empty_delta_and_terminal_separator_are_safe() -> None:
    sanitizer = StreamingSpeechSanitizer()
    assert sanitizer.feed("") == ""
    assert sanitizer.feed("Answer [source](https://example.com)") == "Answer"
    assert sanitizer.finish() == ""


def test_partial_url_prefix_is_buffered_until_resolved() -> None:
    sanitizer = StreamingSpeechSanitizer(markdown=False, urls=True)
    assert sanitizer.feed("See htt") == "See"
    assert sanitizer.feed("ps://example.com/path") == ""
    assert sanitizer.feed(" next") == " next"
    assert sanitizer.finish() == ""


def test_url_like_text_inside_identifier_is_not_suppressed() -> None:
    assert _stream(["mailboxhttps://example.com done"], markdown=False) == (
        "mailboxhttps://example.com done"
    )


def test_final_incomplete_markdown_constructs_are_preserved() -> None:
    assert _stream(["Keep [unfinished"], urls=False) == "Keep [unfinished"
    assert _stream(["Keep [label]"], urls=False) == "Keep [label]"
    assert _stream(["Keep [label]("], urls=False) == "Keep [label]("
    assert _stream(["Keep [label](bad url"], urls=False) == "Keep [label](bad url"


def test_nested_parentheses_in_markdown_url_are_suppressed() -> None:
    assert _stream(["Before [label](https://example.com/a(b)c) after"]) == (
        "Before after"
    )


def test_markdown_disabled_returns_text_without_format_buffering() -> None:
    sanitizer = StreamingSpeechSanitizer(markdown=False, urls=False)
    assert sanitizer.feed("**literal** `code`") == "**literal** `code`"
    assert sanitizer.finish() == ""


def test_line_prefix_boundaries_and_non_markers() -> None:
    sanitizer = StreamingSpeechSanitizer()
    assert sanitizer._line_prefix("   ", False) == ("incomplete", 0)
    assert sanitizer._line_prefix("   ", True) == ("none", 0)
    assert sanitizer._line_prefix("###", False) == ("incomplete", 0)
    assert sanitizer._line_prefix("###", True) == ("none", 0)
    assert sanitizer._line_prefix("-", False) == ("incomplete", 0)
    assert sanitizer._line_prefix("-", True) == ("none", 0)
    assert sanitizer._line_prefix("12", False) == ("incomplete", 0)
    assert sanitizer._line_prefix("12", True) == ("none", 0)
    assert sanitizer._line_prefix("12.", False) == ("incomplete", 0)
    assert sanitizer._line_prefix("12.", True) == ("none", 0)
    assert sanitizer._line_prefix("word", False) == ("none", 0)


def test_streaming_enablement_matrix() -> None:
    assert not streaming_speech_processing_enabled(
        _config(speech_processing_enabled=False)
    )
    assert not streaming_speech_processing_enabled(
        _config(
            speech_regex_replacements=[{"pattern": "x", "replacement": "y"}]
        )
    )
    assert not streaming_speech_processing_enabled(
        _config(speech_strip_markdown=False, speech_strip_urls=False)
    )
    assert streaming_speech_processing_enabled(
        _config(speech_strip_markdown=False, speech_strip_urls=True)
    )


def test_has_custom_replacements_rejects_disabled_or_non_list_rules() -> None:
    assert not has_custom_speech_replacements(
        _config(speech_processing_enabled=False, speech_regex_replacements=[{}])
    )
    assert not has_custom_speech_replacements(
        _config(speech_regex_replacements={"pattern": "x"})
    )


def test_delta_listener_forwards_role_non_string_and_metadata_only_deltas() -> None:
    heard: list[dict] = []
    listener = _SpeechDeltaListener(
        lambda _chat_log, delta: heard.append(delta), _config()
    )
    chat_log = object()

    listener(chat_log, {"role": "assistant"})
    listener(chat_log, {"content": 42})
    listener(chat_log, {"content": "[source](https://example.com)", "id": "chunk"})

    assert heard == [
        {"role": "assistant"},
        {"content": 42},
        {"id": "chunk"},
    ]


def test_delta_listener_flushes_buffered_tail_before_new_role() -> None:
    heard: list[dict] = []
    listener = _SpeechDeltaListener(
        lambda _chat_log, delta: heard.append(delta), _config()
    )
    chat_log = object()

    listener(chat_log, {"content": "unfinished "})
    listener(chat_log, {"role": "assistant"})

    assert "".join(delta.get("content", "") for delta in heard[:-1]) == "unfinished "
    assert heard[-1] == {"role": "assistant"}


def test_streaming_cleanup_bypasses_without_listener_or_when_disabled() -> None:
    chat_log = SimpleNamespace(delta_listener=None)
    with async_streaming_speech_cleanup(chat_log, _config()):
        assert chat_log.delta_listener is None

    original = lambda _chat_log, _delta: None
    chat_log = SimpleNamespace(delta_listener=original)
    with async_streaming_speech_cleanup(
        chat_log, _config(speech_processing_enabled=False)
    ):
        assert chat_log.delta_listener is original
    assert chat_log.delta_listener is original


def test_streaming_cleanup_restores_listener_after_exception() -> None:
    original = lambda _chat_log, _delta: None
    chat_log = SimpleNamespace(delta_listener=original)

    with pytest.raises(RuntimeError, match="boom"):
        with async_streaming_speech_cleanup(chat_log, _config()):
            assert chat_log.delta_listener is not original
            raise RuntimeError("boom")

    assert chat_log.delta_listener is original


def test_process_speech_text_skips_non_mapping_and_missing_key_rules(caplog) -> None:
    config = _config(
        speech_strip_markdown=False,
        speech_strip_urls=False,
        speech_regex_replacements=["not-a-rule", {"pattern": "missing replacement"}],
    )
    with caplog.at_level(logging.WARNING):
        assert process_speech_text("unchanged", config) == "unchanged"
    assert caplog.text.count("Skipping invalid speech regex replacement") == 2

    non_list = _config(
        speech_strip_markdown=False,
        speech_strip_urls=False,
        speech_regex_replacements={"pattern": "hello", "replacement": "goodbye"},
    )
    assert process_speech_text("hello   world", non_list) == "hello world"


def test_emit_separator_handles_whitespace_punctuation_and_plain_text() -> None:
    sanitizer = speech.StreamingSpeechSanitizer()
    output: list[str] = []

    sanitizer._emit(output, "")
    assert output == []

    sanitizer._last_output = "word"
    sanitizer._needs_separator = True
    sanitizer._emit(output, "   ")
    assert output == []
    assert sanitizer._needs_separator is True

    sanitizer._emit(output, ",")
    assert output == [","]
    assert sanitizer._needs_separator is False

    sanitizer._last_output = "word"
    sanitizer._needs_separator = True
    output.clear()
    sanitizer._emit(output, "next")
    assert output == [" ", "next"]


def test_suppress_and_hold_whitespace_cover_trailing_chunks() -> None:
    sanitizer = speech.StreamingSpeechSanitizer()
    output = ["text", "   "]
    sanitizer._last_output = "text   "

    end = sanitizer._suppress(output, 12)

    assert end == 12
    assert output == ["text"]
    assert sanitizer._last_output == "text"
    assert sanitizer._needs_separator is True
    assert sanitizer.suppressed_constructs == 1

    output = ["x", " ", "\t"]
    sanitizer._last_output = "x \t"
    assert sanitizer._hold_preceding_whitespace(output) == " \t"
    assert output == ["x"]
    assert sanitizer._last_output == "x"

    # A whitespace-only output chunk may contain multiple actual characters.
    output = ["prefix", " \t", "\n"]
    sanitizer._last_output = "x \t\n"
    assert sanitizer._hold_preceding_whitespace(output) == " \t\n"
    assert output == ["prefix"]
    assert sanitizer._last_output == "x"


def test_outer_image_link_and_partial_outer_parenthesis_paths() -> None:
    assert _stream(["Before (![alt](https://example.com/a.png)) after"]) == "Before after"

    sanitizer = speech.StreamingSpeechSanitizer()
    assert sanitizer.feed("Before (") == "Before"
    assert sanitizer.finish() == " ("


def test_partial_url_prefix_and_identifier_paths() -> None:
    sanitizer = speech.StreamingSpeechSanitizer(markdown=False, urls=True)
    assert sanitizer.feed("prefix htt") == "prefix"
    assert sanitizer.feed("x next") == " httx next"
    assert sanitizer.finish() == ""

    assert _stream(["h", "ttp://example.com next"], markdown=False, urls=True) == "next"


def test_markdown_link_parser_final_and_nested_failure_paths() -> None:
    parser = speech.StreamingSpeechSanitizer._markdown_link_end

    assert parser("[label", 0, True) == ("no", 0)
    assert parser("[label]", 0, True) == ("no", 0)
    assert parser("[label](", 0, True) == ("no", 0)
    assert parser("[label]x", 0, True) == ("no", 0)
    assert parser("[label](bad url)", 0, False) == ("no", 0)
    assert parser("[label](https://example.com/a(b)c)", 0, False)[0] == "complete"


def test_format_handles_newline_backticks_markers_and_terminal_tail() -> None:
    sanitizer = speech.StreamingSpeechSanitizer(markdown=True, urls=False)
    assert sanitizer._format("plain\n`code` **bold** __word__", final=True) == (
        "plain\ncode bold word"
    )

    sanitizer = speech.StreamingSpeechSanitizer(markdown=True, urls=False)
    assert sanitizer._format("trailing*", final=False) == "trailing"
    assert sanitizer._format("", final=True) == "*"


def test_line_prefix_strips_supported_markers_and_rejects_near_misses() -> None:
    line_prefix = speech.StreamingSpeechSanitizer._line_prefix

    assert line_prefix("   ###   Heading", False) == ("strip", 9)
    assert line_prefix("\t>  Quote", False) == ("strip", 4)
    assert line_prefix("  -   Item", False) == ("strip", 6)
    assert line_prefix("12)  Item", False) == ("strip", 5)
    assert line_prefix("123x Item", False) == ("none", 0)
    assert line_prefix("####### Heading", False) == ("none", 0)
    assert line_prefix("-not-a-list", False) == ("none", 0)


def test_delta_listener_drops_content_only_suppressed_delta_and_flushes_tail() -> None:
    heard: list[dict] = []
    listener = speech._SpeechDeltaListener(
        lambda _chat_log, delta: heard.append(delta), _config()
    )
    chat_log = object()

    listener(chat_log, {"content": "[source](https://example.com)"})
    assert heard == []

    listener(chat_log, {"content": "tail "})
    listener.flush(chat_log)
    assert "".join(item.get("content", "") for item in heard) == "tail "


def test_streaming_cleanup_flush_failure_still_restores_original_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = lambda _chat_log, _delta: None
    chat_log = SimpleNamespace(delta_listener=original)

    def fail_flush(self, _chat_log):
        raise RuntimeError("flush failed")

    monkeypatch.setattr(speech._SpeechDeltaListener, "flush", fail_flush)

    with pytest.raises(RuntimeError, match="flush failed"):
        with speech.async_streaming_speech_cleanup(chat_log, _config()):
            assert chat_log.delta_listener is not original

    assert chat_log.delta_listener is original


def test_streaming_cleanup_summary_runs_after_success(monkeypatch: pytest.MonkeyPatch) -> None:
    original = lambda _chat_log, _delta: None
    chat_log = SimpleNamespace(delta_listener=original)
    calls: list[str] = []

    monkeypatch.setattr(
        speech._SpeechDeltaListener,
        "log_summary",
        lambda self: calls.append("summary"),
    )

    with speech.async_streaming_speech_cleanup(chat_log, _config()):
        assert chat_log.delta_listener is not original

    assert calls == ["summary"]
    assert chat_log.delta_listener is original


def test_process_speech_text_skips_bad_rule_types_and_bad_replacement(caplog) -> None:
    config = _config(
        speech_strip_markdown=False,
        speech_strip_urls=False,
        speech_regex_replacements=[
            None,
            {"replacement": "missing pattern"},
            {"pattern": "x", "replacement": r"\9"},
            {"pattern": "x", "replacement": "y"},
        ],
    )

    with caplog.at_level(logging.WARNING):
        assert speech.process_speech_text("x", config) == "y"

    assert caplog.text.count("Skipping invalid speech regex replacement") == 3


def test_final_whitespace_cleanup_preserves_parentheses_and_collapses_spacing() -> None:
    assert speech._final_whitespace_cleanup("Hello ( )   , world\n\n\nnext") == (
        "Hello ( ), world\nnext"
    )


def test_suppress_trims_trailing_space_without_dropping_nonempty_chunk() -> None:
    sanitizer = speech.StreamingSpeechSanitizer()
    output = ["text "]
    sanitizer._last_output = "text "

    assert sanitizer._suppress(output, 7) == 7
    assert output == ["text"]
    assert sanitizer._last_output == "text"


def test_lone_markdown_image_marker_waits_for_stream_completion() -> None:
    sanitizer = speech.StreamingSpeechSanitizer(markdown=True, urls=False)

    assert sanitizer.feed("Hello !") == "Hello "
    assert sanitizer.finish() == "!"


def test_format_releases_ambiguous_line_prefix_at_safety_limit() -> None:
    sanitizer = speech.StreamingSpeechSanitizer(markdown=True, urls=False)
    text = " " * (speech._FORMAT_PREFIX_LIMIT + 1)

    assert sanitizer._format(text, final=False) == text
    assert sanitizer._format_buffer == ""
    assert sanitizer._line_start is False


def test_line_prefix_finalizes_ambiguous_terminal_markers_as_plain_text() -> None:
    line_prefix = speech.StreamingSpeechSanitizer._line_prefix

    assert line_prefix("#", True) == ("none", 0)
    assert line_prefix("-", True) == ("none", 0)
    assert line_prefix("12", True) == ("none", 0)
    assert line_prefix("12)", True) == ("none", 0)
    assert line_prefix("   ", True) == ("none", 0)


@pytest.mark.parametrize("prefix", ["https://", "http://", "www."])
@pytest.mark.parametrize(
    "suffix", ["", "/path", "/path?query=one", ",", ".", ")", "/path),"]
)
def test_bare_web_addresses_share_runtime_and_completed_cleanup(prefix, suffix) -> None:
    text = f"Visit {prefix}example.com{suffix} for details. Ordinary prose stays."
    punctuation = {
        ",": ",",
        ".": ".",
        ")": ")",
        "/path),": "),",
    }.get(suffix, "")
    expected = f"Visit{punctuation} for details. Ordinary prose stays."
    assert process_speech_text(text, _config()) == expected
    assert process_speech_text(text, _config(speech_strip_urls=False)) == text
    for split in range(len(text) + 1):
        assert _stream([text[:split], text[split:]]) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Ordinary text with periods. Version 1.2.3.",
        "The www. prefix is ordinary text.",
        "Keep www.example and user@www.example.com unchanged.",
    ],
)
def test_www_cleanup_preserves_ordinary_text(text) -> None:
    assert process_speech_text(text, _config()) == text
    for split in range(len(text) + 1):
        assert _stream([text[:split], text[split:]]) == text


@pytest.mark.parametrize("text,expected", [
    ("2**3 = 8", "2**3 = 8"),
    ("2**-3", "2**-3"),
    ("x**y", "x**y"),
    ("(2)**3", "(2)**3"),
    ("word**bold**word", "wordboldword"),
    ("2**3**2", "2**3**2"),
    ("2**3 and **eight**", "2**3 and eight"),
    ("`x**2 + __name__`", "x**2 + __name__"),
    ("`https://example.com/a_b`", "https://example.com/a_b"),
    ("``a ` b ** c``", "a ` b ** c"),
    ("**eight** and 2**3", "eight and 2**3"),
    ("2**3**", "23"),
    ("**3**2", "32"),
    ("*`a* b`*", "a* b"),
    ("_`a_ b`_", "a_ b"),
    ("*``a ` * b``*", "a ` * b"),
    ("*`https://example.com a* b`*", "https://example.com a* b"),
    ("`call()`", "call()"),
])
def test_literal_operators_and_code_survive_all_stream_splits(text, expected):
    assert process_speech_text(text, _config()) == expected
    for split in range(len(text) + 1):
        assert _stream([text[:split], text[split:]]).strip() == expected
    assert _stream(list(text)).strip() == expected


@pytest.mark.parametrize(
    "citation",
    [
        "**[source](https://example.com)**",
        "*[source](https://example.com)*",
        "([source](https://example.com))",
        "[source](https://example.com)",
    ],
)
@pytest.mark.parametrize("spacing", [" ", "   ", "        ", "\t \t", "\n\n"])
def test_removed_citation_before_pending_url_never_replays_source(
    citation: str, spacing: str
) -> None:
    # A previously removed link must not re-enter the raw buffer when the next
    # URL reaches a provider boundary. Output whitespace is not a raw offset.
    original = f"Hello{spacing}{citation} https://example.org"
    assert process_speech_text(original, _config()) == "Hello"
    for chunks in ([original], list(original)):
        streamed = _stream(chunks)
        assert speech._final_whitespace_cleanup(streamed) == "Hello", chunks
    for split in range(len(original) + 1):
        streamed = _stream([original[:split], original[split:]])
        assert speech._final_whitespace_cleanup(streamed) == "Hello", split


@pytest.mark.parametrize("continuation", ["httx next", "www.example next"])
def test_pending_url_prefix_after_removed_citation_keeps_literal_text(
    continuation: str,
) -> None:
    # If the apparent URL becomes ordinary prose, replay only held whitespace,
    # not parts of the already suppressed citation.
    original = f"Hello **[source](https://example.com)** {continuation}"
    expected = f"Hello {continuation}"
    assert process_speech_text(original, _config()) == expected
    for split in range(len(original) + 1):
        streamed = _stream([original[:split], original[split:]])
        assert speech._final_whitespace_cleanup(streamed) == expected, split
    assert speech._final_whitespace_cleanup(_stream(list(original))) == expected


@pytest.mark.parametrize(
    "original,expected",
    [
        (
            "The lights are off    [source](https://example.com) and the door is locked.",
            "The lights are off and the door is locked.",
        ),
        (
            "Summary.\n\n  [source](https://example.com)\n\n## Next steps\n- Take a coat.",
            "Summary.\nNext steps\nTake a coat.",
        ),
        (
            "**Result**[source](https://example.com) **Explanation**.",
            "Result Explanation.",
        ),
        (
            "Hello **_[source](https://example.com)_** world.",
            "Hello world.",
        ),
    ],
)
def test_removed_citations_keep_spoken_boundaries_and_formatting(
    original: str, expected: str
) -> None:
    assert process_speech_text(original, _config()) == expected
    chunks = [[original], list(original)]
    chunks.extend(
        [original[:split], original[split:]]
        for split in range(len(original) + 1)
    )
    for position, parts in enumerate(chunks):
        assert speech._final_whitespace_cleanup(_stream(parts)) == expected, position


@pytest.mark.parametrize(
    "source,expected",
    [
        ("Pass **kwargs to the function.", "Pass **kwargs to the function."),
        (
            "Use sensor.__temperature for the reading.",
            "Use sensor.__temperature for the reading.",
        ),
        ("A ***unfinished span", "A ***unfinished span"),
        ("A ~~unfinished span", "A ~~unfinished span"),
        ("**This is bold** and __this too__.", "This is bold and this too."),
        (r"A \*literal\* and **bold**.", r"A \*literal\* and bold."),
        (r"A \_literal\_ and __strong__.", r"A \_literal\_ and strong."),
        (
            r"A \` literal; **important**. See https://example.com now.",
            r"A \` literal; important. See now.",
        ),
        (r"Type \[name](target) literally.", r"Type \[name](target) literally."),
        (
            "0871234567. That is the contact number.",
            "0871234567. That is the contact number.",
        ),
        ("123456789. This is an ordered item.", "This is an ordered item."),
        (
            "1234567890. This is not an ordered item.",
            "1234567890. This is not an ordered item.",
        ),
    ],
)
def test_speech_literal_syntax_is_not_silently_deleted(
    source: str, expected: str
) -> None:
    assert process_speech_text(source, _config()) == expected
    assert speech._final_whitespace_cleanup(_stream([source])) == expected
    assert speech._final_whitespace_cleanup(_stream(list(source))) == expected
    for split in range(len(source) + 1):
        assert speech._final_whitespace_cleanup(
            _stream([source[:split], source[split:]])
        ) == expected, split


def test_url_only_speech_cleanup_keeps_boundary_after_long_whitespace() -> None:
    source = "The lights are off    https://example.com and the door is locked."
    cfg = _config(speech_strip_markdown=False)
    expected = "The lights are off and the door is locked."
    assert process_speech_text(source, cfg) == expected
    for split in range(len(source) + 1):
        assert speech._final_whitespace_cleanup(
            _stream([source[:split], source[split:]], markdown=False)
        ) == expected, split


def test_long_unmatched_speech_delimiter_has_bounded_fallback() -> None:
    sanitizer = StreamingSpeechSanitizer(max_buffer_chars=64)
    source = "before **" + ("x" * 80)
    result = sanitizer.feed(source) + sanitizer.finish()
    assert result == source
    assert sanitizer.buffered_chars == 0


@pytest.mark.parametrize(
    "source,expected",
    [
        ("**bold *italics***", "bold italics"),
        ("__bold _italics___", "bold italics"),
        ("*italic **both***", "italic both"),
        ("**a **b** c**", "a b c"),
        ("word**bold**word", "wordboldword"),
        ("2**3", "2**3"),
        ("2**3**", "23"),
        ("2**3**2", "2**3**2"),
        (r"**before `a\` after**", r"before a\ after"),
    ],
)
def test_nested_speech_emphasis_and_numeric_compatibility(
    source: str, expected: str
) -> None:
    assert process_speech_text(source, _config()) == expected
    for chunks in ([source], list(source)):
        assert speech._final_whitespace_cleanup(_stream(chunks)) == expected
    for split in range(len(source) + 1):
        assert speech._final_whitespace_cleanup(
            _stream([source[:split], source[split:]])
        ) == expected, split



@pytest.mark.parametrize(
    "prefix,suffix,initial,continuation",
    [
        ("Compute 2**10", "; then the lights are on.", "Compute 2", "**10; then the lights are on."),
        ("Result 2**(3)", "; next step.", "Result 2**(3)", "; next step."),
        ("The value is 2**3", "**2; continue.", "The value is 2", "**3**2; continue."),
    ],
)
def test_literal_numeric_marker_releases_progressive_speech_once_disambiguated(
    prefix: str, suffix: str, initial: str, continuation: str
) -> None:
    sanitizer = StreamingSpeechSanitizer()
    assert sanitizer.feed(prefix) == initial
    assert sanitizer.feed(suffix) == continuation
    assert sanitizer.finish() == ""
    assert sanitizer.buffered_chars == 0


def test_numeric_marker_waits_only_for_a_viable_closing_prefix() -> None:
    sanitizer = StreamingSpeechSanitizer()
    assert sanitizer.feed("Compute 2**3") == "Compute 2"
    assert sanitizer.feed("*") == ""
    # The second asterisk completes the closing marker, but the following
    # character is still needed to distinguish valid bold from arithmetic.
    assert sanitizer.feed("*") == ""
    assert sanitizer.feed("2; more text.") == "**3**2; more text."
    assert sanitizer.finish() == ""


def test_complete_speech_keeps_cleaning_after_long_unmatched_emphasis() -> None:
    prose = "ordinary prose " * 400
    source = "Intro *unfinished " + prose + "\n## Later\n- **Ready**."
    expected = "Intro *unfinished " + prose + "\nLater\nReady."
    assert process_speech_text(source, _config()) == expected
    # Streaming deliberately retains its bounded malformed-input fallback.
    sanitizer = StreamingSpeechSanitizer()
    streamed = sanitizer.feed(source) + sanitizer.finish()
    assert streamed != expected
    assert "## Later" in streamed


def test_complete_speech_keeps_cleaning_after_long_unclosed_link() -> None:
    prose = "ordinary prose " * 400
    source = "Intro [unfinished " + prose + "\n## Later\n- **Ready**."
    expected = "Intro [unfinished " + prose + "\nLater\nReady."
    assert process_speech_text(source, _config()) == expected


@pytest.mark.parametrize(
    "source",
    [
        "**bold** and __strong__.",
        "## Heading\n- **Item**\n2. Second item.",
        "Read [source](https://example.com) and continue.",
        "2**3; literal operators are preserved.",
        "*outer **inner** outer*",
        "Use \`code **not bold**\` and **real bold**.",
        r"A \*literal\* and **bold**.",
        "Hello **_[source](https://example.com)_** world.",
    ],
)
def test_completed_speech_uses_the_same_supported_formatting_rules(
    source: str,
) -> None:
    expected = speech._final_whitespace_cleanup(_stream([source]))
    assert process_speech_text(source, _config()) == expected


def test_completed_speech_long_malformed_delimiters_use_indexed_lookahead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NeverScan:
        def finditer(self, *_args):
            pytest.fail("Completed parsing must use indexed single-delimiter lookahead")

    def never_scan(*_args, **_kwargs):
        pytest.fail("Completed parsing must use indexed paired-delimiter lookahead")

    monkeypatch.setattr(
        speech, "_EMPHASIS_TOKENS", {"*": NeverScan(), "_": NeverScan()}
    )
    monkeypatch.setattr(
        StreamingSpeechSanitizer, "_has_closer", classmethod(never_scan)
    )
    source = ("*word " * 4500) + "\n## Later\n- **Ready**."
    cleaned = process_speech_text(source, _config())
    assert cleaned.startswith("*word *word ")
    assert cleaned.endswith("\nLater\nReady.")
    assert "## Later" not in cleaned


async def test_known_complete_speech_listener_reassembles_synthetic_deltas(
    hass,
) -> None:
    heard = []
    original_listener = lambda _log, delta: heard.append(delta)
    log = SimpleNamespace(delta_listener=original_listener)
    prose = "plain words " * 420
    original = "Before *unfinished " + prose + "\n## Heading\n- **Ready**."
    with async_streaming_speech_cleanup(log, _config(), completed=True) as wrapper:
        assert wrapper is not None
        log.delta_listener(log, {"role": "assistant"})
        log.delta_listener(log, {"content": "Before *unfinished " + prose})
        log.delta_listener(log, {"content": "\n## Heading\n- **Ready**."})
        assert heard == []
        await wrapper.async_flush_completed(hass, log)
    assert log.delta_listener is original_listener
    assert heard == [
        {"role": "assistant"},
        {"content": speech._built_in_cleanup(original, markdown=True, urls=True)},
    ]


async def test_known_complete_listener_preserves_role_groups_and_metadata(hass) -> None:
    heard = []
    log = SimpleNamespace(
        delta_listener=lambda _log, delta: heard.append(delta)
    )
    with async_streaming_speech_cleanup(log, _config(), completed=True) as wrapper:
        log.delta_listener(log, {"role": "assistant"})
        log.delta_listener(log, {"content": "**One**"})
        log.delta_listener(log, {"content": " and [link](https://example.com)"})
        log.delta_listener(log, {"tool_calls": ["opaque"]})
        log.delta_listener(log, {"role": "assistant"})
        log.delta_listener(log, {"content": "## Two"})
        assert heard == []
        await wrapper.async_flush_completed(hass, log)
    assert heard == [
        {"role": "assistant"},
        {"content": "One and"},
        {"tool_calls": ["opaque"]},
        {"role": "assistant"},
        {"content": "Two"},
    ]



async def test_known_complete_speech_presentation_preserves_original_chat_log(
    hass,
) -> None:
    original = "Start *unfinished " + ("ordinary words " * 420) + "\n## Status\n- **Ready**."
    heard: list[dict] = []
    chat_log = conversation.ChatLog(SimpleNamespace(data={}), "speech-complete")
    chat_log.delta_listener = lambda _log, delta: heard.append(delta)

    async def completed_adapted_stream():
        yield {"role": "assistant"}
        midpoint = len(original) // 2
        yield {"content": original[:midpoint]}
        yield {"content": original[midpoint:]}

    with async_streaming_speech_cleanup(
        chat_log, _config(), completed=True
    ) as presentation:
        retained = [
            item
            async for item in chat_log.async_add_delta_content_stream(
                "conversation.speech_test", completed_adapted_stream()
            )
        ]
        assert heard == []
        assert presentation is not None
        await presentation.async_flush_completed(hass, chat_log)

    assert retained[-1].content == original
    assert heard == [
        {"role": "assistant"},
        {
            "content": speech._built_in_cleanup(
                original, markdown=True, urls=True
            )
        },
    ]


def test_completed_speech_path_is_one_shot() -> None:
    sanitizer = StreamingSpeechSanitizer()
    assert sanitizer.process_complete("**one**") == "one"
    assert sanitizer.finish() == ""
    with pytest.raises(ValueError, match="fresh sanitizer"):
        sanitizer.process_complete("**two**")



def test_completed_speech_many_numeric_literals_reach_later_markup() -> None:
    source = ("2**3; " * 1600) + "\n## End\n- **Ready**."
    result = process_speech_text(source, _config())
    assert result.count("2**3;") == 1600
    assert result.endswith("\nEnd\nReady.")
    assert "## End" not in result


def test_completed_speech_many_links_keep_one_spoken_boundary() -> None:
    source = "Before " + ("[source](https://example.com/path(v1)) " * 200) + "after."
    assert process_speech_text(source, _config()) == "Before after."


def test_completed_speech_many_unclosed_parentheses_do_not_block_later_text() -> None:
    source = "Start " + ("[broken](" * 900) + "\n## Heading\n- **Ready**."
    result = process_speech_text(source, _config())
    assert result.startswith("Start [broken](")
    assert result.endswith("\nHeading\nReady.")
