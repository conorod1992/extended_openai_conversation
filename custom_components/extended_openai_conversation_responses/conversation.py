"""Extended OpenAI Conversation (Responses) agent entity."""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

from openai import OpenAIError

from homeassistant.components import conversation
from homeassistant.components.conversation import (
    ChatLog,
    ConversationEntity,
    ConversationEntityFeature,
    ConversationInput,
    ConversationResult,
    async_get_chat_log,
)
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import intent, llm
from homeassistant.helpers.chat_session import async_get_chat_session
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ExtendedOpenAIConfigEntry
from .agent_config import (
    configured_function_tools_from_data,
    function_tool_enabled,
    validate_function_groups,
)
from .const import (
    CONF_ARCHIVE_ENABLED,
    CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
    CONF_ARCHIVE_RETENTION_DAYS,
    CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    CONF_CONTINUE_CONVERSATION,
    CONF_CONVERSATION_CONTINUITY,
    CONF_CONVERSATION_TIMEOUT_MINUTES,
    CONF_FUNCTION_GROUPS,
    CONF_KNOWLEDGE_ENABLED,
    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
    CONF_SHARED_ARCHIVE_ENABLED,
    CONF_SHARED_MEMORY_MODE,
    CONF_SKILLS,
    CONF_TEMPORARY_MEMORY,
    CONF_USAGE_REQUEST_RETENTION_DAYS,
    CONF_USAGE_RUN_RETENTION_DAYS,
    CONTINUE_CONVERSATION_ALWAYS,
    CONTINUE_CONVERSATION_CONDITIONAL,
    DEFAULT_ARCHIVE_ENABLED,
    DEFAULT_ARCHIVE_MODEL_SEARCH_ENABLED,
    DEFAULT_ARCHIVE_RETENTION_DAYS,
    DEFAULT_ARCHIVE_SESSION_TIMEOUT_MINUTES,
    DEFAULT_CONTINUE_CONVERSATION,
    DEFAULT_CONVERSATION_CONTINUITY,
    DEFAULT_CONVERSATION_TIMEOUT_MINUTES,
    DEFAULT_FUNCTION_GROUPS,
    DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT,
    DEFAULT_SHARED_ARCHIVE_ENABLED,
    DEFAULT_SHARED_MEMORY_MODE,
    DEFAULT_TEMPORARY_MEMORY,
    DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
    DEFAULT_USAGE_RUN_RETENTION_DAYS,
    DEFAULT_WORKING_DIRECTORY,
    DOMAIN,
    EVENT_CONVERSATION_FINISHED,
    SHARED_MEMORY_AUTOMATIC,
    SHARED_MEMORY_DISABLED,
    TEMPORARY_MEMORY_OFF,
)
from .continuity import ConversationContinuity, async_get_continuity
from .conversation_archive import ArchiveSession, ConversationArchive, async_get_archive
from .entity import ExtendedOpenAIBaseLLMEntity
from .exceptions import FunctionLoadFailed, FunctionNotFound, InvalidFunction
from .function_groups import (
    FunctionGroupRuntime,
    FunctionGroupSession,
    assemble_function_tools,
    load_function_groups,
    remove_function_group_runtime,
    reset_function_group_runtime,
)
from .helpers import get_exposed_entities
from .knowledge import KnowledgeLibrary, async_get_knowledge, search_result_as_dict
from .memory import (
    MemoryRecord,
    PersistentMemory,
    async_get_memory,
    automatic_memory_enabled,
    memory_as_dict,
    memory_enabled,
    memory_user_id,
)
from .prompt import render_effective_prompt
from .request import assemble_integration_function_tools
from .scope import ResolvedDataScope, memory_scope_id, resolve_data_scope
from .skills import Skill, SkillManager
from .speech import has_custom_speech_replacements, process_speech_text
from .temporary_memory import (
    TemporaryMemory,
    TemporaryMemoryRecord,
    async_get_temporary_memory,
    temporary_memory_as_dict,
)
from .usage import async_get_usage

_LOGGER = logging.getLogger(__name__)

_ACTIVE_SCOPE: ContextVar[ResolvedDataScope | None] = ContextVar(
    "extended_openai_active_scope", default=None
)
_ACTIVE_ARCHIVE: ContextVar[tuple[str, str] | None] = ContextVar(
    "extended_openai_active_archive", default=None
)
_ACTIVE_TEMPORARY_SCOPE: ContextVar[str | None] = ContextVar(
    "extended_openai_active_temporary_scope", default=None
)
_ACTIVE_FUNCTION_GROUP_SESSION: ContextVar[FunctionGroupSession | None] = ContextVar(
    "extended_openai_active_function_group_session", default=None
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ExtendedOpenAIConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the OpenAI Conversation entities."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue

        async_add_entities(
            [ExtendedOpenAIAgentEntity(config_entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class ExtendedOpenAIAgentEntity(
    ConversationEntity,
    conversation.AbstractConversationAgent,
    ExtendedOpenAIBaseLLMEntity,
):
    """Extended OpenAI conversation agent."""

    _attr_supports_streaming = True
    _attr_supported_features = ConversationEntityFeature.CONTROL
    skill_manager: SkillManager
    _memory: PersistentMemory | None = None
    _temporary_memory: TemporaryMemory | None = None
    _continuity: ConversationContinuity | None = None
    _knowledge: KnowledgeLibrary | None = None
    _archive: ConversationArchive | None = None
    _function_groups_runtime: FunctionGroupRuntime | None = None

    def __init__(self, entry: ExtendedOpenAIConfigEntry, subentry: Any) -> None:
        """Initialize the conversation agent and its streaming capability."""
        super().__init__(entry, subentry)
        # Arbitrary regex can depend on future text. Home Assistant selects whether
        # to attach its progressive listener from this per-agent capability flag.
        self._attr_supports_streaming = not has_custom_speech_replacements(
            subentry.data
        )
        if not self._attr_supports_streaming:
            _LOGGER.debug(
                "Progressive TTS disabled because custom speech regex rules are configured"
            )

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    @property
    def skills(self) -> list[str]:
        """Get the enabled skills list for this entity."""
        return self.subentry.data.get(CONF_SKILLS, []) or []

    async def async_added_to_hass(self) -> None:
        """When entity is added to Home Assistant."""
        await super().async_added_to_hass()
        conversation.async_set_agent(self.hass, self.entry, self)

        # Calculate skills directory based on working directory
        working_dir = DEFAULT_WORKING_DIRECTORY
        if Path(working_dir).is_absolute():
            skills_dir = Path(working_dir) / "skills"
        else:
            skills_dir = Path(self.hass.config.config_dir) / working_dir / "skills"

        self.skill_manager = await SkillManager.async_get_instance(
            self.hass, user_skills_dir=str(skills_dir)
        )

        self._usage = await async_get_usage(
            self.hass, self.entry.entry_id, self.subentry.subentry_id
        )
        self._usage.request_retention_days = int(
            self.subentry.data.get(
                CONF_USAGE_REQUEST_RETENTION_DAYS,
                DEFAULT_USAGE_REQUEST_RETENTION_DAYS,
            )
        )
        self._usage.run_retention_days = int(
            self.subentry.data.get(
                CONF_USAGE_RUN_RETENTION_DAYS, DEFAULT_USAGE_RUN_RETENTION_DAYS
            )
        )
        await self._usage.async_prune_details()
        self._archive = await async_get_archive(
            self.hass, self.entry.entry_id, self.subentry.subentry_id
        )
        self._continuity = async_get_continuity(
            self.hass, self.entry.entry_id, self.subentry.subentry_id
        )
        self._function_groups_runtime = reset_function_group_runtime(
            self.hass, self.entry.entry_id, self.subentry.subentry_id
        )
        if (
            self.subentry.data.get(CONF_TEMPORARY_MEMORY, DEFAULT_TEMPORARY_MEMORY)
            != TEMPORARY_MEMORY_OFF
        ):
            try:
                self._temporary_memory = await async_get_temporary_memory(
                    self.hass, self.entry.entry_id, self.subentry.subentry_id
                )
            except Exception:
                _LOGGER.exception("Unable to initialize temporary memory")
        await self._archive.async_prune(
            int(
                self.subentry.data.get(
                    CONF_ARCHIVE_RETENTION_DAYS, DEFAULT_ARCHIVE_RETENTION_DAYS
                )
            )
        )

        try:
            self._knowledge = await async_get_knowledge(
                self.hass, self.entry.entry_id, self.subentry.subentry_id
            )
        except Exception:
            _LOGGER.exception("Unable to initialize Knowledge Library")

        if memory_enabled(self.subentry.data):
            try:
                self._memory = await async_get_memory(
                    self.hass, self.entry.entry_id, self.subentry.subentry_id
                )
            except Exception:
                _LOGGER.exception("Unable to initialize persistent memory")

    async def async_will_remove_from_hass(self) -> None:
        """When entity will be removed from Home Assistant."""
        conversation.async_unset_agent(self.hass, self.entry)
        remove_function_group_runtime(
            self.hass, self.entry.entry_id, self.subentry.subentry_id
        )
        await super().async_will_remove_from_hass()

    async def async_process(self, user_input: ConversationInput) -> ConversationResult:
        """Process a sentence."""
        llm_context = user_input.as_llm_context(DOMAIN)
        source_device_id = user_input.satellite_id or user_input.device_id
        scope = resolve_data_scope(
            SimpleNamespace(
                context=llm_context.context,
                device_id=source_device_id,
            ),
            self.subentry.data,
        )
        continuity_mode = self.subentry.data.get(
            CONF_CONVERSATION_CONTINUITY, DEFAULT_CONVERSATION_CONTINUITY
        )
        timeout_minutes = int(
            self.subentry.data.get(
                CONF_CONVERSATION_TIMEOUT_MINUTES,
                DEFAULT_CONVERSATION_TIMEOUT_MINUTES,
            )
        )
        source_device_id = source_device_id or scope.device_id
        assert self._continuity is not None
        resolution = await self._continuity.async_resolve(
            continuity_mode,
            scope,
            source_device_id,
            user_input.conversation_id,
            timeout_minutes,
        )
        user_input.conversation_id = resolution.conversation_id
        context_id = getattr(getattr(llm_context, "context", None), "id", None)
        session_key = (
            f"continuity:{resolution.key}"
            if resolution.key
            else f"conversation:{user_input.conversation_id}"
            if user_input.conversation_id
            else f"context:{context_id or scope.device_id or 'unidentified'}"
        )
        archive_session: ArchiveSession | None = None
        if self._archive is not None:
            archive_session = await self._archive.async_begin_session(
                session_key,
                scope,
                user_input.conversation_id,
                archive_enabled=self.subentry.data.get(
                    CONF_ARCHIVE_ENABLED, DEFAULT_ARCHIVE_ENABLED
                ),
                shared_archive_enabled=self.subentry.data.get(
                    CONF_SHARED_ARCHIVE_ENABLED, DEFAULT_SHARED_ARCHIVE_ENABLED
                ),
                inactivity_minutes=int(
                    self.subentry.data.get(
                        CONF_ARCHIVE_SESSION_TIMEOUT_MINUTES,
                        DEFAULT_ARCHIVE_SESSION_TIMEOUT_MINUTES,
                    )
                ),
            )
        scope_token = _ACTIVE_SCOPE.set(scope)
        archive_token = _ACTIVE_ARCHIVE.set(
            (session_key, archive_session.session_id) if archive_session else None
        )
        with (
            async_get_chat_session(self.hass, resolution.conversation_id) as session,
            async_get_chat_log(self.hass, session, user_input) as chat_log,
        ):
            temporary_scope = (
                resolution.key or f"conversation:{chat_log.conversation_id}"
            )
            temporary_token = _ACTIVE_TEMPORARY_SCOPE.set(temporary_scope)
            function_group_session = (
                self._function_groups_runtime.begin(
                    (
                        f"continuity:{resolution.key}"
                        if resolution.key
                        else f"conversation:{chat_log.conversation_id}"
                    ),
                    timeout_minutes if resolution.key else 5,
                )
                if self._function_groups_runtime is not None
                else None
            )
            function_group_token = _ACTIVE_FUNCTION_GROUP_SESSION.set(
                function_group_session
            )
            if resolution.history and len(chat_log.content) <= 2:
                # Core chat logs expire independently after five minutes. Restore the
                # integration-owned bounded model history when Core recreates the log.
                current_user = chat_log.content[-1]
                chat_log.content[:] = [*resolution.history, current_user]
            try:
                if self._usage is None:
                    result = await self._async_handle_message(user_input, chat_log)
                    if chat_log.content and isinstance(
                        chat_log.content[-1], conversation.AssistantContent
                    ):
                        await self._continuity.async_record_success(
                            resolution.key, chat_log.content
                        )
                    return result
                async with self._usage.async_run(
                    home_assistant_conversation_id=user_input.conversation_id,
                    source_device_id=source_device_id,
                ) as run:
                    result = await self._async_handle_message(user_input, chat_log)
                    if self._archive is not None and archive_session is not None:
                        assistant_text = ""
                        if chat_log.content and isinstance(
                            chat_log.content[-1], conversation.AssistantContent
                        ):
                            assistant_text = chat_log.content[-1].content or ""
                        await self._archive.async_record_turn(
                            archive_session.session_id,
                            run_id=run.run_id,
                            user_text=user_input.text,
                            assistant_text=assistant_text,
                            successful=run.successful,
                        )
                    if run.successful:
                        await self._continuity.async_record_success(
                            resolution.key, chat_log.content
                        )
                    return result
            finally:
                await self._continuity.async_release(resolution.key)
                _ACTIVE_FUNCTION_GROUP_SESSION.reset(function_group_token)
                _ACTIVE_TEMPORARY_SCOPE.reset(temporary_token)
                _ACTIVE_ARCHIVE.reset(archive_token)
                _ACTIVE_SCOPE.reset(scope_token)

    async def _async_handle_message(
        self,
        user_input: ConversationInput,
        chat_log: ChatLog,
    ) -> ConversationResult:
        """Call the API."""
        # Create LLM context
        llm_context = user_input.as_llm_context(DOMAIN)

        # Get exposed entities for function tools
        exposed_entities = self._get_exposed_entities()

        # Get function tools
        function_tools = self._get_function_tools()

        retrieved_memories = await self._async_retrieve_memories(
            llm_context, user_input.text
        )
        temporary_memories = await self._async_retrieve_temporary_memories()

        # Build custom prompt with exposed entities
        system_prompt = self._build_system_prompt(
            exposed_entities,
            llm_context,
            user_input,
            retrieved_memories,
            temporary_memories,
        )

        # Set system prompt in chat log
        chat_log.content[0] = conversation.SystemContent(content=system_prompt)

        # Call the LLM

        try:
            continue_mode = _get_continue_conversation_mode(self.subentry.data)
            conditional_decision = await self._async_handle_chat_log(
                chat_log,
                function_tools=function_tools,
                exposed_entities=exposed_entities,
                llm_context=llm_context,
                conditional_continue=(
                    continue_mode == CONTINUE_CONVERSATION_CONDITIONAL
                ),
                function_tools_factory=self._get_function_tools,
                function_group_loader=(
                    self._load_function_groups
                    if _ACTIVE_FUNCTION_GROUP_SESSION.get() is not None
                    else None
                ),
            )
        except OpenAIError as err:
            if self._usage is not None:
                self._usage.mark_current_run_failed(type(err).__name__)
            _LOGGER.error(err)
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Sorry, I had a problem talking to OpenAI: {err}",
            )
            return conversation.ConversationResult(
                response=intent_response, conversation_id=user_input.conversation_id
            )
        except HomeAssistantError as err:
            if self._usage is not None:
                self._usage.mark_current_run_failed(type(err).__name__)
            _LOGGER.error("Error during conversation: %s", err, exc_info=True)
            intent_response = intent.IntentResponse(language=user_input.language)
            intent_response.async_set_error(
                intent.IntentResponseErrorCode.UNKNOWN,
                f"Something went wrong: {err}",
            )
            return conversation.ConversationResult(
                response=intent_response, conversation_id=user_input.conversation_id
            )

        # Fire conversation finished event
        self.hass.bus.async_fire(
            EVENT_CONVERSATION_FINISHED,
            {
                "user_input": user_input,
                "messages": [c.as_dict() for c in chat_log.content],
                "agent_id": self.subentry.subentry_id,
            },
        )

        # Build response from chat log
        intent_response = intent.IntentResponse(language=user_input.language)

        # Get last assistant message
        last_content = chat_log.content[-1]
        if isinstance(last_content, conversation.AssistantContent):
            original_text = last_content.content or ""
            speech_text = process_speech_text(original_text, self.subentry.data)
            intent_response.async_set_speech(speech_text)
        else:
            intent_response.async_set_speech("")

        return ConversationResult(
            response=intent_response,
            conversation_id=chat_log.conversation_id,
            continue_conversation=_resolve_continue_conversation(
                continue_mode,
                chat_log.continue_conversation,
                conditional_decision,
            ),
        )

    def _build_system_prompt(
        self,
        exposed_entities: list[dict],
        llm_context: llm.LLMContext,
        user_input: ConversationInput,
        memories: list[MemoryRecord] | None = None,
        temporary_memories: list[TemporaryMemoryRecord] | None = None,
    ) -> str:
        """Build system prompt with exposed entities and skills."""
        return render_effective_prompt(
            self.hass,
            self.subentry.data,
            exposed_entities=exposed_entities,
            current_device_id=llm_context.device_id,
            user_input=user_input,
            skills=self._get_enabled_skills(),
            memories=memories,
            temporary_memories=temporary_memories,
            knowledge_available=self._knowledge_available,
        ).text

    async def _async_retrieve_memories(
        self, llm_context: llm.LLMContext, query: str
    ) -> list[MemoryRecord]:
        """Retrieve bounded automatic context when memory is enabled."""
        if self._memory is None:
            return []
        scope_id = self._current_memory_scope_id(llm_context)
        if scope_id is None:
            return []
        try:
            retrieve_limit = int(
                self.subentry.data.get(
                    CONF_MEMORY_AUTO_RETRIEVE_LIMIT,
                    DEFAULT_MEMORY_AUTO_RETRIEVE_LIMIT,
                )
            )
            if retrieve_limit <= 0:
                return []
            return await self._memory.async_search(
                scope_id, query, limit=retrieve_limit
            )
        except Exception:
            _LOGGER.exception("Automatic memory retrieval failed; continuing")
            return []

    async def _async_retrieve_temporary_memories(
        self,
    ) -> list[TemporaryMemoryRecord]:
        """Inject all active bounded facts for the safe continuity scope."""
        scope_id = _ACTIVE_TEMPORARY_SCOPE.get()
        if self._temporary_memory is None or scope_id is None:
            return []
        try:
            return await self._temporary_memory.async_active(scope_id)
        except Exception:
            _LOGGER.exception("Temporary-memory retrieval failed; continuing")
            return []

    def _get_enabled_skills(self) -> list[Skill]:
        """Get enabled skills as list for template rendering."""
        enabled_skill_names = self.skills
        all_skills = self.skill_manager.get_all_skills()

        return [s for s in all_skills if s.name in enabled_skill_names]

    def _get_exposed_entities(self) -> list[dict[str, Any]]:
        return get_exposed_entities(self.hass)

    def _get_function_tools(self) -> list[dict[str, Any]]:
        """Get the effective configured and integration-owned function tools."""
        try:
            configured_tools = self._get_configured_function_tools()
            groups = validate_function_groups(
                self.subentry.data.get(
                    CONF_FUNCTION_GROUPS, list(DEFAULT_FUNCTION_GROUPS)
                ),
                configured_tools,
            )
            session = _ACTIVE_FUNCTION_GROUP_SESSION.get()
            assembly = assemble_function_tools(
                configured_tools,
                groups,
                session.loaded_group_ids if session is not None else set(),
            )
            if self._function_groups_runtime is not None:
                self._function_groups_runtime.record_request(assembly)
            result = list(assembly.tools)
            configured_names = {
                tool.get("spec", {}).get("name")
                for tool in configured_tools
                if isinstance(tool, dict)
            }
            result.extend(
                assemble_integration_function_tools(
                    self.subentry.data,
                    configured_names,
                    memory_scope_available=self._current_memory_scope_id() is not None,
                    temporary_scope_available=(
                        self._temporary_memory is not None
                        and _ACTIVE_TEMPORARY_SCOPE.get() is not None
                    ),
                    knowledge_available=self._knowledge_available,
                )
            )
            return result
        except (InvalidFunction, FunctionNotFound) as e:
            raise e
        except Exception as e:
            raise FunctionLoadFailed() from e

    def _get_configured_function_tools(self) -> list[dict[str, Any]]:
        """Parse and validate only user-configured tools without changing storage."""
        return self._configured_function_tools_from_data(self.subentry.data)

    def _configured_function_tools_from_data(self, data: Any) -> list[dict[str, Any]]:
        """Parse configured tools from one current or persisted data mapping."""
        return configured_function_tools_from_data(data)

    def _load_function_groups(self, requested: Any) -> dict[str, Any]:
        """Apply an integration-owned loader call to the active conversation."""
        session = _ACTIVE_FUNCTION_GROUP_SESSION.get()
        if session is None:
            return {
                "status": "error",
                "error": "No active conversation is available for group loading",
            }
        configured_tools = self._get_configured_function_tools()
        groups = validate_function_groups(
            self.subentry.data.get(CONF_FUNCTION_GROUPS, list(DEFAULT_FUNCTION_GROUPS)),
            configured_tools,
        )
        return load_function_groups(session, requested, groups, configured_tools)

    async def _execute_function_tool(
        self,
        function_tool: dict[str, Any],
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext | None,
        exposed_entities: list[dict[str, Any]],
    ) -> conversation.ToolResultContent:
        """Execute an integration-owned tool or a configured tool."""
        function_type = function_tool.get("function", {}).get("type")
        if function_type not in {
            "memory",
            "temporary_memory",
            "knowledge",
            "archive",
        }:
            tool_name = function_tool.get("spec", {}).get("name")
            latest_entry = self.hass.config_entries.async_get_entry(self.entry.entry_id)
            latest_subentry = (
                latest_entry.subentries.get(self.subentry.subentry_id)
                if latest_entry is not None
                else None
            )
            current_tool = next(
                (
                    tool
                    for tool in self._configured_function_tools_from_data(
                        latest_subentry.data
                        if latest_subentry is not None
                        else self.subentry.data
                    )
                    if tool.get("spec", {}).get("name") == tool_name
                ),
                None,
            )
            if current_tool is None:
                raise FunctionNotFound(str(tool_name))
            if not function_tool_enabled(function_tool) or not function_tool_enabled(
                current_tool
            ):
                raise HomeAssistantError(f"Function Tool `{tool_name}` is disabled")
            return await super()._execute_function_tool(
                function_tool, tool_input, llm_context, exposed_entities
            )

        try:
            if function_type == "archive":
                result = await self._async_execute_archive_tool(
                    function_tool["function"]["operation"], tool_input.tool_args
                )
            elif function_type == "knowledge":
                result = await self._async_execute_knowledge_tool(
                    function_tool["function"]["operation"], tool_input.tool_args
                )
            elif function_type == "memory":
                result = await self._async_execute_memory_tool(
                    function_tool["function"]["operation"],
                    tool_input.tool_args,
                    llm_context,
                )
            else:
                result = await self._async_execute_temporary_memory_tool(
                    function_tool["function"]["operation"], tool_input.tool_args
                )
        except (RuntimeError, ValueError) as err:
            result = {"status": "error", "error": str(err)}
        except Exception:
            if function_type in {"memory", "temporary_memory"}:
                _LOGGER.exception("Memory tool failed")
                result = {
                    "status": "unavailable",
                    "error": "Memory is temporarily unavailable",
                }
            else:
                _LOGGER.exception("Knowledge Library tool failed")
                result = {
                    "status": "unavailable",
                    "error": "Knowledge Library is temporarily unavailable",
                }

        return conversation.ToolResultContent(
            agent_id=self.entity_id,
            tool_call_id=tool_input.id,
            tool_name=tool_input.tool_name,
            tool_result={"result": json.dumps(result, ensure_ascii=False)},
        )

    @property
    def _knowledge_available(self) -> bool:
        """Return whether an enabled, populated library is ready."""
        return bool(
            self.subentry.data.get(CONF_KNOWLEDGE_ENABLED, False)
            and self._knowledge is not None
            and self._knowledge.source_count > 0
        )

    async def _async_execute_knowledge_tool(
        self, operation: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a read-only Knowledge Library operation."""
        if not self._knowledge_available or self._knowledge is None:
            raise RuntimeError("Knowledge Library is unavailable")
        if operation == "search":
            query = arguments.get("query")
            source_ids = arguments.get("source_ids")
            limit = arguments.get("limit", 5)
            if (
                not isinstance(query, str)
                or (
                    source_ids is not None
                    and (
                        not isinstance(source_ids, list)
                        or not all(isinstance(item, str) for item in source_ids)
                    )
                )
                or not isinstance(limit, int)
                or isinstance(limit, bool)
            ):
                raise ValueError("query, source_ids, or limit has an invalid type")
            allowed_ids, ignored_ids = self._knowledge.resolve_source_filter(source_ids)
            results = await self._knowledge.async_search(
                query, sorted(allowed_ids) if allowed_ids else None, limit
            )
            filter_requested = bool(source_ids)
            return {
                "results": [search_result_as_dict(result) for result in results],
                "source_filter": {
                    "applied_source_ids": sorted(allowed_ids or []),
                    "ignored_source_ids": ignored_ids,
                    "fell_back_to_all_sources": filter_requested
                    and allowed_ids is None,
                },
            }
        if operation == "list":
            query = arguments.get("query")
            limit = arguments.get("limit", 20)
            offset = arguments.get("offset", 0)
            if (
                (query is not None and not isinstance(query, str))
                or not isinstance(limit, int)
                or isinstance(limit, bool)
                or not isinstance(offset, int)
                or isinstance(offset, bool)
            ):
                raise ValueError("query, limit, or offset has an invalid type")
            return await self._knowledge.async_catalog(query, limit, offset)
        if operation == "get":
            source_id = arguments.get("source_id")
            start = arguments.get("start_character", 0)
            maximum = arguments.get("max_characters", 6_000)
            if (
                not isinstance(source_id, str)
                or not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(maximum, int)
                or isinstance(maximum, bool)
            ):
                raise ValueError(
                    "source_id, start_character, or max_characters has an invalid type"
                )
            return await self._knowledge.async_get_section(source_id, start, maximum)
        raise ValueError("unknown knowledge operation")

    async def _async_execute_memory_tool(
        self,
        operation: str,
        arguments: dict[str, Any],
        llm_context: llm.LLMContext | None,
    ) -> dict[str, Any]:
        """Execute a scoped persistent-memory operation."""
        if self._memory is None:
            raise RuntimeError("persistent memory is unavailable")
        user_id = self._current_memory_scope_id(llm_context)
        if user_id is None:
            raise RuntimeError("persistent memory is disabled for this data scope")

        if operation == "add":
            source = arguments.get("source")
            content = arguments.get("content")
            category = arguments.get("category")
            if (
                not isinstance(content, str)
                or not isinstance(category, str)
                or not isinstance(source, str)
            ):
                raise ValueError("content, category, and source must be strings")
            if source == "implicit" and not automatic_memory_enabled(
                self.subentry.data
            ):
                raise ValueError("automatic memory creation is disabled")
            scope = _ACTIVE_SCOPE.get()
            if (
                source == "implicit"
                and scope is not None
                and scope.scope_type == "shared"
                and self.subentry.data.get(
                    CONF_SHARED_MEMORY_MODE, DEFAULT_SHARED_MEMORY_MODE
                )
                != SHARED_MEMORY_AUTOMATIC
            ):
                raise ValueError("automatic shared memory creation is disabled")
            return await self._memory.async_add(
                user_id,
                content,
                category,
                source,
            )
        if operation == "search":
            query = arguments.get("query")
            category = arguments.get("category")
            limit = arguments.get("limit", 5)
            if (
                not isinstance(query, str)
                or (category is not None and not isinstance(category, str))
                or not isinstance(limit, int)
                or isinstance(limit, bool)
            ):
                raise ValueError("query, category, or limit has an invalid type")
            memories = await self._memory.async_search(
                user_id,
                query,
                category,
                limit,
            )
            return {"memories": [memory_as_dict(memory) for memory in memories]}
        if operation == "list":
            category = arguments.get("category")
            limit = arguments.get("limit", 50)
            offset = arguments.get("offset", 0)
            if (
                (category is not None and not isinstance(category, str))
                or not isinstance(limit, int)
                or isinstance(limit, bool)
                or not isinstance(offset, int)
                or isinstance(offset, bool)
            ):
                raise ValueError("category, limit, or offset has an invalid type")
            memories = await self._memory.async_list(
                user_id,
                category,
                limit,
                offset,
            )
            return {"memories": [memory_as_dict(memory) for memory in memories]}
        if operation == "update":
            memory_id = arguments.get("memory_id")
            content = arguments.get("content")
            category = arguments.get("category")
            if (
                not isinstance(memory_id, str)
                or (content is not None and not isinstance(content, str))
                or (category is not None and not isinstance(category, str))
                or (content is None and category is None)
            ):
                raise ValueError("memory_id and at least one valid update are required")
            memory = await self._memory.async_update(
                user_id,
                memory_id,
                content,
                category,
            )
            return {"status": "updated", "memory": memory_as_dict(memory)}
        if operation == "delete":
            memory_ids = arguments.get("memory_ids")
            if not isinstance(memory_ids, list) or not all(
                isinstance(memory_id, str) for memory_id in memory_ids
            ):
                raise ValueError("memory_ids must be a list of strings")
            deleted = await self._memory.async_delete(user_id, memory_ids)
            return {"status": "deleted", "deleted": deleted}
        raise ValueError("unknown memory operation")

    def _current_memory_scope_id(
        self, llm_context: llm.LLMContext | None = None
    ) -> str | None:
        """Use the session-stable resolver for all memory operations."""
        scope = _ACTIVE_SCOPE.get()
        if scope is None:
            # Compatibility for direct service/test invocations outside the
            # conversation lifecycle. Live conversations always set a resolved
            # scope before tools are assembled.
            return memory_user_id(llm_context)
        if (
            scope.scope_type == "shared"
            and self.subentry.data.get(
                CONF_SHARED_MEMORY_MODE, DEFAULT_SHARED_MEMORY_MODE
            )
            == SHARED_MEMORY_DISABLED
        ):
            return None
        return memory_scope_id(scope)

    async def _async_execute_temporary_memory_tool(
        self, operation: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute a narrow operation within the request-derived scope."""
        if self._temporary_memory is None:
            raise RuntimeError("temporary memory is unavailable")
        scope_id = _ACTIVE_TEMPORARY_SCOPE.get()
        if scope_id is None:
            raise RuntimeError("temporary memory is unavailable for this request")
        if operation == "add":
            content = arguments.get("content")
            expires_at = arguments.get("expires_at")
            category = arguments.get("category", "general")
            if (
                not isinstance(content, str)
                or not isinstance(expires_at, str)
                or not isinstance(category, str)
            ):
                raise ValueError("content, expires_at, and category must be strings")
            return await self._temporary_memory.async_add(
                scope_id, content, expires_at, category
            )
        if operation == "update":
            memory_id = arguments.get("memory_id")
            if not isinstance(memory_id, str):
                raise ValueError("memory_id is required")
            record = await self._temporary_memory.async_update(
                scope_id,
                memory_id,
                arguments.get("content"),
                arguments.get("expires_at"),
                arguments.get("category"),
            )
            return {
                "status": "updated",
                "memory": temporary_memory_as_dict(record),
            }
        if operation == "delete":
            memory_ids = arguments.get("memory_ids")
            if not isinstance(memory_ids, list) or not all(
                isinstance(memory_id, str) for memory_id in memory_ids
            ):
                raise ValueError("memory_ids must be a list of strings")
            return {
                "status": "deleted",
                "deleted": await self._temporary_memory.async_delete(
                    scope_id, memory_ids
                ),
            }
        raise ValueError("unknown temporary-memory operation")

    async def _async_execute_archive_tool(
        self, operation: str, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        """Execute scoped archive, privacy, and exact-session deletion actions."""
        if self._archive is None:
            raise RuntimeError("conversation archive is unavailable")
        scope = _ACTIVE_SCOPE.get()
        active = _ACTIVE_ARCHIVE.get()
        if scope is None or active is None:
            raise RuntimeError("active conversation session is unavailable")
        session_key, session_id = active
        if operation == "search":
            if not self.subentry.data.get(
                CONF_ARCHIVE_MODEL_SEARCH_ENABLED,
                DEFAULT_ARCHIVE_MODEL_SEARCH_ENABLED,
            ):
                raise RuntimeError("model archive search is disabled")
            query = arguments.get("query")
            if not isinstance(query, str):
                raise ValueError("query is required")
            return await self._archive.async_search(
                scope.scope_id,
                query,
                start_date=arguments.get("start_date"),
                end_date=arguments.get("end_date"),
                limit=int(arguments.get("limit", 5)),
            )
        if operation == "get":
            requested = arguments.get("session_id")
            if not isinstance(requested, str):
                raise ValueError("session_id is required")
            return await self._archive.async_get(
                scope.scope_id,
                requested,
                int(arguments.get("start_turn", 0)),
                int(arguments.get("limit", 6)),
            )
        if operation == "private":
            return await self._archive.async_make_private(session_id)
        if operation == "resume":
            session = await self._archive.async_resume_saving(
                session_key, session_id, scope
            )
            _ACTIVE_ARCHIVE.set((session_key, session.session_id))
            return {
                "private_mode_enabled": False,
                "session_id": session.session_id,
                "future_turns_retained": session.retention_state == "retained",
                "private_content_restored": False,
            }
        if operation == "delete_current":
            result = await self._archive.async_delete_session(
                scope.scope_id, session_id
            )
            return {"session_id": session_id, **result}
        if operation == "delete_selected":
            session_ids = arguments.get("session_ids")
            if not isinstance(session_ids, list) or not all(
                isinstance(value, str) for value in session_ids
            ):
                raise ValueError("session_ids must be a list of strings")
            return await self._archive.async_delete_selected(
                scope.scope_id,
                session_ids,
                confirm=arguments.get("confirm") is True,
            )
        if operation == "delete_range":
            start_date = arguments.get("start_date")
            end_date = arguments.get("end_date")
            if not isinstance(start_date, str) or not isinstance(end_date, str):
                raise ValueError("start_date and end_date are required")
            return await self._archive.async_delete_date_range(
                scope.scope_id,
                start_date,
                end_date,
                confirm=arguments.get("confirm") is True,
            )
        raise ValueError("unknown archive operation")


def _resolve_continue_conversation(
    mode: str,
    ha_default: bool,
    conditional_decision: bool | None,
) -> bool:
    """Resolve the configured continuation behavior for a successful response."""
    if mode == CONTINUE_CONVERSATION_ALWAYS:
        return True
    if mode == CONTINUE_CONVERSATION_CONDITIONAL:
        return conditional_decision is True
    return ha_default


def _get_continue_conversation_mode(options: Mapping[str, Any]) -> str:
    """Return the mode, preserving HA Default for existing config entries."""
    return str(options.get(CONF_CONTINUE_CONVERSATION, DEFAULT_CONTINUE_CONVERSATION))
