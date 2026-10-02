#!/usr/bin/env bash
set -euo pipefail

: "${EOAI_PLAYWRIGHT_ENGINE:?browser engine is required}"
: "${EOAI_PLAYWRIGHT_VERSION:?Playwright version is required}"

NPM_GLOBAL_ROOT="$(npm root -g)" \
  node /opt/eoai-ci/verify_playwright_engine.mjs \
  "$EOAI_PLAYWRIGHT_ENGINE" "$EOAI_PLAYWRIGHT_VERSION" \
  > /opt/eoai-ci/playwright-runtime.json
BROWSER_VERSION="$(python -c 'import json; print(json.load(open("/opt/eoai-ci/playwright-runtime.json"))["browser"])')"
IDENTITY=/opt/eoai-ci/browser-environment.identity.json

python /opt/eoai-ci/environment_fingerprint.py \
  --requirements /opt/eoai-ci/requirements_test.txt \
  --manifest /opt/eoai-ci/manifest.json \
  --recipe /opt/eoai-ci/Dockerfile.stable \
  --recipe /opt/eoai-ci/install_ha_dependencies.py \
  --recipe /opt/eoai-ci/install_ha_media_dependencies.py \
  --recipe /opt/eoai-ci/environment_fingerprint.py \
  --recipe /opt/eoai-ci/resolve_ha_test_plugin.py \
  --recipe /opt/eoai-ci/reconcile_stable_environment.sh \
  --recipe /opt/eoai-ci/write_playwright_environment_identity.sh \
  --recipe /opt/eoai-ci/verify_playwright_engine.mjs \
  --python-version "$(python -c 'import platform; print(platform.python_version())')" \
  --base-image "$(cat /opt/eoai-ci/base-image.txt)" \
  --extra "ha_version=$(python -c 'from importlib.metadata import version; print(version("homeassistant"))')" \
  --extra "ha_test_plugin_version=$(python -c 'from importlib.metadata import version; print(version("pytest-homeassistant-custom-component"))')" \
  --extra "playwright_engine=$EOAI_PLAYWRIGHT_ENGINE" \
  --extra "playwright_version=$EOAI_PLAYWRIGHT_VERSION" \
  --extra "node_version=$(node --version)" \
  --extra "browser_version=$BROWSER_VERSION" \
  --write "$IDENTITY" > /dev/null
