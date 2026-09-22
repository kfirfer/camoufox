"""Persistent-profile hygiene for the MCP launcher."""
import os

# Locale prefs Firefox writes to prefs.js on shutdown while a locale spoof is
# active. Left in place, they override MaskConfig on the next launch even
# after we stopped pinning (Accept-Language kept reporting en-SG long after
# `--locale-language` was removed).
STALE_LOCALE_PREFS = (
    "intl.accept_languages",
    "intl.locale.requested",
    "general.useragent.locale",
)


def scrub_stale_prefs(prefs_path: str, pinning_locale: bool) -> int:
    """Strip stale locale user prefs from a profile's prefs.js when this
    session does not pin a locale. prefs.js writes are atomic per line, so a
    targeted line filter is safe. Returns the number of lines removed; a
    missing file or an I/O error removes nothing."""
    if pinning_locale or not os.path.exists(prefs_path):
        return 0
    try:
        with open(prefs_path, "r") as f:
            lines = f.readlines()
        kept = [
            line for line in lines
            if not any(line.startswith(f'user_pref("{name}"') for name in STALE_LOCALE_PREFS)
        ]
        if len(kept) != len(lines):
            with open(prefs_path, "w") as f:
                f.writelines(kept)
        return len(lines) - len(kept)
    except (IOError, OSError):
        return 0
