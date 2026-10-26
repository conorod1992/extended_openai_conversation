# Management ownership contract

This review covers configuration saving, authoritative reads, navigation,
assistant selection and retained-panel recovery. The contracts below are the
oracle for the sequence tests; they do not infer expected results from the merge
helpers under test. They complement native editor/revision tests and backend
authorization. They are bounded coverage, not a proof of every possible HA
lifecycle or event interleaving.

## Invariants

| ID | Contract | Main boundaries |
| --- | --- | --- |
| O1 | A response can update only its originating user, provider entry and assistant. Returning to an assistant does not revive a retired authoring session. | Submission capture, queued mutations, whole-draft clear |
| O2 | A submitted snapshot is immutable. Acknowledgement changes the saved baseline; later edits, including a reversion, remain authored changes. | Save, read, acknowledgement |
| O3 | A dispatched write is not replayed by refresh or recovery. Switching/discard is blocked until its outcome is known. | Mutation admission, navigation, retained scopes |
| O4 | An old or failed read cannot certify a newer saved revision. A partial projection leaves omitted fields unknown. | Read ownership, cache generation, full recovery reads |
| O5 | Replacing a panel transfers every unsaved value once, or keeps an explicit recoverable copy. A destination edited during recovery is not a reason to strand the source. | Retention, save acknowledgement handoff, conflict review |
| O6 | Discard applies only to the reviewed draft. Late callbacks cannot restore it or discard newer work. | Discard, Back/Forward, assistant switching, repeated replacement |
| O7 | Model preparation follows its current draft and explicit choices; clearing that draft retires the preparation before another owner renders. | Model intent transfer, draft clear, delayed lookup |
| O8 | A raw repair index is meaningful only with its reviewed revision. Updating a published control does not rebase an already-open confirmation. | Repair card publication, confirmation, backend revision check |

Owner identity is the user, provider entry and assistant tuple. An authoring
session also has a panel-local generation, retired by a whole-draft clear.
In-flight operations must match both. Recovery explicitly transfers to a
different panel with the same owner identity; its local generation need not
equal the source's. Existing access, scope and revision checks still apply.

## Lifecycle responsibilities

| Boundary | Owner and authority | Shared mechanism / necessary distinction |
| --- | --- | --- |
| Open and author | Dialog instance/generation, or the registered policy scope; source values plus the revision at which editing began. | `dialogState()` is the common raw control snapshot for dismissal, Group save comparison and recovery. `setDialogBaseline()` owns ID-keyed baselines. Memory/Knowledge use typed readers in that same registry; Tool YAML retains its asynchronously loaded authoritative text. |
| Load and publish | Captured user/entry/assistant/session plus visit, cache generation and relevant result identity. | Owner predicates are shared. Visit/cache and accepted-read certificates remain independent because an owner can survive a navigation or cache invalidation. |
| Detect changes | Authoring baseline versus the current draft, including key presence and unparsed native YAML. | Policy `draftScope` and configuration dirty-key tracking keep different granularity; incremental configuration input handling is not replaced with whole-form comparison. |
| Submit and settle | Immutable submitted values and original revision; pending ownership lasts until the dispatched write settles. | Owner-only operations capture identity without full configuration copies. Full/field configuration ACKs retain their merge metadata. A rejected/ambiguous response does not advance the baseline or replay the write. |
| Acknowledge later edits | Submitted values become saved while later values and original-value reversions remain authored. | Policy baseline adoption belongs to `draftScope.save()`. Dialog ACKs store the submitted baseline under the dialog ID. Feature-specific record IDs, revision rules and DOM updates remain with the feature. |
| Detached recovery | Same identity across panels, with explicit access and route gates; source authoring revision remains authoritative. | Shared raw snapshots and the baseline registry compare the saved definition before restoring. Full configuration, policy-page and collection ACKs retain their distinct read-certification rules. |
| Discard / actor switch | Only the reviewed/current scope or dialog may discard. Pending writes block unsafe dismissal; user/entry/session retirement prevents late publication. | Navigation/close guards and exact scope registration remain authoritative. Explicit policy Discard adopts its latest accepted saved projection; it does not clear completed-write refresh obligations. |

Memory and Knowledge no longer maintain the panel-global `_editorInitial` in
parallel with `_eocDialogBaselines`. Open, successful load, acknowledgement, dirty
close and recovery all use the ID-keyed registry. Knowledge's loading/error
baseline is still `null`. Raw recovery arrays are not replaced by typed objects,
and queued close events cannot clear a newly reopened dialog's baseline.

Backend authority is also intentionally different: agent configuration and
Function mutations use configuration revisions; Rules settings use their atomic
Rule revision; Memory/Knowledge update individual records with their expected
revision; Quiet Hours uses a backend revision when provided and otherwise retains
its observed-conflict check. A common frontend owner is not a replacement for any
of these validation/revision contracts.

## Systematic coverage

`management-ownership-sequences.test.mjs` runs production save/read/navigation
and recovery boundaries against a controlled persistence adapter. It enumerates
write outcomes, edits during writes, read completion order and lifecycle changes.
Assertions cover visible authoring state, saved snapshots, pending guards, exact
write counts and recovery ownership after each relevant transition. Failing case
names identify the ordering rather than relying on random timing.

The deterministic matrix has 249 cases:

- 180 save sequences: success/failure/cancellation, unchanged/new/reverted/title
  edits, absent/successful/failed reads on either side of settlement, and
  staying/navigating/replacing the panel.
- 32 acknowledged handoffs: full/Retention projections, successful/failed
  certification, one/two replacements, clean/edited destinations, and fresh/
  previously retired source sessions.
- 34 ownership and control cases: user/entry/assistant/session retirement,
  queued-command isolation, successful and failed reads, and process-restart
  revision retry with an unchanged persisted baseline.
- 3 dirty-callback cases: replacement during certification with an abandoned
  successful/failed read, and a current-callback control. Consumed sources stay
  clean after their queued callbacks drain.

`management-ownership-matrix.spec.mjs` adds 24 browser representatives across
source and shipped assets. They use mounted controls, actual panel replacement,
navigation/Back, assistant A/B/A, conflicting/equal/disjoint edits and exact
saved-payload checks. They hold only the fixture transport, not production
ownership or merge helpers. Native/real-HA tests remain necessary for HA's own
navigation and authentication behavior.

Existing model-preparation, raw-YAML editor, repair-confirmation and genuine-HA
suites cover O6/O7/O8 in greater depth; the matrix focuses on O1–O5 and their
interactions. This does not claim exhaustive coverage of all editor types or
every reconnect ordering.

`guest-ownership.spec.mjs` adds 12 source/bundle cases for policy-specific
authoring state. Review-converted and start-fresh modes must follow the recovered
Guest draft, survive current-owner refreshes and cancelled navigation, and be
reset for a different assistant. Obsolete reads cannot alter those modes before
their values are accepted. Successful saves clear the reviewed migration state.

## Catalogue selection and page-save handoffs

Guest, Quiet Hours and Rule settings scopes use the same captured user, provider,
assistant and authoring session predicate as asynchronous submissions. The exact
coordinator registration also belongs to the scope: replacing a scope retires its
callbacks even when identity is unchanged. Scope reuse, new-save admission, draft
publication and Guest review reset share that predicate. The former separate
`scope.agent` value and agent/provider-only write check are removed. A retired
write may settle its original baseline/revision, but cannot publish into the new
scope; detached current scopes still settle for recovery.

One initial-identity rule remains explicit: an idle scope authored before HA
resolved its user may bind the first known user when all other owner/session and
registration fields match. This happens before save admission and never during a
dispatched write. A known user cannot rebind. Captured asynchronous submissions
and Guest metrics continue using the strict immutable owner check. The policy
scope sequence tests cover both retirement and this initial resolution boundary.

Capture only what an operation owns. Policy presentation, Rule settings and
Quiet Hours saves, the Group UI queue, and a required post-save refresh use
`captureConfigurationOwner()` plus an explicit visit/view where needed. They do
not copy unrelated agent configuration. Guest's field acknowledgement and Function
mutations still require `captureConfigurationSubmission()` for field-aware merges,
read epochs and model intent. UI and backend Group queues remain distinct.

`draftScope.save()` is the only successful-baseline writer for Guest, Quiet Hours
and Rule settings. Feature callbacks publish their own acknowledged projection and
revision, then return saved values for the shared scope to merge with later edits.
Explicit Discard still separately adopts the latest saved projection; it must not
silently rebase a pending edit or discharge an authoritative-read obligation.

An assistant catalogue read owns membership separately from selection. A
Duplicate operation captures its selection intent before the write starts;
subsequent edits, an explicit assistant/route change, an opened editor or a
replacement panel retire automatic selection. The completed copy can still
appear in the current catalogue. Obsolete sources cannot write the browser's
selection preference, close newer editors on a failed read or apply an old
configuration prefetch. Startup, clean duplication and explicit same-owner
import/restore refreshes retain their normal selection behaviour. Permission
changes still invalidate protected presentation and cached user scopes.

Cold catalogue reads start after connection, including when HA assigned `hass`
before attaching the panel. Each connection attempts a cold read once per user
identity. Resolving an initially unknown user starts a new owned read; ordinary
HA state updates do not retry failed reads. Reconnection and explicit reload
remain retry boundaries, and detached or superseded responses cannot publish.
Catalogue publication alone does not complete startup: its first page read may
still be pending. Late user resolution replaces that pending attempt even when
membership is already available. Disconnecting it retires both page-data and
lazy-presentation tokens, and reconnect retries it once. Old attempt settlement
cannot complete its replacement or publish stale configuration. This does not
restart settled panels or replay writes; retained drafts keep their owner and
authoring baseline throughout recovery.

A detached Guest, Quiet Hours or Request Rules settings save can become clean
while its replacement still holds a pre-commit read. The original page scope
therefore retains an acknowledgement independently of its dirty state. Recovery
consumes it only after an accepted read certificate matches the destination's
user, entry, assistant, session, route, visit and cache generation. A resolved
reload promise alone cannot certify freshness. Refresh never replays the save or
publishes the source's saved response over another panel's data.

Original authoring baselines/revisions stay attached to later edits. An accepted
saved projection is kept separately for explicit Discard. Policy saves wait
while a required acknowledgement refresh is unresolved. Without a backend
revision, after this panel observes a changed saved Quiet Hours policy, its older
dirty draft cannot overwrite it. The draft remains available; Discard adopts the
current policy before edits are reapplied. This guards observed conflicts, not
unseen concurrent writes from another client. When a backend supplies a revision,
the original authoring revision remains responsible for conflict detection.

Authentication transitions retire operations separately from authored values.
`captureConfigurationOwner()` captures an authentication generation in addition
to the user, entry, assistant and draft generation. The `hass` setter advances it
when a known user leaves, including known-to-unknown transitions. A later return
to the same identity cannot revive queued writes, reads or callbacks. Ordinary
same-user HA updates and initial unknown-to-known resolution preserve their
existing behavior. Mutation deduplication includes that generation.

Policy authoring uses `captureConfigurationDraftOwner()` instead: a temporary
identity transition does not discard its edits or rebase its authoring revision.
Each save captures a separate publication guard. A retired save still settles
its own acknowledged baseline/revision, but cannot merge values into controls,
replace the accepted result, or present an obsolete success/failure. Likewise,
a retained policy's accepted-read obligation outlives a retired refresh attempt.
This is operation retirement, not a new same-panel cross-user draft store;
recovery between panel instances still requires the existing owner/access gates.

Normal Tool loading, repair loading, validation and acknowledgement share
`captureToolEditorOwner()`. Exact root/dialog identity, normal/repair generation
and repair index remain editor-visit guards; YAML equality and latest validation
still protect submitted text. Repair revision and raw-index semantics are unchanged.

Request Rule editor openings capture their owner once. Model lookups and submit
callbacks reuse the exact dialog/selector ownership check; the existing latest
operation primitive retires superseded model lookups, including changes to local
actions or empty/template model values. Native selector drafts and backend Rule
revisions retain their specialized contracts.

The Rule editor alone owns its save button. Its synchronous admission guard
prevents repeated form submissions; the shared owner-scoped mutation queue still
serializes requests and deduplicates identical payloads. There is no additional
panel-wide Rule save promise or submit interceptor whose completion can reset
another opening's controls.

Native Rule translation readiness has a resource lifetime, not an editor-visit
lifetime. The cache's exact record, connection and effective language must still
match before success hydrates currently mounted selectors or failure removes the
record and reports an error. Reopening an editor can reuse that matching resource;
an obsolete connection cannot replace its labels or delete a newer cached result.

Rule saves use the same lossless `dialogState()` and keyed dialog-baseline registry
as recovery and Function Group saves. Their acknowledged baseline is the raw
dispatch snapshot, including selector YAML; only an unchanged submission closes.
Later controls remain mounted and dirty, and an acknowledged creation keeps its
server-owned rule ID for its next update. An own ACK may advance the editor's
revision only while its original collection baseline or that ACK still owns the
saved projection. A newer accepted collection requires the existing conflict
path. Missing ID/revision responses never retire the draft or its old revision.

Rule collection mutations share one dispatch/result/recovery contract. Admission
and confirmation require the original owner and visit; errors cannot reload a
replacement visit. Successful publication instead uses the guarded shared Rule
revision: a read made before commit must not hide the own ACK, and a newer
accepted read must not be rolled back. Tracking advances only from the revision
present at dispatch or the same acknowledged revision, without ordering opaque
revision strings. Previously queued commands can publish even when an earlier
own ACK replaced their original card.

Control cleanup has a separate latest-operation ticket and exact DOM membership.
It cannot reset another pending control or steal focus from a newer visit. Drag
moves share one list and retain every serialized acknowledgement; they do not
use exclusive control supersession. Group confirmation captures its authoring
revision, copies group records before editing, and reuses existing group rows on
ACK. Later row/new-name text and focus remain intact. This does not introduce a
new general group-dialog discard/navigation/recovery workflow.

Tool load generations also carry their opening owner. New Validate/Save commands
reuse it instead of acquiring the current identity from an old mounted editor.
They reject retired ownership before dispatch or pending UI state. Initially
unknown authoring can bind to its first known identity at a new command's
admission, by replacing the descriptor. Already-captured operation closures keep
their original descriptor and cannot inherit that identity. No additional Tool
owner counter or parallel mutable owner field is maintained.

The authentication owner record stores the last observed user ID alongside its
generation. Reading the previous ID from mutable `hass` would miss in-place
A→B→A updates. Ordinary same-user updates reuse the record; identity changes
replace it. The existing generation property is a computed compatibility view,
not a second independently mutable counter.
