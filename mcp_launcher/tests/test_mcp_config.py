from mcp_launcher.mcp_config import build_mcp_config, DEFAULT_MCP_PACKAGE
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:152.0) Gecko/20100101 Firefox/152.0"
def _cfg(**kw):
    base = dict(executable_path="/b", headless=True, firefox_user_prefs={"a": 1}, env={"CAMOU_CONFIG_1": "{}"},
                user_agent=UA, user_data_dir=None, window_size=None)
    base.update(kw); return build_mcp_config(**base)
def test_capabilities_include_vision():
    assert _cfg()["capabilities"] == ["core", "pdf", "vision"]
def test_persistent_profile_sets_user_data_dir():
    assert _cfg(user_data_dir="/prof")["browser"]["userDataDir"] == "/prof"
    assert "userDataDir" not in _cfg()["browser"]
def test_headed_viewport():
    assert _cfg(window_size=(1472, 862))["browser"]["contextOptions"]["viewport"] == {"width": 1472, "height": 862}
def test_ua_in_context_and_headers():
    co = _cfg()["browser"]["contextOptions"]
    assert co["userAgent"] == UA and co["extraHTTPHeaders"]["user-agent"] == UA
def test_default_package_pinned():
    assert DEFAULT_MCP_PACKAGE == "@playwright/mcp@0.0.68"
def test_args_headless_and_headed():
    from mcp_launcher.mcp_config import build_mcp_args
    assert build_mcp_args("/c.json", None) == ["npx", DEFAULT_MCP_PACKAGE, "--config", "/c.json"]
    assert build_mcp_args("/c.json", (1472, 862), "@playwright/mcp@0.0.78")[-2:] == ["--viewport-size", "1472x862"]
def test_config_file_is_private(tmp_path):
    import json, os, stat
    from mcp_launcher.mcp_config import write_mcp_config
    p = tmp_path / "c.json"; p.write_text("{}"); os.chmod(p, 0o644)
    write_mcp_config(str(p), {"browser": {"launchOptions": {"env": {"SECRET": "x"}}}})
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600 and json.loads(p.read_text())["browser"]
def test_config_write_refuses_symlink(tmp_path):
    # $TMPDIR/camoufox-mcp-config.json holds the host env; never follow a
    # pre-planted symlink (shared /tmp on Linux) and truncate its target.
    import os, pytest
    from mcp_launcher.mcp_config import write_mcp_config
    target = tmp_path / "victim"; target.write_text("keep")
    link = tmp_path / "c.json"; os.symlink(target, link)
    with pytest.raises(OSError):
        write_mcp_config(str(link), {"x": 1})
    assert target.read_text() == "keep"
