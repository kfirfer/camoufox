import camoufox.utils as cu
from mcp_launcher.bundle import install_properties_shim
def test_redirects_macos_bundle_to_resources(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(cu, "_load_properties", lambda path=None: seen.setdefault("p", path))
    install_properties_shim()
    macos = tmp_path / "X.app/Contents/MacOS"; macos.mkdir(parents=True)
    res = tmp_path / "X.app/Contents/Resources"; res.mkdir(); (res / "properties.json").write_text("[]")
    cu._load_properties(path=macos / "camoufox")
    assert str(seen["p"]).endswith("Contents/Resources/properties.json")
def test_shim_is_idempotent():
    install_properties_shim(); first = cu._load_properties
    install_properties_shim(); assert cu._load_properties is first
