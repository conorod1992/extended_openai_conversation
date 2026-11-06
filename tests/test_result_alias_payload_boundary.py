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
def test_renamed_references_under_metadata_named_argument_keys_validate(key, shipped):
    rule = local_rule()
    rule["action"]["actions"] = [
        {
            "action": "extended_openai_conversation_responses.call_function",
            "data": {"function": "produce", "arguments": {}, "result_alias": "updated"},
        },
        {
            "action": "extended_openai_conversation_responses.call_function",
            "data": {
                "function": "consume",
                "arguments": {key: "{lookup.value}", "ordinary_key": "{lookup.value}"},
            },
        },
    ]
    stale = deepcopy(rule)
    stale["action"]["actions"][1]["data"]["arguments"].pop("ordinary_key")
    with pytest.raises(ValueError, match="earlier step"):
        rules.validate_rule(stale)
    rule["action"]["actions"] = rename_with_frontend(
        rule["action"]["actions"], shipped=shipped
    )
    checked = rules.validate_rule(rule)
    assert checked["action"]["actions"][0]["data"]["result_alias"] == "updated"
    assert checked["action"]["actions"][1]["data"]["arguments"] == {
        key: "{updated.value}",
        "ordinary_key": "{updated.value}",
    }
