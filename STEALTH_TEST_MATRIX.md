# Camoufox MCP — Full Stealth Test Matrix

A practical, exhaustive checklist for verifying that the launcher + Camoufox
binary produce a fingerprint indistinguishable from a real Firefox 146 / macOS
session. Use this when changing the launcher, building a new Camoufox binary,
or auditing before a sensitive run (e.g. LinkedIn).

The matrix is organized by **detection layer** and ordered from highest-impact
to lowest. Each row gives the URL/method, what to check, expected result, why
it matters, and pass criteria. JS snippets are paste-ready for
`mcp__playwright__browser_evaluate`.

> Quick smoke test (90 seconds): jump to section 0, then run sections
> 1.A and 2.A. If those pass, the launcher is healthy.

---

## Prerequisites & Setup

Before running anything below.

**Hardware / OS**: macOS Apple Silicon. All paths below are hardcoded to
`obj-aarch64-apple-darwin/`. Substitute consistently on other archs.

**Built Camoufox binary**:
```
/Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/MacOS/camoufox
```
Built with `./mach build` in `camoufox-146.0.1-beta.25/` (dev build —
chrome resources are symlinked, no packaged `omni.ja`).

**Python venv** with `playwright` and `camoufox` installed:
```
/Users/dev345/code/kfirfer/claude-1/.venv/
```

**Persistent profile dir**:
```
/Users/dev345/playwright-profile/profile-claude-camoufox/
```

**Bootstrap the Playwright MCP server** in a fresh Claude Code session:

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

After adding, restart Claude Code / reconnect the MCP server so the
current `launch-camoufox-mcp.py` is spawned.

If you change locale, OS spoofing, or other fingerprint-affecting
launcher args, delete the saved BrowserForge fingerprint first:

```bash
rm -f ~/.camoufox-mcp-fingerprint.json
```

---

## 0. Configuration sanity (no browser needed)

Run before any browser test — catches silent-config regressions in the launcher.

```bash
/Users/dev345/code/kfirfer/claude-1/.venv/bin/python3 -c "
import sys, os, json
sys.argv = ['launch-camoufox-mcp.py',
            '--user-data-dir', '/tmp/test-profile-smoke',
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
        print('FAIL: no CAMOU_CONFIG env'); sys.exit(1)
    cfg = json.loads(''.join(parts))
    checks = {
        'humanize': cfg.get('humanize') is True,
        'showcursor': cfg.get('showcursor') is True,
        'navigator.hardwareConcurrency == 8': cfg.get('navigator.hardwareConcurrency') == 8,
        'locale:language == en': cfg.get('locale:language') == 'en',
        'locale:region == SG': cfg.get('locale:region') == 'SG',
        'timezone == Asia/Singapore': cfg.get('timezone') == 'Asia/Singapore',
        'navigator.userAgent has Firefox/146.0': 'Firefox/146.0' in cfg.get('navigator.userAgent',''),
        'navigator.userAgent has NO Camoufox': 'Camoufox' not in cfg.get('navigator.userAgent',''),
        'headers.Accept-Encoding full set': cfg.get('headers.Accept-Encoding') == 'gzip, deflate, br, zstd',
    }
    bad = [k for k,v in checks.items() if not v]
    if bad:
        print('FAIL:'); [print('  ✗', k) for k in bad]; sys.exit(1)
    print(f'OK — {len(cfg)} config keys, all checks pass')
os.execvpe = fake_exec
exec(open('/Users/dev345/code/kfirfer/camoufox/launch-camoufox-mcp.py').read())
"
```

Also verify the binary mtime is recent and the source files are correctly
patched:

```bash
echo "=== binary mtime ==="
ls -la /Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/MacOS/camoufox

echo "=== error-string rebrand (must say Firefox, not Camoufox) ==="
grep -c "Firefox can't establish" /Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/Resources/browser/chrome/en-US/locale/browser/appstrings.properties

echo "=== Accept-Encoding C++ fix present ==="
grep -A2 "else if (isSecure)" /Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/netwerk/protocol/http/nsHttpHandler.cpp | grep MaskConfig

echo "=== ServiceWorker timezone fallback present ==="
grep -c "TimezoneManager(ucid) → TimezoneManager(0) → MaskConfig\|MaskConfig::GetString..timezone" /Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/dom/workers/WorkerPrivate.cpp
```

---

## 1. HTTP / network-layer fingerprint

### 1.A — Request headers echo

| | |
|---|---|
| URL | `https://httpbin.org/headers` |
| What | Server echoes the exact headers our browser sent |
| Why | Headers are the first thing every CDN / WAF / anti-bot service inspects |

Expected values (real Firefox 146 on macOS):

```json
{
  "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Encoding": "gzip, deflate, br, zstd",
  "Accept-Language": "en-SG,en;q=0.5",
  "Priority": "u=0, i",
  "Sec-Fetch-Dest": "document",
  "Sec-Fetch-Mode": "navigate",
  "Sec-Fetch-Site": "none",
  "Sec-Fetch-User": "?1",
  "Upgrade-Insecure-Requests": "1",
  "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:146.0) Gecko/20100101 Firefox/146.0"
}
```

Pass criteria (all must be true):
- `Accept-Encoding` contains all four of `gzip, deflate, br, zstd` (no missing `br` or `zstd`)
- `Accept-Language` matches the spoofed locale exactly, uses Firefox's `q=0.5` (not Chrome's `q=0.9`)
- `Priority` header present (Firefox 146 only; missing = Chrome-style stack)
- `User-Agent` contains NO `Camoufox`, NO `HeadlessChrome`, NO `PhantomJS`
- `Sec-Fetch-*` headers present for navigations
- NO `sec-ch-ua*` client-hint headers (Firefox doesn't send those — Chrome does)

### 1.B — TLS / JA3 / JA4 fingerprint

| | |
|---|---|
| URL | `https://tls.peet.ws/api/all` (JSON dump of TLS + HTTP/2 fingerprint) |
| | `https://tools.scrapfly.io/api/fp/ja3` (Scrapfly TLS check) |
| | `https://browserleaks.com/ssl` (rendered table) |
| What | The TLS ClientHello fingerprint (cipher suites, extensions, supported_groups, ALPN, GREASE) |
| Why | LinkedIn / Cloudflare / Akamai / DataDome inspect TLS *before* any JS runs. A Firefox UA over a non-Firefox TLS stack is an immediate flag. |

Expected: JA3 and JA4 hashes should match a stock Firefox 146 macOS profile. The peet.ws JSON `tls.ja3` and `tls.ja4` strings should look like Firefox patterns (e.g. JA4 starts with `t13d` for TLS 1.3 over TCP).

Pass criteria:
- `tls.peet.ws` `tls.client_random` non-zero, ja4 starts with `t13d`
- Cipher suite count is the Firefox-typical value (currently ~15 for Fx146)
- ALPN list is `h2,http/1.1` in Firefox order
- No JA3 mismatch warning at `browserleaks.com/ssl`
- HTTP/2 fingerprint shows Firefox-typical SETTINGS frame (not Chrome's)

Known caveat: real macOS Firefox users today are running 150+. Our Camoufox is on 146. So even a perfect Fx146 TLS hash is now in the "long tail" of real traffic and may itself look slightly unusual to ML-based detectors.

### 1.C — WebRTC IP leak

| | |
|---|---|
| URL | `https://browserleaks.com/webrtc` |
| | `https://www.browserscan.net/webrtc` |
| What | Whether STUN ICE candidates leak the real public IP |
| Why | Even with a perfect proxy, a STUN candidate exposes the underlying IP |

Pass criteria (we run `--block-webrtc` so):
- `RTCPeerConnection` undefined OR throws on construction
- No IPv4/IPv6 candidates leaked
- `MediaDevices.enumerateDevices()` may still expose mic/webcam labels — that's fine

Quick in-browser check:
```js
({ rtc: typeof RTCPeerConnection, mozRtc: typeof window.mozRTCPeerConnection })
// expected: { rtc: 'undefined', mozRtc: 'undefined' }
```

### 1.D — DNS leak

| | |
|---|---|
| URL | `https://www.browserscan.net/dns-leak` |
| | `https://www.dnsleaktest.com/` |
| | `https://ipleak.net/` |
| What | Which DNS resolvers your traffic actually hits |
| Why | If proxy traffic uses host DNS, the proxy IP geo lies but DNS resolvers reveal the real ISP |

Pass criteria: DNS resolvers should be from the same region as your proxy exit. Without a proxy this just confirms your host's resolvers.

---

## 2. Browser identity (UA / engine consistency)

### 2.A — JS-side navigator dump

Snippet:
```js
({
  userAgent: navigator.userAgent,
  appVersion: navigator.appVersion,
  appCodeName: navigator.appCodeName,
  appName: navigator.appName,
  vendor: navigator.vendor,
  vendorSub: navigator.vendorSub,
  product: navigator.product,
  productSub: navigator.productSub,
  buildID: navigator.buildID,
  oscpu: navigator.oscpu,
  platform: navigator.platform,
  userAgentData: navigator.userAgentData,
  doNotTrack: navigator.doNotTrack,
  globalPrivacyControl: navigator.globalPrivacyControl,
  webdriver: navigator.webdriver,
  cookieEnabled: navigator.cookieEnabled,
  javaEnabled: navigator.javaEnabled(),
  pdfViewerEnabled: navigator.pdfViewerEnabled,
  hardwareConcurrency: navigator.hardwareConcurrency,
  maxTouchPoints: navigator.maxTouchPoints,
  language: navigator.language,
  languages: navigator.languages,
})
```

Expected (Firefox 146 macOS / Singapore):
```jsonc
{
  "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:146.0) Gecko/20100101 Firefox/146.0",
  "appVersion": "5.0 (Macintosh)",          // Firefox-style short form, NOT the full UA minus Mozilla
  "appCodeName": "Mozilla",
  "appName": "Netscape",
  "vendor": "",                              // Empty for Firefox (Chrome returns "Google Inc.")
  "vendorSub": "",
  "product": "Gecko",
  "productSub": "20100101",                  // Frozen for all Firefox versions
  "buildID": "20181001000000",               // Frozen by Firefox privacy-resistance
  "oscpu": "Intel Mac OS X 10.15",           // Frozen even on Apple Silicon
  "platform": "MacIntel",                    // Frozen even on Apple Silicon
  "userAgentData": undefined,                // Firefox does NOT expose UA-CH (Chrome does)
  "webdriver": false,
  "javaEnabled": false,
  "pdfViewerEnabled": true,
  "hardwareConcurrency": 8,
  "maxTouchPoints": 0,
  "language": "en-SG",
  "languages": ["en-SG", "en"]
}
```

Cross-checks LinkedIn-class detectors run (LinkedIn's `getHasLiedOs`, Castle.io blog):
- `userAgent` claims OS → `oscpu` confirms OS → `platform` confirms OS
- `userAgent` claims OS without touch → `maxTouchPoints == 0`
- `userAgent` claims Firefox → `vendor` is empty (NOT "Google Inc.")
- `productSub == "20100101"` (any other value = spoof)
- `userAgentData === undefined` (defined = Chromium pretending to be Firefox)

### 2.B — Headers vs JS UA agreement

Same UA must appear in HTTP `User-Agent` header (section 1.A) AND `navigator.userAgent` AND any web-platform context that exposes it.

### 2.C — Automation framework markers

```js
({
  webdriver: navigator.webdriver,
  windowChrome: typeof window.chrome,
  cdc:  Object.keys(document).filter(k => /^\$cdc_|^cdc_/i.test(k)),
  selenium: Object.keys(window).filter(k => /selenium|Selenium/.test(k)),
  phantom: ['callPhantom','_phantom','phantom'].filter(k => k in window),
  nightmare: '_nightmare' in window || '__nightmare' in window,
  playwright: ['__pwInitScripts','__playwright__binding__','__playwright_target__'].filter(k => k in window),
  puppeteer: ['__puppeteer_evaluation_script__'].filter(k => k in window),
})
```

Expected:
```json
{
  "webdriver": false,
  "windowChrome": "undefined",
  "cdc": [],
  "selenium": [],
  "phantom": [],
  "nightmare": false,
  "playwright": [],
  "puppeteer": []
}
```

---

## 3. Worker / ServiceWorker consistency

The single most powerful consistency check is "do main thread and Worker
report identical values?" — that's `hasInconsistentWorkerValues` in
deviceandbrowserinfo, and the Worker section in CreepJS.

### 3.A — Dedicated Web Worker round-trip

```js
async () => {
  const code = `self.onmessage = () => self.postMessage({
    ua: navigator.userAgent,
    lang: navigator.language,
    langs: [...navigator.languages],
    cores: navigator.hardwareConcurrency,
    plat: navigator.platform,
    tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    tzOff: new Date().getTimezoneOffset(),
  });`;
  const w = new Worker(URL.createObjectURL(new Blob([code], {type:'application/javascript'})));
  const worker = await new Promise(r => { w.onmessage = e => r(e.data); w.postMessage('x'); });
  w.terminate();
  const main = { ua: navigator.userAgent, lang: navigator.language, langs: [...navigator.languages],
    cores: navigator.hardwareConcurrency, plat: navigator.platform,
    tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    tzOff: new Date().getTimezoneOffset() };
  const mismatches = Object.fromEntries(
    Object.keys(main).map(k => [k, JSON.stringify(main[k]) !== JSON.stringify(worker[k])])
  );
  return { main, worker, mismatches };
}
```

Pass: every key in `mismatches` is `false`.

### 3.B — ServiceWorker timezone (the leak we just fixed)

Test via `https://abrahamjuliot.github.io/creepjs/` (which registers a real
ServiceWorker) and verify the **Worker** panel shows:

```
ServiceWorkerGlobalScope:
  Worker hash …
  lang/timezone: en-SG (1 US dollar) | Asia/Singapore (-480)   ← must match main timezone
  device: cores: 8, Mac (MacIntel), macOS Catalina
  GPU: Apple, Apple M1, or similar
  userAgent: Mozilla/5.0 … Firefox/146.0
```

Pass: ServiceWorker timezone identical to main. (Pre-fix, this leaked the
host real TZ like `Asia/Bangkok (-420)`.)

### 3.C — Direct ServiceWorker registration test

Same-origin SW only — can't be tested from `about:blank`. Use creepjs for
this in practice.

---

## 4. Hardware fingerprint

### 4.A — CPU / memory

```js
({
  hardwareConcurrency: navigator.hardwareConcurrency,
  deviceMemory: navigator.deviceMemory,        // undefined on Firefox
  maxTouchPoints: navigator.maxTouchPoints,
})
```

Pass: `hardwareConcurrency == 8` (M1 default), `deviceMemory` undefined (Firefox doesn't expose), `maxTouchPoints == 0`.

### 4.B — WebGL

| URL | Purpose |
|---|---|
| `https://scrapfly.io/web-scraping-tools/webgl-fingerprint` | Full WebGL parameter dump |
| `https://browserleaks.com/webgl` | Same |
| In-browser snippet (below) | Quick check |

```js
const c = document.createElement('canvas');
const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
const dbg = gl.getExtension('WEBGL_debug_renderer_info');
({
  vendor: gl.getParameter(gl.VENDOR),                                          // "Mozilla" expected (privacy resistance)
  renderer: gl.getParameter(gl.RENDERER),                                      // "Mozilla" expected
  unmaskedVendor: dbg && gl.getParameter(dbg.UNMASKED_VENDOR_WEBGL),           // "Apple"
  unmaskedRenderer: dbg && gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL),       // "Apple M1, or similar" (typical)
  version: gl.getParameter(gl.VERSION),
  shadingLanguageVersion: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
  extCount: gl.getSupportedExtensions().length,                                // ~40 for Firefox+Apple
})
```

Pass:
- `unmaskedVendor` and `unmaskedRenderer` match a real GPU profile (don't spoof to nonsense — anti-bots have datasets of known-good GPU pairs)
- Same values reported in main thread AND Worker (covered by 3.A)
- Extension count in the typical Firefox/macOS range (40)

### 4.C — WebGPU

```js
('gpu' in navigator) ? navigator.gpu.requestAdapter().then(a => ({
  available: !!a,
  info: a && a.info,
})) : { available: false }
```

Pass: Firefox doesn't ship WebGPU by default — `unsupported` or `undefined`
is fine. CreepJS Worker section confirms `userAgentData: unsupported` for
similar reasons.

---

## 5. Screen / viewport

### 5.A — Window dimensions

```js
({
  screen: { w: screen.width, h: screen.height, aw: screen.availWidth, ah: screen.availHeight },
  colorDepth: screen.colorDepth,
  pixelDepth: screen.pixelDepth,
  outer: { w: window.outerWidth, h: window.outerHeight },
  inner: { w: window.innerWidth, h: window.innerHeight },
  visual: { w: visualViewport.width, h: visualViewport.height },
  dpr: window.devicePixelRatio,
  orientation: screen.orientation && screen.orientation.type,
})
```

Pass (default headed config):
- `screen: 1512×982` (MacBook Pro 14" M3 logical resolution)
- `colorDepth: 30` (correct for Retina P3)
- `outer == inner == visual` (no chrome offsets in our setup)
- `dpr: 1` (we report 1× not 2× — TODO: see "Open questions" below)
- `orientation.type: "landscape-primary"`

Common bot signal: `screen 800×600` or `1280×720` = default headless Chrome.
We must NEVER hit those.

Test site: `https://browserleaks.com/javascript` shows the full set rendered.

### 5.B — CSS / matchMedia

```js
({
  prefers_color: matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light',
  prefers_reduced: matchMedia('(prefers-reduced-motion: reduce)').matches,
  hover: matchMedia('(any-hover: hover)').matches,
  pointer: matchMedia('(any-pointer: fine)').matches,
})
```

Pass: `prefers_color: dark` (we set `contextOptions.colorScheme = 'dark'`),
hover/pointer match a Mac with trackpad/mouse (both true).

---

## 6. Locale / timezone

### 6.A — Intl + Date

```js
({
  resolvedLocale: Intl.DateTimeFormat().resolvedOptions(),
  numberLocale: new Intl.NumberFormat().resolvedOptions(),
  dnLocale: new Intl.DisplayNames(undefined, {type:'language'}).of('en'),
  tzOffset: new Date().getTimezoneOffset(),
  localeStr: new Date().toLocaleString(),
  dateFmt: new Intl.DateTimeFormat(undefined, {dateStyle: 'short'}).format(new Date()),
})
```

Pass (Singapore default):
- `timeZone: "Asia/Singapore"`, `locale: "en-SG"`
- `tzOffset: -480` (UTC+8)
- `dateFmt: DD/MM/YYYY` (en-SG style)
- `dnLocale: "English"`
- Currency examples show "S$" / "SGD" when locale=en-SG

### 6.B — Headers locale

Verified in 1.A: `Accept-Language: en-SG,en;q=0.5`.

### 6.C — IP vs timezone consistency

Critical cross-check that LinkedIn does. Compare:
- IP geolocation (from `https://www.browserscan.net/ip` or `https://ipinfo.io/json`) → should be Singapore (requires proxy)
- Browser timezone (from above) → Asia/Singapore
- Browser locale → en-SG

If all three agree, you're consistent. If IP is Thailand but TZ is
Singapore, sophisticated detectors flag the mismatch.

---

## 7. Canvas / rendering fingerprint

### 7.A — Canvas 2D

URL: `https://scrapfly.io/web-scraping-tools/canvas-fingerprint`
Also: `https://browserleaks.com/canvas`

What's tested:
- `HTMLCanvasElement.toDataURL()` hash
- `getImageData()` pixel-level dump
- `measureText()` widths
- Emoji rendering (each emoji renders slightly differently per OS/font/engine)

Pass:
- Hashes are *stable across page reloads* (same session = same hash)
- Hashes match real macOS+Firefox renderings (not headless defaults)
- Canvas hash in main thread matches Canvas hash in iframe sandbox
  (CreepJS canvas1/canvas2/canvas3/canvas4/canvas5 — first two should
  match, the iframe ones can differ as Firefox typically isolates them)

### 7.B — DOMRect / SVGRect

```js
const r = document.createElement('div');
r.style.cssText = 'position:fixed;top:0;left:0;width:100px;height:50px;';
document.body.appendChild(r);
const rect = r.getBoundingClientRect();
r.remove();
({ x: rect.x, y: rect.y, width: rect.width, height: rect.height,
   precision: rect.width.toString().length })
```

CreepJS DOMRect / SVGRect sections — pass if hashes stable.

### 7.C — Font enumeration

URL: `https://browserleaks.com/fonts`
Also: `https://scrapfly.io/web-scraping-tools/fonts`

Pass:
- Font count matches a real macOS install (~250+ fonts including system + bundled)
- macOS-only fonts present: Galvji, MuktaMahee, Luminari, Helvetica Neue, Geneva
- No "fake" fonts that don't exist on real macOS

---

## 8. Audio fingerprint

### 8.A — AudioContext

URL: `https://browserleaks.com/audio`
In-browser:
```js
const ctx = new (window.OfflineAudioContext || window.webkitOfflineAudioContext)(1, 44100, 44100);
const osc = ctx.createOscillator(); osc.type = 'triangle'; osc.frequency.value = 10000;
const comp = ctx.createDynamicsCompressor();
osc.connect(comp); comp.connect(ctx.destination); osc.start(0);
const buf = await ctx.startRendering();
const data = buf.getChannelData(0);
let sum = 0; for (let i=4500;i<5000;i++) sum += Math.abs(data[i]);
({ sampleRate: ctx.sampleRate, sum })
```

Pass: `sum` should be stable across reloads (same value), match a real Mac
AudioContext profile (CreepJS Audio section hash should be a known-good Mac value).

### 8.B — Speech synthesis voices

CreepJS Speech section. Pass:
- `local (72)`, `lang (51)` (matches a real macOS install)
- NOT `local (0): blocked` (that means voices haven't loaded yet, retry; or
  spoofing is broken)

---

## 9. Permissions / Storage / Network APIs

### 9.A — Permissions API

```js
const names = ['geolocation','notifications','camera','microphone','clipboard-write','clipboard-read','persistent-storage'];
Promise.all(names.map(n => navigator.permissions.query({name:n}).then(p => [n, p.state]).catch(e => [n, 'error:'+e.name])))
  .then(r => Object.fromEntries(r))
```

Pass: each permission returns `prompt` / `denied` / `granted`. NO permission
returns "default" for everything (that's a header-only spoof tell). Our
config returns `prompt` for most, which matches real Firefox.

### 9.B — Storage estimate

```js
navigator.storage.estimate().then(e => ({ quota: e.quota, usage: e.usage }))
```

Pass: non-zero `quota` (we report ~10GB which is standard).

### 9.C — Network APIs that Firefox doesn't ship

```js
({
  connection: typeof navigator.connection,        // 'undefined' on Firefox
  getBattery: typeof navigator.getBattery,        // 'undefined' on Firefox 146+
  bluetooth: typeof navigator.bluetooth,
  usb: typeof navigator.usb,
  hid: typeof navigator.hid,
  serial: typeof navigator.serial,
})
```

Pass: `connection`, `getBattery` undefined (a real Firefox tell — Chrome
exposes both, so if our spoof reports a UA of Firefox but exposes
`navigator.connection`, we're caught).

---

## 10. Plugins / MIME types

```js
({
  pluginsLength: navigator.plugins.length,
  plugins: [...navigator.plugins].map(p => p.name),
  mimeLength: navigator.mimeTypes.length,
  mimes: [...navigator.mimeTypes].map(m => m.type),
  pluginsArrayConstructorName: navigator.plugins.constructor.name,
  mimeArrayConstructorName: navigator.mimeTypes.constructor.name,
})
```

Pass (Firefox 146 default):
- `pluginsLength: 5` (PDF Viewer + variants)
- `mimeLength: 2` (application/pdf, text/pdf)
- `PluginArray`, `MimeTypeArray` types (not generic Array)

`pluginsLength == 0` is a strong headless tell.

---

## 11. Iframe / cross-context consistency

### 11.A — Iframe sandbox

```js
const f = document.createElement('iframe');
f.style.display = 'none';
document.body.appendChild(f);
const cw = f.contentWindow;
const result = {
  same_ua: cw.navigator.userAgent === navigator.userAgent,
  same_lang: cw.navigator.language === navigator.language,
  same_cores: cw.navigator.hardwareConcurrency === navigator.hardwareConcurrency,
  same_tz: cw.Intl.DateTimeFormat().resolvedOptions().timeZone === Intl.DateTimeFormat().resolvedOptions().timeZone,
};
f.remove();
result
```

Pass: every field `true`. (Camoufox's `i_know_what_im_doing` config — and
the iframe-leak protections in patches — should ensure this.)

### 11.B — Iframe webdriver leak

```js
const f = document.createElement('iframe');
document.body.appendChild(f);
const leak = f.contentWindow.navigator.webdriver;
f.remove();
({ iframeWebdriver: leak })   // must be false (or undefined), NEVER true
```

Pass: `false` or `undefined`. `hasWebdriverInFrameTrue` is one of the
deviceandbrowserinfo checks specifically.

---

## 12. Behavioral signals (application-layer, NOT verifiable here)

These are NOT covered by the launcher. They must be implemented in whatever
automation script drives the browser. The test sites don't measure them
unless you interact:

- Mouse movement curvature (we have `--humanize`, verified visually with `--showcursor` on `https://camoufox.com/tests/buttonclick`)
- Click cadence (random ~150-400ms between events, not microsecond-uniform)
- Hover before click (real users hover; many bots don't)
- Scroll patterns (chunked, not single-frame jumps; pauses between scrolls)
- Typing cadence (per-key timing variance, not paste)
- Reading pauses (3-8s on a long page before next action)
- Action density (e.g. <30 LinkedIn connection requests / hour; <100 profile views / hour)
- Session length (long-running real users; not 5-minute hit-and-run)

Verification: `https://deviceandbrowserinfo.com/are_you_a_bot_interactions`
runs an interactive version that detects programmatic vs human mouse paths.
Recommended to run manually.

---

## 13. Detection test sites — full list with pass criteria

Run in this order. Each takes 5-15s. Total: ~5 minutes for the full suite.

### 13.1 Browser-fingerprint aggregators (run all)

| URL | What it measures | Pass criteria |
|---|---|---|
| `https://deviceandbrowserinfo.com/are_you_a_bot` | 20-signal aggregate including inconsistent-worker, high-hw-concurrency, CDP, Playwright globals, GPU consistency, client hints | `isBot: false`, every detail field `false` |
| `https://abrahamjuliot.github.io/creepjs/` | The most thorough: headless%, stable+loose fingerprint, full main↔worker↔SW comparison, canvas/audio/font/WebGL hashes, Intl probes | `0% like headless / 0% headless / 0% stealth`, every subtest ✔, stable fingerprint ✔, loose fingerprint ✔, Worker/SW values match main |
| `https://www.browserscan.net/bot-detection` | 15 framework probes + CDP/DevTool + Native Navigator (40+ props) | Top verdict: `Normal`. Every individual row: `Normal`/Clear |
| `https://www.browserscan.net/` (homepage) | Fingerprint authenticity score | `100%` |
| `https://scrapfly.io/web-scraping-tools/automation-detector` | 14 signals: webdriver, CDP, framework markers, Function.toString tampering, plugin/mime count | `Not Automated` / 0 detected / 0 suspicious |
| `https://scrapfly.io/web-scraping-tools/browser-fingerprint` | Full browser fingerprint table | Visual inspection — no flagged inconsistencies |
| `https://pixelscan.net/bot-check` | 4 categories: Navigator / Webdriver / CDP / User Agent | `You're Definitely a Human`, all 4 Clear |
| `https://pixelscan.net/fingerprint-check` | Detailed fingerprint quality scoring | High consistency score |
| `https://bot-detector.rebrowser.net/` | Rebrowser's own probes: dummyFn, sourceUrlLeak, mainWorldExecution, runtimeEnableLeak, exposeFunctionLeak, pwInitScripts, navigatorWebdriver, viewport, bypassCsp | All triggered tests 🟢 green |
| `https://infosimples.github.io/detect-headless/` | 16 legacy probes including plugin/mime/lang counts, broken-image dims, outer-dim presence | All "Headful". `Time Elapse` is a Playwright artifact (alert auto-dismiss timing) — ignore |
| `https://browserleaks.com/javascript` | Comprehensive JS-side dump | Compare against real Firefox 146 baseline |

### 13.2 Specific-signal deep dives

| URL | What |
|---|---|
| `https://browserleaks.com/canvas` | Canvas 2D hash + image |
| `https://browserleaks.com/webgl` | WebGL params + image |
| `https://browserleaks.com/audio` | AudioContext fingerprint |
| `https://browserleaks.com/fonts` | Font enumeration |
| `https://browserleaks.com/ssl` | TLS/JA3/JA4 + cipher suite |
| `https://browserleaks.com/ip` | Public IP + geolocation |
| `https://browserleaks.com/webrtc` | WebRTC IP leak |
| `https://tls.peet.ws/api/all` | Raw TLS + HTTP/2 JSON fingerprint |
| `https://tools.scrapfly.io/api/fp/ja3` | JA3 endpoint |
| `https://tools.scrapfly.io/api/fp/http2` | HTTP/2 fingerprint |
| `https://www.howsmyssl.com/` | SSL/TLS rating |

### 13.3 Identity uniqueness (informational)

| URL | What |
|---|---|
| `https://amiunique.org/fingerprint` | How unique your fingerprint is across the AmIUnique dataset |
| `https://coveryourtracks.eff.org/` | EFF's tracker + fingerprint analyzer |
| `https://www.deviceinfo.me/` | Single-page device-info dump |

These don't return pass/fail. The goal is "unique enough to be one of many"
(i.e. not in the top 0.01% rarest) without being so common you collide with
known bot profiles.

### 13.4 Commercial bot-detection demos (optional)

Most don't expose results; useful only for whether you get blocked:

| URL | Vendor |
|---|---|
| `https://demo.fingerprint.com/playground` | Fingerprint.com |
| (LinkedIn login page) | LinkedIn's own anti-bot |
| `https://nowsecure.nl/` | DataDome demo |
| `https://shop.nordstrom.com/` | Akamai Bot Manager production |
| `https://www.zillow.com/` | PerimeterX production |

If a commercial site is your actual target, run it. The aggregators above
predict but don't guarantee.

### 13.5 Camoufox-specific tests

| URL | What |
|---|---|
| `https://camoufox.com/tests/buttonclick` | Random-button-clicker — visually verify humanized cursor (with `--showcursor`) |
| `https://camoufox.com/tests/screen` | Screen-fingerprint test page |

---

## 14. Local file-system checks + runtime brand-leak trigger

### 14.A — Runtime: force a connection-failure error and inspect chrome console

The file-system grep at 14.B confirms the right strings are on disk; this
proves the running binary actually emits them at runtime. Pre-rebrand,
the chrome console produced `[JavaScript Error: "Camoufox can't establi…"`
on any WebSocket/EventSource failure — a hard brand leak visible to any
script reading the Playwright `console` channel.

Navigate to `about:blank` first, then evaluate:

```js
async () => {
  try { new WebSocket('wss://nonexistent-host-rebuild-check.invalid/'); } catch(_) {}
  try { new EventSource('https://nonexistent-host-rebuild-check.invalid/'); } catch(_) {}
  await new Promise(r => setTimeout(r, 2500));
  return 'errors triggered';
}
```

Then read `mcp__playwright__browser_console_messages`. Pass criteria:

- One or more lines containing `"Firefox can't establis..."`
- ZERO lines containing `"Camoufox can't establi..."`

If you see `"Camoufox can't establi..."`, the binary is stale (a build
without the rebrand patch) or a different `.app` is being launched. Go
back to section 0 and verify binary mtime + `appstrings.properties`.

### 14.B — File-system checks

Verify the binary's resources don't leak the brand string:

```bash
# Should return empty — no Camoufox-branded user-visible strings in chrome resources
grep -rln "Camoufox" /Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/Resources/browser/chrome/ /Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/Resources/browser/localization/

# Expected residuals only — InfoPlist (macOS Dock name), buildconfig (chrome URL), AppConstants (chrome JS only). NOT web-reachable.
grep -rln "Camoufox" /Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/Resources/ | grep -v "^$"

# Verify the Accept-Encoding C++ fix (PR #474 shape)
sed -n '2113,2135p' /Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/netwerk/protocol/http/nsHttpHandler.cpp | grep -q "else if (isSecure)" && grep -q "MaskConfig::GetString" <(sed -n '2113,2135p' /Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/netwerk/protocol/http/nsHttpHandler.cpp) && echo "✓ Accept-Encoding patch in place"

# Verify the ServiceWorker timezone fallback
grep -q 'MaskConfig::GetString."timezone"' /Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/dom/workers/WorkerPrivate.cpp && echo "✓ SW timezone fallback in place"
```

---

## 15. Best practices (when running real targets)

1. **Start with a fresh profile** for any new target account. Profiles
   carry cookies, LocalStorage, IndexedDB, and SW registrations that link
   sessions across fingerprints. A burnt profile contaminates new spoofs.
2. **Use a residential proxy from the spoofed region.** SG locale + TH IP
   = LinkedIn `getHasLiedBrowser`-class flag.
3. **Wipe `~/.camoufox-mcp-fingerprint.json` whenever you change locale or
   OS spoofing.** Saved BrowserForge fingerprint picks fonts/voices/etc.
   for the previous region; changing locale without wiping creates
   internally inconsistent fonts ↔ locale.
4. **Don't randomize everything per request.** A fingerprint should be
   stable across the *session*. Real users don't suddenly become a new
   device mid-browse.
5. **Don't over-spoof.** A WebGL renderer of "Random String 12345" is
   worse than the host's real value. WAFs hash WebGL parameters against
   known-good datasets; an unknown hash is a flag.
6. **Run section 13.1 every time you change the launcher or rebuild the
   binary.** A passing config can silently regress on a Camoufox upgrade.
7. **Test on `about:blank` first** before pointing at the target. Many
   leaks are visible on a static blank page (UA, navigator, WebGL); no
   need to expose the target if the basics fail.
8. **Watch the chrome console**, not just page JS output. Some leaks
   (e.g. the now-fixed `"Camoufox can't establi…"` error string) only
   show up in the browser-internal console reachable by Playwright's
   protocol but not by page scripts. Compare what page-script
   `console.error` sees vs what Playwright's `console` channel reports.
9. **One change per build.** If you bump locale + GPU + cores + viewport
   at once and something breaks, you don't know which change caused it.
10. **Keep a baseline snapshot.** Run section 13.1 immediately after a
    known-good build and screenshot/save the JSON. Compare every
    subsequent run against it.

---

## 16. Open questions / known gaps

These aren't currently solved by the launcher or this matrix. Document
them so they're not "discovered" mid-incident.

- **`devicePixelRatio = 1`** even though we claim a Retina Mac. Real
  M-series Macs with the default scaling typically report `dpr = 2`.
  Fixing this means setting screen dimensions and DPR together; we'd
  need to look up the right combination. Probably matters for some
  fingerprinters.
- **TLS fingerprint is Firefox 146.** Real-world Firefox is now 150+.
  Even a perfectly-imitated Fx146 JA3/JA4 is in the long tail of real
  traffic by 2026. Mitigation: upgrade the Camoufox build base.
- **No `webrtc:ipv4/ipv6` spoofing** — we use `block_webrtc=True` which
  removes the WebRTC vector entirely. A real Firefox user with a real
  ISP allows WebRTC; blocked WebRTC is itself an anomaly (matches
  privacy-resistant users though, which is a non-zero population).
- **WebGPU `unsupported`** — Firefox ships WebGPU behind a pref. Real
  Fx users who enabled it report a full GPU adapter. Most don't, so
  this matches the majority population.
- **macOS Catalina (10.15)** is what we claim via `oscpu`. Firefox UA
  reduction freezes this for all macOS users, but if you cross-check
  with `userAgentData` (Chromium-only API) or Sec-CH-UA, you'd see
  newer Macs there. Firefox 146 doesn't expose those, so we're fine.
- **Speech voices count 72** matches a real macOS install, but the
  *exact* voice list (which 72 voices) is fingerprintable. Camoufox
  defaults to a synthetic list — match it to your spoofed locale if
  this matters for your target.

---

## Appendix A — Quick reference: known-good values

If a check returns one of these values for our current setup (Camoufox
146.0.1-beta.25 + this launcher + Singapore defaults), it's expected.

| Field | Value |
|---|---|
| `navigator.userAgent` | `Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:146.0) Gecko/20100101 Firefox/146.0` |
| `navigator.appVersion` | `5.0 (Macintosh)` |
| `navigator.oscpu` | `Intel Mac OS X 10.15` |
| `navigator.platform` | `MacIntel` |
| `navigator.vendor` | (empty) |
| `navigator.productSub` | `20100101` |
| `navigator.buildID` | `20181001000000` |
| `navigator.hardwareConcurrency` | `8` |
| `navigator.maxTouchPoints` | `0` |
| `navigator.language` | `en-SG` |
| `navigator.languages` | `["en-SG", "en"]` |
| `navigator.webdriver` | `false` |
| `navigator.pdfViewerEnabled` | `true` |
| `navigator.userAgentData` | `undefined` |
| `navigator.connection` | `undefined` |
| `navigator.getBattery` | `undefined` |
| `navigator.plugins.length` | `5` |
| `navigator.mimeTypes.length` | `2` |
| `Intl.DateTimeFormat().resolvedOptions().timeZone` | `Asia/Singapore` |
| `new Date().getTimezoneOffset()` | `-480` |
| `screen.width × screen.height` | `1512 × 982` (headed; varies headless) |
| `screen.colorDepth` | `30` |
| `window.devicePixelRatio` | `1` (see open questions) |
| `Accept-Encoding` header | `gzip, deflate, br, zstd` |
| `Accept-Language` header | `en-SG,en;q=0.5` |
| `Priority` header | `u=0, i` |
| WebGL `UNMASKED_RENDERER` | `Apple M1, or similar` (BrowserForge picks one) |
| Speech `local` voices | `72` (Mac default) |
| Stable fingerprint hash (CreepJS) | deterministic per session; should not change across reloads |

---

## Appendix B — One-command full battery

Save this as `run_stealth_tests.sh` and execute manually. It opens each
detection site in sequence with a delay between, so you can screenshot or
record results.

```bash
#!/bin/bash
# Run after MCP is up. Opens detection sites with delays.
# Drive these via the MCP browser, not by clicking, so you get JS-evaluable results.
URLS=(
  "https://httpbin.org/headers"
  "https://tls.peet.ws/api/all"
  "https://deviceandbrowserinfo.com/are_you_a_bot"
  "https://abrahamjuliot.github.io/creepjs/"
  "https://www.browserscan.net/bot-detection"
  "https://scrapfly.io/web-scraping-tools/automation-detector"
  "https://pixelscan.net/bot-check"
  "https://bot-detector.rebrowser.net/"
  "https://browserleaks.com/javascript"
  "https://browserleaks.com/canvas"
  "https://browserleaks.com/webgl"
  "https://browserleaks.com/webrtc"
  "https://browserleaks.com/ssl"
  "https://www.browserscan.net/"
  "https://www.browserscan.net/webrtc"
  "https://www.browserscan.net/dns-leak"
)
for u in "${URLS[@]}"; do
  echo "TEST: $u"
  # In Claude Code session, would use:
  # mcp__playwright__browser_navigate to $u
  # then a short wait, then snapshot/evaluate
done
```
