"""Model deprecation and retirement lifecycle handling."""

from __future__ import annotations

from datetime import UTC, date, datetime
import logging
from typing import Any

from homeassistant.helpers import issue_registry as ir

from .const import CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL, DOMAIN
from .model_catalog import model_metadata
from .provider_errors import log_provider_failure, provider_error_metadata

_LOGGER = logging.getLogger(__name__)
_DATA_FAILURES = f"{DOMAIN}.model_lifecycle_failures"
_DATA_LOGGED = f"{DOMAIN}.model_lifecycle_logged"


def _parse_date(value: object | None) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def lifecycle_snapshot(model: str, *, today: date | None = None) -> dict[str, Any]:
    """Return bounded lifecycle facts for one exact configured model."""
    metadata = model_metadata(model)
    shutdown = _parse_date(metadata.get("shutdown_at"))
    deprecated = _parse_date(metadata.get("deprecated_at"))
    now = today or datetime.now(UTC).date()
    status = str(metadata.get("status", "unknown"))
    return {
        "model": str(model),
        "status": status,
        "deprecated_at": deprecated.isoformat() if deprecated else None,
        "shutdown_at": shutdown.isoformat() if shutdown else None,
        "lifecycle_note": metadata.get("lifecycle_note"),
        "shutdown_reached": bool(shutdown and now >= shutdown),
    }


def configured_lifecycle(subentry: Any, *, today: date | None = None) -> dict[str, Any]:
    """Return lifecycle facts for one conversation subentry."""
    model = str(subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)).strip()
    return lifecycle_snapshot(model, today=today)


def _failure_key(entry_id: str, subentry_id: str) -> tuple[str, str]:
    return entry_id, subentry_id


def _issue_id(entry_id: str, subentry_id: str) -> str:
    return f"retired_model_{entry_id}_{subentry_id}"


def confirmed_retirement_failure(
    hass: Any, entry_id: str, subentry_id: str, model: str
) -> bool:
    """Return whether this exact configured model has a confirmed retirement failure."""
    failure = hass.data.get(_DATA_FAILURES, {}).get(_failure_key(entry_id, subentry_id))
    return isinstance(failure, dict) and failure.get("model") == model


def retirement_provider_error(error: BaseException) -> bool:
    """Recognize provider failures that can plausibly mean a model is unavailable."""
    metadata = provider_error_metadata(error)
    code = str(metadata.get("code") or "").casefold()
    error_type = str(metadata.get("provider_error_type") or "").casefold()
    message = str(metadata.get("message") or "").casefold()
    status = metadata.get("status_code")
    if code in {
        "model_not_found",
        "model_deprecated",
        "model_unavailable",
        "unsupported_model",
    }:
        return True
    if error_type in {"model_not_found", "model_deprecated", "model_unavailable"}:
        return True
    return bool(
        status == 404
        and "model" in message
        and any(
            phrase in message
            for phrase in (
                "not found",
                "does not exist",
                "not available",
                "unavailable",
                "deprecated",
                "retired",
            )
        )
    )


def retirement_message(lifecycle: dict[str, Any]) -> str:
    """Return the actionable user-facing retirement message."""
    model = lifecycle["model"]
    shutdown = lifecycle.get("shutdown_at")
    when = f" on {shutdown}" if shutdown else ""
    return (
        f"The configured model {model} appears to have been retired by OpenAI{when}. "
        "Select a different model for this assistant in Model & responses."
    )


def record_retirement_failure(
    hass: Any,
    *,
    entry_id: str,
    subentry_id: str,
    title: str,
    model: str,
    error: BaseException,
    configured_model: str | None = None,
    logger: logging.Logger | None = None,
) -> str | None:
    """Record a provider-confirmed retirement and create an actionable HA Repair."""
    lifecycle = lifecycle_snapshot(model)
    if (
        lifecycle["status"] != "deprecated"
        or not lifecycle["shutdown_reached"]
        or not retirement_provider_error(error)
    ):
        return None

    failures = hass.data.setdefault(_DATA_FAILURES, {})
    key = _failure_key(entry_id, subentry_id)
    failure = {
        "model": model,
        "shutdown_at": lifecycle.get("shutdown_at"),
        "configured_model": configured_model or model,
    }
    first = failures.get(key) != failure
    failures[key] = failure

    ir.async_create_issue(
        hass,
        DOMAIN,
        _issue_id(entry_id, subentry_id),
        is_fixable=False,
        is_persistent=True,
        data=failure,
        severity=ir.IssueSeverity.ERROR,
        translation_key="retired_model",
        translation_placeholders={
            "assistant": title or subentry_id,
            "model": model,
            "shutdown_at": str(
                lifecycle.get("shutdown_at") or "the announced shutdown date"
            ),
        },
    )

    message = retirement_message(lifecycle)
    if configured_model is not None and configured_model != model:
        message = f"Request Rule selected retired model {model}. Update or disable the rule's model override in Request Rules."
    if first:
        log_provider_failure(
            logger or _LOGGER,
            f"{message} entry={entry_id} assistant={subentry_id} configured_model={model}",
            error,
        )
    return message


def clear_retirement_failure(
    hass: Any, *, entry_id: str, subentry_id: str, model: str | None = None
) -> None:
    """Resolve a retirement Repair after success or a model configuration change."""
    failures = hass.data.setdefault(_DATA_FAILURES, {})
    key = _failure_key(entry_id, subentry_id)
    failure = failures.get(key)
    if failure is None:
        return
    if (
        model is not None
        and isinstance(failure, dict)
        and failure.get("model") != model
    ):
        return
    failures.pop(key, None)
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry_id, subentry_id))


def log_deprecation_once(
    hass: Any,
    *,
    entry_id: str,
    subentry_id: str,
    title: str,
    lifecycle: dict[str, Any],
) -> None:
    """Log one warning per HA runtime for each configured deprecation state."""
    if lifecycle.get("status") != "deprecated":
        return
    token = (
        entry_id,
        subentry_id,
        lifecycle.get("model"),
        lifecycle.get("shutdown_at"),
    )
    logged = hass.data.setdefault(_DATA_LOGGED, set())
    if token in logged:
        return
    logged.add(token)
    shutdown = lifecycle.get("shutdown_at")
    suffix = f"; scheduled API shutdown {shutdown}" if shutdown else ""
    _LOGGER.warning(
        'Assistant "%s" uses deprecated model %s%s. Select a replacement before shutdown.',
        title or subentry_id,
        lifecycle.get("model"),
        suffix,
    )


def _override_model_is_still_configured(
    hass: Any, entry: Any, subentry: Any, model: str
) -> bool:
    """Keep rule-selected retirement failures only while a route can select them."""
    from .request_rules import _MANAGERS, SLOT_REFERENCE

    manager = hass.data.get(_MANAGERS, {}).get((entry.entry_id, subentry.subentry_id))
    if manager is None or not manager._initialized:
        return True  # Reconcile after rules have been loaded, never guess from absence.
    return any(
        rule.get("enabled", True)
        and rule.get("action_type") == "model_routing"
        and not rule["action"].get("reset")
        and (
            rule["action"].get("model") == model
            or bool(SLOT_REFERENCE.search(rule["action"].get("model") or ""))
        )
        for rule in manager.snapshot()["rules"]
    )


def sync_entry_model_lifecycle(hass: Any, entry: Any) -> None:
    """Refresh one entry's deprecation logging and stale retirement Repairs."""
    active_subentries: set[str] = set()
    for subentry in entry.subentries.values():
        if subentry.subentry_type != "conversation":
            continue
        active_subentries.add(subentry.subentry_id)
        lifecycle = configured_lifecycle(subentry)
        log_deprecation_once(
            hass,
            entry_id=entry.entry_id,
            subentry_id=subentry.subentry_id,
            title=subentry.title,
            lifecycle=lifecycle,
        )
        key = _failure_key(entry.entry_id, subentry.subentry_id)
        failures = hass.data.setdefault(_DATA_FAILURES, {})
        failure = failures.get(key)
        stored_issue = None
        if failure is None:
            stored_issue = ir.async_get(hass).async_get_issue(
                DOMAIN, _issue_id(entry.entry_id, subentry.subentry_id)
            )
            if stored_issue is not None and isinstance(stored_issue.data, dict):
                failure = dict(stored_issue.data)
                failures[key] = failure
        if isinstance(failure, dict) and (
            failure.get("configured_model", failure.get("model")) != lifecycle["model"]
            or (
                failure.get("model") != lifecycle["model"]
                and not _override_model_is_still_configured(
                    hass, entry, subentry, str(failure.get("model", ""))
                )
            )
            or lifecycle_snapshot(str(failure.get("model", "")))["status"]
            != "deprecated"
            or not lifecycle_snapshot(str(failure.get("model", "")))["shutdown_reached"]
        ):
            clear_retirement_failure(
                hass, entry_id=entry.entry_id, subentry_id=subentry.subentry_id
            )
        elif stored_issue is not None and not stored_issue.active:
            ir.async_create_issue(
                hass,
                DOMAIN,
                stored_issue.issue_id,
                is_fixable=False,
                is_persistent=True,
                data=stored_issue.data,
                severity=ir.IssueSeverity.ERROR,
                translation_key="retired_model",
                translation_placeholders=stored_issue.translation_placeholders,
            )

    failures = hass.data.setdefault(_DATA_FAILURES, {})
    for stored_entry_id, stored_subentry_id in list(failures):
        if (
            stored_entry_id == entry.entry_id
            and stored_subentry_id not in active_subentries
        ):
            clear_retirement_failure(
                hass, entry_id=stored_entry_id, subentry_id=stored_subentry_id
            )


def sync_all_model_lifecycles(hass: Any) -> None:
    """Refresh lifecycle state after catalogue activation."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        sync_entry_model_lifecycle(hass, entry)


def _entity_request_model(entity: Any) -> str:
    """Use the recorded request model, including overrides and in-flight edits."""
    usage = getattr(entity, "_usage", None)
    current_run = getattr(usage, "current_run", None)
    run = current_run() if callable(current_run) else None
    models = getattr(run, "models", ())
    if models:
        return str(models[-1])
    return str(entity.subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)).strip()


def record_entity_retirement_failure(
    entity: Any, error: BaseException, *, logger: logging.Logger | None = None
) -> str | None:
    """Record a retirement failure using a conversation entity's ownership data."""
    hass = getattr(entity, "hass", None)
    entry = getattr(entity, "entry", None)
    subentry = getattr(entity, "subentry", None)
    if hass is None or entry is None or subentry is None:
        return None
    model = _entity_request_model(entity)
    return record_retirement_failure(
        hass,
        entry_id=entry.entry_id,
        subentry_id=subentry.subentry_id,
        title=getattr(subentry, "title", subentry.subentry_id),
        model=model,
        error=error,
        configured_model=str(
            subentry.data.get(CONF_CHAT_MODEL, DEFAULT_CHAT_MODEL)
        ).strip(),
        logger=logger,
    )


def clear_entity_retirement_failure(entity: Any) -> None:
    """Resolve any retirement failure associated with one conversation entity."""
    hass = getattr(entity, "hass", None)
    entry = getattr(entity, "entry", None)
    subentry = getattr(entity, "subentry", None)
    if hass is None or entry is None or subentry is None:
        return
    model = _entity_request_model(entity)
    clear_retirement_failure(
        hass,
        entry_id=entry.entry_id,
        subentry_id=subentry.subentry_id,
        model=model,
    )
