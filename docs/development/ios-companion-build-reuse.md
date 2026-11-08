# iOS Companion acceptance build reuse

Every iOS acceptance run installs the selected Home Assistant version, starts a
new disposable instance with the exact EOAI candidate, erases the simulator, and
runs the complete patched upstream onboarding test. Native app compilation is
shared; acceptance results and Home Assistant storage are never shared.

## Dependency caches

Only `develop` (the default branch) writes large build, host-tool, Swift-package
and Python-wheel caches. Validation branches
can restore default-branch caches but cannot restore sibling-branch caches.
The workflow runs the full journey on native-input pushes and Monday/Thursday.
The other days perform native warming only, to detect runner toolchain updates
without issuing acceptance evidence. To warm explicitly, dispatch **iOS Companion
app acceptance** on `develop` with `warm_only: true`. Let that run finish before starting a validation
cohort if you want dependency cache hits on its first native build.

Bundler's small ABI-aware cache is managed by `ruby/setup-ruby`, including on
candidate branches. Host-tool caches use the Xcode build, architecture, tool
manifests and preparation script. Swift downloads have a separate lockfile key.
Python caches contain wheels/downloads, never a reused HA instance; stable HA
is resolved afresh and its integration constraints and dependency checks remain.
Dependency caches are saved after successful preparation. Incremental DerivedData
is saved after successful compilation. Neither is saved from a failed build.
All optional cache restore/save steps are non-blocking; installers, package
resolution, native identity validation and the UI journey remain mandatory.
The Python cache key directly hashes its two dependency files instead of using
GitHub's workspace-wide `hashFiles()` evaluation.

DerivedData uses an independent compatibility prefix (Xcode/SDK, architecture,
configuration and stable checkout path). Prefix restores can reuse older source
or test state, but always run `build-for-testing` before publishing new binaries.
Logs and test results are excluded. An exact executable-artifact restore never
uses a compatibility prefix and never skips the native identity check.

## Compiled test artifacts

`ci/ios_companion_build.py` derives an exact native identity from the pinned iOS
SHA, Xcode build, simulator SDK, architecture, checkout path, build configuration,
tracked package lockfiles and patched native build/test code. Unrelated workflow
edits do not invalidate it. EOAI candidate
SHAs are deliberately excluded: the app and test binary do not contain the EOAI
candidate, which is loaded from the fresh Home Assistant instance at runtime.

On a miss, the patched Fastlane `eoai_build` lane performs `build-for-testing`
once. The workflow publishes `ios-native-<identity>` before running the UI test.
Its tar file contains only the identity manifest and `Build/Products` (including
the app, XCTest runner and `.xctestrun`). Tar preserves executable permissions
and framework symlinks. Intermediate build state, simulator state, test results
and certification evidence are excluded.

Later runs discover exact-name artifacts from this repository's iOS workflow,
including artifacts produced on sibling validation branches. PR-triggered and
foreign-repository builds are excluded. An artifact may come from a run whose UI
test later failed: publication proves compilation completed, not acceptance.
The consumer validates its identity and runs the entire UI journey again using
`test-without-building`. Missing/expired artifacts or lookup-service failures
fall back to a source build; invalid downloaded artifacts fail the run.

Artifacts expire after seven days. Following a successful native publication,
retention keeps only the two newest native variants; evidence and diagnostic
artifacts are never pruned by this maintenance. Any native input change creates
a new identity and causes a cold build. The step summary and timing artifact
report completed step durations, reuse and cache hits. Fresh certification evidence records the candidate SHA, native identity
and reused artifact ID (when applicable), and still requires the expected agent
title to exist in this run's newly created Home Assistant storage.
Retention and timing reports run after the journey and evidence upload and are
non-blocking maintenance. Read-only GitHub API calls have bounded retries for
truncated responses and transport failures. A failed journey still fails the job;
evidence upload runs only after successful evidence generation and still treats
missing expected files as an error.

## Startup and duplicate work

The repository-wide iOS concurrency group uses `queue: max`, so candidates wait
without occupying extra macOS runners. This conservatively serializes the entire
iOS job, including warm journeys, and prevents concurrent cold builds. Each
queued job checks for reusable artifacts when it starts. The pinned actionlint
version predates GitHub's `queue` property, so PR lint ignores only that known
parser message; other workflow checks remain enabled.

Each acceptance job starts HA without blocking, then erases and boots its simulator
once after the lightweight setup actions and before host-tool preparation and
compilation. This avoids simulator first-boot contention during cache setup.
Both continue starting while the native app compiles. Bounded simulator and HTTP/process readiness checks gate
the journey; the upstream login check still runs. The lane does not erase again.
Screen-recording export is guarded when an earlier failure leaves `IOS_DIR` unset.
The test step supplies Fastlane's device, deployment target and log name explicitly
and prohibits build-settings discovery. That prevents a settings query from
resolving packages again when executing an already-built `.xctestrun` bundle.

Full validation declines duplicate *in-progress* cohorts only when candidate,
mode, explicitly supplied stress seed and explicitly supplied HA dev SHA match.
It links to the existing run and emits no new passing certification. Unseeded
stress runs and moving dev snapshots remain distinct coverage and are not deduplicated.

## Validation

After a native-input change, dispatch the workflow once for the cold build, then
dispatch it on a different branch at the same commit. The second run must report
`Compiled artifact reused: true`, skip native compilation and host-tool
preparation, run the full UI test, and generate fresh source-bound evidence.
Compare the two job step timings to measure savings. Do not reuse a certification
artifact as evidence for another EOAI candidate.
For a validation retry after successful native publication, set
`require_native_artifact: true` to refuse a cold compilation if lookup or reuse
is unavailable. This does not skip any acceptance tests.
