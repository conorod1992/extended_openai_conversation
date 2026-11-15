"""Numbered speech retains structure without exposing removed citations."""

import pytest

from tests.test_speech_scope_regressions import all_splits


@pytest.mark.parametrize(
    "source,expected",
    [
        ("1. Turn on the light", "1. Turn on the light"),
        ("2) Check the switch", "2. Check the switch"),
        ("10) Restart the integration", "10. Restart the integration"),
        (
            "- Check the connection\n* Restart the device",
            "Check the connection\nRestart the device",
        ),
        ("1. First\n2) Second\n  3) Third", "1. First\n2. Second\n3. Third"),
        ("> 1) First\n> 2) Second", "1. First\n2. Second"),
        ("* 2) Check\n> - 3) Restart", "2. Check\n3. Restart"),
        ("1) First\n\n2) Second", "1. First\n2. Second"),
        ("1) First\n5.\n5)\nDone", "1. First\n5.\n5)\nDone"),
        ("1) First\n2.\n", "1. First\n2."),
        ("5)", "5)"),
        ("5.", "5."),
        ("5) \nDone", "5) \nDone"),
        ("2. https://example.com", ""),
        ("2) [example.com](https://example.com)", ""),
        ("> 2) https://example.com\n3) Go", "3. Go"),
        (
            "2) [Check the documentation](https://example.com)",
            "2. Check the documentation",
        ),
        (
            "2) https://example.com Check the documentation",
            "2. Check the documentation",
        ),
        ("2) [example.com](https://example.com) Check", "2. Check"),
        ("1) First\n2) https://example.com\n3) Third", "1. First\n3. Third"),
        ("1) First\n2)\n3) Third", "1. First\n2)\n3. Third"),
        (
            "3.14\n2026.11\n2**3\nThere are 2) switches",
            "3.14\n2026.11\n2**3\nThere are 2) switches",
        ),
        (
            "3.14 volts\n2026.11 release\n1998.\n5.0",
            "3.14 volts\n2026.11 release\n1998.\n5.0",
        ),
        (r"2\) Keep\n2\. Keep", r"2\) Keep\n2\. Keep"),
        ("`2) Code`", "2) Code"),
        ("```\n2) Code\n3.14\n```", "2) Code\n3.14"),
        ("1234567890) Very long number", "1234567890) Very long number"),
        ("12)no space\n2.. malformed", "12)no space\n2.. malformed"),
        ("*\n2. https://example.com", ""),
    ],
)
def test_numbered_speech_at_every_chunk_boundary(source, expected):
    all_splits(source, expected)


@pytest.mark.parametrize("marker", ["2.", "2)"])
@pytest.mark.parametrize("gap", [" ", "\t", "  "])
@pytest.mark.parametrize("ending", ["", "\n", "\r\n"])
def test_recognized_numbered_markers(marker, gap, ending):
    all_splits("  " + marker + gap + "Check" + ending, "2. Check")


@pytest.mark.parametrize("markdown", [True, False])
@pytest.mark.parametrize("urls", [True, False])
def test_numbering_and_independent_settings(markdown, urls):
    source = "2) Check\n3) https://example.com"
    expected = (
        ("2. Check" if urls else "2. Check\n3. https://example.com")
        if markdown
        else ("2) Check\n3)" if urls else source)
    )
    all_splits(source, expected, markdown=markdown, urls=urls)
