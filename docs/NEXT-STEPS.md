# What to build, and in what order

Conclusion of the 2026-09-13 research workflow (31 agents, 5 phases). The
evidence is in `docs/research/`; this is the decision that came out of it.

## The question

What do you do with 10TB+ of personal Valorant recordings, and what happens to
the 7,303 lines of `valclips` that stalled in March?

## How this was decided

Four independent proposals were written from deliberately different lenses —
ship-fast, personal-utility, career-leverage, research-ambitious — then scored by
three judges (feasibility, odds-of-completion, value delivered), then every
load-bearing claim was adversarially fact-checked.

| Proposal | Feasibility | Completion odds | Value | Total |
|---|---|---|---|---|
| **ship-fast** | 86 | 88 | 61 | **235** |
| personal-utility | 84 | 70 | 72 | 226 |
| career-leverage | 71 | 47 | 81 | 199 |
| research-ambitious | 56 | 30 | 66 | 152 |

Of 12 verified claims, **3 were refuted and 9 held only partially.** None
survived unqualified. Prefer `docs/research/verify-*.md` over anything else in
the dossier that contradicts them.

Research-ambitious lost decisively — twelve weeks and two hand-labelling
campaigns before any artifact exists, against a repo history where this exact
project died in eight days.

## Order of work

### 0. Inventory first — nothing else is real without it

Every estimate in the dossier rests on ~11 sample files. The archive has never
been counted. `blackbird` is reachable and holds clips on `K:`, `L:`, `C:`, `P:`;
the NAS is offline (`.private/nas-recovery.md`).

Produce: real clip count, total bytes, date range, resolution/codec mix,
duplicate structure. Until this exists, treat every hour and terabyte figure
below as a guess.

### 1. Make the archive browsable — ~2 days, no AI

Highest-value work in the repo and almost none of it is new code:

- Swap the `TemplateResponse` argument order at `web/routes.py:30,45,216,228,240`
- Drop the `sys.platform == "linux"` gate at `config.py:19`
- Fix `cli.py:363` (`err=` → a stderr `Console`)
- Validate dates in `parser.py:23` so one bad filename can't kill a scan
- Add `ai_*` columns to the `--force` upsert, or stop clobbering them
- Run `scan --dir` + `thumbnails`

End state: the whole archive as a searchable thumbnail grid, reachable on your
phone over Tailscale, with working video scrubbing. This stands alone if
everything below it never happens — which is the point.

### 2. Delete the AI layer, with a postmortem

`ai/local_analyzer.py` and `ai/highlights.py` do not work (see `CLAUDE.md`).
Remove them in one commit, and write `docs/postmortem.md` carrying the measured
evidence: the 4K-upscale score inversion, `testsrc2` classified as gameplay,
zero scene detections on five hard cuts.

The repo's credibility problem is a confident docstring over an uncalibrated
heuristic. Deleting it with the measurements attached fixes that.

### 3. Mine Final Cut for labels — the one genuinely novel move

`~/Movies/val.fcpbundle/.../CurrentVersion.fcpevent` is a SQLite Core Data
database whose strings already name source clips
(`Valorant 2026.08.17 - 18.17.53.02.DVR`). `File → Export XML` yields every cut
ever made as an exact `(source, in-point, out-point)` triple, at zero annotation
cost.

This is supervision no commercial product can replicate: it encodes what *you*
thought was worth keeping, not what a general highlight model guesses. It is the
weak-supervision trick from the highlight-detection literature, except the NLE
already wrote the alignment down.

**Known limit:** two editing sessions in four years (Sept 2022, Aug 2026), and
the Valorant event holds five source clips. Count the recoverable labels before
building on them — if it's under ~40, this stays an indexing project.

### 4. Only then, detection

With an inventory, a working UI, and real labels, revisit event detection —
audio-first, since audio is ~1/100th the bytes. Do not start here.

## What not to do

- **Don't rebuild Eklipse/Medal/Valocut.** Capture-time auto-highlights are a
  solved, saturated commodity. The unserved gap is *retroactive corpus-scale
  indexing* — Eklipse caps at ~10 local VODs/month, which is ~100 months for this
  archive. That gap is the claim; make it the headline.
- **Don't benchmark against AMD's 95%.** It is clip-level 7-class classification
  on an unreleased 7,153-instance dataset with no shared split and no timestamps.
  Putting an event-level F1 beside it is two numbers from different tasks, and it
  reads as a credibility problem, not a result.
- **Don't train a "personal AI coach."** Zhang & Wang measured F1 0.850 on a leaky
  split collapsing to 0.487 user-held-out — below a 0.500 majority baseline. At
  n=1 the model learns to identify the player, not the skill.
- **Don't merge `origin/dev`.** Unrelated history; see `CLAUDE.md`.

## The honest framing

Nothing here is a business. The commercial survey found no product that ingests
a 10TB local archive, and also no evidence anyone is paying for one. Build it
because the archive is yours and currently unusable, and let the reusable parts —
a resumable batch pipeline over tens of thousands of media files — flow into
other projects. That reuse is real; a startup is not.
