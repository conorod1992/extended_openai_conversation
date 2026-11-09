"""Frontend rename and backend dependency checks agree on arbitrary payload keys."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from custom_components.extended_openai_conversation_responses import (
    request_rules as rules,
)
from tests.test_request_rules import local_rule

ROOT = Path(__file__).resolve().parents[1]


def renamed_actions_fixture(key, payload_field):
    """Shared producer/consumer contract, checked against source and shipped JS."""
    return json.loads(
        (ROOT / "tests/fixtures/request_rule_alias_actions.json").read_text()
    )[f"{payload_field}/{key}"]


def rename_with_frontend(value, *, shipped=False):
    source = (
        ROOT
        / "custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-impl.js"
    )
    if shipped:
        matches = list(
            (source.parent / "dist/assets").glob("request-rules-ui-impl-*.js")
        )
        assert len(matches) == 1
        source = matches[0]
    script = f"""import {{renameResultReferenceMap}} from {json.dumps(source.as_uri())};
let text=""; for await(const chunk of process.stdin) text+=chunk;
process.stdout.write(JSON.stringify(renameResultReferenceMap(JSON.parse(text),new Map([["lookup","updated"]]))));"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        input=json.dumps(value),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
        cwd=ROOT,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("key", ["result_alias", "step_id"])
@pytest.mark.parametrize("shipped", [False, True])
@pytest.mark.parametrize("payload_field", ["data", "data_template"])
def test_renamed_references_under_metadata_named_argument_keys_validate(
    key, shipped, payload_field
):
    rule = local_rule()
    rule["action"]["actions"] = [
        {
            "action": "extended_openai_conversation_responses.call_function",
            "data": {
                "function": "produce",
                "arguments": {},
                "result_alias": "updated",
                "step_id": "1" * 32,
            },
        },
        {
            "action": "extended_openai_conversation_responses.call_function",
            payload_field: {
                "function": "consume",
                "arguments": {key: "{lookup.value}", "ordinary_key": "{lookup.value}"},
            },
        },
    ]
    stale = deepcopy(rule)
    stale["action"]["actions"][1][payload_field]["arguments"].pop("ordinary_key")
    with pytest.raises(ValueError, match="earlier step"):
        rules.validate_rule(stale)
    rule["action"]["actions"] = rename_with_frontend(
        rule["action"]["actions"], shipped=shipped
    )
    assert rule["action"]["actions"] == renamed_actions_fixture(key, payload_field)
    checked = rules.validate_rule(rule)
    assert checked["action"]["actions"][0]["data"]["result_alias"] == "updated"
    assert checked["action"]["actions"][1][payload_field]["arguments"] == {
        key: "{updated.value}",
        "ordinary_key": "{updated.value}",
    }
