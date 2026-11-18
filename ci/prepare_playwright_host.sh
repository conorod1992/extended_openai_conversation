#!/usr/bin/env bash
set -euo pipefail

# Hosted Ubuntu runners use a per-download mirror list. Avoid the Azure
# endpoint that failed requests and the Ohio EC2 mirror that timed out during
# browser setup. Keep normal Ubuntu archive and signature verification.
if [[ -f /etc/apt/apt-mirrors.txt ]]; then
  sudo sed -i \
    -e '/azure\.archive\.ubuntu\.com/d' \
    -e 's|^https\?://us-east-2\.ec2\.archive\.ubuntu\.com/ubuntu|https://archive.ubuntu.com/ubuntu|' \
    /etc/apt/apt-mirrors.txt
fi

# Match the runner vendor's bounded acquisition policy, applied after older
# image configuration which can override an earlier numbered timeout file.
sudo tee /etc/apt/apt.conf.d/zz-eoai-ci-acquire > /dev/null <<'EOF'
Acquire::Retries "1";
Acquire::http::Timeout "15";
Acquire::https::Timeout "15";
EOF
