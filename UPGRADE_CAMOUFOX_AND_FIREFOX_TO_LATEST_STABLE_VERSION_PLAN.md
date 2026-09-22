# Upgrade Camoufox & Firefox to v152.0.4-beta.30 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.
>
> **Status legend:** `[ ]` not started · `[/]` in progress · `[X]` done. Update the markers as you go and commit the plan along with the work.
>
> **Validated 2026-09-21** against the repo, a scratch trial merge, the real Firefox 152.0.4 sources, and live runs of the 146 binary. See §11 for what was executed and what was corrected.

**Goal:** Move branch `fix-user-data` from Camoufox `146.0.1-beta.25` (Firefox 146.0.1) to the upstream Camoufox release **`v152.0.4-beta.30`** (Firefox **152.0.4**). Every fix and feature already on `fix-user-data` must be kept and must be shown to work after the move.

**What "latest stable" means here:** `v152.0.4-beta.30` (published 2026-09-01) is the newest **published** Camoufox release. `v152.0.4-beta.31` exists only as a git tag, with no GitHub release (see §9). Mainline Firefox stable is already **156.0** (`product-details.mozilla.org/1.0/firefox_versions.json`, checked 2026-09-21), but no Camoufox build exists for 153–156. Camoufox is a patch stack over a specific Firefox tarball, so the browser cannot be moved past the newest Camoufox rebase without re-porting all 53 patches, which is out of scope. The practical consequence: a `Firefox/152.0` UA is **four majors behind current**, which is a long-tail signal (see Task 6.1 and §7).

**Architecture:** `main` (`d6540b5`) is a **direct ancestor** of the upstream tag `v152.0.4-beta.30` (`5d06ec1`, 175 commits ahead). So the upgrade is a **merge of the upstream tag into `fix-user-data`**, not a re-port. Branch commits and history stay as they are, and nothing is force-pushed. A trial `git merge-tree` produced exactly **3 textual conflicts** and **1 semantic conflict**. Each has a decided resolution below. After the merge, we rebuild the browser natively on macOS arm64, harden the Python/server layer, and break the MCP launcher (`launch-camoufox-mcp.py`) into small tested modules that detect the Firefox version on their own.

**Tech Stack:** Firefox 152.0.4 source + Camoufox patch stack (C++/JS), Juggler (Playwright's Firefox protocol), Python `camoufox` pythonlib 0.5.6, Playwright (Python 1.58 locally; the MCP server bundles its own), `@playwright/mcp`, BrowserForge, `pytest`, GNU make + `./mach`.

**Spec:** This document (the user request) together with the existing branch docs `STEALTH_TEST_MATRIX.md` (acceptance tests) and `CAMOUFOX_FEEDBACK.md` (known detection issues).

## Global Constraints

- Target version: `version=152.0.4`, `release=beta.30` (upstream tag `v152.0.4-beta.30`, commit `5d06ec1629ac7843508f1e683f83e404fde8db76`).
- All work lands on branch **`fix-user-data`**. Never on `main`, never force-pushed.
- **Do not remove or change the behaviour of any branch feature** listed in §1.2. Any change to how one is *implemented* must be justified here and covered by a test.
- Upstream remote: `https://github.com/daijro/camoufox` (this repo's `origin` is the fork `kfirfer/camoufox`).
- Build host: macOS arm64 (Darwin 27, SDK 27.0). Firefox 152 needs macOS SDK **≥ 26.4** (`build/moz.configure/toolchain.configure: mac_sdk_min_version() == "26.4"`), so this host qualifies. **Correction (2026-09-22, found in Task 3.3):** the host *default* SDK 27.0 does **not** work: its `.tbd` stubs list `arm64e.x1-macos`, which the bootstrapped clang/lld **20.1.8** rejects (`could not load TAPI file … unknown architecture`), and configure dies with `Couldn't find one that works`. The installed `MacOSX26.5.sdk` (≥ 26.4, no `arm64e.x1`, and the SDK upstream's `macos.mozconfig` uses for cross builds) works. `scripts/build-macos-native.sh` selects it via `MACOS_SDK_DIR` (the env alias of `--with-macos-sdk`; `SDKROOT` alone is ignored because configure scans xcrun's SDK dir for the *newest* SDK), keeping the Makefile clean. It also pins `RUSTC`/`CARGO` to the active toolchain's real binaries: Homebrew's rustup proxies are bash wrappers that lose `argv[0]`, so configure's `rustup which rustc` unwrap step runs rustc and fails (`multiple input filenames provided`).
- Firefox 152 stock header defaults (checked against `FIREFOX_152_0_4_RELEASE:modules/libpref/init/all.js`):
  `network.http.accept-encoding = "gzip, deflate"`, `network.http.accept-encoding.secure = "gzip, deflate, br, zstd"`, `network.http.accept-encoding.dictionary = "dcb, dcz"`.
- Python constraint from upstream pythonlib 0.5.6: `playwright < 1.63`. From Playwright **1.61** onward the browser build must be **≥ beta.30** (`CONSTRAINTS.PLAYWRIGHT_BROWSER_FLOORS`).
- `@playwright/mcp` stays pinned (currently `0.0.68` → Playwright `1.59.0-alpha`). Any bump must stay on Playwright `< 1.63`, i.e. `@playwright/mcp ≤ 0.0.78`.
- Keep the upstream Makefile diff clean. Put build-environment tweaks in scripts, not the Makefile.
- Python environments (checked 2026-09-21): the repo `.venv` is **uv-managed**. It has **no `pip` and no `pytest`**, and a non-editable site-packages `camoufox 0.5.0`, so every `pytest` step fails until Task 0.3 runs. The MCP runtime venv `claude-1/.venv` is Python 3.12 with `camoufox 0.4.11` and only ships `pip3`, not `pip`. Always call interpreters by explicit path (`.venv/bin/python`), never a bare `python3`/`pip`.
- The upstream merge adds a root **`CLAUDE.md`** (agent guidance: "do not hand-edit patch files", macOS builds are normally cross-compiled from Linux). The hand-edits in Task 1.1 are unavoidable conflict resolution. They are validated in §11, and Task 3.2 re-checks them against a real tree.

---

## 1. Current-State Analysis (review of the branch & server-connection code)

### 1.1 Components that talk to the browser ("server connection" layer)

| Component | File | Role | Upgrade impact |
|---|---|---|---|
| MCP launcher | `launch-camoufox-mcp.py` | Builds the fingerprint + prefs, writes `camoufox-mcp-config.json`, then `exec`s `npx @playwright/mcp@0.0.68 --config …`. The MCP server launches Camoufox over Juggler (pipe), with an optional persistent `userDataDir`. | **High.** It hard-codes `ff_version = 146`, finds the version with a path regex, patches `_load_properties` for macOS bundles, and its comments and UA strings assume 146. |
| Python WS server | `pythonlib/camoufox/server.py` | `launch_server()` → Node `launchServer.js` → Playwright `firefox.launchServer()` → prints the WS endpoint. | **High.** Upstream rewrote it (graceful shutdown, loads the driver via `index.js`) **and added code that rejects `user_data_dir`**, which clashes with the branch feature. The branch's version of this feature never actually delivered persistence (§1.3-2). |
| Node bridge | `pythonlib/camoufox/launchServer.js` | Reads base64 options from stdin and calls the Playwright launcher. | **High.** Upstream replaced `require(lib/browserServerImpl.js)` (removed in Playwright 1.60) with `require(<driver>/index.js)`. Take upstream's version. |
| Launch options | `pythonlib/camoufox/utils.py` | `launch_options()` builds env (`CAMOU_CONFIG_*`), prefs and the executable path. The branch added `persistent_context` / `user_data_dir` → `_user_data_dir`. | **High.** Auto-merges, but carries a pre-existing bug (§1.3). |
| Juggler page handler | `additions/juggler/protocol/PageHandler.js` | Mouse dispatch. The branch added the humanized trajectory. | **Conflict.** Upstream (beta.28, PRs #679/#680) re-implemented the same feature with a hang fix. |
| C++ patches | `patches/network-patches.patch`, `patches/timezone-spoofing.patch`, `patches/voice-spoofing.patch` | Accept-Encoding override, worker timezone, voice registry. | **Conflict** in two of them (§2). |

### 1.2 Branch features that MUST survive (acceptance inventory)

| # | Feature / fix (commit) | Where | How it is verified after the upgrade |
|---|---|---|---|
| F1 | Persistent profile (`user_data_dir`) through `launch_options()` + `launch_server()` (`_userDataDir`, plus `_sharedBrowser` from this upgrade) (`9527ed5`, `server.py` camel-case `_` prefix) | `pythonlib/camoufox/utils.py`, `server.py`, `sync_api.py`, `async_api.py` | Task 2.1, 2.2 tests + Task 5.3 |
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
2. **Upstream `launch_server()` rejects `persistent_context` / `user_data_dir`** with the reason that "Playwright cannot serve a persistent context". **That reason is half right, and the branch feature was silently broken all along.** Verified on 2026-09-21 by source reading and live runs (merged pythonlib, Playwright 1.58 driver, 146 binary):
   - `BrowserServerLauncherImpl.launchServer()` does honour `options._userDataDir` and calls `launchPersistentContext()` (present in playwright-core 1.58 `lib/browserServerImpl.js:69-72` and in 1.59/1.60/1.61/1.62 `lib/coreBundle.js`). The profile directory is created and written.
   - **However**, the server is created in mode `"launchServer"`, which maps to `isolateContexts: !sharedBrowser` = `true` (`server/dispatchers/playwrightDispatcher.js:57`). Remote clients therefore see **`browser.contexts == []`**, and every `new_context()` they create is an ephemeral container context. Live result: cookies and `localStorage` set by a client were **gone** after a server restart.
   - **Fix:** also send the private option **`_sharedBrowser: true`** whenever `_userDataDir` is set. That selects mode `"launchServerShared"` → `isolateContexts: false`, and the dispatcher then exposes `browser._defaultContext` (the persistent one) to every client. `_sharedBrowser` is present in playwright-core 1.58, 1.59, 1.60, 1.61 and 1.62. Live result: `len(b.contexts) == 1`, and cookie + `localStorage` **survived** a server restart.
   - Decision: keep F1 and make it actually work (drop the rejection and add `_sharedBrowser`). Adopting upstream's rejection would also be defensible; it is the fallback if Playwright ever drops `_sharedBrowser` (see §7).
3. **Launcher version detection is fragile.** `ff_version = 146` is hard-coded, with a regex on the path (`camoufox-(\d+)`) as the only override. A binary installed through `camoufox fetch`, or any other path layout, silently gets a **Firefox/146 UA on a 152 engine**, a strong UA-vs-engine tell.
4. `launch-camoufox-mcp.py` is a 629-line file whose `main()` alone is about 540 lines. It has no tests, a module-level monkey-patch, and a silent `except Exception → config = {}` fallback that drops the whole fingerprint.
5. **pythonlib cannot use a macOS `.app` executable in-process or via `launch_server`.** `utils._load_properties(path)` reads `<exe dir>/properties.json`, i.e. `Camoufox.app/Contents/MacOS/properties.json`, but the bundle ships the file in `Contents/Resources/`. Reproduced: `Camoufox(executable_path=".../Contents/MacOS/camoufox")` raises `FileNotFoundError`, both before and after the merge (upstream beta.30 still does this). Upstream's own `service-tester/run_tests.sh` works around it by **copying** `properties.json` into `MacOS/`. The MCP launcher avoids it with the F8 shim. Tasks 3.3 and 5.3 must do one or the other.
6. **The launcher writes the whole host environment, secrets included, into `camoufox-mcp-config.json`.** `launch_options()` defaults `env` to `dict(os.environ)` (pre-existing: `main` did the same). The launcher copies `config["env"]` into `launchOptions.env`, then `json.dump`s it into `$TMPDIR/camoufox-mcp-config.json` with the default umask (0644). A live run confirmed API-key/password env vars land in that file. `$TMPDIR` is per-user on macOS, but `/tmp` on Linux is shared. Minimal hardening (Task 4.3): create the file with mode `0600`. Do not change what goes into `env`: Playwright's `env` *replaces* the browser environment, so trimming it is a behaviour change.

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
| `patches/timezone-spoofing.patch` | textual (hunk moved: `@@ -6350` → `@@ -6429`; upstream also re-ordered the file sections) | **Take upstream, then re-add only the `ucid → 0` fallback** in the relocated `WorkerPrivate.cpp` hunk, **and fix its header to `@@ -6429,6 +6431,27 @@`** | Upstream's `TimezoneManager::GetTimezone()` now falls back to `MaskConfig::GetString("timezone")` by itself, so our explicit MaskConfig branch (and the `MaskConfig.hpp` include in `WorkerPrivate.cpp`) is redundant. The one piece still missing upstream is the fallback to the default container (`ucid 0`) when a non-default container has no entry. **Precedence changes slightly:** the branch order was `ucid → ucid 0 → MaskConfig`, and the merged order is `ucid → MaskConfig (inside GetTimezone) → ucid 0`. It differs only when a global `timezone` config *and* a different `window.setTimezone()` on container 0 are both active. That is not a configuration the MCP launcher produces, so it is accepted. |
| `pythonlib/camoufox/server.py` | **semantic** (auto-merged; merged file == upstream) | **Delete upstream's `persistent_context`/`user_data_dir` rejection loop.** Pop `persistent_context`, keep `user_data_dir`, and **set `_shared_browser=True` when a profile dir is set**. | See §1.3-2. Without `_sharedBrowser` the profile is launched but unreachable. |
| `pythonlib/camoufox/utils.py` | auto-merged + latent bug | Emit `_user_data_dir` **only when set**. Translate it in `NewBrowser`/`AsyncNewBrowser` **without dropping upstream's `no_viewport` default (#666)**. | See §1.3-1. |
| `patches/voice-spoofing.patch` | auto-merged | Take merged result | Upstream carries the same `@@ -63,2 +63,5 @@` fix plus #731. |
| `launchServer.js`, branding, docs | auto-merged | Take merged result | Branding files are untouched upstream, so ours are kept. |

---

## 3. File Structure (created / modified)

```
upstream.sh                                   M  version=152.0.4 release=beta.30 (from merge)
additions/juggler/protocol/PageHandler.js     M  = upstream beta.30
patches/network-patches.patch                 M  ours (HTTPS-only), re-based hunk
patches/timezone-spoofing.patch               M  upstream + ucid→0 fallback
pythonlib/camoufox/server.py                  M  allow user_data_dir → _userDataDir + _sharedBrowser
pythonlib/camoufox/utils.py                   M  conditional _user_data_dir + split_user_data_dir()
pythonlib/camoufox/sync_api.py                M  use split_user_data_dir()
pythonlib/camoufox/async_api.py               M  use split_user_data_dir()
pythonlib/tests/test_user_data_dir.py         C  F1 regression tests
mcp_launcher/__init__.py                      C  package marker
mcp_launcher/version.py                       C  detect_firefox_major()
mcp_launcher/user_agent.py                    C  clean_user_agent(), needs_ua_refresh()
mcp_launcher/profile.py                       C  scrub_stale_prefs()
mcp_launcher/bundle.py                        C  macOS properties.json shim (install_properties_shim)
mcp_launcher/mcp_config.py                    C  build_mcp_config(), build_mcp_args(), write_mcp_config() (0600)
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

Phases 2, 3 and 4 are independent after Phase 1. Phase 3 (the build, about 40 min cold) can run while 2 and 4 are in progress. Phase 5 needs all three. Phases 2 and 4 need the test environment from **Task 0.3**. Tasks 4.1 Step 5 and 5.x need the built binary from Phase 3.

---

## Phase 0 — Preparation & Safety Net

**Deliverable:** a recoverable starting point, a recorded baseline, and the upstream tag available locally.

### [X] Task 0.1: Snapshot and baseline

**Files:** none modified.

- [X] **Step 1: Confirm a clean tree on the right branch**
  ```bash
  cd /Users/dev345/code/kfirfer/camoufox
  git switch fix-user-data && git status --short   # expect: empty
  git pull --ff-only origin fix-user-data
  ```
- [X] **Step 2: Tag the pre-upgrade state (rollback point)**
  ```bash
  git tag -a pre-ff152-upgrade -m "fix-user-data before v152.0.4-beta.30 merge"
  git push origin pre-ff152-upgrade
  ```
- [X] **Step 3: Record the baseline behaviour of the current 146 build** (used for the before/after comparison in Phase 5)
  ```bash
  B146=camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/MacOS/camoufox
  ls -la "$B146"
  "$B146" --version                    # "Camoufox Camoufox 146.0.1-beta.25" (from application.ini, not branding)
  # CLI surface to preserve. Fix COLUMNS: argparse wraps help to the terminal width.
  COLUMNS=100 .venv/bin/python launch-camoufox-mcp.py --help > /tmp/launcher-help-146.txt
  # The launcher persists the MCP identity here. Every launcher run (including Task 4.3's
  # golden check) rewrites it, so keep a copy for rollback (§8).
  cp -p ~/.camoufox-mcp-fingerprint.json ~/.camoufox-mcp-fingerprint.json.bak-146
  claude mcp get playwright > /tmp/mcp-registration-146.txt   # exact current flags, reused in Task 5.2
  ```
  Also run the STEALTH_TEST_MATRIX "Quick smoke" section against 146 and save the results as `docs/upgrade/baseline-146.md` (not committed if it contains personal IP data).
- [X] **Step 4: Free disk space.** A Firefox 152 source tree plus an obj dir needs about 40 GB. Keep the 146 tree until Phase 5 passes.

### [X] Task 0.2: Bring in the upstream tag

- [X] **Step 1: Add upstream remote and fetch the exact tag**
  ```bash
  git remote add upstream https://github.com/daijro/camoufox.git 2>/dev/null || true
  git fetch upstream tag v152.0.4-beta.30 --no-tags   # tag is already local as of 2026-09-21; this is a no-op then
  git rev-parse v152.0.4-beta.30^{commit}   # expect 5d06ec1629ac7843508f1e683f83e404fde8db76
  git merge-base --is-ancestor main v152.0.4-beta.30 && echo "main is ancestor — merge is safe"
  ```
- [X] **Step 2: Dry-run the merge and confirm the conflict set matches §2**
  ```bash
  git merge-tree --write-tree --name-only fix-user-data v152.0.4-beta.30
  ```
  Expected: the first line is a tree OID (validated: `32a28c5…`), then exactly `additions/juggler/protocol/PageHandler.js`, `patches/network-patches.patch`, `patches/timezone-spoofing.patch`, then the `CONFLICT`/`Auto-merging` messages. Exit code 1 is normal when there are conflicts. If the file list differs, stop and update §2 before continuing.

### [X] Task 0.3: Test environment (prerequisite for Phases 2 and 4)

The repo `.venv` is uv-managed and has **no `pip` and no `pytest`**. `import camoufox` currently resolves to a **non-editable site-packages 0.5.0**, so tests would silently exercise the wrong code.

- [X] **Step 1: Install pythonlib editable, plus pytest** (run *after* Task 1.1, so the editable install points at the merged 0.5.6 tree)
  ```bash
  uv pip install --python .venv/bin/python -e pythonlib "playwright<1.63" pytest
  .venv/bin/python -c "import camoufox, importlib.metadata as m; print(camoufox.__file__, m.version('camoufox'))"
  # expect: .../camoufox/pythonlib/camoufox/__init__.py 0.5.6
  ```
  Validated in a scratch venv: the merged tree's upstream suite then gives `168 passed, 4 skipped`.

---

## Phase 1 — Merge Upstream v152.0.4-beta.30 into `fix-user-data`

**Deliverable:** a single merge commit on `fix-user-data` with the conflicts resolved per §2. `upstream.sh` reads `152.0.4` / `beta.30`.

### [X] Task 1.1: Perform the merge and resolve conflicts

**Files:**
- Modify: `additions/juggler/protocol/PageHandler.js`, `patches/network-patches.patch`, `patches/timezone-spoofing.patch`, `pythonlib/camoufox/server.py`

- [X] **Step 1: Start the merge**
  ```bash
  git merge --no-ff --no-commit v152.0.4-beta.30
  ```
- [X] **Step 2: PageHandler.js: take upstream**
  ```bash
  git checkout --theirs additions/juggler/protocol/PageHandler.js
  grep -n "camouGetMouseTrajectory\|_lastTrackedPos\|>= boundingBox.width" additions/juggler/protocol/PageHandler.js
  ```
  Expected: all three patterns are present (trajectory call, tracked position, `>=` guard).
- [X] **Step 3: network-patches.patch: keep ours, on upstream's context.** Resolve the conflict so that the `SetAcceptEncodings` hunk reads exactly:
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
  Remove the `nsCString encodingOverride;` / `aAcceptEncodings = encodingOverride.get();` lines from upstream #543. In practice this means keeping the whole `HEAD` side of the conflict block and dropping the `v152.0.4-beta.30` side. The `@@ -2101,6 +2115,10 @@` header is already exact: upstream's own hunk starts at `-2098`/`+2112` with 3 more leading context lines. **Validated:** it applies to `FIREFOX_152_0_4_RELEASE` sources with no offset or fuzz, both alone and inside the full 53-patch stack.
- [X] **Step 4: timezone-spoofing.patch: take upstream, re-add the ucid→0 fallback**
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
  Do **not** re-add `#include "MaskConfig.hpp"` to `WorkerPrivate.cpp`, because it is no longer used there.

  **Then fix the hunk header in the same step. This is mandatory, not deferrable:**
  ```bash
  sed -i '' 's/^@@ -6429,6 +6431,19 @@/@@ -6429,6 +6431,27 @@/' patches/timezone-spoofing.patch
  grep -n '^@@ -6429' patches/timezone-spoofing.patch   # expect: @@ -6429,6 +6431,27 @@
  ```
  The block above has 20 lines plus the existing trailing `+` blank line, i.e. 21 added lines versus upstream's 13, so the new-side count is `6 + 21 = 27`. **Validated:** leaving the header at `+6431,19` makes `patch` abort with `malformed patch at line 291`, which fails `make dir` in Task 3.1 *before* Task 3.2 could regenerate anything. With `+6431,27`, the patch applies with no offset or fuzz and yields the same result set as pristine upstream in the full stack.
- [X] **Step 5: server.py: drop the persistent rejection** (auto-merged file, semantic fix). Replace upstream's loop:
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
      # `_userDataDir` option and calls launchPersistentContext(). On its own
      # that is not enough: in the default "launchServer" mode every client
      # connection gets isolated contexts and never sees the persistent default
      # context, so nothing it does is saved. `_sharedBrowser` selects
      # "launchServerShared", which exposes that context as browser.contexts[0].
      # Both private options exist in playwright-core 1.58-1.62 (pythonlib pins
      # playwright<1.63). launch_options() emits `_user_data_dir`, and
      # camel_case() keeps the leading underscore (-> `_userDataDir`,
      # `_sharedBrowser`).
      kwargs.pop('persistent_context', None)
  ```
  and directly after `config = launch_options(**kwargs)` add:
  ```python
      if config.get('_user_data_dir'):
          config['_shared_browser'] = True
  ```
  Also rewrite the docstring note: persistent profiles are servable, and clients reach the profile through `browser.contexts[0]`, not `new_context()`.
- [X] **Step 6: Check that no conflict markers remain, then commit the merge**
  ```bash
  git diff --check && ! git grep -n '^<<<<<<<\|^>>>>>>>' -- additions patches pythonlib
  cat upstream.sh    # version=152.0.4 / release=beta.30
  git add -A && git commit -m "Merge upstream Camoufox v152.0.4-beta.30 (Firefox 152.0.4) into fix-user-data

  Conflicts resolved:
  - PageHandler.js: take upstream humanize trajectory (superset + edge-guard hang fix)
  - network-patches.patch: keep HTTPS-only Accept-Encoding override (FF152 dictionary list dcb,dcz must stay stock)
  - timezone-spoofing.patch: take upstream (GetTimezone MaskConfig fallback) + keep ucid->0 SW fallback
  - server.py: keep persistent launch_server support (_userDataDir + _sharedBrowser) — drop upstream rejection"
  ```
  Then run Task 0.3 (editable install against the merged tree).

---

## Phase 2 — Python Library: Persistent-Profile Correctness (F1)

**Deliverable:** `launch_options()`, `Camoufox()`, `AsyncCamoufox()` and `launch_server()` all work both with and without `user_data_dir`, with regression tests.

### [X] Task 2.1: `_user_data_dir` only when set + a single translation helper

**Files:**
- Modify: `pythonlib/camoufox/utils.py` (the `launch_options` result dict, about lines 975-990 after the merge)
- Modify: `pythonlib/camoufox/sync_api.py` (`NewBrowser`), `pythonlib/camoufox/async_api.py` (`AsyncNewBrowser`)
- Test: `pythonlib/tests/test_user_data_dir.py`

**Interfaces:**
- Produces: `camoufox.utils.split_user_data_dir(options: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]`, which returns a copy of `options` without `_user_data_dir`/`user_data_dir`, plus the directory if either was present.

- [X] **Step 1: Write the failing tests**
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
      # The exact TypeError from §1.3-1, without needing a browser.
      from camoufox import sync_api
      monkeypatch.setattr(sync_api, "attach_no_viewport_default", lambda b: None)
      pw = _FakePlaywright()
      sync_api.NewBrowser(pw, from_options={"headless": True, "_user_data_dir": "/p"})
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
  ```
- [X] **Step 2: Run and confirm they fail**
  ```bash
  cd pythonlib && ../.venv/bin/python -m pytest tests/test_user_data_dir.py -v
  ```
  Expected: `ImportError: cannot import name 'split_user_data_dir'`.
- [X] **Step 3: Implement it in `utils.py`.** Remove the unconditional `"_user_data_dir": …` entry from the `result` dict (about line 985 after the merge). Add this after the `if proxy is not None:` block:
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
      launch_server); in-process Playwright wants it as the positional
      `user_data_dir` of launch_persistent_context() instead.
      """
      opts = dict(options)
      # Pop both keys unconditionally: an `a or b` short-circuit would leave
      # `user_data_dir` behind whenever `_user_data_dir` is set.
      private = opts.pop("_user_data_dir", None)
      public = opts.pop("user_data_dir", None)
      udd = private or public
      return opts, (str(udd) if udd else None)
  ```
- [X] **Step 4: Use it in both APIs, keeping upstream's viewport logic.** The merged `NewBrowser` already contains upstream's `no_viewport_default = spoofs_window_dimensions(from_options)` block (the Juggler deadlock fix for #666) and `attach_no_viewport_default(browser)`. **Do not rewrite the function.** Change only the two launch calls. Validated diff for `sync_api.py`:
  ```diff
   from .utils import (
       attach_no_viewport_default,
       launch_options,
  +    split_user_data_dir,
       spoofs_window_dimensions,
       sync_attach_vd,
   )
  @@ def NewBrowser(
       if persistent_context:
           if no_viewport_default and not ('viewport' in from_options or 'no_viewport' in from_options):
               from_options = {**from_options, 'no_viewport': True}
  -        context = playwright.firefox.launch_persistent_context(**from_options)
  +        opts, user_data_dir = split_user_data_dir(from_options)
  +        if not user_data_dir:
  +            raise ValueError("persistent_context=True requires user_data_dir")
  +        context = playwright.firefox.launch_persistent_context(user_data_dir, **opts)
           return sync_attach_vd(context, virtual_display)

       # Browser
  -    browser = playwright.firefox.launch(**from_options)
  +    browser = playwright.firefox.launch(**split_user_data_dir(from_options)[0])
       if no_viewport_default:
           attach_no_viewport_default(browser)
  ```
  Apply the identical change to `async_api.AsyncNewBrowser`, with `await` in front of both launch calls.
- [X] **Step 5: Run the new tests and the whole upstream suite**
  ```bash
  cd pythonlib && ../.venv/bin/python -m pytest tests -q
  ```
  Expected with Playwright 1.58: **`177 passed, 4 skipped`** (168 upstream + the 9 tests above), and 178 once Task 2.2 appends its test. A scratch run of the same code, without `test_split_pops_both_keys_when_both_present`, gave 177 including Task 2.2. No upstream test asserts the `launch_server` rejection, so no upstream test needs changing.
- [X] **Step 6: Commit**
  ```bash
  git add pythonlib && git commit -m "pythonlib: emit _user_data_dir only when set; translate for in-process persistent contexts (fixes TypeError in Camoufox())"
  ```

### [X] Task 2.2: `launch_server()` persistent round-trip test

**Files:** Test: `pythonlib/tests/test_user_data_dir.py` (append)

- [X] **Step 1: Test that the Node payload carries `_userDataDir`**
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
      # launchServerShared mode: without it Playwright isolates contexts per
      # connection and clients never see the persistent default context.
      assert sent["_sharedBrowser"] is True
  ```
- [X] **Step 2: Run it (`../.venv/bin/python -m pytest tests/test_user_data_dir.py -v`). Expected: PASS** (validated). A `ValueError` means the upstream rejection was not removed. A `KeyError: '_sharedBrowser'` means the second half of Task 1.1 Step 5 is missing.
- [X] **Step 3: Commit** `git commit -am "pythonlib: test launch_server persistent profile forwarding"`

---

## Phase 3 — Browser Source: Patch Regeneration & Native macOS Build

**Deliverable:** `camoufox-152.0.4-beta.30/obj-aarch64-apple-darwin/dist/Camoufox.app` built from the merged branch, with every patch applying cleanly and F3/F4 present in the compiled sources.

### [X] Task 3.1: Fetch and prepare the Firefox 152.0.4 tree

- [X] **Step 1: Toolchain check**
  ```bash
  xcrun --show-sdk-version           # must be >= 26.4 (host: 27.0)
  python3 -c 'import tomllib'        # mach needs Python >= 3.11
  ```
- [X] **Step 2: Fetch + extract + copy additions**
  ```bash
  which aria2c                                     # `make fetch` uses aria2c (installed at /opt/homebrew/bin)
  make fetch          # archive.mozilla.org/pub/firefox/releases/152.0.4/source/ (verified HTTP 200)
  make setup          # creates camoufox-152.0.4-beta.30/ as a git repo tagged `unpatched`
  ```
- [X] **Step 3: Apply the full patch stack**
  ```bash
  # patch.py defaults to macos,arm64 when BUILD_TARGET is unset; set it explicitly anyway.
  BUILD_TARGET=macos,arm64 make dir 2>&1 | tee /tmp/ff152-patch.log; echo "make dir exit=${pipestatus[1]:-${PIPESTATUS[0]}}"
  grep -E "patch\(es\) failed|\.rej|malformed" /tmp/ff152-patch.log   # expect: nothing
  grep -n "target=aarch64-apple-darwin" camoufox-152.0.4-beta.30/mozconfig
  ```
  `patch.py` prints `ERROR: N patch(es) failed to apply cleanly` and exits 1 on any reject. Note that `make dir` resets the tree (`git reset --hard unpatched && ./mach clobber && git clean -fdx`), which also wipes any `obj-*` dir.
  **Pre-validated:** the merged stack with the §2 resolutions was applied in `patch.py` order (sorted by basename, `roverfox/` last) to the 208 `FIREFOX_152_0_4_RELEASE` files the stack touches. `network-patches.patch` and `timezone-spoofing.patch` applied with no offset or fuzz, and the result set was identical to pristine upstream beta.30. A real failure here therefore points at the tarball or tree state rather than the resolutions.
- [X] **Step 4: One-time mach bootstrap** (only if `~/.mozbuild` is stale): `make mozbootstrap` — not run: `--enable-bootstrap` fetches toolchains on demand during `make build`.

### [X] Task 3.2: Confirm (and only if needed, regenerate) the two hand-merged patches

> **Result (2026-09-22):** `make dir` exit 0, zero `offset`/`fuzz`/reject lines in `/tmp/ff152-patch.log`; both hunks present in the tree. Steps 2–4 were **not needed** (not run).

With the Task 1.1 headers, both patches were validated to apply with no offset or fuzz, so **regeneration is expected to be unnecessary**. This task is a verification gate. Regenerate only if Task 3.1's log shows `offset`/`fuzz`/rejects for either file.

**Files:** (only if regenerating) `patches/network-patches.patch`, `patches/timezone-spoofing.patch`

- [X] **Step 1: Check the apply log for either patch**
  ```bash
  grep -A8 -E "network-patches.patch|timezone-spoofing.patch" /tmp/ff152-patch.log | grep -E "offset|fuzz|FAILED" ; echo "(empty = nothing to regenerate)"
  grep -n -A6 "else if (isSecure)" camoufox-152.0.4-beta.30/netwerk/protocol/http/nsHttpHandler.cpp
  grep -n -B2 -A18 "Apply per-context timezone override" camoufox-152.0.4-beta.30/dom/workers/WorkerPrivate.cpp
  ```
  Expected: the HTTPS-only override (Task 1.1 Step 3) and the `ucid != 0` fallback block (Task 1.1 Step 4).
- [X] **Step 2 (only if Step 1 showed offset/fuzz/reject): regenerate non-interactively.** `make edits` is an `easygui` GUI, so an agent should not use it. Its "Write workspace to patch" is just `git diff first-checkpoint > file`. That form **omits untracked files**, and `timezone-spoofing.patch` *creates* `dom/base/TimezoneManager.{cpp,h}`, so stage everything first:
  ```bash
  P=patches/timezone-spoofing.patch          # or patches/network-patches.patch
  make workspace ./$P                        # unapply → first-checkpoint → re-apply this patch
  # (fix the source by hand here if it applied with fuzz)
  (cd camoufox-152.0.4-beta.30 && git add -A && git diff --cached first-checkpoint -- . ':!_READY' ':!mozconfig*') > $P.new
  (cd camoufox-152.0.4-beta.30 && git reset -q)
  diff <(grep '^diff --git' $P | sort -u) <(grep '^diff --git' $P.new | sort -u) && mv $P.new $P   # same file set, or stop
  ```
- [X] **Step 3: Round-trip check** (only if Step 2 ran). Every patch applies to a pristine tree:
  ```bash
  BUILD_TARGET=macos,arm64 make dir 2>&1 | grep -E "patch\(es\) failed|\.rej|malformed"; echo "(empty = OK)"
  ```
  `make dir` resets to `unpatched` by itself, so no separate `make revert` is needed.
- [X] **Step 4: Commit** (only if Step 2 ran) `git commit -am "patches: regenerate network/timezone patches against Firefox 152.0.4"`

### [/] Task 3.3: Build and package

- [ ] **Step 1: Build**: `scripts/build-macos-native.sh build 2>&1 | tee /tmp/ff152-build.log` (plain `make build` fails on this host's SDK 27, see Global Constraints). That takes about 40 min cold and about 5 min incremental with ccache. Expected tail: `Your build was successful!`
- [ ] **Step 2: Smoke-run the binary**
  ```bash
  APP=$PWD/camoufox-152.0.4-beta.30/obj-aarch64-apple-darwin/dist/Camoufox.app
  B152=$APP/Contents/MacOS/camoufox
  "$B152" --version        # expect: "Camoufox Camoufox 152.0.4-beta.30"
  grep '^Version=' "$APP/Contents/Resources/application.ini"   # expect: Version=152.0.4-beta.30
  ```
  `--version` prints `Vendor`/`Name` from `application.ini`, which stay "Camoufox" (the 146 build prints `Camoufox Camoufox 146.0.1-beta.25`). F6 changes only the user-visible locale strings (`brand.ftl`, `brand.properties`, `appstrings.properties`, `aboutDialog.xhtml`), which Task 5.5 checks.
- [ ] **Step 3: Make pythonlib able to use the bundle** (§1.3-5). This mirrors upstream `service-tester/run_tests.sh`:
  ```bash
  cp "$APP/Contents/Resources/properties.json" "$APP/Contents/MacOS/properties.json"
  ```
  This is needed by Task 5.3 and by any in-process `Camoufox(executable_path="$B152")`. The obj dir is regenerated by `make build`, so repeat this after every rebuild. The MCP launcher does not depend on it, because it keeps the F8 shim.
- [ ] **Step 4 (optional, for distribution): package**: `make package-macos arch=arm64`.

### [X] Task 3.4: Static verification that the branch C++ fixes are compiled in

- [X] **Step 1**
  ```bash
  SRC=camoufox-152.0.4-beta.30
  grep -A4 "else if (isSecure)" $SRC/netwerk/protocol/http/nsHttpHandler.cpp | grep -q MaskConfig && echo F3-OK
  grep -q "ucid != 0" $SRC/dom/workers/WorkerPrivate.cpp && echo F4-OK
  ! grep -q '"MaskConfig.hpp"' $SRC/dom/workers/WorkerPrivate.cpp && echo F4-no-stale-include-OK
  grep -q 'MaskConfig::GetString("timezone")' $SRC/dom/base/TimezoneManager.cpp && echo F4-upstream-OK
  grep -q "MVoices().has_value()" $SRC/dom/media/webspeech/synth/nsSynthVoiceRegistry.cpp && echo F5-OK
  grep -q "camouGetMouseTrajectory" $SRC/juggler/protocol/PageHandler.js && echo F2-OK
  ```
  Expected: all six `*-OK` lines. All six were validated against the stack applied to real FF152 files. `juggler/` sits at the source root because `copy-additions.sh` runs `cp -r ../additions/* .`.

---

## Phase 4 — MCP Launcher: Modular, Version-Aware, Tested (F7, F8)

**Deliverable:** `launch-camoufox-mcp.py` becomes a thin CLI over `mcp_launcher/`. CLI flags and generated config are byte-for-byte equivalent (except for the version-derived UA). The Firefox major version is detected from the binary. Unit tests cover every module.

Run all tests with the repo venv **after Task 0.3**: `.venv/bin/python -m pytest mcp_launcher/tests -q`. `test_bundle.py` imports `camoufox.utils`, so without the editable install it tests site-packages 0.5.0 (or fails). Keep the code **Python 3.12-compatible**: the MCP entry runs under `claude-1/.venv` (3.12), even though the root `pyproject.toml` says `>=3.13`.

**Pre-validated:** the test and implementation code for Tasks 4.1 and 4.2 in this plan was extracted verbatim and run against the merged pythonlib, giving **15 passed**. `detect_firefox_major()` on the real 146 bundle returned `146`, read from `Contents/Resources/application.ini` (`Version=146.0.1-beta.25`). The current launcher, run unmodified against merged pythonlib 0.5.6, still produced a correct config: no locale/TZ keys, `humanize` set, HTTPS AE pinned, `vision` capability.

### [/] Task 4.1: `version.py`: detect the Firefox major from the binary

**Files:** Create `mcp_launcher/__init__.py` (empty), `mcp_launcher/version.py`, `mcp_launcher/tests/__init__.py` (empty), `mcp_launcher/tests/test_version.py`

**Interfaces:**
- Produces: `detect_firefox_major(executable_path: str | None, default: int = DEFAULT_FIREFOX_MAJOR) -> int` and the constant `DEFAULT_FIREFOX_MAJOR = 152`.

- [X] **Step 1: Failing tests**
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
- [X] **Step 2: Run the tests. Expected: `ModuleNotFoundError`.**
- [X] **Step 3: Implement**
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
- [X] **Step 4: Run the tests. Expected: 5 passed.**
- [ ] **Step 5: Verify against the real build**: `.venv/bin/python -c "from mcp_launcher.version import detect_firefox_major as d; print(d('$B152'))"`. Expected `152`. The same call on `$B146` returns `146` (validated). The bundle's `application.ini` layout (`[App]` … `Version=<full>`, plus `[Gecko] MinVersion=`/`MaxVersion=`, which the `^Version=` anchor ignores) was checked against the 146 build.
- [X] **Step 6: Commit** `git add mcp_launcher && git commit -m "mcp_launcher: detect Firefox major from application.ini"`

### [X] Task 4.2: `user_agent.py`, `profile.py`, `bundle.py`: extract the existing logic unchanged

**Files:** Create `mcp_launcher/user_agent.py`, `mcp_launcher/profile.py`, `mcp_launcher/bundle.py` and tests `mcp_launcher/tests/test_user_agent.py`, `test_profile.py`, `test_bundle.py`.

**Interfaces:**
- `clean_user_agent(ff_major: int) -> str`. `CLEAN_APP_VERSION = "5.0 (Macintosh)"`.
- `needs_ua_refresh(saved_ua: str, ff_major: int) -> bool`. True if the UA contains "Camoufox", has no `rv:`, or `rv` ≠ `ff_major`.
- `scrub_stale_prefs(prefs_path: str, pinning_locale: bool) -> int`. Returns the number of removed lines, using the same pref list as today: `intl.accept_languages`, `intl.locale.requested`, `general.useragent.locale`.
- `install_properties_shim() -> None`. Idempotent. Wraps `camoufox.utils._load_properties` with the `Contents/MacOS → Contents/Resources/properties.json` redirect.

- [X] **Step 1: Failing tests**
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
- [X] **Step 2: Run them. Expected: import errors.**
- [X] **Step 3: Implement** by moving the code **verbatim** from `launch-camoufox-mcp.py` (`_CLEAN_UA` block, prefs.js strip block, `_load_properties_macos_bundle`) into these functions. The shim must capture the original `cu._load_properties` **inside `install_properties_shim()`**, not at module import, otherwise `test_redirects_macos_bundle_to_resources` (which monkeypatches it first) cannot observe the redirect. It marks the wrapper with `_camoufox_mcp_shim = True` and returns early if `cu._load_properties` already carries that marker. `scrub_stale_prefs` keeps today's semantics: only the three locale prefs are ever stripped (the current code computes `stripping_tz` but never uses it), and a missing file or `OSError` returns 0.
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
- [X] **Step 4: Run the tests. Expected: all pass.**
- [X] **Step 5: Commit** `git commit -m "mcp_launcher: extract UA, prefs scrubber and macOS bundle shim"`

### [/] Task 4.3: `mcp_config.py` + thin CLI + `--mcp-package`

**Files:** Create `mcp_launcher/mcp_config.py`, `mcp_launcher/tests/test_mcp_config.py`. Modify `launch-camoufox-mcp.py`.

**Interfaces:**
- `build_mcp_config(*, executable_path: str, headless: bool, firefox_user_prefs: dict, env: dict, user_agent: str, user_data_dir: str | None, window_size: tuple[int, int] | None) -> dict`. Returns exactly today's structure: `browser.browserName="firefox"`, `launchOptions{executablePath, headless, firefoxUserPrefs, env}`, optional `userDataDir`, `contextOptions{colorScheme:"dark", userAgent, extraHTTPHeaders{"user-agent"}, viewport?}`, `capabilities=["core","pdf","vision"]`.
- `DEFAULT_MCP_PACKAGE = "@playwright/mcp@0.0.68"`.
- `build_mcp_args(config_file: str, window_size: tuple[int, int] | None, mcp_package: str = DEFAULT_MCP_PACKAGE) -> list[str]`. Returns `["npx", mcp_package, "--config", config_file]`, plus `["--viewport-size", "WxH"]` in headed mode. Today this list is built inline at the end of `main()`, and the headed-mode fix depends on it.
- `write_mcp_config(path: str, config: dict) -> None`. Writes JSON with mode **`0600`** (`os.open(path, O_WRONLY|O_CREAT|O_TRUNC, 0o600)` + `os.fchmod(fd, 0o600)`, so an existing 0644 file is tightened too). This addresses §1.3-6: the file contains the full host environment.

- [X] **Step 1: Failing tests**
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
  ```
- [X] **Step 2: Run. Expected: import error.**
- [X] **Step 3: Implement `build_mcp_config`, `build_mcp_args`, `write_mcp_config`** by moving the dict-building and arg-building code verbatim from the current `main()`. Keep `FINGERPRINT_CONFIG_PATH` (`~/.camoufox-mcp-fingerprint.json`) and the load/save and strip-on-save logic in the CLI, unchanged.
- [X] **Step 4: Rewrite `launch-camoufox-mcp.py` as wiring only.** Keep **every existing flag with the same name, default and help**, and add:
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
  #   run the launcher with PATH pointing `npx` at a stub that just exits 0.
  # The launcher REWRITES ~/.camoufox-mcp-fingerprint.json (the live MCP identity): snapshot + restore it.
  cp -p ~/.camoufox-mcp-fingerprint.json /tmp/fp.golden.bak
  mkdir -p /tmp/stubbin && printf '#!/bin/sh\nexit 0\n' > /tmp/stubbin/npx && chmod +x /tmp/stubbin/npx
  PATH=/tmp/stubbin:$PATH .venv/bin/python launch-camoufox-mcp.py --user-data-dir /tmp/p --executable-path "$B152" --humanize
  .venv/bin/python - <<'EOF'
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
  p = os.path.join(tempfile.gettempdir(), "camoufox-mcp-config.json")
  assert oct(os.stat(p).st_mode & 0o777) == "0o600", "config holds host env incl. secrets"
  print("launcher config OK")
  EOF
  cp -p /tmp/fp.golden.bak ~/.camoufox-mcp-fingerprint.json   # restore the live identity
  diff <(COLUMNS=100 .venv/bin/python launch-camoufox-mcp.py --help) /tmp/launcher-help-146.txt   # only the new --mcp-package lines differ
  ```
  Everything except the `0600` assertion was validated against the current launcher on merged 0.5.6, which printed `rv:146.0` because the 146 binary was used. The first run in a fresh `HOME` downloads the default uBO addon, so it needs network access.
- [X] **Step 6: Run all launcher tests**: `.venv/bin/python -m pytest mcp_launcher/tests -q`. Expected: all pass.
- [X] **Step 7: Commit** `git commit -am "mcp_launcher: thin CLI over tested modules; auto-detect Firefox 152; --mcp-package; fail loudly without fingerprint"`

### [ ] Task 4.4: Decide the `@playwright/mcp` version

Compatibility matrix (from `npm view @playwright/mcp@<v> dependencies.playwright-core`, plus upstream `PLAYWRIGHT_BROWSER_FLOORS`):

| `@playwright/mcp` | bundled Playwright | Needs Camoufox ≥ | Within pythonlib ceiling `<1.63` |
|---|---|---|---|
| 0.0.68 (current pin) – 0.0.69 | 1.59.0-alpha | any | yes |
| 0.0.70 – 0.0.74 | 1.60.0-alpha | any (upstream measured 1.60 OK on beta.29/30) | yes |
| 0.0.75 – 0.0.76 | 1.61.0-alpha | **beta.30** | yes |
| 0.0.77 – 0.0.78 | 1.62.0-alpha | **beta.30** | yes |
| 0.0.79 – 0.0.82 | 1.63/1.64-alpha | beta.30+ (untested upstream) | **no, avoid** |

Table re-verified with `npm view` on 2026-09-21. The latest release is `0.0.82`, and the latest `playwright-core` is `1.63.0`. `PLAYWRIGHT_BROWSER_FLOORS` is enforced only by pythonlib and never by the MCP server's bundled Playwright. The `< 1.63` limit for MCP is a "tested Juggler protocol" limit, not something anything enforces.

- [ ] **Step 1:** Run the Phase 5.2 MCP smoke test with the default `0.0.68`.
- [ ] **Step 2:** If Juggler protocol errors show up (e.g. `Protocol error (Browser.setDefaultViewport)`, or unknown-method errors because the FF152 Juggler is newer than the 1.59 client), retry with `--mcp-package @playwright/mcp@0.0.78`, the newest version that stays inside the tested ceiling.
- [ ] **Step 3:** Pin the version that passes in `DEFAULT_MCP_PACKAGE` and in the test, and record the result in this table.

---

## Phase 5 — Verification (all branch features + upstream suites)

**Deliverable:** evidence (command output) that every item in §1.2 works on the 152 build, plus green upstream test suites.

### [ ] Task 5.1: Upstream automated suites

- [ ] **Step 1: pythonlib unit tests**: `cd pythonlib && ../.venv/bin/python -m pytest tests -q`. Expected: `178 passed, 4 skipped` (Playwright 1.58).
- [ ] **Step 2: Install pythonlib into the MCP runtime venv.** The repo `.venv` was done in Task 0.3. `claude-1/.venv` currently has **camoufox 0.4.11** and ships only `pip3`, so use `uv` or `-m pip`:
  ```bash
  uv pip install --python /Users/dev345/code/kfirfer/claude-1/.venv/bin/python3 -e /Users/dev345/code/kfirfer/camoufox/pythonlib "playwright<1.63"
  /Users/dev345/code/kfirfer/claude-1/.venv/bin/python3 -c "import camoufox, importlib.metadata as m; print(camoufox.__file__, m.version('camoufox'))"   # .../camoufox/pythonlib/... 0.5.6
  ```
  Both venvs have Playwright **1.58**, which is below the 1.61 floor, so no browser-floor issue. Do not upgrade them past `<1.63`. This changes another project's venv (`claude-1`). Tell the user, and note the rollback in §8.
- [ ] **Step 3: build-tester against the new binary** (`run_tests.sh` creates its own venv and runs `npm install`; the README example passes the `.app`)
  ```bash
  bash build-tester/run_tests.sh "$APP" --no-cert
  ```
- [ ] **Step 4: Playwright tests**: run `(cd tests && bash run-tests.sh --executable-path "$B152")`. Do not use `make tests`: its path is hard-coded to `obj-x86_64-pc-linux-gnu/dist/bin/camoufox-bin`, and the Makefile must stay clean. Record pass/fail counts, and compare failures with a run of the pristine upstream beta.30 binary before attributing them to the branch.
- [ ] **Step 5 (optional; needs `service-tester/proxies.txt`): service-tester**, the second suite upstream's `CLAUDE.md` marks as required for PRs. It builds a wheel from `pythonlib/`, auto-detects the local macOS build, and copies `properties.json` itself: `(cd service-tester && ./run_tests.sh --binary local)`. Skip it and say so if no proxies are available.

### [ ] Task 5.2: MCP end-to-end (F7, F8)

- [ ] **Step 1: Re-register the MCP server with the 152 binary** (update `MISC.md` accordingly)
  ```bash
  claude mcp remove playwright -s local
  claude mcp add playwright -- /Users/dev345/code/kfirfer/claude-1/.venv/bin/python3 \
    /Users/dev345/code/kfirfer/camoufox/launch-camoufox-mcp.py \
    --user-data-dir /Users/dev345/playwright-profile/profile-claude-camoufox \
    --executable-path "/Users/dev345/code/kfirfer/camoufox/camoufox-152.0.4-beta.30/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/MacOS/camoufox" \
    --no-headless --humanize --showcursor
  ```
  Keep the **exact flags of the current registration**. As of 2026-09-21 that is `--no-headless --humanize --showcursor` (see `/tmp/mcp-registration-146.txt` from Task 0.1), local scope, registered for this repo directory. Only the binary path changes. Run `claude mcp remove`/`add` from `/Users/dev345/code/kfirfer/camoufox`, because local scope is per-directory.
  Back up the persistent profile first (`cp -a ~/playwright-profile/profile-claude-camoufox{,.bak-146}`). Firefox 146 → 152 migrates the profile forward, and there is no way back.
- [ ] **Step 2:** Run all of `STEALTH_TEST_MATRIX.md` with the expectations updated to 152 (Task 6.1). Minimum pass criteria:
  - `navigator.userAgent` = `… rv:152.0) Gecko/20100101 Firefox/152.0`, the same in Worker and ServiceWorker. HTTP `User-Agent` matches.
  - HTTPS `Accept-Encoding: gzip, deflate, br, zstd` (F3). br/zstd pages render (no mojibake).
  - With no `--timezone`: host TZ everywhere. With `--timezone Asia/Singapore`: Window = Worker = **ServiceWorker** = `Asia/Singapore` (F4).
  - Persistent profile: log in to a site, restart the MCP server, and confirm you are still logged in (F1/F7).
  - No "Camoufox" string in UA, `about:` dialogs or error pages (F6).
- [ ] **Step 3: Headed mode**: repeat with `--no-headless --showcursor`. Check that the viewport equals the window size and nothing flickers.

### [ ] Task 5.3: `launch_server` persistent profile against a live browser (F1)

Prerequisite: Task 3.3 Step 3 (`properties.json` mirrored into `Contents/MacOS/`). Without it `launch_options()` raises `FileNotFoundError` (§1.3-5).

This procedure was validated end to end against the 146 binary with merged pythonlib, Playwright 1.58 and `_sharedBrowser`: `contexts on connect: 1`, and cookie plus localStorage survived the restart. **Without** `_sharedBrowser`, the same run gives `contexts on connect: 0`, `b.contexts[0]` raises `IndexError`, and data written through `new_context()` is gone after the restart.

- [ ] **Step 1: Server + client scripts.** The heredoc is unquoted, so `$B152` is expanded. The old `<<'EOF'` form passed the literal string `$B152` to Python.
  ```bash
  rm -rf /tmp/cf-ws-profile
  cat > /tmp/cf-srv.py <<EOF
  from camoufox.server import launch_server
  launch_server(executable_path="$B152", ff_version=152, os="macos", i_know_what_im_doing=True,
                persistent_context=True, user_data_dir="/tmp/cf-ws-profile", headless=True)
  EOF
  cat > /tmp/cf-cli.py <<'EOF'
  import sys
  from playwright.sync_api import sync_playwright
  with sync_playwright() as p:
      b = p.firefox.connect(sys.argv[1])
      assert len(b.contexts) == 1, f"persistent context not shared: {len(b.contexts)} (missing _sharedBrowser?)"
      ctx = b.contexts[0]; pg = ctx.pages[0] if ctx.pages else ctx.new_page()
      pg.goto("https://example.com")
      if sys.argv[2] == "set":
          pg.evaluate("() => { document.cookie = 'k=v; max-age=999999; path=/'; localStorage.setItem('k','v'); }")
      print(sys.argv[2], pg.evaluate("document.cookie"), pg.evaluate("localStorage.getItem('k')"))
  EOF
  ```
- [ ] **Step 2: Round trip across a server restart**
  ```bash
  run() { (.venv/bin/python -u /tmp/cf-srv.py > /tmp/cf-srv.log 2>&1 &)
          for i in $(seq 30); do grep -q ws:// /tmp/cf-srv.log && break; sleep 1; done
          .venv/bin/python /tmp/cf-cli.py "$(grep -o 'ws://[^ ]*' /tmp/cf-srv.log | head -1)" $1
          pkill -INT -f /tmp/cf-srv.py; sleep 5; pkill -f 'camoufox/launchServer.js'; }
  run set    # expect: set k=v v
  run read   # expect: read k=v v   <- persisted across a server restart
  ```
  Stopping the Python parent closes Node's stdin, and Node then closes the browser cleanly. The trailing `pkill` is only a safety net.

### [ ] Task 5.4: Humanize (F2)

- [ ] **Step 1:** Make `test_humanize.py` take the binary from `CAMOUFOX_BINARY` (defaulting to the 152 path), then run `CAMOUFOX_BINARY="$B152" .venv/bin/python test_humanize.py`. Expected: 15/15 clicks, the cursor visibly follows curved paths, and **no hang**. The `>=` guard is exercised whenever a button spawns at the viewport edge. The script launches headed and loads `https://camoufox.com/tests/buttonclick`, so it needs a display and network access. It uses raw Playwright rather than pythonlib, so §1.3-5 does not affect it. Upstream's `pythonlib/tests/test_humanize.py` is a different file and runs in Task 5.1 Step 1.

### [ ] Task 5.5: Regression greps (F6 + no stale 146)

- [ ] **Step 1**
  ```bash
  APP=camoufox-152.0.4-beta.30/obj-aarch64-apple-darwin/dist/Camoufox.app
  R=$APP/Contents/Resources
  # dist/ files are SYMLINKS into the source tree and BSD `grep -r` does not follow them,
  # so the old `grep -rl …` always printed 0 whatever the content (verified on the 146 build).
  # -S follows symlinks. Also assert positively that the brand name is "Firefox".
  grep -rlS "Camoufox can\|Camoufox is configured\|Camoufox doesn" $R/browser $R/chrome 2>/dev/null | wc -l   # expect 0
  grep -S -- "-brand-short-name = Firefox" $R/browser/localization/en-US/branding/brand.ftl                # expect 1 line
  grep -cS "Camoufox" $R/browser/localization/en-US/branding/brand.ftl $R/browser/chrome/en-US/locale/browser/appstrings.properties  # expect :0 for both
  git grep -n "146" -- launch-camoufox-mcp.py mcp_launcher test_humanize.py   # expect 0 functional hits
  ```

### [ ] Task 5.6: Re-evaluate `CAMOUFOX_FEEDBACK.md`

- [ ] **Step 1:** Re-run the six-site battery from `CAMOUFOX_FEEDBACK.md`: demo.fingerprint.com (P1.1, possibly fixed by `debugger-invisible-to-content.patch`), bot.incolumitas.com (P1.2), fingerprint-scan.com (P1.3), browserleaks webgl (P1.4), CreepJS (P2.1), and the Cloudflare smoke test. Record the per-item status in that file (`fixed in 152` / `still open`). Do not start fixing open items here, because they are out of scope for the upgrade.

---

## Phase 6 — Documentation, Cleanup & Delivery

**Deliverable:** docs match the 152 reality, the branch is pushed, and the rollback path is documented.

### [ ] Task 6.1: Update branch docs

- [ ] `STEALTH_TEST_MATRIX.md`: replace the 33 occurrences of `146` (paths `camoufox-146.0.1-beta.25` → `camoufox-152.0.4-beta.30`, UA `rv:146.0`/`Firefox/146.0` → `152`). Update the TLS/JA4 "Known caveat" (around line 195) and its repeat (around line 893). **152 is still long-tail**, not current: mainline Firefox is 156.0 as of 2026-09-21, four majors ahead. Say so instead of claiming parity. Replace **both** F4 greps (around lines 136 and 842). They look for `MaskConfig::GetString("timezone")` in `WorkerPrivate.cpp`, which is **absent after the merge** because that fallback now lives in `dom/base/TimezoneManager.cpp`. Use `grep -q "ucid != 0" …/dom/workers/WorkerPrivate.cpp && grep -q 'MaskConfig::GetString("timezone")' …/dom/base/TimezoneManager.cpp`.
- [ ] `STEALTH_TEST_MATRIX.md` persistence check: in F1 checks that go through `launch_server`, clients must use `browser.contexts[0]`, not `new_context()` (§1.3-2).
- [ ] `MISC.md`: new binary path and the `claude mcp add` lines, keeping `--no-headless --humanize --showcursor` (3 occurrences of `146` today).
- [ ] `CAMOUFOX_FEEDBACK.md`: the 4 occurrences of `146`, plus the Task 5.6 status column.
- [ ] `test_humanize.py`: `CAMOUFOX_BINARY` env var (Task 5.4).
- [ ] Mark each task in this plan `[X]` and fill in the §4.4 table.
- [ ] Commit: `git commit -am "docs: update stealth matrix, MISC and helpers for Firefox 152.0.4 / Camoufox beta.30"`

### [ ] Task 6.2: Final review & push

- [ ] **Step 1:** `git log --oneline pre-ff152-upgrade..HEAD` shows one merge commit plus focused follow-up commits.
- [ ] **Step 2:** `git diff v152.0.4-beta.30 -- patches additions pythonlib` shows **only** the intended branch deltas: branding (5 files under `additions/browser/`), `PageHandler.js` **identical to upstream** (no diff), the HTTPS-only encoding override, the ucid→0 fallback, `server.py` (rejection removed + `_shared_browser`), `utils.py`/`sync_api.py`/`async_api.py` user-data-dir handling, and `test_user_data_dir.py`. Anything else is an accidental regression of upstream and must be reverted.
- [ ] **Step 3:** Request a code review (superpowers:requesting-code-review), then `git push origin fix-user-data`.
- [ ] **Step 4:** Remove the old tree **only after sign-off**: `rm -rf camoufox-146.0.1-beta.25 firefox-146.0.1.source.tar.xz` (both are gitignored, about 30 GB).

---

## 7. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| `@playwright/mcp@0.0.68` (PW 1.59) client is incompatible with the FF152 Juggler | Medium | MCP unusable | Task 4.4 fallback to 0.0.78 (PW 1.62, officially floor-compatible with beta.30) |
| Upstream later removes the `_userDataDir` or `_sharedBrowser` path from Playwright (both private) | Low (both present 1.58 → 1.62) | F1 `launch_server` breaks: the profile would be launched but unreachable | `test_launch_server_forwards_user_data_dir` asserts both keys; the `<1.63` ceiling blocks untested upgrades. Before lifting it, grep the new `playwright-core` for `_sharedBrowser` and `launchServerShared`. If they are gone, fall back to upstream's rejection. |
| The `Firefox/152.0` UA is 4 majors behind mainline (156.0) | Certain | Long-tail UA/TLS signal for ML detectors | Not fixable here: no Camoufox build exists for 153–156. Documented in Task 6.1; track the next upstream rebase (§9). |
| The live MCP identity file `~/.camoufox-mcp-fingerprint.json` is rewritten by every launcher run | Certain | Accidental identity change during testing | Backup in Task 0.1; snapshot and restore around the Task 4.3 golden check |
| Regenerated patch drifts from the intended hunk | Low | F3/F4 silently lost | Task 3.4 greps against the compiled source tree + STEALTH_TEST_MATRIX runtime checks |
| Persistent profile migration 146 → 152 is one-way | Certain | Cannot downgrade the profile | Backup in Task 5.2 Step 1 |
| Native macOS build diverges from upstream's Linux cross-compile (upstream `CLAUDE.md`: macOS is "never built natively") | Medium | Build errors | This host already built 146 natively (`obj-aarch64-apple-darwin`). Host SDK 27.0 ≥ 26.4. `assets/macos.mozconfig` only forces `--with-macos-sdk` on non-Darwin hosts. Fallback: `docker build` + `multibuild.py --target macos --arch arm64` (uses `make setup-macos-sdk`) |
| Hidden upstream behaviour changes (WebRTC prefs, `privacy.partition.network_state=true`, `fission.webContentIsolationStrategy=0`) change stealth results | Medium | Matrix diffs | Task 5.2/5.6 compare against the baseline from Task 0.1 Step 3. These are upstream-intended changes, so keep them |

## 8. Rollback

```bash
git switch fix-user-data
git reset --hard pre-ff152-upgrade          # local only; if already pushed use: git revert -m 1 <merge-sha>
# restore MCP to the 146 binary (MISC.md lines at the pre-ff152-upgrade tag) and the profile backup:
rm -rf ~/playwright-profile/profile-claude-camoufox && cp -a ~/playwright-profile/profile-claude-camoufox{.bak-146,}
cp -p ~/.camoufox-mcp-fingerprint.json.bak-146 ~/.camoufox-mcp-fingerprint.json
# claude-1 venv had camoufox 0.4.11 from PyPI before Task 5.1 Step 2:
uv pip install --python /Users/dev345/code/kfirfer/claude-1/.venv/bin/python3 "camoufox==0.4.11"
```

## 9. Follow-ups (out of scope)

- [ ] Evaluate **`v152.0.4-beta.31`**. As of 2026-09-21 it is a git tag with **no published GitHub release**, which is why it is not the target. It contains the mouse boundary-row dispatch fix (#751/#752, adjacent to F2), a Juggler refactor that "makes the input-dispatch deadlock structurally unreachable" (touches the same `PageHandler.js` region as F2), sealing of fingerprint setters (#749), `media:spoof_codecs`, `navigator.maxTouchPoints` spoofing, font-allowlist changes, and removal of stale `.bak` files. Merge it the same way (tag merge + the §2 decisions) once beta.30 is signed off, or sooner if it gets published.
- [ ] Watch for an upstream rebase onto Firefox 153+ (mainline is 156.0). That is the only way to close the long-tail UA gap in §7.
- [ ] Propose the `_load_properties()` macOS-bundle fallback (§1.3-5) upstream. That would let both the F8 shim and the Task 3.3 copy step go away.
- [ ] Propose the HTTPS-only Accept-Encoding and ucid→0 timezone fixes upstream (PRs against `daijro/camoufox`), which would shrink the branch delta for future upgrades.
- [ ] Open or maintain the remaining `CAMOUFOX_FEEDBACK.md` items that Task 5.6 finds still open.

## 10. Sources consulted

- Upstream repo, tags and diffs: `github.com/daijro/camoufox`, tags `v152.0.4-beta.26 … beta.31`, compare `d6540b5…v152.0.4-beta.30` (175 commits, 143 files). Trial merge via `git merge-tree`.
- Release notes (via Exa): `github.com/daijro/camoufox/releases`. beta.28 (#679/#680 humanize restored + edge guard), beta.30 (#743 Playwright 1.61, #745, #746 browser floor, #747 rustc 1.98).
- Firefox 152 sources: `mozilla-firefox/firefox@FIREFOX_152_0_4_RELEASE`, `modules/libpref/init/all.js` (Accept-Encoding defaults) and `build/moz.configure/toolchain.configure` (macOS SDK ≥ 26.4). Source tarball `archive.mozilla.org/pub/firefox/releases/152.0.4/source/` (HTTP 200).
- MDN Firefox 147/152 release notes (via Perplexity): no web-platform changes in 147; 152 adds WASM JSPI, text-module imports, and `MediaCapabilities` WebRTC type.
- Playwright: `playwright-core` 1.58.0 (`lib/browserServerImpl.js`, `server/dispatchers/{playwright,browser}Dispatcher.js`), and 1.59.0/1.60.0/1.61.0/1.62.0 (`lib/coreBundle.js`, via `npm pack`), confirm `options._userDataDir` → `launchPersistentContext`, `options._sharedBrowser` → mode `launchServerShared` → `isolateContexts: false`. Latest `playwright-core` is 1.63.0, bundling Firefox 153.0 (rev 1538). `@playwright/mcp` 0.0.68 → 0.0.82 dependency map from the npm registry.
- Mozilla product-details (`firefox_versions.json`): `LATEST_FIREFOX_VERSION = 156.0`, `FIREFOX_ESR = 140.16.0esr` (2026-09-21). GitHub releases API for `daijro/camoufox`: newest published release is `v152.0.4-beta.30` (2026-09-01).

## 11. Validation record (2026-09-21)

Everything below was **executed**, not inferred. Scratch artefacts lived in the session scratchpad and have been removed. The repo tree was left clean.

| Check | How | Result |
|---|---|---|
| Tag, ancestry, size | `git rev-parse`, `merge-base --is-ancestor`, `rev-list --count`, `diff --stat` | `5d06ec1…` ✔, `main` is ancestor ✔, 175 commits ✔, 143 files ✔ |
| Conflict set | `git merge-tree --write-tree --name-only` + a real `git merge --no-commit` in a scratch worktree | exactly the 3 files in §2 ✔. `voice-spoofing.patch`, `launchServer.js`, `server.py` merge to upstream byte-for-byte |
| PageHandler | upstream beta.30 source | trajectory, `_lastTrackedPos`, `>=` guard, exact-destination finish, zero-move skip. Superset of F2 ✔ |
| Patch resolutions | applied the full 53-patch stack in `patch.py` order to the 208 `FIREFOX_152_0_4_RELEASE` files it touches, branch-resolved vs pristine upstream | identical result sets. network/timezone apply with **no offset/fuzz**. F2–F5 markers present ✔. **Plan's original `+6431,19` header → `malformed patch`** ✘ (fixed in Task 1.1 Step 4) |
| FF152 facts | raw sources at `FIREFOX_152_0_4_RELEASE` | version 152.0.4, AE prefs as in §Global, `mac_sdk_min_version() == "26.4"`, tarball HTTP 200 ✔ |
| §1.3-1 TypeError | branch pythonlib + 146 binary | reproduced ✔ |
| Phase 2 code | merged pythonlib in a scratch venv (Py 3.13, Playwright 1.58) | upstream 168 pass. Plan tests pass alone but **one failed in the full suite** (path bug, fixed). **Plan's `NewBrowser` rewrite dropped the #666 `no_viewport` default** (fixed). Corrected version: 177 passed, 4 skipped |
| In-process F1 | `Camoufox(persistent_context=True, user_data_dir=…)` on the 146 binary, two runs | cookie + localStorage persisted ✔ (needs §1.3-5 workaround) |
| `launch_server` F1 | live server + `firefox.connect()` round trip across a restart | as originally planned: 0 contexts, data **lost** ✘. With `_sharedBrowser`: 1 context, data **persisted** ✔ |
| Phase 4 code | Task 4.1/4.2 tests + impl extracted verbatim | 15 passed ✔. `detect_firefox_major($B146) == 146` ✔ |
| Launcher on 0.5.6 | current launcher, stub `npx`, sandboxed `HOME`/`TMPDIR` | config correct (no locale/TZ, humanize, AE, vision) ✔. Host env incl. secrets written to the config file (§1.3-6) |
| Branding grep | 146 build | original `grep -r` is vacuous (symlinks) ✘. `grep -S` version works ✔ |
| Environments | `.venv`, `claude-1/.venv`, `claude mcp get playwright` | no pip/pytest in `.venv`. `claude-1` has camoufox 0.4.11 and `pip3` only. Registration uses `--no-headless --humanize --showcursor` |

Not executed here (these need the ~40 min FF152 build or live sites): Tasks 3.1–3.3 on the full tree, 5.1 Steps 3–5, 5.2, 5.4 and 5.6.
