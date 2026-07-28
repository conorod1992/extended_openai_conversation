# Voice follow-ups

Voice follow-ups control whether a compatible Home Assistant voice client listens for another utterance immediately after the assistant answers.

This is configured through **Continue conversation** on the conversation agent.

## Modes

### HA Default

Preserves Home Assistant's normal behavior.

This is the safest starting point if you want the integration to behave like another Home Assistant conversation agent without changing follow-up behavior.

### Always

Requests another utterance after every successful response.

This can create a more conversational experience, but it also means the satellite will keep listening even after responses that clearly complete the interaction.

### Conditional

Lets the model decide whether its response expects an immediate reply.

The decision is produced as part of the existing model response rather than by making a second model request. This allows responses such as questions or requests for clarification to continue naturally while completed commands can end without reopening the microphone.

## Client support

The voice client must support Home Assistant's `continue_conversation` signal.

Current ESPHome Assist satellites support this behavior. Older or custom voice clients may ignore the signal even when the conversation agent requests a follow-up.

## Which mode should I choose?

Use **HA Default** when you want the least surprising behavior.

Use **Always** when you intentionally want every interaction to remain open for another utterance.

Use **Conditional** when you want the assistant to behave conversationally without reopening the microphone after every completed request.

## Troubleshooting

If follow-ups do not occur:

1. confirm the conversation agent's Continue conversation setting
2. confirm the voice client supports `continue_conversation`
3. test with a response that clearly asks the user a question
4. compare behavior with **Always** mode to separate model-decision issues from client support issues
