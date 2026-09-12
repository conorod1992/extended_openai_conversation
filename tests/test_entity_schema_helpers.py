"""Focused coverage for Responses entity normalization and schema helpers."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.entity import (
    _adjust_schema,
    _make_schema_nullable,
    _normalize_function_result,
    _normalize_url_citation,
    _schema_explicitly_allows_null,
    _shorten_tool_call_id,
)


def test_shorten_tool_call_id_is_deterministic_alphanumeric() -> None:
    """The provider compatibility ID is stable and has Mistral's required shape."""
    shortened = _shorten_tool_call_id("call_with-provider.characters/123")

    assert shortened == _shorten_tool_call_id("call_with-provider.characters/123")
    assert len(shortened) == 9
    assert shortened.isalnum()


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "null"}, True),
        ({"type": ["string", "null"]}, True),
        ({"anyOf": [{"type": "string"}, {"type": "null"}]}, True),
        ({"oneOf": [{"type": "integer"}, {"type": ["number", "null"]}]}, True),
        ({"allOf": [{"type": "null"}, {"type": ["string", "null"]}]}, True),
        ({"allOf": [{"type": "null"}, {"type": "string"}]}, False),
        ({"allOf": []}, False),
        ({"anyOf": ["not-a-schema", {"type": "string"}]}, False),
        ({"type": "string"}, False),
    ],
)
def test_schema_explicitly_allows_null(
    schema: dict[str, object], expected: bool
) -> None:
    """Null detection handles direct, union, and composition schemas."""
    assert _schema_explicitly_allows_null(schema) is expected


@pytest.mark.parametrize(
    ("schema", "expected"),
    [
        ({"type": "string"}, {"type": ["string", "null"]}),
        ({"type": ["string", "number"]}, {"type": ["string", "number", "null"]}),
        (
            {"description": "value without a direct type"},
            {
                "anyOf": [
                    {"description": "value without a direct type"},
                    {"type": "null"},
                ]
            },
        ),
        (
            {"oneOf": [{"type": "string"}, {"type": "integer"}]},
            {
                "anyOf": [
                    {"oneOf": [{"type": "string"}, {"type": "integer"}]},
                    {"type": "null"},
                ]
            },
        ),
    ],
)
def test_make_schema_nullable(
    schema: dict[str, object], expected: dict[str, object]
) -> None:
    """Non-nullable schemas gain null support without losing their constraints."""
    _make_schema_nullable(schema)

    assert schema == expected


def test_make_schema_nullable_leaves_existing_nullable_schema_unchanged() -> None:
    """Already-nullable schemas are not needlessly rewritten."""
    schema = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    original = {"anyOf": [{"type": "string"}, {"type": "null"}]}

    _make_schema_nullable(schema)

    assert schema == original


def test_adjust_schema_recurses_and_makes_only_optional_properties_nullable() -> None:
    """Structured-output normalization preserves required fields and fixes nested ones."""
    schema = {
        "type": "object",
        "required": ["required_name"],
        "properties": {
            "required_name": {"type": "string"},
            "optional_count": {"type": "integer"},
            "nested": {
                "type": "object",
                "properties": {"enabled": {"type": "boolean"}},
            },
            "ignored": "not-a-schema",
        },
    }

    _adjust_schema(schema)

    assert schema["strict"] is True
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["required_name", "optional_count", "nested"]
    properties = schema["properties"]
    assert properties["required_name"] == {"type": "string"}
    assert properties["optional_count"] == {"type": ["integer", "null"]}
    assert properties["nested"] == {
        "type": ["object", "null"],
        "strict": True,
        "additionalProperties": False,
        "properties": {"enabled": {"type": ["boolean", "null"]}},
        "required": ["enabled"],
    }
    assert properties["ignored"] == "not-a-schema"


def test_adjust_schema_handles_compositions_arrays_and_explicit_settings() -> None:
    """Normalization reaches composed/array schemas without overriding explicit policy."""
    schema = {
        "anyOf": [
            {
                "type": "array",
                "items": {
                    "type": "object",
                    "strict": False,
                    "additionalProperties": True,
                    "properties": {"label": {"type": "string"}},
                },
            },
            {"type": "null"},
            "not-a-schema",
        ]
    }

    _adjust_schema(schema)

    item_schema = schema["anyOf"][0]["items"]
    assert item_schema["strict"] is False
    assert item_schema["additionalProperties"] is True
    assert item_schema["required"] == ["label"]
    assert item_schema["properties"]["label"] == {"type": ["string", "null"]}


def test_normalize_url_citation_accepts_mapping_and_sdk_style_object() -> None:
    """Both mapping and attribute-based SDK annotations normalize identically."""
    expected = {
        "type": "url_citation",
        "start_index": 2,
        "end_index": 8,
        "title": "Example",
        "url": "https://example.com",
    }
    mapping = {
        "type": "url_citation",
        "start_index": 2,
        "end_index": 8,
        "title": "Example",
        "url": "https://example.com",
    }
    annotation = SimpleNamespace(**mapping)

    assert _normalize_url_citation(mapping) == expected
    assert _normalize_url_citation(annotation) == expected


@pytest.mark.parametrize(
    "annotation",
    [
        {"type": "file_citation", "start_index": 0, "end_index": 1},
        {"type": "url_citation", "start_index": "0", "end_index": 1},
        {"type": "url_citation", "start_index": 0, "end_index": None},
    ],
)
def test_normalize_url_citation_rejects_unsupported_annotations(
    annotation: dict[str, object],
) -> None:
    """Only documented URL citations with integer offsets are accepted."""
    assert _normalize_url_citation(annotation) is None


def test_normalize_function_result_preserves_json_and_stringifies_fallback() -> None:
    """Serializable results stay structured while unsupported values degrade safely."""

    class UnsupportedResult:
        def __str__(self) -> str:
            return "unsupported-result"

    structured = {"ok": True, "values": [1, 2, 3]}

    assert _normalize_function_result(structured) is structured
    assert _normalize_function_result(UnsupportedResult()) == "unsupported-result"
