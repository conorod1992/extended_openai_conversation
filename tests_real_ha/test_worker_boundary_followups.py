"""Native HA checks for privacy continuity and payload-sized worker boundaries."""

import json
from pathlib import Path
import threading

import pytest


async def test_skill_recovery_failure_keeps_conversation_and_recovers_on_reload(
    hass, monkeypatch, caplog
):
    from custom_components.extended_openai_conversation_responses.const import (
        DEFAULT_CONF_FUNCTION_TOOLS,
        DEFAULT_WORKING_DIRECTORY,
        DOMAIN,
    )
    from custom_components.extended_openai_conversation_responses.skill_transactions import (
        prepare_transaction,
    )
    from tests_real_ha.test_cross_feature_acceptance import _agent, _say, _speech
    from tests_real_ha.test_provider_wire_e2e import _chat_sse_text, _install_wire

    skills = Path(hass.config.config_dir) / DEFAULT_WORKING_DIRECTORY / "skills"
    staging = skills.with_name(".skills.staging")
    installed = skills / "review"
    installed.mkdir(parents=True)
    (installed / "SKILL.md").write_text(
        "---\ndescription: Retained Skill instructions\n---\nReview notes.\n",
        encoding="utf-8",
    )
    backup = staging / f"review.remove-{'a' * 32}"
    journal = prepare_transaction(staging, installed, backup, None)
    installed.rename(backup)
    good_journal = journal.read_text(encoding="utf-8")
    bad_journal = json.loads(good_journal)
    bad_journal["previous_identity"][1] += 1
    journal.write_text(json.dumps(bad_journal), encoding="utf-8")

    loader = next(
        tool
        for tool in DEFAULT_CONF_FUNCTION_TOOLS
        if tool["spec"]["name"] == "load_skill"
    )
    agent = await _agent(hass, skills=["review"], functions=[loader])
    assert agent is not None
    wire = _install_wire(
        monkeypatch, agent, [_chat_sse_text("Ready"), _chat_sse_text("Recovered")]
    )
    assert _speech(await _say(hass, agent, "What can you do?")) == "Ready"
    assert "load_skill" not in json.dumps(wire.requests[0]["body"])
    assert "Skills could not be initialized" in caplog.text
    assert backup.is_dir()
    assert json.loads(journal.read_text(encoding="utf-8")) == bad_journal

    journal.write_text(good_journal, encoding="utf-8")
    response = await hass.services.async_call(
        DOMAIN, "reload_skills", blocking=True, return_response=True
    )
    assert response == {"loaded_skills": 1}
    assert _speech(await _say(hass, agent, "What can you do now?")) == "Recovered"
    assert "load_skill" in json.dumps(wire.requests[1]["body"])
    assert "Retained Skill instructions" in json.dumps(wire.requests[1]["body"])


@pytest.mark.parametrize("guest", [False, True], ids=["owner-control", "guest"])
async def test_native_owner_replacement_keeps_new_owner_history(
    hass, monkeypatch, guest
):
    from tests.test_guest_replacement_continuity import (
        test_owner_replacement_preserves_b_followup_only,
    )

    await test_owner_replacement_preserves_b_followup_only(hass, monkeypatch, guest)


@pytest.mark.parametrize("files,fields", [(1, 10000), (128, 100)])
async def test_native_catalogue_parser_uses_ha_executor(
    hass, tmp_path, monkeypatch, files, fields
):
    from custom_components.extended_openai_conversation_responses.skills import (
        SkillManager,
        SkillMdParser,
    )

    root = tmp_path / "skills"
    for i in range(files):
        directory = root / f"skill-{i:03d}"
        directory.mkdir(parents=True)
        metadata = "".join(f"metadata_{j}: value_{j}\n" for j in range(fields))
        (directory / "SKILL.md").write_text(
            f"---\ndescription: Valid Skill\n{metadata}---\nInstructions\n",
            encoding="utf-8",
        )
    manager = SkillManager(hass)
    manager._user_skills_dir = root
    previous = manager._skills
    parse = SkillMdParser.parse
    main = threading.get_ident()
    threads = []

    def checked(*args):
        threads.append(threading.get_ident())
        assert manager._filesystem_lock.locked()
        assert manager._skills is previous
        return parse(*args)

    monkeypatch.setattr(SkillMdParser, "parse", staticmethod(checked))
    # Use HA's own executor, not the unit fixture's executor adapter.
    await manager.async_load_skills()
    assert len(manager._skills) == files and manager._skills is not previous
    assert len(threads) == files and main not in threads


@pytest.mark.parametrize("api", ["chat_completions", "responses"])
async def test_native_large_attachment_revisions_use_worker(
    hass, tmp_path, monkeypatch, api
):
    from tests.test_attachment_revision_workers import (
        test_full_handler_revision_is_off_loop_and_detects_replaced_attachment,
    )

    await test_full_handler_revision_is_off_loop_and_detects_replaced_attachment(
        hass, tmp_path, monkeypatch, api, True
    )
