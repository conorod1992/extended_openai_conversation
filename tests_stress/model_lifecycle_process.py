"""Independent real HA process for lifecycle persistence acceptance."""

import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock, patch

import httpx


async def main():
    config_dir = Path(os.environ["MODEL_LIFECYCLE_CONFIG_DIR"])
    phase = os.environ["MODEL_LIFECYCLE_PROCESS_PHASE"]
    sys.path.insert(0, str(config_dir))
    from custom_components.extended_openai_conversation_responses import (
        config_flow,
        const,
        model_lifecycle,
    )
    from homeassistant import bootstrap, runner
    from homeassistant.components import conversation
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.core import Context
    from homeassistant.helpers import issue_registry as ir
    from tests_real_ha.test_delayed_tool_process_restart import _assert_source_component
    from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _raw_client

    _assert_source_component(config_dir)

    class AfterShutdown(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2027, 4, 2, tzinfo=UTC)

    with patch.object(model_lifecycle, "datetime", AfterShutdown):
        hass = await bootstrap.async_setup_hass(
            runner.RuntimeConfig(config_dir=str(config_dir), skip_pip=False)
        )
        assert hass is not None
        await hass.async_start()
        try:
            if phase == "fail":
                with patch.object(
                    config_flow,
                    "get_authenticated_client",
                    AsyncMock(return_value=object()),
                ):
                    result = await hass.config_entries.flow.async_init(
                        const.DOMAIN, context={"source": "user"}
                    )
                    result = await hass.config_entries.flow.async_configure(
                        result["flow_id"],
                        {
                            "name": "Lifecycle restart",
                            "api_key": "sk-lifecycle-test",
                            const.CONF_SKIP_AUTHENTICATION: True,
                            const.CONF_BASE_URL: const.DEFAULT_CONF_BASE_URL,
                            const.CONF_API_PROVIDER: "openai",
                        },
                    )
                entry = result["result"]
                await hass.async_block_till_done()
                subentry = next(
                    sub
                    for sub in entry.subentries.values()
                    if sub.subentry_type == "conversation"
                )
                hass.config_entries.async_update_subentry(
                    entry,
                    subentry,
                    data={
                        **subentry.data,
                        const.CONF_CHAT_MODEL: "gpt-5.1",
                        const.CONF_API_MODE: "chat_completions",
                        const.CONF_REASONING_EFFORT: "none",
                    },
                )
                assert await hass.config_entries.async_reload(entry.entry_id)
                await hass.async_block_till_done()
                (config_dir / "lifecycle-entry.json").write_text(
                    json.dumps(
                        {"entry": entry.entry_id, "subentry": subentry.subentry_id}
                    )
                )
            else:
                metadata = json.loads((config_dir / "lifecycle-entry.json").read_text())
                entry = hass.config_entries.async_get_entry(metadata["entry"])
                assert entry is not None
                if entry.state is ConfigEntryState.NOT_LOADED:
                    assert await hass.config_entries.async_setup(entry.entry_id)
                await hass.async_block_till_done()
                subentry = entry.subentries[metadata["subentry"]]
            agent = conversation.async_get_agent(hass, entry.entry_id)
            assert agent is not None
            issue_id = f"retired_model_{entry.entry_id}_{subentry.subentry_id}"
            registry = ir.async_get(hass)
            if phase == "recover":
                issue = registry.async_get_issue(const.DOMAIN, issue_id)
                assert issue is not None and issue.active and issue.is_persistent
                assert issue.translation_placeholders["model"] == "gpt-5.1"
                assert model_lifecycle.confirmed_retirement_failure(
                    hass, entry.entry_id, subentry.subentry_id, "gpt-5.1"
                )

            async def send(request, *args, **kwargs):
                assert json.loads(request.content)["model"] == "gpt-5.1"
                if phase == "fail":
                    return httpx.Response(
                        404,
                        json={
                            "error": {
                                "code": "model_not_found",
                                "type": "invalid_request_error",
                                "message": "The model gpt-5.1 is retired",
                            }
                        },
                        request=request,
                    )
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=_chat_sse_text("Available after restart"),
                    request=request,
                )

            with patch.object(_raw_client(agent)._client, "send", send):
                result = await conversation.async_converse(
                    hass=hass,
                    text="availability probe",
                    conversation_id=None,
                    context=Context(),
                    language="en",
                    agent_id=entry.entry_id,
                )
            if phase == "fail":
                assert result.response.error_code is not None
                assert registry.async_get_issue(const.DOMAIN, issue_id).is_persistent
            else:
                assert result.response.error_code is None
                assert registry.async_get_issue(const.DOMAIN, issue_id) is None
        finally:
            await hass.async_stop()


if __name__ == "__main__":
    asyncio.run(main())
