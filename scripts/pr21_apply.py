from pathlib import Path
import json


def replace(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text()
    if old not in text:
        raise SystemExit(f"Expected text not found in {path}: {old[:120]!r}")
    p.write_text(text.replace(old, new, 1))


Path("custom_components/extended_openai_conversation_responses/provider_errors.py").write_text('''"""Safe provider failure classification and diagnostics."""

from __future__ import annotations

from collections.abc import Mapping
import json
import logging
from typing import Any

from openai import APIConnectionError, AuthenticationError, OpenAIError

_MAX_MESSAGE = 1000
_MAX_FIELD = 200
_LOGGER = logging.getLogger(__name__)


def _value(source: object | None, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _text(value: object | None, limit: int = _MAX_FIELD) -> str | None:
    if value is None:
        return None
    normalized = " ".join(str(value).split())
    if not normalized:
        return None
    return normalized[:limit]


def _integer(value: object | None) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _root_cause_category(error: BaseException) -> tuple[str | None, str | None]:
    cause = error.__cause__ or error.__context__
    if cause is None:
        return None, None
    cause_type = type(cause).__name__
    lowered = cause_type.casefold()
    if "timeout" in lowered:
        category = "timeout"
    elif "ssl" in lowered or "tls" in lowered or "certificate" in lowered:
        category = "tls"
    elif "dns" in lowered or "name" in lowered:
        category = "dns"
    elif "connect" in lowered or "network" in lowered:
        category = "connection"
    else:
        category = "other"
    return cause_type[:_MAX_FIELD], category


class ProviderStreamError(OpenAIError):
    """Structured provider failure reported inside an established response stream."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        error_type: str | None = None,
        status_code: int | None = None,
        request_id: str | None = None,
        response_id: str | None = None,
    ) -> None:
        super().__init__(_text(message, _MAX_MESSAGE) or "Provider stream failed")
        self.code = _text(code)
        self.type = _text(error_type)
        self.status_code = status_code
        self.request_id = _text(request_id)
        self.response_id = _text(response_id)


def provider_stream_error(
    prefix: str,
    source: object | None,
    *,
    response_id: object | None = None,
) -> ProviderStreamError:
    """Build an OpenAIError-compatible failure from a structured stream event."""
    message = _text(_value(source, "message"), _MAX_MESSAGE) or "unknown reason"
    status = _integer(_value(source, "status_code"))
    if status is None:
        status = _integer(_value(source, "status"))
    return ProviderStreamError(
        f"{prefix}: {message}",
        code=_text(_value(source, "code")),
        error_type=_text(_value(source, "type")),
        status_code=status,
        request_id=_text(_value(source, "request_id")),
        response_id=_text(response_id),
    )


def ensure_successful_responses_result(response: object) -> None:
    """Reject explicit failed/incomplete non-stream Responses API results."""
    response_id = _value(response, "id")
    error = _value(response, "error")
    if error is not None:
        raise provider_stream_error(
            "OpenAI response failed", error, response_id=response_id
        )

    status = _text(_value(response, "status"))
    if status == "incomplete":
        details = _value(response, "incomplete_details")
        reason = _text(_value(details, "reason")) or "unknown reason"
        raise ProviderStreamError(
            f"OpenAI response incomplete: {reason}",
            code=reason,
            error_type="incomplete",
            response_id=_text(response_id),
        )
    if status not in {None, "completed"}:
        raise ProviderStreamError(
            f"OpenAI response ended with status {status}",
            code=status,
            error_type="response_status",
            response_id=_text(response_id),
        )


def provider_error_metadata(error: BaseException) -> dict[str, Any]:
    """Return bounded failure metadata without request/response bodies or credentials."""
    metadata: dict[str, Any] = {
        "message": _text(str(error), _MAX_MESSAGE) or type(error).__name__,
    }
    status = _integer(getattr(error, "status_code", None))
    if status is not None:
        metadata["status_code"] = status
    for attribute, key in (
        ("code", "code"),
        ("type", "provider_error_type"),
        ("request_id", "provider_request_id"),
        ("response_id", "provider_response_id"),
    ):
        if value := _text(getattr(error, attribute, None)):
            metadata[key] = value
    cause_type, cause_category = _root_cause_category(error)
    if cause_type:
        metadata["root_cause_type"] = cause_type
        metadata["root_cause_category"] = cause_category
    return metadata


def provider_user_message(error: BaseException) -> str:
    """Return a concise provider error suitable for UI/Assist surfaces."""
    metadata = provider_error_metadata(error)
    message = str(metadata["message"])
    details: list[str] = []
    if status := metadata.get("status_code"):
        details.append(f"HTTP {status}")
    if code := metadata.get("code"):
        details.append(f"code {code}")
    if request_id := metadata.get("provider_request_id"):
        details.append(f"request {request_id}")
    return f"{message} ({', '.join(details)})" if details else message


def classify_config_provider_error(error: BaseException) -> str:
    """Map provider failures to stable Home Assistant config-flow categories."""
    status = _integer(getattr(error, "status_code", None))
    if isinstance(error, AuthenticationError) or status == 401:
        return "invalid_auth"
    if isinstance(error, APIConnectionError):
        return "cannot_connect"
    if status == 403:
        return "provider_forbidden"
    if status == 429:
        return "provider_rate_limited"
    if status is not None and status >= 500:
        return "provider_unavailable"
    return "provider_error"


def request_reauthentication(hass: Any, entry: Any, error: BaseException) -> bool:
    """Start Home Assistant reauthentication for runtime authentication failures."""
    status = _integer(getattr(error, "status_code", None))
    if not isinstance(error, AuthenticationError) and status != 401:
        return False
    try:
        entry.async_start_reauth(hass)
    except Exception:
        _LOGGER.exception("Unable to start Extended OpenAI reauthentication flow")
        return False
    return True


def log_provider_failure(logger: logging.Logger, context: str, error: BaseException) -> None:
    """Log only bounded, explicitly safe provider failure fields."""
    logger.error(
        "%s: %s",
        context,
        json.dumps(provider_error_metadata(error), sort_keys=True),
    )
''')

replace(
    "custom_components/extended_openai_conversation_responses/config_flow.py",
    "from openai._exceptions import APIConnectionError, AuthenticationError\n",
    "from openai import OpenAIError\n",
)
replace(
    "custom_components/extended_openai_conversation_responses/config_flow.py",
    "from .helpers import get_authenticated_client, get_model_config\n",
    "from .helpers import get_authenticated_client, get_model_config\nfrom .provider_errors import classify_config_provider_error, log_provider_failure\n",
)
replace(
    "custom_components/extended_openai_conversation_responses/config_flow.py",
    '''        except APIConnectionError:\n            errors["base"] = "cannot_connect"\n        except AuthenticationError:\n            errors["base"] = "invalid_auth"\n        except Exception:  # pylint: disable=broad-except\n            _LOGGER.exception("Unexpected exception")\n            errors["base"] = "unknown"\n''',
    '''        except OpenAIError as err:\n            log_provider_failure(_LOGGER, "Provider validation failed", err)\n            errors["base"] = classify_config_provider_error(err)\n        except Exception:  # pylint: disable=broad-except\n            _LOGGER.exception("Unexpected exception")\n            errors["base"] = "unknown"\n''',
)
replace(
    "custom_components/extended_openai_conversation_responses/config_flow.py",
    '''            except APIConnectionError:\n                errors["base"] = "cannot_connect"\n            except AuthenticationError:\n                errors["base"] = "invalid_auth"\n            except Exception:\n                _LOGGER.exception("Unexpected exception during reauthentication")\n                errors["base"] = "unknown"\n''',
    '''            except OpenAIError as err:\n                log_provider_failure(_LOGGER, "Provider reauthentication failed", err)\n                errors["base"] = classify_config_provider_error(err)\n            except Exception:\n                _LOGGER.exception("Unexpected exception during reauthentication")\n                errors["base"] = "unknown"\n''',
)

for path, indent in (
    ("custom_components/extended_openai_conversation_responses/strings.json", 2),
    ("custom_components/extended_openai_conversation_responses/translations/en.json", 4),
):
    p = Path(path)
    data = json.loads(p.read_text())
    data["config"]["error"].update(
        {
            "provider_forbidden": "Provider rejected access for these credentials or account permissions.",
            "provider_rate_limited": "Provider rate limit reached. Try again later.",
            "provider_unavailable": "Provider is temporarily unavailable. Try again later.",
            "provider_error": "Provider rejected the request. Check the provider settings and Home Assistant logs.",
        }
    )
    p.write_text(json.dumps(data, ensure_ascii=False, indent=indent) + "\n")

replace(
    "custom_components/extended_openai_conversation_responses/exceptions.py",
    '''        super().__init__(\n            self,\n            f"failed to parse arguments `{arguments}`. Increase maximum token to avoid the issue.",\n        )\n        self.arguments = arguments\n\n    def __str__(self) -> str:\n        """Return string representation."""\n        return f"failed to parse arguments `{self.arguments}`. Increase maximum token to avoid the issue."\n''',
    '''        super().__init__(\n            self,\n            "The provider returned malformed or unparseable tool-call arguments.",\n        )\n        self.arguments = arguments\n\n    def __str__(self) -> str:\n        """Return string representation."""\n        return "The provider returned malformed or unparseable tool-call arguments."\n''',
)

replace(
    "custom_components/extended_openai_conversation_responses/entity.py",
    "from .provider_loop import MAX_PROVIDER_REQUESTS, assert_provider_loop_completed\n",
    "from .provider_errors import provider_stream_error\nfrom .provider_loop import MAX_PROVIDER_REQUESTS, assert_provider_loop_completed\n",
)
replace(
    "custom_components/extended_openai_conversation_responses/entity.py",
    '''            if event_type == "response.failed":\n                error = getattr(event.response, "error", None)\n                reason = getattr(error, "message", None) or "unknown reason"\n                raise HomeAssistantError(f"OpenAI response failed: {reason}")\n\n            if event_type in {"error", "response.error"}:\n                reason = getattr(event, "message", None) or "unknown reason"\n                raise HomeAssistantError(f"OpenAI response error: {reason}")\n''',
    '''            if event_type == "response.failed":\n                response = getattr(event, "response", None)\n                raise provider_stream_error(\n                    "OpenAI response failed",\n                    getattr(response, "error", None),\n                    response_id=getattr(response, "id", None),\n                )\n\n            if event_type in {"error", "response.error"}:\n                raise provider_stream_error("OpenAI response error", event)\n''',
)

replace(
    "custom_components/extended_openai_conversation_responses/conversation.py",
    "from .prompt import render_effective_prompt\n",
    "from .prompt import render_effective_prompt\nfrom .provider_errors import (\n    log_provider_failure,\n    provider_user_message,\n    request_reauthentication,\n)\nfrom .debug import record_current_provider_failure\n",
)
replace(
    "custom_components/extended_openai_conversation_responses/conversation.py",
    '''        except OpenAIError as err:\n            if self._usage is not None:\n                self._usage.mark_current_run_failed(type(err).__name__)\n            _LOGGER.error(err)\n            intent_response = intent.IntentResponse(language=user_input.language)\n            intent_response.async_set_error(\n                intent.IntentResponseErrorCode.UNKNOWN,\n                f"Sorry, I had a problem talking to OpenAI: {err}",\n            )\n''',
    '''        except OpenAIError as err:\n            if self._usage is not None:\n                self._usage.mark_current_run_failed(type(err).__name__)\n            request_reauthentication(self.hass, self.entry, err)\n            record_current_provider_failure(err)\n            log_provider_failure(_LOGGER, "OpenAI conversation request failed", err)\n            intent_response = intent.IntentResponse(language=user_input.language)\n            intent_response.async_set_error(\n                intent.IntentResponseErrorCode.UNKNOWN,\n                f"Sorry, I had a problem talking to OpenAI: {provider_user_message(err)}",\n            )\n''',
)

replace(
    "custom_components/extended_openai_conversation_responses/ai_task.py",
    "from __future__ import annotations\n\nfrom typing import TYPE_CHECKING\n",
    "from __future__ import annotations\n\nimport logging\nfrom typing import TYPE_CHECKING\n",
)
replace(
    "custom_components/extended_openai_conversation_responses/ai_task.py",
    "from typing import TYPE_CHECKING\n\nfrom homeassistant.components import ai_task, conversation\n",
    "from typing import TYPE_CHECKING\n\nfrom openai import OpenAIError\n\nfrom homeassistant.components import ai_task, conversation\n",
)
replace(
    "custom_components/extended_openai_conversation_responses/ai_task.py",
    "from .entity import ExtendedOpenAIBaseLLMEntity\n",
    "from .debug import record_current_provider_failure\nfrom .entity import ExtendedOpenAIBaseLLMEntity\nfrom .provider_errors import log_provider_failure, request_reauthentication\n",
)
replace(
    "custom_components/extended_openai_conversation_responses/ai_task.py",
    "if TYPE_CHECKING:\n",
    "_LOGGER = logging.getLogger(__name__)\n\nif TYPE_CHECKING:\n",
)
replace(
    "custom_components/extended_openai_conversation_responses/ai_task.py",
    '''        await self._async_handle_chat_log(\n            chat_log,\n            function_tools=[],\n            exposed_entities=[],\n            llm_context=None,\n            structure_name=task.name,\n            structure=task.structure,\n        )\n''',
    '''        try:\n            await self._async_handle_chat_log(\n                chat_log,\n                function_tools=[],\n                exposed_entities=[],\n                llm_context=None,\n                structure_name=task.name,\n                structure=task.structure,\n            )\n        except OpenAIError as err:\n            request_reauthentication(self.hass, self.entry, err)\n            record_current_provider_failure(err)\n            log_provider_failure(_LOGGER, "OpenAI AI Task request failed", err)\n            raise\n''',
)

replace(
    "custom_components/extended_openai_conversation_responses/agent_test.py",
    "from openai import AuthenticationError\n",
    "from openai import AuthenticationError, OpenAIError\n",
)
replace(
    "custom_components/extended_openai_conversation_responses/agent_test.py",
    "from .memory import async_get_memory, memory_enabled\n",
    "from .memory import async_get_memory, memory_enabled\nfrom .provider_errors import ensure_successful_responses_result, provider_user_message\n",
)
replace(
    "custom_components/extended_openai_conversation_responses/agent_test.py",
    '''            response = await client.responses.create(\n                model=model,\n                input=[{"role": "user", "content": "Reply OK."}],\n                max_output_tokens=16,\n                store=False,\n                tools=tools,\n                tool_choice="none",\n            )\n''',
    '''            response = await client.responses.create(\n                model=model,\n                input=[{"role": "user", "content": "Reply OK."}],\n                max_output_tokens=16,\n                store=False,\n                tools=tools,\n                tool_choice="none",\n            )\n            ensure_successful_responses_result(response)\n''',
)
replace(
    "custom_components/extended_openai_conversation_responses/agent_test.py",
    "        authentication.message = str(err)\n",
    "        authentication.message = provider_user_message(err)\n",
)
replace(
    "custom_components/extended_openai_conversation_responses/agent_test.py",
    '''    except Exception as err:\n        await usage_manager.async_record_request(successful=False)\n        checks.append(_check("Model access", "Failed", str(err)))\n        checks.append(_check("Function calling", "Failed", "Probe was rejected"))\n        if web_search and web_search_compatible:\n            checks.append(_check("Web Search", "Failed", str(err)))\n''',
    '''    except OpenAIError as err:\n        await usage_manager.async_record_request(successful=False)\n        message = provider_user_message(err)\n        checks.append(_check("Model access", "Failed", message))\n        checks.append(_check("Function calling", "Failed", "Probe was rejected"))\n        if web_search and web_search_compatible:\n            checks.append(_check("Web Search", "Failed", message))\n    except Exception as err:\n        await usage_manager.async_record_request(successful=False)\n        checks.append(_check("Model access", "Failed", str(err)))\n        checks.append(_check("Function calling", "Failed", "Probe was rejected"))\n        if web_search and web_search_compatible:\n            checks.append(_check("Web Search", "Failed", str(err)))\n''',
)

replace(
    "custom_components/extended_openai_conversation_responses/debug.py",
    "from .const import DOMAIN\n",
    "from .const import DOMAIN\nfrom .provider_errors import provider_error_metadata\n",
)
replace(
    "custom_components/extended_openai_conversation_responses/debug.py",
    "    error_type: str | None = None\n    usage: dict[str, int] = field(default_factory=dict)\n",
    "    error_type: str | None = None\n    error: dict[str, Any] = field(default_factory=dict)\n    usage: dict[str, int] = field(default_factory=dict)\n",
)
replace(
    "custom_components/extended_openai_conversation_responses/debug.py",
    '''        self.successful = successful\n        self.error_type = type(error).__name__ if error is not None else None\n''',
    '''        self.successful = successful\n        self.error_type = type(error).__name__ if error is not None else None\n        self.error = provider_error_metadata(error) if error is not None else {}\n''',
)
replace(
    "custom_components/extended_openai_conversation_responses/debug.py",
    "    error_type: str | None = None\n    usage_run_id: str | None = None\n",
    "    error_type: str | None = None\n    error: dict[str, Any] = field(default_factory=dict)\n    usage_run_id: str | None = None\n",
)
replace(
    "custom_components/extended_openai_conversation_responses/debug.py",
    '            "error_type": self.error_type,\n            "usage_run_id": self.usage_run_id,\n',
    '            "error_type": self.error_type,\n            "error": _jsonable(self.error),\n            "usage_run_id": self.usage_run_id,\n',
)
replace(
    "custom_components/extended_openai_conversation_responses/debug.py",
    '            "error_type": self.error_type,\n            "usage_run_id": self.usage_run_id,\n            "incoming_conversation_id": self.incoming_conversation_id,\n',
    '            "error_type": self.error_type,\n            "error": _jsonable(self.error),\n            "usage_run_id": self.usage_run_id,\n            "incoming_conversation_id": self.incoming_conversation_id,\n',
)
replace(
    "custom_components/extended_openai_conversation_responses/debug.py",
    '''        trace.successful = successful\n        trace.error_type = type(error).__name__ if error is not None else None\n        if result is not None:\n''',
    '''        trace.successful = successful\n        if error is not None:\n            trace.error_type = type(error).__name__\n            trace.error = provider_error_metadata(error)\n        if result is not None:\n''',
)
replace(
    "custom_components/extended_openai_conversation_responses/debug.py",
    '''def current_debug_trace() -> DebugTrace | None:\n    return _ACTIVE_DEBUG_TRACE.get()\n\n\nclass _DebugAsyncStream:\n''',
    '''def current_debug_trace() -> DebugTrace | None:\n    return _ACTIVE_DEBUG_TRACE.get()\n\n\ndef record_current_provider_failure(error: BaseException) -> None:\n    """Attach a provider failure to the active opt-in debug trace, if any."""\n    trace = current_debug_trace()\n    if trace is None:\n        return\n    trace.error_type = type(error).__name__\n    trace.error = provider_error_metadata(error)\n    if trace.provider_requests:\n        trace.provider_requests[-1].finish(successful=False, error=error)\n\n\nclass _DebugAsyncStream:\n''',
)
replace(
    "custom_components/extended_openai_conversation_responses/debug.py",
    "            manager.finish(trace, successful=True, result=result)\n",
    "            manager.finish(trace, successful=trace.error_type is None, result=result)\n",
)

replace(
    "custom_components/extended_openai_conversation_responses/runtime_failure_hardening.py",
    "from .guest_mode import GUEST_MODE_UNAVAILABLE\n",
    "from .debug import record_current_provider_failure\nfrom .guest_mode import GUEST_MODE_UNAVAILABLE\nfrom .provider_errors import (\n    log_provider_failure,\n    provider_user_message,\n    request_reauthentication,\n)\n",
)
replace(
    "custom_components/extended_openai_conversation_responses/runtime_failure_hardening.py",
    '''    if isinstance(err, OpenAIError):\n        _LOGGER.error(err)\n        message = f"Sorry, I had a problem talking to OpenAI: {err}"\n''',
    '''    if isinstance(err, OpenAIError):\n        request_reauthentication(entity.hass, entity.entry, err)\n        record_current_provider_failure(err)\n        log_provider_failure(_LOGGER, "OpenAI request preparation failed", err)\n        message = (\n            "Sorry, I had a problem talking to OpenAI: "\n            f"{provider_user_message(err)}"\n        )\n''',
)

Path("tests/test_pr21_provider_failures.py").write_text('''"""Regression tests for PR21 provider failure handling and diagnostics."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from openai import (
    APIConnectionError,
    AuthenticationError,
    InternalServerError,
    PermissionDeniedError,
    RateLimitError,
)
import pytest

from custom_components.extended_openai_conversation_responses.config_flow import (
    ExtendedOpenAIConversationConfigFlow,
)
from custom_components.extended_openai_conversation_responses.conversation import (
    ExtendedOpenAIAgentEntity,
)
from custom_components.extended_openai_conversation_responses.debug import DebugManager
from custom_components.extended_openai_conversation_responses.entity import (
    ExtendedOpenAIBaseLLMEntity,
)
from custom_components.extended_openai_conversation_responses.exceptions import ParseArgumentsFailed
from custom_components.extended_openai_conversation_responses.provider_errors import (
    ProviderStreamError,
    classify_config_provider_error,
    ensure_successful_responses_result,
    provider_error_metadata,
    request_reauthentication,
)
from homeassistant.const import CONF_API_KEY


def _status_error(error_type, status: int, *, code: str = "provider_code"):
    request = httpx.Request("GET", "https://api.openai.com/v1/models")
    response = httpx.Response(
        status,
        request=request,
        headers={"x-request-id": "req_test_123"},
    )
    return error_type(
        "provider rejected request",
        response=response,
        body={"code": code, "type": "provider_type", "secret": "do-not-log"},
    )


def test_config_failure_classification_covers_status_and_connection_errors() -> None:
    assert classify_config_provider_error(_status_error(AuthenticationError, 401)) == "invalid_auth"
    assert classify_config_provider_error(APIConnectionError(request=httpx.Request("GET", "https://example.com"))) == "cannot_connect"
    assert classify_config_provider_error(_status_error(PermissionDeniedError, 403)) == "provider_forbidden"
    assert classify_config_provider_error(_status_error(RateLimitError, 429)) == "provider_rate_limited"
    assert classify_config_provider_error(_status_error(InternalServerError, 503)) == "provider_unavailable"


def test_provider_metadata_is_bounded_and_body_free() -> None:
    metadata = provider_error_metadata(
        _status_error(RateLimitError, 429, code="rate_limit_exceeded")
    )
    assert metadata["status_code"] == 429
    assert metadata["code"] == "rate_limit_exceeded"
    assert metadata["provider_request_id"] == "req_test_123"
    assert "body" not in metadata
    assert "secret" not in str(metadata)


async def test_config_and_reauth_use_same_provider_classification() -> None:
    error = _status_error(RateLimitError, 429)
    flow = SimpleNamespace(
        hass=MagicMock(),
        _reauth_entry=SimpleNamespace(data={CONF_API_KEY: "old"}),
        async_show_form=MagicMock(side_effect=lambda **kwargs: kwargs),
        async_create_entry=MagicMock(),
        async_update_reload_and_abort=MagicMock(),
    )
    with patch(
        "custom_components.extended_openai_conversation_responses.config_flow.validate_input",
        AsyncMock(side_effect=error),
    ):
        initial = await ExtendedOpenAIConversationConfigFlow.async_step_user(
            flow, {CONF_API_KEY: "key"}
        )
        reauth = await ExtendedOpenAIConversationConfigFlow.async_step_reauth_confirm(
            flow, {CONF_API_KEY: "replacement"}
        )
    assert initial["errors"]["base"] == "provider_rate_limited"
    assert reauth["errors"]["base"] == "provider_rate_limited"


class FakeStream:
    def __init__(self, events: list[object]) -> None:
        self._events = iter(events)
    def __aiter__(self):
        return self
    async def __anext__(self):
        try:
            return next(self._events)
        except StopIteration as err:
            raise StopAsyncIteration from err


def _entity() -> ExtendedOpenAIBaseLLMEntity:
    entity = ExtendedOpenAIBaseLLMEntity.__new__(ExtendedOpenAIBaseLLMEntity)
    entity.subentry = SimpleNamespace(data={})
    return entity


async def test_responses_failed_preserves_structured_provider_code() -> None:
    event = SimpleNamespace(
        type="response.failed",
        response=SimpleNamespace(
            id="resp_123",
            error=SimpleNamespace(
                message="capacity unavailable",
                code="server_error",
                type="server_error",
            ),
        ),
    )
    with pytest.raises(ProviderStreamError) as caught:
        _ = [
            item
            async for item in _entity()._transform_responses_stream(
                SimpleNamespace(async_trace=lambda _value: None), FakeStream([event])
            )
        ]
    assert caught.value.code == "server_error"
    assert caught.value.type == "server_error"
    assert caught.value.response_id == "resp_123"


async def test_response_error_event_preserves_code() -> None:
    event = SimpleNamespace(
        type="response.error",
        message="rate limited",
        code="rate_limit_exceeded",
        request_id="req_stream_1",
    )
    with pytest.raises(ProviderStreamError) as caught:
        _ = [
            item
            async for item in _entity()._transform_responses_stream(
                SimpleNamespace(async_trace=lambda _value: None), FakeStream([event])
            )
        ]
    assert caught.value.code == "rate_limit_exceeded"
    assert caught.value.request_id == "req_stream_1"


def test_debug_provider_request_records_safe_failure_metadata() -> None:
    manager = DebugManager()
    trace = manager.begin(
        entry_id="entry",
        subentry_id="agent",
        user_input={},
        incoming_conversation_id=None,
    )
    request = trace.start_provider_request("responses", (), {})
    request.finish(successful=False, error=_status_error(RateLimitError, 429))
    payload = request.as_dict()
    assert payload["error_type"] == "RateLimitError"
    assert payload["error"]["status_code"] == 429
    assert payload["error"]["provider_request_id"] == "req_test_123"
    assert "body" not in payload["error"]


def test_nonstream_responses_result_rejects_failed_and_incomplete_states() -> None:
    with pytest.raises(ProviderStreamError, match="failed") as failed:
        ensure_successful_responses_result(
            SimpleNamespace(
                id="resp_failed",
                status="failed",
                error=SimpleNamespace(message="bad", code="server_error", type="server_error"),
            )
        )
    assert failed.value.code == "server_error"
    with pytest.raises(ProviderStreamError, match="incomplete") as incomplete:
        ensure_successful_responses_result(
            SimpleNamespace(
                id="resp_incomplete",
                status="incomplete",
                error=None,
                incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            )
        )
    assert incomplete.value.code == "max_output_tokens"


def test_runtime_authentication_failure_starts_reauth() -> None:
    hass = MagicMock()
    entry = SimpleNamespace(async_start_reauth=MagicMock())
    assert request_reauthentication(
        hass, entry, _status_error(AuthenticationError, 401)
    ) is True
    entry.async_start_reauth.assert_called_once_with(hass)


class _Usage:
    def __init__(self) -> None:
        self.failed: list[str] = []
    def mark_current_run_failed(self, name: str) -> None:
        self.failed.append(name)


async def test_conversation_runtime_401_starts_reauth_and_returns_error() -> None:
    auth_error = _status_error(AuthenticationError, 401)
    entry = SimpleNamespace(async_start_reauth=MagicMock())
    entity = SimpleNamespace(
        hass=MagicMock(),
        entry=entry,
        subentry=SimpleNamespace(data={}),
        _usage=_Usage(),
        _get_exposed_entities=lambda: [],
        _get_function_tools=lambda: [],
        _async_retrieve_memories=AsyncMock(return_value=[]),
        _async_retrieve_temporary_memories=AsyncMock(return_value=[]),
        _build_system_prompt=lambda *_args: "system",
        _async_handle_chat_log=AsyncMock(side_effect=auth_error),
        _fire_conversation_finished=MagicMock(),
    )
    user_input = SimpleNamespace(
        language="en",
        conversation_id="conversation",
        text="hello",
        as_llm_context=lambda _domain: SimpleNamespace(),
    )
    chat_log = SimpleNamespace(content=[None], conversation_id="conversation")
    result = await ExtendedOpenAIAgentEntity._async_handle_message(
        entity, user_input, chat_log
    )
    entry.async_start_reauth.assert_called_once_with(entity.hass)
    assert entity._usage.failed == ["AuthenticationError"]
    assert result.conversation_id == "conversation"


def test_parse_arguments_error_no_longer_claims_token_exhaustion() -> None:
    message = str(ParseArgumentsFailed('{"broken":'))
    assert "malformed or unparseable" in message
    assert "maximum token" not in message.casefold()
    assert '{"broken":' not in message
''')

replace(
    "tests/test_agent_test.py",
    "    API_MODE_CHAT_COMPLETIONS,\n",
    "    API_MODE_CHAT_COMPLETIONS,\n    API_MODE_RESPONSES,\n",
)
with Path("tests/test_agent_test.py").open("a") as f:
    f.write('''\n\nasync def test_responses_failed_status_is_reported() -> None:\n    hass, entry, subentry, client, usage = _objects(\n        {CONF_API_MODE: API_MODE_RESPONSES, CONF_CHAT_MODEL: "gpt-5.6"}\n    )\n    client.responses = SimpleNamespace(\n        create=AsyncMock(\n            return_value=SimpleNamespace(\n                id="resp_failed", status="failed",\n                error=SimpleNamespace(message="provider failed", code="server_error", type="server_error"),\n                usage=None,\n            )\n        )\n    )\n    with (\n        patch("custom_components.extended_openai_conversation_responses.agent_test.get_exposed_entities", return_value=[SimpleNamespace()]),\n        patch("custom_components.extended_openai_conversation_responses.agent_test.async_get_usage", AsyncMock(return_value=usage)),\n    ):\n        result = await async_test_agent(hass, entry, subentry)\n    model = next(check for check in result.checks if check.name == "Model access")\n    assert result.status == "Failed"\n    assert model.status == "Failed"\n    assert "server_error" in model.message\n    usage.async_record_request.assert_awaited_once_with(successful=False)\n\n\nasync def test_responses_incomplete_status_is_reported() -> None:\n    hass, entry, subentry, client, usage = _objects(\n        {CONF_API_MODE: API_MODE_RESPONSES, CONF_CHAT_MODEL: "gpt-5.6"}\n    )\n    client.responses = SimpleNamespace(\n        create=AsyncMock(\n            return_value=SimpleNamespace(\n                id="resp_incomplete", status="incomplete", error=None,\n                incomplete_details=SimpleNamespace(reason="max_output_tokens"), usage=None,\n            )\n        )\n    )\n    with (\n        patch("custom_components.extended_openai_conversation_responses.agent_test.get_exposed_entities", return_value=[SimpleNamespace()]),\n        patch("custom_components.extended_openai_conversation_responses.agent_test.async_get_usage", AsyncMock(return_value=usage)),\n    ):\n        result = await async_test_agent(hass, entry, subentry)\n    model = next(check for check in result.checks if check.name == "Model access")\n    assert result.status == "Failed"\n    assert model.status == "Failed"\n    assert "max_output_tokens" in model.message\n    usage.async_record_request.assert_awaited_once_with(successful=False)\n''')

Path(".github/workflows/pr21-apply.yml").unlink(missing_ok=True)
Path("scripts/pr21_apply.py").unlink(missing_ok=True)
