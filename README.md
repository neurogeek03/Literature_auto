# Literature automation
### Save, link and query all literature you come across through one Slack message 

**Drop a paper in Slack → get a structured, cross-linked Obsidian literature note back.** A self-hosted, mostly-deterministic pipeline that turns a PDF, a DOI/URL, or a phone photo of a conference poster into a clean Markdown note wired into a topic knowledge graph — with a single, tightly-scoped LLM step doing the only part that actually needs a model.

<!-- ![demo](docs/demo.gif) — add once you record the demo GIF -->
<!-- TODO: record a 10–15s screen capture: drop a PDF in Slack → note appears in Obsidian with topic links. Save as docs/demo.gif and whitelist it in .gitignore. -->

---

## Why it's an agent system

This is an autonomous, event-driven agent with a deliberately **small** LLM surface. The design philosophy is that an agent system is more trustworthy when the model is fenced into the one node that genuinely needs judgment and everything else is deterministic plumbing.

- **Shared state as the control plane.** A single `state.json` holds the last-processed Slack timestamp. Every entry point — the live listener and the catch-up reconciler — reads and advances the same cursor, so the system has one authoritative notion of "what's done."
- **Slack as a durable queue + failure recovery.** Messages aren't held in memory. Slack *is* the queue. When the machine is asleep or offline, nothing is lost: on startup (and on a timer) `catchup.py` replays channel history since the state cursor through the **exact same handler** the live listener uses, and an emoji-reaction marker dedupes anything already processed. Identical treatment online or recovered.
- **A hard determinism boundary.** Metadata resolution, full-text extraction, code-link detection, embedding/related-note ranking, preprint↔published matching, and note rendering are all pure functions: same input → same output, no model. **Exactly one node is an LLM agent** — a headless Claude Code call (`node.py` / `poster_node.py`) that reads the full text (or the poster image) and returns key-point bullets plus 1–3 topic tags from a fixed vocabulary. One call, two structured outputs, nothing else delegated to the model.
- **Semantic cross-linking with local embeddings.** `related.py` uses [fastembed](https://github.com/qdrant/fastembed) (BAAI `bge-small-en-v1.5` by default) + cosine similarity to find the most related existing notes and wire them into the new one. The vault index is cached and rebuilt incrementally — only new/changed notes are re-embedded.
- **Preprint↔published de-duplication.** When a published paper arrives that supersedes a preprint already in the vault, the pipeline detects it — first via DOI relations (Crossref `has-preprint` / OpenAlex), then via a tightly-gated fallback (exact normalized title **and** matching first-author surname) — and **surgically updates the existing note in place** (flips `preprint`→`peer-reviewed`, refreshes metadata, renames the citekey) while preserving the model-generated summary and your own reading notes.
- **Phone-photo poster/slide capture.** Snap a conference poster or a talk slide, drop the photo in Slack, and a vision variant of the same node (`--allowedTools Read` pre-authorizes exactly the one tool the headless run needs) extracts title/authors/venue plus key points — no DOI, no PDF required.
- **Fully self-hosted, bring-your-own-keys.** Runs on your own machine against your own Slack app, your own Obsidian vault, and your own Claude Code subscription (via `claude -p`, not the API). No managed service, no third-party data egress beyond public metadata lookups (Crossref/OpenAlex/Unpaywall) and the optional Google Drive staging step.

## Architecture

```mermaid
flowchart TD
    SLACK["Slack channel<br/>(durable queue)"]
    STATE[("state.json<br/>cursor")]
    LISTEN["slack_listener.py<br/>Socket Mode"]
    CATCH["catchup.py<br/>replays missed drops"]

    SLACK --> LISTEN
    SLACK --> CATCH
    LISTEN <--> STATE
    CATCH <--> STATE
    LISTEN --> META
    CATCH --> META

    subgraph ORCH["process_paper.py &mdash; orchestrator (deterministic except one node)"]
        direction TB
        META["metadata.py<br/>DOI to Crossref / OpenAlex"]
        FULL["fulltext.py<br/>PDF to Markdown + gate"]
        CODE["codelinks.py<br/>code / data links"]
        REL["related.py<br/>fastembed + cosine"]
        NODE["node.py<br/>HEADLESS CLAUDE CODE<br/>key points + topic tags"]
        REND["note_render.py<br/>template + preprint/published dedup"]
        META --> FULL --> CODE --> REL --> NODE --> REND
    end

    IMG["Image drop<br/>poster / slide photo"] --> PPOST["process_poster.py"]
    PPOST --> PNODE["poster_node.py<br/>VISION LLM NODE"]
    PNODE --> REND

    REND --> VAULT[("Obsidian vault note<br/>cross-linked topic graph")]
    REND --> POST["slack_post.py<br/>reply card / prompt / error"]
    REND -. optional .-> DRIVE["drive.py<br/>Google Drive / NotebookLM"]

    classDef llm fill:#2E5D43,stroke:#17281F,color:#F7F4EB;
    class NODE,PNODE llm;
```

**Module map**

| Module | Role | Deterministic? |
|--------|------|----------------|
| `slack_listener.py` | Socket Mode listener; watches the channel for PDFs / DOI-URL messages | yes |
| `catchup.py` | Replays drops missed while offline through the same handler | yes |
| `process_paper.py` | Orchestrates the paper path end-to-end | yes (except node) |
| `process_poster.py` | Orchestrates the image (poster/slide) path | yes (except node) |
| `metadata.py` | DOI → Crossref/OpenAlex → fields + citekey; preprint detection | yes |
| `fulltext.py` | PDF → Markdown + a word-count sufficiency gate | yes |
| `codelinks.py` | Regex extraction of code/data availability | yes |
| `related.py` | fastembed embeddings + cosine similarity; incremental index | yes |
| `node.py` | **Headless Claude Code**: full text → key points + topic tags | **no (LLM)** |
| `poster_node.py` | **Headless Claude Code (vision)**: image → fields + key points + tags | **no (LLM)** |
| `note_render.py` | Template → vault note; preprint↔published in-place update | yes |
| `conference.py` | Parses a typed caption into a whitelisted conference tag | yes |
| `drive.py` | Optional Google Drive staging for NotebookLM | yes |
| `slack_post.py` | Builds every reply (success / prompt / error) | yes |
| `build_index.py` | CLI for a full manual re-embed of the vault | yes |
| `config.py` | Loads `config.yaml` + `.env`; single source of paths/settings | — |

## Features

- Slack-native intake: drop a PDF, paste a DOI/URL, or snap a poster photo.
- Deterministic metadata (Crossref/OpenAlex), clean citekeys, OA link resolution.
- Full text extracted to Markdown with a sufficiency gate that asks for the full PDF when a page is too thin.
- Local semantic cross-linking into an Obsidian topic knowledge graph.
- Preprint→published supersession that updates notes in place without losing your edits.
- Code/data availability auto-extraction.
- Offline-safe: anything dropped while the machine is off is reconciled on wake.
- Runs headless on macOS via `launchd` (listener + timer-driven catch-up).

## Setup

Requires Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), and the [Claude Code CLI](https://docs.claude.com/en/docs/claude-code) on your PATH.

```bash
git clone <your-fork-url> paper-pipeline
cd paper-pipeline
uv sync                      # core deps
# uv sync --extra drive      # optional: Google Drive / NotebookLM staging

# 1. Secrets
cp .env.example .env         # fill in SLACK_BOT_TOKEN and SLACK_APP_TOKEN

# 2. Config
cp config.example.yaml config.yaml   # set vault.path to your Obsidian vault

# 3. Topic taxonomy (creates your topic stubs + graph colors)
uv run python scripts/setup_topics.py
```

**Slack app** (about 5 minutes):
1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest** → pick your workspace → paste [`slack_manifest.yaml`](slack_manifest.yaml) → **Create** → **Install to Workspace**.
2. **Basic Information → App-Level Tokens → Generate**, with scope `connections:write`. Copy the `xapp-…` token into `.env` as `SLACK_APP_TOKEN`.
3. **OAuth & Permissions**: copy the Bot User OAuth Token (`xoxb-…`) into `.env` as `SLACK_BOT_TOKEN`.
4. In Slack, create a private channel and type `/invite @paper-pipeline`. Put its channel ID (channel details, bottom of the About tab) into `config.yaml` → `slack.channel_id`.

Each person runs their own copy: the app only forwards messages to the listener on your own Mac, which writes to your vault with your Claude login.

## Usage

```bash
# Live listener (foreground)
uv run python -m src.slack_listener

# Reconcile anything missed while offline
uv run python -m src.catchup

# Process a file directly, no Slack (handy for testing)
uv run python -m src.process_paper /path/to/paper.pdf --doi 10.xxxx/yyyy
uv run python -m src.process_poster /path/to/poster.jpg --prompt "ISMB 26 — poster by X lab"

# Full manual re-embed of the vault index
uv run python -m src.build_index

# Podcast of a paper note (normally triggered by replying "podcast" in its Slack thread)
uv sync --extra drive --extra podcast
uv run python -m src.podcast "/path/to/vault/@Author_Year.md" --focus "the statistics" --no-upload
uv run python -m src.spotify_upload --login   # one-time Spotify for Creators login
```

In Slack, reply in a paper's thread: `podcast`, `podcast short`, `podcast 15 min critique`,
or `podcast focus on the sex differences`. Needs the `files:write` bot scope to post the MP3.

For unattended operation on macOS, the `launchd/` folder contains sample `.plist` files for the listener and the catch-up timer — **edit the absolute paths in them to match where you cloned the repo** before loading with `launchctl`.

## License

MIT. See [LICENSE](LICENSE).
