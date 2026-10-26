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

An assistant catalogue read owns membership separately from selection. A
Duplicate operation captures its selection intent before the write starts;
subsequent edits, an explicit assistant/route change, an opened editor or a
replacement panel retire automatic selection. The completed copy can still
appear in the current catalogue. Obsolete sources cannot write the browser's
selection preference, close newer editors on a failed read or apply an old
configuration prefetch. Startup, clean duplication and explicit same-owner
import/restore refreshes retain their normal selection behaviour. Permission
changes still invalidate protected presentation and cached user scopes.

A detached Guest, Quiet Hours or Request Rules settings save can become clean
while its replacement still holds a pre-commit read. The original page scope
therefore retains an acknowledgement independently of its dirty state. Recovery
consumes it only after an accepted read certificate matches the destination's
user, entry, assistant, session, route, visit and cache generation. A resolved
reload promise alone cannot certify freshness. Refresh never replays the save or
publishes the source's saved response over another panel's data.

Original authoring baselines/revisions stay attached to later edits. An accepted
saved projection is kept separately for explicit Discard. Policy saves wait
while a required acknowledgement refresh is unresolved. Quiet Hours has no
backend revision: after this panel observes a changed saved policy, its older
dirty draft cannot overwrite it. The draft remains available; Discard adopts the
current policy before edits are reapplied. This guards observed conflicts, not
unseen concurrent writes from another client.
