#!/bin/bash
# Run every integration test against the connected Nano.
#   tests/run_all.sh        asks you to confirm first (5 s to abort)
#   tests/run_all.sh -y     no pause
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 99

if [ "${1:-}" != "-y" ]; then
  cat <<'EOF'
These tests drive a REAL Nano over adb. It must be in ADB mode and idle at the
menu. The screen will flash red/blue and the menu restarts a few times; the
USB mode never actually changes (the mode-switch tests are dry runs and show
no notices). Your USB mode, menu entries and files are not changed.
Starting in 5 s - press Ctrl-C to abort.
EOF
  sleep 5
fi

failed=0
for t in test_nanoctl.sh test_usbmode.sh test_dev.sh; do
  echo
  echo "################ $t"
  bash "./$t" || failed=$((failed + 1))
done

echo
if [ "$failed" -eq 0 ]; then
  echo "ALL TEST FILES PASSED"
else
  echo "$failed TEST FILE(S) FAILED"
  exit 1
fi
