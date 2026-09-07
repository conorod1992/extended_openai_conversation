"""Effective management routing for bounded retained-history responses."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from . import management_ui
from .conversation_archive import async_get_archive
from .management_history_queries import (
    archive_get_page,
    archive_list_page,
    archive_search_page,
    usage_breakdowns,
    usage_daily_page,
    usage_requests_page,
    usage_runs_page,
    usage_summary,
)
from .usage import async_get_usage

ManagementCommand = Callable[
    [HomeAssistant, str, bool, dict[str, Any]], Awaitable[dict[str, Any]]
]
_PATCHED = "extended_openai_management_history_bounds"
_FRONTEND_MODULE = "management-history-pagination.js"


def wrap_management_history_bounds(original: ManagementCommand) -> ManagementCommand:
    """Replace only retained-history reads while preserving all other routing."""

    async def wrapped(
        hass: HomeAssistant,
        user_id: str,
        is_admin: bool,
        message: dict[str, Any],
    ) -> dict[str, Any]:
        section = message.get("section", "overview")
        action = message.get("action")
        if section not in {"overview", "usage", "conversations"}:
            return await original(hass, user_id, is_admin, message)
        if section == "overview" and action != "summary":
            return await original(hass, user_id, is_admin, message)

        entry_id = message.get("entry_id")
        subentry_id = message.get("subentry_id")
        if not isinstance(entry_id, str) or not isinstance(subentry_id, str):
            raise HomeAssistantError("entry_id and subentry_id are required")
        management_ui.entry_and_agent(hass, entry_id, subentry_id)

        if section == "overview":
            result = await original(hass, user_id, is_admin, message)
            # The optimized Overview path loads Usage directly, bypassing the
            # section-specific dispatcher below. Re-project it only after that
            # load succeeded so Overview's existing partial-failure behavior stays intact.
            if result.get("usage"):
                usage = await async_get_usage(hass, entry_id, subentry_id)
                result = {**result, "usage": usage_summary(usage)}
            return result

        if section == "usage":
            usage = await async_get_usage(hass, entry_id, subentry_id)
            if action == "summary":
                return usage_summary(usage)
            if action == "daily":
                return usage_daily_page(
                    usage,
                    start_date=str(message.get("start_date", "0000-01-01")),
                    end_date=str(message.get("end_date", "9999-12-31")),
                    limit=int(message.get("limit", 366)),
                    offset=int(message.get("offset", 0)),
                )
            if action == "runs":
                return usage_runs_page(
                    usage,
                    limit=int(message.get("limit", 50)),
                    offset=int(message.get("offset", 0)),
                    successful=message.get("successful"),
                )
            if action == "requests":
                run_id = message.get("run_id")
                if not isinstance(run_id, str):
                    raise HomeAssistantError("run_id is required")
                return usage_requests_page(
                    usage,
                    run_id,
                    limit=int(message.get("limit", 100)),
                    offset=int(message.get("offset", 0)),
                )
            if action == "breakdowns":
                return usage_breakdowns(
                    usage,
                    start_date=message.get("start_date"),
                    end_date=message.get("end_date"),
                )
            return await original(hass, user_id, is_admin, message)

        if action not in {"list", "search", "get"}:
            return await original(hass, user_id, is_admin, message)
        scope_id = management_ui._selected_scope(
            user_id, is_admin, message.get("scope_id")
        )
        archive = await async_get_archive(hass, entry_id, subentry_id)
        if action == "list":
            return await archive_list_page(
                archive,
                scope_id,
                limit=int(message.get("limit", 50)),
                offset=int(message.get("offset", 0)),
            )
        if action == "search":
            return await archive_search_page(
                archive,
                scope_id,
                str(message.get("query", "")),
                start_date=message.get("start_date"),
                end_date=message.get("end_date"),
                limit=int(message.get("limit", 20)),
                offset=int(message.get("offset", 0)),
            )
        return await archive_get_page(
            archive,
            scope_id,
            str(message.get("session_id", "")),
            start_turn=int(message.get("start_turn", 0)),
            limit=int(message.get("limit", 20)),
        )

    return wrapped


def _register_frontend_module() -> None:
    """Expose the pagination extension through the existing management asset route."""
    management_ui.MANAGEMENT_FRONTEND_MODULES = tuple(
        dict.fromkeys((*management_ui.MANAGEMENT_FRONTEND_MODULES, _FRONTEND_MODULE))
    )


def install_management_history_bounds() -> bool:
    """Install once around the current optimized management command."""
    _register_frontend_module()
    if getattr(management_ui, _PATCHED, False):
        return False
    management_ui.async_management_command = wrap_management_history_bounds(  # type: ignore[assignment]
        management_ui.async_management_command
    )
    setattr(management_ui, _PATCHED, True)
    return True
