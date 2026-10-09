"""AI Task integration for Extended OpenAI Conversation (Responses)."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
from json import JSONDecodeError
import logging
from typing import TYPE_CHECKING, Any

from openai import OpenAIError

from homeassistant.components import ai_task, conversation
from homeassistant.components.ai_task.const import DEFAULT_SYSTEM_PROMPT
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.json import json_loads

from .debug import record_current_provider_failure
from .entity import (
    ExtendedOpenAIBaseLLMEntity,
    _make_schema_nullable,
    _serialize_structured_output,
)
from .ha_llm_tools import ToolSnapshot, caller_api_tools, tool_snapshot_scope
from .ha_schema import SchemaValidator
from .provider_errors import log_provider_failure, request_reauthentication
from .schema_errors import SCHEMA_ERRORS

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _OptionalNullPlan:
    """Retain caller field semantics once, including alternative branches."""

    optional_nonnullable: frozenset[str]
    properties: dict[str, _OptionalNullPlan]
    items: _OptionalNullPlan | None
    alternatives: list[_OptionalNullPlan] = field(default_factory=list)
    validator: Any = None
    reference: _OptionalNullPlan | None = None

    def apply(self, data: Any) -> tuple[Any, int]:
        """Return cleaned data and a count of removed placeholders."""
        if self.validator is not None and self.validator.is_valid(data):
            return data, 0
        removed = 0
        if self.reference is not None:
            data, removed = self.reference.apply(data)
        if isinstance(data, dict):
            cleaned = {}
            for key, value in data.items():
                if value is None and key in self.optional_nonnullable:
                    removed += 1
                    continue
                if key in self.properties:
                    value, count = self.properties[key].apply(value)
                    removed += count
                cleaned[key] = value
            data = cleaned
        elif isinstance(data, list) and self.items is not None:
            cleaned_items = []
            for item in data:
                value, count = self.items.apply(item)
                cleaned_items.append(value)
                removed += count
            data = cleaned_items

        if self.validator is None or self.validator.is_valid(data):
            return data, removed
        # Try alternatives independently. Applying every branch in succession
        # would erase nulls legitimately accepted by a different branch.
        candidates = []
        for branch in self.alternatives:
            candidate, count = branch.apply(data)
            if self.validator.is_valid(candidate):
                candidates.append((candidate, count))
        if candidates:
            candidate, count = min(candidates, key=lambda item: item[1])
            return candidate, removed + count
        # Leave invalid data for the authoritative caller validator to reject.
        return data, removed


def _caller_object_fields(
    schema: dict[str, Any],
) -> tuple[dict[str, Any], set[str]]:
    """Combine intersected fields without making a required null removable."""
    properties = dict(schema.get("properties", {}))
    required = set(schema.get("required", []))
    for branch in schema.get("allOf", []):
        branch_properties, branch_required = _caller_object_fields(branch)
        required.update(branch_required)
        for key, value in branch_properties.items():
            properties[key] = (
                {"allOf": [properties[key], value]} if key in properties else value
            )
    return properties, required


def _optional_null_plan(
    schema: dict[str, Any],
    *,
    root: dict[str, Any] | None = None,
    memo: dict[int, _OptionalNullPlan] | None = None,
    validator: Any = None,
    resolver: Any = None,
) -> _OptionalNullPlan:
    from jsonschema.validators import validator_for
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012, specification_with

    root = schema if root is None else root
    memo = {} if memo is None else memo
    if id(schema) in memo:
        return memo[id(schema)]
    validator = validator_for(root)(root) if validator is None else validator
    specification = specification_with(root.get("$schema", ""), default=DRAFT202012)
    if resolver is None:
        resolver = Registry().resolver_with_root(
            Resource.from_contents(root, default_specification=specification)
        )

    class ScopedValidator:
        """Validate a branch with the same resource scope used by its plan."""

        def is_valid(self, data):
            return (
                next(validator.descend(data, schema, resolver=resolver), None) is None
            )

    def child(value, *, resolved_scope=None):
        child_resolver = (
            resolved_scope
            if resolved_scope is not None
            else resolver.in_subresource(
                Resource.from_contents(value, default_specification=specification)
            )
        )
        return _optional_null_plan(
            value, root=root, memo=memo, validator=validator, resolver=child_resolver
        )

    def allows_null(value):
        child_resolver = resolver.in_subresource(
            Resource.from_contents(value, default_specification=specification)
        )
        return (
            next(validator.descend(None, value, resolver=child_resolver), None) is None
        )

    properties, required = _caller_object_fields(schema)
    plan = _OptionalNullPlan(
        optional_nonnullable=frozenset(
            key
            for key, value in properties.items()
            if key not in required and not allows_null(value)
        ),
        properties={},
        items=None,
    )
    memo[id(schema)] = plan

    reference = schema.get("$ref")
    if isinstance(reference, str) and reference.startswith("#"):
        # A local pointer belongs to the nearest resource, which can be a nested
        # $id. lookup also retains that scope when a pointer enters a resource.
        resolved = resolver.lookup(reference)
        if isinstance(resolved.contents, dict):
            plan.reference = child(resolved.contents, resolved_scope=resolved.resolver)
    plan.properties = {key: child(value) for key, value in properties.items()}
    if isinstance(schema.get("items"), dict):
        plan.items = child(schema["items"])
    for keyword in ("anyOf", "oneOf"):
        for branch in schema.get(keyword, []):
            plan.alternatives.append(child(branch))
    if plan.alternatives or "allOf" in schema:
        # Imported only for composed AI Task schemas, never ordinary tool calls.
        plan.validator = ScopedValidator()
    return plan


def _normalize_caller_nullable(schema: dict[str, Any]) -> None:
    """Express OpenAPI nullable for branch validation without adding placeholders."""
    if schema.pop("nullable", False):
        _make_schema_nullable(schema)
    for keyword in ("anyOf", "oneOf", "allOf"):
        for branch in schema.get(keyword, []):
            _normalize_caller_nullable(branch)
    for value in schema.get("properties", {}).values():
        _normalize_caller_nullable(value)
    for keyword in ("$defs", "definitions"):
        for value in schema.get(keyword, {}).values():
            if isinstance(value, dict):
                _normalize_caller_nullable(value)
    if isinstance(schema.get("items"), dict):
        _normalize_caller_nullable(schema["items"])


def _omit_optional_nulls(data: Any, schema: dict[str, Any]) -> Any:
    """Undo placeholders with a plan reused for every object in array items."""
    normalized = deepcopy(schema)
    _normalize_caller_nullable(normalized)
    return _optional_null_plan(normalized).apply(data)[0]


def parse_ai_task_structured_response(
    text: str,
    structure: SchemaValidator | None = None,
    *,
    original_schema: dict[str, Any] | None = None,
) -> Any:
    """Parse and validate the caller's contract without exposing task contents."""
    try:
        data = json_loads(text)
    except JSONDecodeError as err:
        _LOGGER.error("Failed to parse structured AI Task JSON response: %s", err)
        raise HomeAssistantError("Error with structured response") from err
    if original_schema is not None:
        data = _omit_optional_nulls(data, original_schema)
    return _validate_structured_response(data, structure)


def _validate_structured_response(data: Any, structure: SchemaValidator | None) -> Any:
    """Keep the caller's authoritative HA validation on the event loop."""
    if structure is not None:
        try:
            return structure(data)
        except SCHEMA_ERRORS:
            # Selector errors may include model output. Expose a stable task
            # failure and keep validation details out of logs and public errors.
            raise HomeAssistantError(
                "AI Task result does not match the requested structure"
            ) from None
    return data


if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry

    from . import ExtendedOpenAIConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up AI Task entities."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != "ai_task_data":
            continue

        async_add_entities(
            [ExtendedOpenAITaskEntity(config_entry, subentry)],
            config_subentry_id=subentry.subentry_id,
        )


class ExtendedOpenAITaskEntity(
    ai_task.AITaskEntity,
    ExtendedOpenAIBaseLLMEntity,
):
    """Extended OpenAI AI Task entity."""

    def __init__(
        self, entry: ExtendedOpenAIConfigEntry, subentry: ConfigSubentry
    ) -> None:
        """Initialize the entity."""
        super().__init__(entry, subentry)
        self._attr_supported_features = (
            ai_task.AITaskEntityFeature.GENERATE_DATA
            | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
        )

    async def _async_generate_data(
        self,
        task: ai_task.GenDataTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenDataTaskResult:
        """Handle a generate data task."""
        # Core has already assembled a caller-supplied API with the task context.
        # Reuse our exchange/budget; never import this agent's custom Functions.
        snapshot, tools = (
            caller_api_tools(chat_log.llm_api)
            if chat_log.llm_api is not None
            else (ToolSnapshot(), [])
        )
        try:
            if chat_log.llm_api is not None:
                caller_instance = chat_log.llm_api
                # Ask Core to render its normal task baseline without source
                # prompts. Our per-round exposure now owns those intact fragments.
                # Retain the caller serializer for structured output conversion.
                await chat_log.async_provide_llm_data(
                    llm_context=caller_instance.llm_context,
                    user_llm_prompt=DEFAULT_SYSTEM_PROMPT,
                )
                chat_log.llm_api = caller_instance
            original_schema = (
                _serialize_structured_output(task.structure, chat_log.llm_api)
                if task.structure is not None
                else None
            )
            with tool_snapshot_scope(snapshot):
                await self._async_handle_chat_log(
                    chat_log,
                    function_tools=tools,
                    exposed_entities=[],
                    llm_context=chat_log.llm_api.llm_context
                    if chat_log.llm_api
                    else None,
                    structure_name=task.name,
                    structure=task.structure,
                    structure_schema=original_schema,
                )
        except OpenAIError as err:
            from .model_lifecycle import record_entity_retirement_failure

            record_entity_retirement_failure(self, err, logger=_LOGGER)
            request_reauthentication(self.hass, getattr(self, "entry", None), err)
            record_current_provider_failure(err)
            log_provider_failure(_LOGGER, "OpenAI AI Task request failed", err)
            raise

        from .model_lifecycle import clear_entity_retirement_failure

        clear_entity_retirement_failure(self)

        # Extract response
        if not isinstance(chat_log.content[-1], conversation.AssistantContent):
            raise HomeAssistantError(
                "Last content in chat log is not an AssistantContent"
            )

        text = chat_log.content[-1].content or ""

        # Handle structured output
        if not task.structure:
            return ai_task.GenDataTaskResult(
                conversation_id=chat_log.conversation_id,
                data=text,
            )

        # JSON parsing and schema branch matching touch only this response and
        # its schema snapshot. Large arrays must not stall HA's event loop.
        data = await asyncio.to_thread(
            parse_ai_task_structured_response,
            text,
            original_schema=original_schema,
        )
        data = _validate_structured_response(data, task.structure)

        return ai_task.GenDataTaskResult(
            conversation_id=chat_log.conversation_id,
            data=data,
        )
