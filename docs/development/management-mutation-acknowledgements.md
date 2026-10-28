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

## Operation audit

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
| Backup transfer/restore | Existing transfer-session/restore-completion ownership is retained. No journal, rollback or restore coordinator change. |

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
