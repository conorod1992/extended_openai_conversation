"""Diagnostic usage sensors for conversation agents."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
import logging
from math import inf, isfinite, nextafter

from homeassistant.components import sensor as sensor_component
from homeassistant.components.recorder import history
from homeassistant.components.sensor import (
    ATTR_LAST_RESET,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import EVENT_CORE_CONFIG_UPDATE, EntityCategory
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.recorder import DATA_INSTANCE as DATA_RECORDER_INSTANCE
from homeassistant.helpers.restore_state import (
    ExtraStoredData,
    RestoredExtraData,
    RestoreEntity,
)
from homeassistant.util import dt as dt_util

from . import ExtendedOpenAIConfigEntry
from .agent_maintenance import get_agent_maintenance_gate
from .const import CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL, DOMAIN
from .guest_mode import GuestModeManager, async_get_guest_mode
from .local_time import resolve_wall_time
from .persistence_hardening import StorageRepairRequired
from .usage import UsageManager, async_get_usage, usage_retention_policy

_LOGGER = logging.getLogger(__name__)
_RECORDER_WINDOW_SECONDS = 5 * 60


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ExtendedOpenAIConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one compact diagnostic sensor per conversation agent."""
    for subentry in config_entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue
        try:
            usage = await async_get_usage(
                hass, config_entry.entry_id, subentry.subentry_id
            )
            guest_mode = await async_get_guest_mode(
                hass, config_entry.entry_id, subentry.subentry_id
            )
        except StorageRepairRequired as err:
            _LOGGER.error(
                "Assistant %s sensors remain unavailable because retained storage requires repair: %s",
                subentry.subentry_id,
                err,
            )
            continue
        except HomeAssistantError:
            gate = get_agent_maintenance_gate(
                hass, config_entry.entry_id, subentry.subentry_id
            )
            if not gate.recovery_required:
                raise
            _LOGGER.error(
                "Assistant %s sensors remain unavailable pending restore recovery",
                subentry.subentry_id,
            )
            continue
        retention = usage_retention_policy(subentry.data)
        usage.request_retention_days = retention["request_retention_days"]
        usage.run_retention_days = retention["run_retention_days"]
        async_add_entities(
            [
                UsageSensor(subentry, usage),
                UsageTodaySensor(subentry, usage),
                UsageMonthSensor(subentry, usage),
                LastResponseUsageSensor(subentry, usage),
                GuestModeSensor(subentry, guest_mode),
            ],
            config_subentry_id=subentry.subentry_id,
        )


class GuestModeSensor(SensorEntity):
    """Expose the integration-owned Guest Mode schedule."""

    _attr_has_entity_name = True
    _attr_translation_key = "guest_mode"
    _attr_icon = "mdi:account-lock"
    _attr_should_poll = False

    def __init__(self, subentry: ConfigSubentry, guest_mode: GuestModeManager) -> None:
        self._guest_mode = guest_mode
        self._attr_unique_id = f"{subentry.subentry_id}_guest_mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="OpenAI",
            model=subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        """Do not present a speculative schedule after uncertain persistence."""
        return self._guest_mode.authoritative_available

    @property
    def native_value(self) -> str | None:
        if not self.available:
            return None
        return str(self._guest_mode.status()["state"])

    @property
    def extra_state_attributes(self) -> dict:
        if not self.available:
            return {}
        status = self._guest_mode.status()
        return {
            key: status[key]
            for key in (
                "active_from",
                "active_until",
                "indefinite",
                "currently_active",
                "scheduled",
            )
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._guest_mode.async_add_listener(self.async_write_ha_state)
        )


class UsageSensor(SensorEntity):
    """Expose cumulative token usage with request counts as attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "usage"
    _attr_icon = "mdi:counter"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = "tokens"
    _attr_should_poll = False
    # Backup restore can legitimately decrease this counter. A decrease is a
    # correction, not a new meter cycle whose entire value should be added.
    _attr_state_class: SensorStateClass | None = SensorStateClass.TOTAL

    def __init__(self, subentry: ConfigSubentry, usage: UsageManager) -> None:
        """Initialize the diagnostic sensor."""
        self._usage = usage
        self._subentry_id = subentry.subentry_id
        self._usage_listener_remove: Callable[[], None] | None = None
        self._attr_unique_id = f"{subentry.subentry_id}_usage"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="OpenAI",
            model=subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL),
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> int | None:
        """Return cumulative real token usage."""
        return self._usage.totals.total_tokens

    @property
    def extra_state_attributes(self) -> dict:
        """Expose the distinct conversation, request, and token counters."""
        return self._usage.as_dict()

    def _remove_usage_listener(self) -> None:
        """Remove the current manager listener without changing entity lifetime."""
        remove = self._usage_listener_remove
        self._usage_listener_remove = None
        if remove is not None:
            remove()

    def rebind_usage(self, usage: UsageManager) -> bool:
        """Move this live sensor to a replacement authoritative Usage manager."""
        if usage is self._usage:
            return False
        subscribed = self._usage_listener_remove is not None
        self._remove_usage_listener()
        self._usage = usage
        if subscribed:
            self._usage_listener_remove = usage.async_add_listener(
                self._handle_usage_update
            )
            self._handle_usage_update()
        return True

    @callback
    def _handle_usage_update(self) -> None:
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Subscribe to persisted usage updates."""
        await super().async_added_to_hass()
        self._usage_listener_remove = self._usage.async_add_listener(
            self._handle_usage_update
        )
        self.async_on_remove(self._remove_usage_listener)


@dataclass(frozen=True, slots=True)
class _PeriodObservation:
    reset: datetime
    tokens: int
    lifetime_tokens: int | None
    timestamp: float | None = None


def _period_observation(state: State | None) -> _PeriodObservation | None:
    if state is None:
        return None
    reset = state.attributes.get(ATTR_LAST_RESET)
    if not isinstance(reset, str):
        return None
    marker = dt_util.parse_datetime(reset)
    try:
        tokens = int(state.state)
    except TypeError, ValueError:
        return None
    if marker is None or marker.tzinfo is None or tokens < 0:
        return None
    return _PeriodObservation(marker, tokens, None, state.last_updated_timestamp)


def _saved_recorder_observation(data: object) -> _PeriodObservation | None:
    if not isinstance(data, dict) or not isinstance(reset := data.get("reset"), str):
        return None
    marker = dt_util.parse_datetime(reset)
    tokens = data.get("tokens")
    lifetime = data.get("lifetime_tokens")
    timestamp = data.get("timestamp")
    if (
        marker is None
        or marker.tzinfo is None
        or type(tokens) is not int
        or tokens < 0
        or (lifetime is not None and (type(lifetime) is not int or lifetime < 0))
        or type(timestamp) not in (int, float)
        or not isfinite(timestamp)
    ):
        return None
    return _PeriodObservation(marker, tokens, lifetime, timestamp)


class _PeriodUsageSensor(UsageSensor, RestoreEntity):
    """Common event-driven period sensor behavior."""

    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, subentry: ConfigSubentry, usage: UsageManager) -> None:
        super().__init__(subentry, usage)
        self._cancel_period_rollover: Callable[[], None] | None = None
        self._observation: _PeriodObservation | None = None
        self._recorded_observation: _PeriodObservation | None = None
        self._recorder_baseline: int | None = None

    def _summary(self) -> dict:
        raise NotImplementedError

    def _period_start(self, today: date) -> date:
        return today

    @property
    def native_value(self) -> int:
        if self._recorder_baseline is not None:
            return self._recorder_baseline
        return int(self._summary()["total_tokens"])

    @property
    def last_reset(self) -> datetime | None:
        if self._recorder_baseline is not None:
            # A numeric TOTAL sample without last_reset changes the running
            # baseline without opening a cycle. It also stays neutral if
            # Recorder sees both equal-time samples as the next start state.
            return None
        if self._observation is not None:
            return self._observation.reset
        return dt_util.start_of_local_day(self._period_start(dt_util.now().date()))

    @property
    def extra_restore_state_data(self) -> ExtraStoredData | None:
        if self._observation is None:
            return None
        recorded = self._recorded_observation
        return RestoredExtraData(
            {
                "lifetime_tokens": self._observation.lifetime_tokens,
                "recorded": (
                    {
                        "reset": recorded.reset.isoformat(),
                        "tokens": recorded.tokens,
                        "lifetime_tokens": recorded.lifetime_tokens,
                        "timestamp": recorded.timestamp,
                    }
                    if recorded is not None
                    else None
                ),
            }
        )

    @property
    def extra_state_attributes(self) -> dict:
        summary = self._summary()
        return {
            "input": summary["input_tokens"],
            "output": summary["output_tokens"],
            "cached_input": summary["cached_input_tokens"],
            "reasoning": summary["reasoning_tokens"],
            "runs": summary["run_count"],
            "requests": summary["api_request_count"],
            "failures": summary["failed_request_count"],
            "average_tokens_per_run": summary["average_tokens_per_completed_run"],
        }

    async def async_added_to_hass(self) -> None:
        """Refresh event-driven period state when the local date rolls over."""
        await super().async_added_to_hass()
        previous_state = await self._async_restore_period()
        self._publish_period(initial=True, previous_state=previous_state)
        self._reschedule_period_rollover()
        self.async_on_remove(self._remove_period_rollover)
        self.async_on_remove(
            self.hass.bus.async_listen(
                EVENT_CORE_CONFIG_UPDATE, self._handle_core_config_update
            )
        )

    async def _async_restore_period(self) -> State | None:
        restored = (
            await self.async_get_last_state()
            if self.registry_entry is not None
            else None
        )
        observation = _period_observation(restored)
        previous_state = restored if observation is not None else None
        extra = await self.async_get_last_extra_data() if restored is not None else None
        data = extra.as_dict() if extra is not None else {}
        if observation is not None:
            tokens = data.get("lifetime_tokens")
            if type(tokens) is int and tokens >= 0:
                observation = replace(observation, lifetime_tokens=tokens)
        recorded = _saved_recorder_observation(data.get("recorded"))
        live_state = self.hass.states.get(self.entity_id)
        if live := _period_observation(live_state):
            if observation is not None and (live.reset, live.tokens) == (
                observation.reset,
                observation.tokens,
            ):
                live = replace(live, lifetime_tokens=observation.lifetime_tokens)
            observation, previous_state = live, live_state
        previous_visible = observation

        # Before these sensors used RestoreEntity, Recorder held their offset-
        # bearing marker. It also distinguishes a saved visible state published
        # during a pause from the last state accepted by Recorder.
        historical = None
        if (recorder := self.hass.data.get(DATA_RECORDER_INSTANCE)) is not None:
            try:
                states = await recorder.async_add_executor_job(
                    history.get_last_state_changes, self.hass, 8, self.entity_id
                )
                for state in reversed(states.get(self.entity_id, [])):
                    if historical := _period_observation(state):
                        if (
                            previous_state is None
                            or state.last_updated > previous_state.last_updated
                        ):
                            observation, previous_state = historical, state
                        break
            except Exception:
                _LOGGER.debug(
                    "Unable to read the previous Usage period from Recorder",
                    exc_info=True,
                )
        if historical is not None and (
            recorded is None or (historical.timestamp or 0) > (recorded.timestamp or 0)
        ):
            known = recorded or previous_visible
            if known is not None and (known.reset, known.tokens) == (
                historical.reset,
                historical.tokens,
            ):
                historical = replace(historical, lifetime_tokens=known.lifetime_tokens)
            recorded = historical
        elif recorded is None and "recorded" not in data and self._records_period():
            recorded = observation
        self._observation = observation
        self._recorded_observation = recorded
        return previous_state

    @callback
    def _records_period(self) -> bool:
        recorder = self.hass.data.get(DATA_RECORDER_INSTANCE)
        return bool(
            recorder is not None
            and recorder.recording
            and recorder.enabled
            and (
                recorder.entity_filter is None or recorder.entity_filter(self.entity_id)
            )
        )

    @callback
    def _preserve_period_sample_order(
        self, previous: _PeriodObservation, sample: State
    ) -> None:
        if previous.timestamp is None:
            return
        previous_time, sample_time = (
            previous.timestamp,
            sample.last_updated_timestamp,
        )
        if previous_time < sample_time or (
            previous_time == sample_time
            and previous.reset.isoformat() == sample.attributes.get(ATTR_LAST_RESET)
        ):
            return
        # Recorder can read all samples tied at the window start. Publish the
        # real state at the next representable time, within that same window,
        # so a real old-cycle state at this timestamp is not replayed.
        timestamp = nextafter(previous_time, inf)
        if (
            timestamp // _RECORDER_WINDOW_SECONDS
            != sample_time // _RECORDER_WINDOW_SECONDS
        ):
            return
        self._write_recorded_sample(sample, timestamp)

    @callback
    def _write_recorded_sample(
        self, sample: State, timestamp: float | None = None
    ) -> None:
        self.hass.states.async_set(
            self.entity_id,
            sample.state,
            sample.attributes,
            force_update=True,
            context=sample.context,
            state_info=sample.state_info,
            timestamp=timestamp,
        )

    @callback
    def _track_recorded_sample(
        self, previous: _PeriodObservation | None, before: State | None
    ) -> None:
        if (
            self.hass is None
            or self._observation is None
            or not self._records_period()
            or (sample := self.hass.states.get(self.entity_id)) is None
        ):
            return
        if sample is before:
            if previous is not None and (previous.reset, previous.tokens) == (
                self._observation.reset,
                self._observation.tokens,
            ):
                # Lifetime-only reconciliation does not change this period's
                # Recorder sample and must not become usage at the next reset.
                self._recorded_observation = replace(
                    previous, lifetime_tokens=self._observation.lifetime_tokens
                )
                return
            # The real gauge may already be visible from while Recorder was
            # paused. Ensure the newly accepted sample reaches its state log.
            self._write_recorded_sample(sample)
            sample = self.hass.states.get(self.entity_id) or sample
        if previous is not None:
            self._preserve_period_sample_order(previous, sample)
            sample = self.hass.states.get(self.entity_id) or sample
        self._recorded_observation = replace(
            self._observation, timestamp=sample.last_updated_timestamp
        )

    @callback
    def _publish_period(
        self, *, initial: bool = False, previous_state: State | None = None
    ) -> None:
        period = self._period_start(dt_util.now().date())
        tokens = self.native_value
        lifetime = self._usage.totals.total_tokens
        visible = self._observation
        previous = self._recorded_observation
        before = self.hass.states.get(self.entity_id)
        reset = dt_util.start_of_local_day(period)
        recording = self._records_period()
        if visible is not None and visible.reset.date() == period:
            reset = visible.reset
        if recording and previous is not None:
            if previous.reset.date() == period:
                reset = previous.reset
            else:
                if reset == previous.reset:
                    # Opposite sides of the date line may share a UTC midnight.
                    reset += timedelta(microseconds=1)
                delta = (
                    lifetime - previous.lifetime_tokens
                    if previous.lifetime_tokens is not None
                    else lifetime - visible.lifetime_tokens
                    if visible is not None and visible.lifetime_tokens is not None
                    else 0
                )
                # Recorder adds the full state on a reset. Close the old cycle
                # with a correction first when only part of the new bucket is
                # new usage. The next write displays the actual bucket value.
                if delta != tokens:
                    baseline = previous.tokens + delta - tokens
                    if initial and previous_state is not None:
                        # The Entity API starts writing only after registration.
                        attributes = dict(previous_state.attributes)
                        attributes.pop(ATTR_LAST_RESET, None)
                        self.hass.states.async_set(
                            self.entity_id, str(baseline), attributes
                        )
                    elif not initial:
                        self._recorder_baseline = baseline
                        try:
                            self.async_write_ha_state()
                        finally:
                            self._recorder_baseline = None
        self._observation = _PeriodObservation(reset, tokens, lifetime)
        if not initial:
            self.async_write_ha_state()
        if recording:
            if initial:
                self.hass.loop.call_soon(self._track_recorded_sample, previous, before)
            else:
                self._track_recorded_sample(previous, before)

    @callback
    def _handle_usage_update(self) -> None:
        self._publish_period()

    @callback
    def _remove_period_rollover(self) -> None:
        cancel, self._cancel_period_rollover = self._cancel_period_rollover, None
        if cancel is not None:
            cancel()

    @callback
    def _reschedule_period_rollover(self) -> None:
        self._remove_period_rollover()
        zone = dt_util.DEFAULT_TIME_ZONE
        tomorrow = dt_util.now().date() + timedelta(days=1)
        boundary = resolve_wall_time(tomorrow, time.min, zone).astimezone(UTC)
        self._cancel_period_rollover = async_track_point_in_utc_time(
            self.hass, self._handle_period_rollover, boundary
        )

    @callback
    def _handle_core_config_update(self, event: Event) -> None:
        """Rebase the current period and next midnight when HA changes timezone."""
        if "time_zone" in event.data:
            self._reschedule_period_rollover()
            self._publish_period()

    @callback
    def _handle_period_rollover(self, _now: datetime) -> None:
        """Publish the new local day/month even when there is no new usage."""
        self._cancel_period_rollover = None
        self._reschedule_period_rollover()
        self._publish_period()


class UsageTodaySensor(_PeriodUsageSensor):
    """Tokens used during the current Home Assistant local day."""

    _attr_translation_key = "usage_today"

    def __init__(self, subentry: ConfigSubentry, usage: UsageManager) -> None:
        super().__init__(subentry, usage)
        self._attr_unique_id = f"{subentry.subentry_id}_usage_today"

    def _summary(self) -> dict:
        return self._usage.today_summary()


class UsageMonthSensor(_PeriodUsageSensor):
    """Tokens derived from daily aggregates for the current local month."""

    _attr_translation_key = "usage_month"

    def __init__(self, subentry: ConfigSubentry, usage: UsageManager) -> None:
        super().__init__(subentry, usage)
        self._attr_unique_id = f"{subentry.subentry_id}_usage_month"

    def _summary(self) -> dict:
        return self._usage.month_summary()

    def _period_start(self, today: date) -> date:
        return today.replace(day=1)


class LastResponseUsageSensor(UsageSensor):
    """Bounded usage for the latest finalized user turn."""

    _attr_translation_key = "last_response_usage"
    _attr_state_class = None
    # Run detail expires with time even without another accounting event.
    _attr_should_poll = True

    def __init__(self, subentry: ConfigSubentry, usage: UsageManager) -> None:
        super().__init__(subentry, usage)
        self._attr_unique_id = f"{subentry.subentry_id}_last_response_usage"

    @property
    def native_value(self) -> int | None:
        return self._usage.latest_run.total_tokens if self._usage.latest_run else None

    @property
    def extra_state_attributes(self) -> dict:
        run = self._usage.latest_run
        if run is None:
            return {}
        return {
            "completed_at": run.completed_at,
            "duration_ms": run.duration_ms,
            "models": run.models,
            "providers": run.providers,
            "api_request_count": run.request_count,
            "tool_call_count": run.tool_call_count,
            "input": run.input_tokens,
            "output": run.output_tokens,
            "cached_input": run.cached_input_tokens,
            "reasoning": run.reasoning_tokens,
            "success": run.successful,
            "error_type": run.error_type,
        }


def rebind_usage_sensors(
    hass: HomeAssistant,
    subentry_id: str,
    old_usage: UsageManager,
    new_usage: UsageManager,
) -> int:
    """Rebind loaded Usage sensors after volatile-to-durable recovery."""
    component = hass.data.get(sensor_component.DATA_COMPONENT)
    rebound = 0
    for entity in getattr(component, "entities", ()):
        if (
            isinstance(entity, UsageSensor)
            and entity._subentry_id == subentry_id
            and entity._usage is old_usage
            and entity.rebind_usage(new_usage)
        ):
            rebound += 1
    return rebound
