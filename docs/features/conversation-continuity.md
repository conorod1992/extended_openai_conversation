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

The conversation timeout is an inactivity timeout and every successful turn resets
it. Context remains subject to the normal threshold, truncation, summarization, and
token limits. The Conversations page offers a compact administrator action to end an
active conversation.

Continuity, Conversation Archive, Persistent Memory, and Temporary Memory are
separate. Continuity is active model context; Archive is retained searchable history;
Persistent Memory is durable selected facts; Temporary Memory is expiring current
context. Archive sessions use the continuity key when continuity is enabled, so a
deliberately resumed conversation is not fragmented, while the resolved privacy scope
still controls archive ownership.

Home Assistant's ChatLog and chat-session state is in memory and Core normally cleans
it after five minutes. The integration uses the supported conversation entity,
`async_get_chat_session`, and `async_get_chat_log` interfaces and keeps a bounded local
history copy for the configured active timeout. Active continuity mappings do not
survive a Home Assistant restart; the next request starts fresh.
