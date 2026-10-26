"""Native HA checks for privacy continuity and payload-sized worker boundaries."""

import threading

import pytest


@pytest.mark.parametrize("guest", [False, True], ids=["owner-control", "guest"])
async def test_native_owner_replacement_keeps_new_owner_history(hass, monkeypatch, guest):
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
