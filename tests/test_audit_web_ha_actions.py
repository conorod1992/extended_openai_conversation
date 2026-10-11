"""Transport bytes, ordinary log privacy, executor use and HA dispatch regressions."""

import asyncio
from functools import partial
import json
import logging
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from aiohttp import web as http_web
import pytest

from custom_components.extended_openai_conversation_responses import ha_actions
from custom_components.extended_openai_conversation_responses.functions import web
from custom_components.extended_openai_conversation_responses.functions.native import (
    NativeFunction,
)
from custom_components.extended_openai_conversation_responses.functions.script import (
    _AuthorizedScriptServices,
)
from homeassistant.core import State
from homeassistant.exceptions import HomeAssistantError
from tests.test_remote_response_bounds import _FakeResponse, _FakeSession, _rest_config


@pytest.mark.asyncio
@pytest.mark.parametrize("scrape", [False, True])
@pytest.mark.parametrize(
    "source,expected",
    [
        (" {{ text }}\n", ' {"a":1}\n'),
        ("{{ text }}\n\n", '{"a":1}\n\n'),
        ("{% for i in range(2) %}{{ text }}\n{% endfor %}", '{"a":1}\n{"a":1}\n'),
    ],
    ids=["leading-space", "final-newlines", "ndjson-loop"],
)
async def test_literal_and_templated_payloads_transmit_identical_bytes(
    hass, monkeypatch, socket_enabled, scrape, source, expected
):
    hass.loop = asyncio.get_running_loop()
    hass.is_stopping = False
    received = []

    async def receive(request):
        received.append(await request.read())
        return http_web.Response(text="<p>ok</p>", content_type="text/html")

    app = http_web.Application()
    app.router.add_post("/", receive)
    runner = http_web.AppRunner(app)
    await runner.setup()
    site = http_web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    function = web.ScrapeFunction() if scrape else web.RestFunction()
    config = {
        "type": "scrape" if scrape else "rest",
        "method": "POST",
        "resource": f"http://127.0.0.1:{port}/",
    }
    if scrape:
        config["sensor"] = [{"select": "p"}]
    try:
        async with aiohttp.ClientSession() as session:
            monkeypatch.setattr(
                web, "async_get_clientsession", lambda *a, **kw: session
            )
            for payload in ({"payload": expected}, {"payload_template": source}):
                validated = function.validate_schema({**config, **payload})
                await function.execute(hass, validated, {"text": '{"a":1}'}, None, [])
        assert received == [expected.encode(), expected.encode()]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("scrape", [False, True])
async def test_real_rest_transport_failure_never_logs_url_secrets(
    hass, monkeypatch, caplog, scrape
):
    caplog.set_level(logging.WARNING)
    hass.loop = asyncio.get_running_loop()
    hass.is_stopping = False
    secret_url = "https://user:password@example.invalid/api?token=PRIVATE_TOKEN"

    class FailedSession:
        def request(self, *args, **kwargs):
            raise aiohttp.ClientConnectionError(secret_url)

    monkeypatch.setattr(
        web, "async_get_clientsession", lambda *a, **kw: FailedSession()
    )
    config = {**_rest_config(), "resource": secret_url}
    function = web.ScrapeFunction() if scrape else web.RestFunction()
    if scrape:
        config["sensor"] = [{"select": "p"}]
    with pytest.raises(HomeAssistantError) as caught:
        await function.execute(hass, config, {}, None, [])
    assert "PRIVATE_TOKEN" not in caplog.text
    assert "password" not in caplog.text
    assert "PRIVATE_TOKEN" not in str(caught.value)


@pytest.mark.asyncio
async def test_xml_conversion_runs_on_executor_thread(hass, monkeypatch):
    response = _FakeResponse(b"<?xml version='1.0'?><root><answer>42</answer></root>")
    response.headers = {"Content-Type": "application/xml"}
    monkeypatch.setattr(
        web, "async_get_clientsession", lambda *a, **kw: _FakeSession(response)
    )
    original = web.rest.data.RestData.data_without_xml
    threads = []

    def convert(self):
        threads.append(threading.get_ident())
        return original(self)

    monkeypatch.setattr(web.rest.data.RestData, "data_without_xml", convert)
    hass.async_add_executor_job.side_effect = asyncio.to_thread
    result = await web.RestFunction().execute(hass, _rest_config(), {}, None, [])
    assert json.loads(result) == {"root": {"answer": "42"}}
    assert threads and threads[0] != threading.get_ident()


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["native", "script"])
async def test_recovery_services_allow_offline_entities_but_require_exposure(
    hass, monkeypatch, entrypoint
):
    entity_id = "light.offline"
    hass.states.get.side_effect = lambda entity: State(entity, "unavailable")
    monkeypatch.setattr(
        ha_actions.target_helpers,
        "async_extract_referenced_entity_ids",
        lambda *a: SimpleNamespace(referenced={entity_id}, indirectly_referenced=set()),
    )
    monkeypatch.setattr(ha_actions, "async_require_control_permission", AsyncMock())
    function = NativeFunction()

    async def execute(service, exposed):
        if entrypoint == "native":
            function.validate_service_targets(
                hass,
                {"entity_id": entity_id},
                exposed,
                domain="homeassistant",
                service=service,
            )
        else:
            await _AuthorizedScriptServices(hass, function, exposed).async_call(
                "homeassistant", service, {"entity_id": entity_id}
            )

    await execute("reload_config_entry", [{"entity_id": entity_id}])
    with pytest.raises(HomeAssistantError, match="unavailable"):
        await execute("turn_on", [{"entity_id": entity_id}])
    with pytest.raises(HomeAssistantError):
        await execute("reload_config_entry", [])


@pytest.mark.parametrize("batched", [False, True])
@pytest.mark.parametrize("platforms", [False, True])
def test_indirect_feature_filter_preserves_explicit_unsupported_targets(
    hass, monkeypatch, batched, platforms
):
    entities = {
        "cover.position": SimpleNamespace(supported_features=7),
        "cover.open_only": SimpleNamespace(supported_features=1),
        "cover.alternative": SimpleNamespace(supported_features=8),
    }
    registered = [SimpleNamespace(entities=entities)] if platforms else entities
    dispatch = (
        ha_actions.service_helpers.batched_entity_service_call
        if batched
        else ha_actions.service_helpers.entity_service_call
    )
    service = SimpleNamespace(
        job=SimpleNamespace(
            target=partial(dispatch, hass, registered, required_features=[6, 8])
        )
    )
    hass.services.async_services_for_domain.side_effect = lambda domain: (
        {"set_cover_position": service} if domain == "cover" else {}
    )
    monkeypatch.setattr(
        ha_actions.target_helpers,
        "async_extract_referenced_entity_ids",
        lambda h, selection: SimpleNamespace(
            referenced=selection.entity_ids,
            indirectly_referenced=set(entities) if not selection.entity_ids else set(),
        ),
    )
    assert ha_actions.resolve_action_entity_ids(
        hass, "cover", "set_cover_position", target={"area_id": "room"}
    ) == {"cover.position", "cover.alternative"}
    assert ha_actions.resolve_action_entity_ids(
        hass, "cover", "set_cover_position", target={"entity_id": "cover.open_only"}
    ) == {"cover.open_only"}
    NativeFunction().validate_service_targets(
        hass,
        {"area_id": "room"},
        [{"entity_id": "cover.position"}, {"entity_id": "cover.alternative"}],
        domain="cover",
        service="set_cover_position",
    )
