"""Detect the Firefox major version of a Camoufox binary.

The UA we spoof MUST match the engine, or UA-vs-feature checks flag us.
Order: application.ini (authoritative, written from browser/config/version.txt
at build time) -> "camoufox-<major>" in the path -> DEFAULT_FIREFOX_MAJOR.
"""
import re
import sys
from pathlib import Path
from typing import Optional

DEFAULT_FIREFOX_MAJOR = 152
_INI_VERSION = re.compile(r"^Version=(\d+)\.", re.MULTILINE)
_PATH_VERSION = re.compile(r"camoufox-(\d+)\.")


def _ini_candidates(exe: Path):
    yield exe.parent / "application.ini"
    if exe.parent.name == "MacOS" and exe.parent.parent.name == "Contents":
        yield exe.parent.parent / "Resources" / "application.ini"


def detect_firefox_major(executable_path: Optional[str], default: int = DEFAULT_FIREFOX_MAJOR) -> int:
    if not executable_path:
        return default
    exe = Path(executable_path)
    for ini in _ini_candidates(exe):
        try:
            m = _INI_VERSION.search(ini.read_text(errors="ignore"))
        except OSError:
            continue
        if m:
            return int(m.group(1))
    m = _PATH_VERSION.search(str(exe))
    if m:
        return int(m.group(1))
    print(f"Warning: could not detect Firefox version of {exe}; assuming {default}.", file=sys.stderr)
    return default
