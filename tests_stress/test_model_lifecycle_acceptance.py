"""Model lifecycle acceptance at the real HA, SDK and Repair boundaries."""

import asyncio
from datetime import UTC, datetime
from types import MappingProxyType

import httpx
import pytest

from custom_components.extended_openai_conversation_responses import model_lifecycle
from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_CHAT_COMPLETIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
    DOMAIN,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    async_get_request_rules,
)
from homeassistant.components import conversation
from homeassistant.config_entries import ConfigSubentry
from homeassistant.core import Context
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from tests_real_ha.test_acceptance_lifecycle import _make_entry
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _management_call,
)
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _raw_client,
    _responses_sse_text,
    _speech,
)


@pytest.mark.parametrize("api_mode", [API_MODE_CHAT_COMPLETIONS, API_MODE_RESPONSES])
async def test_provider_confirmed_lifecycle_tracks_actual_request_and_assistant(
    hass, hass_ws_client, monkeypatch, api_mode
):
    class AfterShutdown(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2027, 4, 2, tzinfo=UTC)

    monkeypatch.setattr(model_lifecycle, "datetime", AfterShutdown)
    entry = _make_entry(
        "Model availability",
        include_ai_task=False,
        conversation_options={
            CONF_API_MODE: api_mode,
            CONF_CHAT_MODEL: "gpt-5.1",
        },
    )
    entry.add_to_hass(hass)
    sibling = ConfigSubentry(
        data=MappingProxyType({CONF_API_MODE: api_mode, CONF_CHAT_MODEL: "gpt-5.1"}),
        subentry_type="conversation",
        title="Other assistant",
        unique_id=None,
    )
    assert hass.config_entries.async_add_subentry(entry, sibling)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    original = next(
        sub
        for sub in entry.subentries.values()
        if sub.subentry_id != sibling.subentry_id
    )

    def agent_for(subentry):
        entity_id = next(
            row.entity_id
            for row in er.async_entries_for_config_entry(
                er.async_get(hass), entry.entry_id
            )
            if row.config_subentry_id == subentry.subentry_id
            and row.domain == "conversation"
        )
        return conversation.async_get_agent(hass, entity_id)

    agent = agent_for(original)
    assert agent is not None
    registry = ir.async_get(hass)
    issue_id = f"retired_model_{entry.entry_id}_{original.subentry_id}"
    sibling_issue = f"retired_model_{entry.entry_id}_{sibling.subentry_id}"

    def issue():
        return registry.async_get_issue(DOMAIN, issue_id)

    assert issue() is None  # A catalogue date alone creates no Repair.
    reply = None
    entered, release = asyncio.Event(), asyncio.Event()
    gated = False
    requests = []

    async def send(request, *args, **kwargs):
        import json

        requests.append(json.loads(request.content))
        if gated:
            entered.set()
            await release.wait()
        if reply:
            status, code, message = reply
            return httpx.Response(
                status,
                json={
                    "error": {
                        "code": code,
                        "type": "invalid_request_error",
                        "message": message,
                    }
                },
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=_chat_sse_text("Available")
            if api_mode == API_MODE_CHAT_COMPLETIONS
            else _responses_sse_text("Available"),
            request=request,
        )

    def install(current):
        monkeypatch.setattr(_raw_client(current)._client, "send", send)

    install(agent)

    async def turn(current, text="availability probe"):
        return await conversation.async_converse(
            hass=hass,
            text=text,
            conversation_id=None,
            context=Context(),
            language="en",
            agent_id=current.entity_id,
        )

    assert _speech(await turn(agent)) == "Available"
    assert requests[-1]["model"] == "gpt-5.1"
    assert issue() is None  # Still available after the announced shutdown date.
    for failure in [
        (429, "insufficient_quota", "Quota exceeded"),
        (400, "invalid_parameter", "Unrelated argument failure"),
    ]:
        reply = failure
        assert (await turn(agent)).response.error_code is not None
        assert issue() is None
    reply = (404, "model_not_found", "The model gpt-5.1 has been retired")
    assert (await turn(agent)).response.error_code is not None
    assert issue().translation_placeholders["model"] == "gpt-5.1"
    assert model_lifecycle.confirmed_retirement_failure(
        hass, entry.entry_id, original.subentry_id, "gpt-5.1"
    )
    assert registry.async_get_issue(DOMAIN, sibling_issue) is None
    reply = None
    assert _speech(await turn(agent_for(sibling))) == "Available"
    assert issue() is not None  # Sibling success cannot clear this assistant's failure.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    agent = agent_for(original)
    install(agent)
    assert issue() is not None
    assert _speech(await turn(agent)) == "Available"
    assert issue() is None
    client = await _admin_client(hass, hass_ws_client)

    async def save_model(model):
        before = await _management_call(
            client,
            entry=entry,
            subentry_id=original.subentry_id,
            section="configuration",
            action="get",
        )
        await _management_call(
            client,
            entry=entry,
            subentry_id=original.subentry_id,
            section="configuration",
            action="update",
            revision=before["revision"],
            config={CONF_CHAT_MODEL: model, CONF_REASONING_EFFORT: "none"},
        )

    await save_model("gpt-5.6")
    await hass.async_block_till_done()
    agent = agent_for(original)
    install(agent)
    rules = await async_get_request_rules(hass, entry.entry_id, original.subentry_id)
    await rules.async_create(
        {
            "name": "Legacy model request",
            "phrases": ["legacy probe"],
            "match_type": "equals",
            "action_type": "model_routing",
            "action": {"model": "gpt-5.1", "scope": "request", "continue_to_ai": True},
        }
    )
    reply = (404, "model_unavailable", "The model gpt-5.1 is unavailable")
    assert (await turn(agent, "legacy probe")).response.error_code is not None
    assert requests[-1]["model"] == "gpt-5.1"
    assert issue().translation_placeholders["model"] == "gpt-5.1"
    reply = None
    assert _speech(await turn(agent)) == "Available"
    assert requests[-1]["model"] == "gpt-5.6"
    assert (
        issue() is not None
    )  # Different model success does not certify the failed override.
    assert _speech(await turn(agent, "legacy probe")) == "Available"
    assert issue() is None
    await save_model("gpt-5.1")
    await hass.async_block_till_done()
    agent = agent_for(original)
    install(agent)
    reply = (404, "model_not_found", "The model gpt-5.1 is retired")
    gated = True
    pending = asyncio.create_task(turn(agent))
    await asyncio.wait_for(entered.wait(), 5)
    await save_model("gpt-5.6")
    release.set()
    assert (await pending).response.error_code is not None
    assert requests[-1]["model"] == "gpt-5.1"
    assert issue().translation_placeholders["model"] == "gpt-5.1"
    gated = False
    await save_model("gpt-5.1")
    await save_model("gpt-5.6")
    assert issue() is None
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert issue() is None
    assert registry.async_get_issue(DOMAIN, sibling_issue) is None


async def test_model_retirement_repair_survives_real_process_restart_until_success(
    tmp_path,
    socket_enabled,
):
    from pathlib import Path
    import socket

    from tests_real_ha.process_harness import run_python_child
    from tests_real_ha.test_delayed_tool_process_restart import _stage_component

    source = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
    config_dir = tmp_path / "ha-config"
    destination = config_dir / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    _stage_component(source, destination)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    (config_dir / "configuration.yaml").write_text(
        f"homeassistant:\n  name: Model lifecycle restart\nhttp:\n  server_host: 127.0.0.1\n  server_port: {port}\n"
    )
    script = Path(__file__).with_name("model_lifecycle_process.py")
    for phase in ["fail", "recover"]:
        result = await asyncio.to_thread(
            run_python_child,
            script,
            cwd=config_dir,
            extra_env={
                "MODEL_LIFECYCLE_CONFIG_DIR": str(config_dir),
                "MODEL_LIFECYCLE_PROCESS_PHASE": phase,
            },
            timeout=60,
        )
        assert result.returncode == 0, f"{phase}: {result.stdout}\n{result.stderr}"
