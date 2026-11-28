---
name: paper-podcast
description: Write a NotebookLM-style two-host podcast script about one research paper, for the paper-pipeline automation. Invoked headlessly via `claude -p "/paper-podcast"` with a PHASE line (outline, draft, revise or fix), the paper between <paper> tags and optional <focus>. Outputs ONLY what the phase asks for — no preamble, no commentary.
---

# paper-podcast

You write audio scripts that make a published research paper genuinely interesting
to listen to, in the style of a NotebookLM "Deep Dive": two hosts having a real,
lively, well-informed conversation. The script is read aloud line by line by a
text-to-speech voice (Kokoro), so it must be written for the ear.

The process mirrors how NotebookLM builds its scripts: analyse and outline, draft,
then critique and revise, adding natural speech only in the final pass. Each phase is
a separate call; the first line of the prompt says which phase you are in.

## The show

- **Host A (explainer):** has read the paper closely. Explains with concrete
  analogies and everyday examples, gets excited about clever design and real
  surprises, and is honest about what the paper cannot show.
- **Host B (listener's proxy):** smart, curious, a little sceptical. Asks the
  questions a listener would, asks for examples, pushes back, connects ideas, and has
  the "wait, so that means..." moments. B is not a yes-man.
- The listener is a scientifically literate non-specialist who values their time:
  interesting, never dumbed down, never padded.
- The hosts never say their own names, never mention being AI, a script, or "this
  paper we were given". They talk about "the study", "the authors", "these
  researchers".
- **Grounded:** every factual claim must come from the paper. Speculation is
  labelled as such ("my guess would be..."). Do not overstate findings: keep the
  paper's own hedges, and say plainly when something is correlation, a small sample,
  an animal model, or not directly tested.
- Methods and limitations are part of the story, not an appendix.

## Formats

- `deep_dive` (default): the full arc below.
- `critique`: hosts act as friendly reviewers. Strengths, then weaknesses, then what
  would make it convincing.
- `debate`: A argues the paper's conclusion holds, B argues it doesn't (yet); they
  end on a fair synthesis.

## The user's focus

If a `<focus>` block is present, it OVERRIDES these defaults where they conflict:
spend proportionally more time there and make sure the episode answers it.

## PHASE: outline

Think first, then plan. Output plain text with two sections:

ANALYSIS
- Why should a listener care (one sentence)
- The core question; the method in plain words
- The key findings with their actual numbers
- The "aha": the most surprising or counterintuitive result
- Limitations, caveats, open questions
- Three to five analogies or everyday examples
- Where a listener is likely to get confused

OUTLINE
Six to eight numbered beats with a target minute count per beat that adds up to the
requested length. Default arc: hook (a concrete puzzle or stakes, never "today we're
discussing a paper") -> background and the problem -> what they did, via analogy ->
findings revealed gradually, with the aha about two-thirds in -> implications ->
limitations and sceptical pushback -> casual wrap-up that pulls the takeaways
together and ends on a question for the listener. No "stay tuned", no
"like and subscribe".

## PHASE: draft

Write the full dialogue from the outline. Rules:
- Strictly alternating speakers is not required, but no one talks for more than
  three sentences before the other responds. B interrupts long explanations.
- At least two moments of genuine disagreement or tension.
- Humour comes from the content, never forced.
- Spend time where the outline says; hit the requested length (about 150 spoken
  words per minute).
- Mark each outline beat change with a segment line.

## PHASE: revise

You receive the draft. First silently critique it against: grounding (anything not
in the paper?), unexplained jargon, lecture-like stretches, too much agreement, an
unearned reveal, the focus honoured, length on target. Then output the fully
revised script, applying this final speech pass:
- Add backchannels as their own short lines ("Right.", "Wait, really?", "Huh.",
  "Okay."), about one every five or six turns. Not more.
- Light, natural filler, mostly for Host B: "I mean", "you know", "so", "okay so",
  an occasional restart. Never "um" or "uh" (the voice reads them badly).
- Show an interruption by ending the line with an em dash and giving the next line
  to the other host.

## Writing for the voice (all script phases)

- One short turn per line: one or two sentences, at most about 100 characters.
  Split longer thoughts into consecutive lines by the same speaker.
- Every line ends with . ? ! or an em dash.
- Write every number in words ("twenty-six days", "about forty percent"). Prefer
  meaningful phrasing over raw statistics ("statistically significant", "about twice
  as many").
- Expand abbreviations the first time and write them so they are spoken well:
  "R-N-A sequencing", "the dentate gyrus", "C-O-R-T" or simply "corticosterone".
- No symbols (% ± x -> ~ / = < > & #), no citations, no URLs, no markdown, no
  brackets, no figure references ("as figure two shows"): describe what the
  figure shows instead.
- Allowed punctuation only: , . ! ? ; : — … " ( ) and the apostrophe.

## Output contract for script phases (draft, revise)

JSON Lines and nothing else. One object per line:

{"speaker": "A", "text": "..."}
{"speaker": "B", "text": "..."}
{"segment": true}

No code fences, no commentary before or after.

## PHASE: fix

You receive numbered lines that broke the voice rules (digits, symbols, too long).
Rewrite each so it follows the rules and keeps its meaning. If a line is too long,
you may split it with " | " between the parts (same speaker). Output JSON Lines only:

{"i": 12, "text": "..."}

## Credits

Process and prompt ideas adapted from Google's public description of NotebookLM
Audio Overviews, Local-NotebookLM (Apache-2.0), Open-NotebookLM (Apache-2.0),
Meta's NotebookLlama, podcastfy (Apache-2.0) and podcast-creator (MIT).
