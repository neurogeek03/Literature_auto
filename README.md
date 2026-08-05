# Paper Pipeline — setup

Drop a paper into a private Slack channel → it becomes a self-contained Obsidian
literature note (full text as Markdown, a Sonnet key-points node, topic + related
links) with a summary posted back to Slack. Drop a photo of a conference poster
or a talk slide instead, and the same channel produces a lighter note (the image
embedded, 3-5 key-point bullets from a vision read, same topic graph) — no PDF
or DOI needed. Design + rationale: `PLAN.md`. Orientation for Claude sessions:
`CLAUDE.md`.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- Claude Code CLI, logged in (`claude` — the node step uses your subscription)
- The `paper-node` skill installed at `~/.claude/skills/paper-node/SKILL.md`
  (and `poster-node` at `~/.claude/skills/poster-node/SKILL.md` for poster/slide
  drops). Without them `process_paper` runs but produces **no AI summary node**.
  See [The `paper-node` skill](#the-paper-node-skill) below.
- **Your own Obsidian vault.** Create an empty vault in Obsidian (or just an empty
  folder), then set its absolute path in `config.yaml` → `vault.path` (step 1).
  The example config ships with a placeholder — you must replace it.

## 1. Install

```
uv sync                     # creates .venv + uv.lock
cp config.example.yaml config.yaml   # then edit vault.path to your own vault
cp .env.example .env        # fill in tokens in step 2
```

Edit `config.yaml` and set `vault.path` to the absolute path of **your** Obsidian
vault (the example ships a `/ABSOLUTE/PATH/TO/YOUR/obsidian-vault` placeholder).
Also set the `metadata` emails to your own. Other knobs:
`fulltext.min_words` (sufficiency gate), `related.top_k` / `min_similarity`,
`node.claude_bin` (absolute path).

## 2. Define your themes

```
python scripts/setup_topics.py
```

Every note is tagged with 1-3 **topics** from a vocabulary that is *yours* — not
a fixed list. This prompts you for your own top-level themes and optional
sub-themes, then does everything downstream from that one definition:

- writes the taxonomy into `config.yaml` → `topics:` (the single source of truth),
- creates the topic stub notes in your vault (`topics/*.md`),
- syncs the theme list both extraction skills pick from (paper + poster),
- colors the Obsidian graph — **one color per top-level theme, lighter shades of
  that color for its sub-themes**.

It's additive and idempotent: re-run `python scripts/setup_topics.py --add` any
time to add more themes; nothing is deleted. (Already have a vault full of topic
stubs? `--import-vault` seeds the config from them instead of prompting.)

## 3. Create the Slack app (one time)

1. https://api.slack.com/apps → **Create New App** → *From scratch*.
2. **Socket Mode** → enable → generate an **App-Level Token** with scope
   `connections:write`. Copy it (`xapp-…`) → `.env` as `SLACK_APP_TOKEN`.
3. **OAuth & Permissions** → Bot Token Scopes:
   `chat:write`, `files:read`, `reactions:write`, and channel history for a
   **private** channel: `groups:history` (use `channels:history` for a public
   one). Install to workspace → copy **Bot User OAuth Token** (`xoxb-…`) →
   `.env` as `SLACK_BOT_TOKEN`.
4. **Event Subscriptions** → enable → *Subscribe to bot events*: `message.groups`
   (private channel) or `message.channels` (public). Reinstall if prompted.

## 4. The channel

Create a private channel (e.g. `#papers`), then `/invite @your-app`. Get its ID
(channel details → bottom, `Cxxxxxxxx`) and put it in `config.yaml` →
`slack.channel_id`.

## 5. Test

```
# Offline core (no Slack): writes a note into the vault
uv run python -m src.process_paper /path/to/a/paper.pdf

# Offline core, poster/slide photo
uv run python -m src.process_poster /path/to/a/poster.jpg --prompt "ISMB 26"

# Live listener
uv run python -m src.slack_listener
```

Drop a PDF into the channel from your laptop, or forward one from your phone.
Send just a DOI/link and it fetches the open-access PDF when available; if it's
paywalled or only an abstract, it replies asking for the full PDF.

## 6. Autostart (survives reboots)

```
cp launchd/com.user.paperpipeline.listener.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.paperpipeline.listener.plist
# logs: .cache/listener.log  /  .cache/listener.err.log
```

The listener runs a **catch-up sweep** on start, so anything dropped while the
Mac was off is processed when it next comes on (see `PLAN.md` → Runtime &
availability).

## 7. NotebookLM podcast (optional)

```
uv sync --extra drive
```

Set `drive.enabled: true` + `drive.folder_id` in `config.yaml`, add a Google
`credentials.json` (Desktop OAuth client) to the repo root. On a successful run
the Slack reply then includes a link to stage the PDF for a NotebookLM notebook.
NotebookLM has no API — you still click *Generate* yourself.

## Browser-fetch fallback — Chrome settings (Claude-in-Chrome)

*For the in-progress browser-fetch feature (see `IMPROVEMENTS.md` #7): when a
shared link/DOI is bot-blocked (bioRxiv, Elsevier, paywalled publishers) and the
deterministic strategies fail, the pipeline drives your real logged-in Chrome via
Claude-in-Chrome to fetch the PDF.* For that to run **unattended**, Chrome must
save PDFs to disk with **no dialogs**. Two settings are required — both matter:

1. **Ask where to save each file before downloading → OFF.**
   `chrome://settings/downloads` — otherwise a native macOS "Save As" Finder sheet
   opens on every download. That sheet is an OS-level dialog *outside* the web
   page, so Claude-in-Chrome cannot see or confirm it and the download hangs
   forever.
2. **PDF documents → Download PDFs** (not "Open PDFs in Chrome").
   `chrome://settings/content/pdfDocuments` (Settings → Privacy and security →
   Site settings → Additional content settings → PDF documents). Without this a
   PDF link just *opens* in Chrome's inline viewer; its viewer download (↓) button
   is a "Save As" that **always** shows the Finder sheet regardless of setting #1.
   Selecting "Download PDFs" makes a PDF link a true *download*, which respects
   setting #1 and saves silently.

With both set, navigating to a `.full.pdf` URL writes straight to **`~/Downloads`**
(Claude-in-Chrome is scoped to that folder) with zero prompts — validated live on
bioRxiv `10.64898/2026.07.30.741795v1`. The pipeline will look in `~/Downloads`
for the fetched file and move it into the work dir.

## The `paper-node` skill

The AI summary node comes from a Claude Code **skill** that must live in your home
Claude config, not the repo. Install both (papers + posters/slides) once:

```
mkdir -p ~/.claude/skills
cp -R skills/paper-node ~/.claude/skills/
cp -R skills/poster-node ~/.claude/skills/
```

Reference copies are vendored in this repo under `skills/`. Without them installed
at `~/.claude/skills/paper-node/SKILL.md` (and `poster-node/`), `process_paper`
still runs but writes **no key-points node** — the most common "it worked but
there's no summary" gotcha.

`paper-node` defines the key-point bullets and the topic vocabulary. Edit the
installed copy to tune extraction; the pipeline calls it via
`claude -p "/paper-node" --model claude-sonnet-4-6`. Its theme list lives between
`<!-- TOPICS:auto -->` markers and is regenerated by `scripts/setup_topics.py` —
don't hand-edit that region (see below).

## Topics

Your theme taxonomy is defined once in `config.yaml` → `topics:` (single source of
truth) and materialized by `python scripts/setup_topics.py` into: the
`<vault>/topics/*.md` stub notes, the vocabulary both skills pick from, and the
graph colors (one color per top-level theme, shades for sub-themes). To change
your themes, run `setup_topics.py --add` (or edit the `topics:` block and re-run)
rather than editing stubs or skills by hand.
