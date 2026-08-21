# Persistent memory

Persistent memory keeps concise durable facts after chat history ends. It is off by default and remains separate from conversation transcripts, temporary memory, and the conversation archive.

## Modes and automatic context

- **Off** disables memory tools and automatic context without deleting stored records.
- **Manual** saves only explicit remember requests.
- **Automatic** may also save stable, useful facts proactively under the same privacy rules.

**Automatically include memories** is a per-conversation limit from `0` to `10` (default `3`). The opening user message ranks memories once. The selected IDs are retained with the existing logical conversation and the same bundle is injected on later turns. Updated records appear with their current content; deleted or newly inaccessible records are omitted. The bundle is discarded when the conversation expires or is replaced. `0` makes persistent memory tool-only, and `memory_search` remains available throughout a conversation.

## Retrieval modes

**Lightweight lexical** is the default and is fully local. It uses deterministic BM25-style IDF and term-frequency scoring, phrase bonuses, stemming and normalization, conservative prefix/one-edit typo matching, and category, subject, and canonical-key matches. Importance is applied only after a minimum relevance threshold. Freshness is only a small tie-breaker, and ties finish in stable memory-ID order.

**Hybrid semantic** combines lexical relevance with locally calculated cosine similarity. It uses embeddings, not another LLM, classifier, or reranker call. Memory embeddings are generated when retrieval-relevant data changes and regenerated after restore when needed; one query embedding is requested for a new conversation. No vector database is used and raw embeddings are excluded from normal results, diagnostics, prompts, and backups. If the configured OpenAI-compatible provider or embedding model does not support embeddings, retrieval logs the failure and falls back to lexical without breaking the conversation. Semantic matching remains probabilistic and should not be treated as perfect.

## Records and reliable updates

Every memory has content, category, source, creation/update timestamps, and an importance of `low`, `normal`, or `high` (`normal` by default). High importance supplies a bounded ranking boost and cannot make an unrelated fact relevant.

Optional advanced metadata includes:

- `subject`, such as `Oscar`
- canonical `key`, such as `pet.oscar.breed`
- `valid_from`, when the fact became true if known
- `last_confirmed_at`, when it was created, explicitly reconfirmed, or meaningfully updated

A non-empty normalized key is unique inside one owner scope; the same key can exist in personal and household scopes. Memories never expire automatically, and age alone does not make a fact false.

Prefer `memory_upsert` for durable new or changed facts. An exact key updates the record and returns `updated`; a duplicate refreshes confirmation and returns `confirmed`; a strong but uncertain related candidate returns `needs_resolution` without overwriting; otherwise it returns `created`. `memory_add` remains for compatibility. Current user statements always override conflicting memory context.

## Personal and household privacy

An authenticated conversation can read that user's personal memories plus shared-household memories when shared memory is enabled. Results identify their friendly scope. It can never read another user's personal records. A conversation explicitly assigned to shared household reads household memory only; an unretained conversation reads and writes no retained memory.

Writes are deliberately stricter. Authenticated conversations default to personal. Household writes require the explicit `household` selector and enabled shared memory. A shared-household conversation can write only household memory. Automatic household writes additionally require the shared **Automatic** setting. Identity is never inferred and raw Home Assistant user IDs are not exposed as model-facing scope selectors.

## Management, safety, backup, and limits

Open **Extended OpenAI → Memories** to search/filter by text, category, importance, or scope and edit content, importance, scope, subject, key, and freshness metadata. Advanced fields remain optional. Destructive bulk actions retain confirmation, and authenticated users cannot manipulate another user's personal scope.

Storage uses Home Assistant's private versioned `.storage` API with a 10,000-record limit per agent. Version 2 migrates existing records to normal importance and uses the prior update (or creation) time as `last_confirmed_at`. Full backup/restore round-trips the new metadata and accepts prior records; embeddings are regenerated instead of exported.

Memory context is sent to the configured provider only when selected or returned by a tool. It is always framed as untrusted background data, never instructions or authorization. The storage layer rejects secrets, usable financial credentials, and automatic sensitive-personal facts. Persistent memory is not a secrets manager.
