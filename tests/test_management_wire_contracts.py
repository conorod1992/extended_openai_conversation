"""Real mutations and HA WebSocket serialization reach production JS consumers.

The backend state is checked before invoking Node. A success-shaped fixture is
not evidence of a commit: rejected cases prove no write, and lost replies are
discarded only after the real operation has changed the retained state.
"""

from copy import deepcopy
from inspect import unwrap
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.extended_openai_conversation_responses import management_ui as ui
from custom_components.extended_openai_conversation_responses.agent_config import (
    agent_config_snapshot,
)
from custom_components.extended_openai_conversation_responses.function_yaml import (
    management_function_response,
)
from custom_components.extended_openai_conversation_responses.memory import (
    PersistentMemory,
)
from custom_components.extended_openai_conversation_responses.request_rules import (
    RequestRules,
)
from homeassistant.components.websocket_api.messages import (
    error_message,
    message_to_json_bytes,
    result_message,
)
from tests.test_function_groups_mutation import _group, _tool
from tests.test_memory import FakeStorage
from tests.test_request_rules import MemoryStore


def run_consumer(case):
    node = shutil.which("node")
    if node is None:
        if os.environ.get("RUN_REAL_HA_BROWSER") == "1":
            pytest.fail(
                "Genuine frontend CI requires Node for Management wire contracts"
            )
        pytest.skip("Node is required for the producer-to-consumer contract")
    runner = Path(__file__).with_name("management-python-contract-runner.mjs")
    result = subprocess.run(
        [node, str(runner)],
        input=message_to_json_bytes(case),
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()


async def exchange(hass, message, *, admin=True):
    """Use the actual command handler, conversion and HA wire encoder."""
    frames = []
    connection = SimpleNamespace(
        user=SimpleNamespace(id="admin", is_admin=admin),
        send_result=lambda iden, result: frames.append(
            message_to_json_bytes(result_message(iden, result))
        ),
        send_error=lambda iden, code, message: frames.append(
            message_to_json_bytes(error_message(iden, code, message))
        ),
    )
    await unwrap(ui.websocket_management)(hass, connection, {"id": 1, **message})
    assert len(frames) == 1
    return json.loads(frames[0])


def configuration_snapshot(subentry):
    return management_function_response(
        {
            "title": subentry.title,
            "revision": ui._agent_config_revision(subentry.data, subentry.title),
            "config": agent_config_snapshot(subentry.data),
        }
    )


def routing_rule():
    return {
        "name": "Wire route",
        "phrases": ["wire route"],
        "match_type": "starts_with",
        "action_type": "model_routing",
        "action": {
            "model": "gpt-5-mini",
            "scope": "request",
            "continue_to_ai": True,
        },
    }


@pytest.mark.parametrize(
    "family", ["function", "group", "rule", "configuration", "import", "memory_delete"]
)
@pytest.mark.parametrize("outcome", ["committed", "rejected", "lost"])
async def test_management_producers_reach_owned_js_consumers(
    hass, management_agent, management_message, monkeypatch, family, outcome
):
    entry, subentry = management_agent
    subentry.data = {**subentry.data, "functions": "[]", "function_groups": []}
    rules_store = MemoryStore()
    rules = RequestRules(rules_store)
    await rules.async_initialize()
    monkeypatch.setattr(ui, "async_get_request_rules", AsyncMock(return_value=rules))
    baseline = configuration_snapshot(subentry)
    previous = deepcopy(subentry.data)
    created = []

    def add_subentry(selected_entry, imported):
        assert selected_entry is entry
        entry.subentries[imported.subentry_id] = imported
        created.append(imported)

    hass.config_entries.async_add_subentry.side_effect = add_subentry
    revision = baseline["revision"]
    payload = {"revision": "obsolete" if outcome == "rejected" else revision}
    section, action = {
        "function": ("tools", "save"),
        "group": ("tools", "save_group"),
        "rule": ("request_rules", "create"),
        "configuration": ("configuration", "save"),
        "import": ("configuration", "import"),
        "memory_delete": ("memories", "delete"),
    }[family]
    memory = None
    storage = None
    before_memories = []
    if family == "function":
        payload["tool"] = _tool("wire_tool")
    elif family == "group":
        payload["group"] = _group("wire_group", [], "always")
    elif family == "rule":
        payload = {
            "rule": routing_rule(),
            "revision": "obsolete" if outcome == "rejected" else rules.revision(),
        }
    elif family == "configuration":
        payload["config"] = {"max_tokens": 723}
    elif family == "import":
        # A real create, including its new identity, is the consequence at risk
        # when a reply is lost. Current-mode replacement has separate coverage.
        payload = {
            "mode": "new",
            "document": {
                "schema": "extended_openai_conversation.agent",
                "version": 999 if outcome == "rejected" else 1,
                "title": "Wire imported assistant",
                "config": {"functions": [], "max_tokens": 723},
            },
        }
    else:
        storage = FakeStorage()
        memory = PersistentMemory(storage)
        await memory.async_initialize()
        monkeypatch.setattr(ui, "async_get_memory", AsyncMock(return_value=memory))
        added = await ui.async_management_command(
            hass,
            "admin",
            True,
            management_message(
                "memories", "add", scope_id="shared:household", content="Wire memory"
            ),
        )
        before_memories = [added["memory"]]
        payload = {
            "scope_id": "invalid-scope"
            if outcome == "rejected"
            else "shared:household",
            "memory_id": added["memory"]["memory_id"],
        }
        hass.config_entries.async_update_subentry.reset_mock()

    envelope = await exchange(hass, management_message(section, action, **payload))
    committed = outcome != "rejected"
    assert envelope["success"] is committed, envelope
    if family in {"function", "group", "configuration"}:
        assert (subentry.data != previous) is committed
        if committed:
            hass.config_entries.async_update_subentry.assert_called_once()
        else:
            hass.config_entries.async_update_subentry.assert_not_called()
    elif family == "rule":
        # Reload the manager from its retained Store, not just its RAM snapshot.
        fresh = RequestRules(rules_store)
        await fresh.async_initialize()
        assert len(fresh.snapshot()["rules"]) == int(committed)
        assert rules_store.saves == int(committed)
        hass.config_entries.async_update_subentry.assert_not_called()
    elif family == "import":
        assert len(created) == int(committed)
        if committed:
            assert created[0].data["max_tokens"] == 723
        assert subentry.data == previous
        hass.config_entries.async_update_subentry.assert_not_called()
    else:
        fresh = PersistentMemory(storage)
        await fresh.async_initialize()
        retained = await fresh.async_get_many(
            [("shared:household", before_memories[0]["memory_id"])],
            ["shared:household"],
        )
        assert len(retained) == int(not committed)

    run_consumer(
        {
            "family": family,
            "outcome": outcome,
            "section": section,
            "action": action,
            "payload": payload,
            "envelope": envelope,
            "before": baseline,
            "before_memories": before_memories,
            "rules": {
                "rules": [],
                "groups": [],
                "defaults": {},
                "wording_groups": [],
                "revision": 0,
            },
        }
    )


@pytest.mark.parametrize(
    "outcome", ["committed", "rejected", "lost", "committed_warning"]
)
async def test_journaled_restore_reaches_js_transfer_consumer(monkeypatch, outcome):
    from custom_components.extended_openai_conversation_responses import (
        restore_recovery,
        transfer,
    )
    from custom_components.extended_openai_conversation_responses.mutation_outcomes import (
        mutation_error_code,
    )
    from homeassistant.exceptions import HomeAssistantError
    from tests.test_selective_restore_concurrency import RestoreHarness

    tx = RestoreHarness(monkeypatch)
    if outcome == "committed_warning":
        # The real transaction commits; its existing optional reload boundary
        # then fails and supplies production committed-failure evidence.
        tx.entry.state = None
        tx.hass.data.setdefault(
            "extended_openai_conversation_responses.restore_reload_pending", set()
        ).add(tx.entry.entry_id)
        monkeypatch.setattr(
            restore_recovery,
            "async_settle_owned_operation",
            AsyncMock(side_effect=HomeAssistantError("post-commit reload witness")),
        )
    envelope = None
    try:
        result = await transfer.async_restore_transfer(
            tx.hass,
            tx.entry,
            tx.subentry,
            tx.imported,
            sections=["invalid"]
            if outcome == "rejected"
            else [transfer.SECTION_KNOWLEDGE],
        )
    except HomeAssistantError as error:
        envelope = json.loads(
            message_to_json_bytes(
                error_message(1, mutation_error_code(error), str(error))
            )
        )
    else:
        envelope = json.loads(message_to_json_bytes(result_message(1, result)))
    if outcome == "rejected":
        assert tx.writes == [] and tx.journal.saved == []
        assert envelope["success"] is False
    else:
        assert tx.state.knowledge == []
        assert [item["phase"] for item in tx.journal.saved] == ["applying", "committed"]
        assert tx.journal.data is None
        assert envelope["success"] is (outcome != "committed_warning")
        if outcome == "committed_warning":
            assert envelope["error"]["code"] == "eoai_mutation_committed"
    run_consumer(
        {
            "family": "restore",
            "outcome": outcome,
            "section": "backup",
            "action": "restore",
            "envelope": envelope,
            "before": configuration_snapshot(tx.subentry),
            "payload": {},
        }
    )
