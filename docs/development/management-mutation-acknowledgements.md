# Management mutation acknowledgements

A server acknowledgement and publication of its result have separate lifetimes.
Rendering, modal closure, cache invalidation, catalogue navigation and refreshes
cannot turn an acknowledged mutation into a rejected write or automatically replay
it. This extends the existing configuration acknowledgement boundary; it does not
introduce a second operation queue, ownership registry or server protocol.

## Outcomes and ownership

- An explicit unsuccessful response keeps the feature's existing failure path.
  A zero-count single-record delete is not an unknown transport outcome.
- A response must contain the operation's acknowledgement fields before the UI
  adopts its identity or baseline. Missing fields and transport loss remain
  uncertain. The client tells the user to refresh before retrying and never
  automatically repeats the command. This is not an exactly-once guarantee for
  requests whose responses were lost.
- `callMutation` classifies rejection at the transport boundary. Coded server
  errors retain their failure path; HA's nested connection-loss result and bare
  numeric transport failures remain uncertain, including direct feature requests.
- Once confirmed, record identity, revision and submitted editor baseline survive
  subsequent presentation failure. Knowledge and Memory creates become updates;
  successful imports consume their preview before modal/catalogue processing.
- `presentAcknowledgedMutation` contains secondary failures and reports a saved
  outcome with a refresh warning. It accepts the existing configuration owner or
  an existing ownership predicate. A notification failure cannot reject the write.
- Shared request processing returns the original response even if invalidation or
  revision publication fails. A WeakMap retains that secondary error without
  changing the response, wire contract or backend data. Features pass the original
  response through when constructing configuration projections.
- Collection/visit guards still decide whether to patch a displayed list. The
  authentication generation prevents a same-user sign-out/sign-in round trip from
  reclaiming a pending editor. Deliberate import draft retirement and selection of
  a newly duplicated agent retain their acknowledgement without adopting another
  user's operation.
- Confirmation belongs to the owner captured before the dialog opens. A later
  agent, authentication session, collection scope or visit cannot inherit it.

## Operation audit

### Transaction failure evidence

Transaction owners can annotate the original exception with immutable
`MutationFailure` evidence without changing its type or cancellation semantics.
Both Management and backup-transfer WebSockets carry this evidence in HA's
existing error-code field: `eoai_mutation_rejected`,
`eoai_mutation_uncertain`, or `eoai_mutation_committed`, optionally suffixed
with `_recovery_pending`. Successful response payloads are unchanged.

Failed transactional saves remain uncertain even when reconciliation loads a
usable generation: readback alone does not certify the write's durability.
Restore reports rejection after a completed rollback, uncertainty when its
commit decision cannot be verified, and commitment after a verified commit
decision followed by a completion failure. Recovery pending is independently
determined by the journal's authoritative absence. Reload failures after a
completed restore retain committed evidence too.

The frontend preserves this evidence, retires a failed restore's preview, and
reports a committed restore or pending recovery without offering an immediate
retry. A known committed replacement also retires the submitted configuration
draft through the existing owner checks. Uncertain ordinary mutations retain
their existing refresh/reconciliation barriers. Older coded errors retain their
legacy handling; no certainty is derived from exception wording. This contract
does not turn ordinary HA configuration acceptance into a disk-durability claim
or guarantee exactly-once execution after a lost response.

| Operation family | Acknowledgement and subsequent processing |
| --- | --- |
| Configuration saves and independent Knowledge availability | Existing configuration reconciliation; form error rendering also follows acknowledgement. |
| Knowledge and persistent/temporary Memory creation/editing | Record identity and submitted baseline precede collection/DOM processing. A failed temporary-create close can update that same record. |
| Knowledge/Memory deletion, temporary clear, reassignment | Counts identify the outcome; patching, closing, focus and refresh run within presentation. |
| Conversation deletion/end and Usage detail clearing | Validate returned counts, then preserve the outcome through reconciliation and search refresh. A failed search refresh cannot hide the acknowledged deletion. |
| Guest interval changes | Status acknowledges the mutation independently of details loading and rendering. |
| Guest/Quiet Hours/Request Rules policy saves | Existing draft scopes retain baselines and detached acknowledgements; response publication errors are secondary. |
| Request Rules collection/editor actions and pack imports | Existing revision/operation admission remains authoritative. Rendering cannot enter rejected-mutation rollback; imports consume their review before publication. |
| Function Tools, Groups and HA Tool additions | Existing configuration completion boundary receives the original response. Isolated repair editor processing is moved behind that boundary too. |
| Agent duplication/configuration import | Validate created/updated identity; preserve success through catalogue navigation and consume successful import previews. |
| Provider credentials and embedded Debug | Acknowledge returned status before modal, diagnostic or capture-list processing. Submitted credentials are cleared after acknowledgement. |
| Overview Broadcast | Queue/status acknowledgement is independent of the subsequent snapshot read; a failed snapshot produces a refresh warning. |
| Backup transfer/restore | Validate `status: restored`, consume the acknowledged session/preview and replaced draft before cache/modal work, and retain provider-connection warnings. Missing or lost acknowledgements remain uncertain. No journal, rollback or restore coordinator change. |

Read-only previews, searches, validation, exports and diagnostics are not promoted
to mutations. Live request testing keeps the backend's execution/result contract;
this change does not infer successful tool effects from its rendered report.

## Regression evidence

`tests/management-mutation-acknowledgement.test.mjs` runs production panel methods
against the authoritative collection backend fixture. Assertions cover actual
stored records and dispatched write counts, failure after commit, subsequent
updates, rejected/incomplete/lost responses, authentication changes, duplication,
imports and other collection actions. Two deliberate source mutations are evaluated
in memory using the same outcome witnesses; neither rewrites the checkout.

`tests_browser/management-mutation-acknowledgement.spec.mjs` exercises source and
rebuilt production assets with real browser dialogs, controls and authoritative
backend state. It injects cache, rendering and modal failures and checks that a
subsequent edit updates the committed record rather than duplicating it. It also
proves that an incomplete reply after an actual write remains uncertain.

`tests/backup-transfer-ownership.test.mjs` and
`tests_browser/restore-acknowledgement.spec.mjs` exercise acknowledgement loss,
cache/modal/notification/cleanup failures and consumed restore sessions. Browser
assertions read restored backend state and count actual restore requests. The
standalone sensitivity case removes the response-processing guard and detects
the resulting false failure with the same witness.
