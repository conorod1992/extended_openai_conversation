"""Apply PR15's narrow edits to large existing source files."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one match in {path}, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


AGENT_CONFIG = "custom_components/extended_openai_conversation_responses/agent_config.py"
REQUEST_RULES = "custom_components/extended_openai_conversation_responses/request_rules.py"
MANAGEMENT = "custom_components/extended_openai_conversation_responses/management_ui.py"
FRONTEND = "custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-base.js"

replace_once(
    AGENT_CONFIG,
    "from .functions import FUNCTIONS, get_function\n",
    "from .function_execution import validate_function_schema\n"
    "from .function_tool_policy import (\n"
    "    FUNCTION_TOOL_NAME_PATTERN,\n"
    "    NATIVE_FUNCTION_IMPLEMENTATIONS,\n"
    "    RESERVED_FUNCTION_TOOL_NAMES,\n"
    ")\n"
    "from .functions import FUNCTIONS, get_function\n",
)

replace_once(
    AGENT_CONFIG,
    '''        spec = tool.get("spec")
        function_config = tool.get("function")
        if not isinstance(spec, dict):
            raise AgentConfigError(f"{field}.spec", "must be an object")
        name = spec.get("name")
        if not isinstance(name, str) or not name.strip():
            raise AgentConfigError(f"{field}.spec.name", "is required")
        if name in names:
            raise AgentConfigError(f"{field}.spec.name", f"duplicate tool name: {name}")
        names.add(name)
        if not isinstance(function_config, dict):
            raise AgentConfigError(f"{field}.function", "must be an object")
        function_type = function_config.get("type")
        if not isinstance(function_type, str) or function_type not in FUNCTIONS:
            raise AgentConfigError(
                f"{field}.function.type", f"unrecognized function type: {function_type}"
            )
        try:
            get_function(function_type).validate_schema(deepcopy(function_config))
        except Exception as err:
            raise AgentConfigError(
                f"{field}.function",
                f"configuration is invalid for {function_type}: {err}",
            ) from err
        normalized = deepcopy(tool)
        normalized["spec"] = deepcopy(spec)
        normalized["function"] = deepcopy(function_config)
''',
    '''        spec = tool.get("spec")
        function_config = tool.get("function")
        if not isinstance(spec, dict):
            raise AgentConfigError(f"{field}.spec", "must be an object")
        unknown_spec_fields = set(spec) - {"name", "description", "parameters", "strict"}
        if unknown_spec_fields:
            raise AgentConfigError(
                f"{field}.spec",
                "unknown fields: " + ", ".join(sorted(unknown_spec_fields)),
            )
        name = spec.get("name")
        if not isinstance(name, str) or not name:
            raise AgentConfigError(f"{field}.spec.name", "is required")
        if not FUNCTION_TOOL_NAME_PATTERN.fullmatch(name):
            raise AgentConfigError(
                f"{field}.spec.name",
                "must contain only letters, numbers, underscores, or hyphens "
                "and be at most 64 characters",
            )
        if name in RESERVED_FUNCTION_TOOL_NAMES:
            raise AgentConfigError(
                f"{field}.spec.name", f"reserved integration tool name: {name}"
            )
        if name in names:
            raise AgentConfigError(f"{field}.spec.name", f"duplicate tool name: {name}")
        names.add(name)
        description = spec.get("description")
        if description is not None and not isinstance(description, str):
            raise AgentConfigError(f"{field}.spec.description", "must be a string")
        strict = spec.get("strict")
        if strict is not None and not isinstance(strict, bool):
            raise AgentConfigError(f"{field}.spec.strict", "must be a boolean")
        parameters = spec.get("parameters", {})
        if not isinstance(parameters, dict):
            raise AgentConfigError(f"{field}.spec.parameters", "must be an object")
        try:
            validate_function_schema(parameters)
        except HomeAssistantError as err:
            message = str(err).removeprefix("Function input schema is invalid: ")
            raise AgentConfigError(f"{field}.spec.parameters", message) from err
        if not isinstance(function_config, dict):
            raise AgentConfigError(f"{field}.function", "must be an object")
        function_type = function_config.get("type")
        if not isinstance(function_type, str) or function_type not in FUNCTIONS:
            raise AgentConfigError(
                f"{field}.function.type", f"unrecognized function type: {function_type}"
            )
        try:
            get_function(function_type).validate_schema(deepcopy(function_config))
        except Exception as err:
            raise AgentConfigError(
                f"{field}.function",
                f"configuration is invalid for {function_type}: {err}",
            ) from err
        if (
            function_type == "native"
            and function_config.get("name") not in NATIVE_FUNCTION_IMPLEMENTATIONS
        ):
            raise AgentConfigError(
                f"{field}.function.name",
                f"unknown native implementation: {function_config.get('name')}",
            )
        normalized = deepcopy(tool)
        normalized["spec"] = deepcopy(spec)
        normalized["function"] = deepcopy(function_config)
''',
)

replace_once(
    REQUEST_RULES,
    "import asyncio\nfrom collections.abc import Awaitable, Callable, Mapping, Sequence\n",
    "import asyncio\nfrom collections.abc import Awaitable, Callable, Mapping, Sequence\nfrom copy import deepcopy\n",
)

replace_once(
    REQUEST_RULES,
    '''    def snapshot(self) -> dict[str, Any]:
        """Return a copy suitable for the management API."""
        return {
            "storage_version": STORAGE_VERSION,
            "defaults": dict(self._defaults),
            "wording_groups": _copy_wording_groups(self._wording_groups),
            "rules": [dict(rule) for rule in self._rules],
        }

''',
    '''    def snapshot(self) -> dict[str, Any]:
        """Return a copy suitable for the management API."""
        return {
            "storage_version": STORAGE_VERSION,
            "defaults": dict(self._defaults),
            "wording_groups": _copy_wording_groups(self._wording_groups),
            "rules": [dict(rule) for rule in self._rules],
        }

    def function_references(self, function_name: str) -> list[dict[str, str]]:
        """Return Request Rules that directly call one configured Function Tool."""
        service_action = f"{DOMAIN}.{SERVICE_CALL_FUNCTION}"
        references: list[dict[str, str]] = []
        for rule in self._rules:
            actions = rule.get("action", {}).get("actions", [])
            if any(
                isinstance(action, Mapping)
                and action.get("action") == service_action
                and isinstance(action.get("data"), Mapping)
                and action["data"].get("function") == function_name
                for action in actions
            ):
                references.append({"id": rule["id"], "name": rule["name"]})
        return references

    async def async_rename_function_reference(
        self, old_name: str, new_name: str
    ) -> int:
        """Rewrite exact configured-function references and persist once."""
        if old_name == new_name:
            return 0
        service_action = f"{DOMAIN}.{SERVICE_CALL_FUNCTION}"
        async with self._lock:
            changed = 0
            updated_rules: list[dict[str, Any]] = []
            for rule in self._rules:
                updated = deepcopy(rule)
                for action in updated.get("action", {}).get("actions", []):
                    if (
                        isinstance(action, Mapping)
                        and action.get("action") == service_action
                        and isinstance(action.get("data"), Mapping)
                        and action["data"].get("function") == old_name
                    ):
                        action["data"] = {**action["data"], "function": new_name}
                        changed += 1
                updated_rules.append(validate_rule(updated))
            if changed:
                self._rules = updated_rules
                self._sort_and_compile()
                await self._async_save_locked()
        return changed

''',
)

replace_once(
    MANAGEMENT,
    "    CONF_FUNCTION_TOOLS,\n    CONF_GUEST_MODE_ENABLED,\n",
    "    CONF_FUNCTION_TOOLS,\n    CONF_GUEST_ALLOWED_FUNCTION_NAMES,\n    CONF_GUEST_MODE_ENABLED,\n",
)

replace_once(
    MANAGEMENT,
    '''def _persist_function_configuration(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    tools: list[dict[str, Any]],
    groups: list[dict[str, Any]],
) -> dict[str, Any]:
    """Persist only Function Tool fields against the latest saved subentry."""
    normalized = preserve_legacy_guest_policy(
        subentry.data,
        merge_agent_config(
            subentry.data,
            {CONF_FUNCTION_TOOLS: tools, CONF_FUNCTION_GROUPS: groups},
        ),
    )
    hass.config_entries.async_update_subentry(entry, subentry, data=normalized)
    snapshot = agent_config_snapshot(normalized)
    return {
        "functions": snapshot[CONF_FUNCTION_TOOLS],
        "function_groups": snapshot[CONF_FUNCTION_GROUPS],
    }

''',
    '''def _persist_function_configuration(
    hass: HomeAssistant,
    entry: Any,
    subentry: Any,
    tools: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    *,
    extra_updates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist only Function Tool fields against the latest saved subentry."""
    updates: dict[str, Any] = {
        CONF_FUNCTION_TOOLS: tools,
        CONF_FUNCTION_GROUPS: groups,
    }
    if extra_updates:
        updates.update(extra_updates)
    normalized = preserve_legacy_guest_policy(
        subentry.data,
        merge_agent_config(subentry.data, updates),
    )
    hass.config_entries.async_update_subentry(entry, subentry, data=normalized)
    snapshot = agent_config_snapshot(normalized)
    return {
        "functions": snapshot[CONF_FUNCTION_TOOLS],
        "function_groups": snapshot[CONF_FUNCTION_GROUPS],
    }


async def _function_reference_state(
    hass: HomeAssistant,
    entry_id: str,
    subentry_id: str,
    subentry_data: MappingProxyType | dict[str, Any],
    function_name: str,
):
    """Return exact durable references to one configured Function Tool."""
    rules = await async_get_request_rules(hass, entry_id, subentry_id)
    guest_names = subentry_data.get(CONF_GUEST_ALLOWED_FUNCTION_NAMES, [])
    return rules, {
        "request_rules": rules.function_references(function_name),
        "guest_mode": isinstance(guest_names, list) and function_name in guest_names,
    }


def _function_reference_error(name: str, references: dict[str, Any]) -> str:
    """Describe semantic references that must be resolved before deletion."""
    parts: list[str] = []
    rule_names = [
        item.get("name", item.get("id", "unnamed rule"))
        for item in references.get("request_rules", [])
    ]
    if rule_names:
        parts.append("Request Rules: " + ", ".join(rule_names))
    if references.get("guest_mode"):
        parts.append("Guest Mode custom function access")
    return (
        f"Function Tool `{name}` is still referenced by " + "; ".join(parts)
        + ". Update those references before deleting it."
    )

''',
)

replace_once(
    MANAGEMENT,
    '''            if existing_index is None:
                tools.append(saved_tool)
            else:
                tools[existing_index] = saved_tool
                if original_name != saved_name:
                    groups = [
                        {
                            **group,
                            "functions": [
                                saved_name if name == original_name else name
                                for name in group["functions"]
                            ],
                        }
                        for group in groups
                    ]
            return _persist_function_configuration(hass, entry, subentry, tools, groups)
        if action == "set_enabled":
            name = message.get("name")
            enabled = message.get("enabled")
            if not isinstance(name, str) or not isinstance(enabled, bool):
                raise HomeAssistantError("name and enabled are required")
            tool = next((item for item in tools if item["spec"]["name"] == name), None)
            if tool is None:
                raise HomeAssistantError("The Function Tool no longer exists")
            tool["enabled"] = enabled
            return _persist_function_configuration(hass, entry, subentry, tools, groups)
        if action == "delete":
            if message.get("confirm") is not True:
                raise HomeAssistantError("Explicit confirmation is required")
            name = message.get("name")
            if not isinstance(name, str):
                raise HomeAssistantError("name is required")
            remaining = [tool for tool in tools if tool["spec"]["name"] != name]
            if len(remaining) == len(tools):
                raise HomeAssistantError("The Function Tool no longer exists")
            groups = [
                {
                    **group,
                    "functions": [item for item in group["functions"] if item != name],
                }
                for group in groups
            ]
            return _persist_function_configuration(
                hass, entry, subentry, remaining, groups
            )
''',
    '''            if existing_index is None:
                tools.append(saved_tool)
                return _persist_function_configuration(
                    hass, entry, subentry, tools, groups
                )

            tools[existing_index] = saved_tool
            if original_name == saved_name:
                return _persist_function_configuration(
                    hass, entry, subentry, tools, groups
                )

            original_tools = configured_function_tools_from_data(subentry.data)
            original_groups = [dict(group) for group in groups]
            groups = [
                {
                    **group,
                    "functions": [
                        saved_name if name == original_name else name
                        for name in group["functions"]
                    ],
                }
                for group in groups
            ]
            rules, references = await _function_reference_state(
                hass, entry_id, subentry_id, subentry.data, original_name
            )
            guest_names = list(
                subentry.data.get(CONF_GUEST_ALLOWED_FUNCTION_NAMES, [])
            )
            renamed_guest_names = [
                saved_name if name == original_name else name for name in guest_names
            ]
            result = _persist_function_configuration(
                hass,
                entry,
                subentry,
                tools,
                groups,
                extra_updates={
                    CONF_GUEST_ALLOWED_FUNCTION_NAMES: renamed_guest_names
                },
            )
            try:
                renamed_rule_references = await rules.async_rename_function_reference(
                    original_name, saved_name
                )
            except Exception:
                _persist_function_configuration(
                    hass,
                    entry,
                    subentry,
                    original_tools,
                    original_groups,
                    extra_updates={CONF_GUEST_ALLOWED_FUNCTION_NAMES: guest_names},
                )
                raise
            result["renamed_references"] = {
                "request_rules": renamed_rule_references,
                "guest_mode": references["guest_mode"],
            }
            return result
        if action == "set_enabled":
            name = message.get("name")
            enabled = message.get("enabled")
            if not isinstance(name, str) or not isinstance(enabled, bool):
                raise HomeAssistantError("name and enabled are required")
            tool = next((item for item in tools if item["spec"]["name"] == name), None)
            if tool is None:
                raise HomeAssistantError("The Function Tool no longer exists")
            tool["enabled"] = enabled
            result = _persist_function_configuration(
                hass, entry, subentry, tools, groups
            )
            if not enabled:
                _rules, references = await _function_reference_state(
                    hass, entry_id, subentry_id, subentry.data, name
                )
                result["references"] = references
            return result
        if action == "delete":
            if message.get("confirm") is not True:
                raise HomeAssistantError("Explicit confirmation is required")
            name = message.get("name")
            if not isinstance(name, str):
                raise HomeAssistantError("name is required")
            remaining = [tool for tool in tools if tool["spec"]["name"] != name]
            if len(remaining) == len(tools):
                raise HomeAssistantError("The Function Tool no longer exists")
            _rules, references = await _function_reference_state(
                hass, entry_id, subentry_id, subentry.data, name
            )
            if references["request_rules"] or references["guest_mode"]:
                raise HomeAssistantError(_function_reference_error(name, references))
            groups = [
                {
                    **group,
                    "functions": [item for item in group["functions"] if item != name],
                }
                for group in groups
            ]
            return _persist_function_configuration(
                hass, entry, subentry, remaining, groups
            )
''',
)

replace_once(
    FRONTEND,
    '''  root.querySelectorAll(".tool-enabled").forEach((input)=>input.addEventListener("change",async()=>{const tool=panel._draft.functions[Number(input.dataset.index)];input.disabled=true;try{const result=await panel._call("tools","set_enabled",{name:tool.spec.name,enabled:input.checked});synchronizePersistedFunctions(panel,result);panel._toast(input.checked?"Function enabled":"Function disabled");panel._render();}catch(err){input.checked=!input.checked;input.disabled=false;panel._toast(`Unable to update function: ${err.message||String(err)}`,true);}}));
''',
    '''  root.querySelectorAll(".tool-enabled").forEach((input)=>input.addEventListener("change",async()=>{const tool=panel._draft.functions[Number(input.dataset.index)];input.disabled=true;try{const result=await panel._call("tools","set_enabled",{name:tool.spec.name,enabled:input.checked});synchronizePersistedFunctions(panel,result);const references=result.references||{};const affected=(references.request_rules||[]).length+(references.guest_mode?1:0);panel._toast(input.checked?"Function enabled":affected?`Function disabled; ${affected} saved reference${affected===1?"":"s"} remain configured but unavailable until it is re-enabled`:"Function disabled");panel._render();}catch(err){input.checked=!input.checked;input.disabled=false;panel._toast(`Unable to update function: ${err.message||String(err)}`,true);}}));
''',
)

replace_once(
    FRONTEND,
    '''  root.querySelectorAll(".delete-tool").forEach(button=>button.addEventListener("click",async()=>{const tool=panel._draft.functions[Number(button.dataset.index)];if(!await panel._confirm("Delete function tool?",`The Function Tool “${tool.spec?.name||"Unnamed"}” will be deleted immediately and removed from any group assignment.`,"Delete function"))return;try{const result=await panel._call("tools","delete",{name:tool.spec.name,confirm:true});synchronizePersistedFunctions(panel,result);panel._toast("Function deleted");panel._render();}catch(err){panel._toast(`Unable to delete function: ${err.message||String(err)}`,true);}}));
''',
    '''  root.querySelectorAll(".delete-tool").forEach(button=>button.addEventListener("click",async()=>{const tool=panel._draft.functions[Number(button.dataset.index)];if(!await panel._confirm("Delete function tool?",`The Function Tool “${tool.spec?.name||"Unnamed"}” will be deleted and removed from any Function Group. Deletion is refused while Request Rules or Guest Mode still reference it.`,"Delete function"))return;try{const result=await panel._call("tools","delete",{name:tool.spec.name,confirm:true});synchronizePersistedFunctions(panel,result);panel._toast("Function deleted");panel._render();}catch(err){panel._toast(`Unable to delete function: ${err.message||String(err)}`,true);}}));
''',
)
