# Camoufox — Feedback / Bug Report

**Date:** 2026-05-11
**Reporter context:** Tested **Playwright MCP with stock Playwright-Firefox 146 channel** (not camoufox) against the 2026 anti-bot detection suite in `PLAYWRIGHT_MCP_BOT_DETECTION_TESTS.md` (50+ sites). This document lists **only the findings camoufox should fix.**

Test environment (reproduces every issue below):
- Engine: Firefox 146.0 on macOS, real Firefox/NSS binary driven by Playwright (Juggler)
- UA: `Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:146.0) Gecko/20100101 Firefox/146.0`
- Viewport 1472×801 / screen 1440×900 / DPR 1 / colorDepth 30
- IP: residential (Thailand, ASN 17552 TrueNet) — not a contributing factor
- JA4: `t13d1717h2_5b57614c22b0_3cbfd9057e0d` — genuine Firefox 146 NSS JA4

---

## Priority 1 — Real automation tells (must fix for production stealth)

### P1.1 — Fingerprint Pro detects **`Developer Tools = Yes`** even when Bot = Not Detected
**Source:** T2.13 `https://demo.fingerprint.com/playground`
**Visitor ID:** E240Ye806aHyeZKRNTWX
**Observed:** Bot=Not detected ✅, but Smart Signals panel reports `Developer Tools: Yes` (red) → Suspect Score = 8.
**Hypothesis:** Fingerprint Pro's devtools detector uses one or more of:
1. The `Marionette` / `RemoteAgent` listener that Playwright-Firefox enables to drive the browser (TCP port + prefs `marionette.enabled=true`, `remote.active-protocols`).
2. The classic Firefox debugger-port timing trick (`debugger;` statement timing) — fires whenever a debug client is attached even if devtools UI is closed.
3. `toString`/`Function.prototype.toString` overload trap on objects logged to console — fires if the console is being read by a JS-callable inspector (the Castle blog noted V8 fixed this for Chrome in May 2025; Firefox's SpiderMonkey behaviour was not affected, so the trap still works on Firefox).
**What camoufox can do:**
- Already mitigated in upstream camoufox via `remote.enabled=false` + custom CDP-less driver channel? **Please confirm** — if camoufox still ships with Marionette listening, this is the signal Fingerprint Pro is catching.
- Patch `Function.prototype.toString` so that re-toString'ing a console-logged object does not differ from a non-instrumented run.
- Patch `console.{debug,log,…}` so accessor-trap getters on logged arguments are not invoked.
**How to reproduce:** open demo.fingerprint.com/playground; wait ~10 s; check "Developer Tools" row.

### P1.2 — Intoli's `fpscanner.webDriverAdvanced` returns **FAIL**
**Source:** T2.3 `https://bot.incolumitas.com/` — `intoli.webDriverAdvanced: "FAIL"` (all six other Intoli rows OK).
**What the test checks:** `document.documentElement` attributes for any of `webdriver`, `driver`, `selenium`, `webdriver-evaluate`, `selenium-evaluate`, `webdriver-evaluate-response`, `webdriverFunc`, `webdriverCommand`. Marionette / Geckodriver historically writes a `webdriver` attribute on `<html>` when driving Firefox.
**Hypothesis:** Playwright-Firefox's Juggler patch (or Marionette fallback) still tags `<html>` with one of these attributes during navigation.
**What camoufox can do:**
- Strip Geckodriver-style attribute writes from `document.documentElement` in the Juggler patch.
- Already covered by camoufox? **Please verify with this exact site** (`bot.incolumitas.com`, scroll to *Old Bot Detection Tests* section, look for the JSON line `"webDriverAdvanced": "OK"`).

### P1.3 — `Date.toLocaleString()` returns the **Unix epoch** while timezone is correct
**Source:** T2.14 `https://fingerprint-scan.com/` — `Locale Date: 1/1/1970, 7:00:00 AM` (timezone Asia/Bangkok = GMT+7 ✅, but the Date value is epoch).
**Why this is bad:** every fingerprint vendor that combines `new Date().toLocaleString()` with `Intl.DateTimeFormat().resolvedOptions().timeZone` sees an internal contradiction. A real browser at 11:30 UTC+7 returns a current-time-of-day string; ours returns 1970-01-01. This is a **fingerprint-inconsistent** flag in the arxiv-fp-inconsistent paper category — by itself it can be enough for DataDome/Akamai to escalate.
**Hypothesis:** Playwright-Firefox's `--headless`-like time-zone patch is overriding `Date` constructor with epoch-zero when no JS-side `new Date()` argument is passed; or a `privacy.resistFingerprinting`-style spoof is clamping `Date.now()` to a fixed value.
**What camoufox can do:**
- Audit Date/Intl spoofing layer to ensure `new Date()` returns wall-clock time consistent with timezone.
- If `privacy.resistFingerprinting` is enabled, disable the time-spoofing portion (`privacy.resistFingerprinting.reduceTimerPrecision` only, not full Date clamping).
**Reproduces in:** any page that prints `new Date().toLocaleString()` server-side or client-side.

### P1.4 — WebGL vendor/renderer is the constant string `"ATI Technologies Inc." / "Radeon R9 200 Series, or similar"`
**Source:** T1.6, T2.14, T2.15, T3.1, T3.3 — all five tests captured this **same fixed value**, regardless of the actual host GPU.
**Why this is bad:** real Firefox running on macOS with Apple Silicon or Intel returns `Apple GPU` / `Apple M-series` / `Intel Iris …` or, with WebGL extensions disabled, returns the WebKit-built-in renderer. The "Radeon R9 200 Series, or similar" string is the well-known `privacy.resistFingerprinting` substitute and is itself a fingerprint — any vendor maintaining a list of Firefox-privacy-resistance signatures sees it as **"this is a privacy-tool or anti-detect Firefox profile"**, even if every other signal is clean.
**What camoufox can do:**
- Replace the resistFingerprinting WebGL spoof with a **per-profile randomized but plausible** vendor/renderer that matches the host OS family (Mac → `Apple Inc.` / `Apple GPU`, Linux → `Intel`/`Mesa`/`NVIDIA`, Win → `Google Inc. (NVIDIA)` etc.).
- Vary the answer per profile so vendors can't build a single-value blacklist.
- Already partially handled by camoufox config — please verify it's enabled by default for new profiles.
**Reproduces in:** browserleaks.com/webgl, scrapfly webgl-fingerprint, CreepJS WebGL section.

---

## Priority 2 — Minor heuristic flag

### P2.1 — CreepJS reports **`like-headless: 6%`**
**Source:** T3.1 — overall verdict: chromium=false, headless 0%, stealth 0%, **like-headless 6%**, lies 0 (✔ loose and stable fingerprints both passed).
**What this means:** CreepJS's `like-headless` is a sum of ~17 weak heuristics — 6% means one or two weak signals fired. Without source-diving the page, candidates from our captured data are:
- `Notification.permission` = `default` vs `Permissions.query({name:'notifications'}).state` = `prompt` (a real Firefox profile usually has these consistent).
- `outerWidth == innerWidth` (1472 == 1472) — no left/right chrome — true for many configs but flagged by some heuristics.
- `screen.availLeft = 72` / `availTop = 33` — macOS dock + menubar — fine, but if Playwright sets these from the window manager differently, can disagree with `outerWidth`.
- `WebRTC = blocked` while UA claims a default Firefox install — most default Firefox installs have WebRTC enabled. Disabling it is itself a fingerprint.
**What camoufox can do:**
- Run CreepJS in a CI loop and binary-search which of the ~17 sub-heuristics fires. The CreepJS code is open source (`creepjs/creep.js`) and the `likeHeadless` array is enumerable.
- Provide a working but non-leaking WebRTC stub (mDNS-only ICE) instead of full disable — closes the "Firefox-without-WebRTC" tell while keeping privacy.

---

## Suggested reproduction battery for camoufox CI

A single regression suite that exercises every issue above in <5 minutes per run:

```yaml
- https://demo.fingerprint.com/playground       # P1.1 (Developer Tools row must be "No")
- https://bot.incolumitas.com/                  # P1.2 (intoli.webDriverAdvanced must be "OK")
- https://fingerprint-scan.com/                 # P1.3 (Locale Date must be current wall-clock)
- https://browserleaks.com/webgl                # P1.4 (vendor/renderer randomized & plausible)
- https://abrahamjuliot.github.io/creepjs/      # P2.1 (like-headless must be 0%)
- https://www.planetminecraft.com/account/sign_in/  # End-to-end smoke (Cloudflare must pass)
```

A camoufox session that returns clean verbatim verdicts on all six is indistinguishable from a regular human-on-Firefox at the per-browser layer.

## Evidence files
Same directory as this report: `T1.1-rebrowser.png`, `T1.2-browserscan.png`, `T1.3-pixelscan.png`, `T1.4-pixelscan-dev.png`, `T1.5-scrapfly.png`, `T1.6-sannysoft.png`, `T2.1-deviceandbrowserinfo.png`, `T2.3-incolumitas.png`, `T2.13-fingerprintcom.png`, `T3.1-creepjs.png`, `T5.1-peetws.png`. Full per-site verbatim verdicts in `REPORT.md`.
