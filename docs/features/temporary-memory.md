# Temporary memory

Temporary memory is short-lived factual context that the assistant may create
automatically and silently. It is separate from Persistent Memory: durable facts such
as “My parents live in Cork” remain candidates for persistent memory, while “My
parents are visiting this weekend” belongs in temporary memory and should expire at
the end of Sunday.

- **Off** creates and injects no temporary memories.
- **Balanced** stores clearly useful near-term facts.
- **Eager** more readily stores plausible near-term context, without storing every
  sentence or conversational debris.

The model infers reasonable expiry in Home Assistant's configured timezone instead of
asking unnecessary questions. “Waiting for a parcel today” lasts through today;
“cooking pasta” or “watching Oppenheimer” lasts a few hours; “the plumber is coming
tomorrow” lasts through tomorrow; an explicit duration or date takes precedence.

Temporary records use a separate private Home Assistant Store. They survive restarts
only while their expiry is in the future, are filtered and opportunistically pruned
before every injection, and are pruned at startup. Injection is local and bounded.
The model can add, supersede, or forget records only in the scope derived from the
current request; it cannot choose another owner. Per-device and per-user continuity
use their matching scope. Home Assistant-default mode uses the current HA conversation
only, so temporary context does not follow an unrelated new Assist session.

Existing secret, credential, payment-card, banking, and automatic-sensitive-memory
protections also apply to temporary memory. Current user statements override stored
temporary facts. The Memories page has lightweight Persistent and Temporary views,
with expiry and deletion controls.
