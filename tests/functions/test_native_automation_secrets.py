"""Secret references in newly registered automations stay unresolved outside HA."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import voluptuous as vol
import yaml

from custom_components.extended_openai_conversation_responses import execution_outcomes
from custom_components.extended_openai_conversation_responses.const import (
    EVENT_AUTOMATION_REGISTERED,
)
from custom_components.extended_openai_conversation_responses.functions import native
from custom_components.extended_openai_conversation_responses.yaml_serialization import (
    dump_yaml,
)
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util.yaml import Secrets, load_yaml

_PRIVATE_MARKER = "private-automation-marker"
_SOURCE = (
    "- id: caller-id\n"
    "  alias: !secret automation_alias\n"
    "  triggers: []\n"
    "  actions:\n"
    "    - action: example.record\n"
    "      data: {marker: !secret automation_marker}\n"
)


@pytest.fixture
def automation_admin(hass):
    hass.auth.async_get_user = AsyncMock(
        return_value=SimpleNamespace(is_active=True, is_admin=True)
    )
    return SimpleNamespace(context=Context(user_id="admin-user"))


@pytest.mark.parametrize(
    ("marker_key", "authored_key"),
    [("automation_marker", "automation_marker"), ("marker\x85line", r'"marker\Nline"')],
)
async def test_add_automation_keeps_secrets_in_native_validation_and_reload(
    hass, monkeypatch, automation_admin, marker_key, authored_key
):
    config_dir = Path(hass.config.config_dir)
    (config_dir / "secrets.yaml").write_text(
        dump_yaml({"automation_alias": "Private alias", marker_key: _PRIVATE_MARKER}),
        encoding="utf-8",
    )
    source = _SOURCE.replace("!secret automation_marker", f"!secret {authored_key}")
    path = config_dir / "automations.yaml"
    validation = AsyncMock()
    monkeypatch.setattr(
        native.automation.config, "_async_validate_config_item", validation
    )
    receipt = Mock(return_value=None)
    monkeypatch.setattr(execution_outcomes, "execution_started", receipt)

    async def reload(*args, **kwargs):
        loaded = load_yaml(path, Secrets(config_dir))[0]
        assert loaded["alias"] == "Private alias"
        assert loaded["actions"][0]["data"]["marker"] == _PRIVATE_MARKER
        hass.data[native.automation.DOMAIN] = SimpleNamespace(
            entities=[SimpleNamespace(unique_id=loaded["id"])]
        )

    hass.services.async_call = AsyncMock(side_effect=reload)
    assert (
        await native.NativeFunction().add_automation(
            hass, {}, {"automation_config": source}, automation_admin, []
        )
        == "Success"
    )
    resolved = validation.await_args.args[1]
    assert resolved["id"] != "caller-id"
    assert resolved["alias"] == "Private alias"
    assert resolved["actions"][0]["data"]["marker"] == _PRIVATE_MARKER

    written = path.read_text(encoding="utf-8")
    tags = yaml.compose(written).value[0].value
    assert next(value for key, value in tags if key.value == "alias").tag == "!secret"
    event, data = hass.bus.async_fire.call_args.args
    assert event == EVENT_AUTOMATION_REGISTERED
    assert data["automation_config"]["alias"] == "!secret automation_alias"
    assert data["automation_config"]["actions"][0]["data"]["marker"] == (
        f"!secret {marker_key}"
    )
    assert data["raw_config"] == written
    for retained in (written, json.dumps(data), json.dumps(receipt.call_args.kwargs)):
        assert _PRIVATE_MARKER not in retained
        assert "Private alias" not in retained


@pytest.mark.parametrize("failure", ["missing", "invalid", "reload", "rollback"])
async def test_automation_secret_failures_do_not_disclose_resolved_values(
    hass, monkeypatch, caplog, automation_admin, failure
):
    config_dir = Path(hass.config.config_dir)
    marker_line = (
        "" if failure == "missing" else f"automation_marker: {_PRIVATE_MARKER}\n"
    )
    (config_dir / "secrets.yaml").write_text(
        "automation_alias: Private alias\n" + marker_line, encoding="utf-8"
    )
    path = config_dir / "automations.yaml"
    original = "- id: existing\n  alias: Existing\n"
    path.write_text(original, encoding="utf-8")
    validation = AsyncMock(
        side_effect=vol.Invalid(f"Invalid value {_PRIVATE_MARKER}")
        if failure == "invalid"
        else None
    )
    monkeypatch.setattr(
        native.automation.config, "_async_validate_config_item", validation
    )
    hass.services.async_call = AsyncMock(
        side_effect=HomeAssistantError(f"Internal reload failure {_PRIVATE_MARKER}")
    )
    if failure == "rollback":

        def fail_rollback(*args):
            raise HomeAssistantError(f"Internal rollback failure {_PRIVATE_MARKER}")

        monkeypatch.setattr(native, "_restore_automation_file", fail_rollback)

    with pytest.raises(
        HomeAssistantError, match="automation YAML containing !secret"
    ) as err:
        await native.NativeFunction().add_automation(
            hass, {}, {"automation_config": _SOURCE}, automation_admin, []
        )

    assert err.value.__cause__ is err.value.__context__ is None
    assert _PRIVATE_MARKER not in str(err.value)
    assert _PRIVATE_MARKER not in caplog.text
    written = path.read_text(encoding="utf-8")
    if failure == "rollback":
        assert written.startswith(original) and "!secret" in written
        assert _PRIVATE_MARKER not in written
    else:
        assert written == original
    hass.bus.async_fire.assert_not_called()
    assert hass.services.async_call.await_count == {"reload": 2, "rollback": 1}.get(
        failure, 0
    )
