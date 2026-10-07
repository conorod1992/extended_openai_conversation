#!/usr/bin/env bash
set -euo pipefail

export ANDROID_SERIAL="${ANDROID_SERIAL:-emulator-5554}"
diagnostics="${RUNNER_TEMP}/android-companion"
mkdir -p "$diagnostics"
adb version > "$diagnostics/adb-version.txt"

# pm clear can briefly disconnect ADB. Do it before Maestro creates its
# persistent driver channels, then require a stable booted transport.
adb shell pm clear io.homeassistant.companion.android
timeout 120 adb wait-for-device
stable=0
for attempt in {1..60}; do
  if [[ "$(timeout 5 adb shell getprop sys.boot_completed | tr -d '\r')" == 1 ]]; then
    stable=$((stable + 1))
    if [[ "$stable" == 5 ]]; then
      break
    fi
  else
    stable=0
  fi
  sleep 2
done
[[ "$stable" == 5 ]]
adb devices -l > "$diagnostics/devices-before.txt"
adb logcat -v threadtime > "$diagnostics/logcat.txt" 2>&1 &
logcat_pid=$!
collect_diagnostics() {
  result=$?
  kill "$logcat_pid" 2>/dev/null || true
  timeout 10 adb devices -l > "$diagnostics/devices-after.txt" 2>&1 || true
  exit "$result"
}
trap collect_diagnostics EXIT

maestro test tests_mobile/companion-app-smoke.yaml \
  -e HOME_ASSISTANT_URL=http://10.0.2.2:8123 \
  -e HOME_ASSISTANT_USERNAME=eoai-companion \
  -e HOME_ASSISTANT_PASSWORD=eoai-companion-password
