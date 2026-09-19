"""Focused residual coverage for Home Assistant LLM tool adapters."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

from custom_components.extended_openai_conversation_responses import (
    ha_llm_tools as tools,
)


def test_serializer_compat_install_is_idempotent(monkeypatch) -> None:
    """Do not wrap an already-installed compatibility serializer again."""

    def existing(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {}

    existing._extended_openai_serializer_compat = True  # type: ignore[attr-defined]
    monkeypatch.setattr(tools.llm, "to_openapi", existing, raising=False)

    tools._install_openapi_serializer_compat()

    assert tools.llm.to_openapi is existing


def test_serializer_compat_skips_when_optional_converter_is_unavailable(
    monkeypatch,
) -> None:
    """Keep HA untouched if the compatibility dependencies cannot be imported."""
    monkeypatch.setattr(tools.llm, "to_openapi", None, raising=False)
    monkeypatch.setitem(sys.modules, "probatio", None)

    tools._install_openapi_serializer_compat()

    assert tools.llm.to_openapi is None


def test_serializer_compat_selects_converter_and_translates_unsupported(
    monkeypatch,
) -> None:
    """Bridge unsupported sentinels in both Probatio and voluptuous directions."""
    probatio = ModuleType("probatio")
    voluptuous_openapi = ModuleType("voluptuous_openapi")

    probatio_unsupported = object()
    voluptuous_unsupported = object()

    class ProbatioSchema:
        pass

    probatio.Schema = ProbatioSchema  # type: ignore[attr-defined]
    probatio.UNSUPPORTED = probatio_unsupported  # type: ignore[attr-defined]
    voluptuous_openapi.UNSUPPORTED = voluptuous_unsupported  # type: ignore[attr-defined]

    calls: list[str] = []

    def probatio_convert(
        _schema: Any, *, custom_serializer: Any = None, **_kwargs: Any
    ) -> Any:
        calls.append("probatio")
        return custom_serializer("value")

    def voluptuous_convert(
        _schema: Any, *, custom_serializer: Any = None, **_kwargs: Any
    ) -> Any:
        calls.append("voluptuous")
        return custom_serializer("value")

    probatio.to_openapi = probatio_convert  # type: ignore[attr-defined]
    voluptuous_openapi.convert = voluptuous_convert  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "probatio", probatio)
    monkeypatch.setitem(sys.modules, "voluptuous_openapi", voluptuous_openapi)
    monkeypatch.setattr(tools.llm, "to_openapi", None, raising=False)

    tools._install_openapi_serializer_compat()
    converter = tools.llm.to_openapi

    assert (
        converter(
            ProbatioSchema(), custom_serializer=lambda _value: voluptuous_unsupported
        )
        is probatio_unsupported
    )
    assert (
        converter(object(), custom_serializer=lambda _value: probatio_unsupported)
        is voluptuous_unsupported
    )
    assert calls == ["probatio", "voluptuous"]


def test_prompt_for_live_tool_without_source_prompt_still_adds_alias() -> None:
    """A prompt-less live tool still contributes its request-local name mapping."""
    reference = {
        "type": tools.TOOL_TYPE,
        "source_type": "api",
        "source_id": "example.Tool",
        "api_id": "example-api",
        "tool_name": "turn_on",
    }
    live = tools.LiveTool(
        reference=reference,
        tool=SimpleNamespace(name="turn_on"),
        instance=SimpleNamespace(),
        source_label="Example API",
        prompt="",
        spec={},
    )
    snapshot = tools.ToolSnapshot(tools={tools.reference_key(reference): live})

    rendered = snapshot.prompt_for(
        [
            {
                "spec": {"name": "ha_local_name"},
                "function": reference,
                "enabled": True,
            }
        ]
    )

    assert rendered == (
        "HA tool names in this request (source name = callable name):\n"
        "Example API: turn_on = ha_local_name"
    )
