"""Auto-recall padding weights must not alter explicit search or safety gates."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from custom_components.extended_openai_conversation_responses.const import (
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_ENABLED,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
    _ACTIVE_TURN,
    _ACTIVE_GUEST_POLICY,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
    _automatic_query_weights,
    _bm25_score,
    _token_list,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope


class _Storage:
    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


_VERBOSE = (
    "Hey, I was hoping you might kindly please tell me, if you happen "
    "to know or recall, perhaps just remind me which school Emma attends; "
    "I was curious and wondering about it, thanks."
)


async def _populated():
    memory = PersistentMemory(_Storage())
    await memory.async_initialize()
    created = await memory.async_add(
        "alice", "Emma attends St Brigid's National School.", "education", "explicit"
    )
    return memory, created["memory"]["memory_id"]


async def test_automatic_recall_recovers_verbose_question_without_changing_tool_search():
    memory, target = await _populated()
    # Reproduce the original lexical-threshold miss on the exact same corpus.
    assert await memory.async_search("alice", _VERBOSE) == []
    recalled = await memory.async_search(
        "alice", _VERBOSE, automatic_recall=True, allow_fuzzy_only=False
    )
    assert [record.memory_id for record in recalled] == [target]
    assert await memory.async_search(
        "alice", _VERBOSE, allow_fuzzy_only=False
    ) == []


async def test_automatic_recall_retains_short_and_padding_only_search_contracts():
    memory, _ = await _populated()
    for query in ("Emma school", "please tell me", "hi perhaps just kindly please"):
        normal = await memory.async_search("alice", query)
        automatic = await memory.async_search("alice", query, automatic_recall=True)
        assert automatic == normal
        assert _automatic_query_weights(_token_list(query)) is None


async def test_automatic_recall_keeps_controls_negations_names_identifiers_numbers():
    terms = _token_list(
        "Please could you tell me about Emma not disabling device a1_b2 in room 204?"
    )
    weights = _automatic_query_weights(terms)
    assert weights is not None
    for term in ("could", "emma", "not", "disabl", "device", "a1_b2", "204"):
        assert weights[term] == 1.0
    assert weights["please"] < 1.0
    assert weights["you"] < 1.0
    # Control/negation tokens remain full weight even in long polite requests.
    assert weights["not"] == 1.0
    framed = _automatic_query_weights(_token_list(_VERBOSE))
    assert framed is not None
    for control in ("might", "if", "or"):
        assert framed[control] == 1.0
    assert framed["was"] == framed["hope"] == framed["happen"] == 0.03


def test_automatic_query_bm25_weights_numerator_and_denominator_together():
    query = _token_list(_VERBOSE)
    weights = _automatic_query_weights(query)
    assert weights is not None
    tf = ("emma", "attend", "school")
    df = dict.fromkeys(query, 1)
    old = _bm25_score(query, tf, df, 1, float(len(tf)))
    new = _bm25_score(
        query, tf, df, 1, float(len(tf)), query_weights=weights
    )
    assert 0 < old < new < 1
    compound = _automatic_query_weights(
        _token_list("Hey please tell me which school Emma attends in carlow-city?")
    )
    assert compound is not None
    assert compound["carlow-city"] < 1
    assert compound["carlow"] < 1


async def test_first_turn_bundle_retrieval_and_followup_keep_original_selection():
    memory, target = await _populated()
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity._memory = memory
    entity._continuity = ConversationContinuity("agent")
    entity.subentry = SimpleNamespace(
        data={CONF_MEMORY_ENABLED: True, CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 3}
    )
    context = SimpleNamespace(context=SimpleNamespace(user_id="alice"))
    scope_token = _ACTIVE_TURN.set(
        replace(_ACTIVE_TURN.get(), scope=user_scope("alice", source="test"))
    )
    session_token = _ACTIVE_TURN.set(
        replace(_ACTIVE_TURN.get(), memory_session=("verbose:auto", 5))
    )
    policy_token = _ACTIVE_GUEST_POLICY.set(GuestCapabilityPolicy.unrestricted())
    try:
        first = await entity._async_select_memories(context, _VERBOSE)
        assert [record.memory_id for record in first] == [target]
        followup = await entity._async_select_memories(context, "Unrelated question")
        assert [record.memory_id for record in followup] == [target]
    finally:
        _ACTIVE_GUEST_POLICY.reset(policy_token)
        _ACTIVE_TURN.reset(session_token)
        _ACTIVE_TURN.reset(scope_token)


async def test_verbose_irrelevant_query_is_not_admitted_as_related():
    memory, _ = await _populated()
    unrelated = _VERBOSE.replace("school Emma attends", "favourite colour Oliver prefers")
    assert await memory.async_search(
        "alice", unrelated, automatic_recall=True, allow_fuzzy_only=False
    ) == []
    assert await memory.async_search(
        "other-user", _VERBOSE, automatic_recall=True, allow_fuzzy_only=False
    ) == []


async def test_automatic_query_corpus_improves_recall_without_new_single_anchor_false_positives():
    memory, target = await _populated()
    unrelated = (
        "Emma enjoys swimming in the mornings.",
        "Emma drives a blue electric car.",
        "Oliver attends St Patrick's Secondary School.",
        "School holiday dates are announced in September.",
        "The office is closed on Wednesdays.",
        "The family dog is called Oscar.",
        "St Brigid's National School is in Carlow.",
        "The home lights switch on at sunset.",
    )
    for text in unrelated:
        await memory.async_add("alice", text, "personal", "explicit")
    old = await memory.async_search(
        "alice", _VERBOSE, limit=50, allow_fuzzy_only=False
    )
    new = await memory.async_search(
        "alice", _VERBOSE, limit=50, allow_fuzzy_only=False, automatic_recall=True
    )
    baseline = {item.memory_id for item in old}
    weighted = {item.memory_id for item in new}
    assert target in weighted
    # Reweighting cannot newly admit a memory on Emma's name alone or on
    # "school" alone, even when those words have high IDF in a larger corpus.
    assert weighted - baseline <= {target}
    # Existing explicit-search results must remain eligible; an empty baseline
    # is valid and is exactly the verbose-query defect we are fixing.
    assert baseline <= weighted
