"""User-facing conversation continuity and immediate follow-up option contracts."""

from custom_components.extended_openai_conversation_responses.const import (
    CONTINUE_CONVERSATION_ALWAYS,
    CONTINUE_CONVERSATION_CONDITIONAL,
    CONTINUE_CONVERSATION_DEFAULT,
    CONVERSATION_CONTINUITY_DEVICE,
    CONVERSATION_CONTINUITY_HA_DEFAULT,
    CONVERSATION_CONTINUITY_USER,
)
from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    _resolve_continue_conversation,
)
from custom_components.extended_openai_conversation_responses.scope import user_scope


def test_conversation_continuity_options_select_the_expected_owner() -> None:
    """Each UI continuity choice maps to a distinct, inspectable runtime owner."""
    alice_kitchen = user_scope("alice", source="test", device_id="kitchen")
    alice_office = user_scope("alice", source="test", device_id="office")
    bob_kitchen = user_scope("bob", source="test", device_id="kitchen")

    assert ConversationContinuity.identity_key(
        CONVERSATION_CONTINUITY_HA_DEFAULT, alice_kitchen, "kitchen"
    )[0] is None

    assert ConversationContinuity.identity_key(
        CONVERSATION_CONTINUITY_DEVICE, alice_kitchen, "kitchen"
    )[0] == "user:alice:device:kitchen"
    assert ConversationContinuity.identity_key(
        CONVERSATION_CONTINUITY_DEVICE, alice_office, "office"
    )[0] == "user:alice:device:office"
    assert ConversationContinuity.identity_key(
        CONVERSATION_CONTINUITY_DEVICE, bob_kitchen, "kitchen"
    )[0] == "user:bob:device:kitchen"

    assert ConversationContinuity.identity_key(
        CONVERSATION_CONTINUITY_USER, alice_kitchen, "kitchen"
    )[0] == "user:alice"
    assert ConversationContinuity.identity_key(
        CONVERSATION_CONTINUITY_USER, alice_office, "office"
    )[0] == "user:alice"
    assert ConversationContinuity.identity_key(
        CONVERSATION_CONTINUITY_USER, bob_kitchen, "kitchen"
    )[0] == "user:bob"


def test_listen_for_follow_up_options_have_complete_runtime_truth_table() -> None:
    """HA default, Always and Conditional each control the final Assist flag."""
    for ha_default in (False, True):
        assert (
            _resolve_continue_conversation(
                CONTINUE_CONVERSATION_DEFAULT, ha_default, None
            )
            is ha_default
        )
        assert (
            _resolve_continue_conversation(
                CONTINUE_CONVERSATION_ALWAYS, ha_default, None
            )
            is True
        )
        assert (
            _resolve_continue_conversation(
                CONTINUE_CONVERSATION_CONDITIONAL, ha_default, False
            )
            is False
        )
        assert (
            _resolve_continue_conversation(
                CONTINUE_CONVERSATION_CONDITIONAL, ha_default, True
            )
            is True
        )
        assert (
            _resolve_continue_conversation(
                CONTINUE_CONVERSATION_CONDITIONAL, ha_default, None
            )
            is False
        )
