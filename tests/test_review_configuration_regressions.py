"""Schema pointer and concurrent Function repair regressions."""

from copy import deepcopy
import json
from types import SimpleNamespace

from jsonschema import Draft202012Validator
import pytest
import yaml

from custom_components.extended_openai_conversation_responses.ai_task import parse_ai_task_structured_response
from custom_components.extended_openai_conversation_responses import management_function_repair as repair, management_ui
from homeassistant.exceptions import HomeAssistantError
from tests.test_ai_task_optional_compositions import _caller_structure
from tests.test_management_function_repair import _mixed_legacy_tool_data, _entry_and_subentry, _FakeConfigEntries


@pytest.mark.parametrize("reference", ["#/properties/sample/anyOf/0", "#/properties/sample/anyOf/%30"])
def test_ai_task_local_reference_can_traverse_composition_arrays(reference):
    schema = {"type": "object", "properties": {"sample": {"anyOf": [{"type": "string"}, {"type": "integer"}]}, "copy": {"$ref": reference}}, "required": ["sample", "copy"], "additionalProperties": False}
    data = {"sample": "hello", "copy": "world"}
    Draft202012Validator(schema).validate(data)
    assert parse_ai_task_structured_response(json.dumps(data), _caller_structure(schema), original_schema=schema) == data


@pytest.mark.parametrize("concurrent_save", [False, True])
async def test_failed_repair_rename_preserves_newer_configuration(monkeypatch, concurrent_save):
    data, mixed, _ = _mixed_legacy_tool_data()
    data["prompt"] = "original"
    entry, subentry = _entry_and_subentry(data)
    hass = SimpleNamespace(data={}, config_entries=_FakeConfigEntries())
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))
    original = deepcopy(data)

    class Rules:
        def revision(self):
            return "rules-revision"

        async def async_rename_function_reference(self, *_args, **_kwargs):
            if concurrent_save:
                subentry.data = {**subentry.data, "prompt": "newer successful save"}
            raise HomeAssistantError("rules write failed")

    async def references(*_):
        return Rules(), {}

    monkeypatch.setattr(management_ui, "_function_reference_state", references)
    tool = deepcopy(mixed[1])
    tool["spec"]["parameters"].pop("description")
    tool["spec"]["name"] = "repaired_tool"
    with pytest.raises(HomeAssistantError, match="newer configuration was preserved" if concurrent_save else "rules write failed"):
        await repair.async_function_repair(hass, "admin", True, {"action": "save_one", "entry_id": entry.entry_id, "subentry_id": subentry.subentry_id, "revision": repair.repair_revision(subentry), "index": 1, "tool": tool})
    if concurrent_save:
        assert subentry.data["prompt"] == "newer successful save"
        assert yaml.safe_load(subentry.data["functions"])[1]["spec"]["name"] == "repaired_tool"
    else:
        assert subentry.data == original


async def test_repair_rechecks_revision_after_loading_references(monkeypatch):
    data, mixed, _ = _mixed_legacy_tool_data()
    entry, subentry = _entry_and_subentry(data)
    hass = SimpleNamespace(data={}, config_entries=_FakeConfigEntries())
    monkeypatch.setattr(management_ui, "entry_and_agent", lambda *_: (entry, subentry))

    async def references(*_):
        subentry.data = {**subentry.data, "prompt": "newer successful save"}
        return SimpleNamespace(revision=lambda: "rules-revision"), {}

    monkeypatch.setattr(management_ui, "_function_reference_state", references)
    tool = deepcopy(mixed[1])
    tool["spec"]["parameters"].pop("description")
    tool["spec"]["name"] = "repaired_tool"
    with pytest.raises(HomeAssistantError, match="changed"):
        await repair.async_function_repair(hass, "admin", True, {"action": "save_one", "entry_id": entry.entry_id, "subentry_id": subentry.subentry_id, "revision": repair.repair_revision(subentry), "index": 1, "tool": tool})
    assert subentry.data["prompt"] == "newer successful save"
    assert subentry.data["functions"] == data["functions"]
    assert hass.config_entries.updates == 0
