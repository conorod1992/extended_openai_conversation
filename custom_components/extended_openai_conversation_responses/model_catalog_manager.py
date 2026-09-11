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
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import CONF_CHAT_MODEL, CONF_REASONING_EFFORT, DEFAULT_CHAT_MODEL, DOMAIN
from .model_catalog import (
    BUNDLED_CATALOG,
    MAX_CATALOG_BYTES,
    activate_catalog,
    all_reasoning_efforts,
    catalog_model_metadata,
    catalog_reasoning_efforts,
    model_metadata,
    parse_catalog,
    validate_catalog,
    validate_catalog_transition,
)
from .request_rules import SLOT_REFERENCE, async_get_request_rules

CATALOG_URL = (
    "https://raw.githubusercontent.com/conorod1992/extended_openai_conversation/"
    "develop/custom_components/extended_openai_conversation_responses/model_catalog.json"
)
UPDATE_INTERVAL = 24 * 60 * 60
DATA_MANAGER = f"{DOMAIN}.model_catalog"
WS_CATALOG = f"{DOMAIN}/model_catalog"
_LOGGER = logging.getLogger(__name__)


class _TransientCatalogUpdateError(Exception):
    """Remote catalogue refresh failed for an ordinary transient reason."""


class ModelCatalogManager:
    """Serialize update/reset, persist before publication, and retain last good data."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.model_catalog")
        self.catalog: dict[str, Any] | None = None
        self.etag: str | None = None
        self.last_checked = 0.0
        self.last_error: str | None = None
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        """Bad persisted data must not prevent HA from starting."""
        try:
            saved = await self.store.async_load()
            if saved:
                candidate = saved.get("catalog")
                if candidate is not None:
                    candidate = validate_catalog(candidate)
                checked = saved.get("last_checked", 0)
                etag = saved.get("etag")
                if type(checked) not in (float, int) or not 0 <= checked <= time.time():
                    raise ValueError("Invalid catalogue check time")
                if etag is not None and (
                    not isinstance(etag, str)
                    or len(etag) > 256
                    or "\n" in etag
                    or "\r" in etag
                ):
                    raise ValueError("Invalid catalogue ETag")
                # An integration upgrade must not resurrect older downloaded data.
                if (
                    candidate is not None
                    and candidate["catalog_version"]
                    < BUNDLED_CATALOG["catalog_version"]
                ):
                    candidate, etag = None, None
                elif candidate is not None:
                    # Stored overrides must retain every reasoning choice/capability
                    # accepted by the bundled release, otherwise durable agent/rule
                    # data could become invalid immediately after HA restarts.
                    validate_catalog_transition(None, candidate)
                self.catalog, self.etag, self.last_checked = candidate, etag, checked
        except Exception:
            self.last_error = (
                "Stored model data could not be loaded; using bundled data."
            )
            _LOGGER.warning(self.last_error)
        activate_catalog(self.catalog)

    def status(self) -> dict[str, Any]:
        return {
            "source": "downloaded" if self.catalog is not None else "bundled",
            "catalog_version": (self.catalog or BUNDLED_CATALOG)["catalog_version"],
            "schema_version": 1,
            "last_checked": self.last_checked,
            "last_error": self.last_error,
        }

    async def _save(
        self, catalog: dict[str, Any] | None, etag: str | None, checked: float
    ) -> None:
        await self.store.async_save(
            {"catalog": catalog, "etag": etag, "last_checked": checked}
        )

    async def _record_failed_update(self, checked: float, *, transient: bool) -> None:
        """Retain current data while remembering when the failed check occurred."""
        self.last_error = "Model data update failed; the current catalogue was kept."
        if transient:
            # Background refreshes are best-effort. Ordinary loss of internet access,
            # rate limiting, or GitHub/server outages must not pollute HA warnings.
            _LOGGER.debug(self.last_error)
        else:
            # Invalid remote data or a local persistence problem is actionable and can
            # indicate a broken published catalogue, so retain warning visibility.
            _LOGGER.warning(self.last_error)
        # Persist attempt time as well, avoiding retry storms across restarts.
        try:
            await self._save(self.catalog, self.etag, checked)
        except Exception:
            _LOGGER.warning("Unable to persist model catalogue check time")

    async def async_update(self, *, force: bool = False) -> dict[str, Any]:
        """Fetch only trusted data; HTTP/parsing/storage failures preserve current data."""
        async with self._lock:
            now = time.time()
            if not force and now - self.last_checked < UPDATE_INTERVAL:
                return self.status()
            self.last_checked = now
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
                            await self._save(self.catalog, self.etag, now)
                            self.last_error = None
                            return self.status()
                        if response.status == 429 or response.status >= 500:
                            raise _TransientCatalogUpdateError(
                                "Catalogue service temporarily unavailable"
                            )
                        if response.status != 200:
                            raise ValueError("Catalogue HTTP update failed")
                        raw = bytearray()
                        async for chunk in response.content.iter_chunked(16384):
                            raw.extend(chunk)
                            if len(raw) > MAX_CATALOG_BYTES:
                                raise ValueError("Catalogue download too large")
                        candidate = parse_catalog(bytes(raw))
                        etag = response.headers.get("ETag")
                        if etag and (len(etag) > 256 or "\n" in etag or "\r" in etag):
                            raise ValueError("Invalid catalogue ETag")
                current_version = (self.catalog or BUNDLED_CATALOG)["catalog_version"]
                if candidate["catalog_version"] < current_version:
                    raise ValueError("Catalogue version is older than current data")
                if candidate["catalog_version"] == current_version and candidate != (
                    self.catalog or BUNDLED_CATALOG
                ):
                    raise ValueError("Changed catalogue must increment catalog_version")
                validate_catalog_transition(self.catalog, candidate)
                await self._save(candidate, etag, now)
                activate_catalog(candidate)
                self.catalog, self.etag, self.last_error = candidate, etag, None
            except ClientError, TimeoutError, _TransientCatalogUpdateError:
                await self._record_failed_update(now, transient=True)
            except Exception:
                await self._record_failed_update(now, transient=False)
            return self.status()

    async def _bundled_reset_would_invalidate_saved_reasoning(self) -> bool:
        """Check durable agent/rule choices before narrowing back to bundled data."""
        if self.catalog is None:
            return False

        bundled_efforts = set(catalog_reasoning_efforts(None))
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            for subentry in entry.subentries.values():
                if subentry.subentry_type != "conversation":
                    continue

                configured_model = str(
                    subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)
                )
                configured_effort = subentry.data.get(CONF_REASONING_EFFORT)
                bundled_config_efforts = catalog_model_metadata(None, configured_model)[
                    "reasoning_efforts"
                ]
                if (
                    isinstance(configured_effort, str)
                    and configured_effort
                    and configured_effort not in bundled_config_efforts
                ):
                    return True

                rules = await async_get_request_rules(
                    self.hass, entry.entry_id, subentry.subentry_id
                )
                for rule in rules.snapshot()["rules"]:
                    if rule.get("action_type") != "model_routing":
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
                    if (
                        isinstance(model, str)
                        and model
                        and not SLOT_REFERENCE.search(model)
                    ):
                        metadata = catalog_model_metadata(None, model)
                        if (
                            not metadata["parameters"]["supports_reasoning_effort"]
                            or effort not in metadata["reasoning_efforts"]
                        ):
                            return True
                    elif effort not in bundled_efforts:
                        return True
        return False

    async def async_reset(self) -> dict[str, Any]:
        """Return to bundled data without invalidating currently durable choices."""
        async with self._lock:
            now = time.time()
            if await self._bundled_reset_would_invalidate_saved_reasoning():
                self.last_error = (
                    "Model data reset was blocked because saved configuration or "
                    "Request Rules use reasoning choices unavailable in bundled data."
                )
                return self.status()
            try:
                await self._save(None, None, now)
            except Exception:
                self.last_error = (
                    "Model data reset failed; the current catalogue was kept."
                )
                return self.status()
            activate_catalog(None)
            self.catalog, self.etag, self.last_checked, self.last_error = (
                None,
                None,
                now,
                None,
            )
            return self.status()


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_CATALOG,
        vol.Optional("action", default="lookup"): vol.In(("lookup", "update", "reset")),
        vol.Optional("model", default=""): vol.All(str, vol.Length(max=128)),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_catalog(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The management UI reads the same Python lookup used by requests and rules."""
    manager: ModelCatalogManager = hass.data[DATA_MANAGER]
    if msg["action"] == "update":
        status = await manager.async_update(force=True)
        if status["last_error"]:
            connection.send_error(
                msg["id"], "model_catalog_update_failed", status["last_error"]
            )
            return
    elif msg["action"] == "reset":
        await manager.async_reset()
    metadata = model_metadata(msg["model"])
    connection.send_result(
        msg["id"],
        {
            **manager.status(),
            "model_capabilities": {
                **metadata["parameters"],
                "reasoning_effort_options": metadata["reasoning_efforts"],
            },
            "reasoning_effort_options": metadata["reasoning_efforts"]
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
        await manager.async_update()

    # No startup network dependency. First check within an hour, then at most daily.
    cancel = async_track_time_interval(hass, check, timedelta(hours=1))

    @callback
    def stop(_event: Any) -> None:
        cancel()
        activate_catalog(None)

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop)
