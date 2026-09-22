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
import pathlib
import platform
import sys
import tempfile

# The mcp_launcher package sits next to this script; Claude Code invokes the
# script by absolute path from any cwd.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from browserforge.fingerprints import Screen
from camoufox.utils import launch_options

from mcp_launcher.bundle import install_properties_shim
from mcp_launcher.fingerprint import camou_config_from_env
from mcp_launcher.mcp_config import (
    DEFAULT_MCP_PACKAGE,
    build_mcp_args,
    build_mcp_config,
    write_mcp_config,
)
from mcp_launcher.profile import scrub_stale_prefs
from mcp_launcher.user_agent import CLEAN_APP_VERSION, clean_user_agent, needs_ua_refresh
from mcp_launcher.version import detect_firefox_major


# WORKAROUND: on macOS, properties.json lives in Contents/Resources/, not next
# to the executable; see mcp_launcher/bundle.py.
install_properties_shim()


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
    parser.add_argument(
        "--locale-language",
        default=None,
        help="ISO 639-1 language code to pin via Camoufox's locale spoof.  "
             "When omitted (default), navigator.language / Accept-Language "
             "are left unspoofed and Firefox reports whatever the host OS "
             "is configured for — same as a real Chrome on this machine.",
    )
    parser.add_argument(
        "--locale-region",
        default=None,
        help="ISO 3166-1 alpha-2 region code, paired with --locale-language "
             "to form navigator.language (e.g. en-SG).  Both must be set "
             "to activate locale spoofing.  When omitted, no spoof applied.",
    )
    parser.add_argument(
        "--timezone",
        default=None,
        help="IANA timezone identifier to pin (e.g. Asia/Singapore).  When "
             "omitted (default), Firefox reads the timezone from the host "
             "OS — same as a real Chrome.  Useful if you switch VPN exits "
             "and don't want to edit flags each time.",
    )
    parser.add_argument(
        "--block-webrtc",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Disable WebRTC entirely (default: true).  When enabled, real "
             "public IP cannot leak via STUN candidates.  Use --no-block-webrtc "
             "if the target site genuinely needs WebRTC (video calls, etc.); "
             "without a Camoufox-supported `webrtc:ipv4`/`webrtc:ipv6` spoof "
             "value, the real IP will leak.",
    )
    parser.add_argument(
        "--hardware-concurrency",
        type=int,
        default=None,
        help="Override navigator.hardwareConcurrency.  Defaults to a plausible "
             "value for the current host (8 on Apple Silicon).  Camoufox patches "
             "WorkerNavigator too, so main and worker stay consistent.",
    )
    parser.add_argument(
        "--mcp-package",
        default=DEFAULT_MCP_PACKAGE,
        help="npm spec for the Playwright MCP server (default: %(default)s). "
             "Must bundle Playwright < 1.63 (i.e. @playwright/mcp <= 0.0.78).",
    )
    args = parser.parse_args()

    user_data_dir = args.user_data_dir
    persistent = user_data_dir is not None

    if persistent:
        os.makedirs(user_data_dir, exist_ok=True)

        # Strip stale locale user prefs from the persistent profile's prefs.js
        # when locale isn't being pinned this session.  Firefox writes these on
        # every shutdown when a spoof was active, and they then override our
        # MaskConfig-level behavior on the next launch even after we've stopped
        # pinning (Accept-Language kept reporting en-SG long after
        # `--locale-language` was removed).
        scrub_stale_prefs(
            os.path.join(user_data_dir, "prefs.js"),
            pinning_locale=bool(args.locale_language and args.locale_region),
        )

    # Detect the Firefox major version of the binary (application.ini, then
    # the path, then a default).  Used to derive a version-matched UA so the
    # spoofed UA stays in sync with the actual Firefox engine.  A stale
    # Firefox/<old> UA claim on a newer engine is a strong bot signal
    # (UA-vs-engine mismatch).
    ff_version = detect_firefox_major(args.executable_path)

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

    # Pick navigator.hardwareConcurrency.
    #
    # Camoufox now patches BOTH Navigator::HardwareConcurrency (main thread)
    # AND WorkerNavigator::HardwareConcurrency (worker thread) to read from
    # MaskConfig — see dom/workers/WorkerNavigator.cpp:268.  Setting this
    # config key propagates to both contexts, so main↔worker are guaranteed
    # consistent regardless of what Firefox would have detected from the host.
    #
    # The historical bug (#364) where Workers leaked the efficiency-core count
    # on Apple Silicon is no longer present in this build, so we can pick a
    # realistic value for the spoofed OS instead of pinning to the host's
    # leaked value.  Real Apple Silicon Macs report 8 (M1/M2 base) up through
    # 12 (M3/M4 Pro) cores via navigator.hardwareConcurrency.
    if args.hardware_concurrency is not None:
        worker_hw_concurrency = args.hardware_concurrency
    elif platform.system() == "Darwin":
        # Default to 8 — matches M1/M2 base config, the most common Mac.
        worker_hw_concurrency = 8
    else:
        worker_hw_concurrency = os.cpu_count() or 4

    # Build the user config overlay.  Explicit values here take precedence
    # over BrowserForge auto-population and saved_config alike.
    user_config = dict(saved_config) if saved_config else {}
    user_config["navigator.hardwareConcurrency"] = worker_hw_concurrency

    # FIX: Camoufox leaks its identity in the User-Agent string (e.g.
    # "... Gecko/20100101 Camoufox/<version>").  Override with a clean stock
    # Firefox macOS UA derived from ff_version (see mcp_launcher/user_agent.py).
    _CLEAN_UA = clean_user_agent(ff_version)

    # Refresh the saved UA if it (a) leaks "Camoufox", or (b) pins a stale
    # Firefox major version that no longer matches the current binary.
    if needs_ua_refresh(user_config.get("navigator.userAgent", ""), ff_version):
        user_config.pop("navigator.userAgent", None)
        user_config.pop("navigator.appVersion", None)
        user_config.pop("headers.User-Agent", None)
    user_config.setdefault("navigator.userAgent", _CLEAN_UA)
    user_config.setdefault("navigator.appVersion", CLEAN_APP_VERSION)
    user_config.setdefault("headers.User-Agent", _CLEAN_UA)

    # Cursor movement & highlighter (optional, config dict properties).
    # See: https://camoufox.com/fingerprint/cursor-movement/
    if args.humanize and args.humanize_min_time is not None:
        user_config["humanize:minTime"] = args.humanize_min_time
    if args.showcursor:
        user_config["showcursor"] = True

    # Pin HTTPS Accept-Encoding to the stock Firefox default so it cannot drift
    # via a stale BrowserForge fingerprint (Bug 2 of camoufox#473).  This value
    # flows into nsHttpHandler::SetAcceptEncodings via MaskConfig and — with
    # the C++ patch from PR #474 — applies only to HTTPS, leaving HTTP and
    # dictionary compression paths untouched.
    user_config["headers.Accept-Encoding"] = "gzip, deflate, br, zstd"

    # Locale + timezone: by default we DON'T spoof these — Firefox reads them
    # from the host OS, same as real Chrome.  This is the right choice when the
    # host is behind a VPN that rotates exit locations: a pinned spoof would
    # drift out of sync with the VPN IP every time it changes, creating the
    # very IP↔TZ inconsistency that anti-bot systems flag.  Letting the host
    # values through means there's no flag to edit on each VPN switch.
    #
    # Always strip any stale locale/TZ keys carried over from saved_config or
    # BrowserForge auto-population — those would otherwise re-introduce a
    # spoof we no longer want.  Pin only when the caller explicitly supplies
    # an override via --locale-language + --locale-region (both required) or
    # --timezone.
    for k in (
        "navigator.language",
        "navigator.languages",
        "headers.Accept-Language",
        "locale:language",
        "locale:region",
        "timezone",
    ):
        user_config.pop(k, None)
    if args.locale_language and args.locale_region:
        user_config["locale:language"] = args.locale_language
        user_config["locale:region"] = args.locale_region
    if args.timezone:
        user_config["timezone"] = args.timezone

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
    # WebRTC: disable entirely to prevent the real public IP leaking via STUN
    # ICE candidates.  Camoufox's `block_webrtc=True` sets
    # `media.peerconnection.enabled=false` so navigator.connection / WebRTC
    # APIs return unsupported — same posture as `privacy.resistFingerprinting`
    # users.  If --no-block-webrtc is passed, the real IP will leak unless a
    # `webrtc:ipv4`/`webrtc:ipv6` spoof is configured (currently not wired).
    if args.block_webrtc:
        launch_kwargs["block_webrtc"] = True
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
        launch_kwargs.setdefault("ff_version", ff_version)

    try:
        config = launch_options(**launch_kwargs)
    except Exception as e:
        if not args.executable_path:
            raise
        # Launching without a fingerprint (no CAMOU_CONFIG_*, no humanize,
        # no UA/AE pins) is worse than not launching: fail loudly.
        if os.environ.get("CAMOUFOX_MCP_ALLOW_EMPTY_CONFIG") != "1":
            print(f"ERROR: launch_options() failed: {e!r}. Refusing to start without a fingerprint; "
                  f"re-run with CAMOUFOX_MCP_ALLOW_EMPTY_CONFIG=1 to override.", file=sys.stderr)
            sys.exit(2)
        print(f"WARNING: launch_options() failed: {e!r}. Starting WITHOUT a fingerprint because "
              f"CAMOUFOX_MCP_ALLOW_EMPTY_CONFIG=1.", file=sys.stderr)
        config = {}
    config = {k: v for k, v in config.items() if v is not None}

    executable_path = args.executable_path or config.get("executable_path", "")
    camoufox_env = config.get("env", {})
    firefox_user_prefs = config.get("firefox_user_prefs", {})

    # Save the generated fingerprint config for next session.
    # The CAMOU_CONFIG env var contains the full fingerprint JSON.
    # Strip locale/TZ keys before saving when no spoof was requested — without
    # this, BrowserForge-populated values would re-contaminate the saved file
    # and re-introduce a region pin on the next launch.
    _save_strip_keys = set()
    if not (args.locale_language and args.locale_region):
        _save_strip_keys.update({
            "locale:language",
            "locale:region",
            "navigator.language",
            "navigator.languages",
            "headers.Accept-Language",
        })
    if not args.timezone:
        _save_strip_keys.add("timezone")
    # The config is chunked across CAMOU_CONFIG_1..N once it passes 32767
    # chars; reading only the first chunk would silently skip the save.
    try:
        fp_config = camou_config_from_env(camoufox_env)
    except ValueError as e:
        print(f"Warning: could not parse CAMOU_CONFIG; fingerprint not saved ({e}).", file=sys.stderr)
        fp_config = None
    if fp_config:
        if _save_strip_keys:
            fp_config = {kk: vv for kk, vv in fp_config.items()
                         if kk not in _save_strip_keys}
        _save_config(fp_config)

    # hardwareConcurrency: Camoufox now patches both Navigator and
    # WorkerNavigator (dom/workers/WorkerNavigator.cpp:268) to read from
    # MaskConfig, so the user_config["navigator.hardwareConcurrency"] set
    # above propagates to both contexts.  No `dom.maxHardwareConcurrency`
    # pref needed — that ceiling-only pref couldn't raise a low detected
    # value anyway, so it was redundant.

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

    mcp_config = build_mcp_config(
        executable_path=executable_path,
        headless=bool(headless_mode),
        firefox_user_prefs=firefox_user_prefs,
        env=camoufox_env,
        user_agent=_CLEAN_UA,
        user_data_dir=user_data_dir if persistent else None,
        window_size=window_size,
    )

    # Write config to a temp file (0600: launchOptions.env holds the host env)
    config_file = os.path.join(
        tempfile.gettempdir(), "camoufox-mcp-config.json"
    )
    write_mcp_config(config_file, mcp_config)

    # Merge current env with all camoufox env vars
    env = os.environ.copy()
    for k, v in camoufox_env.items():
        env[k] = v

    mcp_args = build_mcp_args(config_file, window_size, args.mcp_package)

    # Launch MCP server - exec replaces this process so stdio is passed through
    os.execvpe("npx", mcp_args, env)


if __name__ == "__main__":
    main()
