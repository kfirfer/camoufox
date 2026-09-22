"""Read the Camoufox fingerprint config back out of the launch env."""
import json
from typing import Optional


def camou_config_from_env(env: dict) -> Optional[dict]:
    """Reassemble CAMOU_CONFIG_<n> chunks in numeric order and parse them.

    launch_options() splits the config JSON across CAMOU_CONFIG_1..N (macOS
    caps an env value at 32767 chars), so a single chunk is only a fragment
    once the fingerprint grows past that. Returns None when there are no
    chunks; raises ValueError on malformed JSON.
    """
    chunks = sorted(
        (int(k.rsplit("_", 1)[1]), v)
        for k, v in env.items()
        if k.startswith("CAMOU_CONFIG_") and k.rsplit("_", 1)[1].isdigit()
    )
    if not chunks:
        return None
    return json.loads("".join(v for _, v in chunks))
