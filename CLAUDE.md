# CLAUDE.md — Paper Pipeline

Context for any Claude session working in this repo. Read `PLAN.md` for full
design and rationale; this is the fast orientation.

## What this is

An unattended automation. The user drops/forwards a paper (PDF or DOI) into one
**private** Slack channel from phone or laptop; a fixed pipeline builds a
self-contained **Obsidian** literature note — full paper text as Markdown
(collapsed in the note), a bulleted key-points "node" written by Sonnet via
**headless Claude Code**, related notes linked into the knowledge graph — and
posts a summary back to Slack. **No Zotero, no stored PDF.** Built once, runs
forever.

## Hard rules

- **Obsidian-only. No Zotero, no PDF kept.** Zotero is the user's separate manual
  habit for deep reads; the pipeline must not touch it. Full text is stored as
  Markdown inside the note, collapsed.
- **Deterministic everywhere except the node.** Metadata, full-text conversion,
  code-link extraction, and related-links (fastembed) are fixed functions — same
  input, same output. The ONLY model/LLM step is the `paper-node` block.
- **The node comes from headless Claude Code on the user's subscription, not the
  API.** `claude -p "/paper-node" --model claude-sonnet-4-6`, output is 5–10
  key-point bullets (no length cap), written between `%% node:start %%` /
  `%% node:end %%`. Never let it touch the deterministic parts of the note. Do
  not propose the Anthropic API for this (see user preference memory).
- **Secrets in `.env`, paths in `config.yaml` (both gitignored).** No tokens or
  absolute user paths hardcoded in `src/`.
- **NotebookLM has no API.** Only stage a PDF to Drive + post a link when the user
  opts in by dropping the actual PDF. Never try to auto-generate a podcast.
- **Full-text sufficiency gate.** If the converted text is too short (abstract
  only, paywall landing page, or scanned/no-text PDF), do NOT write a note —
  reply in Slack asking for the full PDF, naming the paper + word count. Threshold
  `min_fulltext_words` (config). Every failure mode ends in a Slack message via
  `slack_post.py`, never a silent no-op.
- **Fail safe.** Retry rather than drop work; the paper stays in Slack and can be
  re-dropped.

## Environment

- **uv-managed.** `pyproject.toml` + `uv.lock`, venv at `.venv/`. Set up with
  `uv sync`. launchd / any daemon must call the interpreter by absolute path
  (`.venv/bin/python`), not a bare `python`.
- Deps: slack_bolt, pymupdf / pymupdf4llm, requests, fastembed, numpy, pyyaml.
- **Headless Claude Code gotcha:** launchd runs with a minimal PATH and no login
  shell — `claude` is often not-found or unauthenticated there. Always invoke
  `claude` by absolute path and ensure the daemon context can reach its auth.
  This is the most likely cause of silent breakage.

## Vault + template

- Vault: `/Users/marlenfaf/Desktop/UofT_PhD/literature_auto`
- Template: `literature_auto/ZI_Template.md` (Zotero Integration / Phelan style).
  We reuse its **structure** (frontmatter category/tags/citekey/status, Connections
  callout with Contribution + Related, Abstract, Metadata) but: replace the PDF
  file-link with the **DOI URL**, drop the Zotero annotation macro, add the
  **node** block and a **collapsed full-text** section.
- Notes are named `@<citekey>.md`; citekey = `Author_Year` (pandoc convention),
  generated from metadata — no Better BibTeX.
- Link convention: `[[@Author_Year]]` between literature notes (the graph).
- Topic stubs at `literature_auto/topics/<name>.md` (two-tier hierarchy); papers
  link to 1–3 topics in the Connections block. Topic assignment is part of the
  `paper-node` call. Full vocabulary and examples in `PLAN.md` § Knowledge graph.

## Per-paper flow

1. Slack drop (PDF or DOI) in the private channel → download / read DOI.
2. `metadata.py`: DOI → Crossref/OpenAlex (title/authors/year/venue/abstract);
   citekey `Author_Year`; if DOI-only, try an OA PDF (OpenAlex/Unpaywall).
3. `fulltext.py`: `pymupdf4llm` → Markdown, stored collapsed in the note. Then
   the **sufficiency gate**: if extracted body < `min_fulltext_words`, STOP —
   write no note, post the friendly "send the full paper" reply instead.
4. `codelinks.py`: regex GitHub/GitLab/Zenodo / "code available" → Code line.
5. `related.py`: embed full text (fastembed), cosine over the cached vault index,
   top-K → `[[@citekey]]` links.
6. `node.py`: headless `claude -p "/paper-node" --model claude-sonnet-4-6` →
   bullets → `%% node %%` block.
7. `note_render.py`: assemble + write `@<citekey>.md`.
8. `slack_post.py`: reply card — title, authors, DOI link, code link, related
   links, node preview, note path; optional NotebookLM staging link if a PDF was
   dropped.

`build_index.py` maintains the vault embedding index incrementally.

## Runtime & availability

Listener runs on the Mac (Socket Mode). Real-time processing needs the Mac awake;
Socket Mode does **not** replay missed events. Nothing is lost, though: the Slack
channel is the durable queue, and `catchup.py` reconciles anything dropped while
the Mac was off (on launchd start, a periodic timer, and wake-from-sleep) using
the `state.json` last-processed timestamp. The Mac is in the loop regardless
because the vault write and the Sonnet node both live here.

## Repo layout

```
src/            pipeline modules (see PLAN.md for each)
templates/      note_layout.md — derived from ZI_Template.md
launchd/        com.user.paperpipeline.listener.plist
pyproject.toml  uv project + deps ; uv.lock ; .venv/ (gitignored)
config.yaml     paths + settings (gitignored)
.env            secrets (gitignored)
state.json      last-processed Slack ts (gitignored)
~/.claude/skills/paper-node/SKILL.md   the Sonnet extraction skill
```

## Status

**Built and validated** (2026-07-07). All 13 `src/` modules import; the
deterministic core + live Claude node + fastembed related-links + sufficiency
gate were tested end-to-end on a synthetic PDF into an isolated temp vault. uv
env synced; 19 topic stubs in the vault; `paper-node` skill installed. Remaining
before it runs for real: the user creates the **Slack app + private channel +
tokens** (see `README.md` §2–3) and fills `config.yaml → slack.channel_id` +
`.env`. Google Drive staging is optional/off. Not yet done: run under launchd,
first real drop test.

## Conventions

- Python via uv; standard library + the deps above. Single-purpose modules; the
  deterministic core (`fulltext`, `metadata`, `codelinks`, `related`,
  `note_render`) must be testable offline with no Slack/Drive/Claude.
- No emojis in generated notes or code.
