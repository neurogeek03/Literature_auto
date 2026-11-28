---
name: setup-pipeline
description: Guide a new user (often a biologist, not a programmer) through setting up LitAuto (Literature Automation), this paper pipeline, on their own computer - Obsidian vault, Slack app, research topics, Claude skills, optional podcasts and Spotify, and the background service. Use when the user says "set me up", "install this", "help me set up LitAuto", "configure the paper pipeline", or is stuck on a setup step.
---

# setup-pipeline

You are helping someone set up LitAuto (Literature Automation), the paper pipeline in this repository, on their own
computer (macOS, Linux or Windows). Assume they are a scientist who may never have
used a terminal. Explain each step in one or two plain sentences, do the technical
work yourself, and only ask them for things only they can do (click in Slack,
choose a folder, pick topics, log in).

Never print or repeat their Slack tokens back to them. Never read `.env` aloud.

## The steps (same as `uv run python -m src.setup`, which they can also run themselves)

Run commands from the repository root. Use `uv run python ...` for everything.

0. **Dependencies.** Run `uv sync --extra podcast` (a few minutes the first time;
   tell them it's downloading the voice engine). If `uv` is missing, point them to
   https://docs.astral.sh/uv/getting-started/installation/ and wait.

1. **Claude Code works headless.** Run `claude -p "Reply with exactly: OK"`. If it
   doesn't print OK, ask them to type `claude` in a terminal once to log in.

2. **Obsidian vault.** Ask for the folder of their Obsidian vault (they can create an
   empty vault in Obsidian first). Check it exists. Ask for their email (Crossref and
   Unpaywall ask for one). Then:
   - If `config.yaml` doesn't exist: copy `config.example.yaml` to `config.yaml`.
   - Set values with the wizard's helper, e.g.
     `uv run python -c "from src.setup import update_config; update_config(['vault','path'], '\"/path/to/vault\"')"`
     and the same for `['metadata','crossref_mailto']` and `['metadata','unpaywall_email']`.

3. **Slack app.** Walk them through, one click at a time:
   1. https://api.slack.com/apps -> Create New App -> From a manifest -> their workspace
      -> paste `slack_manifest.yaml` (show them its contents) -> Create -> Install to Workspace.
   2. Basic Information -> App-Level Tokens -> Generate, scope `connections:write`.
   3. OAuth & Permissions -> copy the Bot User OAuth Token.
   4. In Slack: create a private channel, type `/invite @litauto`, copy the
      channel ID (click the channel name -> bottom of the About tab).
   Write `SLACK_BOT_TOKEN=...` and `SLACK_APP_TOKEN=...` into `.env` (keep other lines),
   set `['slack','channel_id']`, then verify with
   `uv run python -c "import os, src.config; from slack_sdk import WebClient; c=WebClient(token=os.environ['SLACK_BOT_TOKEN']); print(c.auth_test().headers.get('x-oauth-scopes'))"`.
   Required scopes: chat:write, reactions:write, files:read, files:write, groups:history.

4. **Skills and topics.** Copy every `skills/<name>/SKILL.md` to
   `~/.claude/skills/<name>/SKILL.md`. Then ask about their research: 2-6 broad themes,
   each with optional sub-themes and a few words of description. Write them into the
   `topics:` block of `config.yaml` in this form, then run
   `uv run python scripts/setup_topics.py` (it creates topic notes in the vault and
   syncs the skills):
   ```yaml
   topics:
     neuroscience:
       - {slug: hippocampus, desc: "hippocampal circuits, neurogenesis"}
     methods: []
   ```
   Slugs are lowercase-with-hyphens.

5. **Podcasts (optional, recommended on).** Explain: reply `podcast` in a paper's Slack
   thread to get a two-host audio episode. Set `['podcast','enabled']` to `true`/`false`.
   Spotify is optional and most people skip it. If they want it: they create a show at
   https://creators.spotify.com, give you the show URL (drop the `/episodes` ending),
   set `['podcast','spotify','show_url']` and `['podcast','spotify','enabled']`, run
   `uv run python -m playwright install chromium`, then tell them a browser will open and
   run `uv run python -m src.spotify_upload --login` (they log in, then close the window).

6. **Background service.** Run `uv run python -m src.service install`, then
   `uv run python -m src.service status`. Post a hello message to their channel and
   tell them to drop a paper PDF or DOI there to try it.

## If something fails

- Logs: `.cache/listener.log`, `.cache/listener.err.log`, `.cache/podcast.log`.
- "claude not found" in the logs: run `uv run python -m src.service install` again from
  a terminal where `claude` works (it records where Claude Code lives).
- Missing Slack permission: add it under OAuth & Permissions, reinstall the app.
- Linux: if it should run while logged out, `loginctl enable-linger $USER`.
