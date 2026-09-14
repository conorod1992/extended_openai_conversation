# Speech processing

Configure spoken-output cleanup from **Extended OpenAI → Assistant → Speech**.

Speech processing creates a TTS-safe version of the assistant response without replacing the original provider text stored in conversation history, archives, ChatLog and request context.

## Built-in cleanup

The built-in sanitizer can remove content that is useful on screen but awkward when spoken, including:

- Markdown links and citation-style text
- formatting markers
- bare URLs

The sanitizer is provider-neutral and works with Responses, Chat Completions and compatible-provider streams. It is stateful across streaming deltas, so a Markdown link or URL split across multiple provider chunks can still be removed before it reaches progressive TTS.

Home Assistant currently uses a shared progressive listener for visual streaming and TTS. As a result, live visual deltas receive the same speech-safe progressive text, while the original provider response remains retained for history and later conversation context.

Responses API URL-citation annotations remain attached to the native response item for replay/context even though their spoken representation is cleaned.

## Custom replacements

Advanced replacement rules use Python regular expressions and run in order on the **completed** response.

```yaml
- pattern: '\\[[0-9]+\\]'
  replacement: ''
- pattern: '\\bHA\\b'
  replacement: 'Home Assistant'
```

Arbitrary regular expressions may depend on text that has not arrived yet, so configuring any custom replacement disables progressive TTS for that response. The completed response is cleaned first, then custom replacements are applied.

Custom processing is bounded and isolated. Invalid saved rules, timeouts, worker failures, oversized input or excessive output growth cause the custom-replacement stage to fail open atomically to the speech text from before custom replacements. A partial replacement result is not spoken.

Use **Preview spoken text** in the Speech section to check the completed-response pipeline without making a provider request.

## What speech processing does not change

Speech cleanup does not rewrite the assistant's canonical response for future model context. It is an output presentation layer for spoken text, not a second conversation-history format.
