"""Build and write the @playwright/mcp config and command line."""
import json
import os
from typing import Dict, List, Optional, Tuple

# Must bundle Playwright < 1.63 (pythonlib's tested ceiling), i.e.
# @playwright/mcp <= 0.0.78. See the upgrade plan, Task 4.4.
DEFAULT_MCP_PACKAGE = "@playwright/mcp@0.0.68"


def build_mcp_config(
    *,
    executable_path: str,
    headless: bool,
    firefox_user_prefs: dict,
    env: dict,
    user_agent: str,
    user_data_dir: Optional[str],
    window_size: Optional[Tuple[int, int]],
) -> dict:
    browser_config = {
        "browserName": "firefox",
        "launchOptions": {
            "executablePath": executable_path,
            "headless": bool(headless),
            "firefoxUserPrefs": firefox_user_prefs,
            # Pass all Camoufox env vars (fingerprint config, display, etc.)
            "env": {k: v for k, v in env.items()},
        },
    }
    if user_data_dir is not None:
        browser_config["userDataDir"] = user_data_dir

    # Accept-Encoding is handled at the C++ level by the patched
    # nsHttpHandler::SetAcceptEncodings (camoufox#473, patches/network-patches.patch).
    # The override is honoured only on the HTTPS branch and the HTTP/dictionary
    # paths use real Firefox values, so no Playwright-layer override is needed:
    # pages decode br/zstd correctly AND outbound Accept-Encoding matches stock Firefox.
    context_options = {
        "colorScheme": "dark",
        "userAgent": user_agent,
        "extraHTTPHeaders": {
            "user-agent": user_agent,
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
    #       playwright#39841)
    #     * contextOptions in config JSON sometimes ignored by MCP server
    #       (microsoft/playwright-mcp#1092)
    #     * Persistent profiles cache old viewport sizes across sessions
    #       (Playwright MCP docs: viewport "saved and reused")
    #
    # Fix: Explicitly set viewport to match Camoufox's window dimensions.
    # We set it in BOTH contextOptions (for newContext) AND as the
    # --viewport-size CLI flag (see build_mcp_args), the most reliable path
    # in Playwright MCP. This ensures Playwright and Camoufox agree on the
    # content area size.
    if window_size is not None:
        context_options["viewport"] = {
            "width": window_size[0],
            "height": window_size[1],
        }

    browser_config["contextOptions"] = context_options

    return {
        "browser": browser_config,
        "capabilities": ["core", "pdf", "vision"],
    }


def build_mcp_args(
    config_file: str,
    window_size: Optional[Tuple[int, int]],
    mcp_package: str = DEFAULT_MCP_PACKAGE,
) -> List[str]:
    args = ["npx", mcp_package, "--config", config_file]
    # In headed mode, also pass --viewport-size as CLI flag.
    # This is the most reliable way to set viewport in Playwright MCP —
    # it's processed at the server level and overrides any cached viewport
    # from persistent profiles.  contextOptions.viewport alone is unreliable
    # on Firefox (see build_mcp_config).
    if window_size is not None:
        args.extend(["--viewport-size", f"{window_size[0]}x{window_size[1]}"])
    return args


def write_mcp_config(path: str, config: Dict) -> None:
    """Write the MCP config readable by the owner only.

    launchOptions.env carries the full host environment (launch_options()
    defaults env to os.environ, and Playwright's env *replaces* the browser
    environment), so the file can contain secrets. fchmod also tightens a
    pre-existing file that O_CREAT's mode would leave untouched.
    """
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(config, f)
