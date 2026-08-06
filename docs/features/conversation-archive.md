# Conversation archive, privacy, and voice ownership

The optional conversation archive stores what was actually discussed. It is separate from persistent memory, which stores concise durable facts.

The archive is **off by default**. When enabled, the default retention is 30 days; model archive search, unidentified voice retention, and the shared-household archive are off until explicitly enabled.

## What is stored

Only the user message, the final assistant reply, timestamps, session/run identifiers, success state, and resolved scope/source metadata are stored. A title is normalized and truncated from the first user message locally.

The archive never stores hidden reasoning, intermediate tool messages, tool arguments or results, provider payloads, exposed-entity dumps, web-search bodies, image/PDF contents, or attachments. No extra model call creates titles, summaries, topics, embeddings, classifications, or metadata.

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

Private mode applies only to the exact active session. Enabling it deletes already retained turns for that session, returns the exact count, and prevents future turns in that session from being archived. Other browser or satellite sessions are unaffected. Resuming saving creates a new retained session; private messages are not restored or saved retroactively.

Single-session deletion is exact. Semantic deletion begins with bounded search and requires selected session IDs. Date-range and whole-scope deletion require explicit confirmation and return exact session and turn counts. Deleting transcript text never changes token aggregates. You can verify the result under **Extended OpenAI → Conversations**.

## Voice satellite ownership

Scope is resolved once when the session starts and remains stable:

1. authenticated Home Assistant request user
2. a future supported speaker identity, if Home Assistant exposes one
3. explicit satellite/device mapping
4. selected agent-wide default voice owner
5. configured shared-household scope
6. unretained, with memory writes disabled

The integration never infers identity from presence, room occupancy, phone location, Bluetooth, cameras, or the room containing a satellite.

An explicit mapping can assign each device to a user, the shared household, or unretained. A mapping identifies the configured archive owner, not necessarily the physical speaker. Home Assistant currently supplies a request context user and device ID, but not reliable speaker recognition; installations must treat technical execution users cautiously.

Shared memories remain separate from personal memories. Shared automatic memory is off by default, and the shared archive requires separate consent. Anyone able to use a shared assistant may be able to search or delete its shared archive.

Unidentified requests configured as unretained do not write into the legacy `__anonymous__` memory scope.

## Existing anonymous memories

Existing `__anonymous__` records are preserved as **Legacy anonymous**. They are never silently assigned to an administrator or integration owner. An administrator can inspect them in the Memories scope selector, reassign selected records to a user or shared household with exact counts, leave them in place, or delete them.

## Access and settings

Normal users can manage only their personal memory/archive scope. Home Assistant administrators can select personal, shared, and legacy scopes and manage integration-wide retention and voice mappings. Administrator panel selection never changes the scope available to model-facing tools.

Settings are available both under **Settings → Devices & services → Extended OpenAI** and in the feature tabs of the **Extended OpenAI** panel. Backend updates accept only named validated fields and rely on the integration update listener for one reload.
