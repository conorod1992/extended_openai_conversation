# Delayed Function Tools

Configured Function Tools can request actions that should happen later, such as turning a device off after a delay. This is separate from Home Assistant's ordinary timer feature: the pending item represents a future Function Tool action.

## Allow a Function Tool to request a delay

Add an optional `delay` property to the tool's `spec.parameters.properties`, alongside its normal inputs:

```yaml
delay:
  type: object
  properties:
    hours: {type: integer, minimum: 0}
    minutes: {type: integer, minimum: 0}
    seconds: {type: integer, minimum: 0}
  additionalProperties: false
```

For example, `"delay": {"minutes": 10}` schedules the configured operation for later. The integration removes this scheduling object before passing the other arguments to the implementation. A non-object parameter named `delay` remains an ordinary tool argument. Home Assistant LLM Tool references cannot use this scheduler.

## What happens if Home Assistant restarts?

Pending delayed calls are saved. If Home Assistant or the integration restarts before a call is due, an eligible pending action can be restored and executed once using the Home Assistant user context that originally scheduled it.

If a write fails and its stored outcome cannot be read, the scheduler pauses.
After storage is repaired, successful setup in the same Home Assistant process
reloads verified pending calls and resumes their timers. Calls that crossed the
durable execution boundary are excluded from recovery.

If Home Assistant stops at a point where the action may already have begun, Extended OpenAI does **not** automatically run it again after restart. This avoids accidentally performing the same real-world action twice when the earlier result is uncertain. In technical terms, delayed execution follows an at-most-once restart boundary.

## Permission is checked again when the action is due

Scheduling a delayed call does not permanently reserve permission to perform it. When the action becomes due, Extended OpenAI checks the current Home Assistant user and current control permissions again, together with the current relevant Extended OpenAI restrictions.

If that action is no longer allowed, it is completed without executing. Restoring permission later does not replay the old rejected item; a new request can schedule a fresh action.

Guest Mode and other current restrictions can therefore make a previously scheduled action unavailable by the time it is due.

## What if the tool or configuration changes?

If the Function Tool has been removed, disabled, or its schema or implementation has changed, the pending action is cancelled. A saved fingerprint binds the call to the definition used when it was scheduled; keeping the same tool name does not authorize a different implementation. Pending calls created before fingerprint support are also cancelled because their original definition cannot be verified. Schedule those actions again after upgrading.

Delayed execution is also coordinated with maintenance operations such as full Backup & Restore, so a due action is not deliberately run while the agent's configuration or policy is only partly restored.

Full agent backups do not contain the delayed-call queue. Restoring an agent on another installation does not migrate its pending jobs; schedule the required actions again on that installation.

## Local handling

Home Assistant may interpret both ordinary timers and delayed device requests through timer-related intents. Under **Capabilities → Home Assistant & local handling**, you can choose to send delayed device commands onward to AI/Function Tools while still allowing ordinary requests such as "set a 20 minute timer" to stay on the local Home Assistant path.

Use delayed Function Tools when the eventual action needs the Function Tool path and its authorization checks; use Home Assistant timers when the request is simply a timer.

### Capacity limits

The scheduler accepts at most 256 persisted calls globally and 32 per agent, including calls already executing. Arguments must be JSON and fit within 16,384 encoded UTF-8 bytes. Rejected requests are neither saved nor armed. Completed or removed calls release capacity; a Home Assistant restart retains pending reservations.

If an older persisted queue exceeds the call-count or argument-size limits, EOAI and Management still start, but the delayed Function Tool scheduler stays disabled. Saved jobs remain unchanged, no jobs are armed, and new delayed requests are rejected. Home Assistant logs the reason and recovery instructions.

To resolve this, remove affected agents through Management (which removes only their saved jobs), or reduce the saved queue while Home Assistant is stopped. Restart Home Assistant after the remaining queue fits all limits to retry recovery. Removing jobs never automatically resumes the scheduler in the same process. Existing jobs are never silently discarded to satisfy capacity limits.
