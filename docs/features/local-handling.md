# Local handling

Extended OpenAI can try Home Assistant's built-in command handling after Request Rules and before sending a request to Function Tools or the model. Configure it under the agent's **Configuration → Local handling** section in the dedicated Extended OpenAI management UI.

When enabled, a matching built-in command is handled locally. Requests that do not match, or command types you exclude, continue to Function Tools and AI. The command-type list is supplied by Home Assistant and may vary with the installed intents.

## Delayed device commands

You can route delayed device commands such as “turn off the lights in 20 minutes” to AI/Function Tools while leaving ordinary timers such as “set a 20 minute timer” on Home Assistant's local path. See [Delayed Function Tools](delayed-function-tools.md).

## Home Assistant pipeline setting

Home Assistant's own **Prefer local handling** option runs before a request reaches Extended OpenAI. If it handles a command first, Extended OpenAI cannot apply Request Rules or choose that command for a Function Tool. The management UI reports pipeline conflicts where available. Adjust the pipeline setting if you want Extended OpenAI to control the order.

Extended OpenAI local handling applies only after the request reaches the integration. It does not change Home Assistant's native pipeline behavior.
