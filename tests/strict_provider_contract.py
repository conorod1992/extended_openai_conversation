"""Independent wire oracle; never consult the production request builder."""

import json
import re


def validate_request(
    path,
    body,
    *,
    supports_tools=True,
    function_apis=("responses", "chat_completions"),
    attachments=(),
    provider="openai",
):
    """Reject unsupported tools, incomplete exchanges and missing file bytes."""
    responses = path.endswith("/responses")
    assert responses or path.endswith("/chat/completions"), path
    assert supports_tools or not body.get("tools"), "unsupported Function Tools"
    if body.get("tools"):
        assert ("responses" if responses else "chat_completions") in function_apis, (
            "Functions unsupported on selected API"
        )
    # Provider/API contracts are external constants, independent of production.
    forbidden = (
        {"messages", "response_format", "max_completion_tokens"}
        if responses
        else {"input", "text", "max_output_tokens"}
    )
    assert not forbidden.intersection(body), "parameters belong to another API"
    if provider == "azure" and not responses:
        assert len(body.get("tools", [])) <= 128, "Azure tool budget exceeded"
    output = (
        body.get("text", {}).get("format", {})
        if responses
        else body.get("response_format", {}).get("json_schema", {})
    )
    if output:
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", output.get("name", "")), (
            "invalid schema name"
        )
        if output.get("strict"):
            validate_strict_schema(output["schema"])
    for tool in body.get("tools", []):
        function = tool if responses else tool.get("function", {})
        if function.get("strict"):
            validate_strict_schema(function["parameters"])
    items = body["input" if responses else "messages"]
    pending, seen = set(), set()
    for item in items:
        kind = item.get("type") if responses else item.get("role")
        if responses and kind == "function_call":
            calls = [item["call_id"]]
        elif not responses and kind == "assistant":
            assert (
                "content" in item or item.get("tool_calls") or item.get("function_call")
            ), "assistant has neither content nor calls"
            assert not pending, "assistant interrupted unresolved exchange"
            calls = [call["id"] for call in item.get("tool_calls", [])]
        else:
            calls = []
        if calls:
            for call_id in calls:
                assert call_id not in seen, "duplicate tool call ID"
                pending.add(call_id)
                seen.add(call_id)
        elif kind in ("function_call_output", "tool"):
            call_id = item["call_id" if responses else "tool_call_id"]
            assert call_id in pending, "orphan or duplicate tool result"
            pending.remove(call_id)
        elif item.get("role") in ("user", "assistant"):
            assert not pending, "message interrupted unresolved exchange"
    assert not pending, "tool call has no result"
    serialized = json.dumps(items)
    for attachment in attachments:
        assert attachment in serialized, "historical attachment missing"


def validate_strict_schema(schema, *, root=True):
    """Enforce provider schema rules independently, visiting only schema nodes."""
    assert isinstance(schema, dict), "schema must be an object"
    if root:
        assert schema.get("type") == "object" and "anyOf" not in schema, (
            "strict root must be an object without anyOf"
        )
    assert "allOf" not in schema, "allOf is unsupported in strict schemas"
    kind = schema.get("type")
    if kind == "object" or isinstance(kind, list) and "object" in kind:
        assert schema.get("additionalProperties") is False, "strict object must be closed"
        assert set(schema.get("required", [])) == set(schema.get("properties", {})), (
            "strict object must require all properties"
        )
    for keyword in ("properties", "$defs", "definitions"):
        for child in schema.get(keyword, {}).values():
            validate_strict_schema(child, root=False)
    if isinstance(schema.get("items"), dict):
        validate_strict_schema(schema["items"], root=False)
    for child in schema.get("anyOf", []):
        validate_strict_schema(child, root=False)
