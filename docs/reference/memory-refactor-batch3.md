# Memory refactor — Batch 3: conversation-model semantic resolution

## Trust and action boundaries

The existing conversation model, using the ordinary tool loop, interprets possible
fact equivalence, contradiction, historical coexistence and ambiguity. No new
classifier call, embedding-dependent authority, background agent, or persisted
semantic index is added.

The model sees potentially relevant opening Memory records and may call
`memory_search` or `memory_list`; their results include scope, provenance,
exact ID and revision. They are **candidates**, not confirmation of semantic
equivalence. A query miss never establishes that the assertion is new.

The model may select:
- **No write:** an existing fact expresses the same statement, or the input is
  hypothetical, quoted, too temporary, insufficiently certain or unsuitable.
- **Propose:** a new independent durable fact, using `memory_upsert`. A stable
  key is useful but not a semantic authorization credential.
- **Correct:** only an inferred record, using `memory_update` with the exact
  ID/revision from a scoped read. Exact target and revision checks happen inside
  the storage lock; protected explicit records require authenticated Management.
- **Delete:** only exact revision-checked inferred targets; the entire batch is
  checked before any deletion. Broad or guessed deletion is forbidden.
- **Clarify:** ask the user when subject, time, target, intended persistence or
  meaning is genuinely ambiguous.

A proposed record yielding `needs_resolution` has **not** been written.
The model must not retry with a fictitious different key simply to evade that
result. A model-supplied `source=explicit` label cannot grant owner consent,
sensitive-storage permission, or write authority in explicitly restricted shared
scope. Guest Mode, retention scope and management permissions remain separate.

## Remaining no-write conflict heuristic

`_find_related_candidate()` is **not** a fact-equivalence classifier. It is a
conservative admission barrier: a probable match can only return a no-write
conflict for model proposals. Because the available semantic fixtures have not
been independently adjudicated with a sufficiently representative live-model
confusion matrix, removing it would be an unsupported safety regression.

Previously, an incoming *different* canonical key bypassed that barrier, even
when the new content contradicted a keyed fact. Batch 3 also checks related
candidates for model proposals with new keys. It returns an exact candidate and
revision to a reader permitted to see that scope; write-only callers receive
only a status. No automatic rewrite, metadata promotion, or record merge occurs.

**Trade-off:** conservative conflict checks can abstain on independently true
facts about the same subject. This is preferable to allowing a silent write
based solely on lexical matching. The model can still create genuinely distinct
facts. Do **not** turn this guard into a synonym matching or merge algorithm,
and do not delete it until independent evaluations demonstrate safe replacement.

## Evidence boundaries

The 40 Batch 1 semantic fixtures are deliberately **draft semantic expectations**.
An expected `update` label means the user's statement semantically revises
a previous assertion. It does **not** mean the model has authority to call
`memory_update` on a protected explicit record. For those cases the permitted
model action is no change and referral to authenticated Management.

Testing is separated into four independent layers:

1. **Retrieval:** records and hard negatives via the frozen retrieval corpus.
2. **Semantic action selection:** prompt/tool descriptions and controlled provider
   tool calls on Chat Completions and Responses. Controlled responses verify
   plumbing but are *not* evidence of real model classification accuracy.
3. **Mutation safety:** scope/guest/privacy gates, no-write conflicts,
   duplicate protection, ownership, revision freshness and atomic deletion.
4. **Durability:** saved record state after accepted changes and unchanged state
   after rejected or ambiguous changes.

Further live-model evaluation should score action selection by family, especially
negation, compound corrections, subject identity, history, plans, false
equivalence, false novelty, and over-abstention. Its labels require independent
review before being treated as regression gates. Never make live-model
classification a prerequisite for deterministic mutation security.

## Non-changes

Store version 2, authenticated Management calls, storage locks, rollback,
replay, backup/restore, embeddings and Knowledge are unchanged. The Archive
ranking pipeline is not part of this semantic-action refactor.
