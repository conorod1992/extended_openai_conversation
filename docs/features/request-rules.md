# Request Rules

Request Rules examine text received by Extended OpenAI Conversation before its normal provider request. They provide deterministic local voice shortcuts and AI-routing commands without asking a model to interpret the trigger.

Open **Extended OpenAI > Capabilities > Request Rules**. Rules, matching defaults, and wording alternatives are stored locally per conversation agent and included in that agent's backup.

Request Rules only see text that Home Assistant routes to this conversation agent. If a native intent or sentence-trigger automation handles a sentence first, the rule is not involved.

## Build a rule

The editor follows the way a command is usually designed:

1. Enter what you will say and choose how it matches.
2. Choose what should happen.
3. Choose what the assistant should say.
4. Open **Advanced matching and action configuration** only when the defaults or friendly action fields are not enough.

## Local commands

A **Local command** runs one or more Home Assistant actions or enabled ExtendedOpenAI functions in order and does not make an OpenAI/API call. Select an action by its `domain.action` name, then choose an entity, device, or area through Home Assistant's native selectors. Fields published by the selected service, such as brightness, temperature, media, or a select option, appear as friendly controls when Home Assistant provides selector metadata.

Existing `target` and `data` values are preserved when a rule is edited. **Advanced JSON** is a lossless fallback for service data or target keys that the friendly editor does not expose.

If any action fails, the remaining actions do not run and the configured failure response is returned. While Guest Mode is active, the entire sequence is authorized before the first action runs; if one action is unavailable, none run.

### Example: fast local script

- Phrase: `good night`
- Match: **Equals**
- Action: `script.turn_on` targeting the Goodnight script
- Response: `Good night`

## AI routing

An **AI routing** rule keeps the provider pipeline but can override the model, reasoning effort, or both. Its scope can be **This request only** or **Rest of this conversation**:

`single-request override > conversation override > configured agent default`

Conversation overrides use the current conversation/continuity identity and expire with it. They do not change the saved agent configuration. A rule can also reset the active conversation to configured defaults.

Equals and sentence-pattern routing commands are complete commands and are acknowledged locally. They must use conversation scope. Broader matches keep the original request wording unchanged; the matched prefix or substring is not removed.

## Matching modes

### Text matching

**Equals**, **Starts with**, **Ends with**, and **Contains** are case-insensitive and normalize punctuation and whitespace. Rules can inherit the defaults or customize:

- **Normalize word forms** handles conservative English forms such as `light/lights`.
- **Wording alternatives** map different ways of saying the same thing to a main phrase. The seeded alternatives preserve the previous built-in behavior, such as `switch on` to `turn on` and `television` to `tv`. Alternatives can be added, edited, and removed. Ambiguous duplicate phrases are rejected.
- **Fuzzy matching** tolerates small speech-recognition differences only after strict matching fails. Conservative, Normal, and Tolerant correspond to progressively lower thresholds. Sensitivity is unavailable when fuzzy matching is off.

Strict matching always wins over fuzzy matching. More specific strict types win over broader ones, and stable rule order resolves an otherwise equal result.

### ExtendedOpenAI sentence patterns

Choose **ExtendedOpenAI sentence pattern** when parts of a command can vary. This is a small syntax owned by ExtendedOpenAI; it is not a promise of compatibility with Home Assistant/Hassil grammar. Patterns are parsed and compiled when rules are loaded or saved, then matched by a bounded finite-state matcher.

Sentence patterns are case-insensitive but otherwise use their own exact grammar path. Fuzzy matching, wording alternatives, and word-form normalization do not apply.

Literal spelling and constrained choices use the same Unicode and whitespace normalization as the input. Sentence-ending punctuation (`.`, `!`, `?`, `。`, and `؟`, including fullwidth equivalents) may follow a complete command. Punctuation explicitly written in a pattern still has to match; punctuation in the middle of a command is literal.

Supported syntax:

| Syntax | Meaning | Example |
| --- | --- | --- |
| plain text | Required literal text | `turn the light off` |
| `[text]` | Optional text | `turn [the] light off` |
| `[one|two]` | Optional alternatives | `[please|kindly] turn it off` |
| `(one|two)` | Required alternatives | `(turn|switch) the light off` |
| `{name}` | Non-empty free-text capture | `remember {fact}` |
| `{name=one|two}` | Constrained capture | `set {room=kitchen|bedroom} lights on` |
| `{name=min..max}` | Integer capture in an inclusive range | `set brightness to {level=0..100}` |
| `\` | Escape a syntax character so it is literal | `say \(hello\)` |

Optional and alternative blocks may be embedded inside a word, for example `light[s]` matches both `light` and `lights`.

A constrained capture both validates and captures the selected value. For example:

```text
[please ](turn|switch) {room=kitchen|bedroom|office} light[s] on
```

`please switch bedroom lights on` captures `room = bedroom`. A value outside the listed choices does not match.

Escapes also work inside constrained choices: `{value=a\|b|c}` accepts the literal values `a|b` and `c`, not separate `a` and `b` choices. Choices that become identical after Unicode, whitespace, and case normalization are rejected.

Numeric captures accept integer digits (including a leading `+` or `-` where the configured range allows it) and validate the value without expanding the range into thousands of alternatives:

```text
set brightness to {level=0..100}
```

`set brightness to 73` captures `level = 73`; `set brightness to 173` does not match.

Free-text captures may contain multiple words. The matcher chooses the shortest capture that still allows the complete pattern to succeed. For example:

```text
add {item} to {list_name}
```

`add semi skimmed milk to weekly shopping` captures `item = semi skimmed milk` and `list_name = weekly shopping`.

Free-text captures must be separated by required literal or constrained text. Ambiguous patterns such as `do {first} {second}` are rejected. A pattern also needs at least one required literal, constrained capture, or numeric capture, so a bare `{anything}` cannot intercept every request.

All phrase variants in one rule must expose the same capture names. Captured values can be selected as **Value from request** in supported action/function fields or inserted into responses with simple `{name}` substitution. This substitution is deterministic and does not execute Jinja.

Named expansion syntax such as `<device>`, Hassil permutations using `;`, predefined Hassil slot lists, arbitrary regular expressions, lookarounds, backreferences, and repetition operators are not part of the ExtendedOpenAI sentence-pattern language.

### Matching bounds

Sentence-pattern matching is deliberately bounded. The grammar is compiled to a finite-state representation instead of recursively backtracking through every optional/alternative combination, so repeated optional terms do not create exponential work.

The current safety limits are:

- 200 characters per saved phrase;
- 25 phrase variants per rule;
- 500 Request Rules per agent;
- 8 levels of sentence-pattern nesting;
- 8 captures per pattern, of which at most 4 may be free-text captures;
- 32 choices per constrained capture, with each choice limited to 80 characters;
- 512 compiled states per pattern;
- 50,000 enabled compiled sentence-pattern states per agent;
- 2,048 characters and 256 words in a live or Match Preview input, checked before and (for sentence matching) after Unicode normalization and case folding; and
- a shared aggregate matcher-work budget across every sentence pattern considered for one request.

The same parser, compiled matcher, input bounds, winner-selection rules, and aggregate work budget are used by live requests and Match Preview. Matching itself runs outside Home Assistant's main event loop. Each match reads one complete configuration snapshot, even if rules or defaults are being saved concurrently. Compiled patterns are immutable and reused through a bounded cache.

Candidates are evaluated in the existing precedence order. Once a match is certain to win, lower-ranked patterns are skipped. An unresolved candidate that could change the winner still causes the whole evaluation to fail safely if it exceeds the work budget.

If a live request is larger than the matching limit or the aggregate work budget cannot safely complete, **no Request Rule action runs** and the original request continues through the normal AI path. The matcher never treats an interrupted higher-priority rule as a failed match and then executes a lower-priority local action. Match Preview instead reports the limit as an error.

If an existing stored sentence pattern is no longer accepted by the documented grammar or its safety bounds, it is preserved rather than silently deleted. The Request Rules screen marks that rule inactive with an actionable diagnostic so it can be edited or removed.

Inactive legacy rules can be disabled and repaired individually without blocking unrelated changes. Backups preserve them too; restoring a backup applies the same activation limits and diagnostics as loading stored rules. New or re-enabled rules must satisfy the current grammar and limits.

## Function actions

A rule can call an enabled Function Tool directly without sending the request to the AI provider. The function selector comes from the current agent's configured tools. Selecting one shows common string, number, integer, boolean, enum, and simple-array inputs from its existing schema. Each input can be a fixed value or a captured request value.

For example, use `Show {entity_id} attributes`, select the existing `get_attributes` function, and set `entity_id` to **Value from request → entity_id**. Direct execution uses the same implementation, argument validation, current enabled state, Guest Mode policy, and entity-access checks as a model-initiated call.

## Request Rules compared with native automations

Use a Request Rule when:

- the phrase belongs specifically to this Extended OpenAI conversation agent;
- a stable command should bypass the AI/API call;
- the phrase should change model or reasoning routing; or
- you want the rule and its response managed with the agent.

Use a native Home Assistant sentence-trigger automation when:

- the command should work independently of this integration or conversation agent;
- it needs automation triggers, conditions, templates, variables, traces, or modes;
- it should be owned alongside the rest of your Home Assistant automations; or
- native Assist handling should take priority before text reaches an AI agent.

The two approaches can coexist, but their sentence-pattern languages are separate. Avoid giving both the same effective phrase unless their routing priority is intentional.

## Security and limits

Request Rules are not semantic understanding and do not learn from history. They do not use embeddings, a general thesaurus, automatic suggestions, or speaker authentication. Use Equals or a narrow sentence pattern for locks, alarms, garage-style covers, and other sensitive actions. The editor warns about obvious sensitive action domains when tolerant text matching is active.

Home Assistant service validation still applies. Guest Mode authorization uses the same backend enforcement as model-initiated Home Assistant tools and never partially executes a rejected sequence.
