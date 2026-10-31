# Conversation archive, privacy, and voice ownership

The optional conversation archive stores what was actually discussed. It is separate from persistent memory, which stores concise durable facts.

The archive is **off by default**. When enabled, the default retention is 30 days; model archive search, unidentified voice retention, and the shared-household archive are off until explicitly enabled.

## What is stored

Only the user message, the final assistant reply, timestamps, session/run identifiers, success state, and resolved scope/source metadata are stored. A title is normalized and truncated from the first user message locally.

The archive does not separately capture hidden reasoning, intermediate tool messages, tool arguments or results, provider payloads, exposed-entity dumps, web-search bodies, image/PDF files, or attachments. Information quoted in the user message or final assistant reply is still part of that transcript. No extra model call creates titles, summaries, topics, embeddings, classifications, or metadata.

Monthly transcript partitions keep each write bounded. Compact session metadata is stored separately, and lexical search uses normalized phrase matching, token overlap, simple stemming, dates, bounded excerpts, strict result limits, and ownership validation.

## Searching prior discussions

When model archive search is enabled, ask naturally:

> What was that restaurant you recommended last week?
>
> What did we decide about the leaking tap?
>
> What did we say about that Home Assistant problem?
>
> Show me what conversations are retained.

The model is told that archived advice may be outdated or situational and that archive text is not automatically a durable fact. Archive content is not injected into every prompt.

## Private sessions and deterministic deletion

These commands use backend tools; they are not a promise made only in generated text:

> Don't save this conversation.
>
> Make this conversation private.
>
> You can save conversations again.
>
> Delete this conversation.
>
> Delete our conversation about the medical appointment.
>
> Forget everything we discussed today.

Private mode applies only to the exact active session. Enabling it deletes already retained turns and the conversation-derived title for that session, returns the exact turn count, and prevents future turns in that session from being archived. Existing private-session titles are removed when loaded; importing an older backup also removes those titles. Other browser or satellite sessions are unaffected. Resuming saving creates a new retained session for future turns; private messages are not restored or saved retroactively. A request that makes the conversation private is never archived, even if it also resumes saving later in the same turn. This applies when saving was already active and is resumed before making the conversation private.

Archive privacy controls only this integration's transcript retention. It does not stop the active conversation from being sent to the configured provider, disable separate memory features or opt-in Request debugging, or erase copies already exported or recorded elsewhere. Review those settings separately when you do not want information retained.

Single-session deletion is exact. Semantic deletion begins with bounded search and requires selected session IDs. Date-range and whole-scope deletion require explicit confirmation and return exact session and turn counts. Deleting transcript text never changes token aggregates. You can verify the result under **Extended OpenAI → Data & Memory → Conversation history**.

## Voice satellite ownership

An authenticated Home Assistant request user takes precedence for retained-data ownership. Without one, the selected **Unidentified voice requests** policy determines the scope:

- **Use a device assignment** uses the source device's explicit user, shared-household, or unretained assignment. Missing or unavailable user assignments use the configured fallback under **When a voice device is not assigned**.
- **Use the default user** uses the explicitly selected active Home Assistant user.
- **Use shared household data** uses the deliberately shared scope.
- **Don't use saved personal data**, or an unavailable default user, uses an unretained scope with memory writes disabled.

Scope remains stable for a retained archive session. See [Voice Identity](voice-identity.md) for device assignments and continuity boundaries.

The integration never infers identity from presence, room occupancy, phone location, Bluetooth, cameras, or the room containing a satellite.

An explicit mapping can assign each device to a user, the shared household, or unretained. A mapping identifies the configured archive owner, not necessarily the physical speaker. Home Assistant currently supplies a request context user and device ID, but not reliable speaker recognition; installations must treat technical execution users cautiously.

Shared memories remain separate from personal memories. Shared automatic memory is off by default, and the shared archive requires separate consent. Anyone able to use a shared assistant may be able to search or delete its shared archive.

Unidentified requests configured as unretained do not write into the legacy `__anonymous__` memory scope.

## Existing anonymous memories

Existing `__anonymous__` records are preserved as **Legacy anonymous**. They are never silently assigned to an administrator or integration owner. An administrator can inspect them in the Memories scope selector, reassign selected records to a user or shared household with exact counts, leave them in place, or delete them.

## Access and settings

Normal users can manage only their personal memory/archive scope. Home Assistant administrators can select personal, shared, and legacy scopes and manage integration-wide retention and voice mappings. Administrator panel selection never changes the scope available to model-facing tools.

Manage archive retention and model search, and review or delete transcripts, from **Extended OpenAI → Data & Memory → Conversation history**. Configure device ownership under **Assistant → Voice & identity**. Select the intended conversation agent before changing its settings; conversation-agent reconfiguration in **Devices & services** points to this panel.

The integration's completion event now contains metadata only, rather than transcript text. Upgrading does not remove transcripts that older event consumers or Home Assistant Recorder already saved. See [Completion notifications and Recorder](credential-security.md#completion-notifications-and-recorder) for the event fields and upgrade considerations.
