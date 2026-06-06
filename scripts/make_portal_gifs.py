#!/usr/bin/env python3
"""Generate showcase GIFs of the researcher portal workflow (sign in → key → schema).

Drives the real ``/portal`` page in headless Chromium with Playwright, captures
frames through the flow, and assembles them into animated GIFs with Pillow.

Output (in ``docs/gifs/``):
    portal-full.gif      the whole workflow
    portal-1-login.gif   the sign-in form (with typing)
    portal-2-key.gif     the issued API key
    portal-3-schema.gif  the rendered dataset schema

Setup:
    pip install -r requirements-dev.txt
    python -m playwright install chromium   # or rely on a cached browser

If Playwright can't find its pinned browser it falls back to any Chromium under
PLAYWRIGHT_BROWSERS_PATH / the default cache.

Usage:
    python scripts/make_portal_gifs.py
"""
from __future__ import annotations

import glob
import os
from io import BytesIO
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

from _demo_server import ROOT, running_server

OUT_DIR = ROOT / "docs" / "gifs"
VIEW = {"width": 820, "height": 660}
GIF_COLORS = 96

NAME = "Dr. Ada Lovelace"
EMAIL = "ada@royalsociety.org"


# ── Chromium discovery ────────────────────────────────────────────────────────

def _find_chrome() -> str | None:
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH", ""), os.path.expanduser("~/.cache/ms-playwright")]
    patterns = ("chromium-*/chrome-linux/chrome", "chromium_headless_shell-*/chrome-linux/headless_shell")
    for root in roots:
        if not root:
            continue
        for pat in patterns:
            hits = sorted(glob.glob(os.path.join(root, pat)))
            if hits:
                return hits[-1]
    return None


def _launch(p):
    """Launch Chromium, falling back to any cached build if the pinned one is missing."""
    try:
        return p.chromium.launch(args=["--no-sandbox"])
    except Exception:
        exe = _find_chrome()
        if not exe:
            raise
        print(f"using cached chromium at {exe}")
        return p.chromium.launch(executable_path=exe, args=["--no-sandbox"])


# ── Capture ───────────────────────────────────────────────────────────────────

def _capture(base: str) -> list[tuple[Image.Image, int, str]]:
    """Walk the portal flow, returning (frame, duration_ms, step) tuples."""
    frames: list[tuple[Image.Image, int, str]] = []

    with sync_playwright() as p:
        browser = _launch(p)
        page = browser.new_page(viewport=VIEW, device_scale_factor=1)

        def shot(ms: int, step: str) -> None:
            img = Image.open(BytesIO(page.screenshot())).convert("RGB")
            frames.append((img, ms, step))

        page.goto(f"{base}/portal")
        page.wait_for_selector("#name")
        page.wait_for_timeout(300)
        shot(1300, "login")  # landing

        # Type the name, then the email — a couple of chars per frame for a typing feel.
        for i in range(2, len(NAME) + 1, 2):
            page.fill("#name", NAME[:i])
            shot(70, "login")
        page.fill("#name", NAME)
        page.click("#email")
        for i in range(2, len(EMAIL) + 1, 2):
            page.fill("#email", EMAIL[:i])
            shot(60, "login")
        page.fill("#email", EMAIL)
        shot(1000, "login")  # form filled

        # Submit → the key is issued.
        page.click("#submit")
        page.wait_for_selector("#key-card:not(.hidden)")
        page.wait_for_function("document.querySelector('#apikey').textContent.length > 0")
        page.eval_on_selector("#key-card", "el => el.scrollIntoView({block: 'center'})")
        page.wait_for_timeout(300)
        shot(2000, "key")  # API key revealed

        # The schema loads with the new key.
        page.wait_for_selector("#schema-card:not(.hidden)")
        page.wait_for_function("document.querySelector('#tables').children.length > 0")
        page.eval_on_selector("#schema-card", "el => el.scrollIntoView({block: 'start'})")
        page.wait_for_timeout(300)
        shot(2000, "schema")  # fields + first tables
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(300)
        shot(2600, "schema")  # tables / columns

        browser.close()

    return frames


# ── GIF assembly ──────────────────────────────────────────────────────────────

def _build_palette(imgs: list[Image.Image]) -> Image.Image:
    """One palette covering the whole clip, so frames share indices and diff well."""
    picks = [imgs[0], imgs[len(imgs) // 2], imgs[-1]]
    canvas = Image.new("RGB", (max(i.width for i in picks), sum(i.height for i in picks)))
    y = 0
    for i in picks:
        canvas.paste(i, (0, y))
        y += i.height
    return canvas.quantize(colors=GIF_COLORS, method=Image.MEDIANCUT)


def _save_gif(items: list[tuple[Image.Image, int]], path: Path) -> None:
    imgs = [im for im, _ in items]
    durations = [ms for _, ms in items]
    durations[-1] = max(durations[-1], 1800)  # hold the final frame
    # Shared palette + disposal=1 → Pillow encodes only each frame's changed
    # region, so the near-identical typing frames cost almost nothing.
    pal = _build_palette(imgs)
    paletted = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in imgs]
    paletted[0].save(
        path,
        save_all=True,
        append_images=paletted[1:],
        duration=durations,
        loop=0,
        optimize=True,
        disposal=1,
    )
    print(f"  wrote {path.name}  ({len(imgs)} frames, {path.stat().st_size // 1024} KB)")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with running_server() as base:
        frames = _capture(base)

    # Full workflow.
    _save_gif([(im, ms) for im, ms, _ in frames], OUT_DIR / "portal-full.gif")

    # Per-step clips.
    steps = [("login", "portal-1-login"), ("key", "portal-2-key"), ("schema", "portal-3-schema")]
    for tag, fname in steps:
        items = [(im, ms) for im, ms, step in frames if step == tag]
        if items:
            _save_gif(items, OUT_DIR / f"{fname}.gif")

    print(f"\nDone → {OUT_DIR.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
