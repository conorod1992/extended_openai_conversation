"""Remove block-fence info only with bounded original-source evidence."""

import pytest

from custom_components.extended_openai_conversation_responses.speech import (
    StreamingSpeechSanitizer as Sanitizer,
    _final_whitespace_cleanup as clean,
    process_speech_text,
)
from tests.test_speech_scope_regressions import all_splits, streamed


@pytest.mark.parametrize(
    "source,expected",
    [
        ("```yaml\nname: house\n```", "name: house"),
        (
            "Intro\n  ```python title\nprint('hello')\n  ```\nAfter",
            "Intro\nprint('hello')\nAfter",
        ),
        (
            "```yaml\npath: C:\\config\\file.yaml\nurl: https://example.com\n# **literal** | ---\n```",
            "path: C:\\config\\file.yaml\nurl: https://example.com\n# **literal** | ---",
        ),
        ("```\ncode\n```\n```json\n{}\n```", "code\n{}"),
        ("Inline ```yaml``` text", "Inline yaml text"),
        ("```yaml```", "yaml"),
        ("`yaml`", "yaml"),
        (r"\```yaml", r"\`yaml"),
        ("```yam`l\nbody\n```", "yam`l\nbody"),
        ("```yaml", "yaml"),
        ("```yaml\n`inline` and \\literal\n```", "`inline` and \\literal"),
        ("```yaml\n```not-an-opener\nbody\n```", "not-an-opener\nbody"),
    ],
)
def test_fence_info_and_literal_controls_at_every_split(source, expected):
    all_splits(source, expected)


def test_removed_url_cannot_create_a_block_fence():
    all_splits("https://example.com ```yaml\nbody\n```", "yaml\nbody")


def test_oversized_or_unfinished_opener_has_bounded_literal_fallback():
    source = "```" + "x" * 200 + "\nhttps://example.com C:\\config\\file\n```"
    expected = clean(Sanitizer(max_buffer_chars=64).process_complete(source))
    assert expected == "x" * 200 + "\nhttps://example.com C:\\config\\file"
    for width in (1, 7, 70, len(source)):
        assert streamed(source, width, max_buffer_chars=64) == expected
    sanitizer = Sanitizer(max_buffer_chars=64)
    emitted = ""
    for char in source:
        emitted += sanitizer.feed(char)
        assert sanitizer.buffered_chars <= 128
    assert clean(emitted + sanitizer.finish()) == expected


def test_prose_latency_and_disabled_markdown_are_unchanged():
    sanitizer = Sanitizer()
    assert (
        sanitizer.feed("Ordinary prose arrives immediately.")
        == "Ordinary prose arrives immediately."
    )
    assert sanitizer.feed("\nMore prose.") == "\nMore prose."
    source = "```yaml\npath: C:\\config\\file\n```"
    all_splits(source, source, markdown=False)


def test_replacements_run_after_fence_info_cleanup():
    original = "```yaml\nvalue: 1\n```"
    config = {
        "speech_processing_enabled": True,
        "speech_strip_markdown": True,
        "speech_strip_urls": True,
        "speech_regex_replacements": [{"pattern": "value", "replacement": "setting"}],
    }
    assert process_speech_text(original, config) == "setting: 1"
    assert original.startswith("```yaml")


@pytest.mark.parametrize(
    "source,expected",
    [
        ("- ```python\n  print('hello')\n  ```", "print('hello')"),
        ("1. Example:\n   ```yaml\n   value: 1\n   ```", "1. Example:\n value: 1"),
        ("> ```python\n> print('hello')\n> ```", "print('hello')"),
        ("> ```text\n>code\n> >> 2\n>```", "code\n>> 2"),
        (
            "- Example:\n\n    ```python title=demo\n    x = 1 > 0\n    y = 8 >> 2\n    ```",
            "Example:\n x = 1 > 0\n y = 8 >> 2",
        ),
        (
            "> > ```text\n> > > operator\n> > >> shift\n> > https://example.com\n> > 123\n> > - **literal**\n> > ```",
            "> operator\n>> shift\nhttps://example.com\n123\n- **literal**",
        ),
        (
            "> - ```python\n>   `inline` and \\literal\n>   ```",
            "`inline` and \\literal",
        ),
        ("Literal:\n    ```yaml\n    value: 1\n    ```", "Literal:\n yaml\n value: 1"),
        ("https://example.com > ```yaml\nbody\n```", "yaml\nbody"),
        ("- https://example.com ```yaml\nbody\n```", "yaml\nbody"),
        (r"- \```yaml" + "\nbody\n", r"\`yaml" + "\nbody"),
        (
            "- Example:\n    ```text\n    # heading\n    1. numbered\n    > quote\n    ---\n    https://example.com\n    ```\nAfter",
            "Example:\n # heading\n 1. numbered\n > quote\n ---\n https://example.com\nAfter",
        ),
    ],
)
def test_container_fences_use_original_structure_at_every_split(source, expected):
    all_splits(source, expected)


def test_container_fences_disabled_and_bounded_fallback():
    source = "> ```python\n> 1 > 0\n> ```"
    all_splits(source, source, markdown=False, urls=False)
    source = "> ```" + "x" * 200 + "\n> code\n> ```"
    expected = clean(Sanitizer(max_buffer_chars=64).process_complete(source))
    assert "x" * 200 in expected
    assert streamed(source, 1, max_buffer_chars=64) == expected
    z = Sanitizer(max_buffer_chars=64)
    for char in source:
        z.feed(char)
        assert z.buffered_chars <= 128
