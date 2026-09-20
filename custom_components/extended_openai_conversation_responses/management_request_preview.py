"""Lower-level Management request preview ownership."""

from __future__ import annotations

from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .agent_config import function_tool_enabled
from .const import (
    CONF_CONTINUE_CONVERSATION,
    CONF_CONVERSATION_CONTINUITY,
    CONF_FUNCTION_GROUPS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_SKILLS,
    CONF_TEMPORARY_MEMORY,
    CONTINUE_CONVERSATION_CONDITIONAL,
    DEFAULT_CONTINUE_CONVERSATION,
    DEFAULT_CONVERSATION_CONTINUITY,
    DEFAULT_FUNCTION_GROUPS,
    DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT,
    DEFAULT_TEMPORARY_MEMORY,
    DOMAIN,
    FUNCTION_GROUP_LOADER_TOOL_NAME,
    TEMPORARY_MEMORY_OFF,
)
from .continuity import ConversationContinuity
from .function_groups import assemble_function_tools
from .guest_mode import async_get_guest_mode, resolve_guest_policy
from .ha_llm_tools import ToolSnapshot, async_discover, is_ha_tool
from .helpers import get_exposed_entities
from .knowledge import get_loaded_knowledge
from .management_function_quarantine import (
    _management_configured_tools as configured_function_tools_from_data,
    _management_validate_function_groups as validate_function_groups,
)
from .memory import memory_enabled
from .prompt import render_effective_prompt
from .request import (
    CONTINUE_CONVERSATION_TOOL,
    assemble_integration_function_tools,
    build_provider_request_snapshot,
    canonical_json,
    format_function_tools,
)
from .scope import user_scope
from .skill_runtime_availability import effective_tool_runtime_scope
from .skills import SkillManager
from .temporary_memory import (
    async_read_temporary_memory_snapshot,
    get_loaded_temporary_memory,
)

def entry_and_agent(hass: HomeAssistant, entry_id: str, subentry_id: str):
    """Resolve an exact entry and conversation subentry for every management API."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise HomeAssistantError("Integration entry not found")
    subentry = entry.subentries.get(subentry_id)
    if subentry is None or subentry.subentry_type != "conversation":
        raise HomeAssistantError("Conversation agent not found")
    return entry, subentry


async def async_preview_effective_request(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    options: dict[str, Any],
    user_id: str,
) -> dict[str, Any]:
    """Assemble a side-effect-free snapshot of a fresh provider request."""
    configured_tools = configured_function_tools_from_data(options)
    guest_manager = await async_get_guest_mode(
        hass, entry.entry_id, subentry.subentry_id
    )
    guest_policy = resolve_guest_policy(hass, options, guest_manager, configured_tools)
    references = [
        tool["function"]
        for tool in configured_tools
        if is_ha_tool(tool) and function_tool_enabled(tool)
    ]
    ha_snapshot = ToolSnapshot()
    if references and not guest_policy.guest_active:
        from homeassistant.helpers import llm

        ha_snapshot = await async_discover(
            hass,
            llm.LLMContext(
                platform=DOMAIN,
                context=Context(user_id=user_id),
                language=hass.config.language,
                assistant="conversation",
                device_id=None,
            ),
            references,
        )
    configured_tools = ha_snapshot.project(configured_tools)
    temporary_memories = []
    notes = [
        "User input and conversation history are excluded.",
        "Query-derived persistent memories are excluded because there is no user query.",
    ]
    temporary = get_loaded_temporary_memory(hass, entry.entry_id, subentry.subentry_id)
    temporary_mode = options.get(CONF_TEMPORARY_MEMORY, DEFAULT_TEMPORARY_MEMORY)
    scope = user_scope(user_id, source="management_preview")
    temporary_scope, _label = ConversationContinuity.identity_key(
        options.get(CONF_CONVERSATION_CONTINUITY, DEFAULT_CONVERSATION_CONTINUITY),
        scope,
        None,
    )
    if (
        temporary_mode != TEMPORARY_MEMORY_OFF
        and temporary_scope is not None
        and guest_policy.temporary_memory
    ):
        temporary_memories = (
            await temporary.async_active_snapshot(
                temporary_scope, owner_scope_id=f"user:{user_id}"
            )
            if temporary is not None
            else await async_read_temporary_memory_snapshot(
                hass,
                entry.entry_id,
                subentry.subentry_id,
                temporary_scope,
                owner_scope_id=f"user:{user_id}",
            )
        )
    elif temporary_mode != TEMPORARY_MEMORY_OFF:
        notes.append(
            "Active temporary memories are excluded because a fresh device or "
            "conversation scope cannot be identified without a request."
        )

    skill_manager = SkillManager.get_loaded_instance()
    enabled_names = set(options.get(CONF_SKILLS, []) or [])
    skills = (
        [
            skill
            for skill in skill_manager.get_all_skills()
            if skill.name in enabled_names
        ]
        if skill_manager is not None and guest_policy.skills
        else []
    )
    knowledge = get_loaded_knowledge(hass, entry.entry_id, subentry.subentry_id)
    knowledge_available = bool(
        guest_policy.knowledge_access
        and options.get(CONF_KNOWLEDGE_ENABLED)
        and knowledge is not None
        and knowledge.source_count > 0
    )
    try:
        exposed_entities = get_exposed_entities(hass)
        if guest_policy.guest_active:
            exposed_entities = [
                entity
                for entity in exposed_entities
                if guest_policy.allows_entity_read(str(entity.get("entity_id", "")))
            ]
        preview = render_effective_prompt(
            hass,
            options,
            exposed_entities=exposed_entities,
            current_device_id=None,
            user_input=None,
            skills=skills,
            memories=None,
            temporary_memories=temporary_memories,
            knowledge_available=knowledge_available,
            guest_policy=guest_policy,
        )
        groups = validate_function_groups(
            options.get(CONF_FUNCTION_GROUPS, DEFAULT_FUNCTION_GROUPS),
            configured_tools,
        )
        availability_groups = groups
        availability_tools = configured_tools
        if guest_policy.guest_active:
            membership = {
                name: group for group in groups for name in group.get("functions", [])
            }
            configured_tools = [
                tool
                for tool in configured_tools
                if not is_ha_tool(tool)
                and guest_policy.allows_configured_tool(tool["spec"]["name"])
                and (
                    tool["spec"]["name"] not in membership
                    or membership[tool["spec"]["name"]].get("guest_allowed") is True
                )
            ]
            allowed_names = {tool["spec"]["name"] for tool in configured_tools}
            groups = [
                {
                    **group,
                    "functions": [
                        name for name in group["functions"] if name in allowed_names
                    ],
                }
                for group in groups
                if group.get("guest_allowed") is True
            ]
        with effective_tool_runtime_scope(
            options, availability_tools, skill_manager, availability_groups
        ):
            grouped = assemble_function_tools(configured_tools, groups, set())
        provider = build_provider_request_snapshot(options, getattr(entry, "data", {}))
        custom_tools = [
            tool
            for tool in grouped.tools
            if tool.get("spec", {}).get("name") != FUNCTION_GROUP_LOADER_TOOL_NAME
        ]
        loader_tools = [
            tool
            for tool in grouped.tools
            if tool.get("spec", {}).get("name") == FUNCTION_GROUP_LOADER_TOOL_NAME
        ]
        configured_names = {
            tool.get("spec", {}).get("name")
            for tool in configured_tools
            if isinstance(tool, dict)
        }
        integration_tools = assemble_integration_function_tools(
            options,
            configured_names,
            memory_scope_available=memory_enabled(options),
            temporary_scope_available=(
                temporary_mode != TEMPORARY_MEMORY_OFF and temporary_scope is not None
            ),
            knowledge_available=knowledge_available,
            guest_policy=guest_policy,
        )
        conditional_continue = (
            options.get(CONF_CONTINUE_CONVERSATION, DEFAULT_CONTINUE_CONVERSATION)
            == CONTINUE_CONVERSATION_CONDITIONAL
        )
        if conditional_continue:
            integration_tools.append(CONTINUE_CONVERSATION_TOOL)

        formatted_custom = format_function_tools(custom_tools, provider.api_mode)
        formatted_loader = format_function_tools(loader_tools, provider.api_mode)
        formatted_integration = format_function_tools(
            integration_tools, provider.api_mode
        )
        formatted_provider = (
            list(provider.provider_tools) if guest_policy.web_search else []
        )
        request_settings = {"api_mode": provider.api_mode, **provider.api_kwargs}
        if (
            formatted_custom
            or formatted_loader
            or formatted_integration
            or formatted_provider
        ):
            request_settings["tool_choice"] = (
                "required" if conditional_continue else "auto"
            )

        section_values = (
            (
                "system_context",
                "System / context",
                "\n".join(
                    part
                    for part in (preview.text, ha_snapshot.prompt_for(grouped.tools))
                    if part
                ),
                "text",
            ),
            (
                "function_tools",
                "Function tools",
                canonical_json(formatted_custom),
                "json",
            ),
            (
                "function_group_loader",
                "Function Group catalogue / loader",
                canonical_json(formatted_loader),
                "json",
            ),
            (
                "integration_tools",
                "Integration tools",
                canonical_json(formatted_integration),
                "json",
            ),
            (
                "provider_tools",
                "Provider tools",
                canonical_json(formatted_provider),
                "json",
            ),
            (
                "request_settings",
                "Request settings",
                canonical_json(request_settings),
                "json",
            ),
        )
        sections = [
            {
                "key": key,
                "label": label,
                "content": content,
                "format": output_format,
                "character_count": len(content),
            }
            for key, label, content, output_format in section_values
        ]

        enabled_tools = [
            tool
            for tool in configured_tools
            if function_tool_enabled(tool)
            and (not is_ha_tool(tool) or tool.get("ha_available") is True)
        ]
        grouped_payload = canonical_json(
            format_function_tools(grouped.tools, provider.api_mode)
        )
        ungrouped_payload = canonical_json(
            format_function_tools(enabled_tools, provider.api_mode)
        )
        raw_savings = len(ungrouped_payload) - len(grouped_payload)
        savings = max(0, raw_savings)
        savings_percent = (
            round((savings / len(ungrouped_payload)) * 100)
            if savings and ungrouped_payload
            else 0
        )
    except Exception as err:
        concise = " ".join(str(err).split())[:500] or type(err).__name__
        raise HomeAssistantError(
            f"The effective request could not be assembled: {concise}"
        ) from err

    if (
        int(
            options.get(
                CONF_MEMORY_AUTO_RETRIEVE_LIMIT, DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT
            )
        )
        <= 0
    ):
        notes = [
            note
            for note in notes
            if not note.startswith("Query-derived persistent memories")
        ]
    return {
        "prompt": "\n".join(
            part
            for part in (preview.text, ha_snapshot.prompt_for(grouped.tools))
            if part
        ),
        "prompt_sections": [
            {
                "key": section.key,
                "label": section.label,
                "volatility": section.volatility,
            }
            for section in preview.sections
        ],
        "guest_mode": {
            "status": guest_manager.status(),
            "policy": guest_policy.as_diagnostics(),
        },
        "sections": sections,
        "total_character_count": sum(
            section["character_count"] for section in sections
        ),
        "function_group_savings": {
            "characters": savings,
            "percent": savings_percent,
            "grouped_characters": len(grouped_payload),
            "without_on_demand_grouping_characters": len(ungrouped_payload),
        },
        "notes": notes,
    }
