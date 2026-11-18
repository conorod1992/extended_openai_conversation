"""Unresolved removed spans retain their original delimiter classification."""

import pytest

from tests.test_speech_scope_regressions import all_splits


@pytest.mark.parametrize("links", [
    "[a.com](https://a.com) [b.com](https://b.com)",
    "https://a.com https://b.com",
    "[a.com](https://a.com) https://b.com",
])
@pytest.mark.parametrize("gap", [" ", "\t", "\n", " \t "])
@pytest.mark.parametrize("source,expected", [
    ("The switch is ***off*** {links} ***now***.", "The switch is off now."),
    ("**_first_** {links} **_second_**.", "first second."),
    ("***The switch is ***off*** {links} ***now***, I think.***", "The switch is off now, I think."),
    ("The switch is **off** {links} **now**.", "The switch is off now."),
])
def test_consecutive_removals_preserve_word_boundaries(links, gap, source, expected):
    all_splits(source.format(links=links.replace(" ", gap)), expected)


@pytest.mark.parametrize("source,expected", [
    ("The switch is ***off*** [a.com](https://a.com) ***now***.", "The switch is off now."),
    ("2**3 https://a.com https://b.com next", "2**3 next"),
    (r"Keep \*literal\* https://a.com https://b.com next", "Keep *literal* next"),
    ("`https://a.com https://b.com` next", "https://a.com https://b.com next"),
])
def test_single_link_and_literal_controls(source, expected):
    all_splits(source, expected)


@pytest.mark.parametrize("wrapper,closer", [("**", "**"), ("**_", "_**"), ("***", "***")])
@pytest.mark.parametrize("links", ["[a.com](https://a.com)", "[a.com](https://a.com) [b.com](https://b.com)", "https://a.com"])
def test_empty_emphasized_citation_wrappers(wrapper, closer, links):
    all_splits(f"Before {wrapper}{links}{closer} after.", "Before after.")
