#!/usr/bin/env bash
set -euo pipefail

: "${HISTORICAL_RUNTIME_DIR:?historical runtime directory is required}"
: "${HISTORICAL_MANIFEST:?published manifest path is required}"
: "${HISTORICAL_RELEASE_TAG:?published release tag is required}"
: "${HISTORICAL_RELEASE_SHA:?published release SHA is required}"
: "${HISTORICAL_HA_VERSION:?historical Home Assistant version is required}"
: "${HISTORICAL_BASE_IMAGE:?historical runtime base identity is required}"

PIP_VERSION="$(python -m pip --version | awk '{print $2}')"
python -m venv "$HISTORICAL_RUNTIME_DIR"
HISTORICAL_PYTHON="$HISTORICAL_RUNTIME_DIR/bin/python"
"$HISTORICAL_PYTHON" -m pip install --no-cache-dir --upgrade "pip==$PIP_VERSION"
"$HISTORICAL_PYTHON" -m pip install --no-cache-dir \
  "homeassistant==$HISTORICAL_HA_VERSION"
"$HISTORICAL_PYTHON" ci/install_ha_dependencies.py \
  --manifest "$HISTORICAL_MANIFEST"
"$HISTORICAL_PYTHON" -m pip check
"$HISTORICAL_PYTHON" ci/historical_runtime_identity.py \
  --manifest "$HISTORICAL_MANIFEST" \
  --release-tag "$HISTORICAL_RELEASE_TAG" \
  --release-sha "$HISTORICAL_RELEASE_SHA" \
  --homeassistant-version "$HISTORICAL_HA_VERSION" \
  --base-image "$HISTORICAL_BASE_IMAGE" \
  --recipe ci/install_ha_dependencies.py \
  --recipe ci/prepare_historical_runtime.sh \
  --recipe ci/historical_runtime_identity.py \
  --write "$HISTORICAL_RUNTIME_DIR/.eoai-runtime-identity.json"
