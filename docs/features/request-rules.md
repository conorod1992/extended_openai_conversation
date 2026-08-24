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

### Home Assistant sentence patterns

Choose **Home Assistant sentence pattern** for Hassil grammar. Supported syntax is:

- `[optional words]`
- `(first|second)` alternatives
- `{slot}` wildcard capture

For example:

```text
[please ](turn|switch) {room} lights on
```

This matches phrases such as `turn kitchen lights on` and captures `room = kitchen`. Choose **Value from request** for supported action/function fields, or include `{room}` in the local response. Text substitution is deterministic and does not execute Jinja.

## Function actions

A rule can call an enabled Function Tool directly without sending the request to the AI provider. The function selector comes from the current agent's configured tools. Selecting one shows common string, number, integer, boolean, enum, and simple-array inputs from its existing schema. Each input can be a fixed value or a captured request value.

For example, use `Show {entity_id} attributes`, select the existing `get_attributes` function, and set `entity_id` to **Value from request → entity_id**. Direct execution uses the same implementation, argument validation, current enabled state, Guest Mode policy, and entity-access checks as a model-initiated call.

Named expansion references such as `<device>` are intentionally rejected because Request Rules do not configure a named-expansion catalogue. Sentence-pattern matching is a separate exact grammar path: fuzzy matching, wording alternatives, and word-form normalization do not apply.

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

The two approaches can coexist, but avoid giving both the same phrase unless their routing priority is intentional.

## Security and limits

Request Rules are not semantic understanding and do not learn from history. They do not use embeddings, a general thesaurus, automatic suggestions, or speaker authentication. Use Equals or a narrow sentence pattern for locks, alarms, garage-style covers, and other sensitive actions. Configure **Protected Actions** separately when a matching local action should require confirmation or a locally verified PIN. The editor warns about obvious sensitive action domains when tolerant text matching is active.

Home Assistant service validation still applies. Guest Mode authorization uses the same backend enforcement as model-initiated Home Assistant tools and never partially executes a rejected sequence.
