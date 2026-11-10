# Persistent memory

Persistent memory keeps concise durable facts after chat history ends. It is off by default and remains separate from conversation transcripts, temporary memory, and the conversation archive.

## Modes and automatic context

- **Off** disables memory tools and automatic context without deleting stored records.
- **Manual** saves only explicit remember requests.
- **Automatic** may also save stable, useful facts proactively within the configured access permissions.

**Automatically include memories** is a per-conversation limit from `0` to `10` (default `3`). The opening user message ranks memories once. The selected IDs are retained with the existing logical conversation and the same bundle is injected on later turns. Updated records appear with their current content; deleted or newly inaccessible records are omitted. The bundle is discarded when the conversation expires or is replaced. `0` makes persistent memory tool-only, and `memory_search` remains available throughout a conversation.

## Retrieval modes

**Keyword matching** (lightweight lexical) is the default and is fully local. It uses deterministic BM25-style IDF and term-frequency scoring, phrase bonuses, stemming and normalization, conservative prefix/one-edit typo matching, and category, subject, and canonical-key matches. A minimum relevance threshold filters weak matches, and equal scores use stable memory-ID order.

**Keyword + meaning** (hybrid semantic) combines lexical relevance with locally calculated cosine similarity. It sends memory text and retrieval metadata from the permitted scopes to the configured provider's embedding endpoint when vectors are missing or stale. Vectors are cached locally; changed records and restored memories are regenerated on the next relevant search, rather than when a fact is saved. Each hybrid search also sends its query for embedding, including the opening-message search and later `memory_search` calls. These provider requests can add latency and API cost.

Hybrid uses embeddings, not another LLM, classifier, or reranker call. No vector database is used and raw embeddings are excluded from normal results, diagnostics, prompts, and backups. If the configured provider or embedding model does not support embeddings, retrieval logs the failure and falls back to lexical without breaking the conversation. Semantic matching remains probabilistic and should not be treated as perfect. Choose lightweight lexical matching if you want memory ranking to remain entirely local.

## Records and reliable updates

Every memory has content, category, source, and creation/update timestamps. Retrieval ranks relevance; equal scores use a stable memory ID tie-break, independent of when a fact was edited.

Optional advanced metadata includes:

- `subject`, such as `Oscar`
- canonical `key`, such as `pet.oscar.breed`
- `valid_from`, when the fact became true if known

A non-empty normalized key is unique inside one owner scope; the same key can exist in personal and household scopes. Memories never expire automatically, and age alone does not make a fact false.

Use model-facing `memory_upsert` to **propose a new** durable fact. It creates a record when no exact-key or exact-content conflict exists, reports `unchanged` for an unchanged, unambiguously identified record, or returns `needs_resolution` with `applied: false` and a candidate for an exact-key collision or compatible literal duplicate. It **never edits** an existing record, even when the key matches; changes must use a separate, revision-checked `memory_update` if permitted. Literal duplicates with distinct subjects or known validity dates can coexist. Word overlap, stemming, and retrieval similarity **never block a model proposal**; the selected conversation LLM is responsible for judging semantic relatedness, so different-key contradictions and paraphrase duplicates are possible if it chooses the wrong action. The legacy model `memory_add` alias follows the same safe path and stays hidden from fresh model tool catalogues. The older direct `PersistentMemory.async_upsert()` still has its conservative related-fact check for compatibility; authenticated Management uses separate add/update operations. Current user statements override conflicting memory context.

## Personal and household privacy

An authenticated conversation can read that user's personal memories plus shared-household memories when shared memory is enabled. Results identify their friendly scope. It can never read another user's personal records. A conversation explicitly assigned to shared household reads household memory only; an unretained conversation reads and writes no retained memory.

Writes are deliberately stricter. Authenticated conversations default to personal. Household writes require enabled shared memory and, for user-scoped conversations, the explicit `household` selector. A shared-household conversation writes only household memory by default. Shared Explicit permits model-executed household changes **only when the original user utterance directly requests the corresponding memory action**. A user-scoped conversation must explicitly mention the household target, whereas an already-shared conversation may simply ask to remember, correct or forget something. Shared Automatic additionally permits proactive writes. The model's `source: explicit` label is never sufficient by itself. These deliberately conservative checks can require rephrasing indirect requests; identity and Guest Mode restrictions remain unchanged. Raw Home Assistant user IDs are never exposed as model-facing scope selectors.

## Management, safety, backup, and limits

Open **Extended OpenAI → Data & Memory → Memories** to search/filter by text, category, or owner and edit content, category, owner, subject, key, and valid-from metadata. Use **+ Add memory** on either tab to choose Long-term or Short-term. Existing memories keep their type. Advanced fields remain optional. Destructive bulk actions retain confirmation. Non-administrator users can manage only their own personal scope; administrators can select other owners and shared-household or legacy scopes. Administrator management access does not expand the scope available to a conversation's model tools.

Storage uses Home Assistant's private versioned `.storage` API with a 10,000-record limit per agent. Existing stores and backups remain compatible: obsolete importance and confirmation fields are ignored and removed when saved. Full backup/restore round-trips the new metadata and accepts prior records; embeddings are regenerated instead of exported.

Memory context enters conversation requests when selected automatically or returned by a tool. Hybrid retrieval additionally sends eligible memory text and queries to the embedding endpoint as described above. In conversation context, memory is framed as untrusted background data, never instructions or authorization. EOAI no longer classifies content or metadata to block credentials, financial information or sensitive personal topics. Users may deliberately store these when their Memory mode and permissions allow; automatic retention still depends on the configured mode and the conversation LLM's interpretation of usefulness and user preferences. Persistent Memory is not a dedicated secrets vault.

Full private backups and selected custom transfers can contain complete Memory values, including owner scope and metadata. Their YAML encoding and ZIP transport are not encryption. Shareable Setup excludes Memory and continues redacting configured provider/HTTP credentials. Ordinary diagnostics and Usage remain content-free; explicitly enabled administrator request debugging can contain full request material, including remembered values.

Storage versions remain unchanged. Older releases may reject these newly permitted records on restore or purge them at startup after a downgrade. Removing the filter cannot recover records already purged by an older release. Existing fact comparison ignores trailing sentence punctuation, so Memory is not byte-exact credential storage.

### Deleting an assistant

Deleting a conversation assistant permanently removes its private Memory and embedding cache, Temporary Memory, Knowledge, Archive, Usage, Guest Mode and Request Rule stores. Cleanup waits for current requests to settle, then blocks detached background writes from recreating deleted data. Removing an entire provider entry also removes orphan stores belonging to that entry. Export a backup first if you want to retain those records.

Removing the final EOAI provider entry also stops Quiet Hours and Broadcast callbacks and queued deliveries. Quiet Hours restores the controls it still owns before shutdown. Normal unloading and reloading keep these integration-wide features running.

Authenticated Management may promote an inferred record's provenance through an explicit edit. Model-origin `source: explicit` is descriptive only; it is not independently verified user consent, cannot promote an existing record or override scope/mode restrictions. Model-origin creation labelled explicit remains compatible with Manual mode requests, including credentials and sensitive personal facts. Search pagination applies `offset` after ranking, so successive pages return different results.

Model `memory_update` requires `memory_id` and `expected_revision` obtained from `memory_search`, `memory_list` or the injected opening bundle. Inferred records retain their existing mode-specific update policy; explicit records may also be corrected when the current user utterance directly requests it. Model edits always preserve provenance, even if the tool's source label is explicit. Model `memory_delete` requires `targets: [{memory_id, expected_revision}, ...]` and validates the complete batch before deleting anything. Missing, foreign or stale targets fail without partial deletion. Explicit-record deletion requires a direct user forget/delete instruction. Confirm broad deletion with a fresh explicit instruction rather than a bare yes. Revisions are opaque hashes of substantive state, not proof of consent or a monotonic history of edits.

Hybrid embedding caches are bound to the configured provider, endpoint, API version and embedding model. Changing that space rebuilds affected vectors when needed. Older caches without this identity regenerate safely; stored memory facts are preserved.

Model-facing `memory_update` retains the legacy optional source field. A declared proactive (`implicit`) update requires Automatic mode; an explicit label cannot grant additional privileges or promote provenance. Omitted source defaults to explicit for a recognized direct user correction request and otherwise retains the legacy mode-specific default. Authenticated Management retains separate, revision-checked editing rights. Content, category, subject and key remain subject to structural type, size and syntax validation. Structurally valid previously stored sensitive content and metadata load normally without content-based deletion, including during failed-save recovery. Malformed siblings still preserve the original generation behind a repair barrier.
