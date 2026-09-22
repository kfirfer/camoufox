#!/usr/bin/env bash
# fix-user-data: native macOS build wrapper (keeps the upstream Makefile clean).
#
# Firefox 152 bootstraps clang/lld 20.1.8. Configure picks the host SDK via
# `xcrun --show-sdk-path`, and the macOS 27 SDK's .tbd stubs list the new
# `arm64e.x1-macos` target, which lld 20 rejects:
#   ld64.lld: error: could not load TAPI file at .../MacOSX27.sdk/usr/lib/libSystem.tbd: malformed file
#   ... error: unknown architecture arm64e.x1-macos
# so configure fails with "Couldn't find one that works". Point SDKROOT (which
# xcrun honours) at the newest installed SDK that is >= 26.4 (Firefox 152's
# mac_sdk_min_version) and has no arm64e.x1 stubs.
#
# Usage: scripts/build-macos-native.sh [make target, default: build]
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -z "${SDKROOT:-}" ]; then
  for sdk in $(ls -d /Library/Developer/CommandLineTools/SDKs/MacOSX2[6-9].*.sdk \
                     /Applications/Xcode*.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX2[6-9].*.sdk \
                     2>/dev/null | sort -rV); do
    ver=$(basename "$sdk" .sdk); ver=${ver#MacOSX}
    # >= 26.4
    if [ "$(printf '%s\n26.4\n' "$ver" | sort -V | head -1)" != "26.4" ]; then continue; fi
    if grep -q 'arm64e\.x1' "$sdk/usr/lib/libSystem.tbd" 2>/dev/null; then continue; fi
    export SDKROOT="$sdk"; break
  done
fi
if [ -z "${SDKROOT:-}" ]; then
  echo "ERROR: no macOS SDK >= 26.4 without arm64e.x1 stubs found; install one or set SDKROOT." >&2
  exit 1
fi
echo "Using SDKROOT=$SDKROOT" >&2
export PATH="$HOME/.cargo/bin:$PATH"
exec make "${1:-build}"
