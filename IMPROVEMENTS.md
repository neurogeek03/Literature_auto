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

**Status:** Clarified (Drive-only rename). Open question: keyword source + timing.

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

**Status:** Under discussion.

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

**Status:** Ready to implement (straightforward stub creation + vocabulary update).

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

**Status:** Clarified (whitelist with fuzzy matching). Open question: note placement of tag.

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

**Status:** Confirmed — do after #1–#4 are stable.

---

## Priority order (suggested)

| # | Item | Effort | Value | Suggested order |
|---|---|---|---|---|
| 2 | Error reporting in-thread | Low | High | 1st |
| 3 | New topics (AI Agents, Conferences) | Very low | Medium | 2nd |
| 4 | Prompt parsing (conference tag + focus hint) | Medium | High | 3rd |
| 1 | PDF rename convention | Low–Medium | Medium | 4th |
| 5 | Poster image support | High | Medium | 5th |
