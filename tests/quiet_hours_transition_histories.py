"""Independent authored effect histories; no production decisions or flags.

Clock offsets are hours after 2026-11-01 22:30 UTC. Quiet is 22:00-07:00.
Every reconcile checks the entire service-command delta, feedback-settled public
value and convergence. Drivers provide I/O only; expected effects are literals.
"""

import asyncio
from dataclasses import dataclass

import pytest

from custom_components.extended_openai_conversation_responses.global_runtime import (
    LOADED_ENTRIES,
)


@dataclass(frozen=True)
class Step:
    action: str
    value: str | float | None = None
    state: str | None = None
    commands: tuple[str, ...] = ()


def check(hour, state, *commands):
    return Step("reconcile", hour, state, tuple(commands))


HISTORIES = {
    "attributed-effect-lost-ack": (
        Step("lose_ack"),
        check(0, "quiet", "quiet"),
        Step("crash"),
        check(0.1, "quiet"),
        check(10, "baseline", "baseline"),
    ),
    "steady": (
        check(0, "quiet", "quiet"),
        check(0.1, "quiet"),
        check(10, "baseline", "baseline"),
        check(10.1, "baseline"),
    ),
    "manual-observed-before-crash": (
        check(0, "quiet", "quiet"),
        Step("report", "alternate"),
        check(0.1, "alternate"),
        Step("crash"),
        check(0.2, "alternate"),
        check(10, "alternate"),
        check(24, "quiet", "quiet"),
        check(34, "alternate", "alternate"),
    ),
    "manual-unobserved-before-crash": (
        check(0, "quiet", "quiet"),
        Step("report", "alternate"),
        Step("crash"),
        check(0.1, "alternate"),
        check(0.2, "alternate"),
        check(10, "alternate"),
    ),
    "already-quiet-observation": (
        Step("report", "quiet"),
        check(0, "quiet"),
        Step("report", "alternate"),
        Step("crash"),
        check(0.1, "alternate"),
        check(24, "quiet", "quiet"),
        check(34, "alternate", "alternate"),
    ),
    "available-across-skipped-day": (
        check(0, "quiet", "quiet"),
        Step("crash"),
        check(24, "quiet"),
        check(24.1, "quiet"),
        check(34, "baseline", "baseline"),
    ),
    "offline-across-observed-boundaries": (
        check(0, "quiet", "quiet"),
        Step("offline"),
        check(10, "offline"),
        check(24, "offline"),
        check(34, "offline"),
        check(48, "offline"),
        Step("crash"),
        Step("report", "baseline"),
        check(49, "quiet", "quiet"),
        check(58, "baseline", "baseline"),
    ),
    "offline-across-skipped-boundaries": (
        check(0, "quiet", "quiet"),
        Step("offline"),
        Step("crash"),
        Step("report", "baseline"),
        check(48, "quiet", "quiet"),
        check(58, "baseline", "baseline"),
    ),
    "changed-return-adopts-new-baseline": (
        check(0, "quiet", "quiet"),
        Step("offline"),
        check(10, "offline"),
        check(24, "offline"),
        Step("report", "alternate"),
        Step("crash", state="quiet", commands=("quiet",)),
        check(25, "quiet"),
        check(34, "alternate", "alternate"),
    ),
    "departing-owned": (
        check(0, "quiet", "quiet"),
        Step("depart"),
        check(0.1, "baseline", "baseline"),
        Step("crash"),
        check(0.2, "baseline"),
        Step("return"),
        check(0.3, "quiet", "quiet"),
        check(10, "baseline", "baseline"),
    ),
    "departing-manual": (
        check(0, "quiet", "quiet"),
        Step("report", "alternate"),
        Step("depart"),
        check(0.1, "alternate"),
        Step("crash"),
        Step("return"),
        check(0.2, "alternate"),
        check(24, "quiet", "quiet"),
        check(34, "alternate", "alternate"),
    ),
    "restoration-lost-ack-rejoin": (
        check(0, "quiet", "quiet"),
        Step("depart"),
        Step("lose_ack"),
        check(0.1, "baseline", "baseline"),
        Step("return"),
        check(0.2, "quiet", "quiet"),
        check(0.3, "quiet"),
        check(10, "baseline", "baseline"),
    ),
    "restoration-lost-ack-restart": (
        check(0, "quiet", "quiet"),
        Step("depart"),
        Step("lose_ack"),
        check(0.1, "baseline", "baseline"),
        Step("return"),
        Step("crash", state="quiet", commands=("quiet",)),
        check(0.2, "quiet"),
        check(10, "baseline", "baseline"),
    ),
    "restoration-retry-reports-after-policy-reentry": (
        check(0, "quiet", "quiet"),
        Step("depart"),
        Step("reject"),
        check(0.1, "quiet", "baseline"),
        Step("reject"),
        check(0.2, "quiet", "baseline"),
        Step("return"),
        check(0.3, "quiet"),
        Step("late_restore_report", "baseline"),
        check(0.4, "quiet", "quiet"),
        check(10, "baseline", "baseline"),
    ),
    "replacement-retains-pending": (
        Step("interrupt", "pending", "baseline"),
        Step("startup_restore", state="baseline"),
        Step("crash", state="quiet", commands=("quiet",)),
        check(0.2, "quiet"),
        check(10, "baseline", "baseline"),
    ),
    "replacement-retains-prepared": (
        Step("interrupt", "prepared", "baseline"),
        Step("startup_restore", state="baseline"),
        Step("crash", state="quiet", commands=("quiet",)),
        check(0.2, "quiet"),
        check(10, "baseline", "baseline"),
    ),
    "replacement-preserves-newer-baseline-context": (
        Step("interrupt", "prepared", "baseline"),
        Step("report", "baseline"),
        Step("startup_restore", state="baseline"),
        Step("crash", state="baseline"),
        check(0.2, "baseline"),
        check(24, "quiet", "quiet"),
        check(34, "baseline", "baseline"),
    ),
    "pending-daytime-restoration": (
        check(0, "quiet", "quiet"),
        Step("offline"),
        check(10, "offline"),
        Step("crash"),
        Step("report", "quiet"),
        check(10.1, "baseline", "baseline"),
        check(10.2, "baseline"),
        check(24, "quiet", "quiet"),
        check(34, "baseline", "baseline"),
    ),
}


VOLUME_RETARGET_HISTORIES = {
    "acknowledged-contextless-exact": (
        check(0, "quiet", "quiet"),
        Step("contextless"),
        Step("retarget", "retargeted", "retargeted", ("retargeted",)),
        check(0.1, "retargeted"),
        check(10, "baseline", "baseline"),
    ),
    "acknowledged-contextless-late-after-restart": (
        check(0, "quiet", "quiet"),
        Step("hold_contextless"),
        Step("retarget", "retargeted", "quiet", ("retargeted",)),
        Step("crash", state="quiet"),
        Step("release_feedback"),
        check(0.1, "retargeted"),
        check(10, "baseline", "baseline"),
    ),
    "acknowledged-then-manual-adjustment": (
        check(0, "quiet", "quiet"),
        Step("contextless"),
        Step("retarget", "retargeted", "retargeted", ("retargeted",)),
        Step("report", "alternate"),
        check(0.1, "alternate"),
        check(10, "alternate"),
    ),
    "failed-acknowledgement-with-contextless-exact-report": (
        check(0, "quiet", "quiet"),
        Step("contextless"),
        Step("lose_ack"),
        Step("retarget", "retargeted", "retargeted", ("retargeted",)),
        check(0.1, "retargeted"),
        check(10, "retargeted"),
    ),
}


async def interrupt_application(driver, checkpoint):
    """Interrupt after the selected durable write and before device dispatch."""
    save = driver.manager._async_save_control_state_locked
    # The switch scenario first observes a speaker already below the limit.
    target = {"pending": 1, "prepared": 2}[checkpoint] + (driver.kind == "switch")
    writes = 0

    async def save_then_interrupt():
        nonlocal writes
        await save()
        writes += 1
        if writes == target:
            raise asyncio.CancelledError

    driver.manager._async_save_control_state_locked = save_then_interrupt
    try:
        with pytest.raises(asyncio.CancelledError):
            await driver.reconcile(0)
    finally:
        del driver.manager._async_save_control_state_locked
    active = driver.manager.active
    if checkpoint == "pending":
        assert driver.entity in active["pending_controls"]
    else:
        assert active["controls"][driver.entity]["application_state"] == "prepared"


async def restore_while_suspended(manager):
    """Run the startup restoration entry point without enabling the policy."""
    hass = manager.hass
    previous = hass.data.get(LOADED_ENTRIES)
    hass.data[LOADED_ENTRIES] = set()
    try:
        await manager.async_restore_saved_controls()
    finally:
        if previous is None:
            hass.data.pop(LOADED_ENTRIES)
        else:
            hass.data[LOADED_ENTRIES] = previous


def retire_without_restoration(manager):
    """End this simulated process's callbacks without a graceful device restore."""
    for unsubscribe in manager._unsubscribers:
        unsubscribe()
    manager._unsubscribers.clear()
    for field in ("_timezone_unsubscribe", "_registry_unsubscribe"):
        unsubscribe = getattr(manager, field, None)
        if unsubscribe is not None:
            unsubscribe()
            setattr(manager, field, None)
    manager._stopping = True


async def exercise_history(driver, history):
    for index, step in enumerate(history):
        before = len(driver.commands)
        if step.action == "reconcile":
            await driver.reconcile(step.value)
            driver.feedback()
        elif step.action == "report":
            driver.report(driver.values[step.value])
        elif step.action == "offline":
            driver.offline()
        elif step.action == "crash":
            await driver.crash()
            driver.feedback()
        elif step.action == "lose_ack":
            driver.lose_ack = True
        elif step.action == "reject":
            driver.reject = True
        elif step.action == "late_restore_report":
            application, first_restore, second_restore = driver.command_contexts
            assert first_restore.id == second_restore.id != application.id
            driver.report(driver.values[step.value], first_restore)
        elif step.action == "depart":
            driver.depart()
        elif step.action == "return":
            driver.return_to_policy()
        elif step.action == "interrupt":
            await interrupt_application(driver, step.value)
        elif step.action == "startup_restore":
            await driver.startup_restore()
        elif step.action in {"contextless", "hold_contextless"}:
            driver.contextless = True
            driver.hold_feedback = step.action == "hold_contextless"
        elif step.action == "release_feedback":
            driver.hold_feedback = False
            driver.feedback()
        elif step.action == "retarget":
            await driver.manager.async_update_config(
                {
                    **driver.manager.config.as_dict(),
                    "max_volume": driver.values[step.value],
                }
            )
            driver.feedback()
        else:
            raise AssertionError(step)
        if step.action == "reconcile":
            assert driver.policy_active() == (0 <= step.value % 24 < 8.5), (index, step)
        if step.state is not None:
            assert driver.current() == driver.values.get(step.state), (index, step)
        expected = []
        for name in step.commands:
            value = driver.values[name]
            domain, service = (
                ("media_player", "volume_set")
                if driver.kind == "volume"
                else ("switch", "turn_on" if value else "turn_off")
            )
            expected.append((domain, service, driver.entity, value))
        assert driver.commands[before:] == expected, (index, step, driver.commands)
