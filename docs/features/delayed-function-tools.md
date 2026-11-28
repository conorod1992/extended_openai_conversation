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

For example, `"delay": {"minutes": 10}` schedules the configured operation for later. The integration removes this scheduling object before passing the other arguments to the implementation. With the example schema, omit `delay` or use zero for an immediate tool call. If a different schema explicitly allows `null`, a null duration field counts as omitted. Requests recognised as delayed commands and routed to EOAI still require a positive delay. A non-object parameter named `delay` remains an ordinary tool argument. Home Assistant LLM Tool references cannot use this scheduler.

## What happens if Home Assistant restarts?

Pending delayed calls are saved. If Home Assistant or the integration restarts before a call is due, an eligible pending action can be restored and executed once using the Home Assistant user context that originally scheduled it.

If a write fails and its stored outcome cannot be read, the scheduler pauses.
After storage is repaired, successful setup in the same Home Assistant process
reloads verified pending calls and resumes their timers. Calls that crossed the
durable execution boundary are excluded from recovery.

If Home Assistant stops at a point where the action may already have begun, Extended OpenAI does **not** automatically run it again after restart. This avoids accidentally performing the same real-world action twice when the earlier result is uncertain. In technical terms, delayed execution follows an at-most-once restart boundary.

## Overdue actions and clock changes

New delayed calls expire 15 minutes after their scheduled UTC time. Calls still
pending at or after that deadline are cancelled without execution, including
after downtime, agent retries, or maintenance waits. If storage cannot persist
the cancellation, it is retried; expired work is never executed as a fallback.
The deadline is saved with each job and does not change on restart or tool edits.

For actions that should still be attempted after a long outage, set
`delay_catch_up: true` at the top level of that Function Tool's YAML, alongside
`spec`, `function`, and `enabled`. This administrator-controlled option applies
to new jobs only and preserves eventual execution subject to the normal
validation and bounded agent-reload retries. It is not a model argument and does
not change the function definition fingerprint. Omitted or false uses the
15-minute grace period. Existing saved jobs without an expiry deadline keep
their original eventual-execution semantics.

Waiters resample the wall clock at least every 30 seconds and recheck it after
maintenance and authorization waits, immediately before persisting execution.
Backward clock corrections postpone execution; forward corrections make due or
expired work eligible for processing within the bounded polling interval.

## Permission is checked again when the action is due

Scheduling a delayed call does not permanently reserve permission to perform it. When the action becomes due, Extended OpenAI checks the current Home Assistant user and current control permissions again, together with the current relevant Extended OpenAI restrictions.

If that action is no longer allowed, it is completed without executing. Restoring permission later does not replay the old rejected item; a new request can schedule a fresh action.

Guest Mode and other current restrictions can therefore make a previously scheduled action unavailable by the time it is due.

## What if the tool or configuration changes?

If the Function Tool has been removed, disabled, or its schema or implementation has changed, the pending action is cancelled. A saved fingerprint binds the call to the definition used when it was scheduled. Keeping the same tool name does not authorize a different implementation.

The current fingerprint preserves authored value and key types and mapping order. Pending calls saved with the earlier fingerprint, or without a fingerprint, remain in the queue until their normal expiry or due-time validation. They are discarded before execution because their original definitions cannot be verified. Schedule any still-needed actions again after upgrading. A downgrade likewise rejects calls saved with the newer fingerprint.

Delayed execution is also coordinated with maintenance operations such as full Backup & Restore, so a due action is not deliberately run while the agent's configuration or policy is only partly restored.

Agent backups omit the delayed-call queue. After restoring an agent on another installation, schedule any actions you still need on the destination.

## Review and cancel pending actions

Open **Extended OpenAI → Capabilities → Scheduled Actions**, then select an assistant. Administrators can review and cancel its pending EOAI actions even during a conversation entity reload. Times use Home Assistant's configured time zone. Refresh to see recent changes. If a cancellation could not be confirmed, refresh before retrying. An action that has already started is no longer eligible for cancellation.

You can also ask the assistant “What actions are scheduled?” or “Cancel the scheduled command for the living room lights.” The assistant can use an exact action ID from the list or an earlier result. If several actions match, it should ask which one you mean. A successful scheduling result includes the saved ID and execution time. EOAI reserves the tool names `list_scheduled_actions` and `cancel_scheduled_action`. If a custom Function Tool used either name, rename it through the Functions repair view.

Signed-in callers can manage their own actions for the current assistant. Unsigned voice requests can manage unsigned jobs from the same identifiable Assist satellite or device. Anyone using that same voice origin shares this access. Voice Identity assignments for memory and archives do not change it. Requests without an identifiable origin and conversations in Guest Mode have no scheduled action access. The model needs tool support, room for the scheduling tools and an available tool call. Administrators can use **Scheduled Actions** to review or cancel pending actions when voice management is unavailable.

The page and the assistant use the same summaries. Native service calls can show service and visible entity identifiers, permitted area, device, floor, or label names, and selected values such as brightness or target temperature. Summaries flag omitted targets. Other tools use their names. Pending actions stay manageable after the underlying tool is removed or disabled, until the scheduler removes them under its normal rules. Assistant duplication starts with an empty queue. Deleting an assistant removes its queued work.

## Choose how delayed commands are routed

Home Assistant handles ordinary timers such as “set a 20-minute timer” and can also schedule native delayed conversation commands such as “turn off the lights in 20 minutes.” EOAI scheduled actions belong to its Function Tool queue. The Scheduled Actions page and conversation tools manage only that queue.

Under **Capabilities → Home Assistant & local handling**, enable **Use EOAI for delayed actions** to route delayed commands that reach EOAI to its Function Tools. An ordinary timer still uses Home Assistant. On the AI path, the preference blocks the native timer's delayed-command argument and Home Assistant's dedicated Schedule command tool on versions that provide it. This applies even when EOAI local handling is off. The setting retains its previous saved value and defaults to off for new assistants. Changing it does not change any already queued jobs.

Enable a suitable Function Tool and check that its saved input schema accepts `delay`. The **Execute services** editor preset does not include `delay`; add the property shown above if it is missing. Save both settings, try a delayed command with a test device, and verify that it appears in **Scheduled Actions**. If Home Assistant recognises the utterance as a delayed command, EOAI requires a positive delay before the requested action can execute. Indirect wording and follow-ups such as “actually, do that in 20 minutes” can fall back to model interpretation. If the model interprets the request as a delayed action, it should use a positive delay or explain that it cannot schedule the action. Ordinary requests use their usual tools. For an Assist pipeline to apply the EOAI preference consistently, turn off Home Assistant's pipeline-level **Prefer local handling** so requests reach EOAI. Existing Request Rules still run first.

### Capacity limits

The scheduler accepts at most 256 persisted calls globally and 32 per agent, including calls already executing. Arguments must be JSON and fit within 16,384 encoded UTF-8 bytes. Rejected requests are neither saved nor armed. Completed or removed calls release capacity; a Home Assistant restart retains pending reservations.

If an older persisted queue exceeds the call-count or argument-size limits, EOAI and Management still start, but the delayed Function Tool scheduler stays disabled. Saved jobs remain unchanged, no jobs are armed, and new delayed requests are rejected. Home Assistant logs the reason and recovery instructions.

To resolve this, remove affected agents through Management (which removes only their saved jobs), or reduce the saved queue while Home Assistant is stopped. Restart Home Assistant after the remaining queue fits all limits to retry recovery. Removing jobs never automatically resumes the scheduler in the same process. Existing jobs are never silently discarded to satisfy capacity limits.
