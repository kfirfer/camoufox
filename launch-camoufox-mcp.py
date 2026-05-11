#!/usr/bin/env python3
"""
Wrapper script that launches the Playwright MCP server with Camoufox browser
and persistent profile support.

Usage (as MCP command):
    python3 /path/to/launch-camoufox-mcp.py [--user-data-dir /path/to/profile]

If --user-data-dir is provided, the browser launches with a persistent profile.
If omitted, the browser launches without persistent context (ephemeral session).

This replaces the need for:
  1. Running start-camoufox-server.py separately
  2. Using remoteEndpoint in the MCP config (which ignores persistent context)

Instead, this script:
  1. Generates Camoufox fingerprint config via BrowserForge
  2. Creates a dynamic MCP config with userDataDir + executablePath
  3. Launches the MCP server with the correct environment
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile

from browserforge.fingerprints import Screen
from camoufox.utils import launch_options


# Persistent fingerprint config path — reusing the same config across sessions
# prevents CreepJS from seeing each launch as a "new visitor" with a different
# fingerprint hash. See: https://github.com/daijro/camoufox/issues/328
FINGERPRINT_CONFIG_PATH = os.path.join(
    os.path.expanduser("~"), ".camoufox-mcp-fingerprint.json"
)


def _load_saved_config():
    """Load previously saved fingerprint config for session consistency."""
    if os.path.exists(FINGERPRINT_CONFIG_PATH):
        try:
            with open(FINGERPRINT_CONFIG_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_config(config_dict):
    """Save fingerprint config so future sessions reuse the same identity."""
    try:
        with open(FINGERPRINT_CONFIG_PATH, "w") as f:
            json.dump(config_dict, f, indent=2)
    except IOError:
        pass


def _detect_worker_hardware_concurrency():
    """Return the hardwareConcurrency value that Firefox Web Workers report.

    Camoufox's C++ patch spoofs navigator.hardwareConcurrency in the main
    thread but does NOT patch WorkerNavigator, so Workers return the real
    value from the OS/browser.  On macOS Apple Silicon, Firefox Workers
    report the efficiency-core count (hw.perflevel1.logicalcpu = 4 on M1/
    M2/M3), not the total logical CPU count.  We detect this so we can pin
    the fingerprint config to the same value and avoid a main↔worker
    mismatch that trips bot detection.
    """
    if platform.system() == "Darwin":
        # Apple Silicon has performance + efficiency cores.  Firefox Workers
        # report the efficiency-core count.
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.perflevel1.logicalcpu"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip().isdigit():
                return int(result.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            pass
        # Intel Mac or sysctl unavailable — fall through to os.cpu_count()

    return os.cpu_count() or 4



def main():
    parser = argparse.ArgumentParser(description="Launch Playwright MCP with Camoufox")
    parser.add_argument(
        "--user-data-dir",
        default=None,
        help="Path to persistent browser profile directory. "
             "If omitted, launches an ephemeral (non-persistent) session.",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run browser in headless mode (default: true). "
             "Use --no-headless to launch a visible browser window.",
    )
    parser.add_argument(
        "--humanize",
        action="store_true",
        default=False,
        help="Enable human-like cursor movement. "
             "If omitted, cursor movement config is not set at all.",
    )
    parser.add_argument(
        "--humanize-max-time",
        type=float,
        default=None,
        help="Maximum time in seconds for human-like cursor movement (e.g. 1.5). "
             "Only effective when --humanize is enabled.",
    )
    parser.add_argument(
        "--humanize-min-time",
        type=float,
        default=None,
        help="Minimum time in seconds for human-like cursor movement (e.g. 0.5). "
             "Only effective when --humanize is enabled.",
    )
    parser.add_argument(
        "--showcursor",
        action="store_true",
        default=False,
        help="Enable the cursor highlighter (not visible to the page). "
             "If omitted, cursor highlighter config is not set at all.",
    )
    parser.add_argument(
        "--executable-path",
        default=None,
        help="Path to a custom Camoufox binary. "
             "If omitted, uses the default from camoufox.utils.launch_options().",
    )
    args = parser.parse_args()

    user_data_dir = args.user_data_dir
    persistent = user_data_dir is not None

    if persistent:
        os.makedirs(user_data_dir, exist_ok=True)

    # Load saved fingerprint config for cross-session consistency
    saved_config = _load_saved_config()

    # Determine headless mode:
    # --no-headless → visible browser window
    # --headless (default):
    #   - Linux: use "virtual" (Xvfb virtual display) for best stealth
    #   - macOS/other: use True (Camoufox's built-in headless patches achieve
    #     0% headless / 0% stealth on CreepJS even without virtual display)
    if not args.headless:
        headless_mode = False
    else:
        is_linux = platform.system() == "Linux"
        headless_mode = "virtual" if is_linux else True

    # Detect the hardwareConcurrency value that Firefox Web Workers will report.
    #
    # BUG: Camoufox's C++ patch for navigator.hardwareConcurrency only covers
    # the main thread (nsGlobalWindowInner), NOT WorkerNavigator inside Web
    # Workers.  deviceandbrowserinfo.com's "hasInconsistentWorkerValues" check
    # compares hardwareConcurrency between main thread and a Web Worker — if
    # BrowserForge picks a different value, the Worker leaks the unpatched
    # value and triggers bot detection.
    #
    # On macOS Apple Silicon, Firefox Workers report the *efficiency* core
    # count (hw.perflevel1.logicalcpu), not the total.  We detect this and
    # pin the fingerprint to that value so both contexts agree.
    # See: https://github.com/daijro/camoufox/issues/364
    worker_hw_concurrency = _detect_worker_hardware_concurrency()

    # Build the user config overlay.  Explicit values here take precedence
    # over BrowserForge auto-population and saved_config alike.
    user_config = dict(saved_config) if saved_config else {}
    # Pin hardwareConcurrency to the Worker-reported value to avoid mismatch.
    user_config["navigator.hardwareConcurrency"] = worker_hw_concurrency

    # FIX: Camoufox 146.0.1-beta.25 leaks its identity in the User-Agent string
    # (e.g. "Mozilla/5.0 (...) Gecko/20100101 Camoufox/146.0.1-beta.25").  This
    # is a one-line giveaway for any anti-bot system doing UA substring checks.
    # Override the UA with a clean stock Firefox 146 macOS string and pin the
    # related navigator/header fields so they all agree.  Verified at
    # https://bot.sannysoft.com/ — UA row should read pure Firefox.
    _CLEAN_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:146.0) "
        "Gecko/20100101 Firefox/146.0"
    )
    _CLEAN_APP_VERSION = "5.0 (Macintosh)"
    # Override only if a saved config didn't already pin a clean (non-Camoufox)
    # UA — saved_config wins for cross-session consistency.
    if "Camoufox" in user_config.get("navigator.userAgent", ""):
        user_config.pop("navigator.userAgent", None)
        user_config.pop("navigator.appVersion", None)
        user_config.pop("headers.User-Agent", None)
    user_config.setdefault("navigator.userAgent", _CLEAN_UA)
    user_config.setdefault("navigator.appVersion", _CLEAN_APP_VERSION)
    user_config.setdefault("headers.User-Agent", _CLEAN_UA)

    # Cursor movement & highlighter (optional, config dict properties).
    # See: https://camoufox.com/fingerprint/cursor-movement/
    if args.humanize and args.humanize_min_time is not None:
        user_config["humanize:minTime"] = args.humanize_min_time
    if args.showcursor:
        user_config["showcursor"] = True

    # In non-headless (headed) mode, pin screen dimensions to the actual
    # physical display to avoid rendering defects: flickering margins,
    # oversized windows, constant screen-size changes, and broken layouts.
    # Known Camoufox bugs in headed mode:
    #   - https://github.com/daijro/camoufox/issues/499  (flickering/oversized)
    #   - https://github.com/daijro/camoufox/issues/118  (wrong screen/window)
    #   - https://github.com/daijro/camoufox/issues/425  (DPI scaling)
    #   - https://github.com/daijro/camoufox/issues/532  (constant size change)
    if not args.headless:
        # MacBook Pro 14" M3 default logical resolution (3024x1964 @ 2x Retina)
        screen_w, screen_h = 1512, 982

        # Use a small range so BrowserForge can still find a matching
        # fingerprint, while staying close to the real display size.
        screen_constraint = Screen(
            min_width=screen_w - 100, max_width=screen_w,
            min_height=screen_h - 100, max_height=screen_h,
        )
        # Realistic browser window: slightly smaller than screen to account
        # for macOS menu bar (~25px) and Dock (~70px).
        window_size = (screen_w - 40, screen_h - 120)
    else:
        screen_constraint = Screen(
            min_width=1280, max_width=1920,
            min_height=800, max_height=1080,
        )
        window_size = None

    # Generate Camoufox launch config with fingerprint.
    # Key fixes for anti-detection:
    #   1. os="macos" — matches this host's platform, preventing the
    #      UA/platform mismatch CreepJS detected (Windows UA + MacIntel platform
    #      + -apple-system:Mac CSS hint).
    #   2. screen constraints — generates realistic monitor dimensions.
    #   3. config=user_config — reuses fingerprint properties (fonts:spacing_seed,
    #      navigator props, etc.) so CreepJS sees a returning visitor, not a new
    #      one each session, AND pins hardwareConcurrency to the real CPU count
    #      so main thread and Web Worker report the same value.
    launch_kwargs = dict(
        headless=headless_mode,
        os="macos",
        screen=screen_constraint,
        config=user_config,
        i_know_what_im_doing=True,  # suppress warning for manual navigator overrides
    )
    # Human-like cursor movement (optional, launch kwarg).
    # See: https://camoufox.com/fingerprint/cursor-movement/
    # humanize accepts True (default maxTime) or a float (custom maxTime).
    if args.humanize:
        if args.humanize_max_time is not None:
            launch_kwargs["humanize"] = args.humanize_max_time
        else:
            launch_kwargs["humanize"] = True
    if window_size is not None:
        launch_kwargs["window"] = window_size
    if persistent:
        launch_kwargs["persistent_context"] = True
        launch_kwargs["user_data_dir"] = user_data_dir

    if args.executable_path:
        # When a custom executable is provided, bypass the installed-version
        # check by injecting the path into launch_kwargs so launch_options()
        # doesn't need to locate an installed Camoufox binary.
        launch_kwargs["executable_path"] = args.executable_path
        # Also pass ff_version to bypass installed_verstr() which raises
        # CamoufoxNotInstalled even when executable_path is provided.
        # Without this, launch_options() fails and falls back to config={},
        # losing ALL fingerprint config including humanize/showcursor.
        if "ff_version" not in launch_kwargs:
            # Extract version from the executable path if possible (e.g.
            # "camoufox-146.0.1-beta.25" → 146), otherwise default to 146.
            import re
            match = re.search(r'camoufox-(\d+)', args.executable_path)
            launch_kwargs["ff_version"] = int(match.group(1)) if match else 146

    try:
        config = launch_options(**launch_kwargs)
    except Exception as e:
        if args.executable_path:
            # launch_options() failed (e.g. CamoufoxNotInstalled) but we have
            # a custom binary — generate a minimal config and continue.
            import sys
            print(f"Warning: launch_options() failed ({e}), using minimal config with custom executable.", file=sys.stderr)
            config = {}
        else:
            raise
    config = {k: v for k, v in config.items() if v is not None}

    executable_path = args.executable_path or config.get("executable_path", "")
    camoufox_env = config.get("env", {})
    firefox_user_prefs = config.get("firefox_user_prefs", {})

    # Save the generated fingerprint config for next session.
    # The CAMOU_CONFIG env var contains the full fingerprint JSON.
    for k, v in camoufox_env.items():
        if k.startswith("CAMOU_CONFIG") and v:
            try:
                fp_config = json.loads(v) if isinstance(v, str) else v
                _save_config(fp_config)
            except (json.JSONDecodeError, TypeError):
                pass
            break

    # Force-align hardwareConcurrency across main thread and Web Workers.
    #
    # Camoufox's MaskConfig C++ system does NOT intercept
    # navigator.hardwareConcurrency — the config value is accepted but never
    # read at runtime.  On macOS Apple Silicon the main thread sees all 16
    # logical CPUs while Workers see only 4 (efficiency cores), triggering
    # deviceandbrowserinfo.com's "hasInconsistentWorkerValues" check.
    #
    # Firefox's "dom.maxHardwareConcurrency" preference caps the value
    # reported by BOTH Navigator AND WorkerNavigator at the engine level,
    # so setting it to the Worker's natural value (4) makes them agree.
    firefox_user_prefs["dom.maxHardwareConcurrency"] = worker_hw_concurrency

    # WORKAROUND: Camoufox Accept-Encoding spoofing bug (#473, #479, #535, #537).
    #
    # Root cause (confirmed by pim97's analysis in #473):
    #   Bug 1: The C++ patch in nsHttpHandler::SetAcceptEncodings() only
    #          overrides mHttpsAcceptEncodings and returns early, leaving
    #          mHttpAcceptEncodings at a stale/wrong value.  HTTP requests
    #          use incorrect encoding expectations.
    #   Bug 2: Older BrowserForge fingerprints omit "zstd" which Firefox
    #          126+ supports natively.  CDNs (e.g. WP Rocket) may send
    #          zstd/brotli content the browser then fails to decompress,
    #          resulting in binary garbage / mojibake in the DOM.
    #
    # Community-confirmed fix (icepaq collaborator, #535 pinned comment):
    #   Set extra_http_headers={"accept-encoding": "identity"} at the
    #   browser context level (NOT page level).  This overrides the
    #   broken C++ Accept-Encoding header for all requests.
    #
    # Using "gzip, deflate" instead of "identity" preserves basic
    # compression while avoiding the broken brotli/zstd paths.
    #
    # firefoxUserPrefs alone may NOT fix this because the C++ patch in
    # SetAcceptEncodings() intercepts before Firefox reads the prefs.
    # The extra HTTP headers approach works at Playwright's network layer,
    # which overrides the header *after* Firefox sets it.
    #
    # PRs attempting C++ fix: #474, #517 (both unmerged as of Mar 2026).
    # See also: #479, #522, #524, #537.
    #
    # We apply BOTH approaches as defense-in-depth:
    #   1. firefoxUserPrefs  — may help if C++ patch doesn't intercept
    #   2. contextOptions.extraHTTPHeaders — proven workaround via Playwright
    firefox_user_prefs["network.http.accept-encoding"] = "gzip, deflate"
    firefox_user_prefs["network.http.accept-encoding.secure"] = "gzip, deflate, br, zstd"

    # Defense-in-depth UA override at the Firefox engine level.  If the
    # Camoufox MaskConfig path misses (or saved_config gets stale), this
    # pref forces the same clean UA on every HTTP request and on
    # navigator.userAgent before any JS runs.
    firefox_user_prefs["general.useragent.override"] = _CLEAN_UA

    # Build MCP config JSON.
    browser_config = {
        "browserName": "firefox",
        "launchOptions": {
            "executablePath": executable_path,
            "headless": bool(headless_mode),
            "firefoxUserPrefs": firefox_user_prefs,
            "env": {
                # Pass all Camoufox env vars (fingerprint config, display, etc.)
                k: v
                for k, v in camoufox_env.items()
            },
        },
    }
    if persistent:
        browser_config["userDataDir"] = user_data_dir

    # Context-level Accept-Encoding override (proven workaround for #473).
    # Playwright MCP's contextOptions maps to browser.newContext() options.
    # extraHTTPHeaders overrides the Accept-Encoding header for ALL requests
    # made by pages in this context, bypassing the broken C++ patch entirely.
    context_options = {
        "colorScheme": "dark",
        "userAgent": _CLEAN_UA,
        "extraHTTPHeaders": {
            "accept-encoding": "gzip, deflate",
            "user-agent": _CLEAN_UA,
        },
    }

    # WORKAROUND: Headed-mode viewport/window rendering bugs.
    #
    # Camoufox has known issues in headed (non-headless) mode where the
    # browser window renders incorrectly — flickering margins, oversized
    # windows, cut-off content, and constant size changes:
    #   - https://github.com/daijro/camoufox/issues/499  (flickering margins)
    #   - https://github.com/daijro/camoufox/issues/425  (oversized window)
    #   - https://github.com/daijro/camoufox/issues/532  (constant size change)
    #   - https://github.com/daijro/camoufox/issues/118  (wrong screen/window)
    #
    # Root cause: Playwright MCP applies a default viewport of 1280x720 when
    # no viewport is specified.  This conflicts with Camoufox's window
    # dimensions, causing content to render at 1280x720 inside a larger
    # window — producing "half page" rendering with blank/cut-off areas.
    #
    # Additional complications:
    #   - contextOptions.viewport = null does NOT reliably work:
    #     * Firefox ignores contextOptions.screen entirely (microsoft/
    #       playwright#39841, opened 2026-03-25, fix PR not merged)
    #     * contextOptions in config JSON sometimes ignored by MCP server
    #       (microsoft/playwright-mcp#1092)
    #     * Persistent profiles cache old viewport sizes across sessions
    #       (Playwright MCP docs: viewport "saved and reused")
    #
    # Fix: Explicitly set viewport to match Camoufox's window dimensions.
    # We set it in BOTH contextOptions (for newContext) AND as the
    # --viewport-size CLI flag (the most reliable path in Playwright MCP).
    # This ensures Playwright and Camoufox agree on the content area size.
    if window_size is not None:
        context_options["viewport"] = {
            "width": window_size[0],
            "height": window_size[1],
        }

    browser_config["contextOptions"] = context_options

    mcp_config = {
        "browser": browser_config,
        "capabilities": ["core", "pdf"],
        "vision": True,
    }

    # Write config to a temp file
    config_file = os.path.join(
        tempfile.gettempdir(), "camoufox-mcp-config.json"
    )
    with open(config_file, "w") as f:
        json.dump(mcp_config, f)

    # Merge current env with all camoufox env vars
    env = os.environ.copy()
    for k, v in camoufox_env.items():
        env[k] = v

    # Build MCP server args
    mcp_args = [
        "npx",
        "@playwright/mcp@0.0.68",
        "--config",
        config_file,
    ]

    # In headed mode, also pass --viewport-size as CLI flag.
    # This is the most reliable way to set viewport in Playwright MCP —
    # it's processed at the server level and overrides any cached viewport
    # from persistent profiles.  contextOptions.viewport alone is unreliable
    # on Firefox (see comments above).
    if window_size is not None:
        mcp_args.extend([
            "--viewport-size",
            f"{window_size[0]}x{window_size[1]}",
        ])

    # Launch MCP server - exec replaces this process so stdio is passed through
    os.execvpe("npx", mcp_args, env)


if __name__ == "__main__":
    main()
