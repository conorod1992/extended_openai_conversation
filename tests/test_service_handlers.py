"""Behavioural coverage for Extended OpenAI service handlers."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, MagicMock

from openai import OpenAIError
import pytest

from custom_components.extended_openai_conversation_responses.const import (
    API_MODE_RESPONSES,
    CONF_API_KEY,
    CONF_API_MODE,
    CONF_API_PROVIDER,
    CONF_BASE_URL,
    DEFAULT_CONF_BASE_URL,
    DOMAIN,
    GITHUB_SKILLS_BRANCH,
    SERVICE_DOWNLOAD_SKILL,
    SERVICE_MEMORY_CLEAR,
    SERVICE_MEMORY_DELETE,
    SERVICE_MEMORY_LIST,
    SERVICE_QUERY_IMAGE,
    SERVICE_RELOAD_SKILLS,
)
from custom_components.extended_openai_conversation_responses.memory import MemoryRecord
from custom_components.extended_openai_conversation_responses.services import (
    SERVICE_ENABLE_FUNCTION_GROUPS,
    async_set_function_groups_enabled,
    async_set_function_tools_enabled,
    async_setup_services,
    async_skill_source_ref,
    encode_image,
    prepare_image_params,
    resolve_memory_agent,
    to_image_param,
)
from custom_components.extended_openai_conversation_responses.skills import SkillManager
from homeassistant.exceptions import HomeAssistantError


async def _handlers(hass) -> dict[str, object]:
    await async_setup_services(hass, {})
    return {
        registration.args[1]: registration.args[2]
        for registration in hass.services.async_register.call_args_list
        if registration.args[0] == DOMAIN
    }


def _call(data: dict, user_id: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(data=data, context=SimpleNamespace(user_id=user_id))


@pytest.mark.parametrize(
    ("entry", "registry_entry", "message"),
    [
        (None, None, "Config entry not found"),
        (
            SimpleNamespace(domain="other", subentries={}),
            None,
            "Config entry not found",
        ),
        (
            SimpleNamespace(domain=DOMAIN, subentries={}),
            SimpleNamespace(config_entry_id="other", config_subentry_id="agent"),
            "does not belong",
        ),
        (
            SimpleNamespace(domain=DOMAIN, subentries={}),
            SimpleNamespace(config_entry_id="entry", config_subentry_id=None),
            "not linked to a subentry",
        ),
        (SimpleNamespace(domain=DOMAIN, subentries={}), None, "agent not found"),
    ],
)
def test_resolve_memory_agent_rejects_invalid_references(
    hass, monkeypatch, entry, registry_entry, message
) -> None:
    hass.config_entries.async_get_entry.return_value = entry
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.er.async_get",
        lambda _hass: SimpleNamespace(async_get=lambda _reference: registry_entry),
    )

    with pytest.raises(HomeAssistantError, match=message):
        resolve_memory_agent(hass, "entry", "agent")


async def test_function_state_actions_report_unknown_names(hass, monkeypatch) -> None:
    subentry = SimpleNamespace(
        subentry_id="agent",
        subentry_type="conversation",
        data={
            "functions": [
                {
                    "spec": {
                        "name": "known",
                        "description": "Known",
                        "parameters": {"type": "object", "properties": {}},
                    },
                    "function": {"type": "native", "name": "get_energy"},
                }
            ],
            "function_groups": [
                {
                    "id": "known-group",
                    "name": "Known",
                    "description": "Known group",
                    "loading_mode": "on_demand",
                    "functions": ["known"],
                }
            ],
        },
    )
    entry = SimpleNamespace(domain=DOMAIN, subentries={"agent": subentry})
    hass.config_entries.async_get_entry.return_value = entry
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.er.async_get",
        lambda _hass: SimpleNamespace(async_get=lambda _reference: None),
    )

    with pytest.raises(HomeAssistantError, match="Function Tool not found: missing"):
        await async_set_function_tools_enabled(
            hass, "entry", "agent", ["missing", "missing"], True
        )
    with pytest.raises(HomeAssistantError, match="Function Group not found: missing"):
        await async_set_function_groups_enabled(
            hass, "entry", "agent", ["missing", "missing"], True
        )
    hass.config_entries.async_update_subentry.assert_not_called()


async def test_skill_source_ref_rejects_blank_and_falls_back_without_version(
    hass, monkeypatch
) -> None:
    with pytest.raises(HomeAssistantError, match="cannot be empty"):
        await async_skill_source_ref(hass, "   ")

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.async_get_integration",
        AsyncMock(return_value=SimpleNamespace(version="   ")),
    )
    assert await async_skill_source_ref(hass) == GITHUB_SKILLS_BRANCH


@pytest.mark.parametrize("api_mode", [API_MODE_RESPONSES, "chat_completions"])
async def test_query_image_builds_provider_request_for_each_api_mode(
    hass, monkeypatch, api_mode
) -> None:
    response = SimpleNamespace(model_dump=lambda: {"id": "response-id"})
    responses_create = AsyncMock(return_value=response)
    chat_create = AsyncMock(return_value=response)
    entry = SimpleNamespace(
        domain=DOMAIN,
        runtime_data=SimpleNamespace(
            responses=SimpleNamespace(create=responses_create),
            chat=SimpleNamespace(completions=SimpleNamespace(create=chat_create)),
        ),
    )
    hass.config_entries.async_get_entry.return_value = entry
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.get_api_mode",
        lambda _requested, _model: api_mode,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.get_token_param_for_model",
        lambda _model: "max_tokens",
    )
    ensure_success = MagicMock()
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.ensure_successful_responses_result",
        ensure_success,
    )
    handlers = await _handlers(hass)

    result = await handlers[SERVICE_QUERY_IMAGE](
        _call(
            {
                "config_entry": "entry",
                "model": "gpt-test",
                CONF_API_MODE: api_mode,
                "prompt": "What is shown?",
                "images": [{"url": "https://example.test/image.png"}],
                "max_tokens": 123,
            }
        )
    )

    assert result == {"id": "response-id"}
    if api_mode == API_MODE_RESPONSES:
        kwargs = responses_create.await_args.kwargs
        assert kwargs["input"][0]["content"] == [
            {"type": "input_text", "text": "What is shown?"},
            {
                "type": "input_image",
                "image_url": "https://example.test/image.png",
                "detail": "auto",
            },
        ]
        assert kwargs["max_output_tokens"] == 123
        assert kwargs["store"] is False
        ensure_success.assert_called_once_with(response)
        chat_create.assert_not_awaited()
    else:
        kwargs = chat_create.await_args.kwargs
        assert kwargs["messages"][0]["content"][1] == {
            "type": "image_url",
            "image_url": {"url": "https://example.test/image.png"},
        }
        assert kwargs["max_tokens"] == 123
        responses_create.assert_not_awaited()


async def test_query_image_translates_provider_error_and_requests_reauthentication(
    hass, monkeypatch
) -> None:
    entry = SimpleNamespace(
        domain=DOMAIN,
        runtime_data=SimpleNamespace(
            responses=SimpleNamespace(
                create=AsyncMock(side_effect=OpenAIError("provider unavailable"))
            )
        ),
    )
    hass.config_entries.async_get_entry.return_value = entry
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.get_api_mode",
        lambda *_: API_MODE_RESPONSES,
    )
    reauthenticate = MagicMock()
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.request_reauthentication",
        reauthenticate,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.provider_user_message",
        lambda _err: "Provider request failed",
    )
    handlers = await _handlers(hass)

    with pytest.raises(HomeAssistantError, match="Error generating image: Provider"):
        await handlers[SERVICE_QUERY_IMAGE](
            _call(
                {
                    "config_entry": "entry",
                    "model": "gpt-test",
                    CONF_API_MODE: API_MODE_RESPONSES,
                    "prompt": "inspect",
                    "images": [{"url": "https://example.test/image.png"}],
                    "max_tokens": 100,
                }
            )
        )
    reauthenticate.assert_called_once_with(hass, entry, ANY)


async def test_query_image_rejects_wrong_config_entry(hass) -> None:
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(domain="other")
    handlers = await _handlers(hass)

    with pytest.raises(HomeAssistantError, match="Config entry not found"):
        await handlers[SERVICE_QUERY_IMAGE](
            _call(
                {
                    "config_entry": "entry",
                    "model": "gpt-test",
                    CONF_API_MODE: API_MODE_RESPONSES,
                    "prompt": "inspect",
                    "images": [{"url": "https://example.test/image.png"}],
                    "max_tokens": 100,
                }
            )
        )


async def test_change_config_validates_and_saves_provider_settings(
    hass, monkeypatch
) -> None:
    entry = SimpleNamespace(
        domain=DOMAIN,
        data={CONF_API_KEY: "old", CONF_BASE_URL: "https://custom.test/v1"},
    )
    hass.config_entries.async_get_entry.return_value = entry
    authenticate = AsyncMock(return_value=object())
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.get_authenticated_client",
        authenticate,
    )
    handlers = await _handlers(hass)

    await handlers["change_config"](
        _call(
            {
                "config_entry": "entry",
                CONF_API_KEY: "new",
                CONF_BASE_URL: DEFAULT_CONF_BASE_URL,
            }
        )
    )

    authenticate.assert_awaited_once()
    assert authenticate.await_args.kwargs["api_key"] == "new"
    assert authenticate.await_args.kwargs["base_url"] is None
    saved = hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert saved == {CONF_API_KEY: "new"}


async def test_change_config_ignores_empty_update_and_rejects_invalid_entry(
    hass,
) -> None:
    handlers = await _handlers(hass)
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        domain=DOMAIN, data={CONF_API_KEY: "key"}
    )
    await handlers["change_config"](_call({"config_entry": "entry"}))
    hass.config_entries.async_update_entry.assert_not_called()

    hass.config_entries.async_get_entry.return_value = None
    with pytest.raises(HomeAssistantError, match="Config entry entry not found"):
        await handlers["change_config"](
            _call({"config_entry": "entry", CONF_API_KEY: "new"})
        )


async def test_change_config_rejects_azure_without_endpoint(hass) -> None:
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        domain=DOMAIN, data={CONF_API_KEY: "key"}
    )
    handlers = await _handlers(hass)

    with pytest.raises(HomeAssistantError, match="requires a custom base URL"):
        await handlers["change_config"](
            _call({"config_entry": "entry", CONF_API_PROVIDER: "azure"})
        )
    hass.config_entries.async_update_entry.assert_not_called()


async def test_change_config_translates_authentication_error(hass, monkeypatch) -> None:
    hass.config_entries.async_get_entry.return_value = SimpleNamespace(
        domain=DOMAIN, data={CONF_API_KEY: "old"}
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.get_authenticated_client",
        AsyncMock(side_effect=OpenAIError("invalid key")),
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.provider_user_message",
        lambda _err: "Invalid credentials",
    )
    handlers = await _handlers(hass)

    with pytest.raises(HomeAssistantError, match="Invalid credentials"):
        await handlers["change_config"](
            _call({"config_entry": "entry", CONF_API_KEY: "new"})
        )
    hass.config_entries.async_update_entry.assert_not_called()


async def test_change_config_does_not_overwrite_concurrent_update(
    hass, monkeypatch
) -> None:
    entry = SimpleNamespace(domain=DOMAIN, data={CONF_API_KEY: "old"})
    hass.config_entries.async_get_entry.return_value = entry

    async def authenticate(**_kwargs):
        entry.data = {CONF_API_KEY: "changed elsewhere"}

    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.get_authenticated_client",
        authenticate,
    )
    handlers = await _handlers(hass)

    with pytest.raises(HomeAssistantError, match=r"changed while.*validated"):
        await handlers["change_config"](
            _call({"config_entry": "entry", CONF_API_KEY: "new"})
        )
    hass.config_entries.async_update_entry.assert_not_called()


async def test_reload_skills_returns_loaded_count(hass, monkeypatch) -> None:
    manager = SimpleNamespace(
        async_load_skills=AsyncMock(), get_all_skills=MagicMock(return_value=[1, 2, 3])
    )
    monkeypatch.setattr(
        SkillManager, "async_get_instance", AsyncMock(return_value=manager)
    )
    handlers = await _handlers(hass)

    assert await handlers[SERVICE_RELOAD_SKILLS](_call({})) == {"loaded_skills": 3}
    manager.async_load_skills.assert_awaited_once_with()


class _Response:
    def __init__(self, status: int, *, payload=None, body: bytes = b"") -> None:
        self.status = status
        self.payload = payload
        self.body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


async def _download_handler(hass, monkeypatch, tmp_path: Path, responses: dict):
    manager = SimpleNamespace(
        user_skills_dir=tmp_path / "skills",
        staging_dir=tmp_path / "staging",
        async_publish_staged_skill=AsyncMock(),
    )
    monkeypatch.setattr(
        SkillManager, "async_get_instance", AsyncMock(return_value=manager)
    )
    session = SimpleNamespace(get=MagicMock(side_effect=lambda url: responses[url]))
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.async_get_clientsession",
        lambda _hass: session,
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.async_skill_source_ref",
        AsyncMock(return_value="v1.2.3"),
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.uuid4",
        lambda: SimpleNamespace(hex="fixed"),
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.async_read_bounded_json",
        AsyncMock(side_effect=lambda response, _description: response.payload),
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.async_read_bounded_response",
        AsyncMock(side_effect=lambda response, *_args: response.body),
    )
    return (await _handlers(hass))[SERVICE_DOWNLOAD_SKILL], manager


async def test_download_skill_recursively_stages_and_publishes_files(
    hass, monkeypatch, tmp_path
) -> None:
    root_url = "https://api.github.com/repos/conorod1992/extended_openai_conversation/contents/examples/skills/demo?ref=v1.2.3"
    responses = {
        root_url: _Response(
            200,
            payload=[
                {
                    "name": "SKILL.md",
                    "path": "skills/demo/SKILL.md",
                    "type": "file",
                    "download_url": "download:skill",
                    "size": 7,
                },
                {
                    "name": "assets",
                    "path": "skills/demo/assets",
                    "type": "dir",
                    "url": "api:assets",
                },
            ],
        ),
        "download:skill": _Response(200, body=b"# Demo\n"),
        "api:assets": _Response(
            200,
            payload=[
                {
                    "name": "guide.txt",
                    "path": "skills/demo/assets/guide.txt",
                    "type": "file",
                    "download_url": "download:guide",
                    "size": 5,
                }
            ],
        ),
        "download:guide": _Response(200, body=b"guide"),
    }
    handler, manager = await _download_handler(hass, monkeypatch, tmp_path, responses)

    result = await handler(_call({"skill_name": "demo"}))

    assert result["skill_name"] == "demo"
    assert result["source_ref"] == "v1.2.3"
    assert result["downloaded_files"] == [
        "skills/demo/SKILL.md",
        "skills/demo/assets/guide.txt",
    ]
    staged = tmp_path / "staging" / "demo.download-fixed"
    assert (staged / "SKILL.md").read_text() == "# Demo\n"
    assert (staged / "assets" / "guide.txt").read_text() == "guide"
    manager.async_publish_staged_skill.assert_awaited_once_with("demo", staged)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_Response(404), "not found at source ref"),
        (_Response(503), "Failed to fetch skill from GitHub"),
        (_Response(200, payload={"message": "not a listing"}), "Unexpected response"),
        (_Response(200, payload=["not an object"]), "Unexpected item"),
        (
            _Response(200, payload=[{"name": "../escape", "type": "dir", "url": "x"}]),
            "unsafe path",
        ),
    ],
)
async def test_download_skill_rejects_invalid_remote_content_and_cleans_staging(
    hass, monkeypatch, tmp_path, response, message
) -> None:
    root_url = "https://api.github.com/repos/conorod1992/extended_openai_conversation/contents/examples/skills/demo?ref=v1.2.3"
    handler, manager = await _download_handler(
        hass, monkeypatch, tmp_path, {root_url: response}
    )

    with pytest.raises(HomeAssistantError, match=message):
        await handler(_call({"skill_name": "demo"}))
    assert not (tmp_path / "staging" / "demo.download-fixed").exists()
    manager.async_publish_staged_skill.assert_not_awaited()


async def test_download_skill_requires_manifest_and_cleans_staging(
    hass, monkeypatch, tmp_path
) -> None:
    root_url = "https://api.github.com/repos/conorod1992/extended_openai_conversation/contents/examples/skills/demo?ref=v1.2.3"
    responses = {
        root_url: _Response(
            200,
            payload=[
                {
                    "name": "README.md",
                    "type": "file",
                    "download_url": "download:readme",
                }
            ],
        ),
        "download:readme": _Response(200, body=b"readme"),
    }
    handler, manager = await _download_handler(hass, monkeypatch, tmp_path, responses)

    with pytest.raises(HomeAssistantError, match=r"does not contain SKILL\.md"):
        await handler(_call({"skill_name": "demo"}))
    assert not (tmp_path / "staging" / "demo.download-fixed").exists()
    manager.async_publish_staged_skill.assert_not_awaited()


async def test_download_skill_wraps_unexpected_publish_failure_and_cleans_staging(
    hass, monkeypatch, tmp_path
) -> None:
    root_url = "https://api.github.com/repos/conorod1992/extended_openai_conversation/contents/examples/skills/demo?ref=v1.2.3"
    responses = {
        root_url: _Response(
            200,
            payload=[
                {
                    "name": "SKILL.md",
                    "type": "file",
                    "download_url": "download:skill",
                }
            ],
        ),
        "download:skill": _Response(200, body=b"# Demo"),
    }
    handler, manager = await _download_handler(hass, monkeypatch, tmp_path, responses)
    manager.async_publish_staged_skill.side_effect = RuntimeError("disk offline")

    with pytest.raises(HomeAssistantError, match="disk offline"):
        await handler(_call({"skill_name": "demo"}))
    assert not (tmp_path / "staging" / "demo.download-fixed").exists()


def _record(memory_id: str) -> MemoryRecord:
    return MemoryRecord(
        memory_id=memory_id,
        user_id="alice",
        content="Prefers tea",
        category="preference",
        source="explicit",
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
    )


async def _memory_handlers(hass, monkeypatch, memory):
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.resolve_memory_agent",
        lambda *_args: ("entry", "agent"),
    )
    get_memory = AsyncMock(return_value=memory)
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.async_get_memory",
        get_memory,
    )
    return await _handlers(hass), get_memory


async def test_memory_list_supports_browse_and_search(hass, monkeypatch) -> None:
    memory = SimpleNamespace(
        async_list=AsyncMock(return_value=[_record("listed")]),
        async_search=AsyncMock(return_value=[_record("found")]),
    )
    handlers, get_memory = await _memory_handlers(hass, monkeypatch, memory)

    listed = await handlers[SERVICE_MEMORY_LIST](
        _call(
            {
                "config_entry": "entry",
                "agent_id": "agent",
                "category": "preference",
                "limit": 10,
                "offset": 5,
            },
            "alice",
        )
    )
    found = await handlers[SERVICE_MEMORY_LIST](
        _call(
            {
                "config_entry": "entry",
                "agent_id": "agent",
                "query": "tea",
                "limit": 3,
                "offset": 0,
            },
            "alice",
        )
    )

    assert listed["memories"][0]["memory_id"] == "listed"
    assert found["memories"][0]["memory_id"] == "found"
    memory.async_list.assert_awaited_once_with("alice", "preference", 10, 5)
    memory.async_search.assert_awaited_once_with("alice", "tea", None, 3)
    get_memory.assert_awaited_with(hass, "entry", "agent")


async def test_memory_delete_and_clear_are_user_scoped(hass, monkeypatch) -> None:
    memory = SimpleNamespace(
        async_delete=AsyncMock(return_value=2),
        async_clear=AsyncMock(return_value=4),
    )
    handlers, _ = await _memory_handlers(hass, monkeypatch, memory)

    deleted = await handlers[SERVICE_MEMORY_DELETE](
        _call(
            {
                "config_entry": "entry",
                "agent_id": "agent",
                "memory_ids": ["one", "two"],
            },
            "alice",
        )
    )
    cleared = await handlers[SERVICE_MEMORY_CLEAR](
        _call(
            {
                "config_entry": "entry",
                "agent_id": "agent",
                "confirm": True,
                "category": "preference",
            },
            "alice",
        )
    )

    assert deleted == {"deleted": 2}
    assert cleared == {"deleted": 4}
    memory.async_delete.assert_awaited_once_with("alice", ["one", "two"])
    memory.async_clear.assert_awaited_once_with("alice", "preference")


async def test_memory_clear_requires_explicit_confirmation(hass) -> None:
    handlers = await _handlers(hass)

    with pytest.raises(HomeAssistantError, match="Set confirm to true"):
        await handlers[SERVICE_MEMORY_CLEAR](
            _call(
                {
                    "config_entry": "entry",
                    "agent_id": "agent",
                    "confirm": False,
                }
            )
        )


async def test_guest_mode_update_translates_invalid_interval(hass, monkeypatch) -> None:
    entry = SimpleNamespace(
        domain=DOMAIN,
        subentries={"agent": SimpleNamespace(subentry_type="conversation")},
    )
    hass.config_entries.async_get_entry.return_value = entry
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.er.async_get",
        lambda _hass: SimpleNamespace(async_get=lambda _reference: None),
    )
    manager = SimpleNamespace(
        async_update_trusted=AsyncMock(side_effect=ValueError("end precedes start"))
    )
    monkeypatch.setattr(
        "custom_components.extended_openai_conversation_responses.services.async_get_guest_mode",
        AsyncMock(return_value=manager),
    )
    handlers = await _handlers(hass)

    with pytest.raises(HomeAssistantError, match="end precedes start"):
        await handlers["guest_mode_update"](
            _call(
                {
                    "config_entry": "entry",
                    "agent_id": "agent",
                    "active_from": "later",
                    "active_until": "earlier",
                    "indefinite": False,
                }
            )
        )


def test_image_parameters_preserve_remote_urls_and_encode_local_images(
    hass, tmp_path
) -> None:
    remote = {"url": "https://example.test/image.png", "caption": "remote"}
    assert to_image_param(hass, remote) == remote

    image_path = tmp_path / "tiny.png"
    image_path.write_bytes(b"png bytes")
    hass.config.is_allowed_path.return_value = True
    converted = to_image_param(hass, {"url": str(image_path)})
    assert converted["url"] == "data:image/png;base64,cG5nIGJ5dGVz"
    assert encode_image(str(image_path)) == "cG5nIGJ5dGVz"


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("denied", "no access to path"),
        ("missing", "does not exist"),
        ("directory", "is not a file"),
        ("text", "is not an image"),
    ],
)
def test_image_parameters_reject_invalid_local_sources(
    hass, tmp_path, setup, message
) -> None:
    path = tmp_path / ("asset.txt" if setup == "text" else "asset.png")
    hass.config.is_allowed_path.return_value = setup != "denied"
    if setup == "directory":
        path.mkdir()
    elif setup == "text":
        path.write_text("not an image")

    with pytest.raises(HomeAssistantError, match=message):
        to_image_param(hass, {"url": str(path)})


def test_prepare_image_params_enforces_attachment_limit(hass) -> None:
    from custom_components.extended_openai_conversation_responses.resource_limits import (
        MAX_ATTACHMENT_COUNT,
    )

    images = [{"url": "https://example.test/image.png"}] * (MAX_ATTACHMENT_COUNT + 1)
    with pytest.raises(HomeAssistantError, match="At most"):
        prepare_image_params(hass, images)


async def test_function_group_service_requires_an_existing_admin_user(hass) -> None:
    hass.auth.async_get_user.return_value = None
    handlers = await _handlers(hass)

    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await handlers[SERVICE_ENABLE_FUNCTION_GROUPS](
            _call(
                {
                    "config_entry": "entry",
                    "agent_id": "agent",
                    "function_groups": ["group"],
                },
                "deleted-user",
            )
        )
