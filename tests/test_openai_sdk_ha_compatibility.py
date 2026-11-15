"""SDK compatibility with Home Assistant's shared legacy HTTPX transport."""

import asyncio
import json
from pathlib import Path
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from packaging.requirements import Requirement
import pytest

from custom_components.extended_openai_conversation_responses import helpers
from custom_components.extended_openai_conversation_responses.debug import (
    DebugOpenAIClientProxy,
)


@pytest.mark.parametrize("provider", ["openai", "azure"])
@pytest.mark.parametrize("skip_authentication", [False, True])
async def test_sdk_resources_prepared_off_loop_before_debug_wrapper(
    monkeypatch, provider, skip_authentication
):
    """Use real cached SDK resources and transport, with a genuine worker thread."""
    from openai import AsyncOpenAI
    from openai.resources.chat import AsyncChat

    loop_thread = get_ident()
    initialized = []
    requests = []

    def observe(cls, name):
        descriptor = vars(cls)[name]

        def get(instance):
            if name not in vars(instance):
                initialized.append((name, get_ident()))
            return descriptor.__get__(instance, type(instance))

        monkeypatch.setattr(cls, name, property(get))

    for name in ("responses", "chat", "embeddings", "models"):
        observe(AsyncOpenAI, name)
    observe(AsyncChat, "completions")

    async def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"object": "list", "data": []})

    async def executor(function, *args):
        return await asyncio.to_thread(function, *args)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as shared:
        monkeypatch.setattr(helpers, "get_async_client", lambda _hass: shared)
        client = await helpers.get_authenticated_client(
            SimpleNamespace(async_add_executor_job=executor),
            "test-key",
            "https://mock.invalid/v1",
            "2024-10-21" if provider == "azure" else None,
            "test-org",
            provider,
            skip_authentication=skip_authentication,
        )
        assert [name for name, _ in initialized] == [
            "responses", "chat", "completions", "embeddings", "models"
        ]
        assert all(thread != loop_thread for _, thread in initialized)
        before_wrapper = list(initialized)
        wrapped = DebugOpenAIClientProxy(client)
        assert initialized == before_wrapper
        assert wrapped.responses._delegate is client.responses
        assert wrapped.chat.completions._delegate is client.chat.completions
        assert wrapped.embeddings._delegate is client.embeddings
        assert client._client is shared
        assert not shared.is_closed
        assert len(requests) == (0 if skip_authentication else 1)


@pytest.mark.parametrize("stage", ["constructor", "headers", "resources"])
@pytest.mark.parametrize("skip_authentication", [False, True])
async def test_required_sdk_initialization_errors_propagate(
    monkeypatch, stage, skip_authentication
):
    """Required initialization is not an optional warm-up failure."""
    from openai import AsyncOpenAI, OpenAIError

    error = OpenAIError("initialization failed")
    optional_warmup = Mock()
    monkeypatch.setattr(helpers, "_warm_response_stream_adapter", optional_warmup)

    def fail(*_args, **_kwargs):
        raise error

    if stage == "constructor":
        monkeypatch.setattr(helpers, "AsyncOpenAI", fail)
    elif stage == "headers":
        monkeypatch.setattr(AsyncOpenAI, "platform_headers", fail)
    else:
        monkeypatch.setattr(AsyncOpenAI, "responses", property(fail))

    async def executor(function, *args):
        return await asyncio.to_thread(function, *args)

    async with httpx.AsyncClient() as shared:
        monkeypatch.setattr(helpers, "get_async_client", lambda _hass: shared)
        with pytest.raises(OpenAIError) as caught:
            await helpers.get_authenticated_client(
                SimpleNamespace(async_add_executor_job=executor),
                "test-key", None, None, None, "openai",
                skip_authentication=skip_authentication,
            )
        assert caught.value is error
        assert not shared.is_closed
        optional_warmup.assert_not_called()


async def test_stream_adapter_warmup_reuses_real_stream_validator(monkeypatch):
    """The exact cache used by real SSE decoding is filled on the worker."""
    from typing import get_args

    from openai import _models
    from openai.types.responses import ResponseStreamEvent, ResponseTextDeltaEvent
    from pydantic import ValidationError

    from tests.test_openai_sdk_wire import _close_client

    factory = _models.TypeAdapter
    if not callable(getattr(factory, "cache_info", None)):
        pytest.skip("SDK does not cache validation adapters; optimization is skipped")
    factory.cache_clear()
    threads = []

    def tracked_factory(type_):
        threads.append(get_ident())
        return factory(type_)

    tracked_factory.cache_info = factory.cache_info
    monkeypatch.setattr(_models, "TypeAdapter", tracked_factory)
    loop_thread = get_ident()
    event = {
        "type": "response.output_text.delta", "delta": "Hello",
        "item_id": "msg_test", "content_index": 0, "output_index": 0,
        "sequence_number": 0, "logprobs": [],
    }
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode(),
        )

    async def executor(function, *args):
        return await asyncio.to_thread(function, *args)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as shared:
        monkeypatch.setattr(helpers, "get_async_client", lambda _hass: shared)
        client = await helpers.get_authenticated_client(
            SimpleNamespace(async_add_executor_job=executor),
            "test-key", "https://mock.invalid/v1", None, None, "openai",
            skip_authentication=True,
        )
        assert not requests
        assert threads and all(thread != loop_thread for thread in threads)
        warmed = factory.cache_info()
        assert warmed.misses == 1 and warmed.currsize == 1
        # construct_type unwraps the Annotated event union before validation.
        adapter = factory(get_args(ResponseStreamEvent)[0])
        assert factory.cache_info().misses == warmed.misses
        with pytest.raises(ValidationError):
            adapter.validate_python({"type": "response.output_text.delta"})
        for _ in range(2):
            stream = await client.responses.create(model="gpt-test", input="Hi", stream=True)
            async with stream:
                events = [item async for item in stream]
            assert len(events) == 1
            assert isinstance(events[0], ResponseTextDeltaEvent)
            assert events[0].delta == "Hello"
        assert len(requests) == 2
        assert factory.cache_info().misses == warmed.misses
        assert factory.cache_info().hits >= warmed.hits + 3
        assert threads[-1] == loop_thread
        await _close_client(client)


@pytest.mark.parametrize("skip_authentication", [False, True])
@pytest.mark.parametrize("incompatibility", [
    "missing_factory", "missing_event", "uncached_factory", "different_type", "construction_error"
])
async def test_optional_adapter_incompatibility_does_not_break_setup(
    monkeypatch, caplog, skip_authentication, incompatibility
):
    from openai import _models
    from openai.types import responses

    from tests.test_openai_sdk_wire import _close_client

    factory = Mock(side_effect=RuntimeError("optional optimization failed"))
    if incompatibility != "uncached_factory":
        factory.cache_info = lambda: None
    else:
        # Mock auto-attributes would otherwise look like an SDK cache.
        def uncached_factory(*_args):
            pytest.fail("uncached factory should be skipped")

        factory = uncached_factory

    requests = []

    async def respond(request):
        requests.append(request)
        if request.url.path.endswith("/responses"):
            event = {
                "type": "response.output_text.delta", "delta": "Still streaming",
                "item_id": "msg_test", "content_index": 0, "output_index": 0,
                "sequence_number": 0, "logprobs": [],
            }
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                content=f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode(),
            )
        return httpx.Response(200, json={"object": "list", "data": []})

    async def executor(function, *args):
        return await asyncio.to_thread(function, *args)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as shared:
        monkeypatch.setattr(helpers, "get_async_client", lambda _hass: shared)
        with monkeypatch.context() as internals:
            if incompatibility == "missing_factory":
                internals.delattr(_models, "TypeAdapter")
            elif incompatibility == "missing_event":
                internals.delattr(responses, "ResponseStreamEvent")
            elif incompatibility == "different_type":
                internals.setattr(responses, "ResponseStreamEvent", int)
            else:
                internals.setattr(_models, "TypeAdapter", factory)
            client = await helpers.get_authenticated_client(
                SimpleNamespace(async_add_executor_job=executor),
                "test-key", "https://mock.invalid/v1", None, None, "openai",
                skip_authentication=skip_authentication,
            )
        assert client._client is shared and not shared.is_closed
        assert len(requests) == (0 if skip_authentication else 1)
        assert not [record for record in caplog.records if record.levelno >= 30]
        if incompatibility == "construction_error":
            factory.assert_called_once()
        stream = await client.responses.create(model="gpt-test", input="Hi", stream=True)
        async with stream:
            events = [item async for item in stream]
        assert len(events) == 1 and events[0].delta == "Still streaming"
        assert len(requests) == (1 if skip_authentication else 2)
        await _close_client(client)


@pytest.mark.parametrize("sdk", ["2.21.0", "2.45.0", "3.10.0", "3.26.0"])
def test_manifest_allows_supported_ha_pins_and_bounds_unvalidated_sdk(sdk):
    manifest = json.loads(
        (
            Path(__file__).parents[1]
            / "custom_components/extended_openai_conversation_responses/manifest.json"
        ).read_text()
    )
    requirement = next(
        Requirement(value)
        for value in manifest["requirements"]
        if Requirement(value).name == "openai"
    )
    assert requirement.specifier.contains(sdk)
    assert not requirement.specifier.contains("2.20.0")
    assert not requirement.specifier.contains("3.26.1")
    assert not requirement.specifier.contains("4.0.0")


def test_sdk_matrix_selects_compatible_ha_snapshots_and_enforces_constraints():
    import yaml

    path = Path(__file__).parents[1] / ".github/workflows/openai-sdk-compatibility.yml"
    job = yaml.safe_load(path.read_text())["jobs"]["sdk-contract"]
    pairs = {row["sdk"]: row["ha"] for row in job["strategy"]["matrix"]["include"]}
    assert pairs == {"2.21.0": "2026.7.0", "2.45.0": "2026.8.0", "3.10.0": "2026.10.0", "3.26.0": "stable", "latest-3x-early-warning": "stable"}
    installs = [step.get("run", "") for step in job["steps"] if "pip install --upgrade" in step.get("run", "")]
    assert len(installs) == 1
    assert '--constraint "$CONSTRAINTS" "$SPEC"' in installs[0]
    assert "package_constraints.txt" in installs[0]
    preparation = next(step["run"] for step in job["steps"] if "EOAI_TEST_REQUIREMENTS" in step.get("run", ""))
    assert 'python -m venv "$RUNNER_TEMP/sdk-venv"' in preparation
    assert 'bash ci/reconcile_stable_environment.sh' in preparation


@pytest.mark.parametrize("provider", ["openai", "azure"])
async def test_real_sdk_authentication_uses_ha_transport_once(
    hass, monkeypatch, provider
):
    requests = []

    async def models(request):
        requests.append(request)
        return httpx.Response(
            200,
            request=request,
            json={
                "object": "list",
                "data": [
                    {
                        "id": "gpt-test",
                        "object": "model",
                        "created": 0,
                        "owned_by": "test",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(models)
    ) as shared_client:
        monkeypatch.setattr(helpers, "get_async_client", lambda _hass: shared_client)
        client = await helpers.get_authenticated_client(
            hass,
            "test-key",
            "https://mock.invalid/v1",
            "2024-10-21" if provider == "azure" else None,
            None,
            provider,
        )
        assert client._client is shared_client
        assert len(requests) == 1
        if provider == "azure":
            assert requests[0].url.path == "/v1/openai/models"
            assert requests[0].url.params["api-version"] == "2024-10-21"
            assert requests[0].headers["api-key"] == "test-key"
        else:
            assert requests[0].url.path == "/v1/models"
            assert requests[0].headers["authorization"] == "Bearer test-key"


@pytest.mark.parametrize("gazetteer", [False, True])
@pytest.mark.parametrize("sdk", ["2.21.0", "2.45.0", "3.10.0", "3.26.0"])
def test_ci_dependency_install_respects_selected_ha_constraints(
    tmp_path, monkeypatch, sdk, gazetteer
):
    from types import SimpleNamespace

    from ci import install_ha_dependencies

    ha_package = tmp_path / "homeassistant"
    (ha_package / "components" / "conversation").mkdir(parents=True)
    constraints = ha_package / "package_constraints.txt"
    constraints.write_text(
        f"openai=={sdk}\nvoluptuous-openapi==0.2.0\npydantic==2.13.5\n"
    )
    (ha_package / "components" / "conversation" / "manifest.json").write_text(json.dumps({"requirements": ["gazetteer-matcher==1.1.0"] if gazetteer else []}))
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "requirements": [
                    "openai>=2.21.0,<=3.26.0",
                    "voluptuous-openapi==0.4.1",
                ],
                "dependencies": ["conversation"],
            }
        )
    )
    monkeypatch.setattr(
        install_ha_dependencies.homeassistant, "__path__", [str(ha_package)]
    )
    monkeypatch.setattr(
        install_ha_dependencies,
        "_parse_args",
        lambda: SimpleNamespace(manifest=manifest),
    )
    commands = []
    captured_constraints = []
    monkeypatch.setattr(
        install_ha_dependencies,
        "requires",
        lambda _name: [
            "pydantic==2.13.5",
            "aiohttp[speedups]>=3.9",
            "voluptuous-openapi==0.2.0",
            "unavailable==1; extra == 'unused'",
        ],
    )

    def capture(command):
        commands.append(command)
        captured_constraints.append(
            Path(command[command.index("--constraint") + 1]).read_text()
        )

    monkeypatch.setattr(install_ha_dependencies.subprocess, "check_call", capture)
    install_ha_dependencies.main()
    assert len(commands) == 1
    command = commands[0]
    assert captured_constraints == [
        f"pydantic==2.13.5\naiohttp>=3.9\nvoluptuous-openapi==0.2.0\nopenai=={sdk}\nvoluptuous-openapi==0.2.0\npydantic==2.13.5\n"
    ]
    assert ("gazetteer-matcher==1.1.0" in command) == gazetteer
    assert "voluptuous-openapi==0.4.1" in command
    assert "openai>=2.21.0,<=3.26.0" in command
