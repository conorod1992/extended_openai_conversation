#!/usr/bin/env bash
set -euo pipefail

IDENTITY=/opt/eoai-ci/environment.identity.json
CURRENT_HOMEASSISTANT="$(python -c 'from importlib.metadata import PackageNotFoundError, version
try:
    print(version("homeassistant"))
except PackageNotFoundError:
    print("")')"
CURRENT_HA_TEST_PLUGIN="$(python -c 'from importlib.metadata import PackageNotFoundError, version
try:
    print(version("pytest-homeassistant-custom-component"))
except PackageNotFoundError:
    print("")')"
ARGS=(
  --requirements requirements_test.txt
  --manifest custom_components/extended_openai_conversation_responses/manifest.json
  --recipe ci/Dockerfile.stable
  --recipe ci/install_ha_dependencies.py
  --recipe ci/install_ha_media_dependencies.py
  --recipe ci/environment_fingerprint.py
  --recipe ci/resolve_ha_test_plugin.py
  --recipe ci/install_ha_test_plugin.py
  --recipe ci/reconcile_stable_environment.sh
  --recipe ci/write_playwright_environment_identity.sh
  --recipe ci/verify_playwright_engine.mjs
  --python-version "$(python -c 'import platform; print(platform.python_version())')"
  --base-image python:3.14-slim-bookworm
  --extra "ha_version=${EOAI_EXPECTED_HA_VERSION:?expected stable Home Assistant version is required}"
  --extra "ha_test_plugin_version=${EOAI_EXPECTED_HA_TEST_PLUGIN_VERSION:?expected compatible Home Assistant test plugin version is required}"
)
if [[ -n "${EOAI_PLAYWRIGHT_ENGINE:-}" ]]; then
  IDENTITY=/opt/eoai-ci/browser-environment.identity.json
  BROWSER_VERSION="$(python -c 'import json; print(json.load(open("/opt/eoai-ci/playwright-runtime.json"))["browser"])')"
  ARGS+=(
    --extra "playwright_engine=$EOAI_PLAYWRIGHT_ENGINE"
    --extra "playwright_version=${EOAI_PLAYWRIGHT_VERSION:?}"
    --extra "node_version=$(node --version)"
    --extra "browser_version=$BROWSER_VERSION"
  )
fi

if [[ -s "$IDENTITY" ]] \
    && [[ "$CURRENT_HOMEASSISTANT" == "${EOAI_EXPECTED_HA_VERSION:?expected stable Home Assistant version is required}" ]] \
    && [[ "$CURRENT_HA_TEST_PLUGIN" == "${EOAI_EXPECTED_HA_TEST_PLUGIN_VERSION:?expected compatible Home Assistant test plugin version is required}" ]] \
    && python ci/environment_fingerprint.py "${ARGS[@]}" --check "$IDENTITY"; then
  python -m pip check
  exit 0
fi

echo "Prebuilt environment identity differs; reconciling declared dependencies."
python -m pip install -r requirements_test.txt
python ci/install_ha_test_plugin.py "$EOAI_EXPECTED_HA_VERSION" "$EOAI_EXPECTED_HA_TEST_PLUGIN_VERSION"
python -m pip install --no-deps --force-reinstall "homeassistant==${EOAI_EXPECTED_HA_VERSION:?expected stable Home Assistant version is required}"
python ci/install_ha_dependencies.py --manifest custom_components/extended_openai_conversation_responses/manifest.json
python -m pip check
python ci/environment_fingerprint.py "${ARGS[@]}" --write "$IDENTITY" > /dev/null
cat requirements_test.txt custom_components/extended_openai_conversation_responses/manifest.json \
  | sha256sum | awk '{print $1}' > /opt/eoai-ci/environment.sha256
