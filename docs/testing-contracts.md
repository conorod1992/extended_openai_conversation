# Testing shared guarantees

Feature regressions remain useful, but this layer compares production entry points against shared guarantees. A new writer or action route should be added to the relevant matrix rather than receiving only a happy-path test.

## Entry-point matrix

| Guarantee | Routes | Evidence |
| --- | --- | --- |
| Invalid requests cannot publish | Configuration save/update, import preview/current/new | Genuine HA: unchanged data, IDs, revision, client and loaded agent |
| Full request validation before persistence | AI Task fast/advanced native flows | Genuine HA: invalid combinations remain forms, no subentry mutation |
| Narrow settings contract | Settings update | General model/search configuration is rejected |
| Saved requests survive catalogue activation | Apply/reset, Conversation/AI Task | Failed publication preserves active and pending catalogues; valid controls publish |
| Provider changes preserve child compatibility | Conversation/AI Task, hosted search on/off | Authentication alone does not permit incompatible child configuration |
| Live/reload decisions agree | Configuration save/update | Token changes preserve runtime; model changes rebuild device metadata |
| Failure remains machine-readable | Assist/process × provider/rule/local intent | Error code, failed Usage, and no continued listening |
| Caller CONTROL permission precedes effects | Native/Script/nested Script | Real restricted HA user: denied state unchanged, allowed context retained |
| Broadcast permission precedes queueing | Service/WebSocket/model/local | Real HA policy; only discovery/delivery replaced, denied queue untouched |

Native Conversation creation accepts only a name; detailed configuration goes through Management. Settings deliberately accepts a restricted subset. Full disaster-recovery restore is not setup import and has its own preservation/recovery contract.

The fast catalogue/provider matrix runs in ordinary unit CI. Genuine HA matrices are discovered by Real HA Acceptance on PRs and nightly runs. Existing oldest/stable/dev public journeys continue to check version compatibility. The matrices replace external provider transport or delivery, never the authorization or request validator they aim to verify.
