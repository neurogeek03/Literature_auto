# Paper Pipeline — setup

Drop a paper into a private Slack channel → it becomes a self-contained Obsidian
literature note (full text as Markdown, a Sonnet key-points node, topic + related
links) with a summary posted back to Slack. Design + rationale: `PLAN.md`.
Orientation for Claude sessions: `CLAUDE.md`.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- Claude Code CLI, logged in (`claude` — the node step uses your subscription)
- An Obsidian vault (already: `/Users/marlenfaf/Desktop/UofT_PhD/literature_auto`)

## 1. Install

```
uv sync                     # creates .venv + uv.lock
cp config.example.yaml config.yaml   # already done; edit if needed
cp .env.example .env        # fill in tokens in step 2
```

Key `config.yaml` knobs: `fulltext.min_words` (sufficiency gate),
`related.top_k` / `min_similarity`, `node.claude_bin` (absolute path).

## 2. Create the Slack app (one time)

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

## 3. The channel

Create a private channel (e.g. `#papers`), then `/invite @your-app`. Get its ID
(channel details → bottom, `Cxxxxxxxx`) and put it in `config.yaml` →
`slack.channel_id`.

## 4. Test

```
# Offline core (no Slack): writes a note into the vault
uv run python -m src.process_paper /path/to/a/paper.pdf

# Live listener
uv run python -m src.slack_listener
```

Drop a PDF into the channel from your laptop, or forward one from your phone.
Send just a DOI/link and it fetches the open-access PDF when available; if it's
paywalled or only an abstract, it replies asking for the full PDF.

## 5. Autostart (survives reboots)

```
cp launchd/com.user.paperpipeline.listener.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.user.paperpipeline.listener.plist
# logs: .cache/listener.log  /  .cache/listener.err.log
```

The listener runs a **catch-up sweep** on start, so anything dropped while the
Mac was off is processed when it next comes on (see `PLAN.md` → Runtime &
availability).

## 6. NotebookLM podcast (optional)

```
uv sync --extra drive
```

Set `drive.enabled: true` + `drive.folder_id` in `config.yaml`, add a Google
`credentials.json` (Desktop OAuth client) to the repo root. On a successful run
the Slack reply then includes a link to stage the PDF for a NotebookLM notebook.
NotebookLM has no API — you still click *Generate* yourself.

## The `paper-node` skill

Lives at `~/.claude/skills/paper-node/SKILL.md`. It defines the key-point bullets
and the topic vocabulary. Edit it to tune extraction; the pipeline calls it via
`claude -p "/paper-node" --model claude-sonnet-4-6`.

## Topics

Topic hub notes are in `<vault>/topics/` (two-tier). The vocabulary is exactly
those filenames — add a stub there and it becomes assignable (also add it to the
skill's list so the model knows about it).
