"""
Test script to verify humanized mouse cursor movement in Camoufox.
Launches the built binary, navigates to the button click test page,
and clicks randomly spawning buttons with humanize + showcursor enabled.
"""

import json
import os
from playwright.sync_api import sync_playwright

BINARY = "/Users/dev345/code/kfirfer/camoufox/camoufox-146.0.1-beta.25/obj-aarch64-apple-darwin/dist/Camoufox.app/Contents/MacOS/camoufox"
URL = "https://camoufox.com/tests/buttonclick"
NUM_CLICKS = 15

config = json.dumps({"humanize": True, "showcursor": True})

# Chunk config into env vars (macOS max 32767 per chunk)
env = os.environ.copy()
env["CAMOU_CONFIG_1"] = config

with sync_playwright() as p:
    browser = p.firefox.launch(
        executable_path=BINARY,
        headless=False,
        env=env,
    )
    page = browser.new_page()
    page.goto(URL, wait_until="domcontentloaded")
    page.wait_for_timeout(1000)

    for i in range(NUM_CLICKS):
        btn = page.wait_for_selector("button", timeout=5000)
        btn.click()
        print(f"Click {i + 1}/{NUM_CLICKS} done")
        page.wait_for_timeout(300)

    print(f"\nAll {NUM_CLICKS} clicks completed. Keeping browser open for 10s to observe...")
    page.wait_for_timeout(10000)
    browser.close()

print("Test finished.")
