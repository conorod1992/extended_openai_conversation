"""Closed built-in choices survive both public export/import contracts."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_ui,
    transfer,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONTEXT_TRUNCATE_STRATEGIES,
    SERVICE_TIER_OPTIONS,
)
from custom_components.extended_openai_conversation_responses.model_catalog import (
    REASONING_EFFORT_VALUES,
)
from custom_components.extended_openai_conversation_responses.request import (
    build_provider_request_snapshot,
)
from custom_components.extended_openai_conversation_responses.sharing import (
    SHARING_VERSION,
    project_configuration,
)
from tests.test_transfer import _portable_document

BUILTINS = (
    [("context_truncate_strategy", item["key"]) for item in CONTEXT_TRUNCATE_STRATEGIES]
    + [("reasoning_effort", value) for value in sorted(REASONING_EFFORT_VALUES)]
    + [("service_tier", value) for value in SERVICE_TIER_OPTIONS]
)


@pytest.mark.parametrize("field,value", BUILTINS)
@pytest.mark.parametrize("kind", ["configuration", "setup"])
@pytest.mark.parametrize("reviewed", [False, True])
def test_supported_public_choice_round_trip(hass, field, value, kind, reviewed):
    model = "gpt-5" if value == "minimal" else "gpt-5.6"
    config = {"chat_model": model, "api_mode": "responses", field: value}
    if kind == "configuration":
        artifact = management_ui._export_agent(
            SimpleNamespace(data=config), reviewed=reviewed
        )
        imported = management_ui._parse_import_document(artifact)["config"]
    else:
        document = _portable_document(mode="setup")
        document["version"] = SHARING_VERSION
        document["sections"]["configuration"] = deepcopy(config)
        artifact = transfer.finalize_setup_export(document, reviewed=reviewed)[
            "document"
        ]
        imported = transfer.inspect_transfer(artifact, "new-assistant").config
    assert imported[field] == value
    assert not any(item["field"] == field for item in artifact["security"]["omitted"])
    if field in {"reasoning_effort", "service_tier"}:
        before = build_provider_request_snapshot(config, {}, tools_required=False)
        after = build_provider_request_snapshot(imported, {}, tools_required=False)
        key = "reasoning" if field == "reasoning_effort" else "service_tier"
        assert before.api_kwargs.get(key) == after.api_kwargs.get(key)


@pytest.mark.parametrize(
    "field",
    ["context_truncate_strategy", "reasoning_effort", "service_tier", "future_setting"],
)
def test_unclassified_values_remain_private(field):
    projected, security = project_configuration({field: "unclassified-value"})
    assert field not in projected
    assert security["omitted"]
