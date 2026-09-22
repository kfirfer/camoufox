from mcp_launcher.profile import scrub_stale_prefs
def test_scrubs_locale_prefs_when_not_pinning(tmp_path):
    p = tmp_path / "prefs.js"
    p.write_text('user_pref("intl.accept_languages", "en-SG");\nuser_pref("browser.x", 1);\n')
    assert scrub_stale_prefs(str(p), pinning_locale=False) == 1
    assert p.read_text() == 'user_pref("browser.x", 1);\n'
def test_keeps_locale_prefs_when_pinning(tmp_path):
    p = tmp_path / "prefs.js"; p.write_text('user_pref("intl.accept_languages", "en-SG");\n')
    assert scrub_stale_prefs(str(p), pinning_locale=True) == 0
def test_missing_file_is_noop(tmp_path):
    assert scrub_stale_prefs(str(tmp_path / "nope.js"), pinning_locale=False) == 0
