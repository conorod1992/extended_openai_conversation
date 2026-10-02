#!/usr/bin/env bash
set -euo pipefail

set +e
pytest tests_stress/test_lifecycle_matrix.py tests_real_ha/test_public_version_journeys.py \
  -v -s --asyncio-mode=auto --timeout=600 \
  --stress-seed="$STRESS_SEED" --stress-intensity=normal \
  --show-capture=no --tb=short > enhanced-raw.log 2>&1
RESULT=$?
set -e
python ci/enhanced_evidence.py sanitize-log enhanced-raw.log stress-artifacts/pytest.log
rm enhanced-raw.log
cat stress-artifacts/pytest.log
exit "$RESULT"
