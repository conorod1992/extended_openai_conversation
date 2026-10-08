# Full validation

Full validation dispatches 26 non-live test workflows for one immutable candidate.
This includes genuine iOS and Android Companion journeys, all six targeted
mutation campaigns, HACS/hassfest validation, and the heavy enhanced campaign.
After every test workflow succeeds, it dispatches nightly programme certification
with both official Container architectures required. The stable HA version comes
from that run's enhanced certification artifact, rather than another version lookup.
Certification failure fails Full validation. Temporary candidate refs are removed
by the parent workflow's cleanup job after certification finishes.

Live OpenAI workflows remain separate because they call paid external providers.
Weekly/manual multi-seed race amplification supplements existing enhanced tests
and remains separate. Release publishing, CI image publishing, and Actions-history
cleanup are operational workflows, not candidate acceptance lanes.

The inventory regression test accounts for every current workflow file. New lanes
must be included or deliberately classified before that check will pass.
