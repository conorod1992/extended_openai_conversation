"""Fail-safe, installation-wide catalogue updates in Home Assistant storage."""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
import time
from typing import Any

from aiohttp import ClientError
import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import (
    CONF_CHAT_MODEL as CONF_CHAT_MODEL,
    CONF_REASONING_EFFORT,
    DEFAULT_CHAT_MODEL as DEFAULT_CHAT_MODEL,
    DOMAIN,
)
from .ha_schema import websocket_command as ha_websocket_command
from .helpers import effective_api_provider
from .model_capabilities import frontend_capabilities
from .model_catalog import (
    BUNDLED_CATALOG,
    MAX_CATALOG_BYTES,
    IncompatibleCatalogError,
    activate_catalog,
    all_reasoning_efforts,
    catalog_generation,
    catalog_model_metadata,
    catalog_picker_models,
    catalog_reasoning_efforts,
    model_metadata,
    parse_catalog,
    validate_catalog,
    validate_catalog_transition,
    validate_or_migrate_catalog,
)
from .model_lifecycle import sync_all_model_lifecycles
from .model_routing import routed_model_override
from .operational_errors import log_handled_failure
from .request_rules import SLOT_REFERENCE, async_get_request_rules
from .strict_store import PropagatingWriteStore

CATALOG_URL = (
    "https://raw.githubusercontent.com/conorod1992/extended_openai_conversation/"
    "develop/custom_components/extended_openai_conversation_responses/model_catalog.json"
)
UPDATE_INTERVAL = 24 * 60 * 60
DATA_MANAGER = f"{DOMAIN}.model_catalog"
WS_CATALOG = f"{DOMAIN}/model_catalog"
_LOGGER = logging.getLogger(__name__)


class _CatalogHTTPError(ValueError):
    """Safe HTTP status from the fixed public catalogue endpoint."""

    def __init__(self, status: int) -> None:
        self.status_code = status
        super().__init__("Catalogue HTTP check failed")


class _TransientCatalogUpdateError(_CatalogHTTPError):
    """Remote catalogue refresh failed for an ordinary transient reason."""


class ModelCatalogManager:
    """Serialize catalogue checks/apply/reset and retain last good data."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: Store[dict[str, Any]] = PropagatingWriteStore(
            hass, 1, f"{DOMAIN}.model_catalog"
        )
        self.catalog: dict[str, Any] | None = None
        self.available_catalog: dict[str, Any] | None = None
        self.etag: str | None = None
        self.last_checked = 0.0
        self.last_error: str | None = None
        self.incompatible_catalog: dict[str, Any] | None = None
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Load/migrate stored data without making startup depend on the network."""
        try:
            saved = await self.store.async_load()
            migrated = False
            if saved:
                candidate = saved.get("catalog")
                if candidate is not None:
                    candidate, migrated = validate_or_migrate_catalog(candidate)
                available = saved.get("available_catalog")
                if available is not None:
                    available = validate_catalog(available)
                checked = saved.get("last_checked", 0)
                etag = saved.get("etag")
                incompatible = saved.get("incompatible_catalog")
                if incompatible is not None and (
                    not isinstance(incompatible, dict)
                    or type(incompatible.get("schema_version")) is not int
                    or type(incompatible.get("catalog_version")) is not int
                    or (
                        incompatible.get("minimum_eoai_version") is not None
                        and not isinstance(incompatible["minimum_eoai_version"], str)
                    )
                ):
                    raise ValueError("Invalid incompatible catalogue status")
                if type(checked) not in (float, int) or not 0 <= checked <= time.time():
                    raise ValueError("Invalid catalogue check time")
                if etag is not None and (
                    not isinstance(etag, str)
                    or len(etag) > 256
                    or "\n" in etag
                    or "\r" in etag
                ):
                    raise ValueError("Invalid catalogue ETag")
                stale_candidate_discarded = False
                if (
                    candidate is not None
                    and candidate["catalog_version"]
                    < BUNDLED_CATALOG["catalog_version"]
                ):
                    candidate = None
                    stale_candidate_discarded = True
                elif candidate is not None:
                    validate_catalog_transition(None, candidate)

                active = candidate or BUNDLED_CATALOG
                if available is not None:
                    if available["catalog_version"] <= active["catalog_version"]:
                        available = None
                    else:
                        validate_catalog_transition(candidate, available)
                if stale_candidate_discarded and available is None:
                    etag = None

                self.catalog = candidate
                self.available_catalog = available
                self.etag = etag
                self.last_checked = checked
                self.incompatible_catalog = incompatible
                if incompatible is not None:
                    # The response ETag belongs to rejected data, never to active data.
                    self.etag = None
                if migrated:
                    _LOGGER.debug(
                        "Migrated stored model capability catalogue to schema v4; "
                        "bundled model data is authoritative"
                    )
                    try:
                        await self._save(
                            candidate, available, etag, checked, incompatible
                        )
                    except Exception as err:
                        log_handled_failure(
                            _LOGGER, "Unable to persist migrated model catalogue", err
                        )
        except Exception as err:
            self.catalog = None
            self.available_catalog = None
            self.etag = None
            self.last_checked = 0.0
            self.incompatible_catalog = None
            self.last_error = (
                "Stored model data could not be loaded; using bundled data."
            )
            log_handled_failure(_LOGGER, self.last_error + " operation=load", err)
        activate_catalog(self.catalog)

    def status(self) -> dict[str, Any]:
        """Return active and remotely available catalogue state."""
        active = self.catalog or BUNDLED_CATALOG
        return {
            "source": "downloaded" if self.catalog is not None else "bundled",
            "catalog_version": active["catalog_version"],
            "catalog_generation": catalog_generation(),
            "schema_version": active["schema_version"],
            "update_available": self.available_catalog is not None,
            "available_catalog_version": (
                self.available_catalog["catalog_version"]
                if self.available_catalog is not None
                else None
            ),
            "last_checked": self.last_checked,
            "last_error": self.last_error,
            "incompatible_catalog": self.incompatible_catalog,
        }

    async def _save(
        self,
        catalog: dict[str, Any] | None,
        available_catalog: dict[str, Any] | None,
        etag: str | None,
        checked: float,
        incompatible_catalog: dict[str, Any] | None = None,
    ) -> None:
        await self.store.async_save(
            {
                "catalog": catalog,
                "available_catalog": available_catalog,
                "etag": etag,
                "last_checked": checked,
                "incompatible_catalog": incompatible_catalog,
            }
        )

    async def _record_failed_check(
        self, checked: float, *, transient: bool, error: Exception
    ) -> None:
        """Retain active/pending data while remembering when a check failed."""
        self.last_checked = checked
        self.last_error = "Model data check failed; the current catalogue was kept."
        log_handled_failure(
            _LOGGER,
            f"{self.last_error} operation=refresh category={'transient' if transient else 'validation_or_internal'}"
            + (
                f" http_status={error.status_code}"
                if isinstance(error, _CatalogHTTPError)
                else ""
            ),
            error,
            level=logging.DEBUG if transient else logging.WARNING,
        )
        try:
            await self._save(
                self.catalog,
                self.available_catalog,
                self.etag,
                checked,
                self.incompatible_catalog,
            )
        except Exception as err:
            log_handled_failure(
                _LOGGER, "Unable to persist model catalogue check time", err
            )

    async def _candidate_preserves_saved_requests(
        self, candidate: dict[str, Any]
    ) -> bool:
        """Check concrete saved agent and routing choices before publication."""
        from .request import (
            build_provider_request_snapshot,
            capability_model_identity,
            conversation_tools_required,
        )

        for entry in self.hass.config_entries.async_entries(DOMAIN):
            entry_data = getattr(entry, "data", {})
            provider = effective_api_provider(
                entry_data.get("api_provider"), entry_data.get("base_url")
            )
            for subentry in entry.subentries.values():
                if subentry.subentry_type not in {"conversation", "ai_task_data"}:
                    continue
                options = subentry.data
                model = capability_model_identity(options, getattr(entry, "data", {}))
                try:
                    build_provider_request_snapshot(
                        options,
                        getattr(entry, "data", {}),
                        tools_required=(
                            conversation_tools_required(options)
                            if subentry.subentry_type == "conversation"
                            else False
                        ),
                        model_capabilities=catalog_model_metadata(
                            candidate, model, provider=provider
                        ),
                    )
                except Exception:
                    return False
                if subentry.subentry_type != "conversation":
                    continue
                rules = await async_get_request_rules(
                    self.hass, entry.entry_id, subentry.subentry_id
                )
                saved_rules = rules.snapshot()["rules"]
                inherited_efforts = set()
                configured_effort = options.get(CONF_REASONING_EFFORT)
                if isinstance(configured_effort, str) and configured_effort:
                    inherited_efforts.add(configured_effort)
                for saved_rule in saved_rules:
                    saved_action = saved_rule.get("action", {})
                    saved_effort = saved_action.get("reasoning_effort")
                    if (
                        saved_rule.get("enabled", True)
                        and saved_rule.get("action_type") == "model_routing"
                        and not saved_action.get("reset")
                        and saved_action.get("scope", "conversation") == "conversation"
                        and isinstance(saved_effort, str)
                        and saved_effort
                        and not SLOT_REFERENCE.search(saved_effort)
                    ):
                        inherited_efforts.add(saved_effort)
                continued_efforts: set[str] = set()
                for rule in saved_rules:
                    if (
                        not rule.get("enabled", True)
                        or rule.get("action_type") != "model_routing"
                    ):
                        continue
                    action = rule.get("action", {})
                    if action.get("reset"):
                        continue
                    routed_model = action.get("model")
                    routed_effort = action.get("reasoning_effort")
                    routed_binding = action.get("azure_model")
                    if (
                        action.get("scope") == "request"
                        and rule.get("continue_matching")
                        and not action.get("continue_to_ai")
                        and isinstance(routed_effort, str)
                        and routed_effort
                        and not SLOT_REFERENCE.search(routed_effort)
                    ):
                        # Only later rules in this ordered snapshot can inherit
                        # a request-only setting through continued matching.
                        continued_efforts.add(routed_effort)
                    if routed_binding and SLOT_REFERENCE.search(str(routed_binding)):
                        continue
                    if (
                        routed_model
                        and SLOT_REFERENCE.search(str(routed_model))
                        and not (provider == "azure" and routed_binding)
                    ):
                        continue
                    dynamic_effort = bool(
                        routed_effort and SLOT_REFERENCE.search(str(routed_effort))
                    )
                    if not routed_model and not routed_effort:
                        continue
                    try:
                        effective = {
                            **options,
                            **routed_model_override(
                                options,
                                routed_model,
                                routed_binding,
                                entry_data,
                                model_is_known=(
                                    catalog_model_metadata(
                                        candidate, routed_model, provider=provider
                                    )["status"]
                                    != "unknown"
                                    if routed_model
                                    else None
                                ),
                            ),
                        }
                        if routed_effort:
                            effective[CONF_REASONING_EFFORT] = routed_effort
                        routed_metadata = catalog_model_metadata(
                            candidate,
                            capability_model_identity(effective, entry_data),
                            provider=provider,
                        )
                        possible_efforts = inherited_efforts | continued_efforts
                        if not routed_effort and possible_efforts:
                            # A model-only rule can inherit explicit reasoning
                            # from the agent or a previous conversation route.
                            current_metadata = catalog_model_metadata(
                                self.catalog,
                                capability_model_identity(effective, entry_data),
                                provider=provider,
                            )
                            for inherited_effort in possible_efforts:
                                if (
                                    not current_metadata["reasoning"]["supported"]
                                    or inherited_effort
                                    not in current_metadata["reasoning"]["efforts"]
                                ):
                                    continue
                                inherited_options = {
                                    **effective,
                                    CONF_REASONING_EFFORT: inherited_effort,
                                }
                                try:
                                    build_provider_request_snapshot(
                                        inherited_options,
                                        entry_data,
                                        tools_required=conversation_tools_required(
                                            effective
                                        ),
                                        model_capabilities=current_metadata,
                                    )
                                except HomeAssistantError:
                                    continue  # This combination was already unusable.
                                if (
                                    not routed_metadata["reasoning"]["supported"]
                                    or inherited_effort
                                    not in routed_metadata["reasoning"]["efforts"]
                                ):
                                    return False
                                build_provider_request_snapshot(
                                    inherited_options,
                                    entry_data,
                                    tools_required=conversation_tools_required(
                                        effective
                                    ),
                                    model_capabilities=routed_metadata,
                                )
                        if dynamic_effort:
                            # Defer the captured value, not the fixed model's
                            # recognition or previously available effort support.
                            current_metadata = catalog_model_metadata(
                                self.catalog,
                                capability_model_identity(effective, entry_data),
                                provider=provider,
                            )
                            if (
                                current_metadata["reasoning"]["supported"]
                                and not routed_metadata["reasoning"]["supported"]
                            ):
                                return False
                            # A captured effort must not hide fixed API, tool,
                            # tier or output requirements. Try the finite effort
                            # set rather than assuming the recommendation: a
                            # conditional capability can require another value.
                            for possible_effort in routed_metadata["reasoning"][
                                "efforts"
                            ] or [None]:
                                try:
                                    build_provider_request_snapshot(
                                        {
                                            **effective,
                                            CONF_REASONING_EFFORT: possible_effort,
                                        },
                                        entry_data,
                                        tools_required=conversation_tools_required(
                                            effective
                                        ),
                                        model_capabilities=routed_metadata,
                                    )
                                except HomeAssistantError:
                                    continue
                                break
                            else:
                                return False
                            continue
                        # Explicit Rule efforts are validated at execution; the
                        # request builder may omit stale agent-only preferences.
                        if routed_effort and (
                            not routed_metadata["reasoning"]["supported"]
                            or routed_effort
                            not in routed_metadata["reasoning"]["efforts"]
                        ):
                            return False
                        build_provider_request_snapshot(
                            effective,
                            getattr(entry, "data", {}),
                            tools_required=conversation_tools_required(effective),
                            model_capabilities=routed_metadata,
                        )
                    except Exception:
                        return False
        return True

    async def async_check(self, *, force: bool = False) -> dict[str, Any]:
        """Check trusted catalogue data without changing the active catalogue."""
        async with self._lock:
            now = time.time()
            if not force and now - self.last_checked < UPDATE_INTERVAL:
                return self.status()
            try:
                headers = {"If-None-Match": self.etag} if self.etag else {}
                async with asyncio.timeout(15):
                    async with async_get_clientsession(self.hass).get(
                        CATALOG_URL,
                        headers=headers,
                        allow_redirects=False,
                    ) as response:
                        if response.status == 304:
                            if not self.etag:
                                raise ValueError("Unsolicited not-modified response")
                            await self._save(
                                self.catalog,
                                self.available_catalog,
                                self.etag,
                                now,
                                self.incompatible_catalog,
                            )
                            self.last_checked = now
                            self.last_error = None
                            return self.status()
                        if response.status == 429 or response.status >= 500:
                            raise _TransientCatalogUpdateError(response.status)
                        if response.status != 200:
                            raise _CatalogHTTPError(response.status)
                        raw = bytearray()
                        async for chunk in response.content.iter_chunked(16384):
                            raw.extend(chunk)
                            if len(raw) > MAX_CATALOG_BYTES:
                                raise ValueError("Catalogue download too large")
                        candidate = parse_catalog(bytes(raw))
                        etag = response.headers.get("ETag")
                        if etag and (len(etag) > 256 or "\n" in etag or "\r" in etag):
                            raise ValueError("Invalid catalogue ETag")

                active = self.catalog or BUNDLED_CATALOG
                current_version = active["catalog_version"]
                if candidate["catalog_version"] < current_version:
                    raise ValueError("Catalogue version is older than current data")
                if candidate["catalog_version"] == current_version:
                    if candidate != active:
                        raise ValueError(
                            "Changed catalogue must increment catalog_version"
                        )
                    available = None
                else:
                    validate_catalog_transition(self.catalog, candidate)
                    if not await self._candidate_preserves_saved_requests(candidate):
                        raise ValueError("Catalogue update invalidates saved requests")
                    available = candidate

                await self._save(self.catalog, available, etag, now)
                self.available_catalog = available
                self.etag = etag
                self.last_checked = now
                self.last_error = None
                self.incompatible_catalog = None
            except IncompatibleCatalogError as exc:
                incompatible = {
                    "schema_version": exc.schema_version,
                    "catalog_version": exc.catalog_version,
                    "minimum_eoai_version": exc.minimum_eoai_version,
                }
                try:
                    await self._save(
                        self.catalog, self.available_catalog, None, now, incompatible
                    )
                except Exception as err:
                    log_handled_failure(
                        _LOGGER, "Unable to persist incompatible catalogue status", err
                    )
                self.etag = None
                self.last_checked = now
                self.last_error = None
                self.incompatible_catalog = incompatible
            except (ClientError, TimeoutError, _TransientCatalogUpdateError) as err:
                await self._record_failed_check(now, transient=True, error=err)
            except Exception as err:
                await self._record_failed_check(now, transient=False, error=err)
            return self.status()

    async def async_update(self, *, force: bool = False) -> dict[str, Any]:
        """Compatibility alias for the former update action; now check-only."""
        return await self.async_check(force=force)

    async def async_apply_update(self) -> dict[str, Any]:
        """Activate the most recently checked newer catalogue."""
        async with self._lock:
            candidate = self.available_catalog
            if candidate is None:
                self.last_error = "No model data update is available."
                return self.status()
            try:
                validate_catalog_transition(self.catalog, candidate)
                if not await self._candidate_preserves_saved_requests(candidate):
                    raise ValueError("Catalogue update invalidates saved requests")
                await self._save(
                    candidate,
                    None,
                    self.etag,
                    self.last_checked,
                    self.incompatible_catalog,
                )
            except Exception as err:
                self.last_error = "Model data update could not be applied; the current catalogue was kept."
                log_handled_failure(_LOGGER, self.last_error + " operation=apply", err)
                return self.status()
            activate_catalog(candidate)
            sync_all_model_lifecycles(self.hass)
            self.catalog = candidate
            self.available_catalog = None
            self.last_error = None
            return self.status()

    async def _bundled_reset_would_invalidate_saved_reasoning(self) -> bool:
        """Check durable agent/rule choices before narrowing to bundled data."""
        if self.catalog is None:
            return False
        from .request import capability_model_identity

        bundled_efforts = set(catalog_reasoning_efforts(None))
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            entry_data = getattr(entry, "data", {})
            provider = effective_api_provider(
                entry_data.get("api_provider"), entry_data.get("base_url")
            )
            for subentry in entry.subentries.values():
                if subentry.subentry_type not in {"conversation", "ai_task_data"}:
                    continue

                configured_model = capability_model_identity(
                    subentry.data, getattr(entry, "data", {})
                )
                configured_effort = subentry.data.get(CONF_REASONING_EFFORT)
                metadata = catalog_model_metadata(
                    None, configured_model, provider=provider
                )
                bundled_config_efforts = metadata["reasoning"]["efforts"]
                # A legacy value omitted by both request builders is inactive.
                # Still protect reasoning that the active catalogue actually uses.
                current_metadata = catalog_model_metadata(
                    self.catalog, configured_model, provider=provider
                )
                if (
                    isinstance(configured_effort, str)
                    and configured_effort
                    and (
                        metadata["reasoning"]["supported"]
                        or current_metadata["reasoning"]["supported"]
                    )
                    and (
                        not metadata["reasoning"]["supported"]
                        or configured_effort not in bundled_config_efforts
                    )
                ):
                    return True

                if subentry.subentry_type != "conversation":
                    continue
                rules = await async_get_request_rules(
                    self.hass, entry.entry_id, subentry.subentry_id
                )
                for rule in rules.snapshot()["rules"]:
                    if (
                        not rule.get("enabled", True)
                        or rule.get("action_type") != "model_routing"
                    ):
                        continue
                    action = rule.get("action", {})
                    if action.get("reset"):
                        continue
                    effort = action.get("reasoning_effort")
                    if (
                        not isinstance(effort, str)
                        or not effort
                        or SLOT_REFERENCE.search(effort)
                    ):
                        continue
                    model = action.get("model")
                    binding = action.get("azure_model")
                    if (
                        isinstance(binding, str) and SLOT_REFERENCE.search(binding)
                    ) or (
                        isinstance(model, str)
                        and SLOT_REFERENCE.search(model)
                        and not (provider == "azure" and binding)
                    ):
                        if effort not in bundled_efforts:
                            return True
                        continue
                    try:
                        effective = {
                            **subentry.data,
                            **routed_model_override(
                                subentry.data,
                                model,
                                binding,
                                entry_data,
                                model_is_known=(
                                    catalog_model_metadata(
                                        None, model, provider=provider
                                    )["status"]
                                    != "unknown"
                                    if model
                                    else None
                                ),
                            ),
                            CONF_REASONING_EFFORT: effort,
                        }
                    except HomeAssistantError:
                        return True
                    metadata = catalog_model_metadata(
                        None,
                        capability_model_identity(effective, entry_data),
                        provider=provider,
                    )
                    if (
                        not metadata["reasoning"]["supported"]
                        or effort not in metadata["reasoning"]["efforts"]
                    ):
                        return True
        return False

    async def async_reset(self) -> dict[str, Any]:
        """Return to bundled data until a checked update is explicitly applied."""
        async with self._lock:
            if (
                await self._bundled_reset_would_invalidate_saved_reasoning()
                or not await self._candidate_preserves_saved_requests(BUNDLED_CATALOG)
            ):
                self.last_error = (
                    "Model data reset was blocked because saved configuration or "
                    "Request Rules are incompatible with bundled data."
                )
                return self.status()

            available = self.available_catalog
            if (
                available is None
                and self.catalog is not None
                and self.catalog["catalog_version"] > BUNDLED_CATALOG["catalog_version"]
            ):
                available = self.catalog
            if (
                available is not None
                and available["catalog_version"] <= BUNDLED_CATALOG["catalog_version"]
            ):
                available = None
            etag = self.etag if available is not None else None
            try:
                await self._save(
                    None, available, etag, self.last_checked, self.incompatible_catalog
                )
            except Exception:
                self.last_error = (
                    "Model data reset failed; the current catalogue was kept."
                )
                return self.status()
            activate_catalog(None)
            sync_all_model_lifecycles(self.hass)
            self.catalog = None
            self.available_catalog = available
            self.etag = etag
            self.last_error = None
            return self.status()


@ha_websocket_command(
    {
        vol.Required("type"): WS_CATALOG,
        vol.Optional("action", default="lookup"): vol.In(
            ("status", "lookup", "check", "update", "apply", "reset")
        ),
        vol.Optional("model", default=""): vol.All(str, vol.Length(max=128)),
        vol.Optional("provider"): vol.In(["openai", "azure"]),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_catalog(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Expose the same capability data used by request validation."""
    manager: ModelCatalogManager = hass.data[DATA_MANAGER]
    if msg["action"] == "status":
        connection.send_result(msg["id"], manager.status())
        return
    if msg["action"] in {"check", "update"}:
        status = await manager.async_check(force=True)
        if status["last_error"]:
            connection.send_error(
                msg["id"], "model_catalog_check_failed", status["last_error"]
            )
            return
    elif msg["action"] == "apply":
        status = await manager.async_apply_update()
        if status["last_error"]:
            connection.send_error(
                msg["id"], "model_catalog_apply_failed", status["last_error"]
            )
            return
    elif msg["action"] == "reset":
        status = await manager.async_reset()
        if status["last_error"]:
            connection.send_error(
                msg["id"], "model_catalog_reset_failed", status["last_error"]
            )
            return

    metadata = model_metadata(msg["model"], provider=msg.get("provider"))
    capabilities = frontend_capabilities(msg["model"], metadata=metadata)
    connection.send_result(
        msg["id"],
        {
            **manager.status(),
            "model_capabilities": capabilities,
            "model_metadata": metadata,
            "catalog_models": catalog_picker_models(manager.catalog, msg["model"]),
            "reasoning_effort_options": metadata["reasoning"]["efforts"]
            if msg["model"]
            else all_reasoning_efforts(),
        },
    )


async def async_setup_model_catalog(hass: HomeAssistant) -> None:
    """One manager and timer per HA installation, shared by all agent entries."""
    if DATA_MANAGER in hass.data:
        return
    manager = ModelCatalogManager(hass)
    hass.data[DATA_MANAGER] = manager
    await manager.async_load()
    websocket_api.async_register_command(hass, websocket_catalog)

    async def check(_now: Any) -> None:
        await manager.async_check()

    cancel = async_track_time_interval(hass, check, timedelta(hours=1))

    @callback
    def stop(_event: Any) -> None:
        cancel()
        activate_catalog(None)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop)
