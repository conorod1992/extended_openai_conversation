# Conversation continuity

Conversation continuity keeps recent turns as active model context when one Assist
run ends and a later wake-word or Assist request begins. It is different from
**Continue conversation**, which only controls whether Home Assistant keeps listening
immediately after an answer.

- **Home Assistant default** preserves the existing conversation-ID behaviour.
- **Per device** lets the same Assist satellite/device resume recent context.
- **Per user** lets requests resolved to the same Home Assistant user resume across
  devices. This uses the integration's existing authenticated-user and voice-device
  mappings; it does not perform speaker recognition. If a personal user cannot be
  resolved, continuity falls back to that device, or to a new/Home Assistant-default
  session when no usable device exists. Shared household scope is never treated as a
  personal user.

## Inactivity and lifecycle

The conversation timeout is an inactivity timeout and every successful turn resets
it. Context remains subject to the normal threshold, truncation, summarization, and
token limits. Continuity mappings and conversation-scoped Function Group and Request
Rule state are intentionally kept in memory rather than persisted. A Home Assistant
restart therefore starts a fresh live conversation even if the configured inactivity
timeout had not expired.

The assistant also has a built-in **start fresh conversation** operation for an
explicit request to start over. The current answer is allowed to finish first, then
the integration discards the active model context, the conversation's automatically
selected memory bundle, loaded Function Groups, and Request Rule conversation
overrides. The next turn starts fresh. If another request has already claimed the
same managed continuity session, ending that live context is deferred until the newer
request releases its claim so an in-flight conversation is not destroyed underneath
it.

This operation does **not** delete Persistent Memory, Temporary Memory, Knowledge
Library sources, or Conversation Archive history. Those are separate retained data
features and remain available according to their own settings.

## Related retained data

Continuity, Conversation Archive, Persistent Memory, and Temporary Memory are
separate. Continuity is active model context; Archive is retained searchable history;
Persistent Memory is durable selected facts; Temporary Memory is expiring current
context. Archive sessions use the continuity key when continuity is enabled, so a
deliberately resumed conversation is not fragmented, while the resolved privacy scope
still controls archive ownership.

Home Assistant's ChatLog and chat-session state is in memory and Core normally cleans
it after five minutes. The integration uses the supported conversation entity,
`async_get_chat_session`, and `async_get_chat_log` interfaces and keeps a bounded local
history copy for the configured active timeout.

## Management

The Conversations page offers a compact administrator action to end a named active
continuity session. This remains an explicit management action; stale claim tokens
cannot recreate or mutate a replacement session after it has been ended.
