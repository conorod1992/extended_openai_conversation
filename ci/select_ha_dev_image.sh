#!/usr/bin/env bash
set -euo pipefail

IMAGE="${1:?immutable HA-dev image reference required}"
: "${EXPECTED_HA_CORE_SHA:?expected HA Core SHA is required}"
: "${EXPECTED_PYTHON_VERSION:?expected Python version is required}"

if ! docker pull "$IMAGE"; then
  echo "match=false" >> "$GITHUB_OUTPUT"
  echo "Exact HA-dev image unavailable; will construct the resolved runtime." >> "$GITHUB_STEP_SUMMARY"
  exit 0
fi

if docker run --rm --network host --ipc=host \
  -v "$GITHUB_WORKSPACE:/workspace" -w /workspace \
  -e EXPECTED_HA_CORE_SHA -e EXPECTED_PYTHON_VERSION \
  "$IMAGE" bash ci/check_ha_dev_runtime.sh; then
  echo "match=true" >> "$GITHUB_OUTPUT"
  echo "Using exact HA-dev image for Core ${EXPECTED_HA_CORE_SHA} and Python ${EXPECTED_PYTHON_VERSION}." >> "$GITHUB_STEP_SUMMARY"
else
  echo "match=false" >> "$GITHUB_OUTPUT"
  echo "HA-dev image identity did not match; will construct the resolved runtime." >> "$GITHUB_STEP_SUMMARY"
fi
