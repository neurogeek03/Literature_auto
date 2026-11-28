"""Guided setup: answers a few questions and does everything else (config,
Slack check, topics, Claude skills, podcast options, background service).
Safe to re-run: current values are shown, press Enter to keep them.

Run:  uv run python -m src.setup
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.yaml"
ENV = ROOT / ".env"


# ----- small helpers -----

def say(text: str = "") -> None:
    print(text)


def step(n: int, title: str) -> None:
    say(f"\n=== Step {n}: {title} ===")


def ask(question: str, current: str = "") -> str:
    hint = f" [{current}]" if current else ""
    answer = input(f"{question}{hint}: ").strip().strip('"').strip("'")
    return answer or current


def yes(question: str, default: bool) -> bool:
    answer = input(f"{question} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not answer else answer.startswith("y")


def set_yaml(text: str, keys: list[str], value: str) -> str:
    """Set a nested key in config.yaml text, keeping comments and layout."""
    lines = text.splitlines()
    start, indent = 0, 0
    for depth, key in enumerate(keys):
        pat = re.compile(rf"^{' ' * indent}{re.escape(key)}:(\s*)(.*)$")
        for i in range(start, len(lines)):
            m = pat.match(lines[i])
            if m:
                if depth == len(keys) - 1:
                    comment = re.search(r"\s+#.*$", m.group(2))
                    lines[i] = f"{' ' * indent}{key}: {value}" + (comment.group(0) if comment else "")
                    return "\n".join(lines) + "\n"
                start, indent = i + 1, indent + 2
                break
        else:
            if depth < len(keys) - 1:
                raise KeyError(".".join(keys))
            # Missing leaf (older config.yaml): add it right under its parent.
            lines.insert(start, f"{' ' * indent}{key}: {value}")
            return "\n".join(lines) + "\n"
    return text


def get_yaml(keys: list[str]) -> str:
    import yaml

    d = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    for k in keys:
        d = (d or {}).get(k) if isinstance(d, dict) else None
    return "" if d is None else str(d)


def update_config(keys: list[str], value: str) -> None:
    CONFIG.write_text(set_yaml(CONFIG.read_text(encoding="utf-8"), keys, value), encoding="utf-8")


def read_env() -> dict:
    env = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def write_env(env: dict) -> None:
    ENV.write_text("".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")


def quoted(s: str) -> str:
    return '"' + s.replace("\\", "/").replace('"', "") + '"'


# ----- steps -----

def check_claude() -> None:
    step(1, "Claude Code")
    exe = shutil.which("claude")
    if not exe:
        say("Claude Code isn't installed yet. Install it from")
        say("  https://docs.claude.com/en/docs/claude-code")
        say("then open a new terminal, type `claude` once to log in, and run this setup again.")
        sys.exit(1)
    say(f"Found Claude Code at {exe}. Checking you're logged in (a few seconds)...")
    r = subprocess.run([exe, "-p", "Reply with exactly: OK"], capture_output=True,
                       text=True, encoding="utf-8", timeout=180)
    if "OK" not in (r.stdout or ""):
        say("Claude Code didn't answer. Type `claude` in a terminal once to log in, then re-run setup.")
        sys.exit(1)
    say("Claude Code works.")


def setup_config() -> None:
    step(2, "Your notes")
    if not CONFIG.exists():
        shutil.copy(ROOT / "config.example.yaml", CONFIG)
    say("Notes are written into an Obsidian vault (a normal folder). Create one in")
    say("Obsidian first if you haven't, then paste its folder path here.")
    while True:
        vault = os.path.expanduser(ask("Vault folder", get_yaml(["vault", "path"])))
        if vault and Path(vault).is_dir():
            break
        say("That folder doesn't exist. Tip: drag the folder onto this window to paste its path.")
    update_config(["vault", "path"], quoted(str(Path(vault).resolve())))

    email = ask("Your email (sent to Crossref/Unpaywall when looking papers up)",
                get_yaml(["metadata", "crossref_mailto"]))
    update_config(["metadata", "crossref_mailto"], quoted(email))
    update_config(["metadata", "unpaywall_email"], quoted(email))


def setup_slack() -> None:
    step(3, "Slack")
    say("If you haven't made the Slack app yet:")
    say("  1. Open https://api.slack.com/apps -> Create New App -> From a manifest")
    say(f"  2. Pick your workspace and paste the contents of {ROOT / 'slack_manifest.yaml'}")
    say("  3. Create, then Install to Workspace")
    say("  4. Basic Information -> App-Level Tokens -> Generate (scope connections:write)")
    say("  5. In Slack, make a private channel and type /invite @litauto")
    env = read_env()
    env["SLACK_BOT_TOKEN"] = ask("Bot token (OAuth & Permissions page, starts xoxb-)", env.get("SLACK_BOT_TOKEN", ""))
    env["SLACK_APP_TOKEN"] = ask("App-level token (starts xapp-)", env.get("SLACK_APP_TOKEN", ""))
    write_env(env)

    from slack_sdk import WebClient

    client = WebClient(token=env["SLACK_BOT_TOKEN"])
    try:
        r = client.auth_test()
    except Exception as e:
        say(f"Slack rejected the bot token ({e}). Check it and re-run setup.")
        sys.exit(1)
    scopes = set((r.headers.get("x-oauth-scopes") or "").split(","))
    missing = {"chat:write", "reactions:write", "files:read", "files:write", "groups:history"} - scopes
    if missing:
        say(f"The app is missing permissions: {', '.join(sorted(missing))}.")
        say("Add them under OAuth & Permissions -> Bot Token Scopes, reinstall the app, re-run setup.")
        sys.exit(1)

    say("Channel ID: open the channel in Slack -> click its name -> bottom of the About tab (starts C).")
    while True:
        channel = ask("Channel ID", get_yaml(["slack", "channel_id"]))
        try:
            client.conversations_info(channel=channel)
            break
        except Exception:
            say("I can't see that channel. Check the ID and that you invited the bot (/invite @litauto).")
    update_config(["slack", "channel_id"], quoted(channel))
    say("Slack is connected.")


def setup_skills_and_topics() -> None:
    step(4, "Claude skills and your research topics")
    dest = Path.home() / ".claude" / "skills"
    for skill in (ROOT / "skills").iterdir():
        if (skill / "SKILL.md").exists():
            (dest / skill.name).mkdir(parents=True, exist_ok=True)
            shutil.copy(skill / "SKILL.md", dest / skill.name / "SKILL.md")
    say(f"Installed the pipeline's Claude skills into {dest}.")
    say("Now your topics: a few broad themes (e.g. neuroscience, methods) with optional sub-themes.")
    say("Papers get tagged with 1-3 of them and linked in your Obsidian graph.")
    subprocess.run([sys.executable, str(ROOT / "scripts" / "setup_topics.py")], cwd=ROOT)


def setup_podcasts() -> None:
    step(5, "Podcasts (optional)")
    say("Reply `podcast` in a paper's Slack thread to get a two-host audio episode about it.")
    on = yes("Turn podcasts on?", get_yaml(["podcast", "enabled"]) != "False")
    update_config(["podcast", "enabled"], "true" if on else "false")
    if not on:
        return
    try:
        import imageio_ffmpeg, soundfile  # noqa: F401
    except ImportError:
        say("Installing the voice engine (one-time, a few minutes)...")
        subprocess.run(["uv", "sync", "--extra", "podcast"], cwd=ROOT, check=True)

    say("\nEpisodes can also be published to your own Spotify for Creators show.")
    say("Most people skip this: episodes always arrive in Slack.")
    spotify = yes("Publish to Spotify too?", get_yaml(["podcast", "spotify", "enabled"]) == "True")
    update_config(["podcast", "spotify", "enabled"], "true" if spotify else "false")
    if spotify:
        say("Create your show at https://creators.spotify.com first, then open its Episodes page.")
        url = ask("Show URL", get_yaml(["podcast", "spotify", "show_url"]))
        update_config(["podcast", "spotify", "show_url"], quoted(url.removesuffix("/episodes")))
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], cwd=ROOT)
        say("A browser window will open: log in to Spotify for Creators, then close the window.")
        subprocess.run([sys.executable, "-m", "src.spotify_upload", "--login"], cwd=ROOT)


def setup_service() -> None:
    step(6, "Run in the background")
    say("The pipeline runs quietly in the background and starts when you log in.")
    if not yes("Install it now?", True):
        say("Skipped. Start it by hand with: uv run python -m src.slack_listener")
        return
    subprocess.run([sys.executable, "-m", "src.service", "install"], cwd=ROOT, check=True)

    from slack_sdk import WebClient

    WebClient(token=read_env()["SLACK_BOT_TOKEN"]).chat_postMessage(
        channel=get_yaml(["slack", "channel_id"]),
        text="LitAuto is set up. Drop a PDF, DOI or poster photo here to try it.",
    )
    say("Done! Check your Slack channel for a hello message.")


def main() -> None:
    say("LitAuto setup. Press Enter to keep a value shown in [brackets].")
    check_claude()
    setup_config()
    setup_slack()
    setup_skills_and_topics()
    setup_podcasts()
    setup_service()


if __name__ == "__main__":
    main()
