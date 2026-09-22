from mcp_launcher.version import detect_firefox_major, DEFAULT_FIREFOX_MAJOR

def _bundle(tmp_path, ini_version):
    macos = tmp_path / "Camoufox.app" / "Contents" / "MacOS"; macos.mkdir(parents=True)
    res = tmp_path / "Camoufox.app" / "Contents" / "Resources"; res.mkdir()
    (res / "application.ini").write_text(f"[App]\nName=Camoufox\nVersion={ini_version}\n")
    exe = macos / "camoufox"; exe.touch(); return exe

def test_reads_application_ini_in_macos_bundle(tmp_path):
    assert detect_firefox_major(str(_bundle(tmp_path, "152.0.4-beta.30"))) == 152

def test_reads_application_ini_next_to_linux_binary(tmp_path):
    (tmp_path / "application.ini").write_text("[App]\nVersion=153.0\n")
    exe = tmp_path / "camoufox-bin"; exe.touch()
    assert detect_firefox_major(str(exe)) == 153

def test_falls_back_to_path_regex(tmp_path):
    d = tmp_path / "camoufox-152.0.4-beta.30" / "dist"; d.mkdir(parents=True)
    exe = d / "camoufox"; exe.touch()
    assert detect_firefox_major(str(exe)) == 152

def test_ini_wins_over_misleading_path(tmp_path):
    exe = _bundle(tmp_path / "camoufox-146-old", "152.0.4-beta.30")
    assert detect_firefox_major(str(exe)) == 152

def test_default_when_unknown(tmp_path):
    assert detect_firefox_major(None) == DEFAULT_FIREFOX_MAJOR
    assert detect_firefox_major(str(tmp_path / "x")) == DEFAULT_FIREFOX_MAJOR
