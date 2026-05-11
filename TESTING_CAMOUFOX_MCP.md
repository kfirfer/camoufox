# Testing the Camoufox MCP setup in another Claude Code session

This document walks a fresh Claude Code session through verifying that:

1. The `launch-camoufox-mcp.py` launcher correctly propagates fingerprint config (humanize, showcursor, UA) to the spawned Camoufox binary.
2. The Camoufox build's `Camoufox`-branded error strings have been replaced with `Firefox` (so chrome-console errors no longer reveal the project name).
3. The browser passes standard bot-detection probes.

Run all steps from a new Claude Code session in this repo. Steps that need a human shell prompt are prefixed with `! ` (which Claude Code runs in your terminal).

---

## 0. Prerequisites

- macOS Apple Silicon (paths below are hardcoded to `obj-aarch64-apple-darwin/`).
- A built Camoufox at `camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/MacOS/camoufox`. The build was produced with `./mach build` in `camoufox-146.0.1-beta.25/`.
- Python venv with `playwright` and `camoufox` installed at `/Users/dev345/code/kfirfer/claude-1/.venv/`.
- Profile dir for persistent sessions: `/Users/dev345/playwright-profile/profile-claude-camoufox/`.

If any path differs on your machine, substitute it consistently everywhere below.

---

## 1. Add the Playwright MCP server (uses our launcher)

In the Claude Code session, ask the user to run this in a shell:

```bash
claude mcp add playwright -- \
  /Users/dev345/code/kfirfer/claude-1/.venv/bin/python3 \
  /Users/dev345/code/kfirfer/camoufox/launch-camoufox-mcp.py \
  --user-data-dir /Users/dev345/playwright-profile/profile-claude-camoufox \
  --executable-path "/Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/MacOS/camoufox" \
  --no-headless \
  --humanize \
  --showcursor
```

After adding the server, restart Claude Code or reconnect the MCP server so the new launcher process is spawned with the current `launch-camoufox-mcp.py` code.

---

## 2. Verify the launcher produces the expected CAMOU_CONFIG env vars (no browser launch)

This catches regressions in the launcher (e.g. the bug where `launch_options()` failed on macOS app-bundle paths and silently dropped all spoofing config).

```bash
/Users/dev345/code/kfirfer/claude-1/.venv/bin/python3 -c "
import sys, os, json
sys.argv = ['launch-camoufox-mcp.py',
            '--user-data-dir', '/tmp/test-profile-verify',
            '--executable-path', '/Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/MacOS/camoufox',
            '--no-headless', '--humanize', '--showcursor']

def fake_exec(prog, args, env):
    parts = []
    i = 1
    while True:
        k = f'CAMOU_CONFIG_{i}'
        if k not in env: break
        parts.append(env[k]); i += 1
    if not parts:
        print('FAIL: no CAMOU_CONFIG env vars set'); sys.exit(1)
    cfg = json.loads(''.join(parts))
    assert cfg.get('humanize') is True, f'humanize missing: {cfg.get(\"humanize\")}'
    assert cfg.get('showcursor') is True, f'showcursor missing: {cfg.get(\"showcursor\")}'
    assert 'Firefox/146.0' in cfg.get('navigator.userAgent',''), f'UA wrong: {cfg.get(\"navigator.userAgent\")}'
    assert 'Camoufox' not in cfg.get('navigator.userAgent',''), 'UA leaks Camoufox brand'
    print('OK', len(cfg), 'config keys, humanize+showcursor present, UA clean')
    sys.exit(0)
os.execvpe = fake_exec
exec(open('/Users/dev345/code/kfirfer/camoufox/launch-camoufox-mcp.py').read())
"
```

Expected output (something like):

```
OK 43 config keys, humanize+showcursor present, UA clean
```

If you get `FAIL: no CAMOU_CONFIG env vars set`, the launcher is dropping config — most likely the macOS `properties.json` lookup workaround is missing or broken. See `launch-camoufox-mcp.py` `_load_properties_macos_bundle`.

---

## 3. Verify the Firefox/Camoufox rebrand is live in the built binary

Confirms the chrome resources point at the edited source files and no packaged `omni.ja` could override them.

```bash
echo "=== chrome-resource appstrings.properties symlink ==="
ls -la /Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/Resources/browser/chrome/en-US/locale/browser/appstrings.properties

echo ""
echo "=== Strings the binary will read ==="
grep -E "Camoufox|Firefox" /Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/Resources/browser/chrome/en-US/locale/browser/appstrings.properties | head -3

echo ""
echo "=== Any packaged omni.ja or .jar that could override? ==="
find /Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app \( -name "omni.ja" -o -name "*.jar" \)
echo "(empty above = dev build uses symlinks, no archive override)"

echo ""
echo "=== Residual 'Camoufox' strings inside the bundle ==="
grep -rln "Camoufox" /Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/Resources/browser/chrome/ /Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/Resources/browser/localization/ 2>/dev/null
echo "(empty above = no Camoufox leaks in browser chrome/localization)"
```

Expected:

- The `appstrings.properties` path is a symlink to `…/browser/locales/en-US/chrome/overrides/appstrings.properties`.
- Grep shows lines like `connectionFailure=Firefox can’t establish a connection…` (no `Camoufox`).
- `find … omni.ja` is empty.
- Residual grep returns nothing.

Three remaining `Camoufox` strings elsewhere in the bundle are expected and chrome-only (not web-reachable):

- `Contents/Resources/en.lproj/InfoPlist.strings` — macOS Dock/Finder display name
- `Contents/Resources/chrome/toolkit/content/global/buildconfig.html` — `about:buildconfig` page
- `Contents/Resources/modules/AppConstants.sys.mjs` — privileged chrome JS constants

These are tied to `MOZ_APP_DISPLAYNAME=Camoufox` in `additions/browser/branding/camoufox/configure.sh` and were kept intentionally so the bundle is identifiable on disk.

---

## 4. Live runtime check — UA, fingerprint, no Camoufox brand

In the Claude Code session, navigate to `about:blank` via the Playwright MCP, then evaluate:

```js
({
  userAgent: navigator.userAgent,
  appVersion: navigator.appVersion,
  oscpu: navigator.oscpu,
  platform: navigator.platform,
  hardwareConcurrency: navigator.hardwareConcurrency,
  hasCamoufoxInUA: navigator.userAgent.includes('Camoufox'),
})
```

Expected:

```json
{
  "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:146.0) Gecko/20100101 Firefox/146.0",
  "appVersion": "5.0 (Macintosh)",
  "oscpu": "Intel Mac OS X 10.15",
  "platform": "MacIntel",
  "hardwareConcurrency": 4,
  "hasCamoufoxInUA": false
}
```

Critical: `hardwareConcurrency` must match the value WorkerNavigator reports (4 on M-series Macs because `hw.perflevel1.logicalcpu = 4`). A main↔worker mismatch is what `deviceandbrowserinfo.com`'s `hasInconsistentWorkerValues` check fires on.

---

## 5. Verify the chrome-console error path no longer says `Camoufox`

Trigger the same failure paths that previously surfaced `Camoufox can't establish…`:

```js
async () => {
  try { new WebSocket('wss://nonexistent-host-rebuild-check.invalid/'); } catch(_) {}
  try { new EventSource('https://nonexistent-host-rebuild-check.invalid/'); } catch(_) {}
  await new Promise(r => setTimeout(r, 2500));
  return 'errors triggered';
}
```

In the MCP console output you should see lines like:

```
[ERROR] [JavaScript Error: "Firefox can't establis..." line: N] @ debugger eval code …
```

The string `"Firefox can't establis..."` is the proof. If you ever see `"Camoufox can't establi..."` here, the rebrand step in §3 didn't take effect (the binary is running a stale build or a different .app).

---

## 6. Bot-detection test sites

Drive the MCP browser to each URL below, wait a few seconds for results to populate, and check the values listed.

### a) bot.sannysoft.com

`https://bot.sannysoft.com/`

Expected:
- Intoli table: every row `passed` (Chrome row will say "missing (failed)" — irrelevant, we're Firefox).
- User Agent row shows pure Firefox 146 string, no `Camoufox`.
- Fingerprint Scanner table: all rows `ok`.

### b) CreepJS

`https://abrahamjuliot.github.io/creepjs/`

Wait ~10s for hashing to complete. Expected:
- **Headless section**: `0% like headless`, `0% headless`, `0% stealth`, `chromium: false`.
- **Worker section**: `userAgent` matches main-thread UA, `cores: 4`, `Mac (MacIntel)`.
- **Navigator section**: `ua parsed: Firefox 146`, `appVersion: 5.0 (Macintosh)`, `webdriver: false`.
- Footer shows ✔ for `loose fingerprint passed` and `stable fingerprint passed`.

### c) browserscan.net bot detection

`https://www.browserscan.net/bot-detection`

Wait ~5s. Expected top verdict: **Normal**. Every individual framework check (WebDriver, Selenium, PhantomJS, Headless Chrome, CDP, Dev Tool, etc.) should also read `Normal`.

Also check `https://www.browserscan.net/` — headline reads `Browser fingerprint authenticity: 100%`.

---

## 7. Optional: humanized cursor visual check

`https://camoufox.com/tests/buttonclick` — random-button-clicker page used during development.

In the MCP session, click the button 5–10 times with ~300 ms between clicks. With `--humanize --showcursor` enabled, the red cursor highlighter should travel along curved trajectories between button positions, not jump. If the cursor jumps, the launcher isn't passing `humanize: True` to the binary (see §2).

---

## 8. Known residuals (do not panic about these)

| Residual | Where | Web-reachable? |
|---|---|---|
| `Camoufox.app` bundle name | filesystem & Dock | No |
| `MOZ_APP_BASENAME=Camoufox` | `modules/AppConstants.sys.mjs` | No — chrome-only |
| `Camoufox` in `about:buildconfig` | `chrome/toolkit/content/global/buildconfig.html` | No — chrome URL |
| WebRTC leaks real public IP | STUN candidate | Not a bot signal; pass `--block-webrtc` if anonymity matters (Camoufox supports `block_webrtc=True`; the launcher would need to wire it through) |

---

## 9. If something fails

1. **UA still contains "Camoufox"** → launcher saved-fingerprint refresh logic didn't fire. Delete `~/.camoufox-mcp-fingerprint.json` and restart MCP server.
2. **`humanize`/`showcursor` not active** → §2 will catch it. Most likely cause: `launch_options()` raised and the silent `except: config = {}` fallback dropped everything. Check `launch-camoufox-mcp.py` for the `_load_properties_macos_bundle` monkey-patch.
3. **Error strings still say "Camoufox"** → check §3. The symlink target file in `camoufox-146.0.1-beta.25/browser/locales/en-US/chrome/overrides/appstrings.properties` must say `Firefox`. If a release-style `omni.ja` was produced (`./mach build stage-package`), the dev symlink is overridden — rebuild without packaging, or repack.
4. **CreepJS shows >0% headless / stealth** → headed mode pin (`--no-headless`) wasn't applied, or screen size mismatch. Check launcher logs and the `window_size` block in `launch-camoufox-mcp.py`.
