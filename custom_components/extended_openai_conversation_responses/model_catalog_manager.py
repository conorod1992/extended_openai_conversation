"""Fail-safe, installation-wide catalogue updates in Home Assistant storage."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import timedelta
import logging
import math
import time
from types import MappingProxyType
from typing import Any

from aiohttp import ClientError
import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
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
from .operation_settlement import async_wait_owned_cleanup
from .operational_errors import log_handled_failure
from .provider_credentials import require_provider_restore_complete
from .request_rules import (
    _MANAGERS as _RULE_MANAGERS,
    SLOT_REFERENCE,
    RequestRules,
    async_get_request_rules,
)
from .strict_store import PropagatingWriteStore

CATALOG_URL = (
    "https://raw.githubusercontent.com/conorod1992/extended_openai_conversation/"
    "develop/custom_components/extended_openai_conversation_responses/model_catalog.json"
)
UPDATE_INTERVAL = 24 * 60 * 60
DATA_MANAGER = f"{DOMAIN}.model_catalog"
_DATA_REFRESH = f"{DOMAIN}.model_catalog_refresh"
_DATA_PUBLICATION = f"{DOMAIN}.model_catalog_publication"
WS_CATALOG = f"{DOMAIN}/model_catalog"
_LOGGER = logging.getLogger(__name__)


def require_current_configuration_catalogue(
    hass: HomeAssistant, generation: int
) -> None:
    """Check model-dependent validation before a synchronous configuration write."""
    if _DATA_PUBLICATION in hass.data:
        raise HomeAssistantError(
            "Model data is being updated. Wait for it to finish and retry saving."
        )
    if generation != catalog_generation():
        raise HomeAssistantError(
            "Model data changed during validation. Review the settings and retry saving."
        )


@dataclass(frozen=True, slots=True)
class _SavedRules:
    owner: RequestRules
    revision: str | None
    rules: tuple[Mapping[str, Any], ...]
    registered: bool


@dataclass(frozen=True, slots=True)
class _SavedAssistant:
    subentry: ConfigSubentry
    source: Mapping[str, Any]
    options: Mapping[str, Any]
    rules: _SavedRules | None = None


@dataclass(frozen=True, slots=True)
class _SavedConnection:
    entry: ConfigEntry
    source: Mapping[str, Any] | None
    entry_data: Mapping[str, Any]
    client: Any
    provider: str
    assistants: tuple[_SavedAssistant, ...]


type _SavedInventory = tuple[_SavedConnection, ...]


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
                if type(checked) not in (float, int) or not 0 <= checked < math.inf:
                    raise ValueError("Invalid catalogue check time")
                if checked > time.time():
                    # A wall-clock rollback does not invalidate downloaded data.
                    # Mark it unchecked; the next refresh persists the corrected time.
                    checked = 0.0
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

    def _capture_saved_connections(self) -> _SavedInventory:
        connections = []
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            assistants = tuple(
                _SavedAssistant(
                    subentry,
                    subentry.data,
                    MappingProxyType(deepcopy(dict(subentry.data))),
                )
                for subentry in entry.subentries.values()
                if subentry.subentry_type in {"conversation", "ai_task_data"}
            )
            if assistants:
                source = getattr(entry, "data", None)
                entry_data = MappingProxyType(deepcopy(dict(source or {})))
                connections.append(
                    _SavedConnection(
                        entry,
                        source,
                        entry_data,
                        getattr(entry, "runtime_data", None),
                        effective_api_provider(
                            entry_data.get("api_provider"), entry_data.get("base_url")
                        ),
                        assistants,
                    )
                )
        return tuple(connections)

    async def _capture_saved_rules(self, inventory: _SavedInventory) -> _SavedInventory:
        connections = []
        for connection in inventory:
            assistants = []
            for assistant in connection.assistants:
                if assistant.subentry.subentry_type == "conversation":
                    key = (connection.entry.entry_id, assistant.subentry.subentry_id)
                    owner = await async_get_request_rules(self.hass, *key)
                    # A save can start as discovery gives up its maintenance lease.
                    # Use the Rule lock only to take a settled snapshot, with no
                    # lease or other manager lock acquired while holding it.
                    if isinstance(owner, RequestRules):
                        async with owner._lock:
                            if not owner.initialized:
                                raise ValueError(
                                    "Request Rules changed during discovery"
                                )
                            snapshot = owner.snapshot()
                    else:
                        snapshot = owner.snapshot()
                    assistant = replace(
                        assistant,
                        rules=_SavedRules(
                            owner,
                            snapshot.get("revision"),
                            tuple(
                                MappingProxyType(deepcopy(rule))
                                for rule in snapshot["rules"]
                            ),
                            self.hass.data.get(_RULE_MANAGERS, {}).get(key) is owner,
                        ),
                    )
                assistants.append(assistant)
            connections.append(replace(connection, assistants=tuple(assistants)))
        return tuple(connections)

    def _inventory_is_current(self, inventory: _SavedInventory) -> bool:
        entries = {
            (entry.entry_id, subentry.subentry_id): (entry, subentry)
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            for subentry in entry.subentries.values()
            if subentry.subentry_type in {"conversation", "ai_task_data"}
        }
        managers = self.hass.data.get(_RULE_MANAGERS, {})
        for connection in inventory:
            entry = connection.entry
            source = getattr(entry, "data", None)
            if (
                source is not connection.source
                or (source or {}) != connection.entry_data
                or getattr(entry, "runtime_data", None) is not connection.client
            ):
                return False
            for assistant in connection.assistants:
                subentry = assistant.subentry
                key = (entry.entry_id, subentry.subentry_id)
                current_entry, current_subentry = entries.pop(key, (None, None))
                if (
                    current_entry is not entry
                    or current_subentry is not subentry
                    or subentry.data is not assistant.source
                    or subentry.data != assistant.options
                ):
                    return False
                if (rules := assistant.rules) is not None:
                    current_owner = managers.get(key)
                    if (
                        rules.registered or current_owner is not None
                    ) and current_owner is not rules.owner:
                        return False
                    if rules.revision is not None:
                        if (
                            not rules.owner.initialized
                            or rules.owner._lock.locked()
                            or rules.owner.revision() != rules.revision
                        ):
                            return False
                    elif tuple(rules.owner.snapshot()["rules"]) != rules.rules:
                        return False
        return not entries

    def _candidate_preserves_saved_defaults(
        self, candidate: dict[str, Any], inventory: _SavedInventory
    ) -> bool:
        """Reject invalid base routes before any Request Rules storage reads."""
        from .request import (
            build_provider_request_snapshot,
            capability_model_identity,
            validate_runtime_readiness,
        )

        for connection in inventory:
            entry_data = connection.entry_data
            client = connection.client
            provider = connection.provider
            for assistant in connection.assistants:
                options = assistant.options
                model = capability_model_identity(options, entry_data)
                metadata = catalog_model_metadata(candidate, model, provider=provider)
                try:
                    if assistant.subentry.subentry_type == "conversation":
                        validate_runtime_readiness(
                            options,
                            entry_data,
                            model_capabilities=metadata,
                            client=client,
                        )
                    else:
                        build_provider_request_snapshot(
                            options,
                            entry_data,
                            tools_required=False,
                            model_capabilities=metadata,
                            client=client,
                        )
                except Exception:
                    return False
        return True

    async def _candidate_inventory(
        self, candidate: dict[str, Any], *, reset: bool = False
    ) -> _SavedInventory | None:
        inventory = self._capture_saved_connections()
        if reset and self._bundled_reset_invalidates_saved_defaults(inventory):
            return None
        if not self._candidate_preserves_saved_defaults(candidate, inventory):
            return None
        return await self._capture_saved_rules(inventory)

    async def _candidate_preserves_saved_requests(
        self, candidate: dict[str, Any], inventory: _SavedInventory | None = None
    ) -> bool:
        """Check concrete saved agent and routing choices from one inventory."""
        from .request import capability_model_identity, validate_runtime_readiness

        if inventory is None:
            inventory = await self._candidate_inventory(candidate)
            if inventory is None:
                return False

        for connection in inventory:
            entry_data = connection.entry_data
            client = connection.client
            provider = connection.provider
            for assistant in connection.assistants:
                options = assistant.options
                if assistant.rules is None:
                    continue
                saved_rules = assistant.rules.rules
                inherited_efforts = set()
                effort_only_routes: list[tuple[int, str]] = []
                configured_effort = options.get(CONF_REASONING_EFFORT)
                if isinstance(configured_effort, str) and configured_effort:
                    inherited_efforts.add(configured_effort)
                for index, saved_rule in enumerate(saved_rules):
                    saved_action = saved_rule.get("action", {})
                    saved_effort = saved_action.get("reasoning_effort")
                    if (
                        saved_rule.get("enabled", True)
                        and saved_rule.get("action_type") == "model_routing"
                        and not saved_action.get("reset")
                        and isinstance(saved_effort, str)
                        and saved_effort
                        and not SLOT_REFERENCE.search(saved_effort)
                    ):
                        if saved_action.get("scope", "conversation") == "conversation":
                            inherited_efforts.add(saved_effort)
                        if not (
                            saved_action.get("model") or saved_action.get("azure_model")
                        ):
                            effort_only_routes.append((index, saved_effort))
                continued_efforts: set[str] = set()
                for index, rule in enumerate(saved_rules):
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
                    fixed_route = bool(routed_model or routed_binding)
                    if not (fixed_route or routed_effort):
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
                        possible_efforts = set()
                        if not routed_effort:
                            possible_efforts.update(
                                inherited_efforts | continued_efforts
                            )
                        if fixed_route:
                            # A saved conversation route can affect later turns.
                            # A request route can only affect later matching rules.
                            if action.get("scope", "conversation") == "conversation":
                                possible_efforts.update(
                                    effort for _, effort in effort_only_routes
                                )
                            elif rule.get("continue_matching") and not action.get(
                                "continue_to_ai"
                            ):
                                possible_efforts.update(
                                    effort
                                    for later, effort in effort_only_routes
                                    if later > index
                                )
                        if routed_effort and not dynamic_effort and not fixed_route:
                            # Protect the default only if it could use this effort.
                            # Other fixed models are checked with their own identity.
                            possible_efforts.add(routed_effort)
                        if possible_efforts:
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
                                    validate_runtime_readiness(
                                        inherited_options,
                                        entry_data,
                                        model_capabilities=current_metadata,
                                        client=client,
                                    )
                                except HomeAssistantError:
                                    continue  # This combination was already unusable.
                                if (
                                    not routed_metadata["reasoning"]["supported"]
                                    or inherited_effort
                                    not in routed_metadata["reasoning"]["efforts"]
                                ):
                                    return False
                                validate_runtime_readiness(
                                    inherited_options,
                                    entry_data,
                                    model_capabilities=routed_metadata,
                                    client=client,
                                )
                            if not fixed_route:
                                continue
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
                                    validate_runtime_readiness(
                                        {
                                            **effective,
                                            CONF_REASONING_EFFORT: possible_effort,
                                        },
                                        entry_data,
                                        model_capabilities=routed_metadata,
                                        client=client,
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
                        validate_runtime_readiness(
                            effective,
                            entry_data,
                            model_capabilities=routed_metadata,
                            client=client,
                        )
                    except Exception:
                        return False
        return True

    async def async_check(self, *, force: bool = False) -> dict[str, Any]:
        """Check trusted catalogue data without changing the active catalogue."""
        async with self._lock:
            now = time.time()
            if (
                not force
                and self.last_checked > 0
                and 0 <= now - self.last_checked < UPDATE_INTERVAL
            ):
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

    async def _async_publish(
        self,
        catalog: dict[str, Any] | None,
        available: dict[str, Any] | None,
        etag: str | None,
        inventory: _SavedInventory,
    ) -> None:
        """Complete the durable save and live publication under the caller's lock."""

        async def publish() -> None:
            # Task creation can yield between validation and this last check.
            if not self._inventory_is_current(inventory):
                raise ValueError(
                    "Saved configuration changed during catalogue validation"
                )
            for connection in inventory:
                require_provider_restore_complete(self.hass, connection.entry.entry_id)
            self.hass.data[_DATA_PUBLICATION] = True
            try:
                await self._save(
                    catalog,
                    available,
                    etag,
                    self.last_checked,
                    self.incompatible_catalog,
                )
                activate_catalog(catalog)
                self.catalog = catalog
                self.available_catalog = available
                self.etag = etag
                self.last_error = None
            finally:
                del self.hass.data[_DATA_PUBLICATION]
            try:
                sync_all_model_lifecycles(self.hass)
            except Exception as err:
                log_handled_failure(
                    _LOGGER,
                    "Unable to refresh model lifecycle after catalogue publication",
                    err,
                )

        task = asyncio.create_task(publish())
        cancellation = await async_wait_owned_cleanup(task)
        try:
            task.result()
        except Exception as err:
            if cancellation is None:
                raise
            log_handled_failure(
                _LOGGER, "Unable to publish model catalogue during cancellation", err
            )
        if cancellation is not None:
            raise cancellation

    async def async_apply_update(self) -> dict[str, Any]:
        """Activate the most recently checked newer catalogue."""
        async with self._lock:
            candidate = self.available_catalog
            if candidate is None:
                self.last_error = "No model data update is available."
                return self.status()
            try:
                validate_catalog_transition(self.catalog, candidate)
                inventory = await self._candidate_inventory(candidate)
                if (
                    inventory is None
                    or not await self._candidate_preserves_saved_requests(
                        candidate, inventory
                    )
                ):
                    raise ValueError("Catalogue update invalidates saved requests")
                await self._async_publish(candidate, None, self.etag, inventory)
            except Exception as err:
                self.last_error = "Model data update could not be applied; the current catalogue was kept."
                log_handled_failure(_LOGGER, self.last_error + " operation=apply", err)
                return self.status()
            return self.status()

    def _bundled_reset_invalidates_saved_defaults(
        self, inventory: _SavedInventory
    ) -> bool:
        if self.catalog is None:
            return False
        from .request import capability_model_identity

        for connection in inventory:
            for assistant in connection.assistants:
                configured_model = capability_model_identity(
                    assistant.options, connection.entry_data
                )
                configured_effort = assistant.options.get(CONF_REASONING_EFFORT)
                metadata = catalog_model_metadata(
                    None, configured_model, provider=connection.provider
                )
                bundled_config_efforts = metadata["reasoning"]["efforts"]
                # A legacy value omitted by both request builders is inactive.
                # Still protect reasoning that the active catalogue actually uses.
                current_metadata = catalog_model_metadata(
                    self.catalog, configured_model, provider=connection.provider
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
        return False

    async def _bundled_reset_would_invalidate_saved_reasoning(
        self, inventory: _SavedInventory | None = None
    ) -> bool:
        """Check durable Rule choices before narrowing to bundled data."""
        if self.catalog is None:
            return False
        from .request import capability_model_identity

        if inventory is None:
            inventory = self._capture_saved_connections()
            if self._bundled_reset_invalidates_saved_defaults(inventory):
                return True
            inventory = await self._capture_saved_rules(inventory)
        bundled_efforts = set(catalog_reasoning_efforts(None))
        for connection in inventory:
            entry_data = connection.entry_data
            provider = connection.provider
            for assistant in connection.assistants:
                if assistant.rules is None:
                    continue
                options = assistant.options
                for rule in assistant.rules.rules:
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
                        not (model or binding)
                        or (isinstance(binding, str) and SLOT_REFERENCE.search(binding))
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
                            **options,
                            **routed_model_override(
                                options,
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
            try:
                inventory = await self._candidate_inventory(BUNDLED_CATALOG, reset=True)
                if (
                    inventory is None
                    or await self._bundled_reset_would_invalidate_saved_reasoning(
                        inventory
                    )
                    or not await self._candidate_preserves_saved_requests(
                        BUNDLED_CATALOG, inventory
                    )
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
                    and self.catalog["catalog_version"]
                    > BUNDLED_CATALOG["catalog_version"]
                ):
                    available = self.catalog
                if (
                    available is not None
                    and available["catalog_version"]
                    <= BUNDLED_CATALOG["catalog_version"]
                ):
                    available = None
                etag = self.etag if available is not None else None
                await self._async_publish(None, available, etag, inventory)
            except Exception as err:
                self.last_error = (
                    "Model data reset failed; the current catalogue was kept."
                )
                log_handled_failure(_LOGGER, self.last_error + " operation=reset", err)
                return self.status()
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
    async_start_model_catalog_refresh(hass)

    @callback
    def stop(_event: Any) -> None:
        async_stop_model_catalog_refresh(hass)
        activate_catalog(None)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop)


@callback
def async_start_model_catalog_refresh(hass: HomeAssistant) -> None:
    """Resume the existing catalogue's hourly refresh after provider re-addition."""
    manager = hass.data.get(DATA_MANAGER)
    if manager is None or _DATA_REFRESH in hass.data:
        return
    token = object()

    async def check(_now: Any) -> None:
        owner = hass.data.get(_DATA_REFRESH)
        if owner is not None and owner[0] is token:
            await manager.async_check()

    cancel = async_track_time_interval(hass, check, timedelta(hours=1))
    hass.data[_DATA_REFRESH] = (token, cancel)


@callback
def async_stop_model_catalog_refresh(hass: HomeAssistant) -> None:
    """Stop periodic refresh while keeping the manager available for the UI."""
    if (owner := hass.data.pop(_DATA_REFRESH, None)) is not None:
        owner[1]()
