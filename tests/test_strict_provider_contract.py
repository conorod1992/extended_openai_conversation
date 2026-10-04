"""Prove the independent wire oracle rejects each invalid protocol shape."""

from copy import deepcopy

import pytest

from tests.strict_provider_contract import validate_request


def test_chat_oracle_rejects_role_only_assistant():
    with pytest.raises(AssertionError, match="neither content nor calls"):
        validate_request("/v1/chat/completions", {"messages": [{"role": "assistant"}]})


def _exchange(responses):
    if responses:
        return "/v1/responses", [
            {"type": "function_call", "call_id": "a"},
            {"type": "function_call", "call_id": "b"},
            {"type": "function_call_output", "call_id": "b"},
            {"type": "function_call_output", "call_id": "a"},
        ]
    return "/v1/chat/completions", [
        {"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {"role": "tool", "tool_call_id": "b"},
        {"role": "tool", "tool_call_id": "a"},
    ]


@pytest.mark.parametrize("responses", [False, True])
@pytest.mark.parametrize("fault", [None, "missing", "orphan", "duplicate", "interrupt"])
def test_exchange_oracle(responses, fault):
    path, items = _exchange(responses)
    items = deepcopy(items)
    if fault == "missing":
        items.pop()
    elif fault == "orphan":
        items[-1]["call_id" if responses else "tool_call_id"] = "unknown"
    elif fault == "duplicate":
        items.append(deepcopy(items[-1]))
    elif fault == "interrupt":
        items.insert(1, {"role": "user", "content": "follow-up"})
    body = {"input" if responses else "messages": items}
    if fault is None:
        validate_request(path, body)
    else:
        with pytest.raises(AssertionError):
            validate_request(path, body)


@pytest.mark.parametrize("responses", [False, True])
def test_text_only_capability_and_attachment_oracle(responses):
    path, _ = _exchange(responses)
    body = {
        "input" if responses else "messages": [
            {"role": "user", "content": "data:image/png;base64,Ynl0ZXM="}
        ]
    }
    validate_request(path, body, supports_tools=False, attachments=("Ynl0ZXM=",))
    with pytest.raises(AssertionError, match="attachment"):
        validate_request(path, body, attachments=("missing-bytes",))
    body["tools"] = [{"type": "function"}]
    with pytest.raises(AssertionError, match="unsupported"):
        validate_request(path, body, supports_tools=False)
    if responses:
        validate_request(path, body, function_apis=("responses",))
    else:
        with pytest.raises(AssertionError, match="selected API"):
            validate_request(path, body, function_apis=("responses",))
