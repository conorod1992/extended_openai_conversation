# Full validation

Full validation dispatches 27 non-live test workflows for one immutable candidate.
This includes genuine iOS and Android Companion journeys, all six targeted
mutation campaigns, HACS/hassfest validation, and the heavy enhanced campaign.
After every test workflow succeeds, it dispatches nightly programme certification
with both official Container architectures required. The stable HA version comes
from that run's enhanced certification artifact, rather than another version lookup.
The dispatcher resolves Home Assistant dev once before launching workflows. CI,
real-HA acceptance, Enhanced lifecycle and native browser compatibility receive
the same full commit through `ha_dev_sha`; Python selection, dependency metadata,
images and source installs use that immutable source. Standalone scheduled/manual
runs may omit the input and resolve current dev locally. An invalid supplied pin
fails rather than falling back to the moving branch.
Certification checks actual dev evidence against the dispatcher's pin as well as
checking agreement between workflows, so agreeing on a different source still fails.
Certification selects evidence only from that Full Validation cohort's branch;
a newer daily run of the same commit cannot replace its architecture evidence.
Certification failure fails Full validation. Temporary candidate refs are removed
by the parent workflow's cleanup job after certification finishes.

Live OpenAI workflows remain separate because they call paid external providers.
Weekly/manual multi-seed race amplification supplements existing enhanced tests
and remains separate. Release publishing, CI image publishing, and Actions-history
cleanup are operational workflows, not candidate acceptance lanes.

The inventory regression test accounts for every current workflow file. New lanes
must be included or deliberately classified before that check will pass.

The parent uploads `full-validation-cohort.json`, recording its candidate SHA,
HA dev SHA, parent run and attempt, temporary ref, and every child run and attempt. Release
certification rechecks that complete inventory and the programme certificate,
including stable/dev environment identity, executed cases, and both architectures.
Expired artifacts, missing lanes, failed children, or superseded attempts fail closed.
Use Full validation for release candidates and deliberate comprehensive checks;
dispatch individual specialist workflows when diagnosing one area.

See [Actions execution policy](actions-policy.md) for PR enforcement and scheduled cadence.
