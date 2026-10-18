#!/usr/bin/env bash
set -euo pipefail

RUNNER=(python -c 'import homeassistant, pytest; raise SystemExit(pytest.main())')
if [ "${HA_POINT:-}" = "dev" ]; then
  RUNNER=(python ci/run_ha_dev_pytest.py --core-sha "${EXPECTED_HA_CORE_SHA:?}")
fi

set +e
# Initialize HA's httpx alias before pytest autoloads HTTP-related plugins.
"${RUNNER[@]}" \
  tests_stress/test_lifecycle_matrix.py tests_real_ha/test_public_version_journeys.py \
  tests_real_ha/test_ha_semantic_contract.py \
  -v -s --asyncio-mode=auto --timeout=600 \
  --stress-seed="$STRESS_SEED" --stress-intensity=normal \
  --show-capture=no --tb=short > enhanced-raw.log 2>&1
RESULT=$?
set -e
python ci/enhanced_evidence.py sanitize-log enhanced-raw.log stress-artifacts/pytest.log
rm enhanced-raw.log
cat stress-artifacts/pytest.log
exit "$RESULT"
