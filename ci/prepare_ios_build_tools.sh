#!/usr/bin/env bash
# Resolve and compile the pinned host tools before Xcode starts a test session.
set -euo pipefail

cd "${1:?Usage: prepare_ios_build_tools.sh IOS_DIR}"

# The app's Xcode package graph does not include BuildTools. Use the upstream
# wrapper so SwiftPM retains its pinned versions, checksums and cache paths.
# These commands do not launch/onboard the app and can safely be retried.
for attempt in 1 2 3; do
  if env -u SDKROOT ./Tools/build_tool swiftgen --version &&
     env -u SDKROOT ./Tools/build_tool swiftlint version; then
    exit 0
  fi
  if [[ "$attempt" -lt 3 ]]; then
    echo "iOS host build-tool preparation failed (attempt $attempt/3); retrying" >&2
    sleep "$((attempt * 5))"
  fi
done

echo "iOS host build-tool preparation failed after 3 attempts" >&2
exit 1
