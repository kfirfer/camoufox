import json

import pytest

from mcp_launcher.fingerprint import camou_config_from_env


def test_single_chunk():
    assert camou_config_from_env({"CAMOU_CONFIG_1": '{"a": 1}', "PATH": "/bin"}) == {"a": 1}


def test_reassembles_chunks_in_numeric_order():
    blob = json.dumps({"k": "x" * 50, "n": 2})
    parts = [blob[i:i + 7] for i in range(0, len(blob), 7)]
    assert len(parts) >= 10  # CAMOU_CONFIG_10 must sort after _9, not after _1
    env = {f"CAMOU_CONFIG_{i}": p for i, p in enumerate(parts, 1)}
    assert camou_config_from_env(env) == {"k": "x" * 50, "n": 2}


def test_no_chunks_returns_none():
    assert camou_config_from_env({"PATH": "/bin"}) is None


def test_invalid_json_raises():
    with pytest.raises(ValueError):
        camou_config_from_env({"CAMOU_CONFIG_1": '{"a": '})
