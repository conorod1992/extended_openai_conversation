# Local handling

Extended OpenAI can try Home Assistant's built-in command handling after Request Rules and before sending a request to Function Tools or the model. The independently enabled [Broadcast shortcut](broadcast.md#built-in-voice-command) runs between Request Rules and general local handling. Configure it under **Extended OpenAI → Capabilities → Home Assistant & local handling** for the selected agent.

When enabled, a matching built-in command is handled locally. Requests that do not match, or command types you exclude, continue to Function Tools and AI. The command-type list is supplied by Home Assistant and may vary with the installed intents.

EOAI always excludes `HassBroadcast` from generic delegation. The simple `Broadcast {message}` command has its own global Overview switch. A saved legacy `HassBroadcast` exclusion also suppresses that shortcut for its agent; it is retained as an unavailable choice until explicitly removed. Turning general local handling off does not turn the independent Broadcast shortcut off.

## Delayed device commands

You can route delayed device commands such as “turn off the lights in 20 minutes” to AI/Function Tools while leaving ordinary timers such as “set a 20 minute timer” on Home Assistant's local path. See [Delayed Function Tools](delayed-function-tools.md).

## Home Assistant pipeline setting

Home Assistant's own **Prefer local handling** option runs before a request reaches Extended OpenAI. If it handles a command first, Extended OpenAI cannot apply Request Rules or choose that command for a Function Tool. The management UI reports pipeline conflicts where available. Adjust the pipeline setting if you want Extended OpenAI to control the order.

Extended OpenAI local handling applies only after the request reaches the integration. It does not change Home Assistant's native pipeline behavior.
