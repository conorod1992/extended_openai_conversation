# Queued configuration acknowledgements

`captureConfigurationSubmission()` owns the assistant, submitted draft and saved
baseline. `reconcileConfigurationAcknowledgement()` advances the saved baseline
without adopting later local edits as persisted values.

If an earlier acknowledgement has advanced a field's baseline since submission,
the later acknowledgement compares the current draft against that newer baseline.
A draft equal to it can adopt the later saved value. A draft different from it
remains a local edit, including a revert to the original submitted value.
The saved revision still advances, and the next save submits the retained edit
against that revision. No separate edit-generation state is needed for this case.

Real consumers include full configuration save in `management-actions.js`, queued
Function/Group operations in `agent-config-tools-base.js`, Function repair, Guest
settings and independently saved Knowledge availability. Function/Group callers
capture submissions before queued writes complete, so multiple outstanding
submissions can share an older baseline. Their controls also impose their own
pending and navigation restrictions; reproducing the complete array-revert
sequence through genuine browser interaction remains unestablished.

The focused combined regression reproduces both full and field acknowledgement
variants, then exercises the real full-save caller to verify that the retained
draft is submitted using the latest acknowledged revision. Existing tests retain
the complementary guarantee that queued acknowledgements without local edits
advance the draft normally. This establishes the helper contract without claiming
an end-to-end browser reproduction.


A subset acknowledgement advances its fields and revision, but does not certify
that the entire configuration snapshot is fresh. Function and group renames also
rewrite Guest allowlists that are absent from their partial response. The helper
invalidates the full-snapshot cache and retains stale status until a full read or
acknowledgement arrives. In-document navigation rereads stale configuration even
with an unrelated dirty draft. The read merges the latest draft against its saved
baseline: unchanged fields adopt the server value, while changed fields, deleted
keys and an edited title remain local. A field reverted to that baseline has no
local delta. A newer baseline/revision, owner, route, load token or cache generation
invalidates the pending read. Cold Retention reads remain partial; expansion and
refresh of an existing full snapshot preserve unrelated fields. Recovering a full
draft into a replacement panel on Retention requires a complete authoritative
read before ownership transfers. Omitted fields in a partial projection are not
deletions. Failed or superseded reads leave the original draft retained; retries
stay scoped to the current user, assistant and visit.

If the replacement form is edited while recovery reads are pending, recovery
compares both drafts with their own authoring baselines. Disjoint or equal edits
can join the current full snapshot without replacing a newer model intent.
Conflicting fields remain available in a read-only recovered copy until explicit
discard; they are never silently applied over the current draft. Pending model
preparation follows the merged draft, and retained navigation markers identify
the actual edited settings rather than the route open at disconnection.
Clearing the whole draft for an assistant switch, import or restore also retires
its model intent, pending preparation and reasoning-choice revision before any
render can inspect the next owner's configuration.

Raw Function repair indices are paired with the metadata revision published by
their card. An accepted render can refresh the snapshot of a preserved control;
an open confirmation or editor keeps the revision it originally presented. A
refresh during confirmation therefore cannot authorize an old index against a
newer raw collection.

Focused regressions cover both rename projections, real UI Save/preview handlers,
navigation guards, edits during refresh, presence/revert semantics and read races.
Genuine HA browser regressions save Function/group renames through the shipped
editors under custom Guest policy with clean and dirty prompt/title drafts,
navigate within the same panel, and check the allowed renamed tool in the preview.
The subsequent full Save sends only local edits using the refreshed revision.


## Frontend mutation declarations

`management-mutation-effects.js` declares each mutation tracked by the panel once,
including its revision/queue family and affected configuration, collection,
scope or Usage projections. Mutation admission, queued implicit revisions,
acknowledgement epochs and cache invalidation consume that same lookup. Existing
methods still own execution, ownership guards, acknowledgements and cache removal;
backend authorization is independent of this frontend inventory. Creation and
duplication retain their existing feature-owned flows outside this inventory.

Guest activation differs from saving Guest policy. Knowledge CRUD differs from
feature enablement. Restore retains its broad section/scope invalidation and
existing replacement of the global Usage-window cache. Conversation deletion/end
retains its existing overview-only classification. Generation increments, explicit
caller revisions and failure handling are unchanged by this extraction.

The focused test inventory independently enumerates commands and checks actual
panel cache/revision effects rather than deriving expected behavior from the
production table. Genuine HA Chromium journeys cover configuration save followed
by Rule creation, Rule Pack review/import with an old read crossing acknowledgement,
and clearing Usage details after visiting multiple history windows.
