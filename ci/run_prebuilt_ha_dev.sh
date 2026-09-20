#!/usr/bin/env bash
set -euo pipefail

MODE="${1:?usage: run_prebuilt_ha_dev.sh <compat|upcoming>}"

CURRENT_ENVIRONMENT_SHA="$(cat requirements_test.txt custom_components/extended_openai_conversation_responses/manifest.json | sha256sum | awk '{print $1}')"
BUILT_ENVIRONMENT_SHA="$(cat /opt/eoai-ci/environment.sha256)"

if [ "$CURRENT_ENVIRONMENT_SHA" != "$BUILT_ENVIRONMENT_SHA" ]; then
  echo "Dependency declarations changed since image build; reconciling without replacing HA dev."
  bash ci/reconcile_ha_dev_environment.sh
fi

python -m pip check
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
