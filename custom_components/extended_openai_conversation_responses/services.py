"""Services for the extended openai conversation component."""

import base64
import logging
import mimetypes
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from openai._exceptions import OpenAIError
import voluptuous as vol

from homeassistant.const import CONF_API_KEY
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import (
    config_validation as cv,
    entity_registry as er,
    selector,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .agent_config import merge_agent_config, validate_function_tools
from .const import (
    API_MODE_OPTIONS,
    API_MODE_RESPONSES,
    CONF_API_MODE,
    CONF_API_PROVIDER,
    CONF_API_VERSION,
    CONF_BASE_URL,
    CONF_FUNCTION_TOOLS,
    CONF_ORGANIZATION,
    CONF_SKIP_AUTHENTICATION,
    DEFAULT_API_MODE,
    DEFAULT_CONF_BASE_URL,
    DOMAIN,
    GITHUB_REPO_NAME,
    GITHUB_REPO_OWNER,
    GITHUB_SKILLS_BRANCH,
    GITHUB_SKILLS_PATH,
    SERVICE_DISABLE_FUNCTION_TOOLS,
    SERVICE_DOWNLOAD_SKILL,
    SERVICE_ENABLE_FUNCTION_TOOLS,
    SERVICE_MEMORY_CLEAR,
    SERVICE_MEMORY_DELETE,
    SERVICE_MEMORY_LIST,
    SERVICE_QUERY_IMAGE,
    SERVICE_RELOAD_SKILLS,
)
from .helpers import get_api_mode, get_authenticated_client, get_token_param_for_model
from .memory import async_get_memory, memory_as_dict, memory_user_id

QUERY_IMAGE_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry"): selector.ConfigEntrySelector(
            {
                "integration": DOMAIN,
            }
        ),
        vol.Required("model", default="gpt-4.1-mini"): cv.string,
        vol.Optional(CONF_API_MODE, default=DEFAULT_API_MODE): vol.In(
            [mode["key"] for mode in API_MODE_OPTIONS]
        ),
        vol.Required("prompt"): cv.string,
        vol.Required("images"): vol.All(cv.ensure_list, [{"url": cv.string}]),
        vol.Optional("max_tokens", default=300): cv.positive_int,
    }
)

CHANGE_CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required("config_entry"): selector.ConfigEntrySelector(
            {
                "integration": DOMAIN,
            }
        ),
        vol.Optional(CONF_API_KEY): cv.string,
        vol.Optional(CONF_BASE_URL): cv.string,
        vol.Optional(CONF_API_VERSION): cv.string,
        vol.Optional(CONF_ORGANIZATION): cv.string,
        vol.Optional(CONF_SKIP_AUTHENTICATION): cv.boolean,
        vol.Optional(CONF_API_PROVIDER): cv.string,
    }
)

RELOAD_SKILLS_SCHEMA = vol.Schema({})

DOWNLOAD_SKILL_SCHEMA = vol.Schema(
    {
        vol.Required("skill_name"): cv.string,
    }
)

MEMORY_AGENT_FIELDS: dict[Any, Any] = {
    vol.Required("config_entry"): selector.ConfigEntrySelector({"integration": DOMAIN}),
    vol.Required("agent_id"): cv.string,
}

MEMORY_LIST_SCHEMA = vol.Schema(
    {
        **MEMORY_AGENT_FIELDS,
        vol.Optional("query"): cv.string,
        vol.Optional("category"): cv.string,
        vol.Optional("limit", default=50): vol.All(cv.positive_int, vol.Range(max=100)),
        vol.Optional("offset", default=0): vol.All(vol.Coerce(int), vol.Range(min=0)),
    }
)

MEMORY_DELETE_SCHEMA = vol.Schema(
    {
        **MEMORY_AGENT_FIELDS,
        vol.Required("memory_ids"): vol.All(
            cv.ensure_list, vol.Length(min=1, max=50), [cv.string]
        ),
    }
)

MEMORY_CLEAR_SCHEMA = vol.Schema(
    {
        **MEMORY_AGENT_FIELDS,
        vol.Optional("category"): cv.string,
        vol.Required("confirm", default=False): cv.boolean,
    }
)

FUNCTION_TOOL_STATE_SCHEMA = vol.Schema(
    {
        **MEMORY_AGENT_FIELDS,
        vol.Required("functions"): vol.All(
            cv.ensure_list, vol.Length(min=1, max=100), [cv.string]
        ),
    }
)

_LOGGER = logging.getLogger(__package__)


def resolve_memory_agent(
    hass: HomeAssistant, entry_id: str, agent_reference: str
) -> tuple[str, str]:
    """Resolve a readable conversation entity or legacy subentry ID."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise HomeAssistantError("Config entry not found")

    registry_entry = er.async_get(hass).async_get(agent_reference)
    if registry_entry is not None:
        if registry_entry.config_entry_id != entry_id:
            raise HomeAssistantError(
                "Conversation agent does not belong to the selected config entry"
            )
        subentry_id = registry_entry.config_subentry_id
        if subentry_id is None:
            raise HomeAssistantError("Conversation agent is not linked to a subentry")
    else:
        # Preserve compatibility with existing actions that pass the raw subentry ID.
        subentry_id = agent_reference

    subentry = entry.subentries.get(subentry_id)
    if subentry is None or subentry.subentry_type != "conversation":
        raise HomeAssistantError("Conversation agent not found")
    return entry_id, subentry_id


async def async_set_function_tools_enabled(
    hass: HomeAssistant,
    entry_id: str,
    agent_reference: str,
    function_names: list[str],
    enabled: bool,
) -> None:
    """Persist the authoritative enabled state for selected Function Tools."""
    _, subentry_id = resolve_memory_agent(hass, entry_id, agent_reference)
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        raise HomeAssistantError("Config entry not found")
    subentry = entry.subentries[subentry_id]
    configured = validate_function_tools(subentry.data.get(CONF_FUNCTION_TOOLS, []))
    requested = list(dict.fromkeys(function_names))
    configured_names = {tool["spec"]["name"] for tool in configured}
    missing = [name for name in requested if name not in configured_names]
    if missing:
        raise HomeAssistantError(
            "Function Tool not found: " + ", ".join(sorted(missing))
        )
    for tool in configured:
        if tool["spec"]["name"] in requested:
            tool["enabled"] = enabled
    normalized = merge_agent_config(
        dict(subentry.data), {CONF_FUNCTION_TOOLS: configured}
    )
    hass.config_entries.async_update_subentry(entry, subentry, data=normalized)


async def _async_require_service_admin(hass: HomeAssistant, call: ServiceCall) -> None:
    """Allow system automations and require admin rights for user-originated calls."""
    user_id = getattr(getattr(call, "context", None), "user_id", None)
    if user_id is None:
        return
    user = await hass.auth.async_get_user(user_id)
    if user is None or not user.is_admin:
        raise HomeAssistantError("Administrator permission is required")


async def async_setup_services(hass: HomeAssistant, config: ConfigType) -> None:
    """Set up services for the extended openai conversation component."""

    async def query_image(call: ServiceCall) -> ServiceResponse:
        """Query an image."""
        try:
            model = call.data["model"]
            api_mode = get_api_mode(call.data[CONF_API_MODE], model)
            image_params = [
                to_image_param(hass, image) for image in call.data["images"]
            ]

            entry = hass.config_entries.async_get_entry(call.data["config_entry"])
            if entry is None:
                raise HomeAssistantError("Config entry not found")

            client = entry.runtime_data

            if api_mode == API_MODE_RESPONSES:
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": call.data["prompt"]},
                            *[
                                {
                                    "type": "input_image",
                                    "image_url": image["url"],
                                    "detail": "auto",
                                }
                                for image in image_params
                            ],
                        ],
                    }
                ]
                _LOGGER.info("Prompt for %s using %s: %s", model, api_mode, messages)
                response = await client.responses.create(
                    model=model,
                    input=messages,
                    max_output_tokens=call.data["max_tokens"],
                    store=False,
                )
            else:
                images = [
                    {"type": "image_url", "image_url": image} for image in image_params
                ]
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": call.data["prompt"]},
                            *images,
                        ],
                    }
                ]
                _LOGGER.info("Prompt for %s using %s: %s", model, api_mode, messages)
                token_param = get_token_param_for_model(model)
                token_kwargs = {token_param: call.data["max_tokens"]}
                response = await client.chat.completions.create(
                    model=model,
                    messages=messages,
                    **token_kwargs,
                )
            response_dict: dict = response.model_dump()
            _LOGGER.info("Response %s", response_dict)
        except OpenAIError as err:
            raise HomeAssistantError(f"Error generating image: {err}") from err

        return response_dict

    async def change_config(call: ServiceCall) -> None:
        """Change configuration."""
        entry_id = call.data["config_entry"]
        entry = hass.config_entries.async_get_entry(entry_id)
        if not entry or entry.domain != DOMAIN:
            raise HomeAssistantError(f"Config entry {entry_id} not found")

        updates = {}
        for key in (
            CONF_API_KEY,
            CONF_BASE_URL,
            CONF_API_VERSION,
            CONF_ORGANIZATION,
            CONF_SKIP_AUTHENTICATION,
            CONF_API_PROVIDER,
        ):
            if key in call.data:
                updates[key] = call.data[key]

        if not updates:
            return

        new_data = entry.data.copy()
        new_data.update(updates)

        _LOGGER.debug("Updating config entry %s with %s", entry_id, new_data)

        base_url = new_data.get(CONF_BASE_URL)
        if base_url == DEFAULT_CONF_BASE_URL:
            # Do not set base_url if using OpenAI for case of OpenAI's base_url change
            base_url = None
            new_data.pop(CONF_BASE_URL)

        if new_data.get(CONF_API_PROVIDER) == "azure" and not base_url:
            raise HomeAssistantError("Azure OpenAI requires a custom base URL.")

        await get_authenticated_client(
            hass=hass,
            api_key=new_data[CONF_API_KEY],
            base_url=new_data.get(CONF_BASE_URL),
            api_version=new_data.get(CONF_API_VERSION),
            organization=new_data.get(CONF_ORGANIZATION),
            skip_authentication=new_data.get(CONF_SKIP_AUTHENTICATION, False),
            api_provider=new_data.get(CONF_API_PROVIDER),
        )

        hass.config_entries.async_update_entry(entry, data=new_data)

    async def reload_skills(call: ServiceCall) -> ServiceResponse:
        """Reload skills from the user skill directory."""
        from .skills import SkillManager

        skill_manager = await SkillManager.async_get_instance(hass)
        await skill_manager.async_load_skills()

        return {
            "loaded_skills": len(skill_manager.get_all_skills()),
        }

    async def download_skill(call: ServiceCall) -> ServiceResponse:
        """Download a skill from the GitHub repository."""
        from .skills import SkillManager

        skill_name = call.data["skill_name"]
        session = async_get_clientsession(hass)

        # Fetch skill directory contents from GitHub API
        api_url = (
            f"https://api.github.com/repos/{GITHUB_REPO_OWNER}/{GITHUB_REPO_NAME}"
            f"/contents/{GITHUB_SKILLS_PATH}/{skill_name}"
            f"?ref={GITHUB_SKILLS_BRANCH}"
        )

        downloaded_files: list[str] = []

        async def _download_directory(url: str, local_dir: Path) -> None:
            """Recursively download a directory from GitHub."""
            async with session.get(url) as resp:
                if resp.status == 404:
                    raise HomeAssistantError(
                        f"Skill `{skill_name}` not found in repository"
                    )
                if resp.status != 200:
                    raise HomeAssistantError(
                        f"Failed to fetch skill from GitHub (HTTP {resp.status})"
                    )
                items = await resp.json()

            if not isinstance(items, list):
                raise HomeAssistantError(
                    f"Unexpected response from GitHub for skill `{skill_name}`"
                )

            for item in items:
                item_path = local_dir / item["name"]
                if item["type"] == "file":
                    # Download file content
                    async with session.get(item["download_url"]) as file_resp:
                        if file_resp.status != 200:
                            raise HomeAssistantError(
                                f"Failed to download `{item['path']}`"
                            )
                        content = await file_resp.read()

                    await hass.async_add_executor_job(
                        _write_file_sync, item_path, content
                    )
                    downloaded_files.append(str(item["path"]))
                elif item["type"] == "dir":
                    # Recurse into subdirectory
                    await _download_directory(item["url"], item_path)

        def _write_file_sync(file_path: Path, content: bytes) -> None:
            """Write file content to disk (run in executor)."""
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_bytes(content)

        # Determine target directory
        skill_manager = await SkillManager.async_get_instance(hass)
        target_dir = skill_manager.user_skills_dir / skill_name

        _LOGGER.info("Downloading skill `%s` to %s", skill_name, target_dir)

        try:
            await _download_directory(api_url, target_dir)
        except HomeAssistantError:
            raise
        except Exception as err:
            raise HomeAssistantError(
                f"Failed to download skill `{skill_name}`: {err}"
            ) from err

        # Reload skills after download
        await skill_manager.async_load_skills()

        _LOGGER.info(
            "Successfully downloaded skill `%s` (%d files)",
            skill_name,
            len(downloaded_files),
        )

        return {
            "skill_name": skill_name,
            "downloaded_files": downloaded_files,
            "target_directory": str(target_dir),
        }

    async def _memory_for_call(call: ServiceCall):
        entry_id, subentry_id = resolve_memory_agent(
            hass, call.data["config_entry"], call.data["agent_id"]
        )
        return await async_get_memory(hass, entry_id, subentry_id)

    async def memory_list(call: ServiceCall) -> ServiceResponse:
        """Inspect memories in the caller's own user scope."""
        memory = await _memory_for_call(call)
        user_id = memory_user_id(call)
        if query := call.data.get("query"):
            records = await memory.async_search(
                user_id,
                query,
                call.data.get("category"),
                call.data["limit"],
            )
        else:
            records = await memory.async_list(
                user_id,
                call.data.get("category"),
                call.data["limit"],
                call.data["offset"],
            )
        return cast(
            ServiceResponse,
            {"memories": [memory_as_dict(record) for record in records]},
        )

    async def memory_delete(call: ServiceCall) -> ServiceResponse:
        """Delete selected memories in the caller's own user scope."""
        memory = await _memory_for_call(call)
        deleted = await memory.async_delete(
            memory_user_id(call), call.data["memory_ids"]
        )
        return {"deleted": deleted}

    async def memory_clear(call: ServiceCall) -> ServiceResponse:
        """Clear memories only after explicit confirmation."""
        if call.data["confirm"] is not True:
            raise HomeAssistantError("Set confirm to true to clear memories")
        memory = await _memory_for_call(call)
        deleted = await memory.async_clear(
            memory_user_id(call), call.data.get("category")
        )
        return {"deleted": deleted}

    async def enable_function_tools(call: ServiceCall) -> None:
        """Enable one or more configured Function Tools."""
        await _async_require_service_admin(hass, call)
        await async_set_function_tools_enabled(
            hass,
            call.data["config_entry"],
            call.data["agent_id"],
            call.data["functions"],
            True,
        )

    async def disable_function_tools(call: ServiceCall) -> None:
        """Disable one or more configured Function Tools."""
        await _async_require_service_admin(hass, call)
        await async_set_function_tools_enabled(
            hass,
            call.data["config_entry"],
            call.data["agent_id"],
            call.data["functions"],
            False,
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_QUERY_IMAGE,
        query_image,
        schema=QUERY_IMAGE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        "change_config",
        change_config,
        schema=CHANGE_CONFIG_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_RELOAD_SKILLS,
        reload_skills,
        schema=RELOAD_SKILLS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_DOWNLOAD_SKILL,
        download_skill,
        schema=DOWNLOAD_SKILL_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_MEMORY_LIST,
        memory_list,
        schema=MEMORY_LIST_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_MEMORY_DELETE,
        memory_delete,
        schema=MEMORY_DELETE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_MEMORY_CLEAR,
        memory_clear,
        schema=MEMORY_CLEAR_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_ENABLE_FUNCTION_TOOLS,
        enable_function_tools,
        schema=FUNCTION_TOOL_STATE_SCHEMA,
    )

    hass.services.async_register(
        DOMAIN,
        SERVICE_DISABLE_FUNCTION_TOOLS,
        disable_function_tools,
        schema=FUNCTION_TOOL_STATE_SCHEMA,
    )


def to_image_param(hass: HomeAssistant, image: dict) -> dict:
    """Convert url to base64 encoded image if local."""
    url = image["url"]

    if urlparse(url).scheme in cv.EXTERNAL_URL_PROTOCOL_SCHEMA_LIST:
        return image

    if not hass.config.is_allowed_path(url):
        raise HomeAssistantError(
            f"Cannot read `{url}`, no access to path; "
            "`allowlist_external_dirs` may need to be adjusted in "
            "`configuration.yaml`"
        )
    if not Path(url).exists():
        raise HomeAssistantError(f"`{url}` does not exist")
    mime_type, _ = mimetypes.guess_type(url)
    if mime_type is None or not mime_type.startswith("image"):
        raise HomeAssistantError(f"`{url}` is not an image")

    image["url"] = f"data:{mime_type};base64,{encode_image(url)}"
    return image


def encode_image(image_path: str) -> str:
    """Convert to base64 encoded image."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")
