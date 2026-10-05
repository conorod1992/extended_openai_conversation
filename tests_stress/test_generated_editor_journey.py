"""Generated programmes cross native editors, public Assist and a fresh HA process."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import signal
import sys

from aiohttp import ClientSession, web
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.functions.behaviour_generators import (  # noqa: E402
    assert_typed_value,
    schema_cases,
    script_cases,
)
from tests_real_ha.test_browser_backend_acceptance import _run_playwright  # noqa: E402
from tests_real_ha.test_browser_process_restart_acceptance import (  # noqa: E402
    _AUTH_FILE,
    _CHILD_ENV,
    _CONFIG_DIR_ENV,
    _GENERATION_ENV,
    _SYNC_DIR_ENV,
    DOMAIN,
    _ensure_browser_auth,
    _free_port,
    _read_log,
    _start_ha_child,
    _stop_child,
    _wait_for_process_marker,
    _wait_for_stored_values,
    _write_onboarding_store,
)
from tests_real_ha.test_provider_wire_e2e import (  # noqa: E402
    _chat_sse_text,
    _chat_sse_tool_call,
    _responses_sse_text,
    _responses_sse_tool_call,
)
from tests_stress.conftest import record  # noqa: E402
from tests_stress.test_shared_runtime_request_lifetimes import _endpoint  # noqa: E402

TEXTS = [
    "Cafe\u0301 café\u00a0🙂",
    "Grüße Straße 🧭",
    "日本語 東京 🏠",
    "مرحبا بالعالم ✨",
]


def programmes(seed):
    """Reviewed leaf witnesses plus seeded nesting; expected values remain independent."""
    schemas = list(schema_cases(seed))
    scripts = list(script_cases(seed))
    result = []
    for index, (language, witness) in enumerate(
        zip(
            ("fr", "de", "ja", "ar"),
            ("false-coercion", "empty-array", "nullable", "integral-float"),
            strict=True,
        )
    ):
        case = next(
            case
            for case in schemas
            if witness in case.witness and "depth=2" in case.witness
        )
        sequence, effects, business, marker = deepcopy(scripts[index])
        caption = TEXTS[index]
        case.schema["properties"]["caption"] = {
            "type": "string",
            "maxLength": len(caption),
        }
        case.schema["required"].append("caption")
        case.arguments["caption"] = caption
        case.expected["caption"] = caption
        sequence.insert(
            0,
            {
                "action": "script_probe.capture",
                "data": {"payload": "{{ payload }}", "caption": "{{ caption }}"},
            },
        )
        sequence.insert(
            1,
            {
                "enabled": False,
                "action": "script_probe.record",
                "data": {"marker": "forbidden-disabled"},
            },
        )
        name = f"editor_generated_{index}"
        tool = {
            "spec": {"name": name, "description": caption, "parameters": case.schema},
            "function": {"type": "script", "sequence": sequence},
        }
        result.append(
            {
                "tool": tool,
                "yaml": yaml.safe_dump(tool, allow_unicode=True, sort_keys=False),
                "arguments": case.arguments,
                "expected": case.expected,
                "effects": effects,
                "business": business,
                "caption": caption,
                "language": language,
                "marker": marker,
                "rule_phrase": "Route " + caption,
                "rule_name": "Règle " + caption,
                "witness": case.witness,
            }
        )
    return result


def test_programme_oracle_rejects_dropped_falsy_or_unicode_values():
    for seed in (0xC0FFEE, 149526):
        cases = programmes(seed)
        assert len(cases) == 4
        for case in cases:
            assert yaml.safe_load(case["yaml"]) == case["tool"]
            assert len(case["caption"].encode("utf-8")) > len(case["caption"])
            with pytest.raises(AssertionError):
                assert_typed_value({"payload": True}, case["expected"])
            assert any(
                action.get("enabled") is False
                for action in case["tool"]["function"]["sequence"]
            )


@pytest.mark.parametrize("oversized", [False, True])
async def test_utf8_rest_limits_count_bytes_separately_from_schema_characters(
    hass, socket_enabled, oversized, stress_trace
):
    from custom_components.extended_openai_conversation_responses.functions.web import (
        RestFunction,
    )
    from custom_components.extended_openai_conversation_responses.resource_limits import (
        MAX_REMOTE_RESPONSE_BYTES,
    )
    from homeassistant.exceptions import HomeAssistantError

    del socket_enabled
    text = "🙂" * (MAX_REMOTE_RESPONSE_BYTES // 4 + int(oversized))
    assert len(text) < MAX_REMOTE_RESPONSE_BYTES
    assert len(text.encode("utf-8")) == MAX_REMOTE_RESPONSE_BYTES + 4 * int(oversized)

    async def response(request):
        return web.Response(text=text)

    async def unused(request):
        raise AssertionError("REST byte probe unexpectedly called the provider")

    runner, endpoint = await _endpoint(unused, response)
    try:
        function = RestFunction()
        config = function.validate_schema(
            {"type": "rest", "resource": endpoint + "/neighbour"}
        )
        if oversized:
            with pytest.raises(
                HomeAssistantError,
                match="Remote response exceeds the configured safety limit",
            ):
                await function.execute(hass, config, {}, None, [])
        else:
            assert await function.execute(hass, config, {}, None, []) == text
        record(
            stress_trace,
            "summary",
            unicode_byte_boundary_cases=1,
            body_bytes=len(text.encode("utf-8")),
            characters=len(text),
        )
    finally:
        await runner.cleanup()


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_HA_BROWSER") != "1",
    reason="requires the native HA browser runtime",
)
@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
async def test_generated_programmes_survive_native_edit_and_process_restart(
    tmp_path, socket_enabled, stress_seed, stress_trace, mode
):
    del socket_enabled
    cases = programmes(stress_seed)
    wire = []
    functions = {case["tool"]["spec"]["name"]: case for case in cases}

    async def provider(request):
        body = await request.json()
        wire.append(body)
        serialized = json.dumps(
            [
                item
                for item in body.get("messages", body.get("input", []))
                if item.get("role") == "user"
            ],
            ensure_ascii=False,
        )
        case = next(
            case
            for name, case in functions.items()
            if name in serialized and case["caption"] in serialized
        )
        is_result = any(
            item.get("role") == "tool" or item.get("type") == "function_call_output"
            for item in body.get("messages", body.get("input", []))
        )
        if is_result:
            returned = next(
                item
                for item in body.get("messages", body.get("input", []))
                if item.get("role") == "tool"
                or item.get("type") == "function_call_output"
            )
            assert_typed_value(
                json.loads(returned.get("content", returned.get("output")))["result"],
                case["business"],
            )
            payload = (
                _chat_sse_text if mode == "chat_completions" else _responses_sse_text
            )(case["caption"])
        else:
            payload = (
                _chat_sse_tool_call
                if mode == "chat_completions"
                else _responses_sse_tool_call
            )("editor-call", case["tool"]["spec"]["name"], case["arguments"])
        return web.Response(body=payload, content_type="text/event-stream")

    runner, endpoint = await _endpoint(provider)
    config = tmp_path / "ha-config"
    destination = config / "custom_components" / DOMAIN
    destination.parent.mkdir(parents=True)
    shutil.copytree(ROOT / "custom_components" / DOMAIN, destination)
    port = _free_port()
    (config / "configuration.yaml").write_text(
        f"homeassistant:\n  name: Generated native editor acceptance\nhttp:\n  server_host: 127.0.0.1\n  server_port: {port}\nfrontend:\nwebsocket_api:\napi:\n",
        encoding="utf-8",
    )
    _write_onboarding_store(config)
    sync = tmp_path / "sync"
    sync.mkdir()
    (sync / "programmes.json").write_text(
        json.dumps(cases, ensure_ascii=False), encoding="utf-8"
    )
    (sync / "runtime.json").write_text(
        json.dumps({"endpoint": endpoint, "mode": mode, "port": port}), encoding="utf-8"
    )
    children = []
    try:
        for generation in (1, 2):
            log = tmp_path / f"ha-{generation}.log"
            child, handle = await _start_ha_child(
                test_file=Path(__file__),
                config_dir=config,
                sync_dir=sync,
                port=port,
                generation=generation,
                log_file=log,
            )
            children.append((child, handle))
            try:
                await _wait_for_process_marker(
                    child, sync / f"ha-ready-{generation}", timeout=120
                )
            except (TimeoutError, AssertionError) as err:
                raise AssertionError(_read_log(log)) from err
            auth = json.loads((sync / _AUTH_FILE).read_text())
            base = f"http://127.0.0.1:{port}"
            await _run_playwright(
                repo_root=ROOT,
                spec="tests_browser/real-ha-generated-programmes.spec.mjs",
                config="playwright.real-ha-shell.config.mjs",
                env={
                    "REAL_HA_FRONTEND_URL": base,
                    "REAL_HA_FRONTEND_AUTH": json.dumps(auth),
                    "EOAI_GENERATED_CASES": str(sync / "programmes.json"),
                    "EOAI_GENERATED_PHASE": "author" if generation == 1 else "reopen",
                    "PLAYWRIGHT_ARTIFACT_SUFFIX": f"generated-{mode}-{generation}",
                },
                failure_label="Generated native editor acceptance failed",
            )
            async with ClientSession(
                headers={"Authorization": "Bearer " + auth["access_token"]}
            ) as client:
                async with client.get(
                    base + "/api/states/sensor.editor_acceptance"
                ) as response:
                    state = await response.json()
                agent_id = state["attributes"]["agent_id"]
                for case in cases:
                    for text, expected in (
                        (
                            case["tool"]["spec"]["name"] + " " + case["caption"],
                            case["caption"],
                        ),
                        (case["rule_phrase"], "Routed " + case["caption"]),
                    ):
                        async with client.post(
                            base + "/api/conversation/process",
                            json={
                                "text": text,
                                "language": case["language"],
                                "agent_id": agent_id,
                            },
                        ) as response:
                            assert response.status == 200
                            result = await response.json()
                        assert (
                            result["response"]["speech"]["plain"]["speech"] == expected
                        ), result
                    async with client.get(
                        base + "/api/states/sensor.editor_acceptance"
                    ) as response:
                        state = await response.json()
                    observed = state["attributes"]
                    assert_typed_value(
                        observed["captured"][-1]["payload"], case["expected"]["payload"]
                    )
                    assert observed["captured"][-1]["caption"] == case["caption"]
                    assert (
                        observed["effects"][-len(case["effects"]) :] == case["effects"]
                    )
                    assert not any(
                        value.startswith("forbidden-") for value in observed["effects"]
                    )
            record(
                stress_trace,
                "native_public_generation",
                generation=generation,
                mode=mode,
                languages=[case["language"] for case in cases],
                witnesses=[case["witness"] for case in cases],
                provider_requests=len(wire),
            )
            await _stop_child(child, handle, kill=False)
            stored = json.loads(
                (config / ".storage" / "core.config_entries").read_text()
            )
            saved_entry = next(
                item for item in stored["data"]["entries"] if item["domain"] == DOMAIN
            )
            saved_agent = next(
                item
                for item in saved_entry["subentries"]
                if item["subentry_type"] == "conversation"
            )
            saved_tools = saved_agent["data"]["functions"]
            if isinstance(saved_tools, str):
                saved_tools = yaml.safe_load(saved_tools)
            for case in cases:
                expected = deepcopy(case["tool"])
                expected["spec"]["description"] = (
                    "Reviewed harmless edit " + case["caption"]
                )
                saved = next(
                    tool
                    for tool in saved_tools
                    if tool["spec"]["name"] == expected["spec"]["name"]
                )
                assert_typed_value(
                    {"spec": saved["spec"], "function": saved["function"]}, expected
                )
        assert len(wire) == 16
        record(
            stress_trace,
            "summary",
            generated_editor_programmes=16,
            multilingual_public_journeys=8,
            generated_editor_restarts=1,
            unicode_character_boundary_cases=8,
        )
    finally:
        for child, handle in children:
            if not handle.closed:
                await _stop_child(child, handle, kill=True)
        await runner.cleanup()


async def child_main():
    """The harness stays outside this genuinely booted, persisted HA installation."""
    from homeassistant import bootstrap, runner
    from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
    from homeassistant.core import SupportsResponse
    from homeassistant.helpers import entity_registry as er, recorder as recorder_helper

    config = Path(os.environ[_CONFIG_DIR_ENV]).resolve()
    sync = Path(os.environ[_SYNC_DIR_ENV])
    runtime = json.loads((sync / "runtime.json").read_text())
    sys.path.insert(0, str(config))
    hass = await bootstrap.async_setup_hass(
        runner.RuntimeConfig(config_dir=str(config), skip_pip=False)
    )
    assert hass is not None
    if recorder_helper.DATA_RECORDER not in hass.data:
        recorder_helper.async_initialize_recorder(hass)
    effects, captured = [], []

    def publish():
        hass.states.async_set(
            "sensor.editor_acceptance",
            "ready",
            {
                "effects": list(effects),
                "captured": list(captured),
                "agent_id": agent_id,
            },
        )

    async def effect(call):
        effects.append(call.data["marker"])
        publish()

    async def capture(call):
        captured.append(dict(call.data))
        publish()

    async def business(call):
        return {"count": 0, "flag": False, "empty": []}

    hass.services.async_register("script_probe", "record", effect)
    hass.services.async_register("script_probe", "capture", capture)
    hass.services.async_register(
        "script_probe", "response", business, supports_response=SupportsResponse.ONLY
    )
    await hass.async_start()
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        flow = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            {
                "name": "Generated native editor acceptance",
                "api_key": "sk-editor-acceptance",
                "base_url": runtime["endpoint"] + "/v1",
                "skip_authentication": True,
                "api_provider": "openai",
            },
        )
        entry = result["result"]
        await hass.async_block_till_done()
        subentry = next(
            item
            for item in entry.subentries.values()
            if item.subentry_type == "conversation"
        )
        hass.config_entries.async_update_subentry(
            entry,
            subentry,
            data={
                **subentry.data,
                "api_mode": runtime["mode"],
                "chat_model": "gpt-5.6",
                "reasoning_effort": "none",
                "functions": [],
                "current_datetime_enabled": False,
            },
        )
        await hass.async_block_till_done()
    else:
        entry = entries[0]
    assert entry.state is ConfigEntryState.LOADED
    subentry = next(
        item
        for item in entry.subentries.values()
        if item.subentry_type == "conversation"
    )
    agent_id = next(
        entity.entity_id
        for entity in er.async_get(hass).entities.values()
        if entity.config_entry_id == entry.entry_id
        and entity.domain == "conversation"
        and entity.config_subentry_id == subentry.subentry_id
    )
    publish()
    await _ensure_browser_auth(hass, sync, f"http://127.0.0.1:{runtime['port']}")
    await _wait_for_stored_values(
        config / ".storage" / "auth",
        json.loads((sync / _AUTH_FILE).read_text())["refresh_token"],
    )
    (sync / f"ha-ready-{os.environ[_GENERATION_ENV]}").write_text("ready")
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    await stop.wait()
    await hass.async_stop()


if __name__ == "__main__" and os.environ.get(_CHILD_ENV) == "1":
    asyncio.run(child_main())
