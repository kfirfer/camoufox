# Upgrade Camoufox & Firefox to v152.0.4-beta.30 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.
>
> **Status legend:** `[ ]` not started · `[/]` in progress · `[X]` done. Update the markers as you go and commit the plan along with the work.

**Goal:** Move branch `fix-user-data` from Camoufox `146.0.1-beta.25` (Firefox 146.0.1) to the upstream Camoufox release **`v152.0.4-beta.30`** (Firefox **152.0.4**). Every fix and feature already on `fix-user-data` must be kept and must be shown to work after the move.

**Architecture:** `main` (`d6540b5`) is a **direct ancestor** of the upstream tag `v152.0.4-beta.30` (`5d06ec1`, 175 commits ahead). So the upgrade is a **merge of the upstream tag into `fix-user-data`**, not a re-port. Branch commits and history stay as they are, and nothing is force-pushed. A trial `git merge-tree` produced exactly **3 textual conflicts** and **1 semantic conflict**. Each has a decided resolution below. After the merge, we rebuild the browser natively on macOS arm64, harden the Python/server layer, and break the MCP launcher (`launch-camoufox-mcp.py`) into small tested modules that detect the Firefox version on their own.

**Tech Stack:** Firefox 152.0.4 source + Camoufox patch stack (C++/JS), Juggler (Playwright's Firefox protocol), Python `camoufox` pythonlib 0.5.6, Playwright (Python 1.58 locally; the MCP server bundles its own), `@playwright/mcp`, BrowserForge, `pytest`, GNU make + `./mach`.

**Spec:** This document (the user request) together with the existing branch docs `STEALTH_TEST_MATRIX.md` (acceptance tests) and `CAMOUFOX_FEEDBACK.md` (known detection issues).

## Global Constraints

- Target version: `version=152.0.4`, `release=beta.30` (upstream tag `v152.0.4-beta.30`, commit `5d06ec1629ac7843508f1e683f83e404fde8db76`).
- All work lands on branch **`fix-user-data`**. Never on `main`, never force-pushed.
- **Do not remove or change the behaviour of any branch feature** listed in §1.2. Any change to how one is *implemented* must be justified here and covered by a test.
- Upstream remote: `https://github.com/daijro/camoufox` (this repo's `origin` is the fork `kfirfer/camoufox`).
- Build host: macOS arm64 (Darwin 27, SDK 27.0). Firefox 152 needs macOS SDK **≥ 26.4** (`build/moz.configure/toolchain.configure: mac_sdk_min_version() == "26.4"`), so this host qualifies.
- Firefox 152 stock header defaults (checked against `FIREFOX_152_0_4_RELEASE:modules/libpref/init/all.js`):
  `network.http.accept-encoding = "gzip, deflate"`, `network.http.accept-encoding.secure = "gzip, deflate, br, zstd"`, `network.http.accept-encoding.dictionary = "dcb, dcz"`.
- Python constraint from upstream pythonlib 0.5.6: `playwright < 1.63`. From Playwright **1.61** onward the browser build must be **≥ beta.30** (`CONSTRAINTS.PLAYWRIGHT_BROWSER_FLOORS`).
- `@playwright/mcp` stays pinned (currently `0.0.68` → Playwright `1.59.0-alpha`). Any bump must stay on Playwright `< 1.63`, i.e. `@playwright/mcp ≤ 0.0.78`.
- Keep the upstream Makefile diff clean. Put build-environment tweaks in scripts, not the Makefile.

---

## 1. Current-State Analysis (review of the branch & server-connection code)

### 1.1 Components that talk to the browser ("server connection" layer)

| Component | File | Role | Upgrade impact |
|---|---|---|---|
| MCP launcher | `launch-camoufox-mcp.py` | Builds the fingerprint + prefs, writes `camoufox-mcp-config.json`, then `exec`s `npx @playwright/mcp@0.0.68 --config …`. The MCP server launches Camoufox over Juggler (pipe), with an optional persistent `userDataDir`. | **High.** It hard-codes `ff_version = 146`, finds the version with a path regex, patches `_load_properties` for macOS bundles, and its comments and UA strings assume 146. |
| Python WS server | `pythonlib/camoufox/server.py` | `launch_server()` → Node `launchServer.js` → Playwright `firefox.launchServer()` → prints the WS endpoint. | **High.** Upstream rewrote it (graceful shutdown, loads the driver via `index.js`) **and added code that rejects `user_data_dir`**, which clashes with the branch feature. |
| Node bridge | `pythonlib/camoufox/launchServer.js` | Reads base64 options from stdin and calls the Playwright launcher. | **High.** Upstream replaced `require(lib/browserServerImpl.js)` (removed in Playwright 1.60) with `require(<driver>/index.js)`. Take upstream's version. |
| Launch options | `pythonlib/camoufox/utils.py` | `launch_options()` builds env (`CAMOU_CONFIG_*`), prefs and the executable path. The branch added `persistent_context` / `user_data_dir` → `_user_data_dir`. | **High.** Auto-merges, but carries a pre-existing bug (§1.3). |
| Juggler page handler | `additions/juggler/protocol/PageHandler.js` | Mouse dispatch. The branch added the humanized trajectory. | **Conflict.** Upstream (beta.28, PRs #679/#680) re-implemented the same feature with a hang fix. |
| C++ patches | `patches/network-patches.patch`, `patches/timezone-spoofing.patch`, `patches/voice-spoofing.patch` | Accept-Encoding override, worker timezone, voice registry. | **Conflict** in two of them (§2). |

### 1.2 Branch features that MUST survive (acceptance inventory)

| # | Feature / fix (commit) | Where | How it is verified after the upgrade |
|---|---|---|---|
| F1 | Persistent profile (`user_data_dir`) through `launch_options()` + `launch_server()` (`_userDataDir`) (`9527ed5`, `server.py` camel-case `_` prefix) | `pythonlib/camoufox/utils.py`, `server.py` | Task 2.1, 2.2 tests + Task 5.3 |
| F2 | Humanized mouse trajectory (`38e5819`) | `PageHandler.js` | `test_humanize.py` (Task 5.4) |
| F3 | HTTPS-only `headers.Accept-Encoding` override (`ecf8c8f`) | `patches/network-patches.patch` | Task 3.4 grep + STEALTH_TEST_MATRIX network checks |
| F4 | ServiceWorker timezone fallback `ucid → 0 → MaskConfig` (`ed3ace2`) | `patches/timezone-spoofing.patch` | Task 3.4 grep + STEALTH_TEST_MATRIX SW timezone check |
| F5 | `voice-spoofing.patch` hunk-header fix | `patches/voice-spoofing.patch` | Upstream contains the identical fix, so it auto-merges |
| F6 | "Firefox" branding (no "Camoufox" in user-visible strings) (`bcbf374`) | `additions/browser/**` | Upstream did not touch these files (auto-kept). Grep in Task 5.5 |
| F7 | MCP launcher: persistent profile, stale-pref scrubbing, clean UA derived from version, unpinned locale/TZ/hwConcurrency with CLI overrides, WebRTC block, headed-mode geometry, `vision` capability (`5527ea3` … `71b22de`) | `launch-camoufox-mcp.py` | Task 4.x unit tests + Task 5.2 |
| F8 | macOS bundle `properties.json` lookup (`3ca9802`) | launcher shim | Task 4.2 test |
| F9 | Docs: `STEALTH_TEST_MATRIX.md`, `CAMOUFOX_FEEDBACK.md`, `MISC.md`, `test_humanize.py`, root `pyproject.toml`/`uv.lock` | repo root | Task 6.1 |

### 1.3 Defects found during review (fix as part of the upgrade)

1. **`launch_options()` always returns the key `_user_data_dir`** (even as `None`). Any caller that does `playwright.firefox.launch(**launch_options(...))`, including `camoufox.Camoufox()`, `NewBrowser()` and `AsyncNewBrowser()`, crashes. Reproduced on the current branch:
   ```
   TypeError: BrowserType.launch() got an unexpected keyword argument '_user_data_dir'
   ```
   `Camoufox(persistent_context=True, user_data_dir=…)` is broken too, because `user_data_dir` is now captured and never reaches `launch_persistent_context()`. The MCP launcher is unaffected: it reads only `env` and `firefox_user_prefs`.
2. **Upstream `launch_server()` rejects `persistent_context` / `user_data_dir`** with the stated reason that "Playwright cannot serve a persistent context". That is wrong for every Playwright version we target. `BrowserServerLauncherImpl.launchServer()` still takes `options._userDataDir` and calls `launchPersistentContext()`, which was confirmed in the playwright-core **1.58.0** (`lib/browserServerImpl.js:69-72`), **1.61.0** and **1.62.0** (`lib/coreBundle.js`) sources. The public `firefox.launchServer(options)` passes `options` through unchanged (`this._serverLauncher.launchServer(options)`). We keep the branch feature and drop the rejection.
3. **Launcher version detection is fragile.** `ff_version = 146` is hard-coded, with a regex on the path (`camoufox-(\d+)`) as the only override. A binary installed through `camoufox fetch`, or any other path layout, silently gets a **Firefox/146 UA on a 152 engine**, a strong UA-vs-engine tell.
4. `launch-camoufox-mcp.py` is one 630-line `main()`, with no tests, a module-level monkey-patch, and a silent `except Exception → config = {}` fallback that drops the whole fingerprint.

### 1.4 What upstream 146 → 152.0.4-beta.30 brings (relevant highlights)

- Firefox 152 rebase, with the Juggler adapted to FF152 API drift (screenshots, popups, touch, popup close), per PR #666/#673.
- Humanize trajectory restored plus an exact-edge bounds guard (#679, #680). This **supersedes F2's implementation** and keeps its behaviour.
- `debugger-invisible-to-content.patch`, which is relevant to `CAMOUFOX_FEEDBACK.md` P1.1 (Developer Tools = Yes). `trusted-automation-events.patch`, `font-system-fonts-css2.patch`, `system-ui-font-spoofing.patch`, and a WebRTC leak rework (`camoufox.cfg`).
- Accept-Encoding fix #543 (**conflicts with F3**, see §2), a worker `DateTimeInfo` cache fix (`a39cf9f`), and a `TimezoneManager::GetTimezone` fallback to MaskConfig.
- Voice leak fix #731 (a superset of F5). Playwright 1.61 support with the Playwright-keyed browser floor (#743, #746). Rust host triplet under rustc 1.98 (#747).
- pythonlib 0.5.6: new `server.py`/`launchServer.js`, `display.py`, `fingerprint-presets-v150.json`, a `pythonlib/tests/` suite, and `playwright<1.63`.
- **Not in beta.30** (these land in beta.31, a candidate follow-up, see §9): #751/#752 mouse boundary-row dispatch fix, #749 sealing of fingerprint setters, and `media:spoof_codecs`.

---

## 2. Conflict Resolution Decisions (from `git merge-tree fix-user-data v152.0.4-beta.30`)

| File | Type | Decision | Rationale |
|---|---|---|---|
| `additions/juggler/protocol/PageHandler.js` | textual | **Take upstream (`--theirs`)** | Upstream implements the same humanize trajectory (`camouGetBool('humanize')` + `camouGetMouseTrajectory` + `_lastTrackedPos`). It adds the `>=` edge guard, which fixes a permanent input hang when a point lands exactly on `width`/`height`, and always finishes on the exact destination. F2's behaviour is preserved. |
| `patches/network-patches.patch` | textual | **Keep ours (HTTPS-only)**, re-based onto upstream's hunk offsets | Upstream #543 rewrites `aAcceptEncodings` for **all three** lists. Firefox 152 now sends `dcb, dcz` on the *dictionary* list, and plain HTTP stays `gzip, deflate`. With upstream's version, `headers.Accept-Encoding = "gzip, deflate, br, zstd"` would overwrite the dictionary list (breaking Compression Dictionary Transport) and advertise br/zstd over plain HTTP, and neither matches stock Firefox. Ours only touches `mHttpsAcceptEncodings`, which is exactly F3. |
| `patches/timezone-spoofing.patch` | textual (hunk moved: `@@ -6350` → `@@ -6429`) | **Take upstream, then re-add only the `ucid → 0` fallback** in the relocated `WorkerPrivate.cpp` hunk | Upstream's `TimezoneManager::GetTimezone()` now falls back to `MaskConfig::GetString("timezone")` by itself, so our explicit MaskConfig branch (and the `MaskConfig.hpp` include in `WorkerPrivate.cpp`) is redundant. The one piece still missing upstream is the fallback to the default container (`ucid 0`) when a non-default container has no entry. |
| `pythonlib/camoufox/server.py` | **semantic** (auto-merged) | **Delete upstream's `persistent_context`/`user_data_dir` rejection loop.** Pop `persistent_context`, keep `user_data_dir`. | See §1.3-2. Keeps F1. |
| `pythonlib/camoufox/utils.py` | auto-merged + latent bug | Emit `_user_data_dir` **only when set**. Translate it in `NewBrowser`/`AsyncNewBrowser`. | See §1.3-1. |
| `patches/voice-spoofing.patch` | auto-merged | Take merged result | Upstream carries the same `@@ -63,2 +63,5 @@` fix plus #731. |
| `launchServer.js`, branding, docs | auto-merged | Take merged result | Branding files are untouched upstream, so ours are kept. |

---

## 3. File Structure (created / modified)

```
upstream.sh                                   M  version=152.0.4 release=beta.30 (from merge)
additions/juggler/protocol/PageHandler.js     M  = upstream beta.30
patches/network-patches.patch                 M  ours (HTTPS-only), re-based hunk
patches/timezone-spoofing.patch               M  upstream + ucid→0 fallback
pythonlib/camoufox/server.py                  M  allow user_data_dir → _userDataDir
pythonlib/camoufox/utils.py                   M  conditional _user_data_dir + split_user_data_dir()
pythonlib/camoufox/sync_api.py                M  use split_user_data_dir()
pythonlib/camoufox/async_api.py               M  use split_user_data_dir()
pythonlib/tests/test_user_data_dir.py         C  F1 regression tests
mcp_launcher/__init__.py                      C  package marker
mcp_launcher/version.py                       C  detect_firefox_major()
mcp_launcher/user_agent.py                    C  clean_user_agent(), needs_ua_refresh()
mcp_launcher/profile.py                       C  scrub_stale_prefs()
mcp_launcher/bundle.py                        C  macOS properties.json shim (install_properties_shim)
mcp_launcher/mcp_config.py                    C  build_mcp_config()
mcp_launcher/tests/test_*.py                  C  unit tests for each module
launch-camoufox-mcp.py                        M  thin CLI wiring the modules; same flags + --mcp-package
test_humanize.py                              M  binary path via env/arg, not hard-coded 146
STEALTH_TEST_MATRIX.md, MISC.md               M  146 → 152 paths/expectations
UPGRADE_…_PLAN.md                             C  this document
```

Why a separate `mcp_launcher/` package rather than editing `pythonlib/`: the launcher is **branch-specific tooling**, so keeping it out of the upstream-tracked `pythonlib/` keeps future upstream merges (beta.31+) conflict-free. Each module has one job and can be tested without a browser.

---

## 4. Dependency Graph

```
Phase 0 (prep) ──► Phase 1 (merge + conflicts) ──┬─► Phase 2 (pythonlib fixes) ──┐
                                                 ├─► Phase 3 (patch regen + build) ├─► Phase 5 (verification) ──► Phase 6 (docs, push)
                                                 └─► Phase 4 (MCP launcher) ───────┘
```

Phases 2, 3 and 4 are independent after Phase 1. Phase 3 (the build, about 40 min cold) can run while 2 and 4 are in progress. Phase 5 needs all three.

---

## Phase 0 — Preparation & Safety Net

**Deliverable:** a recoverable starting point, a recorded baseline, and the upstream tag available locally.

### [ ] Task 0.1: Snapshot and baseline

**Files:** none modified.

- [ ] **Step 1: Confirm a clean tree on the right branch**
  ```bash
  cd /Users/dev345/code/kfirfer/camoufox
  git switch fix-user-data && git status --short   # expect: empty
  git pull --ff-only origin fix-user-data
  ```
- [ ] **Step 2: Tag the pre-upgrade state (rollback point)**
  ```bash
  git tag -a pre-ff152-upgrade -m "fix-user-data before v152.0.4-beta.30 merge"
  git push origin pre-ff152-upgrade
  ```
- [ ] **Step 3: Record the baseline behaviour of the current 146 build** (used for the before/after comparison in Phase 5)
  ```bash
  B146=camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/MacOS/camoufox
  ls -la "$B146"
  python3 launch-camoufox-mcp.py --help > /tmp/launcher-help-146.txt   # CLI surface to preserve
  ```
  Also run the STEALTH_TEST_MATRIX "Quick smoke" section against 146 and save the results as `docs/upgrade/baseline-146.md` (not committed if it contains personal IP data).
- [ ] **Step 4: Free disk space.** A Firefox 152 source tree plus an obj dir needs about 40 GB. Keep the 146 tree until Phase 5 passes.

### [ ] Task 0.2: Bring in the upstream tag

- [ ] **Step 1: Add upstream remote and fetch the exact tag**
  ```bash
  git remote add upstream https://github.com/daijro/camoufox.git 2>/dev/null || true
  git fetch upstream tag v152.0.4-beta.30 --no-tags
  git rev-parse v152.0.4-beta.30^{commit}   # expect 5d06ec1629ac7843508f1e683f83e404fde8db76
  git merge-base --is-ancestor main v152.0.4-beta.30 && echo "main is ancestor — merge is safe"
  ```
- [ ] **Step 2: Dry-run the merge and confirm the conflict set matches §2**
  ```bash
  git merge-tree --write-tree --name-only fix-user-data v152.0.4-beta.30
  ```
  Expected: exactly `additions/juggler/protocol/PageHandler.js`, `patches/network-patches.patch`, `patches/timezone-spoofing.patch`. If the list differs, stop and update §2 before continuing.

---

## Phase 1 — Merge Upstream v152.0.4-beta.30 into `fix-user-data`

**Deliverable:** a single merge commit on `fix-user-data` with the conflicts resolved per §2. `upstream.sh` reads `152.0.4` / `beta.30`.

### [ ] Task 1.1: Perform the merge and resolve conflicts

**Files:**
- Modify: `additions/juggler/protocol/PageHandler.js`, `patches/network-patches.patch`, `patches/timezone-spoofing.patch`, `pythonlib/camoufox/server.py`

- [ ] **Step 1: Start the merge**
  ```bash
  git merge --no-ff --no-commit v152.0.4-beta.30
  ```
- [ ] **Step 2: PageHandler.js: take upstream**
  ```bash
  git checkout --theirs additions/juggler/protocol/PageHandler.js
  grep -n "camouGetMouseTrajectory\|_lastTrackedPos\|>= boundingBox.width" additions/juggler/protocol/PageHandler.js
  ```
  Expected: all three patterns are present (trajectory call, tracked position, `>=` guard).
- [ ] **Step 3: network-patches.patch: keep ours, on upstream's context.** Resolve the conflict so that the `SetAcceptEncodings` hunk reads exactly:
  ```diff
  @@ -2101,6 +2115,10 @@ nsresult nsHttpHandler::SetAcceptEncodings(const char* aAcceptEncodings,
     if (isDictionary) {
       mDictionaryAcceptEncodings = aAcceptEncodings;
     } else if (isSecure) {
  +    if (auto value = MaskConfig::GetString("headers.Accept-Encoding")) {
  +      mHttpsAcceptEncodings.Assign(nsCString(value.value().c_str()));
  +      return NS_OK;
  +    }
       mHttpsAcceptEncodings = aAcceptEncodings;
     } else {
  ```
  Remove the `nsCString encodingOverride;` / `aAcceptEncodings = encodingOverride.get();` lines from upstream #543. The hunk line numbers get corrected in Task 3.2, when the patch is regenerated from a real tree.
- [ ] **Step 4: timezone-spoofing.patch: take upstream, re-add the ucid→0 fallback**
  ```bash
  git checkout --theirs patches/timezone-spoofing.patch
  ```
  Then change the `dom/workers/WorkerPrivate.cpp` block (upstream hunk `@@ -6429,6 +6431,19 @@`) to:
  ```cpp
  +  // Camoufox: Apply per-context timezone override to this worker's JS realm.
  +  // TimezoneManager::GetTimezone() already falls back to MaskConfig
  +  // "timezone". fix-user-data: if this container has no entry of its own
  +  // (e.g. a ServiceWorker in a non-default userContextId while the spoof
  +  // was set via window.setTimezone() on the default container), fall back
  +  // to the default container (ucid 0) so SWs never leak the host timezone.
  +  {
  +    uint32_t ucid = GetOriginAttributes().mUserContextId;
  +    nsAutoString tz;
  +    bool found = mozilla::dom::TimezoneManager::GetTimezone(ucid, tz) &&
  +                 !tz.IsEmpty();
  +    if (!found && ucid != 0) {
  +      found = mozilla::dom::TimezoneManager::GetTimezone(0, tz) &&
  +              !tz.IsEmpty();
  +    }
  +    if (found) {
  +      NS_LossyConvertUTF16toASCII tzASCII(tz);
  +      JS::SetRealmTimeZoneOverride(aCx, tzASCII.get());
  +    }
  +  }
  ```
  Do **not** re-add `#include "MaskConfig.hpp"` to `WorkerPrivate.cpp`, because it is no longer used there. The hunk header counts get fixed in Task 3.2.
- [ ] **Step 5: server.py: drop the persistent rejection** (auto-merged file, semantic fix). Replace upstream's loop:
  ```python
      for unsupported in ('persistent_context', 'user_data_dir'):
          if kwargs.get(unsupported):
              raise ValueError(...)
          kwargs.pop(unsupported, None)
  ```
  with:
  ```python
      # fix-user-data: a persistent profile IS servable. Playwright's
      # BrowserServerLauncherImpl.launchServer() honours the private
      # `_userDataDir` option and calls launchPersistentContext() (verified in
      # playwright-core 1.58, 1.61, 1.62). launch_options() turns
      # `user_data_dir` into `_user_data_dir`, and camel_case() keeps the
      # leading underscore (-> `_userDataDir`).
      kwargs.pop('persistent_context', None)
  ```
  Also update the docstring note so it no longer says persistent contexts are not servable.
- [ ] **Step 6: Check that no conflict markers remain, then commit the merge**
  ```bash
  git diff --check && ! git grep -n '^<<<<<<<\|^>>>>>>>' -- additions patches pythonlib
  cat upstream.sh    # version=152.0.4 / release=beta.30
  git add -A && git commit -m "Merge upstream Camoufox v152.0.4-beta.30 (Firefox 152.0.4) into fix-user-data

  Conflicts resolved:
  - PageHandler.js: take upstream humanize trajectory (superset + edge-guard hang fix)
  - network-patches.patch: keep HTTPS-only Accept-Encoding override (FF152 dictionary list dcb,dcz must stay stock)
  - timezone-spoofing.patch: take upstream (GetTimezone MaskConfig fallback) + keep ucid->0 SW fallback
  - server.py: keep persistent launch_server support (_userDataDir) — drop upstream rejection"
  ```

---

## Phase 2 — Python Library: Persistent-Profile Correctness (F1)

**Deliverable:** `launch_options()`, `Camoufox()`, `AsyncCamoufox()` and `launch_server()` all work both with and without `user_data_dir`, with regression tests.

### [ ] Task 2.1: `_user_data_dir` only when set + a single translation helper

**Files:**
- Modify: `pythonlib/camoufox/utils.py` (the `launch_options` result dict, about lines 975-990 after the merge)
- Modify: `pythonlib/camoufox/sync_api.py` (`NewBrowser`), `pythonlib/camoufox/async_api.py` (`AsyncNewBrowser`)
- Test: `pythonlib/tests/test_user_data_dir.py`

**Interfaces:**
- Produces: `camoufox.utils.split_user_data_dir(options: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]`, which returns a copy of `options` without `_user_data_dir`/`user_data_dir`, plus the directory if either was present.

- [ ] **Step 1: Write the failing tests**
  ```python
  # pythonlib/tests/test_user_data_dir.py
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


  def test_launch_options_omits_key_when_unset(monkeypatch, tmp_path):
      import shutil, pathlib
      from camoufox import utils
      exe = tmp_path / "camoufox"; exe.touch()
      shutil.copy(pathlib.Path(utils.__file__).parents[2] / "settings" / "properties.json", tmp_path)
      monkeypatch.setattr(utils, "confirm_paths", lambda *a, **k: None, raising=False)
      o = utils.launch_options(executable_path=str(exe), ff_version=152, os="macos",
                               i_know_what_im_doing=True, exclude_addons=list(utils.DefaultAddons))
      assert "_user_data_dir" not in o
      o2 = utils.launch_options(executable_path=str(exe), ff_version=152, os="macos",
                                i_know_what_im_doing=True, exclude_addons=list(utils.DefaultAddons),
                                user_data_dir=tmp_path / "prof")
      assert o2["_user_data_dir"] == str(tmp_path / "prof")
  ```
- [ ] **Step 2: Run and confirm they fail**
  ```bash
  cd pythonlib && ../.venv/bin/python -m pytest tests/test_user_data_dir.py -v
  ```
  Expected: `ImportError: cannot import name 'split_user_data_dir'`.
- [ ] **Step 3: Implement it in `utils.py`.** Replace the unconditional `"_user_data_dir": …` entry in `result` with:
  ```python
      # fix-user-data: only surface the profile dir when one was requested, so
      # plain `firefox.launch(**opts)` never receives an unknown kwarg.
      if user_data_dir:
          result["_user_data_dir"] = str(user_data_dir)
  ```
  and add near `_clean_locals`:
  ```python
  def split_user_data_dir(options: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
      """
      Returns (options without any user-data-dir key, the dir or None).
      launch_options() emits `_user_data_dir` (served as `_userDataDir` by
      launch_server); in-process Playwright wants `user_data_dir` instead.
      """
      opts = dict(options)
      udd = opts.pop("_user_data_dir", None) or opts.pop("user_data_dir", None)
      return opts, (str(udd) if udd else None)
  ```
- [ ] **Step 4: Use it in both APIs.** In `sync_api.NewBrowser` (and the same change with `await` in `async_api.AsyncNewBrowser`):
  ```python
      opts, user_data_dir = split_user_data_dir(from_options)

      # Persistent context
      if persistent_context:
          if not user_data_dir:
              raise ValueError("persistent_context=True requires user_data_dir")
          context = playwright.firefox.launch_persistent_context(user_data_dir, **opts)
          return sync_attach_vd(context, virtual_display)

      # Browser
      browser = playwright.firefox.launch(**opts)
  ```
  (`from .utils import split_user_data_dir` goes in both modules.)
- [ ] **Step 5: Run the new tests and the whole upstream suite**
  ```bash
  cd pythonlib && ../.venv/bin/python -m pytest tests -q
  ```
  Expected: all pass. If an upstream test asserts the old `launch_server` rejection, update that test to assert the `_userDataDir` pass-through instead, and note it in the commit message.
- [ ] **Step 6: Commit**
  ```bash
  git add pythonlib && git commit -m "pythonlib: emit _user_data_dir only when set; translate for in-process persistent contexts (fixes TypeError in Camoufox())"
  ```

### [ ] Task 2.2: `launch_server()` persistent round-trip test

**Files:** Test: `pythonlib/tests/test_user_data_dir.py` (append)

- [ ] **Step 1: Test that the Node payload carries `_userDataDir`**
  ```python
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
  ```
- [ ] **Step 2: Run it (`pytest tests/test_user_data_dir.py -v`). Expected: PASS.** A `ValueError` means the upstream rejection was not removed (Task 1.1 Step 5).
- [ ] **Step 3: Commit** `git commit -am "pythonlib: test launch_server persistent profile forwarding"`

---

## Phase 3 — Browser Source: Patch Regeneration & Native macOS Build

**Deliverable:** `camoufox-152.0.4-beta.30/obj-aarch64-apple-darwin/dist/Camoufox.app` built from the merged branch, with every patch applying cleanly and F3/F4 present in the compiled sources.

### [ ] Task 3.1: Fetch and prepare the Firefox 152.0.4 tree

- [ ] **Step 1: Toolchain check**
  ```bash
  xcrun --show-sdk-version           # must be >= 26.4 (host: 27.0)
  python3 -c 'import tomllib'        # mach needs Python >= 3.11
  ```
- [ ] **Step 2: Fetch + extract + copy additions**
  ```bash
  make fetch          # archive.mozilla.org/pub/firefox/releases/152.0.4/source/ (verified HTTP 200)
  make setup          # creates camoufox-152.0.4-beta.30/ as a git repo tagged `unpatched`
  ```
- [ ] **Step 3: Apply the full patch stack**
  ```bash
  make dir 2>&1 | tee /tmp/ff152-patch.log
  grep -iE "FAILED|rej|error" /tmp/ff152-patch.log   # expect: nothing
  ```
  If a patch fails, it will be one of the three branch-modified ones (the others are verbatim upstream beta.30 and apply by definition). Continue with Task 3.2.
- [ ] **Step 4: One-time mach bootstrap** (only if `~/.mozbuild` is stale): `make mozbootstrap`

### [ ] Task 3.2: Regenerate the two hand-merged patches from a real tree

Hand-edited hunk headers from Task 1.1 are approximate. Regenerate them through the repo's workspace flow so the offsets are exact.

**Files:** Modify: `patches/network-patches.patch`, `patches/timezone-spoofing.patch`

- [ ] **Step 1: network-patches**
  ```bash
  make workspace ./patches/network-patches.patch     # tree = state with this patch applied
  # verify the HTTPS-only override is in the source
  grep -n -A6 "else if (isSecure)" camoufox-152.0.4-beta.30/netwerk/protocol/http/nsHttpHandler.cpp
  ```
  If the hunk was applied with offsets or fuzz, fix the source by hand so that it matches Task 1.1 Step 3, then run `make edits` → "Write workspace to patch" → `network-patches.patch`.
- [ ] **Step 2: timezone-spoofing**: the same flow. Then:
  ```bash
  grep -n -B2 -A18 "Apply per-context timezone override" camoufox-152.0.4-beta.30/dom/workers/WorkerPrivate.cpp
  ```
  Expected: the `ucid != 0` fallback block from Task 1.1 Step 4.
- [ ] **Step 3: Round-trip check.** Every patch applies to a pristine tree:
  ```bash
  make revert && make dir 2>&1 | grep -iE "FAILED|rej" ; echo "exit=$?"   # expect no matches
  ```
- [ ] **Step 4: Commit** `git commit -am "patches: regenerate network/timezone patches against Firefox 152.0.4"`

### [ ] Task 3.3: Build and package

- [ ] **Step 1: Build**: `make build 2>&1 | tee /tmp/ff152-build.log`. That takes about 40 min cold and about 5 min incremental with ccache. Expected tail: `Your build was successful!`
- [ ] **Step 2: Smoke-run the binary**
  ```bash
  B152=camoufox-152.0.4-beta.30/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/MacOS/camoufox
  "$B152" --version        # expect: "... 152.0.4-beta.30" (the branding shows "Firefox")
  ```
- [ ] **Step 3 (optional, for distribution): package**: `make package-macos arch=arm64`.

### [ ] Task 3.4: Static verification that the branch C++ fixes are compiled in

- [ ] **Step 1**
  ```bash
  SRC=camoufox-152.0.4-beta.30
  grep -A4 "else if (isSecure)" $SRC/netwerk/protocol/http/nsHttpHandler.cpp | grep -q MaskConfig && echo F3-OK
  grep -q "ucid != 0" $SRC/dom/workers/WorkerPrivate.cpp && echo F4-OK
  grep -q 'MaskConfig::GetString("timezone")' $SRC/dom/base/TimezoneManager.cpp && echo F4-upstream-OK
  grep -q "MVoices().has_value()" $SRC/dom/media/webspeech/synth/nsSynthVoiceRegistry.cpp && echo F5-OK
  grep -q "camouGetMouseTrajectory" $SRC/juggler/protocol/PageHandler.js && echo F2-OK
  ```
  Expected: all five `*-OK` lines.

---

## Phase 4 — MCP Launcher: Modular, Version-Aware, Tested (F7, F8)

**Deliverable:** `launch-camoufox-mcp.py` becomes a thin CLI over `mcp_launcher/`. CLI flags and generated config are byte-for-byte equivalent (except for the version-derived UA). The Firefox major version is detected from the binary. Unit tests cover every module.

Run all tests with the repo venv: `.venv/bin/python -m pytest mcp_launcher/tests -q`. Keep the code **Python 3.12-compatible**: the MCP entry in `MISC.md` runs under `claude-1/.venv` (3.12), even though the root `pyproject.toml` says `>=3.13`.

### [ ] Task 4.1: `version.py`: detect the Firefox major from the binary

**Files:** Create `mcp_launcher/__init__.py` (empty), `mcp_launcher/version.py`, `mcp_launcher/tests/__init__.py` (empty), `mcp_launcher/tests/test_version.py`

**Interfaces:**
- Produces: `detect_firefox_major(executable_path: str | None, default: int = DEFAULT_FIREFOX_MAJOR) -> int` and the constant `DEFAULT_FIREFOX_MAJOR = 152`.

- [ ] **Step 1: Failing tests**
  ```python
  # mcp_launcher/tests/test_version.py
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
  ```
- [ ] **Step 2: Run the tests. Expected: `ModuleNotFoundError`.**
- [ ] **Step 3: Implement**
  ```python
  # mcp_launcher/version.py
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
  ```
- [ ] **Step 4: Run the tests. Expected: 5 passed.**
- [ ] **Step 5: Verify against the real build**: `.venv/bin/python -c "from mcp_launcher.version import detect_firefox_major as d; print(d('$B152'))"`. Expected `152`. If `application.ini` in the bundle has a different key layout, adjust `_INI_VERSION` and add a test case built from the real file.
- [ ] **Step 6: Commit** `git add mcp_launcher && git commit -m "mcp_launcher: detect Firefox major from application.ini"`

### [ ] Task 4.2: `user_agent.py`, `profile.py`, `bundle.py`: extract the existing logic unchanged

**Files:** Create `mcp_launcher/user_agent.py`, `mcp_launcher/profile.py`, `mcp_launcher/bundle.py` and tests `mcp_launcher/tests/test_user_agent.py`, `test_profile.py`, `test_bundle.py`.

**Interfaces:**
- `clean_user_agent(ff_major: int) -> str`. `CLEAN_APP_VERSION = "5.0 (Macintosh)"`.
- `needs_ua_refresh(saved_ua: str, ff_major: int) -> bool`. True if the UA contains "Camoufox", has no `rv:`, or `rv` ≠ `ff_major`.
- `scrub_stale_prefs(prefs_path: str, pinning_locale: bool) -> int`. Returns the number of removed lines, using the same pref list as today: `intl.accept_languages`, `intl.locale.requested`, `general.useragent.locale`.
- `install_properties_shim() -> None`. Idempotent. Wraps `camoufox.utils._load_properties` with the `Contents/MacOS → Contents/Resources/properties.json` redirect.

- [ ] **Step 1: Failing tests**
  ```python
  # mcp_launcher/tests/test_user_agent.py
  from mcp_launcher.user_agent import clean_user_agent, needs_ua_refresh
  def test_clean_ua_152():
      assert clean_user_agent(152) == ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:152.0) "
                                       "Gecko/20100101 Firefox/152.0")
  def test_refresh_on_stale_major():   assert needs_ua_refresh(clean_user_agent(146), 152)
  def test_refresh_on_brand_leak():    assert needs_ua_refresh("Mozilla/5.0 rv:152.0 Camoufox/152.0.4", 152)
  def test_refresh_on_missing():       assert needs_ua_refresh("", 152)
  def test_keep_matching():            assert not needs_ua_refresh(clean_user_agent(152), 152)
  ```
  ```python
  # mcp_launcher/tests/test_profile.py
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
  ```
  ```python
  # mcp_launcher/tests/test_bundle.py
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
  ```
- [ ] **Step 2: Run them. Expected: import errors.**
- [ ] **Step 3: Implement** by moving the code **verbatim** from `launch-camoufox-mcp.py` (`_CLEAN_UA` block, prefs.js strip block, `_load_properties_macos_bundle`) into these functions. The shim marks itself with `_load_properties_macos_bundle._camoufox_mcp_shim = True` and returns early if `cu._load_properties` already carries that marker.
  ```python
  # mcp_launcher/user_agent.py
  import re
  CLEAN_APP_VERSION = "5.0 (Macintosh)"
  _RV = re.compile(r"rv:(\d+)\.")
  def clean_user_agent(ff_major: int) -> str:
      # Firefox UA reduction freezes macOS at "Intel Mac OS X 10.15" (also on Apple Silicon).
      return (f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:{ff_major}.0) "
              f"Gecko/20100101 Firefox/{ff_major}.0")
  def needs_ua_refresh(saved_ua: str, ff_major: int) -> bool:
      m = _RV.search(saved_ua or "")
      return "Camoufox" in (saved_ua or "") or m is None or int(m.group(1)) != ff_major
  ```
- [ ] **Step 4: Run the tests. Expected: all pass.**
- [ ] **Step 5: Commit** `git commit -m "mcp_launcher: extract UA, prefs scrubber and macOS bundle shim"`

### [ ] Task 4.3: `mcp_config.py` + thin CLI + `--mcp-package`

**Files:** Create `mcp_launcher/mcp_config.py`, `mcp_launcher/tests/test_mcp_config.py`. Modify `launch-camoufox-mcp.py`.

**Interfaces:**
- `build_mcp_config(*, executable_path: str, headless: bool, firefox_user_prefs: dict, env: dict, user_agent: str, user_data_dir: str | None, window_size: tuple[int, int] | None) -> dict`. Returns exactly today's structure: `browser.browserName="firefox"`, `launchOptions{executablePath, headless, firefoxUserPrefs, env}`, optional `userDataDir`, `contextOptions{colorScheme:"dark", userAgent, extraHTTPHeaders{"user-agent"}, viewport?}`, `capabilities=["core","pdf","vision"]`.
- `DEFAULT_MCP_PACKAGE = "@playwright/mcp@0.0.68"`.

- [ ] **Step 1: Failing tests**
  ```python
  # mcp_launcher/tests/test_mcp_config.py
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
  ```
- [ ] **Step 2: Run. Expected: import error.**
- [ ] **Step 3: Implement `build_mcp_config`** by moving the dict-building code verbatim from the current `main()`.
- [ ] **Step 4: Rewrite `launch-camoufox-mcp.py` as wiring only.** Keep **every existing flag with the same name, default and help**, and add:
  ```python
  parser.add_argument("--mcp-package", default=DEFAULT_MCP_PACKAGE,
      help="npm spec for the Playwright MCP server (default: %(default)s). "
           "Must bundle Playwright < 1.63 (i.e. @playwright/mcp <= 0.0.78).")
  ```
  Replace `ff_version = 146 … re.search(...)` with `ff_version = detect_firefox_major(args.executable_path)`. Replace the version-specific comments ("Firefox 146") with version-neutral wording. Replace the silent `except Exception: config = {}` with:
  ```python
      except Exception as e:
          if not args.executable_path:
              raise
          print(f"ERROR: launch_options() failed: {e!r}. Refusing to start without a fingerprint; "
                f"re-run with CAMOUFOX_MCP_ALLOW_EMPTY_CONFIG=1 to override.", file=sys.stderr)
          if os.environ.get("CAMOUFOX_MCP_ALLOW_EMPTY_CONFIG") != "1":
              sys.exit(2)
          config = {}
  ```
  The mandate is production-ready: launching with no fingerprint is worse than failing loudly. The env escape hatch keeps the old behaviour available.
  Because the script lives at the repo root, add `sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))` before `from mcp_launcher import ...`, so it keeps working when Claude Code invokes it by absolute path from any cwd.
- [ ] **Step 5: Golden check that the generated config is unchanged apart from the version**
  ```bash
  # before (on pre-ff152-upgrade tag, 146 binary) and after (152 binary): capture the config file
  #   run the launcher with PATH pointing `npx` at a stub that just exits 0
  mkdir -p /tmp/stubbin && printf '#!/bin/sh\nexit 0\n' > /tmp/stubbin/npx && chmod +x /tmp/stubbin/npx
  PATH=/tmp/stubbin:$PATH python3 launch-camoufox-mcp.py --user-data-dir /tmp/p --executable-path "$B152" --humanize
  python3 - <<'EOF'
  import json, tempfile, os
  c = json.load(open(os.path.join(tempfile.gettempdir(), "camoufox-mcp-config.json")))
  lo = c["browser"]["launchOptions"]
  assert "Firefox/152.0" in c["browser"]["contextOptions"]["userAgent"]
  assert lo["firefoxUserPrefs"]["general.useragent.override"].endswith("Firefox/152.0")
  assert lo["firefoxUserPrefs"]["network.http.accept-encoding.secure"] == "gzip, deflate, br, zstd"
  assert c["browser"]["userDataDir"] == "/tmp/p" and c["capabilities"] == ["core","pdf","vision"]
  cfg = json.loads("".join(v for k, v in sorted(lo["env"].items()) if k.startswith("CAMOU_CONFIG")))
  assert cfg["humanize"] and cfg["headers.Accept-Encoding"] == "gzip, deflate, br, zstd"
  assert "timezone" not in cfg and "locale:language" not in cfg
  print("launcher config OK")
  EOF
  diff <(python3 launch-camoufox-mcp.py --help) /tmp/launcher-help-146.txt   # only the new --mcp-package lines differ
  ```
- [ ] **Step 6: Run all launcher tests**: `.venv/bin/python -m pytest mcp_launcher/tests -q`. Expected: all pass.
- [ ] **Step 7: Commit** `git commit -am "mcp_launcher: thin CLI over tested modules; auto-detect Firefox 152; --mcp-package; fail loudly without fingerprint"`

### [ ] Task 4.4: Decide the `@playwright/mcp` version

Compatibility matrix (from `npm view @playwright/mcp@<v> dependencies.playwright-core`, plus upstream `PLAYWRIGHT_BROWSER_FLOORS`):

| `@playwright/mcp` | bundled Playwright | Needs Camoufox ≥ | Within pythonlib ceiling `<1.63` |
|---|---|---|---|
| 0.0.68 (current pin) | 1.59.0-alpha | any | yes |
| 0.0.70 – 0.0.74 | 1.60.0-alpha | any (upstream measured 1.60 OK on beta.29/30) | yes |
| 0.0.75 – 0.0.76 | 1.61.0-alpha | **beta.30** | yes |
| 0.0.77 – 0.0.78 | 1.62.0-alpha | **beta.30** | yes |
| 0.0.79 – 0.0.82 | 1.63/1.64-alpha | beta.30+ (untested upstream) | **no, avoid** |

- [ ] **Step 1:** Run the Phase 5.2 MCP smoke test with the default `0.0.68`.
- [ ] **Step 2:** If Juggler protocol errors show up (e.g. `Protocol error (Browser.setDefaultViewport)`, or unknown-method errors because the FF152 Juggler is newer than the 1.59 client), retry with `--mcp-package @playwright/mcp@0.0.78`, the newest version that stays inside the tested ceiling.
- [ ] **Step 3:** Pin the version that passes in `DEFAULT_MCP_PACKAGE` and in the test, and record the result in this table.

---

## Phase 5 — Verification (all branch features + upstream suites)

**Deliverable:** evidence (command output) that every item in §1.2 works on the 152 build, plus green upstream test suites.

### [ ] Task 5.1: Upstream automated suites

- [ ] **Step 1: pythonlib unit tests**: `cd pythonlib && ../.venv/bin/python -m pytest tests -q`. Expected: all pass.
- [ ] **Step 2: Reinstall pythonlib into both venvs that run it**
  ```bash
  .venv/bin/pip install -e pythonlib
  /Users/dev345/code/kfirfer/claude-1/.venv/bin/pip install -e /Users/dev345/code/kfirfer/camoufox/pythonlib
  /Users/dev345/code/kfirfer/claude-1/.venv/bin/python -c "import camoufox, importlib.metadata as m; print(m.version('camoufox'))"   # 0.5.6
  ```
  Both venvs have Playwright **1.58**, which is below the 1.61 floor, so no browser-floor issue. Do not upgrade them past `<1.63`.
- [ ] **Step 3: build-tester against the new binary**
  ```bash
  cd build-tester && npm install && pip install -r requirements.txt
  python scripts/run_tests.py "$B152"
  ```
- [ ] **Step 4: Playwright tests**: `make tests` (edit the `--executable-path` in the Makefile invocation locally to the macOS path, or run `tests/run-tests.sh --executable-path "$B152"`). Record pass/fail counts, and compare failures with a run of the pristine upstream beta.30 binary before attributing them to the branch.

### [ ] Task 5.2: MCP end-to-end (F7, F8)

- [ ] **Step 1: Re-register the MCP server with the 152 binary** (update `MISC.md` accordingly)
  ```bash
  claude mcp remove playwright -s local
  claude mcp add playwright -- /Users/dev345/code/kfirfer/claude-1/.venv/bin/python3 \
    /Users/dev345/code/kfirfer/camoufox/launch-camoufox-mcp.py \
    --user-data-dir /Users/dev345/playwright-profile/profile-claude-camoufox \
    --executable-path "/Users/dev345/code/kfirfer/camoufox/camoufox-152.0.4-beta.30/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/MacOS/camoufox" \
    --humanize
  ```
  Back up the persistent profile first (`cp -a ~/playwright-profile/profile-claude-camoufox{,.bak-146}`). Firefox 146 → 152 migrates the profile forward, and there is no way back.
- [ ] **Step 2:** Run all of `STEALTH_TEST_MATRIX.md` with the expectations updated to 152 (Task 6.1). Minimum pass criteria:
  - `navigator.userAgent` = `… rv:152.0) Gecko/20100101 Firefox/152.0`, the same in Worker and ServiceWorker. HTTP `User-Agent` matches.
  - HTTPS `Accept-Encoding: gzip, deflate, br, zstd` (F3). br/zstd pages render (no mojibake).
  - With no `--timezone`: host TZ everywhere. With `--timezone Asia/Singapore`: Window = Worker = **ServiceWorker** = `Asia/Singapore` (F4).
  - Persistent profile: log in to a site, restart the MCP server, and confirm you are still logged in (F1/F7).
  - No "Camoufox" string in UA, `about:` dialogs or error pages (F6).
- [ ] **Step 3: Headed mode**: repeat with `--no-headless --showcursor`. Check that the viewport equals the window size and nothing flickers.

### [ ] Task 5.3: `launch_server` persistent profile against a live browser (F1)

- [ ] **Step 1**
  ```bash
  .venv/bin/python - <<'EOF' &
  from camoufox.server import launch_server
  launch_server(executable_path="$B152", ff_version=152, os="macos", i_know_what_im_doing=True,
                persistent_context=True, user_data_dir="/tmp/cf-ws-profile", headless=True)
  EOF
  # copy the ws:// endpoint it prints, then:
  .venv/bin/python -c "
  from playwright.sync_api import sync_playwright
  import sys
  with sync_playwright() as p:
      b = p.firefox.connect(sys.argv[1]); ctx = b.contexts[0]
      pg = ctx.pages[0] if ctx.pages else ctx.new_page(); pg.goto('https://example.com')
      ctx.add_cookies([{'name':'k','value':'v','url':'https://example.com'}]); print('ok', len(b.contexts))
  " ws://127.0.0.1:XXXX/YYYY
  ls /tmp/cf-ws-profile/cookies.sqlite   # the profile is on disk
  ```
  Kill the server, relaunch it, reconnect, and check that `ctx.cookies('https://example.com')` still contains `k`.

### [ ] Task 5.4: Humanize (F2)

- [ ] **Step 1:** Make `test_humanize.py` take the binary from `CAMOUFOX_BINARY` (defaulting to the 152 path), then run `CAMOUFOX_BINARY="$B152" .venv/bin/python test_humanize.py`. Expected: 15/15 clicks, the cursor visibly follows curved paths, and **no hang**. The `>=` guard is exercised whenever a button spawns at the viewport edge.

### [ ] Task 5.5: Regression greps (F6 + no stale 146)

- [ ] **Step 1**
  ```bash
  APP=camoufox-152.0.4-beta.30/obj-aarch64-apple-darwin/dist/Camoufox.app
  grep -rl "Camoufox can\|Camoufox is configured\|Camoufox doesn" $APP/Contents/Resources/browser 2>/dev/null | wc -l   # expect 0
  git grep -n "146" -- launch-camoufox-mcp.py mcp_launcher test_humanize.py   # expect 0 functional hits
  ```

### [ ] Task 5.6: Re-evaluate `CAMOUFOX_FEEDBACK.md`

- [ ] **Step 1:** Re-run the six-site battery from `CAMOUFOX_FEEDBACK.md`: demo.fingerprint.com (P1.1, possibly fixed by `debugger-invisible-to-content.patch`), bot.incolumitas.com (P1.2), fingerprint-scan.com (P1.3), browserleaks webgl (P1.4), CreepJS (P2.1), and the Cloudflare smoke test. Record the per-item status in that file (`fixed in 152` / `still open`). Do not start fixing open items here, because they are out of scope for the upgrade.

---

## Phase 6 — Documentation, Cleanup & Delivery

**Deliverable:** docs match the 152 reality, the branch is pushed, and the rollback path is documented.

### [ ] Task 6.1: Update branch docs

- [ ] `STEALTH_TEST_MATRIX.md`: replace the 33 occurrences of `146` (paths `camoufox-146.0.1-beta.25` → `camoufox-152.0.4-beta.30`, UA `rv:146.0`/`Firefox/146.0` → `152`). Update the TLS/JA4 expectation note (§"Known caveat": 152 is now current rather than long-tail), and replace the F4 grep with the new `ucid != 0` marker.
- [ ] `MISC.md`: new binary path and the `claude mcp add` lines.
- [ ] `test_humanize.py`: `CAMOUFOX_BINARY` env var (Task 5.4).
- [ ] Mark each task in this plan `[X]` and fill in the §4.4 table.
- [ ] Commit: `git commit -am "docs: update stealth matrix, MISC and helpers for Firefox 152.0.4 / Camoufox beta.30"`

### [ ] Task 6.2: Final review & push

- [ ] **Step 1:** `git log --oneline pre-ff152-upgrade..HEAD` shows one merge commit plus focused follow-up commits.
- [ ] **Step 2:** `git diff v152.0.4-beta.30 -- patches additions pythonlib` shows **only** the intended branch deltas: branding, the HTTPS-only encoding override, the ucid→0 fallback, `server.py`/`utils.py`/`sync_api.py`/`async_api.py` user-data-dir handling, and `test_user_data_dir.py`. Anything else is an accidental regression of upstream and must be reverted.
- [ ] **Step 3:** Request a code review (superpowers:requesting-code-review), then `git push origin fix-user-data`.
- [ ] **Step 4:** Remove the old tree **only after sign-off**: `rm -rf camoufox-146.0.1-beta.25 firefox-146.0.1.source.tar.xz` (both are gitignored, about 30 GB).

---

## 7. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `@playwright/mcp@0.0.68` (PW 1.59) client is incompatible with the FF152 Juggler | Medium | MCP unusable | Task 4.4 fallback to 0.0.78 (PW 1.62, officially floor-compatible with beta.30) |
| Upstream later removes the `_userDataDir` path from Playwright | Low (present through 1.62) | F1 `launch_server` breaks | `test_launch_server_forwards_user_data_dir` + pinned MCP/Playwright; the ceiling `<1.63` blocks untested upgrades |
| Regenerated patch drifts from the intended hunk | Low | F3/F4 silently lost | Task 3.4 greps against the compiled source tree + STEALTH_TEST_MATRIX runtime checks |
| Persistent profile migration 146 → 152 is one-way | Certain | Cannot downgrade the profile | Backup in Task 5.2 Step 1 |
| Native macOS build diverges from upstream's Linux cross-compile | Medium | Build errors | Host SDK 27.0 ≥ 26.4. Fallback: `docker build` + `multibuild.py --target macos --arch arm64` (uses `make setup-macos-sdk`) |
| Hidden upstream behaviour changes (WebRTC prefs, `privacy.partition.network_state=true`, `fission.webContentIsolationStrategy=0`) change stealth results | Medium | Matrix diffs | Task 5.2/5.6 compare against the baseline from Task 0.1 Step 3. These are upstream-intended changes, so keep them |

## 8. Rollback

```bash
git switch fix-user-data
git reset --hard pre-ff152-upgrade          # local only; if already pushed use: git revert -m 1 <merge-sha>
# restore MCP to the 146 binary (MISC.md lines at the pre-ff152-upgrade tag) and the profile backup:
rm -rf ~/playwright-profile/profile-claude-camoufox && cp -a ~/playwright-profile/profile-claude-camoufox{.bak-146,}
```

## 9. Follow-ups (out of scope)

- [ ] Evaluate **`v152.0.4-beta.31`**. It contains the mouse boundary-row dispatch fix (#751/#752, adjacent to F2), sealing of fingerprint setters (#749), `media:spoof_codecs`, and removal of stale `.bak` files. Merge it the same way (tag merge + the §2 decisions) once beta.30 is signed off.
- [ ] Propose the HTTPS-only Accept-Encoding and ucid→0 timezone fixes upstream (PRs against `daijro/camoufox`), which would shrink the branch delta for future upgrades.
- [ ] Open or maintain the remaining `CAMOUFOX_FEEDBACK.md` items that Task 5.6 finds still open.

## 10. Sources consulted

- Upstream repo, tags and diffs: `github.com/daijro/camoufox`, tags `v152.0.4-beta.26 … beta.31`, compare `d6540b5…v152.0.4-beta.30` (175 commits, 143 files). Trial merge via `git merge-tree`.
- Release notes (via Exa): `github.com/daijro/camoufox/releases`. beta.28 (#679/#680 humanize restored + edge guard), beta.30 (#743 Playwright 1.61, #745, #746 browser floor, #747 rustc 1.98).
- Firefox 152 sources: `mozilla-firefox/firefox@FIREFOX_152_0_4_RELEASE`, `modules/libpref/init/all.js` (Accept-Encoding defaults) and `build/moz.configure/toolchain.configure` (macOS SDK ≥ 26.4). Source tarball `archive.mozilla.org/pub/firefox/releases/152.0.4/source/` (HTTP 200).
- MDN Firefox 147/152 release notes (via Perplexity): no web-platform changes in 147; 152 adds WASM JSPI, text-module imports, and `MediaCapabilities` WebRTC type.
- Playwright: `playwright-core` 1.58.0 (`lib/browserServerImpl.js`), 1.61.0 and 1.62.0 (`lib/coreBundle.js`) confirm `options._userDataDir` → `launchPersistentContext`. Latest `playwright-core` is 1.63.0, bundling Firefox 153.0 (rev 1538). `@playwright/mcp` 0.0.68 → 0.0.82 dependency map from the npm registry.
