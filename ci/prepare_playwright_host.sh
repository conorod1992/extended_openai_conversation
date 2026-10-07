#!/usr/bin/env bash
set -euo pipefail

# Hosted Ubuntu runners put Azure first in a per-download mirror list. An
# unavailable Azure endpoint was retried for every index/package until the
# browser job timed out. Retain the runner's official HTTPS archive/security
# mirrors and normal package-signature verification.
if [[ -f /etc/apt/apt-mirrors.txt ]]; then
  sudo sed -i '/azure\.archive\.ubuntu\.com/d' /etc/apt/apt-mirrors.txt
fi

# Match the runner vendor's bounded acquisition policy, applied after older
# image configuration which can override an earlier numbered timeout file.
sudo tee /etc/apt/apt.conf.d/zz-eoai-ci-acquire > /dev/null <<'EOF'
Acquire::Retries "1";
Acquire::http::Timeout "15";
Acquire::https::Timeout "15";
EOF
