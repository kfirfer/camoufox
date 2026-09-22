#!/usr/bin/env bash
# fix-user-data: native macOS build wrapper (keeps the upstream Makefile clean).
#
# Firefox 152 bootstraps clang/lld 20.1.8. Configure picks the host SDK via
# `xcrun --show-sdk-path`, and the macOS 27 SDK's .tbd stubs list the new
# `arm64e.x1-macos` target, which lld 20 rejects:
#   ld64.lld: error: could not load TAPI file at .../MacOSX27.sdk/usr/lib/libSystem.tbd: malformed file
#   ... error: unknown architecture arm64e.x1-macos
# so configure fails with "Couldn't find one that works". SDKROOT alone does
# not help: without --with-macos-sdk, toolchain.configure takes xcrun's SDK dir
# and then scans it for the NEWEST SDK. So pass the SDK through MACOS_SDK_DIR
# (the env alias of --with-macos-sdk): the newest installed SDK that is >= 26.4
# (Firefox 152's mac_sdk_min_version) and has no arm64e.x1 stubs.
#
# Usage: scripts/build-macos-native.sh [make target, default: build]
set -euo pipefail
cd "$(dirname "$0")/.."

if [ -z "${MACOS_SDK_DIR:-}" ]; then
  # Newest first; read line-wise so SDK paths with spaces ("Xcode 16.app") work.
  while IFS= read -r sdk; do
    ver=$(basename "$sdk" .sdk); ver=${ver#MacOSX}
    # >= 26.4
    if [ "$(printf '%s\n26.4\n' "$ver" | sort -V | head -1)" != "26.4" ]; then continue; fi
    if grep -q 'arm64e\.x1' "$sdk/usr/lib/libSystem.tbd" 2>/dev/null; then continue; fi
    export MACOS_SDK_DIR="$sdk"; break
  done < <(shopt -s nullglob
           printf '%s\n' /Library/Developer/CommandLineTools/SDKs/MacOSX2[6-9].*.sdk \
                          /Applications/Xcode*.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX2[6-9].*.sdk \
             | sort -rV)
fi
if [ -z "${MACOS_SDK_DIR:-}" ]; then
  echo "ERROR: no macOS SDK >= 26.4 without arm64e.x1 stubs found; install one or set MACOS_SDK_DIR." >&2
  exit 1
fi
# Keep xcrun/clang consistent with the configure-selected SDK.
export SDKROOT="$MACOS_SDK_DIR"
echo "Using MACOS_SDK_DIR=$MACOS_SDK_DIR" >&2
export PATH="$HOME/.cargo/bin:$PATH"
# Put the active toolchain's real rustc/cargo first. Configure unwraps rustup
# by running the found `rustc` with argv[0]="rustup" (rust.configure,
# `rustup which rustc`). Homebrew's rustup proxies are bash wrappers that exec
# the real binary and lose argv[0], so that call runs rustc and fails with
# "multiple input filenames provided". Plain toolchain binaries skip the unwrap.
# mach prepends ~/.cargo/bin itself, so PATH order is not enough: pin RUSTC/CARGO.
if ! command -v rustup >/dev/null; then
  echo "ERROR: rustup not found on PATH (Firefox's build needs a rustup-managed toolchain)." >&2
  exit 1
fi
RUST_BIN="$(dirname "$(rustup which rustc)")"
export PATH="$RUST_BIN:$PATH" RUSTC="$RUST_BIN/rustc" CARGO="$RUST_BIN/cargo"
echo "Using Rust toolchain in $RUST_BIN" >&2
exec make "${1:-build}"
