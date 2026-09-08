"""Live, request-owned adapters for Home Assistant LLM capabilities.

Platform identity is (API surface, contributor domain, native name). Opaque API
identity additionally pins the tool implementation class: HA exposes no contributor
registry for those APIs. Ambiguous native names are unavailable, never first-wins.
No API, tool, context or schema is persisted by this module.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field, replace
import hashlib
import json
import logging
from types import ModuleType
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm

_LOGGER = logging.getLogger(__name__)
TOOL_TYPE = "ha_llm"
_REFERENCE_FIELDS = frozenset(
    {"type", "source_type", "source_id", "api_id", "tool_name"}
)


def is_ha_tool(tool: Mapping[str, Any]) -> bool:
    """Identify externally owned references without interpreting their names."""
    function = tool.get("function")
    return isinstance(function, Mapping) and function.get("type") == TOOL_TYPE


def validate_reference(value: Any) -> dict[str, str]:
    """Validate an explicit reference, including references currently unavailable."""
    if not isinstance(value, dict) or set(value) != _REFERENCE_FIELDS:
        raise ValueError(
            "HA tool references require type, source_type, source_id, api_id and tool_name"
        )
    for key, item in value.items():
        if (
            not isinstance(item, str)
            or not item.strip()
            or len(item) > 512
            or "*" in item
        ):
            raise ValueError(f"Invalid HA tool reference {key}")
    if value["type"] != TOOL_TYPE or value["source_type"] not in {"platform", "api"}:
        raise ValueError("Unsupported HA tool reference type")
    return dict(value)


def reference_key(reference: Mapping[str, str]) -> str:
    """Encode identity independently of display labels or provider aliases."""
    return json.dumps(
        dict(reference), sort_keys=True, ensure_ascii=True, separators=(",", ":")
    )


def new_reference_tool(reference: dict[str, str], occupied: set[str]) -> dict[str, Any]:
    """Allocate a deterministic local catalogue ID, never a source wildcard."""
    reference = validate_reference(reference)
    identity = reference_key(reference)
    salt = 0
    while True:
        digest = hashlib.sha256(f"{identity}:{salt}".encode()).hexdigest()[:48]
        name = f"ha_{digest}"
        if name not in occupied:
            break
        salt += 1
    occupied.add(name)
    return {"spec": {"name": name}, "function": reference, "enabled": True}


def _implementation(tool: llm.Tool) -> str:
    cls = type(tool)
    return f"{cls.__module__}.{cls.__qualname__}"


def _schema(tool: llm.Tool, serializer: Callable[[Any], Any] | None) -> dict[str, Any]:
    # Core migrated from voluptuous-openapi to probatio in 2026. Both converters
    # must receive the source serializer (not a separately reconstructed schema).
    converter = getattr(llm, "to_openapi", None)
    if converter is None:
        from voluptuous_openapi import convert

        converter = convert
    return dict(converter(tool.parameters, custom_serializer=serializer))


@dataclass(slots=True)
class LiveTool:
    """One capability owned exclusively by the current request."""

    reference: dict[str, str]
    tool: llm.Tool
    instance: llm.APIInstance
    source_label: str
    prompt: str
    spec: dict[str, Any]

    async def async_call(self, tool_input: llm.ToolInput) -> Any:
        """Validate with HA's authoritative schema, then dispatch exactly once."""
        matches = [tool for tool in self.instance.tools if tool.name == self.tool.name]
        if len(matches) != 1 or matches[0] is not self.tool:
            raise HomeAssistantError("HA tool changed before dispatch")
        args = self.tool.parameters(deepcopy(tool_input.tool_args))
        return await self.instance.async_call_tool(
            llm.ToolInput(tool_name=self.tool.name, tool_args=args, id=tool_input.id)
        )


@dataclass(slots=True)
class ToolSnapshot:
    """A bounded discovery result, never stored on a shared entity."""

    tools: dict[str, LiveTool] = field(default_factory=dict)
    unavailable_sources: list[str] = field(default_factory=list)
    caller_provided: bool = False

    def project(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep saved references intact while replacing only live runtime specs."""
        result = []
        for configured in tools:
            if not is_ha_tool(configured):
                result.append(configured)
                continue
            live = self.tools.get(reference_key(configured["function"]))
            result.append(
                {
                    **configured,
                    "spec": {
                        **(live.spec if live else {}),
                        "name": configured["spec"]["name"],
                    },
                    "ha_available": live is not None,
                }
            )
        return result

    def prompt_for(self, tools: list[dict[str, Any]]) -> str:
        """Include intact source prompts only for effectively visible tools."""
        prompts: dict[str, None] = {}
        aliases = []
        for tool in tools:
            if is_ha_tool(tool) and (
                live := self.tools.get(reference_key(tool["function"]))
            ):
                aliases.append(
                    f"{live.source_label}: {live.tool.name} = {tool['spec']['name']}"
                )
                if live.prompt:
                    prompts[live.prompt] = None
        if aliases:
            prompts[
                "HA tool names in this request (source name = callable name):\n"
                + "\n".join(aliases)
            ] = None
        return "\n".join(prompts)


_ACTIVE: ContextVar[ToolSnapshot | None] = ContextVar(
    "extended_openai_ha_tools", default=None
)


@contextmanager
def tool_snapshot_scope(snapshot: ToolSnapshot) -> Iterator[None]:
    """Isolate discovery/requests, including exception and cancellation cleanup."""
    token = _ACTIVE.set(snapshot)
    try:
        yield
    finally:
        _ACTIVE.reset(token)


def current_snapshot() -> ToolSnapshot:
    return _ACTIVE.get() or ToolSnapshot()


def caller_api_tools(
    instance: llm.APIInstance,
) -> tuple[ToolSnapshot, list[dict[str, Any]]]:
    """Adapt an AI Task caller's already assembled API without registering it."""
    snapshot = ToolSnapshot(caller_provided=True)
    _add_tools(
        snapshot,
        instance,
        source_type="api",
        source_id=None,
        source_label=instance.api.name,
        prompt=instance.api_prompt,
    )
    occupied: set[str] = set()
    tools = [
        new_reference_tool(live.reference, occupied) for live in snapshot.tools.values()
    ]
    return snapshot, snapshot.project(tools)


def _add_tools(
    snapshot: ToolSnapshot,
    instance: llm.APIInstance,
    *,
    source_type: str,
    source_id: str | None,
    source_label: str,
    prompt: str,
    api_id: str | None = None,
) -> None:
    names = Counter(tool.name for tool in instance.tools)
    for tool in instance.tools:
        if not snapshot.caller_provided and isinstance(tool, llm.NamespacedTool):
            # Core's merged names use display labels. They are useful within an
            # AI Task exchange but must never become saved external identities.
            continue
        if names[tool.name] != 1:
            continue
        reference = {
            "type": TOOL_TYPE,
            "source_type": source_type,
            "source_id": source_id or _implementation(tool),
            "api_id": api_id if api_id is not None else instance.api.id,
            "tool_name": tool.name,
        }
        try:
            validate_reference(reference)
            spec = {
                "description": tool.description or "",
                "parameters": _schema(tool, instance.custom_serializer),
                "strict": False,
            }
        except Exception:
            _LOGGER.warning("HA LLM tool schema unavailable from %s", source_label)
            continue
        snapshot.tools[reference_key(reference)] = LiveTool(
            reference, tool, instance, source_label, prompt, spec
        )


async def async_discover(
    hass: HomeAssistant,
    context: llm.LLMContext,
    references: list[dict[str, str]] | None = None,
) -> ToolSnapshot:
    """Resolve selected sources with real context; None means management preview.

    The platform registry is intentionally isolated here. Core's public assembled
    result loses source attribution and prompt boundaries. If its registry contract
    changes, platform references remain unavailable instead of remapping to APIs.
    """
    snapshot = ToolSnapshot()
    selected = (
        None if references is None else {reference_key(ref) for ref in references}
    )
    api_ids = None if references is None else {ref["api_id"] for ref in references}
    if api_ids == set():
        return snapshot
    llm_component: ModuleType | None
    try:
        from homeassistant.components import llm as component

        llm_component = component
    except ImportError:
        llm_component = None
    apis = llm.async_get_apis(hass)
    for api in apis:
        if isinstance(api, llm.MergedAPI):
            continue
        if api_ids is not None and api.id not in api_ids:
            continue
        # Only Core's exact Assist implementation is assembled directly from
        # contributor platforms. Opaque custom APIs keep their own assembly path.
        if llm_component is not None and type(api) is getattr(
            llm_component, "AssistAPI", None
        ):
            try:
                registry = hass.data.get(llm_component.DATA_PLATFORMS)
                if registry is None:
                    raise HomeAssistantError("LLM platform registry unavailable")
                async with asyncio.timeout(15):
                    platforms = await registry.async_get_platforms()
            except Exception:
                snapshot.unavailable_sources.append(api.id)
                continue
            for domain, platform in sorted(platforms.items()):
                if references is not None and not any(
                    ref["source_type"] == "platform"
                    and ref["source_id"] == domain
                    and ref["api_id"] == api.id
                    for ref in references
                ):
                    continue
                try:
                    result = platform.async_get_tools(hass, context, api.id)
                    if result is None:
                        continue
                    instance = llm.APIInstance(
                        api=api,
                        api_prompt=result.prompt or "",
                        llm_context=context,
                        tools=list(result.tools),
                        custom_serializer=llm.selector_serializer,
                    )
                    _add_tools(
                        snapshot,
                        instance,
                        source_type="platform",
                        source_id=domain,
                        source_label=f"{domain} · {api.name}",
                        prompt=result.prompt or "",
                    )
                except Exception:
                    snapshot.unavailable_sources.append(f"{api.id}/{domain}")
            continue
        try:
            async with asyncio.timeout(15):
                instance = await api.async_get_api_instance(context)
            # Some third-party APIs reuse an instance. Never mutate it or accept a
            # cached administrator context as the authority for this request.
            instance = replace(
                instance, llm_context=context, tools=list(instance.tools)
            )
            _add_tools(
                snapshot,
                instance,
                source_type="api",
                source_id=None,
                source_label=f"{api.name} ({api.id})",
                prompt=instance.api_prompt,
                api_id=api.id,
            )
        except Exception:
            snapshot.unavailable_sources.append(api.id)
    if selected is not None:
        snapshot.tools = {
            key: live for key, live in snapshot.tools.items() if key in selected
        }
    return snapshot
