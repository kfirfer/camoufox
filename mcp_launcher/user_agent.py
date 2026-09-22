"""Clean, version-matched Firefox macOS User-Agent for the MCP launcher.

Camoufox leaks its identity in the default UA (e.g. "... Gecko/20100101
Camoufox/152.0.4-beta.30"), a one-line giveaway for any anti-bot system doing
UA substring checks. The launcher overrides it with a stock Firefox macOS UA
derived from the detected Firefox major, so a Camoufox upgrade bumps the UA
automatically. Verified against stock Firefox on macOS:
  navigator.userAgent  -> "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:<N>.0) Gecko/20100101 Firefox/<N>.0"
  navigator.appVersion -> "5.0 (Macintosh)"
  navigator.oscpu      -> "Intel Mac OS X 10.15"   (frozen by UA reduction; same on Apple Silicon)
  navigator.platform   -> "MacIntel"               (frozen; same on Apple Silicon)
"""
import re

CLEAN_APP_VERSION = "5.0 (Macintosh)"
_RV = re.compile(r"rv:(\d+)\.")


def clean_user_agent(ff_major: int) -> str:
    # Firefox UA reduction freezes macOS at "Intel Mac OS X 10.15" (also on Apple Silicon).
    return (f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:{ff_major}.0) "
            f"Gecko/20100101 Firefox/{ff_major}.0")


def needs_ua_refresh(saved_ua: str, ff_major: int) -> bool:
    """True if a saved UA (a) leaks "Camoufox", (b) has no rv: version, or
    (c) pins a Firefox major that no longer matches the current binary.
    Without (c), a saved fingerprint would freeze an old rv: after upgrading
    the binary, producing a UA-vs-engine mismatch."""
    m = _RV.search(saved_ua or "")
    return "Camoufox" in (saved_ua or "") or m is None or int(m.group(1)) != ff_major
