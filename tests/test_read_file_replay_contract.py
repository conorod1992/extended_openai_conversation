"""File/Skill reads remain fresh after a provider continuation is lost."""

from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.functions.file import (
    ReadFileFunction,
)
from custom_components.extended_openai_conversation_responses.skills import SkillManager
from custom_components.extended_openai_conversation_responses.template import (
    ExtendedOpenAITemplateManager,
)
from custom_components.extended_openai_conversation_responses.tool_replay_guard import (
    remember_unacknowledged_calls,
)
from tests.test_function_tool_error_recovery import _chat_log
from tests.test_receipt_ownership_contracts import dispatch


@pytest.mark.parametrize("skill", [False, True], ids=["read_file", "load_skill"])
async def test_file_read_retries_after_failed_continuation(
    hass, tmp_path, monkeypatch, skill
):
    backend = ReadFileFunction()
    if skill:
        monkeypatch.setattr(SkillManager, "_instance", None)
        root = tmp_path / "skills"
        directory = root / "heating"
        directory.mkdir(parents=True)
        path = directory / "SKILL.md"
        path.write_text(
            "---\ndescription: Heating instructions\n---\nFirst", encoding="utf-8"
        )
        await SkillManager.async_get_instance(hass, user_skills_dir=str(root))
        helper = ExtendedOpenAITemplateManager(hass)
        monkeypatch.setitem(
            hass.data["template.environment"].globals,
            "extended_openai",
            helper._extended_openai,
        )
        config = {
            "type": "read_file",
            "path": "{{extended_openai.skill_dir(name)}}/{{file}}",
        }
        arguments = {"name": "heating", "file": "SKILL.md"}
    else:
        path = tmp_path / "extended_openai_conversation_responses" / "notes.txt"
        path.write_text("First", encoding="utf-8")
        config = {"type": "read_file", "path": str(path)}
        arguments = {}
    config = backend.validate_schema(config)
    entity, log = SimpleNamespace(hass=hass), _chat_log(hass)
    name = "load_skill" if skill else "read_file"
    first = await dispatch(
        entity, log, backend, config, name=name, call_id="first", arguments=arguments
    )
    assert first["content"].endswith("First")
    # This is the production retention boundary used after provider failure.
    remember_unacknowledged_calls(entity, log, set())
    path.write_text("Second", encoding="utf-8")
    retry = await dispatch(
        entity, log, backend, config, name=name, call_id="retry", arguments=arguments
    )
    assert retry["content"] == "Second"
