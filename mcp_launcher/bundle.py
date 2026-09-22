"""macOS app-bundle workaround for camoufox.utils._load_properties().

_load_properties() looks for `properties.json` next to the executable. On
macOS the executable lives in `Camoufox.app/Contents/MacOS/` but the file
ships in `Camoufox.app/Contents/Resources/`, so a custom --executable-path
inside Contents/MacOS/ raises FileNotFoundError and launch_options() fails,
which drops humanize/showcursor and the whole BrowserForge fingerprint.
Redirect the lookup to Resources/ so CAMOU_CONFIG_* env vars are populated.
"""
import pathlib

import camoufox.utils as _camoufox_utils


def install_properties_shim() -> None:
    """Wrap camoufox.utils._load_properties with the bundle redirect. Idempotent."""
    current = _camoufox_utils._load_properties
    if getattr(current, "_camoufox_mcp_shim", False):
        return
    # Capture the original here, not at import time, so a monkeypatched
    # _load_properties (tests) is what gets wrapped.
    orig = current

    def _load_properties_macos_bundle(path=None):
        if path:
            p = pathlib.Path(str(path))
            if p.parent.name == "MacOS" and p.parent.parent.name == "Contents":
                resources_props = p.parent.parent / "Resources" / "properties.json"
                if resources_props.exists():
                    path = resources_props
        return orig(path=path)

    _load_properties_macos_bundle._camoufox_mcp_shim = True
    _camoufox_utils._load_properties = _load_properties_macos_bundle
