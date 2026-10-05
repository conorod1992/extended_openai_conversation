"""Inspect application disclosures before the evidence uploader redacts anything."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
from zipfile import ZipFile, is_zipfile

from aiohttp import web
import pytest
from pytest_homeassistant_custom_component.common import MockUser

from ci.enhanced_evidence import CANARIES, redact_text, safe
from custom_components.extended_openai_conversation_responses import backup_transfer
from custom_components.extended_openai_conversation_responses.const import (
    CONF_API_MODE,
    CONF_ARCHIVE_ENABLED,
    CONF_CHAT_MODEL,
    CONF_FUNCTION_TOOLS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_MEMORY_MODE,
    CONF_PROMPT,
    CONF_SHARED_ARCHIVE_ENABLED,
    EVENT_CONVERSATION_FINISHED,
    MEMORY_MODE_MANUAL,
)
from custom_components.extended_openai_conversation_responses.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.components import conversation, recorder
from homeassistant.const import CONF_API_KEY
from homeassistant.core import Context
from homeassistant.setup import async_setup_component
from tests_real_ha.test_acceptance_lifecycle import _make_entry, _setup_entry
from tests_real_ha.test_backup_transfer_protocol import _transfer_call
from tests_real_ha.test_management_backend_acceptance import (
    _admin_client,
    _management_call,
    _management_response,
)
from tests_real_ha.test_provider_wire_e2e import (
    _chat_sse_text,
    _chat_sse_tool_call,
    _responses_sse_text,
    _responses_sse_tool_call,
    _speech,
)
from tests_real_ha.test_user_ownership_privacy import _normal_user_token
from tests_stress.conftest import record
from tests_stress.test_active_provider_stream_cancellation import _partial_text
from tests_stress.test_os_storage_faults import real_store_io  # noqa: F401
from tests_stress.test_shared_runtime_request_lifetimes import _cancel, _endpoint

CONTENT = {"prompt", "memory", "knowledge", "user", "argument", "aborted"}


def _markers(seed, mode):
    suffix = hashlib.sha256(f"application-privacy:{seed}:{mode}".encode()).hexdigest()[
        :16
    ]
    # Known upload canary prefixes protect failing pytest/log artifacts too. The
    # application oracle below always sees the untouched, uniquely marked values.
    return {
        "credential": CANARIES[0] + "-" + suffix,
        "prompt": CANARIES[3] + "-" + suffix,
        "knowledge": CANARIES[2] + "-" + suffix,
        **{
            key: CANARIES[1] + f"-{key}-{suffix}"
            for key in ("memory", "user", "argument", "aborted")
        },
    }


def _serialized(value):
    return (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, default=str)
    )


def _audit(markers, observations, permissions):
    violations = []
    for destination, value in observations.items():
        present = {
            name for name, marker in markers.items() if marker in _serialized(value)
        }
        unexpected = present - permissions.get(destination, set())
        if unexpected:
            # Keep failure evidence useful without inserting a private payload
            # into the assertion message or publishing the raw observations.
            violations.append((destination, sorted(unexpected)))
    assert not violations, f"Application destination disclosure: {violations}"


@pytest.mark.parametrize(
    "destination",
    ["logs", "diagnostics", "unrelated_store", "unauthorized_response", "unowned_temp"],
)
def test_application_oracle_detects_disclosure_before_upload_redaction(destination):
    markers = _markers(1, "sensitivity")
    raw = {destination: "Unexpected retained value " + markers["memory"]}
    with pytest.raises(AssertionError, match="Application destination disclosure"):
        _audit(markers, raw, {})
    # Upload redaction cannot serve as the application privacy oracle: this
    # sanitized view hides the very disclosure the raw inspection just rejected.
    uploaded = {destination: redact_text(raw[destination])}
    assert markers["memory"] not in uploaded[destination]
    _audit(markers, uploaded, {})


def test_authorized_retention_does_not_allow_credentials_or_unrelated_destinations():
    markers = _markers(2, "authorization")
    permissions = {"owner_response": CONTENT, "requested_export": CONTENT}
    _audit(
        markers,
        {"owner_response": markers["user"], "requested_export": markers["memory"]},
        permissions,
    )
    for destination, name in [
        ("owner_response", "credential"),
        ("requested_export", "credential"),
        ("unowned_temp", "memory"),
    ]:
        with pytest.raises(AssertionError, match="Application destination disclosure"):
            _audit(markers, {destination: markers[name]}, permissions)


def _active_files(root, database):
    """Inspect live logical files, never freed SQLite pages or journal remnants."""
    result = {}
    sqlite_names = {database.name, database.name + "-wal", database.name + "-shm"}
    for path in root.rglob("*"):
        if not path.is_file() or path.name in sqlite_names:
            continue
        relative = path.relative_to(root).as_posix()
        if is_zipfile(path):
            with ZipFile(path) as archive:
                result[relative] = "\n".join(
                    archive.read(name).decode("utf-8") for name in archive.namelist()
                )
        else:
            result[relative] = path.read_bytes().decode("utf-8", errors="replace")
    return result


def _recorder_records(database):
    """Only committed live rows are application retention, not forensic bytes."""
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        events = connection.execute(
            "SELECT t.event_type, d.shared_data FROM events e JOIN event_types t ON t.event_type_id=e.event_type_id LEFT JOIN event_data d ON d.data_id=e.data_id"
        ).fetchall()
        states = connection.execute("SELECT state FROM states").fetchall()
        attributes = connection.execute(
            "SELECT shared_attrs FROM state_attributes"
        ).fetchall()
    return events, states, attributes


@pytest.mark.parametrize("mode", ["chat_completions", "responses"])
@pytest.mark.usefixtures("real_store_io")
async def test_populated_journey_respects_raw_application_destinations(
    hass,
    hass_ws_client,
    socket_enabled,
    caplog,
    tmp_path,
    stress_seed,
    stress_trace,
    mode,
):
    del socket_enabled
    markers = _markers(stress_seed, mode)
    # Ordinary application logging, without opt-in third-party SQL/protocol DEBUG
    # traces. These are native logger levels, not an evidence sanitizer/filter.
    caplog.set_level(logging.INFO)
    caplog.set_level(logging.WARNING, logger="sqlalchemy.engine")
    caplog.set_level(logging.WARNING, logger="sqlalchemy.pool")
    database = tmp_path / "privacy-recorder.db"
    assert await async_setup_component(
        hass, "recorder", {"recorder": {"db_url": f"sqlite:///{database}"}}
    )
    owner = MockUser(id="private-destination-owner", is_owner=True).add_to_hass(hass)
    _, outsider_token = await _normal_user_token(
        hass, "private-destination-outsider", "Other user"
    )
    requests, headers, effects, completion_events = [], [], [], []
    stalled, release = asyncio.Event(), asyncio.Event()
    step = 0
    text = _responses_sse_text if mode == "responses" else _chat_sse_text
    call = _responses_sse_tool_call if mode == "responses" else _chat_sse_tool_call

    async def provider(request):
        nonlocal step
        body = await request.json()
        requests.append(body)
        headers.append(dict(request.headers))
        current = step
        step += 1
        if current == 0:
            payload = call(
                "private-echo-call",
                "echo_private_value",
                {"value": markers["argument"]},
            )
        elif current == 2:
            echoed = " ".join(
                marker for marker in markers.values() if marker in _serialized(body)
            )
            return web.json_response(
                {
                    "error": {
                        "message": "Rejected input "
                        + echoed
                        + " "
                        + markers["credential"],
                        "type": "invalid_request_error",
                        "code": "unsupported_parameter",
                    }
                },
                status=400,
            )
        elif current == 3:
            response = web.StreamResponse(headers={"content-type": "text/event-stream"})
            await response.prepare(request)
            await response.write(_partial_text(mode)[0])
            stalled.set()
            await release.wait()
            return response
        elif current == 6:
            payload = call(
                "private-knowledge-call",
                "knowledge_search",
                {"query": "Controlled private reference", "limit": 1},
            )
        else:
            payload = text(
                "Owner reply " + markers["user"] + " " + markers["argument"]
                if current == 1
                else "Healthy result"
            )
        return web.Response(body=payload, content_type="text/event-stream")

    async def effect(service_call):
        effects.append(service_call.data["value"])

    hass.services.async_register("private_destination_probe", "record", effect)
    tool = {
        "spec": {
            "name": "echo_private_value",
            "description": "Return a caller-provided business value",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        },
        "function": {
            "type": "script",
            "sequence": [
                {
                    "action": "private_destination_probe.record",
                    "data": {"value": "{{ value }}"},
                },
                {"variables": {"_function_result": "{{ value }}"}},
            ],
        },
    }
    runner, url = await _endpoint(provider)
    entry = _make_entry(
        "Private destination acceptance",
        include_ai_task=False,
        base_url=url + "/v1",
        data={CONF_API_KEY: markers["credential"]},
        conversation_options={
            CONF_API_MODE: mode,
            CONF_CHAT_MODEL: "gpt-5.6",
            CONF_PROMPT: "Controlled confidential context " + markers["prompt"],
            CONF_FUNCTION_TOOLS: [tool],
            CONF_MEMORY_MODE: MEMORY_MODE_MANUAL,
            CONF_MEMORY_AUTO_RETRIEVE_LIMIT: 3,
            CONF_KNOWLEDGE_ENABLED: True,
            CONF_ARCHIVE_ENABLED: True,
            CONF_SHARED_ARCHIVE_ENABLED: False,
        },
    )
    unsubscribe = hass.bus.async_listen(
        EVENT_CONVERSATION_FINISHED, lambda event: completion_events.append(event.data)
    )
    active = None
    export_id = export_path = None
    observations, permissions = {}, {}
    try:
        await _setup_entry(hass, entry)
        agent = conversation.async_get_agent(hass, entry.entry_id)
        admin = await _admin_client(hass, hass_ws_client, user_id=owner.id + "-manager")
        # Explicit scope preserves the actual authenticated request owner's memory
        # while an authorized administrator creates it through the native UI API.
        created = await _management_call(
            admin,
            entry=entry,
            section="memories",
            action="add",
            scope_id="user:" + owner.id,
            content="Private calibration token " + markers["memory"],
            category="preferences",
            key="calibration",
        )
        knowledge = await _management_call(
            admin,
            entry=entry,
            section="knowledge",
            action="create",
            title="Controlled private reference",
            content=markers["knowledge"],
            enabled=True,
        )

        async def say(value, user=owner.id):
            return await conversation.async_converse(
                hass=hass,
                text=value,
                conversation_id=None,
                context=Context(user_id=user),
                language="en",
                agent_id=entry.entry_id,
            )

        first = await say("Private calibration review " + markers["user"])
        assert (
            _speech(first)
            == "Owner reply " + markers["user"] + " " + markers["argument"]
        )
        assert effects == [markers["argument"]]
        assert all(
            markers[name] in _serialized(requests[0])
            for name in ("prompt", "memory", "user")
        )
        assert markers["argument"] in _serialized(requests[1])
        failed = await say("Reject private calibration review " + markers["user"])
        assert failed.response.error_code is not None
        assert markers["credential"] not in _serialized(failed.response.as_dict())
        active = asyncio.create_task(
            say("Aborted calibration review " + markers["aborted"])
        )
        await asyncio.wait_for(stalled.wait(), 10)
        await _cancel(active)
        release.set()
        # Recover through the same public consumer, before diagnostics/manager reads.
        assert _speech(await say("Healthy calibration review")) == "Healthy result"
        assert (
            _speech(
                await say(
                    "Healthy independent operation", "private-destination-outsider"
                )
            )
            == "Healthy result"
        )
        assert not any(
            markers[name] in _serialized(requests[5])
            for name in ("memory", "user", "argument", "aborted", "knowledge")
        )
        assert _speech(await say("Knowledge review")) == "Healthy result"
        assert markers["knowledge"] not in _serialized(requests[6])
        assert markers["knowledge"] in _serialized(requests[7])
        assert len(requests) == 8
        record(
            stress_trace,
            "public_journey_complete",
            mode=mode,
            provider_requests=8,
            cancelled_calls=1,
            recovered_calls=1,
        )

        observations.update(
            {
                "configuration": {
                    "entry": dict(entry.data),
                    "agent": dict(agent.subentry.data),
                },
                "provider_headers": headers,
                "provider_requests": requests,
                "owner_memory_creation": created,
                "owner_response": [first.response.as_dict(), failed.response.as_dict()],
                "native_effect": effects,
                "declared_ha_events": completion_events,
                "diagnostics": await async_get_config_entry_diagnostics(hass, entry),
            }
        )
        permissions.update(
            {
                "configuration": {"credential", "prompt"},
                "provider_headers": {"credential"},
                "provider_requests": CONTENT,
                "owner_memory_creation": {"memory"},
                "owner_response": CONTENT,
                "native_effect": {"argument"},
                "declared_ha_events": CONTENT,
            }
        )
        assert completion_events and any(
            markers["user"] in _serialized(event) for event in completion_events
        )
        outsider = await hass_ws_client(hass, outsider_token)
        for section in ("memories", "conversations"):
            denied = await _management_response(
                outsider,
                entry=entry,
                section=section,
                action="list",
                scope_id="user:" + owner.id,
            )
            assert (
                denied["success"] is False
                and denied["error"]["code"] == "invalid_request"
            )
            observations["unauthorized_" + section] = denied
        memory = await _management_call(
            admin,
            entry=entry,
            section="memories",
            action="list",
            scope_id="user:" + owner.id,
        )
        assert markers["memory"] in _serialized(memory)
        observations["owner_memory"] = memory
        permissions["owner_memory"] = {"memory"}
        fetched = await _management_call(
            admin,
            entry=entry,
            section="knowledge",
            action="get",
            source_id=knowledge["source"]["source_id"],
        )
        assert markers["knowledge"] in _serialized(fetched)
        observations["owner_knowledge"] = fetched
        permissions["owner_knowledge"] = {"knowledge"}
        observations["owner_configuration"] = await _management_call(
            admin, entry=entry, section="configuration", action="get"
        )
        permissions["owner_configuration"] = {"prompt"}

        sessions = await _management_call(
            admin,
            entry=entry,
            section="conversations",
            action="list",
            scope_id="user:" + owner.id,
        )
        observations["owner_archive_list"] = sessions
        permissions["owner_archive_list"] = CONTENT
        private_session = None
        for item in sessions["sessions"]:
            archived = await _management_call(
                admin,
                entry=entry,
                section="conversations",
                action="get",
                session_id=item["session_id"],
                scope_id="user:" + owner.id,
            )
            destination = "owner_archive:" + item["session_id"]
            observations[destination] = archived
            permissions[destination] = CONTENT
            if all(
                markers[name] in _serialized(archived) for name in ("user", "argument")
            ):
                private_session = item["session_id"]
        assert private_session is not None
        denied = await _management_response(
            outsider,
            entry=entry,
            section="conversations",
            action="get",
            session_id=private_session,
        )
        assert (
            denied["success"] is False and denied["error"]["code"] == "invalid_request"
        )
        observations["unauthorized_known_archive"] = denied

        exported = await _transfer_call(
            admin, entry=entry, action="export_start", data={"mode": "full"}
        )
        assert exported["success"], exported
        export_id = exported["result"]["session_id"]
        export_path = Path(backup_transfer._exports(hass)[export_id].path)
        await hass.async_block_till_done()
        await recorder.get_instance(hass).async_block_till_done()
        files = await hass.async_add_executor_job(_active_files, tmp_path, database)
        memory_file = (
            Path(agent._memory._storage._store.path).relative_to(tmp_path).as_posix()
        )
        knowledge_file = (
            Path(agent._knowledge._storage._store.path).relative_to(tmp_path).as_posix()
        )
        archive_prefix = ".storage/" + agent._archive._storage._prefix
        assert markers["memory"] in files[memory_file]
        assert markers["knowledge"] in files[knowledge_file]
        authorized_export = export_path.relative_to(tmp_path).as_posix()
        assert all(
            markers[name] in files[authorized_export]
            for name in ("prompt", "memory", "knowledge", "user", "argument")
        )
        for relative, value in files.items():
            destination = "file:" + relative
            observations[destination] = value
            if relative == memory_file or relative.startswith(memory_file + "."):
                permissions[destination] = {"memory"}
            elif relative == knowledge_file or relative.startswith(
                knowledge_file + "."
            ):
                permissions[destination] = {"knowledge"}
            elif relative.startswith(archive_prefix + ".turns."):
                permissions[destination] = CONTENT
            elif relative == archive_prefix + ".metadata":
                permissions[destination] = {"user", "aborted"}
            elif relative == authorized_export:
                permissions[destination] = CONTENT
            elif relative == ".storage/core.config_entries":
                permissions[destination] = {"credential", "prompt"}
        rows, states, attributes = await hass.async_add_executor_job(
            _recorder_records, database
        )
        assert rows and any(
            event_type == EVENT_CONVERSATION_FINISHED and markers["user"] in value
            for event_type, value in rows
        )
        for index, (event_type, value) in enumerate(rows):
            destination = f"recorder_event:{event_type}:{index}"
            observations[destination] = value
            if event_type == EVENT_CONVERSATION_FINISHED:
                permissions[destination] = CONTENT
            elif event_type == "call_service":
                permissions[destination] = {"argument"}
        observations["recorder_states"] = states
        observations["recorder_attributes"] = attributes
        observations["logs"] = "\n".join(item.getMessage() for item in caplog.records)
        assert "OpenAI conversation request failed" in observations["logs"]
        _audit(markers, observations, permissions)
        # Only structural evidence leaves this test; raw app records were audited
        # before using the upload sanitizer. Known canaries also protect fail logs.
        evidence = safe(
            {
                "mode": mode,
                "destinations_checked": len(observations),
                "live_files_checked": len(files),
                "recorder_rows_checked": len(rows),
                "requested_export_checked": True,
            }
        )
        assert not any(marker in _serialized(evidence) for marker in markers.values())
        record(
            stress_trace,
            "summary",
            application_privacy_journeys=1,
            raw_application_destination_checks=len(observations),
            authorized_export_privacy_cases=1,
            consumer_first_recoveries=1,
            **evidence,
        )
    finally:
        release.set()
        unsubscribe()
        if active is not None and not active.done():
            await _cancel(active)
        if export_id is not None:
            cancelled = await _transfer_call(
                admin,
                entry=entry,
                action="export_cancel",
                data={"session_id": export_id},
            )
            assert cancelled["success"]
            assert not export_path.exists()
        await runner.cleanup()
