"""Plural wording applies equally to catalog terms, requests and saved rules."""

import pytest

from custom_components.extended_openai_conversation_responses.request_rule_match_preview import (
    async_request_rule_match_preview,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    DEFAULT_WORDING_GROUPS,
    normalize_text,
    validate_wording_groups,
)
from tests.test_request_rule_matching_contracts import manager, rule


@pytest.mark.parametrize("word_forms", [False, True])
@pytest.mark.parametrize("alternatives", [False, True])
@pytest.mark.parametrize(
    "canonical,alternative",
    [("tv", "television"), ("light", "lamp"), ("lights", "lamps")],
)
async def test_plural_catalog_matches_saved_phrases_and_preview(
    hass, word_forms, alternatives, canonical, alternative
):
    groups = [{"canonical": canonical, "alternatives": [alternative]}]
    # Exercise plurals on each side, even when catalog terms themselves are plural.
    singular_canonical = canonical.removesuffix("s")
    singular_alternative = alternative.removesuffix("s")
    settings = {
        "word_forms": word_forms,
        "wording_alternatives": alternatives,
        "fuzzy": False,
    }
    plural = singular_alternative + "s"
    for saved, request in [(singular_canonical, plural), (plural, singular_canonical)]:
        rules = await manager(rule("command", saved, matching=settings))
        await rules.async_set_wording_groups(groups)
        matched = rules.match(request)
        assert (matched is not None) == (word_forms and alternatives)
        preview = await async_request_rule_match_preview(hass, rules, request)
        assert preview["matched"] == (matched is not None)
        assert rules.match(request + " unrelated") is None


def test_default_television_plural_and_independent_flags():
    assert (
        normalize_text(
            "Televisions", {"word_forms": True, "wording_alternatives": True}
        )
        == "tv"
    )
    assert normalize_text("Televisions", {"word_forms": True}) == "television"
    assert normalize_text("television", {"wording_alternatives": True}) == "tv"
    assert (
        normalize_text("Televisions", {"wording_alternatives": True}) == "televisions"
    )
    assert (
        normalize_text("tv shows", {"word_forms": True, "wording_alternatives": True})
        == "tv show"
    )


def test_singularization_collision_does_not_choose_a_mapping():
    groups = validate_wording_groups(
        [
            {"canonical": "light", "alternatives": ["lamp"]},
            {"canonical": "device", "alternatives": ["lamps"]},
        ]
    )
    settings = {"word_forms": True, "wording_alternatives": True}
    assert normalize_text("lamps", settings, groups) == "lamp"
    assert normalize_text("lamps", {"wording_alternatives": True}, groups) == "device"


def test_plural_self_mapping_and_bounded_rewrite_failure():
    settings = {"word_forms": True, "wording_alternatives": True}
    groups = validate_wording_groups([{"canonical": "lamps", "alternatives": ["lamp"]}])
    assert normalize_text("lamps", settings, groups) == "lamp"
    expanding = [{"canonical": "lamps lamps", "alternatives": ["lamp"]}]
    assert normalize_text("lamps", settings, expanding) == "lamp"
    assert (
        normalize_text("3.14 news species", settings, DEFAULT_WORDING_GROUPS)
        == "3.14 news species"
    )
