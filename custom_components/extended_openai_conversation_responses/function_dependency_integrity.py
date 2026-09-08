"""Atomic Function Tool and Request Rule dependency integrity."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from typing import Any, cast

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import management_ui, request_rules
from .agent_config import configured_function_tools_from_data, function_tool_enabled
from .const import CONF_GUEST_ALLOWED_GROUP_IDS, DOMAIN, SERVICE_CALL_FUNCTION
from .function_execution import async_validate_function_arguments

_TOOL_MUTATIONS = frozenset(
    {"save", "set_enabled", "delete", "save_group", "delete_group", "ha_add"}
)
_TEMPLATE_MARKERS = ("{{", "{%", "{#")
_ACTIVE_CONFIG_REVISION: ContextVar[str | None] = ContextVar(
    "function_dependency_config_revision", default=None
)
_ACTIVE_GROUP_MUTATION: ContextVar[tuple[str, str | None] | None] = ContextVar(
    "function_dependency_group_mutation", default=None
)
_INSTALLED = False


def _is_template_string(value: Any) -> bool:
    """Return whether a saved Request Rule value is resolved only at runtime."""
    return isinstance(value, str) and any(
        marker in value for marker in _TEMPLATE_MARKERS
    )


def _contains_template(value: Any) -> bool:
    if _is_template_string(value):
        return True
    if isinstance(value, Mapping):
        return any(_contains_template(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_template(item) for item in value)
    return False


def _schema_types(schema: Mapping[str, Any]) -> set[str]:
    value = schema.get("type")
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    if "properties" in schema or "required" in schema:
        return {"object"}
    if "items" in schema:
        return {"array"}
    return set()


def _mask_dynamic_schema(value: Any, schema: Mapping[str, Any]) -> dict[str, Any]:
    """Keep static structure constraints while deferring template-valued leaves."""
    if _is_template_string(value):
        return {}

    result = deepcopy(dict(schema))
    if not _contains_template(value):
        return result

    # A container containing unresolved values cannot be compared meaningfully as a
    # whole against enum/const until runtime. Its static shape still can be checked.
    result.pop("enum", None)
    result.pop("const", None)
    types = _schema_types(schema)

    if isinstance(value, Mapping) and "object" in types:
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        masked_properties = (
            deepcopy(dict(properties)) if isinstance(properties, Mapping) else {}
        )
        for key, item in value.items():
            child_schema = (
                properties.get(key) if isinstance(properties, Mapping) else None
            )
            if isinstance(child_schema, Mapping):
                masked_properties[key] = _mask_dynamic_schema(item, child_schema)
            elif isinstance(additional, Mapping):
                # Promote current additional keys to individual properties so one
                # dynamic value does not weaken validation for its static siblings.
                masked_properties[str(key)] = _mask_dynamic_schema(item, additional)
        if masked_properties or "properties" in result:
            result["properties"] = masked_properties
        if isinstance(additional, Mapping):
            result["additionalProperties"] = False
        return result

    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and "array" in types
        and isinstance(schema.get("items"), Mapping)
    ):
        # The supported schema subset has a single items schema, so loosen it for
        # this structural pass. Static siblings are validated independently below.
        result["items"] = {}
        result.pop("uniqueItems", None)
    return result


async def _async_validate_static_descendants(
    hass: HomeAssistant, value: Any, schema: Mapping[str, Any]
) -> None:
    """Fully validate every concrete subtree inside a partially dynamic value."""
    if _is_template_string(value):
        return
    if not _contains_template(value):
        wrapped_spec = {
            "parameters": {
                "type": "object",
                "properties": {"value": deepcopy(dict(schema))},
                "required": ["value"],
                "additionalProperties": False,
            }
        }
        await async_validate_function_arguments(hass, wrapped_spec, {"value": value})
        return

    types = _schema_types(schema)
    if isinstance(value, Mapping) and "object" in types:
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            child_schema = (
                properties.get(key) if isinstance(properties, Mapping) else None
            )
            if not isinstance(child_schema, Mapping) and isinstance(
                additional, Mapping
            ):
                child_schema = additional
            if isinstance(child_schema, Mapping):
                await _async_validate_static_descendants(hass, item, child_schema)
        return

    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and "array" in types
    ):
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for item in value:
                await _async_validate_static_descendants(hass, item, item_schema)


async def async_validate_static_function_arguments(
    hass: HomeAssistant,
    spec: Mapping[str, Any],
    arguments: Mapping[str, Any],
) -> None:
    """Validate all Request Rule Function arguments known before runtime.

    Template-valued leaves are deliberately deferred to the normal runtime validator,
    while required keys, additional-property policy, static sibling values and nested
    constraints remain enforced when the Request Rule is saved.
    """
    parameters = spec.get("parameters", {})
    if not isinstance(parameters, Mapping):
        raise HomeAssistantError("Function input schema is invalid")
    masked_spec = dict(spec)
    masked_spec["parameters"] = _mask_dynamic_schema(arguments, parameters)
    await async_validate_function_arguments(hass, masked_spec, arguments)
    if _contains_template(arguments):
        await _async_validate_static_descendants(hass, arguments, parameters)


def _rule_script_actions(rule: Mapping[str, Any]):
    action = rule.get("action")
    actions = action.get("actions", []) if isinstance(action, Mapping) else []
    if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)):
        return iter(())
    return request_rules._iter_script_actions(
        cast(Sequence[Mapping[str, Any]], actions)
    )


def recursive_function_references(
    manager: Any, function_name: str
) -> list[dict[str, str]]:
    """Return rules that call a Function Tool anywhere in a native script tree."""
    service_action = f"{DOMAIN}.{SERVICE_CALL_FUNCTION}"
    references: list[dict[str, str]] = []
    for rule in manager._rules:
        if any(
            action.get("action", action.get("service")) == service_action
            and isinstance(action.get("data"), Mapping)
            and action["data"].get("function") == function_name
            for action in _rule_script_actions(rule)
        ):
            references.append({"id": rule["id"], "name": rule["name"]})
    return references


async def async_rename_function_reference_recursive(
    manager: Any,
    old_name: str,
    new_name: str,
    *,
    expected_revision: str | None = None,
) -> int:
    """Rewrite nested Function Tool references with in-memory rollback on save failure."""
    if old_name == new_name:
        return 0
    service_action = f"{DOMAIN}.{SERVICE_CALL_FUNCTION}"
    async with manager._lock:
        manager._require_revision_locked(expected_revision)
        changed = 0
        updated_rules: list[dict[str, Any]] = []
        for rule in manager._rules:
            updated = deepcopy(rule)
            for action in _rule_script_actions(updated):
                data = action.get("data")
                if (
                    action.get("action", action.get("service")) == service_action
                    and isinstance(data, Mapping)
                    and data.get("function") == old_name
                ):
                    if not isinstance(action, dict):
                        raise ValueError("Request Rule action is not mutable")
                    action["data"] = {**data, "function": new_name}
                    changed += 1
            updated_rules.append(request_rules.validate_rule(updated))
        if not changed:
            return 0

        original_rules = manager._rules
        manager._rules = updated_rules
        manager._sort_and_compile()
        try:
            await manager._async_save_locked()
        except BaseException:
            manager._rules = original_rules
            manager._sort_and_compile()
            raise
        return changed


async def async_validate_request_rule_functions(
    hass: HomeAssistant,
    rule: Mapping[str, Any],
    configured_tools: list[dict[str, Any]],
) -> None:
    """Validate every nested configured Function call before a rule is persisted."""
    service_action = f"{DOMAIN}.{SERVICE_CALL_FUNCTION}"
    by_name = {
        tool["spec"]["name"]: tool
        for tool in configured_tools
        if function_tool_enabled(tool)
    }
    for action in _rule_script_actions(rule):
        if action.get("action", action.get("service")) != service_action:
            continue
        data = action.get("data")
        if not isinstance(data, Mapping) or not isinstance(data.get("function"), str):
            raise HomeAssistantError("Configured Function action is invalid")
        arguments = data.get("arguments", {})
        if not isinstance(arguments, Mapping):
            raise HomeAssistantError("Configured Function arguments must be an object")
        function_name = str(data["function"])
        tool = by_name.get(function_name)
        if tool is None:
            raise HomeAssistantError(
                f"Function Tool is unavailable or disabled: {function_name}"
            )
        await async_validate_static_function_arguments(
            hass, cast(Mapping[str, Any], tool["spec"]), arguments
        )


def wrap_persist_function_configuration(original):
    """Apply the active revision and legacy group-reference update in one config write."""

    @wraps(original)
    def wrapped(
        hass: HomeAssistant,
        entry: Any,
        subentry: Any,
        tools: list[dict[str, Any]],
        groups: list[dict[str, Any]],
        *,
        extra_updates: dict[str, Any] | None = None,
        expected_revision: str | None = None,
    ) -> dict[str, Any]:
        updates = dict(extra_updates or {})
        group_mutation = _ACTIVE_GROUP_MUTATION.get()
        if group_mutation is not None:
            old_id, new_id = group_mutation
            current_ids = subentry.data.get(CONF_GUEST_ALLOWED_GROUP_IDS, [])
            if isinstance(current_ids, list):
                if new_id is None:
                    changed_ids = [item for item in current_ids if item != old_id]
                else:
                    changed_ids = [
                        new_id if item == old_id else item for item in current_ids
                    ]
                updates[CONF_GUEST_ALLOWED_GROUP_IDS] = list(dict.fromkeys(changed_ids))

        return cast(
            dict[str, Any],
            original(
                hass,
                entry,
                subentry,
                tools,
                groups,
                extra_updates=updates or None,
                expected_revision=(
                    expected_revision
                    if expected_revision is not None
                    else _ACTIVE_CONFIG_REVISION.get()
                ),
            ),
        )

    return wrapped


def wrap_management_command(original):
    """Complete revision and nested dependency checks around the effective dispatcher."""

    @wraps(original)
    async def wrapped(
        hass: HomeAssistant,
        user_id: str,
        is_admin: bool,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        section = message.get("section")
        action = message.get("action")

        if not is_admin and (
            (section == "request_rules" and action in {"create", "update"})
            or (section == "tools" and action in _TOOL_MUTATIONS)
        ):
            # Preserve the existing authorization boundary as the first observable
            # failure instead of exposing validation or revision details.
            return cast(
                dict[str, Any], await original(hass, user_id, is_admin, message)
            )

        if section == "request_rules" and action in {"create", "update"}:
            entry_id = message.get("entry_id")
            subentry_id = message.get("subentry_id")
            if isinstance(entry_id, str) and isinstance(subentry_id, str):
                _entry, subentry = management_ui.entry_and_agent(
                    hass, entry_id, subentry_id
                )
                rule_id = message.get("rule_id") if action == "update" else None
                candidate = management_ui._prepare_request_rule(
                    message.get("rule"), rule_id if isinstance(rule_id, str) else None
                )
                await async_validate_request_rule_functions(
                    hass,
                    candidate,
                    configured_function_tools_from_data(subentry.data),
                )

        if section != "tools" or action not in _TOOL_MUTATIONS:
            return cast(
                dict[str, Any], await original(hass, user_id, is_admin, message)
            )

        entry_id = message.get("entry_id")
        subentry_id = message.get("subentry_id")
        if not isinstance(entry_id, str) or not isinstance(subentry_id, str):
            return cast(
                dict[str, Any], await original(hass, user_id, is_admin, message)
            )
        _entry, subentry = management_ui.entry_and_agent(hass, entry_id, subentry_id)
        revision = message.get("revision")
        # Missing revisions remain accepted for older direct callers, but every current
        # management frontend mutation supplies one. When present it is checked both
        # now and again at the final persistence seam.
        management_ui._require_agent_config_revision(subentry, revision)

        group_mutation: tuple[str, str | None] | None = None
        if action == "save_group":
            original_id = message.get("original_id")
            group_candidate = message.get("group")
            new_id = (
                group_candidate.get("id")
                if isinstance(group_candidate, Mapping)
                else None
            )
            if (
                isinstance(original_id, str)
                and isinstance(new_id, str)
                and original_id != new_id
            ):
                group_mutation = (original_id, new_id)
        elif action == "delete_group" and isinstance(message.get("group_id"), str):
            group_mutation = (str(message["group_id"]), None)

        revision_token = _ACTIVE_CONFIG_REVISION.set(
            revision if isinstance(revision, str) else None
        )
        group_token = _ACTIVE_GROUP_MUTATION.set(group_mutation)
        try:
            return cast(
                dict[str, Any], await original(hass, user_id, is_admin, message)
            )
        finally:
            _ACTIVE_GROUP_MUTATION.reset(group_token)
            _ACTIVE_CONFIG_REVISION.reset(revision_token)

    return wrapped


def install_function_dependency_integrity() -> None:
    """Install dependency hardening around the current effective management path."""
    global _INSTALLED
    if _INSTALLED:
        return

    setattr(  # noqa: B010
        request_rules.RequestRules,
        "function_references",
        recursive_function_references,
    )
    setattr(  # noqa: B010
        request_rules.RequestRules,
        "async_rename_function_reference",
        async_rename_function_reference_recursive,
    )
    management_ui._persist_function_configuration = (  # type: ignore[misc]
        wrap_persist_function_configuration(
            management_ui._persist_function_configuration
        )
    )
    management_ui.async_management_command = wrap_management_command(  # type: ignore[misc]
        management_ui.async_management_command
    )
    _INSTALLED = True
