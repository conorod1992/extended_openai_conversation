"""Independent wire oracle; never consult the production request builder."""

import json


def validate_request(
    path,
    body,
    *,
    supports_tools=True,
    function_apis=("responses", "chat_completions"),
    attachments=(),
):
    """Reject unsupported tools, incomplete exchanges and missing file bytes."""
    responses = path.endswith("/responses")
    assert responses or path.endswith("/chat/completions"), path
    assert supports_tools or not body.get("tools"), "unsupported Function Tools"
    if body.get("tools"):
        assert ("responses" if responses else "chat_completions") in function_apis, (
            "Functions unsupported on selected API"
        )
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
