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
- **Correct:** use `memory_update` with the exact ID/revision from a scoped read.
  Inferred records retain the mode-specific update policy; explicit records also
  permit a correction when the backend recognizes a direct current user request.
  Exact target and revision checks happen inside the storage lock, and model
  updates preserve provenance and structural validation; content sensitivity
  classification has since been retired.
- **Delete:** use exact revision-checked targets; the entire batch is checked
  before any deletion. Explicit records require a recognized direct current user
  forget/delete request. Confirm broad deletion with a fresh delete instruction;
  a bare confirmation does not authorize a protected mutation.
- **Clarify:** ask the user when subject, time, target, intended persistence or
  meaning is genuinely ambiguous.

A proposed record yielding `needs_resolution` has **not** been written.
The model must not retry with a fictitious different key simply to evade that
result. A model-supplied `source=explicit` label cannot grant owner consent or
write authority in explicitly restricted shared scope. Guest Mode, retention scope and management permissions remain separate.

The follow-up request gate reads the original current user utterance, never
retrieved text or model arguments. Shared Explicit mode permits a recognized
direct household request; a personal conversation must name the household
target. This gate recognizes a limited set of English command forms, rather
than establishing semantic consent for arbitrary natural language. It does not
replace scope, mode, revision or privacy checks. The earlier Management-only
restriction in the Batch 1/2 baseline is superseded.

## Model proposal admission after the follow-up simplification

Model-facing `memory_upsert` (and the legacy `memory_add` tool alias) now
uses **deterministic exact identity checks**, not token-overlap/Jaccard ranking,
to decide whether a proposal may be inserted.

- An **existing canonical key in the same owner scope** is never overwritten.
  A truly unchanged proposal can return `unchanged`; different content,
  provenance or metadata returns `needs_resolution` without a write.
- **Literal duplicate content**, with compatible subject and validity time,
  cannot silently create another record under a different key. A unique exact
  match can return `unchanged` only when its existing identity/metadata also
  match the proposal. Otherwise the model receives a no-write conflict.
- Distinct explicitly known subjects or validity dates may coexist even if
  the short content is identical; multiple exact candidates cannot silently
  become an arbitrary no-op.
- **Semantically related but differently worded facts are not vetoed** by the
  backend. This permits independent facts about the same person or device,
  historical facts, and reversals of relationships. It also permits erroneous
  duplicate/contradictory insertion if the selected model makes a poor semantic
  decision; this is a documented data-quality trade-off, not an authorization
  relaxation. Models must search and avoid inventing keys to evade a known
  collision.
- Candidate data is released only after independent read-scope authorization.
  Write-only guests receive status only. Storage locks, revision checks,
  rollback, retirement and scope isolation remain unchanged. Content filters
  were retired subsequently without changing those protections.

The older `_find_related_candidate()` and non-model `async_upsert()` were
subsequently retired after verifying that they had no production callers.
Authenticated Management retains its explicit add/update APIs. Model-facing
`memory_upsert` and the hidden `memory_add` alias still use `async_propose()`;
retrieval similarity is never an authorization source.

This follow-up deliberately does not require proving that all user-chosen
models make correct semantic decisions. Deterministic tests establish that
model-selected tool actions are executed or rejected according to the backend
contract, including genuine Chat Completions and Responses provider wires.

## Evidence boundaries

The semantic fixture corpus contains the original 40 Batch 1 cases plus a
user-supplied health-fact case added when content filtering was retired. These
41 cases are deliberately **draft semantic expectations**. Unsupported inference
is still rejected; a useful user-supplied sensitive fact is eligible in Automatic
mode, and an explicitly requested synthetic credential is eligible within scope.
An expected `update` label means the user's statement semantically revises
a previous assertion. It does **not** mean the model has authority to call
`memory_update` on an explicit record. The separate current-request, scope,
mode, privacy and revision checks must still allow the operation; otherwise
the record stays unchanged. Authenticated Management remains available through
its separate administrative path.

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
replay, backup/restore and embeddings are unchanged. The Archive ranking
pipeline is not part of this semantic-action refactor. Separate follow-up fixes
on the same PR add secondary word-form matching to Knowledge; they do not
replace the shared Memory/Archive stemmer.
