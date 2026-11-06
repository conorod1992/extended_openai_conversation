# Quiet Hours transition and effect contract

Quiet Hours owns temporary control effects and their restoration obligations.
A scheduled occurrence, a device report and a manager lifetime are independent:
replacing the manager does not create a new quiet occurrence, and an offline
device does not imply that Home Assistant missed its daytime callback.

| Situation | Required observable behavior |
| --- | --- |
| First visit during a quiet occurrence | Apply only controls that need changing; remember their prior values before dispatch. An already quieter speaker stays quieter. |
| Owned quiet effect continues, including across a missed daytime boundary | Retain the original baseline without an intermediate daytime-volume command. |
| Independent adjustment in the same occurrence | Release ownership and preserve that choice through repeated reconciliation and manager replacement. Do not adopt it as the old effect's restoration baseline. |
| An unowned control was already quiet when first observed | Do not reclaim it later in that occurrence merely because its value changes. |
| A later occurrence begins | Old unowned observations cannot suppress its policy. Unavailable owned controls keep their unresolved restoration baseline. |
| A control returns changed in a later occurrence | Settle the old obligation without overwriting the changed report; if current policy requires an effect, capture the returned value as its new baseline. |
| A controlled device leaves current policy | Restore only its still-attributable quiet effect, or retain the obligation while it is unavailable. Preserve independent adjustments. |
| Policy ends while a device is offline | Keep the original restoration obligation; restore on a later available daytime read. |
| Service error follows an attributed effect | Reconcile its prepared receipt against the actual report. Neither replay the effect nor lose its baseline on restart. |
| Repeated reconciliations without relevant new input | Converge: no repeated command, restore/apply oscillation or baseline drift. |

Early application admission for volume and wake-sound controls has one shared
implementation, `_control_application_suppressed()`. Reconciliation handles existing
ownership; an observed unowned choice stays released in the occurrence, while
prepared/pending work may resume. Period orchestration owns observation expiry.
This extraction does not change stored fields, ordering of prepared-intent saves,
service dispatch, context attribution, cancellation or acknowledgement settlement.
Final preparation retains its authoritative ownership and baseline validation.
Other comparisons and restoration decisions remain in their current owners.

`tests/quiet_hours_transition_histories.py` supplies twelve independently authored
histories. It imports no production decision helper or ownership flag. Literal
command expectations cover regular end, missed callbacks, manager progress while
offline, return before/after restart, independent choices, departing devices and
an acknowledged device report followed by a lost service acknowledgement. Each
checkpoint compares the complete domain/service/entity/value command delta, settled public control value and
published active entity, including when the policy is active but owns no control.
Final restoration commands check the meaning of the retained baseline.

The fast and native HA drivers run the same histories for volume and wake sound,
with immediate and deferred feedback (48 cases per driver). Deferred feedback
means a successful service completes before its state report arrives; feedback
is released after the reconciliation/setup boundary. The lost-ack history
explicitly publishes an attributable effect before raising the service error.
It does not pretend that an arbitrary opaque service failure proves an effect.
Crash replacement loads the saved state and runs actual setup/catch-up without
calling graceful shutdown restoration. Native cases use real registry entries,
registered HA services, native Store files and owned callback cleanup.

This contract composes with existing tests for graceful manager replacement,
timezone/DST and overlapping schedules, retargeting and control identity,
quantization, failed saves, pending intents and cancellation. The history suite
adds coverage across those established boundaries; it does not replace their
more specific guarantees or claim to model arbitrary device report reordering.
TOCTOU races and Request Rule Guest-policy exceptions remain outside this review.
