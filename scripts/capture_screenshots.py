"""Capture dashboard screenshots for the documentation.

    docker compose --profile app up -d dashboard
    pip install -e ".[screenshots]"
    python scripts/capture_screenshots.py            # writes docs/images/dashboard_*.png

Uses a browser already installed on the machine (Microsoft Edge by default, no download); pass
--channel chrome or --channel chromium to use another. The dashboard must already be running.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

PAGES = ["Overview", "Country profile", "Relationships", "Pipeline and quality"]
OUT = Path("docs/images")


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def capture(url: str, out: Path, channel: str, pages: list[str], width: int, height: int) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel=channel)
        page = browser.new_page(viewport={"width": width, "height": height}, color_scheme="light")
        page.goto(url, wait_until="domcontentloaded")
        sidebar = page.locator('[data-testid="stSidebar"]')
        sidebar.wait_for(timeout=30_000)
        for name in pages:
            sidebar.get_by_text(name, exact=True).first.click()
            # While a script run is in progress Streamlit shows a "Stop" button in the toolbar.
            page.wait_for_timeout(500)  # let the run start before waiting for it to end
            page.wait_for_function(
                "!(document.querySelector('[data-testid=\"stToolbar\"]')?.innerText || '').includes('Stop')",
                timeout=60_000,
            )
            page.wait_for_timeout(2000)  # let charts finish drawing
            path = out / f"dashboard_{slug(name)}.png"
            page.screenshot(path=str(path))
            written.append(path)
            print(f"wrote {path}")
        browser.close()
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8501")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--channel", default="msedge", help="installed browser: msedge, chrome or chromium")
    parser.add_argument("--pages", nargs="+", default=PAGES, help="sidebar page names")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=950)
    args = parser.parse_args(argv)
    try:
        capture(args.url, args.out, args.channel, args.pages, args.width, args.height)
    except PlaywrightError as exc:
        print(f"capture failed: {exc}\nIs the dashboard running at {args.url}?", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
