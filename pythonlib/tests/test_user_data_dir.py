"""fix-user-data: persistent profile plumbing (F1)."""
from camoufox.server import camel_case, to_camel_case_dict
from camoufox.utils import split_user_data_dir


def test_camel_case_keeps_private_underscore():
    assert camel_case("_user_data_dir") == "_userDataDir"
    assert to_camel_case_dict({"_user_data_dir": "/p"}) == {"_userDataDir": "/p"}


def test_split_removes_private_key_and_returns_dir():
    opts, udd = split_user_data_dir({"headless": True, "_user_data_dir": "/p"})
    assert udd == "/p" and "_user_data_dir" not in opts and opts["headless"] is True


def test_split_accepts_public_key_too():
    opts, udd = split_user_data_dir({"user_data_dir": "/q"})
    assert udd == "/q" and "user_data_dir" not in opts


def test_split_without_dir_is_noop_copy():
    src = {"headless": True}
    opts, udd = split_user_data_dir(src)
    assert udd is None and opts == src and opts is not src


def test_split_pops_both_keys_when_both_present():
    opts, udd = split_user_data_dir({"_user_data_dir": "/p", "user_data_dir": "/q"})
    assert udd == "/p" and "_user_data_dir" not in opts and "user_data_dir" not in opts


def test_launch_options_omits_key_when_unset(monkeypatch, tmp_path):
    import shutil, pathlib
    from camoufox import utils
    exe = tmp_path / "camoufox"; exe.touch()
    # Resolve from THIS file, not utils.__file__: upstream tests put the
    # un-normalised "tests/.." on sys.path, so utils.__file__ is
    # ".../tests/../camoufox/utils.py" and .parents[2] would be tests/.
    shutil.copy(pathlib.Path(__file__).resolve().parents[2] / "settings" / "properties.json", tmp_path)
    monkeypatch.setattr(utils, "confirm_paths", lambda *a, **k: None, raising=False)
    o = utils.launch_options(executable_path=str(exe), ff_version=152, os="macos",
                             i_know_what_im_doing=True, exclude_addons=list(utils.DefaultAddons))
    assert "_user_data_dir" not in o
    o2 = utils.launch_options(executable_path=str(exe), ff_version=152, os="macos",
                              i_know_what_im_doing=True, exclude_addons=list(utils.DefaultAddons),
                              user_data_dir=tmp_path / "prof")
    assert o2["_user_data_dir"] == str(tmp_path / "prof")


class _FakeFirefox:
    def __init__(self):
        self.calls = []

    def launch(self, **kw):
        self.calls.append(("launch", None, kw))
        return object()

    def launch_persistent_context(self, user_data_dir, **kw):
        self.calls.append(("persistent", user_data_dir, kw))
        return object()


class _FakePlaywright:
    def __init__(self):
        self.firefox = _FakeFirefox()


def test_new_browser_never_passes_private_key_to_launch(monkeypatch):
    # The exact TypeError from §1.3-1, without needing a browser: the pre-fix
    # launch_options() always emitted the key, as None when no dir was set.
    from camoufox import sync_api
    monkeypatch.setattr(sync_api, "attach_no_viewport_default", lambda b: None)
    pw = _FakePlaywright()
    sync_api.NewBrowser(pw, from_options={"headless": True, "_user_data_dir": None})
    kind, _, kw = pw.firefox.calls[0]
    assert kind == "launch" and "_user_data_dir" not in kw and "user_data_dir" not in kw


def test_new_browser_persistent_gets_dir_and_keeps_no_viewport(monkeypatch):
    # Guards upstream's #666 no_viewport default, which a naive rewrite drops.
    from camoufox import sync_api
    monkeypatch.setattr(sync_api, "spoofs_window_dimensions", lambda o: True)
    pw = _FakePlaywright()
    sync_api.NewBrowser(pw, from_options={"headless": True, "_user_data_dir": "/p"}, persistent_context=True)
    kind, udd, kw = pw.firefox.calls[0]
    assert (kind, udd) == ("persistent", "/p")
    assert "_user_data_dir" not in kw and kw["no_viewport"] is True


def test_new_browser_persistent_without_dir_raises():
    import pytest
    from camoufox import sync_api
    with pytest.raises(ValueError, match="user_data_dir"):
        sync_api.NewBrowser(_FakePlaywright(), from_options={"headless": True}, persistent_context=True)


def test_launch_server_forwards_user_data_dir(monkeypatch, tmp_path):
    import base64, json, subprocess
    from camoufox import server
    captured = {}
    monkeypatch.setattr(server, "launch_options", lambda **kw: {"headless": True, "_user_data_dir": kw.get("user_data_dir")})
    monkeypatch.setattr(server, "get_nodejs", lambda: str(tmp_path / "node"))
    class P:
        returncode = 0
        def __init__(self, *a, **k):
            import io; self.stdin = io.StringIO()
            self.stdin.close = lambda: captured.setdefault("payload", self.stdin.getvalue())
        def wait(self, timeout=None): captured.setdefault("payload", self.stdin.getvalue()); return 0
        def poll(self): return 0
    monkeypatch.setattr(subprocess, "Popen", P)
    try:
        server.launch_server(persistent_context=True, user_data_dir=str(tmp_path / "prof"))
    except RuntimeError:
        pass  # NoReturn contract: raises once the child exits
    sent = json.loads(base64.b64decode(captured["payload"].strip()))
    assert sent["_userDataDir"] == str(tmp_path / "prof")
    # launchServerShared mode: without it Playwright isolates contexts per
    # connection and clients never see the persistent default context.
    assert sent["_sharedBrowser"] is True


def _capture_launch_server(monkeypatch, tmp_path, options, **kwargs):
    """Run launch_server() with launch_options() stubbed; return the Node payload."""
    import base64, io, json, subprocess
    from camoufox import server
    captured = {}
    monkeypatch.setattr(server, "launch_options", lambda **kw: dict(options))
    monkeypatch.setattr(server, "get_nodejs", lambda: str(tmp_path / "node"))
    class P:
        returncode = 0
        def __init__(self, *a, **k): self.stdin = io.StringIO()
        def wait(self, timeout=None): captured.setdefault("payload", self.stdin.getvalue()); return 0
        def poll(self): return 0
    monkeypatch.setattr(subprocess, "Popen", P)
    try:
        server.launch_server(**kwargs)
    except RuntimeError:
        pass  # NoReturn contract: raises once the child exits
    return json.loads(base64.b64decode(captured["payload"].strip()))


_SPOOFED_WINDOW_ENV = {"CAMOU_CONFIG_1": '{"window.outerWidth": 1366, "window.outerHeight": 753}'}


def test_launch_server_persistent_spoofed_window_disables_default_viewport(monkeypatch, tmp_path):
    # Server-side counterpart of upstream #666: without it the persistent context
    # gets Playwright's 1280x720 viewport, contradicting the spoofed window size.
    sent = _capture_launch_server(
        monkeypatch, tmp_path,
        {"headless": True, "env": _SPOOFED_WINDOW_ENV, "_user_data_dir": "/p"},
        user_data_dir="/p",
    )
    assert sent["noDefaultViewport"] is True


def test_launch_server_persistent_keeps_explicit_viewport(monkeypatch, tmp_path):
    sent = _capture_launch_server(
        monkeypatch, tmp_path,
        {"headless": True, "env": _SPOOFED_WINDOW_ENV, "_user_data_dir": "/p",
         "viewport": {"width": 800, "height": 600}},
        user_data_dir="/p",
    )
    assert "noDefaultViewport" not in sent and sent["viewport"] == {"width": 800, "height": 600}


def test_launch_server_without_profile_leaves_viewport_alone(monkeypatch, tmp_path):
    sent = _capture_launch_server(monkeypatch, tmp_path, {"headless": True, "env": _SPOOFED_WINDOW_ENV})
    assert "noDefaultViewport" not in sent and "_sharedBrowser" not in sent


def test_launch_server_persistent_without_dir_raises(monkeypatch, tmp_path):
    import pytest
    with pytest.raises(ValueError, match="user_data_dir"):
        _capture_launch_server(monkeypatch, tmp_path, {"headless": True}, persistent_context=True)


def test_new_browser_dir_without_persistent_context_raises():
    # A profile dir without persistent_context=True would otherwise be dropped
    # silently and the session would run on a throwaway profile.
    import pytest
    from camoufox import sync_api
    with pytest.raises(ValueError, match="persistent_context"):
        sync_api.NewBrowser(_FakePlaywright(), from_options={"headless": True, "_user_data_dir": "/p"})


def test_async_new_browser_dir_without_persistent_context_raises():
    import asyncio, pytest
    from camoufox import async_api
    class _AsyncFirefox:
        async def launch(self, **kw): return object()
    class _AsyncPlaywright:
        firefox = _AsyncFirefox()
    with pytest.raises(ValueError, match="persistent_context"):
        asyncio.run(async_api.AsyncNewBrowser(_AsyncPlaywright(), from_options={"headless": True, "_user_data_dir": "/p"}))
