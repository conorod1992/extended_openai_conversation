"""Selected, advertised Skills reach both providers completely through paging."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from custom_components.extended_openai_conversation_responses.const import (
    DEFAULT_CONF_FUNCTION_TOOLS,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.guest_mode import (
    GuestCapabilityPolicy,
)
from custom_components.extended_openai_conversation_responses.skill_runtime_availability import (
    selected_skill_scope,
)
from homeassistant.helpers import llm
from tests.test_model_tool_result_boundary import wire_result as wire_result
from tests.test_skill_discovery_load_size import _write, skill_loader as skill_loader


@pytest.fixture(autouse=True)
def isolated_runtime_templates():
    from custom_components.extended_openai_conversation_responses.agent_config import (
        _cached_configured_tools,
    )

    # Each fixture represents a separate HA process/template environment.
    _cached_configured_tools.cache_clear()
    yield
    _cached_configured_tools.cache_clear()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("strict", [False, True])
@pytest.mark.parametrize("filename", ["SKILL.md", "support.txt"])
@pytest.mark.parametrize(
    "body",
    ["instruction " * 4000, '\\"\t\n' * 16000, "é😀" * 20000],
    ids=["ordinary", "escaped", "unicode"],
)
async def test_skill_discovery_advertisement_execution_and_wire_pages(
    hass,
    monkeypatch,
    skill_loader,
    wire_result,
    legacy,
    strict,
    filename,
    body,
    request,
):
    manager, root, _ = skill_loader
    instructions = "---\ndescription: Complete instructions\n---\n" + body
    _write(
        root,
        "paging",
        instructions.encode()
        if filename == "SKILL.md"
        else b"---\ndescription: Supporting file\n---\nRead support.txt",
    )
    if filename != "SKILL.md":
        (root / "paging" / filename).write_bytes(body.encode("utf-8"))
    await manager.async_load_skills()
    assert manager.get_skill("paging") is not None
    loader = deepcopy(
        next(
            t for t in DEFAULT_CONF_FUNCTION_TOOLS if t["spec"]["name"] == "load_skill"
        )
    )
    if legacy:
        loader["spec"]["parameters"]["properties"].pop("offset")
        loader["function"].pop("page_offset")
    if strict:
        loader["spec"]["strict"] = True
        parameters = loader["spec"]["parameters"]
        parameters["required"] = list(parameters["properties"])
        parameters["additionalProperties"] = False
    original = deepcopy(loader)
    options = {
        "functions": [loader],
        "skills": ["paging"],
        "function_groups": [],
        "memory_mode": "off",
    }
    entity = object.__new__(ExtendedOpenAIAgentEntity)
    entity.hass, entity.entity_id = hass, "conversation.paging"
    entity.subentry = SimpleNamespace(data=options)
    entity.skill_manager = manager
    entity._function_groups_runtime = entity._temporary_memory = entity._knowledge = (
        entity._archive
    ) = None
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity,
        "_effective_guest_policy",
        lambda self: GuestCapabilityPolicy.unrestricted(),
    )
    monkeypatch.setattr(
        ExtendedOpenAIAgentEntity, "_current_memory_scope_id", lambda self: None
    )
    tool = next(
        t for t in entity._get_function_tools() if t["spec"]["name"] == "load_skill"
    )
    assert "offset" in tool["spec"]["parameters"]["properties"]
    from custom_components.extended_openai_conversation_responses.entity import (
        _format_tools,
    )
    from custom_components.extended_openai_conversation_responses.function_execution import (
        validate_strict_function_schema,
    )

    api = request.node.callspec.params["wire_result"]
    formatted = _format_tools([tool], api)[0]
    wire_spec = formatted["function"] if api == "chat_completions" else formatted
    assert "offset" in wire_spec["parameters"]["properties"]
    if strict:
        validate_strict_function_schema(wire_spec["parameters"])
    assert loader == original  # Runtime compatibility does not rewrite saved presets.
    offset, pages = 0, []
    with selected_skill_scope(options):
        while True:
            args = {"name": "paging", "file": filename}
            if offset:
                args["offset"] = offset
            elif strict:
                args["offset"] = None
            content = await entity._execute_function_tool(
                tool,
                llm.ToolInput(
                    id=f"page-{offset}", tool_name="load_skill", tool_args=args
                ),
                None,
                [],
            )
            page = wire_result(content)["result"]
            assert page.get("status") != "output_overflow"
            assert "content" in page, page
            pages.append(page["content"])
            if not page.get("has_more"):
                break
            assert page["next_offset"] == offset + len(page["content"])
            assert page["next_offset"] > offset
            offset = page["next_offset"]
    assert len(pages) > 1
    assert "".join(pages) == (instructions if filename == "SKILL.md" else body)


@pytest.mark.parametrize(
    "offset",
    [
        -1,
        1048577,
        "invalid",
        1.5,
        False,
        "1.5",
        "NaN",
        "Infinity",
        "1e1000",
        "1__2",
        "1e+_3",
    ],
)
async def test_invalid_file_page_offset_fails_without_truncated_instructions(
    skill_loader, offset
):
    manager, root, load = skill_loader
    _write(root, "small", b"---\ndescription: Small\n---\nComplete instructions")
    await manager.async_load_skills()
    result = await load("small", offset=offset)
    assert "error" in result


@pytest.mark.parametrize(
    "offset,expected_start",
    [(0.0, 0), (1000.0, 1000), ("1e3", 1000), ("1000.000", 1000)],
)
async def test_file_page_offset_accepts_exact_integral_numeric_renderings(
    skill_loader, offset, expected_start
):
    """Text rendering must not reject valid JSON integer-valued floats."""
    manager, root, load = skill_loader
    instructions = "---\ndescription: Offset test\n---\n" + "x" * 1600
    _write(root, "offset-value", instructions.encode("utf-8"))
    await manager.async_load_skills()

    result = await load("offset-value", offset=offset)

    assert result["content"] == instructions[expected_start:]
    assert result["size"] == len(instructions.encode("utf-8"))
    assert result["has_more"] is False


async def test_nullable_optional_offset_starts_at_first_page(skill_loader):
    manager, root, load = skill_loader
    _write(root, "small", b"---\ndescription: Small\n---\nComplete instructions")
    await manager.async_load_skills()
    assert (await load("small", offset=None))["content"].endswith(
        "Complete instructions"
    )


def test_paging_upgrade_preserves_authored_custom_reader_contract():
    from custom_components.extended_openai_conversation_responses.skill_loader_contract import (
        upgrade_skill_loader_paging,
    )

    tool = deepcopy(
        next(
            t for t in DEFAULT_CONF_FUNCTION_TOOLS if t["spec"]["name"] == "load_skill"
        )
    )
    tool["spec"]["parameters"]["properties"].pop("offset")
    tool["function"].pop("page_offset")
    tool["spec"]["parameters"]["properties"]["custom_argument"] = {"type": "string"}
    before = deepcopy(tool)
    assert upgrade_skill_loader_paging(tool) is tool
    assert tool == before
    tool["spec"]["parameters"]["properties"].pop("custom_argument")
    tool["spec"]["parameters"]["properties"]["name"] = True
    assert upgrade_skill_loader_paging(tool) is tool
