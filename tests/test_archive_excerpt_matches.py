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
    ids=["substring", "casefold", "short", "stem", "compound", "cjk", "phrase"],
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
