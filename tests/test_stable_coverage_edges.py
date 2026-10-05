"""Compact coverage for catalogue, Rule Pack, and management boundary edges."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses import (
    management_loading_performance,
    model_catalog,
    request_rule_packs,
)
from custom_components.extended_openai_conversation_responses.request_rule_packs import (
    async_append_rule_pack,
    export_rule_pack,
    validate_rule_pack,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from tests.test_request_rules import MemoryStore, local_rule


def _valid_model() -> dict:
    return deepcopy(model_catalog.BUNDLED_CATALOG["models"][0])


@pytest.mark.parametrize(
    "mutation",
    [
        lambda model: model.__setitem__("status", "invalid"),
        lambda model: model["api"].__setitem__("responses", "yes"),
        lambda model: model.__setitem__(
            "auto_api",
            next(api for api, enabled in model["api"].items() if not enabled),
        )
        if any(not enabled for enabled in model["api"].values())
        else model["api"].__setitem__("responses", False),
        lambda model: model["function_calling"].__setitem__("preferred_api", "invalid"),
        lambda model: model["reasoning"].__setitem__(
            "supported", not bool(model["reasoning"]["efforts"])
        ),
        lambda model: model["reasoning"].__setitem__("openai_default", "impossible"),
        lambda model: model["limits"].__setitem__("context_tokens", 0),
        lambda model: model.__setitem__("streaming", "yes"),
    ],
)
def test_model_metadata_rejects_invalid_scalar_contracts(mutation) -> None:
    model = _valid_model()
    mutation(model)

    with pytest.raises(ValueError):
        model_catalog._validate_metadata(model, model_entry=True)


def test_model_metadata_rejects_invalid_tool_support_contract() -> None:
    model = _valid_model()
    api = next(iter(model["tools"]["function"]))
    model["tools"]["function"][api]["support"] = "sometimes"

    with pytest.raises(ValueError, match="tool support"):
        model_catalog._validate_metadata(model, model_entry=True)


def test_model_metadata_rejects_conditional_tool_without_condition() -> None:
    model = _valid_model()
    api = next(iter(model["tools"]["function"]))
    model["tools"]["function"][api] = {"support": "conditional"}

    with pytest.raises(ValueError, match="conditions"):
        model_catalog._validate_metadata(model, model_entry=True)


def test_model_metadata_rejects_tool_support_on_unavailable_api() -> None:
    model = _valid_model()
    # Tool rules cover the two request APIs, while the catalogue also tracks the
    # legacy completions API. Disable a request API and keep every surrounding
    # capability internally consistent so validation reaches the intended guard.
    unavailable = "chat_completions"
    model["api"][unavailable] = False
    model["reasoning"]["by_api"][unavailable]["efforts"] = []
    model["function_calling"][unavailable] = False
    if model["function_calling"]["preferred_api"] == unavailable:
        model["function_calling"]["preferred_api"] = "responses"
    model["tools"]["function"][unavailable] = {"support": "always"}
    model["tools"]["web_search"][unavailable] = {"support": "never"}

    with pytest.raises(ValueError, match="available API"):
        model_catalog._validate_metadata(model, model_entry=True)


def test_model_metadata_rejects_reasoning_effort_union_mismatch() -> None:
    model = _valid_model()
    efforts = model["reasoning"]["efforts"]
    if not efforts:
        pytest.skip("Bundled fixture has no reasoning efforts")
    removed = efforts[0]
    for api in model["reasoning"]["by_api"].values():
        api["efforts"] = [item for item in api["efforts"] if item != removed]

    with pytest.raises(ValueError, match="API union"):
        model_catalog._validate_metadata(model, model_entry=True)


@pytest.mark.asyncio
async def test_rule_pack_group_export_rejects_known_but_empty_group() -> None:
    source = RequestRules(
        MemoryStore(
            {
                "groups": [{"id": "unused", "name": "Unused"}],
                "rules": [local_rule("Only")],
            }
        )
    )
    await source.async_initialize()

    with pytest.raises(ValueError, match="Choose at least one"):
        export_rule_pack(source, "group", group_id="unused")


@pytest.mark.asyncio
async def test_rule_pack_export_applies_encoded_size_limit(monkeypatch) -> None:
    source = RequestRules(MemoryStore({"rules": [local_rule("Only")]}))
    await source.async_initialize()
    monkeypatch.setattr(request_rule_packs, "MAX_PACK_BYTES", 32)

    with pytest.raises(ValueError, match="2 MB safety limit"):
        export_rule_pack(source, "all")


@pytest.mark.asyncio
async def test_rule_pack_validation_rejects_rule_missing_required_field() -> None:
    source = RequestRules(MemoryStore({"rules": [local_rule("Only")]}))
    await source.async_initialize()
    pack = export_rule_pack(source, "all")
    pack["rules"][0].pop("conditions")

    with pytest.raises(ValueError, match="missing required fields"):
        validate_rule_pack(pack)


@pytest.mark.asyncio
async def test_rule_pack_import_reuses_group_name_case_insensitively() -> None:
    source_rule = local_rule("Imported")
    source_rule["group_id"] = "source-group"
    source = RequestRules(
        MemoryStore(
            {
                "groups": [{"id": "source-group", "name": "Lighting"}],
                "rules": [source_rule],
            }
        )
    )
    await source.async_initialize()
    prepared = validate_rule_pack(export_rule_pack(source, "all"))

    target = RequestRules(
        MemoryStore(
            {
                "groups": [{"id": "target-group", "name": "lighting"}],
                "rules": [local_rule("Existing")],
            }
        )
    )
    await target.async_initialize()

    result = await async_append_rule_pack(
        target,
        prepared,
        expected_revision=target.revision(),
    )

    assert result["groups"] == [{"id": "target-group", "name": "lighting"}]
    assert result["rules"][0]["group_id"] == "target-group"


@pytest.mark.asyncio
async def test_management_scope_catalog_rejects_unknown_scope_before_loading(hass) -> None:
    with pytest.raises(ValueError, match="scope_kind"):
        await management_loading_performance.async_scope_catalog(
            hass,
            "user",
            True,
            "entry",
            "agent",
            scope_kind="invalid",
        )


@pytest.mark.asyncio
async def test_management_overview_detail_rejects_unknown_kind(hass) -> None:
    entry = SimpleNamespace(entry_id="entry", data={})
    subentry = SimpleNamespace(subentry_id="agent", data={})

    with pytest.raises(ValueError, match="kind must be"):
        await management_loading_performance.async_overview_detail(
            hass,
            entry,
            subentry,
            is_admin=True,
            kind="invalid",
        )


def test_rule_pack_validation_rejects_non_serializable_mapping() -> None:
    pack = {
        "format": request_rule_packs.PACK_FORMAT,
        "version": request_rule_packs.PACK_VERSION,
        "groups": [],
        "rules": {object()},
    }

    with pytest.raises(ValueError, match="invalid or overly nested data"):
        validate_rule_pack(pack)


def test_rule_pack_validation_rejects_oversized_parsed_mapping(monkeypatch) -> None:
    pack = {
        "format": request_rule_packs.PACK_FORMAT,
        "version": request_rule_packs.PACK_VERSION,
        "groups": [],
        "rules": [],
        "padding": "x" * 100,
    }
    # Preserve the exact top-level schema while making the serialized document
    # exceed a deliberately tiny bound.
    pack.pop("padding")
    pack["groups"] = [{"id": "g", "name": "x" * 100}]
    monkeypatch.setattr(request_rule_packs, "MAX_PACK_BYTES", 32)

    with pytest.raises(ValueError, match="2 MB safety limit"):
        validate_rule_pack(pack)


def test_rule_pack_validation_rejects_unrecognized_format() -> None:
    pack = {
        "format": "some_other_format",
        "version": request_rule_packs.PACK_VERSION,
        "groups": [],
        "rules": [validate_rule(local_rule("Only"))],
    }

    with pytest.raises(ValueError, match="Unrecognized Request Rule pack format"):
        validate_rule_pack(pack)


@pytest.mark.asyncio
async def test_rule_pack_append_enforces_destination_rule_limit() -> None:
    target = RequestRules(
        MemoryStore(
            {
                "rules": [
                    local_rule(f"Existing {index}", order=index)
                    for index in range(request_rule_packs.MAX_RULES)
                ]
            }
        )
    )
    await target.async_initialize()
    source = RequestRules(MemoryStore({"rules": [local_rule("Imported")]}))
    await source.async_initialize()
    prepared = validate_rule_pack(export_rule_pack(source, "all"))

    with pytest.raises(ValueError, match="Request Rule limit reached"):
        await async_append_rule_pack(
            target,
            prepared,
            expected_revision=target.revision(),
        )
