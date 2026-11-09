"""Public Archive searches return the fact selected by lexical ranking."""

import pytest

from custom_components.extended_openai_conversation_responses.management_history_queries import (
    archive_search_page,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope
from tests.test_conversation_archive import _archive

CASES = [
    (
        "The carpet in the bedroom is blue. "
        + "The walls are white. " * 40
        + "The car needs an oil change next Tuesday.",
        "car",
        "oil change next Tuesday",
    ),
    (
        "Die Straße ist groß. " * 100 + "Der Ersatzschlüssel liegt im roten Schrank.",
        "Ersatzschlüssel",
        "Ersatzschlüssel liegt im roten Schrank",
    ),
    ("The keys are in the blue drawer.", "keys", "blue drawer"),
    (
        "The changeless wall is blue. "
        + "The walls are white. " * 40
        + "The oil change is due Tuesday.",
        "changes",
        "oil change",
    ),
    (
        "carpet " + "distant-" * 100 + "car-service is due Tuesday.",
        "car",
        "car-service is due Tuesday",
    ),
    (
        "厨房" + "沿着道路" * 200 + "红色钥匙在蓝色抽屉。",
        "红色钥匙",
        "红色钥匙在蓝色抽屉",
    ),
    ("We watched a film last night.", "watch", "watched a film"),
    ("I was running along the river.", "run", "running along the river"),
    ("I enjoy baking bread.", "bake", "baking bread"),
    ("I cancelled the membership.", "cancel", "cancelled the membership"),
    ("I added the reminder.", "add", "added the reminder"),
    ("I liked the movie.", "like", "liked the movie"),
    ("I collect movies.", "movie", "collect movies"),
    (
        "Carpet first. "
        + "The walls are white. " * 40
        + "The CAR: needs an oil change.",
        "car needs",
        "CAR: needs an oil change",
    ),
]


@pytest.mark.parametrize("path", ["model", "management"])
@pytest.mark.parametrize(
    "text,query,fact",
    CASES,
    ids=[
        "substring", "casefold", "short", "stem", "compound", "cjk",
        "past-tense", "doubled-ing", "silent-e", "doubled-l", "short-root",
        "liked", "plural-ie", "phrase",
    ],
)
async def test_search_excerpt_keeps_matching_fact(path, text, query, fact):
    archive = await _archive()
    session = await archive.async_begin_session(
        "one",
        user_scope("alice", source="authenticated_user"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    turn = await archive.async_record_turn(
        session.session_id,
        run_id="run",
        user_text="Remember this",
        assistant_text=text,
        successful=True,
    )
    if path == "model":
        found = await archive.async_search("user:alice", query)
    else:
        found = await archive_search_page(archive, "user:alice", query)
    assert len(found["results"]) == 1
    result = found["results"][0]
    assert result["session_id"] == session.session_id
    assert result["turn_id"] == turn.turn_id
    assert fact in result["excerpt"]
    assert len(result["excerpt"]) <= 502


@pytest.mark.parametrize(
    ("stored", "query"),
    [
        ("I hoped for a new job.", "hop"),
        ("I hopped on the bus.", "hope"),
        ("I added a reminder.", "ad"),
        ("I called a friend.", "cal"),
        ("Our installation completed.", "installment"),
    ],
)
async def test_archive_shared_stemming_does_not_match_distinct_words(
    stored: str, query: str
) -> None:
    archive = await _archive()
    session = await archive.async_begin_session(
        "one",
        user_scope("alice", source="authenticated_user"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    await archive.async_record_turn(
        session.session_id,
        run_id="run",
        user_text=stored,
        assistant_text="Acknowledged.",
        successful=True,
    )
    assert not (await archive.async_search("user:alice", query))["results"]
    assert not (await archive_search_page(archive, "user:alice", query))["results"]


@pytest.mark.parametrize(
    ("form", "expected"),
    [
        ("going", "go"),
        ("being", "be"),
        ("doing", "do"),
    ],
)
def test_archive_keeps_legacy_short_gerund_query_stems(
    form: str, expected: str
) -> None:
    from custom_components.extended_openai_conversation_responses import (
        conversation_archive as archive,
    )

    assert archive._stem(form) == expected


async def test_archive_literal_phrase_still_outscores_new_inflection_match() -> None:
    archive = await _archive()
    session = await archive.async_begin_session(
        "one",
        user_scope("alice", source="authenticated_user"),
        "conversation",
        archive_enabled=True,
        shared_archive_enabled=False,
        inactivity_minutes=30,
    )
    inflected = await archive.async_record_turn(
        session.session_id,
        run_id="first",
        user_text="We watched together.",
        assistant_text="That sounds good.",
        successful=True,
    )
    literal = await archive.async_record_turn(
        session.session_id,
        run_id="second",
        user_text="I want to watch together.",
        assistant_text="Enjoy.",
        successful=True,
    )
    for search in (
        archive.async_search,
        lambda scope, query: archive_search_page(archive, scope, query),
    ):
        result = await search("user:alice", "watch")
        assert [item["turn_id"] for item in result["results"]] == [
            literal.turn_id, inflected.turn_id
        ]


