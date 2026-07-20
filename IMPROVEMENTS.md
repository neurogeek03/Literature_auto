# Pipeline Improvements Backlog

Recorded 2026-07-13. Each item is a candidate feature; review one by one before implementing.

---

## 1. Rename saved PDFs with structured filename

**Idea:** When a PDF is staged to Google Drive, rename it as
`<Author>_<Year>_<Keyword1>_<Keyword2>.pdf` instead of whatever filename the user dropped.
No local PDF retention — the vault remains Markdown-only; this is Drive-only.

**Scope:**
- Drive: `drive_stage.py` uploads the file; rename it at upload time using the structured
  convention. The rename happens before the `files().create()` call — just set the `name`
  field in the file metadata.
- No local saving of PDFs. Pipeline design is unchanged; only the Drive filename differs.

**Open questions:**
- Keywords: extracted from `paper-node` topics/tags (already available at note-render time),
  or from title words? Topics are cleaner and already machine-readable.
- Separator: underscore throughout — `Smith_2023_graph_neural_networks.pdf`.
- `drive_stage.py` runs before or after `node.py`? Currently it runs early (PDF staging is
  optional/early). If keywords come from node output, staging must be deferred until after
  node generation — or the rename can be a separate Drive update call after the node is done.

**Status:** Implemented. Keywords come from `paper-node` topics (up to 2), joined
with the citekey: `<citekey>_<topic1>_<topic2>.pdf`. Timing question resolved by
existing code — `_stage_notebooklm()` already runs after `process_pdf()` returns,
so topics are available; no reordering needed. `drive.stage_pdf()` takes an
optional `filename` override; `slack_listener._structured_filename()` builds it.

---

## 2. Slack error reporting — reply in-thread to the original message

**Idea:** Any failure during processing should post an error reply *in the thread of the
original dropped PDF/link* rather than sending a new top-level message or failing silently.

**Current behaviour:** `slack_post.py` sends a reply, but error paths may not consistently
thread back to the original message `ts`.

**What "any error" covers:**
- Fulltext sufficiency gate (already partially implemented — confirm it threads correctly).
- Metadata fetch failure (Crossref/OpenAlex unreachable or DOI not found).
- PDF parse failure (corrupt file, scanned/image-only PDF).
- Node generation failure (headless Claude times out or returns malformed output).
- Any unhandled exception in the pipeline.

**Implementation sketch:**
- Wrap the top-level orchestrator in a `try/except` that catches all exceptions and calls
  `slack_post.error(channel, thread_ts, message)`.
- Ensure `thread_ts` (the `ts` of the user's original message) is threaded through every
  pipeline stage so it's always available for error replies.

**Status:** Implemented. `process_pdf()` now wraps metadata fetch, code/related
links, node generation, and note write each in try/except returning
`Result(status="error", ...)`, which `slack_post.post_result()` already threads
correctly. The one remaining gap — a truly unexpected exception outside
`process_pdf()` (e.g. `download_slack_file()` failing) — now also posts an
in-thread "something went wrong, retrying automatically" message from
`handle_event()`'s outer except, instead of failing silently while `catchup.py`
retries.

---

## 3. New Obsidian topics: AI Agents & Conferences

**Idea:** Add two new top-level topic stubs to the vault's `topics/` vocabulary.

- `topics/ai_agents.md` — papers on autonomous agents, multi-agent systems, agent
  frameworks, tool use, etc.
- `topics/conferences.md` — umbrella topic for conference-specific sub-topics (see #4).

**Notes:**
- These follow the same two-tier hierarchy already in use (topic stub → papers link to it).
- `paper-node` SKILL.md needs the new entries added to the topic vocabulary so Sonnet
  knows they exist when assigning topics.

**Status:** Implemented. `topics/ai-agents.md` created (using the vault's actual
hyphenated naming convention — all 22 existing stubs use hyphens, not
underscores, so this deviates from the `ai_agents.md` spelling above).
`topics/conferences.md` umbrella stub created; sub-stubs
(`topics/conferences/<slug>.md`) are auto-created on first use by
`conference.ensure_stub()` (see #4) rather than pre-created here, since they
depend on the whitelist. `paper-node` `SKILL.md` vocabulary updated with
`ai-agents` + an example row. `conferences` is intentionally NOT in the
skill's vocabulary — it's a deterministic tag parsed from the user's caption,
never a Sonnet-selected topic.

---

## 4. User prompt alongside PDF — conference tagging & focused node

**Idea:** When the user drops a PDF (or DOI) into Slack, they can optionally include a
short text prompt in the same message. Two effects:

### 4a. Conference sub-topic tagging
If the prompt names a conference (e.g. `ISMB_2026`), the pipeline:
1. Normalises the user's input to the canonical slug via the whitelist (see below).
2. Checks whether `topics/conferences/ISMB_2026.md` exists; if not, creates it as a new
   sub-topic stub linked to `topics/conferences.md`.
3. Adds `[[conferences/ISMB_2026]]` to the paper's Connections block in addition to any
   other topics.

### 4b. Focus instruction passed to `paper-node`
The free-text portion of the prompt is appended to the `paper-node` invocation so Sonnet
can weight its bullet points accordingly.

Example Slack message:
> `ISMB 26 — focus on the benchmarking methodology and runtime comparisons`

Parsed as: conference tag → normalised to `ISMB_2026`; focus hint = `focus on the
benchmarking methodology and runtime comparisons`.

### Conference whitelist + fuzzy matching

Conferences are whitelisted in `config.yaml` under `conferences:`. Each entry has:
- `slug`: the canonical form used for the topic stub and filename (`ISMB_2026`)
- `aliases`: list of strings that should resolve to this slug

Matching pipeline (in order):
1. **Exact match** against all slugs and aliases (case-insensitive).
2. **Normalisation pass**: strip spaces → underscores, expand 2-digit year → 4-digit
   (`26` → `2026`), uppercase acronym. Re-check against slugs.
3. **Partial match**: if the normalised token is a prefix or substring of exactly one
   slug, accept it.
4. **No match**: treat the entire user text as a focus hint only; no conference tag.

Example `config.yaml` block:
```yaml
conferences:
  - slug: ISMB_2026
    aliases: ["ISMB 26", "ISMB26", "ISMB 2026"]
  - slug: NeurIPS_2025
    aliases: ["NeurIPS 25", "neurips2025", "NeurIPS 2025"]
  - slug: RECOMB_2026
    aliases: ["RECOMB 26", "RECOMB 2026"]
```

New conferences are added to `config.yaml` manually (one-time) — the pipeline never
creates a slug that isn't in the whitelist, but the sub-topic stub in the vault *is*
auto-created on first use of a known slug.

**Parsing strategy:**
- Split prompt on `—`, `,`, or `;` (whichever comes first). First token → conference
  lookup; remainder → focus hint. If no separator, try the first whitespace-delimited
  token(s) against the whitelist; if no match, whole string is focus hint.

**Open questions:**
- Where in the note does the conference tag appear? Proposal: frontmatter `tags:` AND
  Connections block (so it shows up in both graph view and search).

**Status:** Implemented (4a only — see #5 for the poster-image half of this
item, still pending). New module `src/conference.py` implements the matching
pipeline exactly as specced (exact → normalize → unique prefix/substring →
focus-hint-only fallback) and `ensure_stub()`. `conferences:` whitelist added
to `config.yaml`/`config.example.yaml`, seeded with the three example entries
above. Caption text (`event["text"]`) is now threaded through on both the PDF
and DOI/URL drop paths (previously discarded on the PDF path). Focus hint is
prepended to the `paper-node` prompt in `node.run_node()`. Note placement
resolved as proposed: the conference tag appears in both frontmatter `tags:`
and the Connections **Topics** line.

---

## 5. Poster images via Slack → Obsidian node

**Idea:** The user drops a poster image (JPG/PNG) from a conference into the Slack channel,
optionally with a prompt (same format as #4). The pipeline extracts the main content and
creates an Obsidian note.

### What's different from a paper drop
- Input is an image, not a PDF or DOI.
- No DOI, no Crossref metadata, no fulltext conversion step.
- The "fulltext" is whatever OCR + vision extraction yields.

### Feasibility notes

| Question | Assessment |
|---|---|
| Can Claude Code read images? | Yes — `claude -p` accepts image attachments or base64. The `paper-node` skill would need a poster-specific variant or a flag. |
| OCR quality | Modern vision models handle dense poster layouts reasonably well. Low-res phone photos are the weak point. |
| Metadata | No DOI; user must supply title/authors/conference in the prompt, or the model infers from the poster. Inferred metadata will sometimes be wrong — user should review. |
| Note structure | Same template but `citekey` = `Author_Year` if inferable, else `Poster_CONFYEAR_ShortTitle`. No fulltext section (poster *is* the content). The node bullets replace the collapsed full-text block. |
| Trigger detection | Slack event `file_shared` with `mimetype` starting `image/` → poster branch. |

### Recommended implementation path (if we proceed)
1. Detect image drop in the Slack listener.
2. Download image; pass to a `poster_node.py` module that calls headless Claude with vision.
3. Extract: title, authors, affiliation, conference (from prompt or inferred), 5–8 key bullets.
4. Write a simplified note (no fulltext block, no code-link, no related-links unless we embed
   the OCR text for cosine search).
5. Post Slack reply with extracted title + bullets for user to confirm/correct.

### Open questions
- Is a separate `poster-node` skill warranted, or add a `--poster` flag to `paper-node`?
- Should we attempt related-links (embed the OCR body and run cosine)? Probably yes — same
  code path, just different source text.
- What if the image is too low-res for useful extraction? Same error path as #2: reply in
  thread asking for a better image.
- Is this worth the added complexity? Poster notes are lower-value than paper notes (less
  depth, often preliminary work). Recommend implementing only after #1–#4 are stable.

**Status:** Implemented. Covers both posters and single talk slides (same code path;
the model classifies which one it's looking at). New modules: `src/poster_node.py`
(the vision call) and `src/process_poster.py` (the orchestrator — image → note,
mirrors `process_paper.py`). New skill `~/.claude/skills/poster-node/SKILL.md`,
same topic vocabulary as `paper-node` so poster/slide nodes sit in the same graph.
New template `templates/poster_layout.md` (no fulltext/abstract section — the
image *is* the content, embedded via `![[filename]]`).

Resolved open questions:
- **Separate skill, not a flag** — `poster-node` outputs a different contract
  (TITLE/AUTHORS/VENUE/SOURCE_TYPE fields the paper skill doesn't have) and reads
  an image path instead of pasted fulltext; a shared skill would need heavy
  branching for no real benefit.
- **Vision call mechanics**: `claude -p` is given the absolute image path in the
  prompt and told to Read it, with `--allowedTools Read` passed explicitly so the
  one tool call it needs is pre-authorized — critical for the unattended/daemon
  context, otherwise a permission prompt would hang forever. Verified working
  end-to-end against real conference-poster photos (including raw HEIC input).
- **Related-links**: yes, same code path. `related.extract_embed_text()` now
  falls back to the Key Points block when there's no Abstract (true for every
  poster/slide note), so cosine search over the vault index works unchanged.
- **Low-res/unreadable image**: the skill outputs `ERROR: <reason>` instead of
  the normal fields; `process_poster.process_image()` turns that into
  `status="insufficient"` and the Slack reply asks for a clearer photo — same
  shape as the PDF sufficiency gate in #2.
- **Citekey**: `Author_Year` when the poster/slide has a legible author list
  (common case), else `Poster_CONFYEAR_ShortTitle`. Year comes from the detected
  conference slug (`ISMB_2026` → `2026`) when the poster itself doesn't state one.
  For a single slide with no printed author list, the user supplies the first
  author's name in the Slack caption and `poster-node` uses it — the skill's
  AUTHORS instruction was updated to accept this ("Additional context from the
  user") specifically because slides usually can't provide it from the image
  alone.
- **Dedup**: posters/slides dedupe on the citekey itself (first author + year)
  via new `note_render.poster_target_path()`, *not* the paper flow's DOI-based
  `target_path()`. Re-dropping the same author's poster/slide updates the
  existing note in place rather than suffixing a duplicate — deliberate,
  matching how the user actually uses this (one node per person/theme). It
  still falls back to the normal `_a`/`_b`/... suffixing if the colliding
  citekey belongs to a real paper note (`pub_type` preprint/peer-reviewed),
  so a poster drop can never clobber an actual paper.
- **HEIC/HEIF**: converted to JPG via macOS `sips` before both the vision call and
  vault storage (iPhone camera default; Read/Obsidian portability both want JPG).

New config: `vault.images_subdir` (default `images`) and `node.poster_skill`
(default `poster-node`) in `config.yaml`/`config.example.yaml`.

Not yet done: wiring through a live Slack round-trip (tested via the offline CLI
— `uv run python -m src.process_poster /path/to/image.jpg [--prompt "..."]` —
against a scratch vault, not the real one). `slack_listener.handle_event()` now
detects image file drops (by `mimetype` or extension) and branches to
`process_poster.process_image()`; `slack_post.post_poster_result()` posts the
summary card. This reuses `catchup.py` for free since it just replays
`handle_event()`. First real Slack drop should be treated as a smoke test.

---

---

## 6. Preprint vs peer-reviewed node colouring in Obsidian graph

**Idea:** Paper nodes in the Obsidian graph view are visually distinct by publication type:
preprints appear grey-ish; peer-reviewed papers appear off-white.

**Implementation (done):**
- `PaperMeta.is_preprint` flag added to `metadata.py`. Detected from:
  - Crossref `type == "posted-content"`
  - OpenAlex `type == "preprint"`
  - `fetch_biorxiv` (always preprint)
  - Venue-name fallback (bioRxiv, medRxiv, arXiv, chemRxiv, SSRN, Research Square)
- `pub_type` frontmatter field + tag added to every note (`preprint` or `peer-reviewed`).
- `graph.json` in the vault gains two color groups:
  - `tag:#preprint` → #9E9E9E (grey, rgb 10395294)
  - `tag:#peer-reviewed` → #F0EDE6 (off-white, rgb 15789542)

**Status:** Implemented.

---

## Priority order (suggested)

| # | Item | Effort | Value | Suggested order |
|---|---|---|---|---|
| 2 | Error reporting in-thread | Low | High | Done |
| 3 | New topics (AI Agents, Conferences) | Very low | Medium | Done |
| 4 | Prompt parsing (conference tag + focus hint) | Medium | High | Done |
| 1 | PDF rename convention | Low–Medium | Medium | Done |
| 5 | Poster image support | High | Medium | Next |
| 6 | Preprint/peer-reviewed graph colouring | Low | Low–Medium | Done |
