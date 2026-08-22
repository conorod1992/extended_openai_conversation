# Request Rules

Request Rules examine text received by Extended OpenAI Conversation before its normal
OpenAI request. They provide one integration-owned place for voice shortcuts and
request routing that is often simpler than maintaining a separate Home Assistant
sentence-trigger automation for every Extended OpenAI-specific command.

Open **Extended OpenAI > Capabilities > Request Rules** to manage them. Rules are
stored locally per conversation agent. They do not replace Home Assistant's native
intents or Hassil sentences: if Home Assistant handles a sentence before it reaches
Extended OpenAI, Request Rules are not involved.

## Local commands

A **Local command** runs one or more configured Home Assistant actions in order and
does not make an OpenAI/API call. The configured success response defaults to
`Done`. If an action fails, the rule stops, logs diagnostic detail, and returns the
configured failure response instead of claiming success.

### Example: fast local script

- Phrase: `good night`
- Match: **Equals**
- Action: run `script.goodnight`
- Result: handled locally without an OpenAI request

Local commands are useful for established household routines where deterministic,
low-latency behavior is more valuable than model interpretation.

## AI routing

An **AI routing** rule keeps the existing OpenAI request pipeline but can override the
model, reasoning effort, or both. Its scope can be **This request only** or **Rest of
this conversation**. Precedence is:

`single-request override > conversation override > configured agent default`

Conversation overrides use the integration's existing conversation/continuity
identity. They remain in memory for that active conversation, do not change saved
agent settings, and are not used by a genuinely new conversation. A routing rule can
also reset the current conversation to configured defaults.

### Example: heavier reasoning

- Phrase: `think carefully`
- Match: **Starts with**
- Action: model `gpt-5`, reasoning **High**
- Scope: **This request only**

The original request text remains unchanged. Request Rules do not strip the matched
prefix in this version.

### Example: conversation model change

- Phrase: `use the better model`
- Match: **Equals**
- Action: switch model/reasoning
- Scope: **Rest of this conversation**

Because an Equals routing command contains no separate substantive request, it is
acknowledged locally instead of causing a pointless model call.

## Matching

Each rule can have several trigger phrases and supports **Equals**, **Starts with**,
**Ends with**, and **Contains**. Matching is case-insensitive and always normalizes
punctuation and whitespace.

The default matching settings optionally add three predictable layers:

1. **Word-form normalization** handles conservative English forms such as
   `light/lights` and `reminder/reminders`.
2. **Common wording alternatives** use a small curated phrase list, including
   `turn on/switch on`, `turn off/switch off`, `close/shut`, `television/TV`,
   `increase/raise/turn up`, and `decrease/lower/turn down`.
3. **Fuzzy matching** is a fallback for small speech-recognition differences. The
   Conservative, Normal, and Tolerant labels map to progressively lower thresholds.

Most rules use the global defaults. Choose **Customize for this rule** to change word
forms, wording alternatives, fuzzy matching, and sensitivity for one rule only.

Matching is deterministic. A strict match always wins over a fuzzy match, Equals
wins over broader match types for the same utterance, and only one enabled rule is
selected. Within otherwise equivalent matches, rule order is stable.

## Limitations and security

Request Rules are lightweight text matching, not semantic understanding. They do not
use embeddings, a transformer, a general thesaurus, regex triggers, history learning,
or automatic rule creation. Fuzzy and wording-alternative matching can broaden what
activates a rule; use Equals and conservative matching for locks, alarms, garage-style
covers, and other sensitive actions. The editor highlights obvious sensitive domains
when tolerant options are active.

Request Rules do not add PIN protection or speaker authentication. Home Assistant
service validation still applies, and local actions use a shared execution seam so a
future protected-action authorization gate can cover both local rules and model tool
execution. Expose and configure only actions appropriate for everyone who can speak
to the selected Assist pipeline.

Automatic **Suggested Local Commands** are intentionally not included. Local actions
do store a stable canonical action signature so a future suggestion feature can
compare successful model-executed Home Assistant actions without redesigning the rule
format.
