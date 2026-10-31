"""Prevent a lost provider continuation from replaying completed side effects."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from functools import wraps
import hashlib
import json
from typing import Any

from homeassistant.components import conversation
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

from .const import DOMAIN
from .exceptions import EffectReplayBlocked
from .execution_arguments import execution_identity
from .function_execution import (
    _schema_without_patterns,
    retained_function_input,
    validate_function_arguments,
)
from .ha_tool_result_compat import is_tool_result_content
from .parallel_tool_execution import is_parallel_safe_integration_tool
from .schema_errors import SCHEMA_ERRORS

_MAX_AMBIGUOUS_CONVERSATIONS = 32
_MAX_RETIRED_CONVERSATIONS = 4096
_MAX_DISPATCH_RECORDS = 4096
_PRECISE_EFFECT_KINDS = {
    "ha_service",
    "automation_update",
    "broadcast",
    "file_write",
    "file_edit",
}


@dataclass
class ReplaySafetyState:
    """Bounded unresolved evidence; no live calls or invocation ownership."""

    effects: dict[str, set[str]] = field(default_factory=dict)
    schemas: dict[str, dict[str, Any]] = field(default_factory=dict)
    uncertain_tools: dict[str, set[str]] = field(default_factory=dict)
    retired_conversations: set[bytes] = field(default_factory=set)
    history_incomplete: bool = False
    acknowledged_conversations: dict[bytes, None] = field(default_factory=dict)


def get_agent_replay_safety(
    hass: Any, entry_id: str, subentry_id: str
) -> ReplaySafetyState:
    """Retain safety across ordinary reload; deletion owns final disposal."""
    owners: dict[tuple[str, str], ReplaySafetyState] = hass.data.setdefault(
        f"{DOMAIN}.replay_safety_states", {}
    )
    key = (entry_id, subentry_id)
    if key not in owners:
        owners[key] = ReplaySafetyState()
    return owners[key]


def replay_safety(entity: Any) -> ReplaySafetyState:
    """Use the bound agent owner, or local state for standalone/AI Task work."""
    state = getattr(entity, "_replay_safety", None)
    if not isinstance(state, ReplaySafetyState):
        state = entity._replay_safety = ReplaySafetyState()
    return state


def has_unacknowledged_calls(entity: Any, conversation_id: str) -> bool:
    return bool(replay_safety(entity).schemas.get(conversation_id))


def _retire_replay_history(entity: Any, conversation_id: str | None) -> None:
    """Keep bounded conservative evidence when precise history is discarded."""
    if conversation_id == "":
        return  # Independent deferred jobs have no failed provider continuation.
    state = replay_safety(entity)
    retired = state.retired_conversations
    digest = (
        hashlib.sha256(conversation_id.encode()).digest()
        if conversation_id is not None
        else None
    )
    if digest is not None:
        state.acknowledged_conversations.pop(digest, None)
    if digest is None or (
        digest not in retired and len(retired) >= _MAX_RETIRED_CONVERSATIONS
    ):
        state.history_incomplete = True
        if conversation_id is None:
            state.acknowledged_conversations.clear()
    else:
        retired.add(digest)


def _replay_history_incomplete(entity: Any, conversation_id: str) -> bool:
    state = replay_safety(entity)
    digest = hashlib.sha256(conversation_id.encode()).digest()
    return digest in state.retired_conversations or (
        state.history_incomplete and digest not in state.acknowledged_conversations
    )


def _forget_dispatch(entity: Any, key: int, *, retire: bool = False) -> None:
    """Dispose one call's bookkeeping together; unknown losses remain unsafe."""
    if (
        retire
        and key not in getattr(entity, "_read_only_tool_inputs", {})
        and getattr(entity, "_execution_tool_outcomes", {}).get(key) != "rejected"
    ):
        _retire_replay_history(
            entity, getattr(entity, "_dispatched_tool_conversations", {}).get(key)
        )
    for name in (
        "_dispatched_tool_inputs",
        "_dispatched_tool_signatures",
        "_dispatched_tool_conversations",
        "_read_only_tool_inputs",
        "_execution_tool_outcomes",
        "_execution_effect_receipts",
    ):
        getattr(entity, name, {}).pop(key, None)


@dataclass
class _EffectReplayScope:
    """A lease that expires even in task contexts inherited from the exchange."""

    entity: Any
    conversation_id: str
    active: bool = True


_effect_replay_scope: ContextVar[_EffectReplayScope | None] = ContextVar(
    "effect_replay_scope", default=None
)


@contextmanager
def bind_effect_replay_scope(
    entity: Any, conversation_id: str | None
) -> Iterator[None]:
    """Keep effect recovery local to one entity and conversation, including tasks."""
    scope = (
        _EffectReplayScope(entity, conversation_id)
        if isinstance(conversation_id, str) and conversation_id
        else None
    )
    token = _effect_replay_scope.set(scope)
    try:
        yield
    finally:
        if scope is not None:
            scope.active = False
        _effect_replay_scope.reset(token)


def with_effect_replay_scope(
    function: Callable[..., Awaitable[None]],
) -> Callable[..., Awaitable[None]]:
    @wraps(function)
    async def scoped(entity: Any, chat_log: Any, *args: Any, **kwargs: Any) -> None:
        with bind_effect_replay_scope(
            entity, getattr(chat_log, "conversation_id", None)
        ):
            await function(entity, chat_log, *args, **kwargs)

    return scoped


class UnidentifiableEffect(ValueError):
    """Prepared values cannot establish a reliable operation identity."""


def effect_signature(kind: str, prepared: Any) -> str:
    """Identify the actual prepared operation, independently of its wrapper."""

    from homeassistant.helpers.template import Template

    from .functions.base import _RuntimeFunctionConfig

    def canonical(value: Any) -> Any:
        if isinstance(value, _RuntimeFunctionConfig):
            # Match the source config, without copying hydrated HA objects or
            # retaining the shared arguments in every effect receipt.
            return canonical(value._persisted_config)
        if isinstance(value, Template):
            return {
                "template": [
                    type(value).__module__,
                    type(value).__qualname__,
                    value.template,
                ]
            }
        # HA/template preparation can produce mappings with non-string keys.
        # Preserve their types rather than failing sort_keys or conflating 1 and
        # "1". All mappings get a tagged representation, avoiding marker clashes.
        if isinstance(value, Mapping):
            pairs = [
                [
                    json.dumps(
                        [type(key).__module__, type(key).__qualname__, canonical(key)],
                        sort_keys=True,
                    ),
                    canonical(item),
                ]
                for key, item in value.items()
            ]
            return {"mapping": sorted(pairs, key=lambda pair: pair[0])}
        if isinstance(value, tuple):
            return {"tuple": [canonical(item) for item in value]}
        if isinstance(value, list):
            return [canonical(item) for item in value]
        if type(value) in (set, frozenset):
            items = [canonical(item) for item in value]
            return {
                type(value).__name__: sorted(
                    items,
                    key=lambda item: json.dumps(
                        item, sort_keys=True, separators=(",", ":")
                    ),
                )
            }
        if value is None or type(value) in (str, int, float, bool):
            # Handler-visible Python scalar types can be meaningful even when
            # JSON Schema considers two numeric representations equivalent.
            return [type(value).__name__, value]
        raise UnidentifiableEffect(type(value).__qualname__)

    encoded = json.dumps(
        [kind, canonical(prepared)],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def check_effect_replay(entity: Any, signature: str) -> None:
    scope = _effect_replay_scope.get()
    if scope is None or not scope.active or scope.entity is not entity:
        return
    signatures = replay_safety(entity).effects.get(scope.conversation_id, ())
    if (
        signature in signatures
        or (signature == "*" and bool(signatures))
        or "*" in signatures
        or _replay_history_incomplete(entity, scope.conversation_id)
    ):
        raise EffectReplayBlocked(
            "A prior effect was not acknowledged, so this operation cannot safely "
            "proceed during provider recovery. Complete a turn without effects "
            "before retrying, or start a new conversation"
        )


def record_dispatch(
    entity: Any,
    tool_input: llm.ToolInput,
    function_tool: Mapping[str, Any] | None = None,
    *,
    prepared_arguments: Mapping[str, Any] | None = None,
    ha_parameters: Any = None,
) -> None:
    """Keep trusted dispatch provenance separately from model-facing outcomes."""
    conversation_id = dispatch_conversation_id(entity)
    if conversation_id == "":
        return  # Explicitly independent work must not evict provider provenance.
    dispatched = getattr(entity, "_dispatched_tool_inputs", None)
    if not isinstance(dispatched, dict):
        dispatched = entity._dispatched_tool_inputs = {}
    read_only = getattr(entity, "_read_only_tool_inputs", None)
    if not isinstance(read_only, dict):
        read_only = entity._read_only_tool_inputs = {}
    verified_read = (
        function_tool is not None
        and is_parallel_safe_integration_tool(function_tool)
        # Scheduling even a read creates a durable side effect.
        and "delay" not in tool_input.tool_args
    )
    retained = dispatch_inputs(tool_input)
    owners = getattr(entity, "_dispatched_tool_conversations", None)
    if not isinstance(owners, dict):
        owners = entity._dispatched_tool_conversations = {}
    canonical = getattr(entity, "_dispatched_tool_signatures", None)
    if not isinstance(canonical, dict):
        canonical = entity._dispatched_tool_signatures = {}
    spec = deepcopy(function_tool.get("spec", {})) if function_tool is not None else {}
    # Regex validation already belongs to the bounded execution validator. Only
    # repeat its context-free canonicalization here, never configured regex work.
    spec["parameters"] = _schema_without_patterns(spec.get("parameters", {}))
    spec["_execution_config"] = (
        deepcopy(function_tool.get("function", {})) if function_tool is not None else {}
    )
    if ha_parameters is not None:
        spec["_ha_parameters"] = ha_parameters
    prepared_call = (
        llm.ToolInput(
            tool_name=tool_input.tool_name,
            tool_args=dict(prepared_arguments),
            id=tool_input.id,
        )
        if prepared_arguments is not None
        else tool_input
    )
    signature = (
        _signature(prepared_call, spec, prepared=prepared_arguments is not None)
        if isinstance(getattr(tool_input, "tool_name", None), str)
        and isinstance(getattr(tool_input, "tool_args", None), Mapping)
        else None
    )
    for item in retained:
        dispatched[id(item)] = item
        owners[id(item)] = conversation_id
        if signature is not None:
            canonical[id(item)] = (signature, spec)
        if verified_read:
            read_only[id(item)] = item
        else:
            read_only.pop(id(item), None)
    while len(dispatched) > _MAX_DISPATCH_RECORDS:
        _forget_dispatch(entity, next(iter(dispatched)), retire=True)


def dispatch_conversation_id(entity: Any) -> str | None:
    """Use the active ownership lease, including the detached-job sentinel."""
    scope = _effect_replay_scope.get()
    conversation_id = (
        scope.conversation_id
        if scope is not None and scope.active and scope.entity is entity
        else None
    )
    if conversation_id is None:
        from .execution_outcomes import execution_is_detached

        if execution_is_detached():
            conversation_id = ""
    return conversation_id


def dispatch_inputs(tool_input: llm.ToolInput) -> list[llm.ToolInput]:
    retained = [tool_input]
    origin = retained_function_input(tool_input)
    if origin is not None:
        retained.append(origin)
    return retained


def _signature(
    tool_input: llm.ToolInput,
    spec: Mapping[str, Any] | None = None,
    *,
    prepared: bool = False,
) -> str:
    arguments = tool_input.tool_args
    if spec is not None:
        # Invalid/externally owned arguments retain exact replay checks.
        with suppress(HomeAssistantError, TypeError, ValueError, *SCHEMA_ERRORS):
            if not prepared:
                arguments = (
                    spec["_ha_parameters"](deepcopy(arguments))
                    if "_ha_parameters" in spec
                    else validate_function_arguments(spec, arguments)
                )
            from .delayed_tools import _delay_as_timedelta
            from .function_execution import split_legacy_execution_delay

            if "_ha_parameters" not in spec:
                arguments, delay = split_legacy_execution_delay(spec, arguments)
            else:
                delay = None
            arguments = execution_identity(spec.get("_execution_config", {}), arguments)
            if delay is not None:
                arguments = {
                    "execution": arguments,
                    "delay_seconds": _delay_as_timedelta(delay).total_seconds(),
                }
    return json.dumps(
        [tool_input.tool_name, arguments],
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _needs_tool_wide_guard(entity: Any, call: llm.ToolInput) -> bool:
    """Precise identities protect themselves even when their outcome is unknown."""
    receipts = getattr(entity, "_execution_effect_receipts", {}).get(id(call), ())
    return not receipts or any(
        # An opaque backend refusal after an earlier effect leaves the whole
        # invocation partial. It cannot authorize a changed-argument retry.
        # Pure refusals have outcome "rejected" and are never retained above.
        (item.outcome != "rejected" or item.kind == "backend")
        and (item.kind not in _PRECISE_EFFECT_KINDS or item.signature == "*")
        for item in receipts
    )


def remember_unacknowledged_calls(
    entity: Any,
    chat_log: conversation.ChatLog,
    existing_content_ids: set[int],
) -> None:
    """Remember completed calls if the provider failed before accepting results."""
    conversation_id = getattr(chat_log, "conversation_id", None)
    if not isinstance(conversation_id, str) or not conversation_id:
        return
    new_content = [
        content
        for content in chat_log.content
        if id(content) not in existing_content_ids
    ]
    completed_ids = {
        call_id
        for content in new_content
        if is_tool_result_content(content)
        and isinstance((call_id := getattr(content, "tool_call_id", None)), str)
    }
    records = [
        getattr(entity, "_dispatched_tool_signatures", {}).get(
            id(tool_input), (_signature(tool_input), {})
        )
        for content in new_content
        if isinstance(content, conversation.AssistantContent) and content.tool_calls
        for tool_input in content.tool_calls
        if (
            tool_input.id in completed_ids
            or getattr(entity, "_execution_tool_outcomes", {}).get(id(tool_input))
            in {"completed", "partial", "uncertain"}
        )
        and getattr(entity, "_execution_tool_outcomes", {}).get(id(tool_input))
        != "rejected"
        and getattr(entity, "_dispatched_tool_inputs", {}).get(id(tool_input))
        is tool_input
        and getattr(entity, "_read_only_tool_inputs", {}).get(id(tool_input))
        is not tool_input
    ]
    if not records:
        return
    state = replay_safety(entity)
    effects = state.effects
    retained_effects = effects.setdefault(conversation_id, set())
    for content in new_content:
        if (
            not isinstance(content, conversation.AssistantContent)
            or not content.tool_calls
        ):
            continue
        for call in content.tool_calls:
            if getattr(entity, "_dispatched_tool_inputs", {}).get(id(call)) is not call:
                continue
            if getattr(entity, "_read_only_tool_inputs", {}).get(id(call)) is call:
                continue
            retained_effects.update(
                signature
                for receipt in getattr(entity, "_execution_effect_receipts", {}).get(
                    id(call), ()
                )
                if receipt.outcome in {"completed", "uncertain"}
                for signature in (
                    receipt.signature,
                    getattr(receipt, "recovery_alias", ""),
                )
                if signature
            )
    if len(retained_effects) > 4096:
        # Never evict an unresolved effect and silently permit its replay.
        effects[conversation_id] = {"*"}
    schemas = state.schemas
    schemas.setdefault(conversation_id, {}).update(dict(records))
    uncertain = state.uncertain_tools
    uncertain.setdefault(conversation_id, set()).update(
        call.tool_name
        for content in new_content
        if isinstance(content, conversation.AssistantContent) and content.tool_calls
        for call in content.tool_calls
        if getattr(entity, "_execution_tool_outcomes", {}).get(id(call))
        in {"partial", "uncertain"}
        and _needs_tool_wide_guard(entity, call)
    )
    while len(schemas) > _MAX_AMBIGUOUS_CONVERSATIONS:
        oldest = next(iter(schemas))
        _retire_replay_history(entity, oldest)
        effects.pop(oldest, None)
        uncertain.pop(oldest, None)
        schemas.pop(oldest)


def was_unacknowledged_equivalent(
    entity: Any, chat_log: conversation.ChatLog, tool_input: llm.ToolInput
) -> bool:
    """Match only calls from an unresolved failed continuation in this conversation."""
    conversation_id = getattr(chat_log, "conversation_id", None)
    if not isinstance(conversation_id, str) or not conversation_id:
        return False
    if _replay_history_incomplete(entity, conversation_id):
        return True
    ledger = replay_safety(entity).schemas
    records = ledger.get(chat_log.conversation_id, {})
    if tool_input.tool_name in replay_safety(entity).uncertain_tools.get(
        chat_log.conversation_id, ()
    ):
        return True
    if _signature(tool_input) in records:
        return True
    return any(
        _signature(tool_input, spec) == signature for signature, spec in records.items()
    )


def clear_unacknowledged_calls(entity: Any, conversation_id: str | None) -> None:
    """A successful provider turn resolves ambiguity for this conversation."""
    if isinstance(conversation_id, str):
        state = replay_safety(entity)
        for ledger in (state.schemas, state.effects, state.uncertain_tools):
            ledger.pop(conversation_id, None)
        digest = hashlib.sha256(conversation_id.encode()).digest()
        state.retired_conversations.discard(digest)
        if state.history_incomplete:
            acknowledged = state.acknowledged_conversations
            acknowledged[digest] = None
            while len(acknowledged) > _MAX_AMBIGUOUS_CONVERSATIONS:
                acknowledged.pop(next(iter(acknowledged)))
        owners = getattr(entity, "_dispatched_tool_conversations", {})
        for key in [key for key, owner in owners.items() if owner == conversation_id]:
            _forget_dispatch(entity, key)
