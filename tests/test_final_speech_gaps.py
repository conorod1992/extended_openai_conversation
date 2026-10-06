"""Speech output must survive malformed syntax and arbitrary provider boundaries."""

import pytest

from custom_components.extended_openai_conversation_responses.speech import (
    StreamingSpeechSanitizer,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1.example is a hostname", "1.example is a hostname"),
        ("2)words stay together", "2)words stay together"),
        ("3.14 is pi", "3.14 is pi"),
        ("([broken] nope)", "([broken] nope)"),
        ("[unfinished label", "[unfinished label"),
        ("A *unfinished emphasis", "A *unfinished emphasis"),
        ("[label](not a URL)", "[label](not a URL)"),
        ("Before ([label]) after", "Before ([label]) after"),
        ("*one * two*.", "one * two."),
        ("*word*inside* end", "word*inside end"),
    ],
)
def test_streamed_speech_preserves_prose_at_every_provider_boundary(text, expected):
    chunkings = [
        [text],
        list(text),
        *[[text[:split], text[split:]] for split in range(1, len(text))],
    ]
    for chunks in chunkings:
        sanitizer = StreamingSpeechSanitizer()
        actual = "".join(
            [*(sanitizer.feed(chunk) for chunk in chunks), sanitizer.finish()]
        )
        assert actual == expected, chunks
        assert sanitizer.buffered_chars == 0
        assert sanitizer.finish() == ""
