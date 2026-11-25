# Speech processing

Configure spoken-output cleanup from **Extended OpenAI → Assistant → Speech**.

Speech processing creates a cleaner version of the assistant response for text-to-speech without replacing the original model response stored in conversation history, archives, ChatLog and later conversation context.

## Built-in cleanup

The built-in cleanup can remove content that is useful on screen but awkward when spoken, including:

- Markdown link destinations (readable labels are preserved)
- formatting markers
- bare URLs

It works with Responses, Chat Completions and compatible providers. Cleanup follows links across streamed chunks. When a recognized URL exceeds the streaming buffer, cleanup keeps skipping the URL even if its closing delimiter never arrives.

Link labels such as `Sources`, `References`, and `1` remain spoken. With bare-URL removal enabled, URLs inside a readable label are removed and the remaining words are spoken. Empty labels and address-only citations are suppressed.

An unspaced em dash in a bare URL path can belong to either the address or the surrounding sentence. When it is followed by a short word and whitespace, cleanup keeps that word as prose. Internal en dashes and em dashes in query strings and fragments stay with the URL. Use an explicit Markdown destination, angle brackets, or separating whitespace to make the boundary clear.

Numbered list items keep their numbers for TTS: `2) Check the switch` becomes `2. Check the switch`. Bullet markers are removed. Items containing only removed URLs or citations disappear with their markers, while standalone numerical text such as `5.` or `5)` stays intact. This also applies to streamed replies and nested lists.

Inline code preserves literal operators, URLs, and spacing while its backtick delimiters are removed, including inside link labels and emphasis. Fenced code preserves indentation and internal spacing. Configured replacement rules can deliberately change code content.

With Markdown cleanup enabled, backslashes escaping ASCII punctuation are removed
outside recognized code. The punctuation stays literal: `\*word\*` is spoken as
`*word*`, without treating those stars as emphasis. Other backslashes and a final
unpaired backslash remain. Streaming waits for the character after a trailing
backslash before deciding how to speak it.

Completed cleanup attaches ordinary punctuation to preceding prose, such as
`Hello , world` becoming `Hello, world`. Spaces before escaped punctuation stay:
`off now. \!photo` becomes `off now. !photo`. Custom replacements still run before
final whitespace cleanup. Copied text and regex backreferences retain literal
spacing. New prose punctuation uses normal attachment, and replacements within
code retain code spacing.

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

Because a custom regular expression may depend on text that has not arrived yet, configuring any custom replacement delays speech until the completed answer is ready. Extended OpenAI applies the normal cleanup and the custom replacements before Home Assistant synthesizes the reply. Home Assistant's shared live callback withholds assistant text during that turn, so live visual text also waits for the completed reply.

Custom processing has safety limits. If a saved rule is invalid, takes too long, fails, receives too much input, or produces excessive output, Extended OpenAI falls back to the cleaned speech text from before the custom replacements. It will not speak a partially modified result.

Use **Preview spoken text** in the Speech section to check the completed-response pipeline without making a provider request.

## What speech processing does not change

Speech cleanup does not rewrite the assistant's original response for future model context. It only changes the version intended to be spoken aloud.
