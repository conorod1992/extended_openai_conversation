"""Independent authored effect histories; no production decisions or flags.

Clock offsets are hours after 2026-11-01 22:30 UTC. Quiet is 22:00-07:00.
Every reconcile checks the entire service-command delta, feedback-settled public
value and convergence. Drivers provide I/O only; expected effects are literals.
"""

from dataclasses import dataclass


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
        check(10, "baseline"),
    ),
    "departing-manual": (
        check(0, "quiet", "quiet"),
        Step("report", "alternate"),
        Step("depart"),
        check(0.1, "alternate"),
        Step("crash"),
        check(24, "alternate"),
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
        elif step.action == "depart":
            driver.depart()
        else:
            raise AssertionError(step)
        if step.action == "reconcile":
            assert driver.policy_active() == (0 <= step.value % 24 < 8.5), (index, step)
        if step.state is not None:
            assert driver.current() == driver.values.get(step.state), (index, step)
        assert driver.commands[before:] == [
            driver.values[name] for name in step.commands
        ], (index, step, driver.commands)
