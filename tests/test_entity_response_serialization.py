"""Coverage for Responses API native-item serialization helpers."""

import pytest

from custom_components.extended_openai_conversation_responses.entity import (
    _serialize_response_item,
)


def test_serialize_response_item_prefers_model_dump_and_filters_reasoning() -> None:
    """SDK models use model_dump and reasoning keeps only replay-safe fields."""

    class ModelDumpItem:
        def model_dump(self, *, exclude_none: bool) -> dict[str, object]:
            assert exclude_none is True
            return {
                "type": "reasoning",
                "id": "reasoning-1",
                "summary": [{"type": "summary_text", "text": "thinking"}],
                "encrypted_content": None,
                "status": "completed",
                "provider_extra": "discard-me",
            }

    assert _serialize_response_item(ModelDumpItem()) == {
        "type": "reasoning",
        "id": "reasoning-1",
        "summary": [{"type": "summary_text", "text": "thinking"}],
    }


def test_serialize_response_item_accepts_to_dict_and_plain_dict() -> None:
    """Older SDK objects and already-serialized dictionaries remain supported."""

    class ToDictItem:
        def to_dict(self) -> dict[str, object]:
            return {"type": "web_search_call", "id": "search-1", "status": "completed"}

    sdk_item = _serialize_response_item(ToDictItem())
    original = {"type": "message", "id": "message-1", "role": "assistant"}
    serialized = _serialize_response_item(original)

    assert sdk_item == {
        "type": "web_search_call",
        "id": "search-1",
        "status": "completed",
    }
    assert serialized == original
    assert serialized is not original


def test_serialize_response_item_rejects_unknown_object() -> None:
    """Unexpected provider objects fail explicitly instead of being silently coerced."""
    with pytest.raises(TypeError, match="Unsupported Responses item type"):
        _serialize_response_item(object())
