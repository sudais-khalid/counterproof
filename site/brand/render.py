"""Render site/brand/card.html to the social card and the README banners (needs Playwright
with Chromium; any Python that has it will do):

    python site/brand/render.py
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUTPUTS = {
    "og": ROOT / "site" / "static" / "og.png",
    "banner-light": ROOT / "docs" / "assets" / "banner-light.png",
    "banner-dark": ROOT / "docs" / "assets" / "banner-dark.png",
}

with sync_playwright() as p:
    browser = p.chromium.launch()
    for variant, out in OUTPUTS.items():
        page = browser.new_page(device_scale_factor=1 if variant == "og" else 2)
        page.goto(f"{(HERE / 'card.html').as_uri()}?variant={variant}", wait_until="networkidle")
        page.wait_for_timeout(500)
        out.parent.mkdir(parents=True, exist_ok=True)
        page.locator("#card").screenshot(path=str(out))
        print(out.relative_to(ROOT))
    browser.close()
