# Temporary memory

Temporary memory is short-lived factual context that the assistant may create
automatically and silently. It is separate from Persistent Memory: durable facts such
as “My parents live in Cork” remain candidates for persistent memory, while “My
parents are visiting this weekend” belongs in temporary memory and should expire at
the end of Sunday.

- **Off** creates and injects no temporary memories.
- **Balanced** stores clearly useful near-term facts.
- **Eager** proactively stores useful short-lived context when it could plausibly be
  relevant again, preferring to retain it when uncertain while still excluding
  secrets, sensitive information, trivial fragments, and conversational filler.

The model infers reasonable expiry in Home Assistant's configured timezone instead of
asking unnecessary questions. “Waiting for a parcel today” lasts through today;
“cooking pasta” or “watching Oppenheimer” lasts a few hours; “the plumber is coming
tomorrow” lasts through tomorrow; an explicit duration or date takes precedence.

Temporary records use a separate private Home Assistant Store. They survive restarts
only while their expiry is in the future, are filtered and opportunistically pruned
before every injection, and are pruned at startup. Injection is local and bounded.
The model can add, supersede, or forget records only in the scope derived from the
current request; it cannot choose another owner. Personal and Shared owners determine access; conversation and device continuity are metadata and do not grant access to another owner’s records.

Existing secret, credential, payment-card, banking, and automatic-sensitive-memory
protections also apply to temporary memory. Current user statements override stored
temporary facts. The Memories page has Long-term and Short-term views. **+ Add memory** on either tab opens one dialog with Memory, Type, Category and Owner; Short-term also asks for Expires using Home Assistant local date/time. The type defaults to the current tab and is fixed when editing.

Manual short-term creation uses an explicit privacy policy and records `source: manual`; model-created records retain `source: automatic` and automatic sensitivity restrictions. Both reject secrets and credentials. Expiry must include a timezone internally, remain in the future, and be within one year. The configured automatic mode does not prevent manual creation.
