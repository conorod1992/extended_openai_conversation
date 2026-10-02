#!/usr/bin/env bash
set -euo pipefail

: "${EXPECTED_HA_CORE_SHA:?expected exact Home Assistant Core SHA is required}"
: "${EXPECTED_PYTHON_VERSION:?expected Python version is required}"

IMAGE_SHA="$(cat /opt/eoai-ci/ha-core-sha)"
test "$IMAGE_SHA" = "$EXPECTED_HA_CORE_SHA"
IMAGE_PYTHON="$(/opt/venv/bin/python -c 'import platform; print(platform.python_version())')"
test "$IMAGE_PYTHON" = "$EXPECTED_PYTHON_VERSION"

ARGS=(
  --requirements requirements_test.txt
  --manifest custom_components/extended_openai_conversation_responses/manifest.json
  --recipe ci/Dockerfile.dev
  --recipe ci/environment_fingerprint.py
  --recipe ci/check_ha_dev_environment.py
  --recipe ci/install_ha_dependencies.py
  --recipe ci/install_ha_media_dependencies.py
  --recipe ci/reconcile_ha_dev_environment.sh
  --recipe ci/run_prebuilt_ha_dev.sh
  --recipe ci/check_ha_dev_runtime.sh
  --python-version "$EXPECTED_PYTHON_VERSION"
  --base-image "$(cat /opt/eoai-ci/base-image.txt)"
  --extra "ha_core_sha=$EXPECTED_HA_CORE_SHA"
)
/opt/venv/bin/python ci/environment_fingerprint.py "${ARGS[@]}" \
  --check /opt/eoai-ci/environment.identity.json
/opt/venv/bin/python ci/check_ha_dev_environment.py
