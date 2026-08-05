# Pipeline Improvements Backlog

Recorded 2026-07-13. Each item is a candidate feature; review one by one before implementing.

All items recorded through 2026-07-19 (PDF rename, in-thread Slack error reporting,
AI Agents / Conferences topics, prompt parsing for conference tag + focus hint, poster
image support, preprint/peer-reviewed graph colouring) have been implemented and removed
from this backlog. See git history for their original specs.

Remaining caveat, not a backlog item: the poster/slide path has only been exercised
offline (`uv run python -m src.process_poster /path/to/image.jpg [--prompt "..."]`); the
first real Slack image drop should be treated as a smoke test.

---

## 7. Claude-in-Chrome as a PDF-acquisition fallback for bot-blocked publishers

Recorded 2026-08-05.

**Idea:** When all deterministic PDF-acquisition strategies fail for a shared link/DOI,
hand off to Claude-in-Chrome driving the user's real, logged-in Chrome to fetch the paper
— instead of the current dead-end that asks the user to download it by hand. Goal: **fully
eliminate manual PDF hunting.**

**Why this works:** Chrome under the extension inherits the user's institutional cookies
and looks like a real browser, so it passes the Cloudflare / anti-bot checks that block
`requests`. Empirically: Nature already succeeds via the plain-`requests`
`citation_pdf_url` path; bioRxiv and Elsevier block scripts and currently hit the manual
dead-end. A browser-driven fetch of a bioRxiv preprint has been confirmed to work by hand,
and produced a first draft site skill (`skills/biorxiv-pdf-download-skill.md`).

**Insertion point:** `slack_listener._handle_text()` is already a cascade of acquisition
strategies (direct `.pdf` link → `citation_pdf_url` meta scrape → OA PDF via
OpenAlex/Unpaywall). Today it dead-ends at the "grab it with your credentials and drop the
PDF here" message (line ~255). The browser fallback slots in **as the last strategy before
that message** — only fires when the cheap deterministic ones have all failed, so nothing
gets slower for journals that already work.

**Scope / architecture (to be settled — see "Central tension" below):**
- New module (e.g. `browser_fetch.py`) invoked from `_handle_text()` after the deterministic
  candidates fail.
- Per-domain **site skills** registry (biorxiv drafted; add elsevier/sciencedirect, wiley,
  OUP, etc.) that encode each publisher's PDF-button layout + an href-domain safety check
  ("only click a link on the official publisher domain").
- Preferred output: the real PDF saved to the pipeline work dir, so the existing
  `fulltext.to_markdown` (pymupdf4llm) + NotebookLM Drive staging paths are unchanged and
  the deterministic-fulltext rule holds. Fallback output: in-browser page/full-text
  extraction when no downloadable PDF exists.

**Central tension (the thing to resolve before building):** the pipeline is a fully
*unattended* launchd daemon (Slack Socket Mode, headless `claude -p` subprocesses, no GUI).
Claude-in-Chrome is *interactive* — it needs a running GUI Chrome with the extension, the
user logged in, and site permissions pre-granted. Three sub-questions:
  1. **Execution model.** Can a headless `claude -p "/fetch-paper <url>" --allowedTools
     "mcp__claude-in-chrome__*"` subprocess (same pattern as the node/poster steps) actually
     reach and drive the running Chrome extension? If yes, the browser step is just another
     pluggable acquisition strategy and fits the existing architecture cleanly. **This is
     the #1 unknown to verify in testing.**
  2. **Download confirmation.** The draft skill (correctly) requires explicit confirmation
     before any download. For an unattended daemon that's a blocker. Options: (a) configure
     Chrome to auto-save PDFs to a folder without the save-dialog + rely on the skill's
     href-domain safety check as the gate; (b) a one-tap Slack "approve" reaction; (c) skip
     the file entirely and extract page text in-browser (no download → no confirmation).
  3. **New precondition.** Browser fallback needs Chrome-with-extension up and the user
     logged in. If Chrome isn't running, degrade gracefully back to today's "drop the PDF"
     message rather than erroring.

**Open questions:**
- One generic `/fetch-paper` skill that dispatches to per-domain sub-skills, vs. one skill
  per publisher? Leaning: a generic dispatcher + a per-domain layout table it consults.
- Do we ever prefer in-browser full-text extraction over downloading (e.g. HTML full-text
  journals), given it violates the deterministic-fulltext rule but is genuinely
  download-free? Probably reserve it for the no-downloadable-PDF case.
- Where does `skills/biorxiv-pdf-download-skill.md` live long-term — promoted to
  `~/.claude/skills/` like `paper-node`/`poster-node`, or kept in-repo under `skills/`?

**Live-test findings (2026-08-05, bioRxiv `10.64898/2026.07.30.741795v1`):**
- **The bot-wall bypass works.** Driving the user's real logged-in Chrome, the article
  page and the full 50-page `.full.pdf` both loaded fine — content `requests` can't reach.
- **A plain click does NOT download.** Clicking "Download PDF" just navigates to the
  `.full.pdf` URL and Chrome renders it inline in its built-in viewer. `get_page_text` on
  that inline PDF returns nothing (no text layer) — so the download-free text route is dead
  for PDFs.
- **The real blocker is the native macOS "Save As" Finder sheet.** Clicking the viewer's
  download (↓) button fires the OS-level save dialog, which lives *outside* the web page.
  Claude-in-Chrome automation can only act inside the tab, so it cannot see or confirm that
  Finder sheet — the download hangs and nothing lands in `~/Downloads`. This is THE
  engineering problem for #7, not the bot wall.
- **Required precondition (the fix):** Chrome → Settings → Downloads → turn OFF *"Ask where
  to save each file before downloading"* (fixed location = `~/Downloads`), and ideally turn
  ON *"Download PDF files instead of automatically opening them"*. With those set, the save
  is dialog-free and the click writes straight to disk — exactly what the unattended daemon
  needs. Claude-in-Chrome appears scoped to `~/Downloads`, so the pipeline should look there
  for the fetched file and move it into the work dir.
- **Temp-file cleanup (aligns with the "no PDF kept" hard rule):** the fetched PDF is a
  transient input, not a kept artifact. Design: (1) **move** (not copy) the file out of
  `~/Downloads` into the work dir the moment it lands, so it never lingers there; (2)
  **delete** it after `fulltext.to_markdown()` (and Drive staging, if opted in) has consumed
  it — same lifecycle Slack-dropped PDFs should follow. (3) **Safety rule: delete by exact
  known path only, never by glob** — `~/Downloads` is real user data, so the pipeline must
  remove only the specific filename it fetched, never `rm ~/Downloads/*.pdf`.

**Status:** Proposed; bot-wall bypass validated live, dialog-free-save precondition
identified. Draft bioRxiv site skill exists (`skills/biorxiv-pdf-download-skill.md`).
Not yet wired into the pipeline. Next: set the two Chrome download settings, re-run the
bioRxiv fetch to confirm a dialog-free save to `~/Downloads`, then decide the execution
model (Q1 above) and build `browser_fetch.py`.

---

## 8. Second channel → journal-club vault (multi-vault routing)

Recorded 2026-08-05.

**Idea:** Kick the same pipeline off automatically whenever someone shares a paper link in
the lab's **journal club** Slack channel, writing into a **separate vault** from the
personal one. Depends on #7 (most journal-club links will be publisher pages that need the
browser fallback).

**Scope (rough — to be detailed later):** generalize the single-channel, single-vault
assumption. Today `handle_event()` hard-checks one `slack.channel_id` and `config.yaml`
points at one vault. Needs a channel→vault (and channel→topic-vocabulary?) mapping so each
watched channel routes to its own vault + index + config.

**Status:** Noted, deferred. User will spec the details later.
