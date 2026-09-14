# Delayed Function Tools

Configured Function Tools can request deferred execution for actions that should happen later, such as turning a device off after a delay. This is separate from Home Assistant's ordinary timer feature: the pending item represents a future Function Tool execution.

## Persistence across restart

Pending delayed calls are persisted. If the integration or Home Assistant process restarts before a call becomes due, an eligible pending call can be restored and executed once with the Home Assistant user context that scheduled it.

The execution boundary is intentionally at-most-once. A persisted item that had already crossed into execution before an unexpected process failure is not blindly replayed after restart, because doing so could repeat a real-world action whose result is uncertain.

## Authorization is checked again

Scheduling a delayed call does not permanently reserve permission to perform it. When the call becomes due, Extended OpenAI rechecks the current active user and the current Home Assistant control authorization, along with the current relevant integration policy.

If the user no longer has permission, the due item is finalized without executing. Restoring permission later does not replay that rejected old item; a new request can schedule a fresh call.

Guest Mode and other current restrictions can therefore tighten what a previously scheduled call is allowed to do.

## Configuration and maintenance changes

Delayed execution is coordinated with integration maintenance operations such as full Backup & Restore so a due tool does not deliberately run against a half-restored configuration or policy state.

If a Function Tool has been removed, disabled, or changed in a way that makes the pending call invalid, current validation applies rather than assuming the old runtime environment still exists.

## Local handling

Home Assistant may interpret both ordinary timers and delayed device requests through timer-related intents. Under **Capabilities → Home Assistant & local handling**, you can choose to send delayed device commands onward to AI/Function Tools while still allowing ordinary requests such as "set a 20 minute timer" to stay on the local Home Assistant path.

Use delayed Function Tools when the eventual action needs the Function Tool path and its authorization checks; use Home Assistant timers when the request is simply a timer.
