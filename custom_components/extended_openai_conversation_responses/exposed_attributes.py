"""Stable per-agent selection of live exposed-entity attributes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from functools import wraps
import json
from types import MappingProxyType
from typing import Any

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.template.helpers import resolve_area_id

from . import agent_config
from .const import DEFAULT_EXPOSED_ENTITIES_CONTEXT_TEMPLATE
from .entity_context_cache import get_entity_prompt_metadata
from .helpers import get_exposed_entities

CONF_EXPOSED_ENTITY_ATTRIBUTES = "exposed_entity_attributes"
_REGISTRY_REF_PREFIX = "registry:"
MAX_CONFIGURED_ENTITIES = 1000
MAX_ATTRIBUTES_PER_ENTITY = 64
MAX_ATTRIBUTE_NAME_LENGTH = 255
MAX_ATTRIBUTE_VALUE_CHARACTERS = 4096
_FRONTEND_MODULE = "exposed-attributes-ui.js"
_INSTALLED = False

_ORIGINAL_NORMALIZE_AGENT_CONFIG = agent_config.normalize_agent_config


def _validate_preferences(value: Any) -> dict[str, list[str]]:
    """Return canonical stable-registry references and selected attribute names."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise agent_config.AgentConfigError(
            CONF_EXPOSED_ENTITY_ATTRIBUTES, "must be an object"
        )
    if len(value) > MAX_CONFIGURED_ENTITIES:
        raise agent_config.AgentConfigError(
            CONF_EXPOSED_ENTITY_ATTRIBUTES,
            f"supports at most {MAX_CONFIGURED_ENTITIES} entities",
        )

    result: dict[str, list[str]] = {}
    for reference, attributes in value.items():
        if (
            not isinstance(reference, str)
            or not reference.startswith(_REGISTRY_REF_PREFIX)
            or not reference.removeprefix(_REGISTRY_REF_PREFIX).strip()
            or len(reference) > len(_REGISTRY_REF_PREFIX) + 255
        ):
            raise agent_config.AgentConfigError(
                CONF_EXPOSED_ENTITY_ATTRIBUTES,
                "entity references must use a stable Home Assistant registry ID",
            )
        if not isinstance(attributes, list):
            raise agent_config.AgentConfigError(
                f"{CONF_EXPOSED_ENTITY_ATTRIBUTES}.{reference}",
                "must be a list of attribute names",
            )
        if len(attributes) > MAX_ATTRIBUTES_PER_ENTITY:
            raise agent_config.AgentConfigError(
                f"{CONF_EXPOSED_ENTITY_ATTRIBUTES}.{reference}",
                f"supports at most {MAX_ATTRIBUTES_PER_ENTITY} attributes",
            )
        canonical: list[str] = []
        for attribute in attributes:
            if (
                not isinstance(attribute, str)
                or not attribute
                or attribute != attribute.strip()
                or len(attribute) > MAX_ATTRIBUTE_NAME_LENGTH
            ):
                raise agent_config.AgentConfigError(
                    f"{CONF_EXPOSED_ENTITY_ATTRIBUTES}.{reference}",
                    "attribute names must be non-empty strings without surrounding whitespace",
                )
            if attribute not in canonical:
                canonical.append(attribute)
        if canonical:
            result[reference] = sorted(canonical)
    return dict(sorted(result.items()))


def _normalize_agent_config_with_exposed_attributes(
    data: dict[str, Any], *, apply_defaults: bool = True, reject_unknown: bool = True
) -> dict[str, Any]:
    """Extend the canonical agent contract without creating a second config store."""
    if not isinstance(data, dict):
        return _ORIGINAL_NORMALIZE_AGENT_CONFIG(
            data, apply_defaults=apply_defaults, reject_unknown=reject_unknown
        )
    source = dict(data)
    preference_value = source.pop(CONF_EXPOSED_ENTITY_ATTRIBUTES, None)
    normalized = _ORIGINAL_NORMALIZE_AGENT_CONFIG(
        source,
        apply_defaults=apply_defaults,
        reject_unknown=reject_unknown,
    )
    if apply_defaults or CONF_EXPOSED_ENTITY_ATTRIBUTES in data:
        normalized[CONF_EXPOSED_ENTITY_ATTRIBUTES] = _validate_preferences(
            preference_value if CONF_EXPOSED_ENTITY_ATTRIBUTES in data else {}
        )
    return normalized


def _register_agent_config_contract() -> None:
    """Register the field before modules import snapshots of the config contract."""
    if CONF_EXPOSED_ENTITY_ATTRIBUTES in agent_config.AGENT_CONFIG_FIELDS:
        return
    defaults = dict(agent_config.AGENT_CONFIG_DEFAULTS)
    defaults[CONF_EXPOSED_ENTITY_ATTRIBUTES] = {}
    agent_config.AGENT_CONFIG_DEFAULTS = MappingProxyType(defaults)
    agent_config.AGENT_CONFIG_FIELDS = frozenset(
        {*agent_config.AGENT_CONFIG_FIELDS, CONF_EXPOSED_ENTITY_ATTRIBUTES}
    )
    agent_config.normalize_agent_config = _normalize_agent_config_with_exposed_attributes


_register_agent_config_contract()


def _registry_entry_by_id(registry: Any, entry_id: str) -> Any | None:
    """Resolve a stable registry ID across supported Home Assistant versions."""
    entities = getattr(registry, "entities", None)
    get_entry = getattr(entities, "get_entry", None)
    if callable(get_entry):
        return get_entry(entry_id)
    values = getattr(entities, "values", None)
    if callable(values):
        return next((entry for entry in values() if entry.id == entry_id), None)
    return None


def _reference_for_entity(registry: Any, entity_id: str) -> str | None:
    entry = registry.async_get(entity_id)
    return f"{_REGISTRY_REF_PREFIX}{entry.id}" if entry is not None else None


def _entry_for_reference(registry: Any, reference: str) -> Any | None:
    if not reference.startswith(_REGISTRY_REF_PREFIX):
        return None
    return _registry_entry_by_id(
        registry, reference.removeprefix(_REGISTRY_REF_PREFIX)
    )


def _preferences_from_options(options: Mapping[str, Any] | Any) -> dict[str, list[str]]:
    """Fail closed for malformed legacy/direct runtime data."""
    try:
        value = options.get(CONF_EXPOSED_ENTITY_ATTRIBUTES, {})
        return _validate_preferences(value)
    except (AttributeError, agent_config.AgentConfigError):
        return {}


def exposed_attribute_catalog(
    hass: Any, options: Mapping[str, Any] | Any
) -> dict[str, Any]:
    """Return current exposed attribute names plus inert saved preferences."""
    preferences = _preferences_from_options(options)
    registry = er.async_get(hass)
    entities: list[dict[str, Any]] = []
    current_references: set[str] = set()

    for exposed in get_exposed_entities(hass):
        entity_id = str(exposed.get("entity_id", ""))
        state = hass.states.get(entity_id)
        if state is None:
            continue
        reference = _reference_for_entity(registry, entity_id)
        if reference is not None:
            current_references.add(reference)
        selected = preferences.get(reference, []) if reference else []
        available = sorted(str(name) for name in state.attributes)
        available_set = set(available)
        entities.append(
            {
                "entity_id": entity_id,
                "name": str(exposed.get("name") or entity_id),
                "reference": reference,
                "attributes": available,
                "selected_attributes": list(selected),
                "missing_selected_attributes": [
                    name for name in selected if name not in available_set
                ],
                "durable_selection_available": reference is not None,
            }
        )

    saved_unexposed: list[dict[str, Any]] = []
    for reference, selected in preferences.items():
        if reference in current_references:
            continue
        entry = _entry_for_reference(registry, reference)
        entity_id = getattr(entry, "entity_id", None)
        saved_unexposed.append(
            {
                "reference": reference,
                "entity_id": entity_id,
                "name": entity_id or "Unavailable entity",
                "selected_attributes": list(selected),
                "registry_entry_exists": entry is not None,
            }
        )

    entities.sort(key=lambda item: (item["name"].casefold(), item["entity_id"]))
    saved_unexposed.sort(key=lambda item: str(item["name"]).casefold())
    return {
        "entities": entities,
        "saved_unexposed": saved_unexposed,
        "identity_policy": "entity_registry",
    }


def _safe_attribute_value(value: Any) -> Any:
    """Convert one live value to bounded JSON-safe data for prompt templates."""
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError, RecursionError):
        encoded = json.dumps(str(value), ensure_ascii=False)
    if len(encoded) > MAX_ATTRIBUTE_VALUE_CHARACTERS:
        return f"<omitted: {len(encoded)} serialized characters>"
    try:
        return json.loads(encoded)
    except json.JSONDecodeError:
        return str(value)[:MAX_ATTRIBUTE_VALUE_CHARACTERS]


def enrich_exposed_entities(
    hass: Any,
    options: Mapping[str, Any] | Any,
    exposed_entities: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach selected values only to entities already exposed for this request."""
    preferences = _preferences_from_options(options)
    if not preferences or not exposed_entities:
        return exposed_entities

    registry = er.async_get(hass)
    exposed_by_id = {
        str(entity.get("entity_id", "")): entity for entity in exposed_entities
    }
    selected_by_entity_id: dict[str, list[str]] = {}
    for reference, attributes in preferences.items():
        entry = _entry_for_reference(registry, reference)
        entity_id = getattr(entry, "entity_id", None)
        if isinstance(entity_id, str) and entity_id in exposed_by_id:
            selected_by_entity_id[entity_id] = attributes

    if not selected_by_entity_id:
        return exposed_entities

    result: list[dict[str, Any]] = []
    for entity in exposed_entities:
        entity_id = str(entity.get("entity_id", ""))
        selected = selected_by_entity_id.get(entity_id)
        if not selected:
            result.append(entity)
            continue
        state = hass.states.get(entity_id)
        if state is None:
            result.append(entity)
            continue
        live = {
            name: _safe_attribute_value(state.attributes[name])
            for name in selected
            if name in state.attributes
        }
        result.append({**entity, **({"attributes": live} if live else {})})
    return result


def _attributes_json(entity: dict[str, Any]) -> str:
    value = json.dumps(
        entity.get("attributes") or {},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return '"' + value.replace('"', '""') + '"'


def _render_grouped_default_with_attributes(
    hass: Any, exposed_entities: list[dict[str, Any]]
) -> str:
    """Preserve the maintained grouped default while adding one compact column."""
    from . import prompt

    grouped: dict[str | None, list[dict[str, Any]]] = {}
    for entity in prompt._default_prompt_entities(exposed_entities):
        entity_id = entity.get("entity_id")
        area_id = (
            get_entity_prompt_metadata(hass, entity_id).area_id
            if isinstance(entity_id, str)
            else None
        )
        grouped.setdefault(area_id, []).append(entity)

    lines = ["## Available Devices", "entity_id,name,state,aliases,attributes"]
    for area_id, entities in grouped.items():
        lines.append(f"area_id={area_id or ''}")
        for entity in entities:
            aliases = entity.get("aliases") or []
            lines.append(
                f"{entity.get('entity_id', '')},{entity.get('prompt_name', '')},"
                f"{entity.get('state', '')},{'/'.join(str(alias) for alias in aliases)},"
                f"{_attributes_json(entity)}"
            )
    return "\n".join(lines) + "\n"


def _render_legacy_default_with_attributes(
    hass: Any, exposed_entities: list[dict[str, Any]]
) -> str:
    """Preserve the legacy maintained-template shape when it is stored explicitly."""
    lines = [
        "## Available Devices",
        "```csv",
        "entity_id,name,state,area_id,aliases,attributes",
    ]
    for entity in exposed_entities:
        entity_id = str(entity.get("entity_id", ""))
        aliases = entity.get("aliases") or []
        lines.append(
            f"{entity_id},{entity.get('name', '')},{entity.get('state', '')},"
            f"{resolve_area_id(hass, entity_id)},"
            f"{'/'.join(str(item) for item in aliases)},{_attributes_json(entity)}"
        )
    lines.append("```")
    return "\n".join(lines) + "\n"


def _has_selected_values(exposed_entities: list[dict[str, Any]]) -> bool:
    return any(bool(entity.get("attributes")) for entity in exposed_entities)


def _wrap_effective_prompt_renderer(original: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(original)
    def wrapped(hass: Any, options: Any, *args: Any, **kwargs: Any) -> Any:
        exposed = kwargs.get("exposed_entities")
        if isinstance(exposed, list):
            kwargs = dict(kwargs)
            kwargs["exposed_entities"] = enrich_exposed_entities(
                hass, options, exposed
            )
        return original(hass, options, *args, **kwargs)

    return wrapped


def _decorate_configuration_result(
    hass: Any, result: dict[str, Any]
) -> dict[str, Any]:
    config = result.get("config")
    if not isinstance(config, dict):
        return result
    decorated = dict(result)
    decorated["exposed_attribute_catalog"] = exposed_attribute_catalog(hass, config)
    return decorated


def install_exposed_attribute_runtime() -> None:
    """Install prompt and management seams after existing optimization layers."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from . import conversation, management_ui, prompt

    original_default_renderer = prompt._default_exposed_entities_context
    original_template_renderer = prompt._render_template

    @wraps(original_default_renderer)
    def default_renderer(hass: Any, exposed_entities: list[dict[str, Any]]) -> str:
        if _has_selected_values(exposed_entities):
            return _render_grouped_default_with_attributes(hass, exposed_entities)
        return original_default_renderer(hass, exposed_entities)

    @wraps(original_template_renderer)
    def template_renderer(
        hass: Any,
        raw: str,
        *,
        exposed_entities: list[dict[str, Any]],
        current_device_id: str | None,
        user_input: Any,
        skills: list[Any],
    ) -> str:
        if (
            raw == DEFAULT_EXPOSED_ENTITIES_CONTEXT_TEMPLATE
            and _has_selected_values(exposed_entities)
        ):
            return _render_legacy_default_with_attributes(hass, exposed_entities)
        return original_template_renderer(
            hass,
            raw,
            exposed_entities=exposed_entities,
            current_device_id=current_device_id,
            user_input=user_input,
            skills=skills,
        )

    prompt._default_exposed_entities_context = default_renderer
    prompt._render_template = template_renderer
    prompt.render_effective_prompt = _wrap_effective_prompt_renderer(
        prompt.render_effective_prompt
    )
    conversation.render_effective_prompt = _wrap_effective_prompt_renderer(
        conversation.render_effective_prompt
    )
    management_ui.render_effective_prompt = _wrap_effective_prompt_renderer(
        management_ui.render_effective_prompt
    )

    original_management_command = management_ui.async_management_command

    @wraps(original_management_command)
    async def management_command(
        hass: Any, user_id: str, is_admin: bool, message: dict[str, Any]
    ) -> dict[str, Any]:
        result = await original_management_command(hass, user_id, is_admin, message)
        if (
            message.get("section") == "configuration"
            and message.get("action") in {"get", "update", "save"}
            and isinstance(result, dict)
        ):
            return _decorate_configuration_result(hass, result)
        return result

    management_ui.async_management_command = management_command
    management_ui.MANAGEMENT_FRONTEND_MODULES = tuple(
        dict.fromkeys((*management_ui.MANAGEMENT_FRONTEND_MODULES, _FRONTEND_MODULE))
    )
