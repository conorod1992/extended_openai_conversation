#!/usr/bin/env bash
set -euo pipefail

MODE="${1:?usage: run_prebuilt_ha_dev.sh <compat|upcoming>}"

IMAGE_HA_CORE_SHA="$(cat /opt/eoai-ci/ha-core-sha)"
BASE_IMAGE=""
if [[ -s /opt/eoai-ci/base-image.txt ]]; then
  BASE_IMAGE="$(cat /opt/eoai-ci/base-image.txt)"
fi
IDENTITY_ARGS=(
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
  --python-version "$(python -c 'import platform; print(platform.python_version())')"
  --base-image "$BASE_IMAGE"
  --extra "ha_core_sha=$IMAGE_HA_CORE_SHA"
)

if [[ -z "$BASE_IMAGE" ]] \
    || [[ ! -s /opt/eoai-ci/environment.identity.json ]] \
    || ! python ci/environment_fingerprint.py "${IDENTITY_ARGS[@]}" \
      --check /opt/eoai-ci/environment.identity.json; then
  echo "Environment identity is missing or changed; reconciling without replacing HA dev."
  bash ci/reconcile_ha_dev_environment.sh
  if [[ -n "$BASE_IMAGE" ]]; then
    python ci/environment_fingerprint.py "${IDENTITY_ARGS[@]}" \
      --write /opt/eoai-ci/environment.identity.json > /dev/null
  else
    echo "Base-image identity is unavailable; this legacy image will be reconciled on each run."
  fi
  cat requirements_test.txt custom_components/extended_openai_conversation_responses/manifest.json \
    | sha256sum | awk '{print $1}' > /opt/eoai-ci/environment.sha256
fi

python ci/check_ha_dev_environment.py
python - <<'PY'
from importlib.metadata import version

print("homeassistant=" + version("homeassistant"))
try:
    print(
        "pytest-homeassistant-custom-component="
        + version("pytest-homeassistant-custom-component")
    )
except Exception:
    pass
PY
echo "home-assistant-core-sha=$(cat /opt/eoai-ci/ha-core-sha)"

case "$MODE" in
  compat)
    exec bash ci/run_ha_dev_compat_tests.sh
    ;;
  upcoming)
    exec bash ci/run_real_ha_upcoming_tests.sh
    ;;
  *)
    echo "Unknown prebuilt HA dev mode: $MODE" >&2
    exit 2
    ;;
esac
