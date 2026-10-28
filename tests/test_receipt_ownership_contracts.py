"""Dispatch counts witness ownership at implementation and shared HTTP seams."""

import asyncio
from types import SimpleNamespace

import aiohttp
from aiohttp import web as http_web
import pytest

from custom_components.extended_openai_conversation_responses import execution_outcomes
from custom_components.extended_openai_conversation_responses.functions import web
from custom_components.extended_openai_conversation_responses.public_errors import (
    SanitizedUnexpectedBackendError,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    bind_effect_replay_scope,
    clear_unacknowledged_calls,
    remember_unacknowledged_calls,
    was_unacknowledged_equivalent,
)
from homeassistant.exceptions import HomeAssistantError
from tests.test_function_tool_error_recovery import (
    _call,
    _chat_log,
    _retain_calls,
    _tool,
)


@pytest.fixture
async def http_boundary(hass, monkeypatch, socket_enabled):
    """Observe received POSTs through real HA RestData and ScrapeCoordinator."""
    hass.loop = asyncio.get_running_loop()
    hass.is_stopping = False
    state = SimpleNamespace(
        received=[], hold=False, entered=asyncio.Event(), release=asyncio.Event()
    )

    async def receive(request):
        state.received.append(await request.read())
        state.entered.set()
        if state.hold:
            await state.release.wait()
        return http_web.Response(text="<p>ok</p>", content_type="text/html")

    app = http_web.Application()
    app.router.add_post("/dispense", receive)
    runner = http_web.AppRunner(app)
    await runner.setup()
    site = http_web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    state.resource = f"http://127.0.0.1:{port}/dispense"
    try:
        async with aiohttp.ClientSession() as session:
            monkeypatch.setattr(
                web, "async_get_clientsession", lambda *_a, **_kw: session
            )
            yield state
    finally:
        state.release.set()
        await runner.cleanup()


async def dispatch(entity, log, backend, config, *, name, call_id, arguments=None):
    call = _call(call_id, name, arguments)
    _retain_calls(log, [call])
    tool = _tool(name, parameters={"type": "object"}, function=config)
    with (
        bind_effect_replay_scope(entity, log.conversation_id),
        execution_outcomes.execution_attempt(entity, call, tool),
    ):
        execution_outcomes.execution_prepared(call.tool_args)
        return await execution_outcomes.execute_backend(
            backend, entity.hass, config, call.tool_args, None, []
        )


def rest_config(state, *, scrape=False):
    config = {
        "type": "scrape" if scrape else "rest",
        "method": "POST",
        "resource": state.resource,
        "payload": "1",
    }
    if scrape:
        config["sensor"] = [{"select": "p"}]
    return config


@pytest.mark.parametrize("failure", ["error", "cancel"])
async def test_overridden_precise_backend_defaults_to_conservative_ownership(
    hass, failure
):
    effects = []

    class OpaqueRest(web.RestFunction):
        async def execute(self, *_args):
            effects.append("effect")
            if len(effects) == 1:
                if failure == "cancel":
                    raise asyncio.CancelledError
                raise TimeoutError("effect acknowledged too late")
            return "ok"

    entity, log = SimpleNamespace(hass=hass), _chat_log(hass)
    backend, config = OpaqueRest(), {"type": "rest"}
    error = (
        asyncio.CancelledError
        if failure == "cancel"
        else SanitizedUnexpectedBackendError
    )
    with pytest.raises(error):
        await dispatch(entity, log, backend, config, name="opaque", call_id="first")
    remember_unacknowledged_calls(entity, log, set())
    blocked = False
    try:
        await dispatch(entity, log, backend, config, name="opaque", call_id="retry")
    except HomeAssistantError as err:
        assert "not acknowledged" in str(err)
        blocked = True
    assert effects == ["effect"], (
        "an opaque override must not repeat an uncertain effect"
    )
    assert blocked
    clear_unacknowledged_calls(entity, log.conversation_id)
    await dispatch(entity, log, backend, config, name="opaque", call_id="acknowledged")
    assert effects == ["effect", "effect"]


async def cross_wrapper_witness(hass, http_boundary):
    entity, log = SimpleNamespace(hass=hass), _chat_log(hass)
    scrape, rest = web.ScrapeFunction(), web.RestFunction()
    scrape_config = scrape.validate_schema(rest_config(http_boundary, scrape=True))
    retry_config = rest.validate_schema(rest_config(http_boundary))
    assert (
        await dispatch(
            entity, log, scrape, scrape_config, name="scrape_effect", call_id="first"
        )
        == "ok"
    )
    assert http_boundary.received == [b"1"]
    remember_unacknowledged_calls(entity, log, set())
    blocked = False
    try:
        await dispatch(
            entity, log, rest, retry_config, name="rest_effect", call_id="retry"
        )
    except HomeAssistantError as err:
        assert "not acknowledged" in str(err)
        blocked = True
    assert http_boundary.received == [b"1"], (
        "the same HTTP effect must not repeat through another backend"
    )
    assert blocked
    clear_unacknowledged_calls(entity, log.conversation_id)
    await dispatch(
        entity, log, rest, retry_config, name="rest_effect", call_id="acknowledged"
    )
    assert http_boundary.received == [b"1", b"1"]


async def test_scrape_http_receipt_prevents_replay_through_rest(hass, http_boundary):
    await cross_wrapper_witness(hass, http_boundary)


async def test_shared_http_boundary_sensitivity_detects_duplicate_posts(
    hass, http_boundary, monkeypatch
):
    monkeypatch.setattr(web, "execution_started", lambda **_kwargs: None)
    with pytest.raises(AssertionError, match="same HTTP effect must not repeat"):
        await cross_wrapper_witness(hass, http_boundary)
    assert http_boundary.received == [b"1", b"1"]


@pytest.mark.parametrize("failure", ["timeout", "cancel"])
async def test_shared_http_without_caller_flag_preserves_uncertain_dispatch(
    hass, http_boundary, failure
):
    entity, log = SimpleNamespace(hass=hass), _chat_log(hass)
    config = rest_config(http_boundary)
    http_boundary.hold = True

    async def execute(_hass, _config, arguments, *_args):
        data = web.get_rest_data(hass, _config, arguments)
        if failure == "timeout":
            data._timeout = aiohttp.ClientTimeout(total=0.05)
        await data.async_update()
        if data.last_exception:
            raise HomeAssistantError("HTTP outcome unknown")
        return data.data

    backend = SimpleNamespace(execute=execute)
    task = asyncio.create_task(
        dispatch(entity, log, backend, config, name="shared_http", call_id="first")
    )
    try:
        await asyncio.wait_for(http_boundary.entered.wait(), 3)
        if failure == "cancel":
            task.cancel()
        with pytest.raises(
            asyncio.CancelledError if failure == "cancel" else HomeAssistantError
        ):
            await task
    finally:
        http_boundary.hold = False
        http_boundary.release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    assert http_boundary.received == [b"1"]
    remember_unacknowledged_calls(entity, log, set())
    rest = web.RestFunction()
    blocked = False
    try:
        await dispatch(
            entity,
            log,
            rest,
            rest.validate_schema(config),
            name="rest_effect",
            call_id="retry",
        )
    except HomeAssistantError as err:
        assert "not acknowledged" in str(err)
        blocked = True
    assert http_boundary.received == [b"1"], (
        "uncertain shared HTTP dispatch must survive caller changes"
    )
    assert blocked


async def predispatch_witness(hass, http_boundary):
    entity, log = SimpleNamespace(hass=hass), _chat_log(hass)
    rest = web.RestFunction()
    invalid = rest_config(http_boundary)
    invalid.pop("resource")
    invalid["resource_template"] = "{{ missing.child }}"
    with pytest.raises(HomeAssistantError):
        await dispatch(
            entity,
            log,
            rest,
            rest.validate_schema(invalid),
            name="rest_effect",
            call_id="invalid",
        )
    assert http_boundary.received == []
    remember_unacknowledged_calls(entity, log, set())
    assert not was_unacknowledged_equivalent(
        entity, log, _call("corrected", "rest_effect")
    ), "predispatch failures must allow a corrected provider retry"
    await dispatch(
        entity,
        log,
        rest,
        rest.validate_schema(rest_config(http_boundary)),
        name="rest_effect",
        call_id="corrected",
    )
    assert http_boundary.received == [b"1"]


async def test_real_rest_predispatch_failure_allows_corrected_request(
    hass, http_boundary
):
    await predispatch_witness(hass, http_boundary)


async def test_callable_contract_sensitivity_detects_false_uncertainty(
    hass, http_boundary, monkeypatch
):
    monkeypatch.setattr(
        web.RestFunction.execute, "_eoai_owns_execution_receipts", False
    )
    with pytest.raises(AssertionError, match="predispatch failures must allow"):
        await predispatch_witness(hass, http_boundary)
    assert http_boundary.received == []


async def test_unchanged_inherited_execute_keeps_precise_boundary_ownership(
    hass, http_boundary
):
    class InheritedRest(web.RestFunction):
        pass

    entity, log = SimpleNamespace(hass=hass), _chat_log(hass)
    backend = InheritedRest()
    config = backend.validate_schema(rest_config(http_boundary))
    await dispatch(entity, log, backend, config, name="inherited_rest", call_id="first")
    assert http_boundary.received == [b"1"]
    call = log.content[-1].tool_calls[0]
    assert [
        receipt.kind for receipt in entity._execution_effect_receipts[id(call)]
    ] == ["rest"]


async def test_scrape_keeps_opaque_guard_for_coordinator_owned_effects(
    hass, http_boundary, monkeypatch
):
    effects = []

    class OpaqueCoordinator:
        def __init__(self, *_args):
            pass

        async def async_refresh(self):
            effects.append("opaque coordinator effect")
            raise HomeAssistantError("coordinator acknowledgement unknown")

    monkeypatch.setattr(web.scrape.coordinator, "ScrapeCoordinator", OpaqueCoordinator)
    entity, log = SimpleNamespace(hass=hass), _chat_log(hass)
    backend = web.ScrapeFunction()
    config = backend.validate_schema(rest_config(http_boundary, scrape=True))
    with pytest.raises(HomeAssistantError):
        await dispatch(entity, log, backend, config, name="scrape", call_id="first")
    remember_unacknowledged_calls(entity, log, set())
    with pytest.raises(HomeAssistantError, match="not acknowledged"):
        await dispatch(entity, log, backend, config, name="scrape", call_id="retry")
    assert effects == ["opaque coordinator effect"]
    assert http_boundary.received == []
