"""Upload an episode to Spotify for Creators. There is no upload API, so:
1. Playwright drives Chrome with a saved login (persistent profile).
2. If that fails, headless Claude in Chrome (`claude -p --chrome`) tries the same.
Both check the episode list first so an episode is never uploaded twice.

One-time login:  uv run python -m src.spotify_upload --login
"""
from __future__ import annotations

import argparse
import logging
import subprocess
from pathlib import Path

from .config import CONFIG, claude_bin, resolve_path

HOME = "https://creators.spotify.com/"
PROFILE = ".cache/spotify_profile"


class LoginExpired(Exception):
    pass


def _cfg() -> dict:
    return CONFIG["podcast"]["spotify"]


def _browser(p, headless: bool):
    """Installed Google Chrome if there is one, else Playwright's own Chromium
    (`playwright install chromium`)."""
    profile = str(resolve_path(PROFILE))
    try:
        return p.chromium.launch_persistent_context(profile, channel="chrome", headless=headless)
    except Exception:
        return p.chromium.launch_persistent_context(profile, headless=headless)


def login() -> None:
    """Open Chrome so the user can log in once; the session is saved in PROFILE."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = _browser(p, headless=False)
        page = ctx.new_page()
        page.goto(HOME)
        print("Log in to Spotify for Creators in the browser window, then close the window.")
        page.wait_for_event("close", timeout=0)
        ctx.close()


def _playwright_upload(mp3: Path, title: str, description: str) -> str:
    from playwright.sync_api import sync_playwright

    show = _cfg()["show_url"].rstrip("/")
    key = title[:60]  # the episode list may cut long titles

    def open_page(url: str) -> None:
        page.goto(url)
        page.wait_for_timeout(6000)  # the app never goes network-idle
        if "login" in page.url or "accounts.spotify.com" in page.url:
            raise LoginExpired("Spotify login expired; run `python -m src.spotify_upload --login`")

    def clear_cookie_banner() -> None:
        page.evaluate("document.getElementById('onetrust-consent-sdk')?.remove()")

    with sync_playwright() as p:
        ctx = _browser(p, headless=True)
        page = ctx.new_page()
        page.set_default_timeout(60_000)

        open_page(f"{show}/episodes")
        if page.get_by_text(key).count():
            # Could be published or a leftover draft; the Claude fallback tells them apart.
            ctx.close()
            raise RuntimeError("an episode with this title is already listed")

        # Upload -> Details -> Review, as in the web wizard.
        open_page(f"{show}/episode/wizard")
        clear_cookie_banner()
        page.locator("input[type=file]").first.set_input_files(str(mp3))
        page.locator("#title-input").wait_for(timeout=600_000)  # appears once the upload is in
        page.locator("#title-input").fill(title)
        page.locator("[name=description]").first.click()
        page.keyboard.insert_text(description)
        clear_cookie_banner()
        page.get_by_role("button", name="Next").click()
        page.locator("label[for=publish-date-now]").click()  # the radio itself is hidden
        page.get_by_role("button", name="Publish").click()
        page.wait_for_timeout(10_000)

        open_page(f"{show}/episodes")
        ok = page.get_by_text(key).count() > 0
        ctx.close()
    if not ok:
        raise RuntimeError("published but the episode isn't listed yet")
    return "Playwright"


def _claude_chrome_upload(mp3: Path, title: str, description: str) -> str:
    """Fallback: Claude drives the user's own Chrome (needs Chrome open)."""
    show = _cfg()["show_url"]
    prompt = (
        "Use the Claude in Chrome tools. Upload one podcast episode to Spotify for Creators.\n"
        f"Show page: {show}\n"
        f"Audio file: {mp3}\n"
        f"Title: {title}\n"
        f"Description: {description}\n\n"
        "Steps: open a new tab at the show's episode list. If a PUBLISHED episode with this "
        "title exists, stop and reply ALREADY. If a DRAFT with this title exists (an earlier "
        "attempt), open it, make sure title and description are set, and publish it now. "
        "Otherwise create a new episode, upload the audio file, set the title and description, "
        "and publish it now. Confirm it shows as published in the episode list. Do not change "
        "anything else. Reply with one line: OK, ALREADY, or ERROR: <reason>."
    )
    proc = subprocess.run(
        [claude_bin(), "-p", "--chrome", "--model", CONFIG["node"]["model"],
         "--allowedTools", "mcp__claude-in-chrome__*"],
        input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=900,
    )
    out = (proc.stdout or "").strip().splitlines()
    last = out[-1] if out else (proc.stderr or "no output").strip()[:200]
    if last.startswith("OK"):
        return "Claude in Chrome"
    if last.startswith("ALREADY"):
        return "already there"
    raise RuntimeError(f"Claude in Chrome: {last}")


def upload(mp3: Path, title: str, description: str) -> str:
    """Returns how it was uploaded; raises if both routes fail."""
    if not _cfg().get("show_url"):
        raise RuntimeError("podcast.spotify.show_url is not set in config.yaml")
    try:
        return _playwright_upload(mp3, title, description)
    except Exception as first:  # incl. LoginExpired: the user's own Chrome may still be logged in
        logging.warning("Playwright upload failed, trying Claude in Chrome: %s", first)
        try:
            return _claude_chrome_upload(mp3, title, description)
        except Exception as second:
            raise RuntimeError(f"Playwright: {first}; {second}") from second


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--login", action="store_true")
    if ap.parse_args().login:
        login()
