"""Regression coverage for Configuration metadata during Function Tool repair."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from custom_components.extended_openai_conversation_responses import (
    exposed_attributes as ea,
    management_configuration_guidance as guidance,
    management_ui,
)
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_defaults,
)
from custom_components.extended_openai_conversation_responses.const import (
    CONF_FUNCTION_GROUPS,
    CONF_FUNCTION_TOOLS,
)


class _FakeConfigEntries:
    def async_update_subentry(
        self, _entry: Any, subentry: Any, *, data: dict[str, Any], **_kwargs: Any
    ) -> None:
        subentry.data = data


class _States:
    def __init__(self, states: dict[str, Any]) -> None:
        self._states = states

    def get(self, entity_id: str) -> Any:
        return self._states.get(entity_id)


def _repairable_agent() -> tuple[Any, Any]:
    defaults = agent_config_defaults()
    tools = yaml.safe_load(defaults[CONF_FUNCTION_TOOLS])
    assert isinstance(tools, list) and tools

    valid_tool = deepcopy(tools[0])
    broken_tool = deepcopy(tools[0])
    broken_tool["spec"]["name"] = f"{broken_tool['spec']['name']}_broken"
    broken_tool["spec"].setdefault("parameters", {"type": "object", "properties": {}})[
        "description"
    ] = 123

    subentry = SimpleNamespace(
        subentry_id="agent-1",
        subentry_type="conversation",
        title="Broken agent",
        data={
            CONF_FUNCTION_TOOLS: yaml.safe_dump(
                [valid_tool, broken_tool], sort_keys=False, allow_unicode=True
            ),
            CONF_FUNCTION_GROUPS: deepcopy(defaults[CONF_FUNCTION_GROUPS]),
        },
    )
    entry = SimpleNamespace(
        entry_id="entry-1",
        title="Extended OpenAI",
        data={},
        subentries={subentry.subentry_id: subentry},
    )
    return entry, subentry


@pytest.mark.asyncio
async def test_function_repair_configuration_get_preserves_dynamic_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repair-mode Configuration keeps Assist catalogue and guidance metadata."""
    entry, subentry = _repairable_agent()
    registry_entry = SimpleNamespace(id="registry-light-1", entity_id="light.kitchen")
    registry = SimpleNamespace(
        async_get=lambda entity_id: (
            registry_entry if entity_id == "light.kitchen" else None
        ),
        entities=SimpleNamespace(
            get_entry=lambda entry_id: (
                registry_entry if entry_id == registry_entry.id else None
            )
        ),
    )
    hass = SimpleNamespace(
        data={},
        config_entries=_FakeConfigEntries(),
        states=_States(
            {
                "light.kitchen": SimpleNamespace(
                    attributes={"brightness": 180, "effect": "none"}
                )
            }
        ),
    )

    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        lambda *_args, **_kwargs: (entry, subentry),
    )
    monkeypatch.setattr(ea.er, "async_get", lambda _hass: registry)
    monkeypatch.setattr(
        ea,
        "get_exposed_entities",
        lambda _hass: [{"entity_id": "light.kitchen", "name": "Kitchen"}],
    )

    repair_configuration = management_ui.async_management_command
    payload = await repair_configuration(
        hass,
        "admin",
        True,
        {
            "section": "function_repair",
            "action": "configuration_get",
            "entry_id": entry.entry_id,
            "subentry_id": subentry.subentry_id,
        },
    )

    assert payload["function_repair"]["invalid_count"] == 1
    assert payload["exposed_attribute_catalog"]["entities"] == [
        {
            "entity_id": "light.kitchen",
            "name": "Kitchen",
            "reference": "registry:registry-light-1",
            "attributes": ["brightness", "effect"],
            "selected_attributes": [],
            "missing_selected_attributes": [],
            "durable_selection_available": True,
        }
    ]
    assert "configuration_guidance" in payload
    assert "web_search" in payload["configuration_guidance"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("section", "action", "catalog_expected"),
    [
        ("configuration", "get", True),
        ("configuration", "update", True),
        ("configuration", "save", True),
        ("configuration", "validate", False),
        ("function_repair", "configuration_get", True),
        ("function_repair", "configuration_save", True),
        ("function_repair", "configuration_validate", False),
    ],
)
async def test_shared_configuration_response_contract_covers_normal_and_repair_actions(
    monkeypatch: pytest.MonkeyPatch,
    section: str,
    action: str,
    catalog_expected: bool,
) -> None:
    """Normal and repair Configuration actions share one metadata contract."""
    entry = SimpleNamespace(data={"provider": "test"})
    subentry = SimpleNamespace()
    monkeypatch.setattr(
        management_ui,
        "entry_and_agent",
        lambda *_args, **_kwargs: (entry, subentry),
    )
    monkeypatch.setattr(
        guidance,
        "configuration_guidance_snapshot",
        lambda entry_data, config: {"entry": entry_data, "config": config},
    )
    monkeypatch.setattr(
        guidance,
        "exposed_attribute_catalog",
        lambda _hass, config: {"entities": [{"config": config}]},
    )

    async def original(
        _hass: Any, _user_id: str, _is_admin: bool, _message: dict[str, Any]
    ) -> dict[str, Any]:
        return {"config": {"marker": action}, "preserved": True}

    async def handler(_request):
        return {"config": {"marker": action}, "preserved": True}

    monkeypatch.setattr(
        management_ui, "_MANAGEMENT_SECTION_HANDLERS", {section: handler}
    )
    wrapped = management_ui.async_management_command
    result = await wrapped(
        SimpleNamespace(data={}),
        "admin",
        True,
        {
            "section": section,
            "action": action,
            "entry_id": "entry-1",
            "subentry_id": "agent-1",
        },
    )

    assert result["preserved"] is True
    assert result["configuration_guidance"]["config"] == {"marker": action}
    if catalog_expected:
        assert result["exposed_attribute_catalog"]["entities"] == [
            {"config": {"marker": action}}
        ]
    else:
        assert "exposed_attribute_catalog" not in result
