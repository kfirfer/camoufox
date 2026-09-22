"""End-to-end checks of launch-camoufox-mcp.py that need no browser."""
import os
import pathlib
import subprocess
import sys

LAUNCHER = pathlib.Path(__file__).resolve().parents[2] / "launch-camoufox-mcp.py"


def _run(tmp_path):
    exe = tmp_path / "camoufox"; exe.touch()  # no properties.json -> launch_options() fails
    env = {**os.environ, "TMPDIR": str(tmp_path)}
    env.pop("CAMOUFOX_MCP_ALLOW_EMPTY_CONFIG", None)
    return subprocess.run([sys.executable, str(LAUNCHER), "--executable-path", str(exe)],
                          env=env, capture_output=True, text=True, timeout=120)


def test_refuses_to_start_without_fingerprint(tmp_path):
    r = _run(tmp_path)
    assert r.returncode == 2 and "Refusing to start without a fingerprint" in r.stderr
    assert not (tmp_path / "camoufox-mcp-config.json").exists()


def test_help_lists_every_flag():
    out = subprocess.run([sys.executable, str(LAUNCHER), "--help"], capture_output=True, text=True).stdout
    for flag in ("--user-data-dir", "--headless", "--humanize", "--humanize-max-time", "--humanize-min-time",
                 "--showcursor", "--executable-path", "--locale-language", "--locale-region", "--timezone",
                 "--block-webrtc", "--hardware-concurrency", "--mcp-package"):
        assert flag in out, flag
