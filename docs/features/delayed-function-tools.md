# Delayed Function Tools

Configured Function Tools can request actions that should happen later, such as turning a device off after a delay. This is separate from Home Assistant's ordinary timer feature: the pending item represents a future Function Tool action.

## What happens if Home Assistant restarts?

Pending delayed calls are saved. If Home Assistant or the integration restarts before a call is due, an eligible pending action can be restored and executed once using the Home Assistant user context that originally scheduled it.

If Home Assistant stops at a point where the action may already have begun, Extended OpenAI does **not** automatically run it again after restart. This avoids accidentally performing the same real-world action twice when the earlier result is uncertain. In technical terms, delayed execution follows an at-most-once restart boundary.

## Permission is checked again when the action is due

Scheduling a delayed call does not permanently reserve permission to perform it. When the action becomes due, Extended OpenAI checks the current Home Assistant user and current control permissions again, together with the current relevant Extended OpenAI restrictions.

If that action is no longer allowed, it is completed without executing. Restoring permission later does not replay the old rejected item; a new request can schedule a fresh action.

Guest Mode and other current restrictions can therefore make a previously scheduled action unavailable by the time it is due.

## What if the tool or configuration changes?

If the Function Tool has been removed, disabled, or changed in a way that makes the pending action invalid, the current configuration is used rather than assuming the old setup still exists.

Delayed execution is also coordinated with maintenance operations such as full Backup & Restore, so a due action is not deliberately run while the agent's configuration or policy is only partly restored.

## Local handling

Home Assistant may interpret both ordinary timers and delayed device requests through timer-related intents. Under **Capabilities → Home Assistant & local handling**, you can choose to send delayed device commands onward to AI/Function Tools while still allowing ordinary requests such as "set a 20 minute timer" to stay on the local Home Assistant path.

Use delayed Function Tools when the eventual action needs the Function Tool path and its authorization checks; use Home Assistant timers when the request is simply a timer.
