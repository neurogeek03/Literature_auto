# Paper Pipeline — Plan

_Last updated: 2026-07-06_

## Goal

A **deterministic, unattended** pipeline: I drop or forward a paper (PDF or DOI)
from my phone or laptop into one private Slack channel, and with no further
action the paper becomes a self-contained Obsidian literature note — full text in
Markdown, a bulleted key-points "node" written by Sonnet, related notes linked
into the knowledge graph, and a summary posted back to Slack. Built once, runs
forever. Zotero is **not** involved; it stays my separate manual habit for the
minority of papers I read deeply.

## Decisions (locked)

| Question | Decision |
|---|---|
| Intake | **Slack upload** into a **private** channel (just me + the app). PDF or DOI/URL. |
| Storage model | **Obsidian-only.** No Zotero, no stored PDF. Full paper kept as **Markdown**. |
| Full text | **Inside the note, collapsed** section (one file per paper). |
| Note engine | Deterministic skeleton + one **headless Claude Code (Sonnet)** call for the node. |
| Node format | **Bullets only** (5–10 key points): main findings, key innovation, notable author/lab if relevant. Plus **1–3 topic wikilinks** from the fixed vocabulary (§ Knowledge graph) emitted for the Connections block. One `paper-node` call, two outputs. |
| Graph source | **Obsidian vault** (`/Users/marlenfaf/Desktop/UofT_PhD/literature_auto`). |
| Podcast | **NotebookLM, manual, opt-in.** Only when I drop the actual PDF and want one. |

## Why Obsidian-only (storage)

Measured 2026-07-06: Zotero storage = **2.7 GB**, 281 PDFs, **avg 8.40 MB/PDF**.
A note with full text in Markdown ≈ 66 KB. Even keeping the full text of every
paper:

| Papers | PDFs today (8.4 MB ea) | Full-text MD + note (~66 KB ea) |
|---|---|---|
| 281 (now) | 2.36 GB | ~18 MB |
| 1,000 | 8.2 GB | ~66 MB |
| 2,000 | 16.4 GB | ~130 MB |

≈130× less space while keeping every paper's searchable text. Also: instant
iCloud/git sync, greppable, future-proof, no DB dependency, fewer fragile parts.

## Vault + template

- Vault: `/Users/marlenfaf/Desktop/UofT_PhD/literature_auto`
- Template: `literature_auto/ZI_Template.md` (Zotero Integration / Phelan style:
  frontmatter category/tags/citekey/status/dateread, Connections callout with
  Contribution + Related, Abstract callout, Citation, Annotations, Metadata).
- We reuse this **structure** but: replace the PDF file-link with the **DOI URL**,
  drop the Zotero annotation macro, add a **Key Points (node)** block and a
  **collapsed full-text** section. Citekeys generated as `Author_Year` (pandoc
  convention) from metadata — no Better BibTeX needed.

## Knowledge graph — topic taxonomy

The vault uses **topic index notes** (Option B), not tags. Each topic is a `.md`
stub under `literature_auto/topics/`. Papers link to their topics via wikilinks
in the Connections block; Tier-2 stubs link back to their Tier-1 parent. Topics
therefore appear as hub nodes in Obsidian's graph view, with papers clustering
around them and cross-topic papers sitting visually between hubs.

### Vocabulary (two-tier)

The taxonomy is **user-defined**: it lives in `config.yaml` → `topics:` (single
source of truth) and is materialized into the topic stubs, the skill vocabulary,
and the graph colors by `scripts/setup_topics.py` (`--add` to extend it). The
table below is the author's own example taxonomy — a new user runs
`setup_topics.py` and gets their own instead.

| Tier 1 | Tier 2 subcategories |
|---|---|
| `neuroscience` | `general-neuroscience`; brain-region nodes (future) |
| `psychiatry-mental-health` | `human-psychiatry` · `animal-psychiatry` |
| `womens-health` | *(standalone — no subcategories yet)* |
| `single-cell` | `cell-types` · `atlas-building` · `single-cell-biology` · `perturbation-sc`; human-sc / nonhuman-sc / brain-region-sc (future) |
| `spatial-transcriptomics` | `st-methods` · `st-biology` |
| `methods` | `data-pipelines` · `foundation-models` · `general-methods` |
| `reviews` | *(flat bucket — no subcategories yet)* |

`perturbation-sc` covers both experimental perturbation studies (Perturb-seq /
CROP-seq) and foundation-model papers applied to perturbation prediction; papers
that are primarily about the ML architecture cross-link to `methods/foundation-models`.

### How it works

- Stubs live at `literature_auto/topics/<name>.md`. Each Tier-2 stub contains
  one line: `> Part of [[topics/<parent>]]`.
- Papers link to the **most specific** applicable level (almost always Tier 2).
  A paper spanning two topics links to both; there is no single-topic constraint.
- Topic wikilinks go in the **Connections block**, labelled "Topics:".
- Topic assignment is done by the LLM inside the same `paper-node` call — one
  call returns (a) key-point bullets → `%% node %%` block, and (b) topic
  wikilinks → Connections block.

### Example assignments

| Paper type | Topics |
|---|---|
| Cell type atlas of human cortex | `single-cell/atlas-building` + `single-cell/cell-types` |
| Perturb-seq + scGPT perturbation model | `single-cell/perturbation-sc` + `methods/foundation-models` |
| New ST analysis method | `spatial-transcriptomics/st-methods` |
| ST finding in human brain | `spatial-transcriptomics/st-biology` + `neuroscience/general-neuroscience` |
| Hormonal vulnerability + depression | `womens-health` + `psychiatry-mental-health/human-psychiatry` |
| Fear memory in rodents | `neuroscience/general-neuroscience` |
| Bioinformatics pipeline review | `reviews` + `methods/data-pipelines` |

## Architecture

```
 Phone / Laptop
      │  drop or forward (PDF or DOI)
      ▼
 private Slack #papers ─────► slack_listener.py  (Socket Mode, always-on via launchd)
                                     │ download PDF / read DOI
                                     ▼
                              process_paper.py  (orchestrator)
   ┌──────────┬───────────┬───────────┬──────────────┬───────────┬────────────┐
   ▼          ▼           ▼           ▼              ▼           ▼            ▼
 metadata   fulltext    codelinks   related        node        note_render  slack_post
 DOI →      pymupdf4llm regex        fastembed      claude -p   assemble +   reply card
 Crossref/  → Markdown  github/     cosine over    Sonnet +    write        + DOI link
 OpenAlex   full text   zenodo      vault index    paper-node  @Author_Year + node +
 + citekey  of paper    links       → [[@links]]   skill       .md          related
```

### Per-paper flow (deterministic except the node)

1. **Intake** — Slack drop in the private channel; download the PDF, or read the
   DOI/URL from the message.
2. **Metadata** — DOI from the PDF text or the message → clean
   title/authors/year/venue/abstract via **Crossref / OpenAlex**. Generate
   citekey `Author_Year`. If only a DOI arrives, try to fetch an open-access PDF
   (OpenAlex/Unpaywall) for full text; if none, the note is abstract+metadata
   only, flagged, and I can drop the PDF later.
3. **Full text + sufficiency gate** — `pymupdf4llm` converts the PDF to Markdown
   (deterministic, CPU, no ML), stored collapsed in the note. Then check the
   extracted body against `min_fulltext_words`. If it's too short (only an
   abstract, a paywall landing page, or a scanned PDF with no text layer),
   **STOP: write no note** and post the friendly "send me the full paper" reply
   instead of proceeding.
4. **Code availability** — regex for GitHub/GitLab/Zenodo / "code available at"
   in the full text; emit a Code line. Deterministic.
5. **Related links** — embed the full text with **fastembed** (local, offline),
   cosine-match against the cached vault index, take top-K over a fixed
   threshold → `[[@citekey]]` in the Connections/Related block.
6. **Node** — pipe the full text to headless Claude Code:
   `claude -p "/paper-node" --model claude-sonnet-4-6` → two outputs from one
   call: (a) 5–10 key-point bullets (main findings, key innovation, notable
   author/lab if relevant) → `%% node:start/end %%` block; (b) 1–3 topic
   wikilinks from the fixed vocabulary → Connections/Topics line. Only
   non-deterministic step; both outputs are isolated from the deterministic note.
7. **Assemble + write** — render the note from the template structure + abstract
   + node + collapsed full text + related + metadata; write `@Author_Year.md`
   into the vault.
8. **Slack reply** — card: title, authors, DOI link, code link, related links,
   node preview, note path. If I dropped a PDF, include a NotebookLM staging
   link (stage that one PDF to Drive) for an optional podcast.

### Full-text sufficiency gate (so I know when I only sent the abstract)

After conversion, `process_paper` compares the extracted body against
`min_fulltext_words` (config, default ~1000). If it falls short, **no note is
written** and Slack gets one short, friendly prompt that always names the paper
and the word count (so it's obvious why). Three cases, same handling:

- **Only the abstract / a landing page** (a few hundred words): _"That looks like
  just the abstract of *<title>* — I only pulled ~230 words. Send me the full-text
  PDF and I'll do the rest."_
- **Scanned PDF, no text layer** (≈0 extractable words): _"*<title>* looks like a
  scanned/image PDF with no text layer — I can't read it. Send a text PDF."_
- **Paywalled DOI, no open-access source**: _"Couldn't reach the full text for
  *<title>* — grab it with your credentials and drop the PDF here."_

The paper isn't lost: re-drop the real PDF and it runs normally. More generally,
`slack_post.py` is the single place that surfaces both success cards and error/
prompt replies, so every failure mode ends in a clear Slack message rather than a
silent no-op.

### Index maintenance

`build_index.py` embeds each vault note incrementally (only new/changed) and
caches vectors, so related-links matching stays fast and current.

### Runtime & availability (does my Mac have to be on?)

The Slack listener runs **on the Mac** (Socket Mode over a local WebSocket), so:

- **Real-time processing needs the Mac awake and online.** While the Mac is off
  or asleep, the listener is disconnected and Socket Mode does **not** replay
  events it missed.
- **But nothing is lost.** The dropped file stays in the Slack channel — Slack is
  the durable queue. On every launchd start (and on a periodic timer, and on
  wake-from-sleep reconnect), `catchup.py` queries channel history since the
  `state.json` timestamp and processes anything that arrived while the Mac was
  off. Net effect: drop from your phone at 2am, Mac processes it when it next
  wakes.
- **Asleep vs. off:** identical from the queue's perspective — both catch up on
  wake. If you want overnight processing without leaving the lid up, a
  `caffeinate`/power-schedule can keep it awake; optional, not required.
- **If you ever want true 24/7 real-time:** run only the *listener* on an
  always-on host (Pi/VPS) that forwards drops to the Mac's queue. Not worth it
  now — the note write + the Sonnet node both need the Mac (vault + your Claude
  Code subscription live here), so the Mac is in the loop regardless. The
  catch-up sweep gives you the same guarantee with far less to run.

## Components / repo layout

```
paper-pipeline/
  CLAUDE.md               project context for future Claude sessions
  PLAN.md                 this document
  README.md               setup guide (Slack app, deps, launchd, paper-node skill)
  config.example.yaml     paths + settings (copied to config.yaml, gitignored)
  .env.example            secret names (copied to .env, gitignored)
  pyproject.toml          uv project + pinned deps
  uv.lock                 reproducible lockfile (uv)
  .venv/                  uv-managed virtualenv (gitignored)
  state.json              last-processed Slack timestamp (gitignored)
  src/
    slack_listener.py     Socket Mode app; watches the private channel
    process_paper.py      orchestrator (steps 1–8)
    metadata.py           DOI + Crossref/OpenAlex + citekey (+ OA PDF fallback)
    fulltext.py           pymupdf4llm → Markdown
    codelinks.py          regex code-availability extraction
    related.py            fastembed index + cosine similarity
    node.py               headless `claude -p` wrapper (Sonnet + paper-node skill)
    note_render.py        assemble the note from the template structure
    slack_post.py         build + send the reply card AND error/prompt replies
    drive.py              (opt-in) stage a PDF to Drive for NotebookLM
    catchup.py            reconcile papers dropped while the Mac was off
    build_index.py        (re)build the vault embedding index
    config.py             load config.yaml + .env
  templates/
    note_layout.md        derived from ZI_Template.md (DOI link, node, full text)
  launchd/
    com.user.paperpipeline.listener.plist

~/.claude/skills/paper-node/SKILL.md   the Sonnet extraction skill (bullets only)
```

## Key technical decisions & risks

- **Node via headless Claude Code (subscription), not the API** — flat cost, and
  the extraction logic lives in the reusable `paper-node` skill. _Risk / must
  solve:_ launchd runs with a minimal PATH and no login shell, so `claude` may be
  not-found or unauthenticated in the daemon context. Mitigate: call `claude` by
  absolute path and ensure the daemon environment can reach auth. This is the
  single most likely cause of silent breakage.
- **Node is non-deterministic** — pin skill + model + fixed bullet format; keep it
  in its own `%% node %%` block so it never touches the deterministic note.
- **Insufficient full text** — abstract-only, paywall landing page, or scanned
  PDF: the sufficiency gate stops before writing a note and replies in Slack
  asking for the full PDF (see gate above). Threshold `min_fulltext_words` is
  configurable.
- **Metadata quality** — clean via Crossref when a DOI exists; preprints/scans
  without one get best-effort title extraction.
- **Private Slack channel** — fully supported; invite the app into the channel.
  App scopes: `files:read`, `chat:write` (+ `reactions:read` if we ever add
  emoji triggers). Tokens live in `.env`.
- **NotebookLM has no API** — only staged (PDF to Drive + link) when I opt in.
- **fastembed** is a fixed function → related-links selection is reproducible.

## What I still need from you

1. **Slack** — create/confirm the app (Socket Mode) + the private channel name;
   put the two tokens in `.env`. We do this together.
2. **A sample PDF** to develop + test the deterministic core offline.
3. **Google Drive folder** for the opt-in NotebookLM staging (or skip for now).

(Vault path and template are set. Zotero is intentionally out.)

## Build order

1. Scaffold repo with **uv** (`pyproject.toml` + `uv.lock`, `.venv/`) + config +
   the `paper-node` skill.
2. Deterministic core — `fulltext.py`, `metadata.py`, `codelinks.py`,
   `related.py`, `note_render.py` — testable offline on a sample PDF.
3. `node.py` headless Claude Code call; verify the daemon-context PATH/auth.
4. `slack_listener.py` + `slack_post.py` + the Slack app; end-to-end test drop.
5. `drive.py` opt-in podcast staging.
6. `launchd` plist so the listener survives reboots.

## Out of scope (for now)

- Auto-generating the podcast (NotebookLM stays a manual click).
- Any Zotero integration (kept fully separate/manual).
- LLM anywhere except the isolated `paper-node` block.
- **Karpathy-style synthesis pass** — once enough papers have accumulated under a
  topic, periodically run an LLM pass that reads all notes for that topic and
  writes a synthesis article into the topic stub (`topics/<name>.md`). Topic stubs
  are designed as the natural landing pad for this. Not part of the pipeline build;
  a separate manual or scripted workflow to be added later.
  After the pass completes, produce a **connections report** — a short Markdown
  file listing each novel connection surfaced: what the connection is, which papers
  are involved, and a one-sentence explanation of how the LLM bridged them. One
  entry per connection; no fluff. Delivered as a file (and optionally posted to
  Slack). The synthesis text itself goes into the topic stub; the report is
  separate and human-skimmable.
