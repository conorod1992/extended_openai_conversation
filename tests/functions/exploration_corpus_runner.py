"""Production-backed replay of reviewed operation traces on genuine HA."""

from __future__ import annotations

from custom_components.extended_openai_conversation_responses.continuity import (
    ConversationContinuity,
)
from custom_components.extended_openai_conversation_responses.functions import (
    file as file_module,
)
from custom_components.extended_openai_conversation_responses.memory import (
    HomeAssistantMemoryStorage,
    PersistentMemory,
)
from custom_components.extended_openai_conversation_responses.scope import (
    ResolvedDataScope,
)
from homeassistant.components import conversation
from tests.functions.exploration_expected_state import (
    CORPUS as CORPUS,
    expected_corpus_case,
)
from tests_real_ha.test_ai_task_provider_wire import replay_invalid_final_task_action


async def replay_corpus_case(case, hass, tmp_path, monkeypatch):
    """Observe EOAI effects against a separately maintained expected-state model."""
    expected = expected_corpus_case(case)
    evidence = []
    schedule = []
    case_id = case["id"]
    if case_id == "continuity-owner-overlap":
        continuity = ConversationContinuity("corpus-agent")
        claims = {}
        for operation in case["operations"]:
            schedule.append(dict(operation))
            op = operation["op"]
            if op == "start_conversation":
                owner = operation["owner"]
                scope = ResolvedDataScope(f"user:{owner}", "user", "device_mapping", owner)
                claims[owner] = await continuity.async_resolve("device", scope, operation["device"], None, 30)
                evidence.append(f"started:{owner}:{operation['device']}")
            elif op == "overlap_turn":
                claim = claims["alpha"]
                scope = ResolvedDataScope("user:alpha", "user", "device_mapping", "alpha")
                overlap = await continuity.async_resolve("device", scope, "shared-device", None, 30)
                assert overlap.conversation_id != claim.conversation_id
                await continuity.async_record_success(claim.key, claim.claim_token, [conversation.UserContent(content="alpha-private-turn")])
                await continuity.async_release(overlap.key, overlap.claim_token)
                evidence.append("overlap:alpha")
            elif op == "resolve_device_owner":
                evidence.append(f"resolved:{operation['owner']}")
            else:
                claim = claims[operation["owner"]]
                assert claim.history == []
                assert claim.conversation_id != claims["alpha"].conversation_id
                evidence.append(f"isolated:{operation['owner']}")
        for claim in claims.values():
            await continuity.async_release(claim.key, claim.claim_token)
    elif case_id == "storage-lost-ack-retry":
        storage = HomeAssistantMemoryStorage(hass, "corpus-entry", "corpus-agent")
        manager = PersistentMemory(storage)
        await manager.async_initialize()
        store = storage._store
        write, read = store._async_write_data, store._async_load_data

        async def lost_ack(data):
            await write(data)
            raise OSError("corpus: committed write acknowledgement lost")

        async def unreadable():
            raise OSError("corpus: committed generation unreadable")

        for operation in case["operations"]:
            schedule.append(dict(operation))
            op = operation["op"]
            if op == "populate_store":
                await manager.async_add("owner", "existing-record", "corpus", "explicit")
                evidence.append("populated:A")
            elif op == "write":
                # Only HA's actual I/O boundary is faulted; EOAI rollback/recovery runs.
                monkeypatch.setattr(store, "_async_write_data", lost_ack)
                monkeypatch.setattr(store, "_async_load_data", unreadable)
                try:
                    await manager.async_add("owner", "new-record", "corpus", "explicit")
                except OSError as error:
                    assert "corpus:" in str(error)
                else:
                    raise AssertionError("Lost acknowledgement must fail the caller")
                evidence.append("committed:B:ack-lost")
            elif op == "readback":
                try:
                    await manager.async_initialize()
                except OSError as error:
                    assert "unreadable" in str(error)
                else:
                    raise AssertionError("Unreadable authoritative state must fail closed")
                evidence.append("readback:unreadable")
            elif op == "retry":
                monkeypatch.setattr(store, "_async_write_data", write)
                monkeypatch.setattr(store, "_async_load_data", read)
                await manager.async_initialize()
                result = await manager.async_add("owner", "new-record", "corpus", "explicit")
                assert result["status"] == "duplicate"
                evidence.append("retry:healthy:B")
            else:
                fresh = PersistentMemory(HomeAssistantMemoryStorage(hass, "corpus-entry", "corpus-agent"))
                await fresh.async_initialize()
                for instance in (manager, fresh):
                    assert {item.content for item in await instance.async_list("owner")} == {"existing-record", "new-record"}
                evidence.append("preserved:A+B")
    elif case_id == "native-file-replacement-conflict":
        target = tmp_path / "corpus-file.txt"
        target.write_text("original-content", encoding="utf-8")
        conflict = None
        for operation in case["operations"]:
            schedule.append(dict(operation))
            op = operation["op"]
            if op == "read_fingerprint":
                text, fingerprint = await hass.async_add_executor_job(file_module._read_text_bounded_snapshot, target)
                assert text == "original-content"
                evidence.append("fingerprint:original")
            elif op == "prepare_replacement":
                replacement = "replacement-content"
                evidence.append("replacement:prepared")
            elif op == "external_edit":
                target.write_text("external-content", encoding="utf-8")
                evidence.append("external-edit:committed")
            elif op == "replacement_boundary":
                try:
                    await hass.async_add_executor_job(file_module._atomic_replace_text_if_unchanged, target, replacement, fingerprint)
                except RuntimeError as error:
                    conflict = str(error)
                evidence.append("replacement:conflict" if conflict else "replacement:written")
            elif op == "assert_conflict":
                assert conflict and "changed since it was read" in conflict
                evidence.append("conflict:observed")
            elif op == "assert_external_bytes_preserved":
                assert target.read_text(encoding="utf-8") == "external-content"
                assert list(tmp_path.glob(".corpus-file.txt.*.tmp")) == []
                evidence.append("external-bytes:preserved")
            else:
                _, fingerprint = await hass.async_add_executor_job(file_module._read_text_bounded_snapshot, target)
                await hass.async_add_executor_job(file_module._atomic_replace_text_if_unchanged, target, replacement, fingerprint)
                assert target.read_text(encoding="utf-8") == replacement
                evidence.append("retry:healthy")
    else:
        # This shared genuine-HA journey asserts real service effects, public task
        # failure contracts, exact wire request counts and an independent healthy task.
        await replay_invalid_final_task_action(hass, monkeypatch, "chat_completions", schedule)
        evidence = ["tool-requested", "effect:once", "output:invalid", "task:failed", "independent-task:healthy", "effect-count:1"]
        assert schedule == case["operations"]
    assert evidence == expected["evidence"], case_id
    return {**expected, "evidence": evidence, "operations": schedule, "production_backed": True}
