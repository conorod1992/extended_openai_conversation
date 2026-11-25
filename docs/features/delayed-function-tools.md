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

For example, `"delay": {"minutes": 10}` schedules the configured operation for later. The integration removes this scheduling object before passing the other arguments to the implementation. A recognized delay containing only zero, null, or omitted duration fields executes immediately and returns the actual result or error. A non-object parameter named `delay` remains an ordinary tool argument. Home Assistant LLM Tool references cannot use this scheduler.

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

Full agent backups do not contain the delayed-call queue. Restoring an agent on another installation does not migrate its pending jobs; schedule the required actions again on that installation.

## Review and cancel pending actions

Open **Extended OpenAI → Capabilities → Scheduled Actions**, then select an assistant. Administrators can review and cancel its pending EOAI actions even during a conversation entity reload. Times use Home Assistant's configured time zone. Refresh to see recent changes. If a cancellation could not be confirmed, refresh before retrying. An action that has already started is no longer eligible for cancellation.

You can also ask the assistant “What actions are scheduled?” or “Cancel the scheduled command for the living room lights.” The assistant can use an exact action ID from the list or an earlier result. If several actions match, it should ask which one you mean. A successful scheduling result includes the saved ID and execution time. EOAI reserves the tool names `list_scheduled_actions` and `cancel_scheduled_action`. If a custom Function Tool used either name, rename it through the Functions repair view.

Signed-in callers can manage their own actions for the current assistant. Unsigned voice requests can manage unsigned jobs from the same identifiable Assist satellite or device. Anyone using that same voice origin shares this access. Voice Identity assignments for memory and archives do not change it. Requests without an identifiable origin and conversations in Guest Mode have no scheduled action access. If the provider has no room for both tools, use Management to inspect and cancel actions.

The page and the assistant use the same summaries. Native service calls can show service and visible entity identifiers. Other tools use their names. Tool arguments such as messages and credentials are excluded. Pending actions stay manageable after the underlying tool is removed or disabled, until the scheduler removes them under its normal rules. Assistant duplication starts with an empty queue. Deleting an assistant removes its queued work.

## Choose how delayed commands are routed

Home Assistant handles ordinary timers such as “set a 20-minute timer” and can also schedule native delayed conversation commands such as “turn off the lights in 20 minutes.” EOAI scheduled actions belong to its Function Tool queue. The Scheduled Actions page and conversation tools manage only that queue.

Under **Capabilities → Home Assistant & local handling**, enable **Use EOAI for delayed actions** to route delayed commands that reach EOAI to its Function Tools. An ordinary timer still uses Home Assistant. On the AI path, the preference blocks the native timer's delayed-command argument and Home Assistant's dedicated Schedule command tool on versions that provide it. This applies even when EOAI local handling is off. The setting retains its previous saved value and defaults to off for new assistants. Changing it does not change any already queued jobs.

Enable a suitable Function Tool with the documented `delay` parameter. If Home Assistant recognises the utterance as a delayed command, EOAI requires a positive delay before it allows the requested action to execute. For other wording, the model is instructed to use a positive delay and explain when it cannot schedule the action. For an Assist pipeline to apply the EOAI preference consistently, turn off Home Assistant's pipeline-level **Prefer local handling** so requests reach EOAI. Existing Request Rules still run first.

### Capacity limits

The scheduler accepts at most 256 persisted calls globally and 32 per agent, including calls already executing. Arguments must be JSON and fit within 16,384 encoded UTF-8 bytes. Rejected requests are neither saved nor armed. Completed or removed calls release capacity; a Home Assistant restart retains pending reservations.

If an older persisted queue exceeds the call-count or argument-size limits, EOAI and Management still start, but the delayed Function Tool scheduler stays disabled. Saved jobs remain unchanged, no jobs are armed, and new delayed requests are rejected. Home Assistant logs the reason and recovery instructions.

To resolve this, remove affected agents through Management (which removes only their saved jobs), or reduce the saved queue while Home Assistant is stopped. Restart Home Assistant after the remaining queue fits all limits to retry recovery. Removing jobs never automatically resumes the scheduler in the same process. Existing jobs are never silently discarded to satisfy capacity limits.
