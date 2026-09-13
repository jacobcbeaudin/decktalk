# Docs notes from Track A (CLI)

Apply these after the merge. Each note names the page, the section, and the exact text to
put in place. Track B and Track C write their own notes about `beats` cross-referencing,
`"verify": false`, skip reasons in depth, and the runtime. Where a page is touched by more
than one track, apply this file's text first and then theirs.

---

## docs/reference/cli.mdx

### 1. Intro paragraph (the paragraph under the `decktalk [-v | -q] [-p DIR] <command> [flags]` block)

Replace the whole paragraph with:

```mdx
Every project command takes `--project DIR`, or `-p DIR`. The default is the
`DECKTALK_PROJECT` variable, then the current directory. Each command reads `.env` from
the project and never prints a key. Progress goes to stderr, and the tables go to stdout.
`-v` or `--verbose` shows debug logging, including every ffmpeg command line, and `-q` or
`--quiet` shows warnings only. Both go before or after the command name. `--version` prints the version. A flag
that repeats, such as `--only`, takes one value each time and does not take commas.
`status`, `beats`, `check`, `verify`, and `doctor` also take `--json`, `--strict`, and
`--no-fail`, which [Machine-readable output](#machine-readable-output) describes.
```

### 2. New section, inserted between the intro paragraph and `## Setting up a machine and a project`

````mdx
## Machine-readable output

`status`, `beats`, `check`, `verify`, and `doctor` share three flags.

| Flag | Meaning |
|---|---|
| `--json` | Print the result as one JSON object on stdout instead of the tables. Progress still goes to stderr. |
| `--strict` | Also exit 1 on an uncertain verdict, the ones marked with a question mark. |
| `--no-fail` | Exit 0 even when a check fails, for scripts that read the table or the JSON themselves. |

Every object opens with the same four keys, and a fifth key named after the command holds
the result.

```json
{
  "command": "verify",
  "version": "<version>",
  "ok": true,
  "findings": { "certain": 0, "uncertain": 0 },
  "verify": { "...": "the result, described below" }
}
```

| Key | Meaning |
|---|---|
| `command` | The command that ran, such as `verify`. |
| `version` | The DeckTalk version that ran it. |
| `ok` | `true` when the command would exit 0 without `--no-fail`. `--strict` counts, so an uncertain finding makes it `false` under `--strict`. |
| `findings.certain` | How many certain findings the command made. Any one of them exits 1. |
| `findings.uncertain` | How many uncertain findings it made. They exit 1 only with `--strict`. |

| Command | The key named after it holds |
|---|---|
| `doctor` | `components`, a list of `{name, ok, detail}`, one per row of the table. |
| `status` | `project` with `root`, `name`, `script`, `script_exists`, `cues`, and `cues_exists`. `sections`, a list of `{key, kind, source, recorded, cut}`, where `kind` is `page` or `clip`. `timeline` with `exists`, `estimated`, `total_seconds`, and `sections`, a list of `{key, title, start, end, duration}`. `beats` with `exists` and `sections`, a list of `{key, cues}`. `final` with `path`, `exists`, and `duration`. `outputs` with `srt`, `vtt`, and `chapters`, each `{path, exists}`. |
| `beats` | `BeatsResult.to_dict()`, the sections with their resolved cues and notes, and the `unresolved` and `unknown` counts. |
| `check` | `recordings`, a list with one `RecordingCheck.to_dict()` per recording. |
| `verify` | `VerifyResult.to_dict()`, the `starts`, `cuts`, and `cues` rows with a `verdict` on each. |

Numbers are JSON numbers, a value that does not exist yet is `null`, and every verdict is
the same string the table prints. Paths are relative to the project root and use forward
slashes on every platform. `doctor` runs outside any project, so its details keep full
paths. Only the object goes to stdout, so `decktalk verify --json > verify.json` captures
nothing else.

`status` reads what exists and judges nothing, so its findings are always zero and it
exits 0 unless the project cannot be loaded.
````

### 3. `### decktalk doctor`

Replace the first paragraph with:

```mdx
Reports the Python, Chromium, ffmpeg, ffprobe, per-machine config file, and KaTeX found,
and exits 1 when a tool is missing. Each `MISSING` row is a certain finding. The config
row is `ok` whether or not the file exists. It changes nothing.
```

After the `A katex row marked MISSING …` paragraph, add:

```mdx
| Flag | Meaning |
|---|---|
| `--json`, `--strict`, `--no-fail` | As in [Machine-readable output](#machine-readable-output). |
```

### 4. `### decktalk beats`

Replace the paragraph with:

```mdx
Resolves every cue phrase to a second and prints a table of sections, their speech end,
their `min_seconds`, and their resolved cues. It reports each phrase it cannot find and
each cue id that appears nowhere in the page that plays it, and exits 1 if there is
either. Both are certain findings. A section whose speech ends before its `min_seconds`
is an uncertain finding, so it exits 1 only with `--strict`.

| Flag | Meaning |
|---|---|
| `--allow-unknown` | Continue when a cue id in cues.json appears nowhere in the page that plays it. |
| `--json`, `--strict`, `--no-fail` | As in [Machine-readable output](#machine-readable-output). |
```

### 5. `### decktalk check`

Replace the verdict table with this one, which adds a `Certain` column:

```mdx
| Verdict | Certain | Meaning |
|---|---|---|
| `ok` | | Nothing suspect. |
| `NO COVER` | yes | `measure` found no magenta run, so the alignment is a guess. |
| `BLACK?` | no | The brightest pixel at the midpoint is below `black_ymax`, so the page probably drew nothing. |
| `TRUNCATED` | yes | The recording is shorter than requested by more than `truncated_slack_seconds`. |
| `KATEX?` | no | The page reported that KaTeX never loaded, so its equations stayed plain text. |
| `STALLED` | yes | The page went longer than `stall_ms` between two frames while recording, so a reveal may have been captured late. The recorder already retried up to `retries` times. |
| `PAGE ERROR` | yes | The page threw a script error or exposed no `window.__decktalk.catalog`, so the section recorded a blank stage. `build` stops on it. |
```

Replace the paragraph `Several verdicts may appear together, separated by spaces.` with:

```mdx
Several verdicts may appear together, separated by spaces. `check` exits 1 when any
recording has a certain verdict. A verdict that ends in a question mark is uncertain, so it
exits 1 only with `--strict`.
```

Replace the flag table with:

```mdx
| Flag | Meaning |
|---|---|
| `--only N` | Only these section numbers. Repeat the flag for several. |
| `--json`, `--strict`, `--no-fail` | As in [Machine-readable output](#machine-readable-output). |
```

### 6. `### decktalk verify [SECTION:CUE ...]`

Replace the heading, the first paragraph, and everything down to (but not including) the
`[How it works](/concepts/how-it-works#what-verify-measures) explains the columns.` line
with:

````mdx
### `decktalk verify [SECTION:CUE ...]`

Confirms that every section opens on a real frame, that the narration is quiet in the
last 150 ms before every cut, and that the picture changed at every cue in
`build/audio/beats.json`, in section order and then cue time. It also measures how far
from the cue the first changed frame sits. After a silent build the cue table gains an
`a/v` column, the picture's first change measured against the placeholder click at the
cued word. Name cues as arguments or with `--cue` to check only those. A cue with
`"verify": false` in `cues.json` is reported as `skipped` and never fails.

| Flag | Meaning |
|---|---|
| `SECTION:CUE …` | Check only these cues, such as `3:3.1eq`. |
| `--cue SECTION:CUE` | One more cue to check. Repeat for several. It adds to any positional cues. |
| `--only N` | Only the cues in these section numbers. Repeat the flag for several. |
| `--json`, `--strict`, `--no-fail` | As in [Machine-readable output](#machine-readable-output). |

```text
sec   cut at  before cut  result
 01    22.45   -129.7 dB  quiet
 02    56.69   -128.4 dB  quiet
 03   107.27   -128.5 dB  quiet
 04   133.69   -125.4 dB  quiet
 05   145.43   -120.0 dB  quiet

check                 cue       at   chg %   ctl %   offset     a/v  result
3:3.1eq             15.66    72.38    0.29    0.00    -20ms    -5ms  changed
3:3.1p1             13.05    69.77    0.24    0.00    -10ms    +5ms  changed
3:3.1up              4.10        -       -       -        -       -  skipped OPTED_OUT
5:5.1cap             2.46   136.14    0.30    0.00    -60ms   -85ms  changed
```

| Result | Certain | Meaning |
|---|---|---|
| `ok` | | The section starts on a real frame. |
| `BLACK` | yes | The section starts on a dark frame. |
| `quiet` | | The narration in the last `cut_window_seconds` before the cut is below `cut_max_db`. Clip sections are not listed. |
| `SPEECH AT CUT` | yes | The narration is still sounding there, so the cut is early. |
| `changed` | | The cue changed the picture within `max_offset_frames` of its time, and within `max_av_frames` of the click after a silent build. |
| `OFF CUE` | yes | The picture changed, but the first change sits further from the cue, or from the click, than that. |
| `NO CHANGE` | yes | The picture did not change enough, or no more than it was already changing. |
| `MISSING` | yes | The named cue is not in `beats.json`. |
| `skipped` | | The cue was not measured, for the reason printed beside it. `OPTED_OUT` means `"verify": false`, `REFERENCE_CLAMPED` means the cue fires inside the section's fade-in, `SECTION_NOT_ASSEMBLED` means the section has no cut, and `TOO_CLOSE_TO_END` means no probe fits before the section ends. |

`verify` exits 1 on any certain result. A cue row whose `a/v` column shows `-` after a
silent build carries the reason `NO_CLICK` in the JSON, which means no click lay within
`click_search_seconds` of the cued word. It is not a failure.
````

### 7. `### decktalk shots`

Add this row to the flag table, after the `--step ID` row:

```mdx
| `--cue ID` | Freeze the page at the moment this cue fires, with the earlier cues of the step shown. It needs exactly one `--step` and does not combine with `--section`. Repeat for one PNG per cue. |
```

### 8. `### decktalk build`

Replace the paragraph with:

```mdx
Runs narrate, beats, record, measure, check, assemble, and verify in order and prints
each stage's table. It stops with exit 1 when a cue phrase is not found, unless
`--allow-unresolved` is given, and when a cue id appears nowhere in its page, unless
`--allow-unknown` is given. It exits 1 when assembly or verification fails.
```

Add this row to the flag table, after `--allow-unresolved`:

```mdx
| `--allow-unknown` | Continue when a cue id in cues.json appears nowhere in the page that plays it. |
```

### 9. `### decktalk status`

After the example block, add:

```mdx
| Flag | Meaning |
|---|---|
| `--json` | As in [Machine-readable output](#machine-readable-output). `status` makes no findings, so `--strict` and `--no-fail` change nothing. |
```

### 10. `## Exit codes`

Replace the table and the paragraph under it with:

```mdx
| Code | Meaning |
|---|---|
| 0 | The command succeeded, or it found only uncertain verdicts, or `--no-fail` was given. |
| 1 | An error, printed to stderr as `error: …`, or a certain finding from `status`, `beats`, `check`, `verify`, or `doctor`, or an uncertain one under `--strict`. `build` exits 1 unless assembly and verification both pass. |
| 2 | A usage error, such as an unknown flag, a missing command, or `shots --cue` without exactly one `--step`, with the usage printed. |
| 130 | The command was interrupted. |

| Finding | Kind | Reported by |
|---|---|---|
| `PAGE ERROR`, `STALLED`, `TRUNCATED`, `NO COVER` | certain | `check` |
| `BLACK`, `SPEECH AT CUT`, `OFF CUE`, `NO CHANGE`, `MISSING` | certain | `verify` |
| An unmatched phrase, an unknown cue id | certain | `beats` |
| A missing tool | certain | `doctor` |
| `BLACK?`, `KATEX?` | uncertain | `check` |
| Speech shorter than `min_seconds` | uncertain | `beats` |

`--no-fail` turns every finding into exit 0, and `ok` in the JSON still says whether the
command passed. An error is not a finding, so a `DeckTalkError` exits 1 even with
`--no-fail`. An unexpected exception prints `error: <type>: <message> (add -v for the
traceback)` and exits 1, and `-v` re-raises it instead.
```

---

## docs/reference/card.mdx

### 1. `## Commands` code block

Replace the whole `console` block with:

```console
decktalk setup                              # once per machine: Chromium, ffmpeg, KaTeX
decktalk doctor                             # each tool and its path, exit 1 if one is missing
decktalk init DIR [--name NAME] [--force]   # scaffold, with KaTeX vendored into deck/katex/
decktalk build --silent                     # every stage with placeholder narration (a click per word), no key
decktalk build                              # the video, at build/out/<name>.mp4
decktalk build --only 3 --only 4 --preset veryfast   # re-record two sections, fast encode
decktalk beats                              # where each cue phrase resolved, exit 1 if one is missing or unknown to the page
decktalk verify                             # measure every cue's offset in the final mp4
decktalk verify 3:3.1eq --cue 5:5.1cap      # measure only these cues
decktalk verify --json                      # the same result as one JSON object on stdout
decktalk shots [--section N --at S]         # PNG per step, or a frame from a playing section
decktalk shots --step 3.1 --cue 3.1eq       # one step frozen at the moment one cue fires
decktalk status [--json]                    # timeline, what is built, captions and chapters
decktalk narrate --dry-run                  # what would be sent to the voice, without sending it
decktalk soundscape --dry-run               # same, for ambience, effects, and music
decktalk runtime                            # refresh deck/decktalk-runtime.js after an upgrade
```

### 2. The paragraph under the commands block

Replace it with:

```mdx
Every project command takes `-p DIR` or `--project DIR`, and the default is
`DECKTALK_PROJECT`, then the current directory. `-v` and `-q` go before or after the
command name. `--only` repeats and does not take commas. `build` verifies section starts
and cuts, and `verify` also measures every cue landing and its offset, or only the cues
named as `SECTION:CUE` arguments or with `--cue`. Every cut must be quieter than -40 dBFS
in its last 150 ms, reported as `quiet` or `SPEECH AT CUT`, and clip sections are exempt.
In a silent build the placeholder track carries a click at every word start. The cue
table then gains an `a/v` column, which is the picture's first change minus the click
nearest the cue. The `offset` column must sit within `max_offset_frames` of the cue, and
the `a/v` column within `max_av_frames` of the click. `status`, `beats`, `check`,
`verify`, and `doctor` take `--json` for one JSON object on stdout, `--strict` to fail on
a verdict ending in a question mark, and `--no-fail` to always exit 0.
[CLI](/reference/cli) lists every flag.
```

### 3. `### Reading a verify table`, the last paragraph (`The keyless order is …`)

Replace it with:

```mdx
The keyless order is `decktalk runtime` for a bare project, then `decktalk build --silent`,
then `decktalk verify`, then `decktalk shots`. `decktalk narrate --silent` followed by
`decktalk beats` shows the narration and cue tables before anything is recorded.
```

### 4. `## Exit codes and errors`, the row for code 1

Replace the row with:

```mdx
| 1 | Any `DeckTalkError`, printed to stderr as `error: …`, even with `--no-fail`. Also a certain finding from `status`, `beats`, `check`, `verify`, or `doctor`, such as an unmatched phrase, an unknown cue id, `TRUNCATED`, `PAGE ERROR`, `OFF CUE`, `NO CHANGE`, or a missing tool, and an uncertain one such as `BLACK?` or `KATEX?` under `--strict`. `--no-fail` makes those five exit 0. `build` exits 1 unless assembly and verification both pass. |
```

---

## docs/guides/ci-and-offline.mdx

### 1. `## The commands` code block and the paragraph under it

Replace the code block with:

```console
decktalk setup                            # Chromium, ffmpeg, and KaTeX, into per-user caches
decktalk doctor                           # exits 1 when a tool is missing
DECKTALK_VIDEO_PRESET=veryfast decktalk build --silent
decktalk verify                           # exits 1 when a cue did not land
decktalk shots                            # one PNG per step, worth uploading as an artifact
```

In the paragraph under it, replace the sentence
`The verify command with a list of SECTION:CUE checks is the test:` with:

```mdx
The verify command is the test, and with no arguments it checks every cue in
`beats.json`.
```

### 2. `## A GitHub Actions job`

In the YAML block, replace `      - run: decktalk verify 3:3.1eq 3:3.1p1 5:5.1cap` with:

```yaml
      - run: decktalk verify --json > verify.json
```

and add this step after the existing `upload-artifact` step:

```yaml
      - uses: actions/upload-artifact@v7
        if: always()
        with:
          name: verify
          path: verify.json
```

### 3. New section `## Asserting in CI`, inserted after `## A GitHub Actions job` and before `## Two flags that do not mix`

````mdx
## Asserting in CI

`status`, `beats`, `check`, `verify`, and `doctor` share one exit rule, so a job can use
their exit codes as its assertions. A certain finding, such as `OFF CUE`, `TRUNCATED`,
or an unknown cue id, exits 1. An uncertain finding, whose verdict ends in a question
mark, such as `BLACK?` on a page that is dark on purpose, exits 0 unless `--strict` is
given. A cue marked `"verify": false` in `cues.json` is reported as `skipped` and never
fails.

```console
decktalk check --strict      # fail on BLACK? and KATEX? too
decktalk verify              # fail on any certain finding
```

To keep the job running and decide later, add `--no-fail` and read the JSON. The object
on stdout carries `ok` and the finding counts, and progress stays on stderr.

```console
decktalk verify --json --no-fail > verify.json
jq -e '.ok' verify.json
jq '.verify.cues[] | select(.verdict != "changed")' verify.json
```

`jq -e` exits 1 when `ok` is `false`, so the first `jq` line is the assertion and the
second lists the cues to look at. [Machine-readable output](/reference/cli#machine-readable-output)
lists every key.
````

### 4. `## Next`

No change.

---

## docs/reference/python-api.mdx

### 1. `## Stages and results` table

Replace these rows:

```mdx
| `resolve_beats(project, *, allow_unknown)` | `BeatsResult` with `beats`, `sections`, `unresolved`, `unknown`, `estimated`, and `problems`. |
| `check(project, only)` | A list of `RecordingCheck`, each with the luma fields, `verdict`, and `ok`. |
| `verify(project, checks, only)` | `VerifyResult` with `total_seconds`, `starts`, `cuts`, `cues`, and `black_starts`. `checks=None` checks every cue in `beats.json`, filtered by `only`. Each `CueCheck` has a `verdict` and a `reason`. Its `ok` is true only when every start, cut, and measured cue check passed, and skipped cues do not count. |
| `build(project, *, silent, force, only, nomix, loudnorm, strict, allow_unresolved, allow_unknown, report)` | `BuildResult` with one field per stage and `ok`, which mirrors the CLI's exit code. `report(stage, result)` is called after each stage. |
```

and add this row after the `build` row:

```mdx
| `status(project)` | `StatusReport` with the project's paths, `sections`, `timeline`, `beats_sections`, `final`, `final_duration`, and `outputs`. It reads what exists and runs nothing. |
```

### 2. New paragraph directly under the `## Stages and results` table

```mdx
`BeatsResult`, `RecordingCheck`, `VerifyResult`, and `StatusReport` each have
`to_dict(root)`, which returns the JSON-ready data that the CLI prints under `--json`.
Numbers stay numbers, verdicts are the strings the tables print, and paths are relative to
`root` with forward slashes. Pass `project.root`.

```python
import json
import decktalk

project = decktalk.Project.load("my-lesson")
result = decktalk.verify(project)
print(json.dumps(result.to_dict(project.root), indent=2))
```
```

### 3. `## Every public name`

Add `StatusReport` after `SpeechRequest`, and `status` after `soundscape`, keeping the
list alphabetical.

---

## README.md

No change. The README's `decktalk verify 1:1.1curve 1:1.1number 3:3.1eq 4:4.1s3` example
still works, because the positional list is kept.
