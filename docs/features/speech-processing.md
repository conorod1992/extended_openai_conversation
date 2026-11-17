# Speech processing

Configure spoken-output cleanup from **Extended OpenAI → Assistant → Speech**.

Speech processing creates a cleaner version of the assistant response for text-to-speech without replacing the original model response stored in conversation history, archives, ChatLog and later conversation context.

## Built-in cleanup

The built-in cleanup can remove content that is useful on screen but awkward when spoken, including:

- Markdown link destinations (readable labels are preserved)
- formatting markers
- bare URLs

It works with Responses, Chat Completions and compatible providers. It also keeps track of text across streamed chunks, so a Markdown link or URL split across several pieces can still be removed before it is spoken.

Link labels such as `Sources`, `References`, and `1` remain spoken. Only empty labels and labels that are themselves URLs or addresses are suppressed; cleanup does not guess whether a label is a citation.

Numbered list items keep their numbers for TTS: `2) Check the switch` becomes `2. Check the switch`. Bullet markers are removed. Items containing only removed URLs or citations disappear with their markers, while standalone numerical text such as `5.` or `5)` stays intact. This also applies to streamed replies and nested lists.

Inline code preserves literal operators and URLs while its backtick delimiters are removed, including when enclosed in emphasis. Final whitespace cleanup preserves parentheses such as `call()`. Configured replacement rules can deliberately change this content.

With Markdown cleanup enabled, backslashes escaping ASCII punctuation are removed
outside recognized code. The punctuation stays literal: `\*word\*` is spoken as
`*word*`, without treating those stars as emphasis. Other backslashes and a final
unpaired backslash remain. Streaming waits for the character after a trailing
backslash before deciding how to speak it.

Completed cleanup attaches ordinary punctuation to preceding prose, such as
`Hello , world` becoming `Hello, world`. Spaces before escaped punctuation stay:
`off now. \!photo` becomes `off now. !photo`. Custom replacements still run before
final whitespace cleanup. Copied text and regex backreferences retain this
literal spacing; newly written replacement punctuation uses normal attachment.

Short, contiguous `=` Setext underlines after plain top-level heading text are
removed. Standalone equals runs, spaced `= = =`, operators, escapes, and code stay
literal. Recognition is deliberately bounded to 32 characters per underline and
does not cover headings in list/quote containers or after complex inline syntax.

Backtick block fences omit their opening info string, such as `yaml`, at the
start of an original source line (with up to three spaces), after list markers,
under list continuation indentation, and inside blockquotes. Quoted code loses
only the recognized outer quote prefix; operators such as `>` and `>>`, numeric
lines, URLs and Markdown-looking code remain protected. Indented text outside
lists does not gain fence recognition from formatting or URL removal.
Recognition waits only for a bounded opener line; unfinished or oversized
candidates keep their literal info text. Markdown tables currently retain their
pipe and delimiter syntax.

Home Assistant currently uses the same progressive stream for live visual updates and TTS. This means the live on-screen stream also receives the speech-safe text, while the original model response is still retained for history and future conversation context.

For Responses API replies, structured URL citation information is still retained with the original response even when the spoken version is cleaned.

## Custom replacements

Advanced replacement rules use Python regular expressions and run in order after the full response is complete. Built-in Markdown and bare-URL cleanup runs first, so replacements see the cleaned text. Turn off **Remove bare URLs** if a custom rule needs to match a URL.

```yaml
- pattern: '\[[0-9]+\]'
  replacement: ''
- pattern: '\bHA\b'
  replacement: 'Home Assistant'
```

Because a custom regular expression may depend on text that has not arrived yet, configuring any custom replacement disables progressive TTS for that response. Extended OpenAI waits for the completed answer, applies the normal cleanup, and then runs the custom replacements.

Custom processing has safety limits. If a saved rule is invalid, takes too long, fails, receives too much input, or produces excessive output, Extended OpenAI falls back to the cleaned speech text from before the custom replacements. It will not speak a partially modified result.

Use **Preview spoken text** in the Speech section to check the completed-response pipeline without making a provider request.

## What speech processing does not change

Speech cleanup does not rewrite the assistant's original response for future model context. It only changes the version intended to be spoken aloud.
