# iOS Companion acceptance build reuse

Every iOS acceptance run installs the selected Home Assistant version, starts a
new disposable instance with the exact EOAI candidate, erases the simulator, and
runs the complete patched upstream onboarding test. Native app compilation is
shared; acceptance results and Home Assistant storage are never shared.

## Dependency caches

Only `develop` (the default branch) writes Actions caches. Validation branches
can restore default-branch caches but cannot restore sibling-branch caches.
The workflow runs on native-input pushes to `develop` and twice weekly to keep
these caches warm. To warm them explicitly, dispatch **iOS Companion app
acceptance** on `develop`. Let that run finish before starting a validation
cohort if you want dependency cache hits on its first native build.

Bundler caches use Ruby, runner architecture, Gemfile and Gemfile.lock. Host-tool
caches use the Xcode build, architecture, tool manifests and preparation script.
Both are saved immediately after successful preparation, before the native build
and UI test. DerivedData is a separate fallback for cold native builds.

## Compiled test artifacts

`ci/ios_companion_build.py` derives an exact native identity from the pinned iOS
SHA, Xcode build, simulator SDK, architecture, checkout path, build configuration,
tracked package lockfiles, workflow and native helper scripts. EOAI candidate
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

Artifacts are retained for 30 days. Any native input change creates a new identity
and causes a cold build. The step summary reports native reuse and Bundler cache
hits. Fresh certification evidence records the candidate SHA, native identity
and reused artifact ID (when applicable), and still requires the expected agent
title to exist in this run's newly created Home Assistant storage.

## Validation

After a native-input change, dispatch the workflow once for the cold build, then
dispatch it on a different branch at the same commit. The second run must report
`Compiled artifact reused: true`, skip native compilation and host-tool
preparation, run the full UI test, and generate fresh source-bound evidence.
Compare the two job step timings to measure savings. Do not reuse a certification
artifact as evidence for another EOAI candidate.
