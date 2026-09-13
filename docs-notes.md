# Docs notes from Track B (pipeline)

Each entry names the page, the section, and the exact replacement text. Apply them in the
docs pass after the merge. Flag rows for `--allow-unknown`, `--cue`, `--only`, `--json`,
`--strict`, and `--no-fail` belong to Track A's notes, and the runtime side of
`?step=&cue=` belongs to Track C's notes. Where an entry below mentions one of those flags,
it only describes what the pipeline does with it.

---

## docs/guides/clip-section.mdx

### Section "## Where a clip may sit"

Replace the whole section body with:

```mdx
## Where a clip may sit

The narration is one continuous track that begins with the first page section, and each
page section is cut to its own span in that track. A clip therefore sits before the first
page section or after the last one, and `decktalk.toml` refuses to load when a clip sits
between two page sections. A clip there would play while the narration of the following
section is already running, so that section's words and picture would no longer agree.

```text
error: decktalk.toml: [[section]] number=2 is a clip between page sections 1 and 3. The narration is one continuous track placed at the first page section, so a clip in the middle would play over the words of section 3. Move the clip before section 1 or after section 3, or make it a page section.
```

Open on a clip, close on a clip, or both. A clip of your own in the middle of a lesson
becomes a page section that plays the video, or a separate video.
```

---

## docs/reference/decktalk-toml.mdx

### Section "## `[[section]]`", the `clip` row of the key table

Replace the row with:

```mdx
| `clip` | clip | required | Your video file, with its own audio. It is scaled and padded to the frame size. A clip sits before the first page section or after the last one. [Add a clip section](/guides/clip-section#where-a-clip-may-sit) explains why. |
```

### Same section, after the paragraph that begins "A missing clip is not an error"

Add this paragraph:

```mdx
A clip between two page sections is an error at load, because the narration is one
continuous track placed at the first page section and a clip in the middle would play
over the words of the section after it. The message names the clip, the page sections on
either side, and the two places the clip may move to.
```

---

## docs/help/troubleshooting.mdx

### Accordion "unknown cue id in the recorder's log"

Replace the accordion with these two accordions:

```mdx
  <Accordion title="N cue id(s) in cues.json appear nowhere in the page that plays them">
    `beats` reads the page of every section with cues and looks for each cue id as a
    quoted literal, such as `data-cue="4.1answer"`, a key of a step's `cues` object, or a
    key of an `on` handler. An id it cannot find is a typo in `cues.json` or a step that
    was never given the cue, so the page would never reveal it. The beats table notes
    `4.1answer: not in deck/index.html`, `beats` exits 1, and `build` stops after the
    beats table. Add `data-cue="4.1answer"` to the step, fix the id in `cues.json`, or pass
    `--allow-unknown` when the page builds its ids at run time and never spells them out.
    [Cues](/concepts/cues#unknown-cue-ids) has the rule.
  </Accordion>

  <Accordion title="unknown cue id in the recorder's log">
    The page received a cue in `?beats=` that no step owns. The static check in `beats`
    catches an id that the page never mentions, so this warning means the page mentions
    the id but no step claims it. The warning is also written to the sidecar. Check the
    id against the step ids and their `cues` lists, and remember that a cue belongs to the
    step whose id is its longest prefix. [The page contract](/concepts/page-contract#cue-ownership)
    has the rules.
  </Accordion>
```

### After the accordion "hold_seconds is allowed only on the last page section"

Add this accordion:

```mdx
  <Accordion title="is a clip between page sections">
    The narration is one continuous track placed at the first page section, so a clip
    between two page sections would play over the words of the section after it. Move the
    clip before the first page section or after the last one, or make it a page section.
    [Add a clip section](/guides/clip-section#where-a-clip-may-sit) explains the rule.
  </Accordion>
```

### Accordion "Loudness warnings on a silent build"

Replace the accordion with:

```mdx
  <Accordion title="[loud] skipped on a silent build">
    A silent build's narration is clicks and silence, so there is no speech to normalize.
    `assemble` skips the loudness pass and logs
    `[loud] skipped: the narration is a silent placeholder, so there is no speech to normalize, and the clicks stay at -24 dBFS for the a/v check`.
    The clicks keep their level so that `verify` can find them, and `--strict` has no
    loudness to fail on. A voiced build runs the pass as usual.
  </Accordion>
```

### After the accordion "OFF CUE for a cue in verify"

Add this accordion:

```mdx
  <Accordion title="skipped for a cue in verify">
    A skipped row measured nothing and never fails the check. The note names the reason.
    `REFERENCE_CLAMPED` means the cue sits so close to the section start, or inside the
    section's fade-in, that no reference frame fits before it, which is usual for a
    `$start` cue. `SECTION_NOT_ASSEMBLED` means the section has no `NN-section.mp4`, so run
    `decktalk assemble`. `TOO_CLOSE_TO_END` means every probe would fall past the end of
    the section, so give the section more `extra_seconds` or move the cue earlier.
    `OPTED_OUT` means `cues.json` sets `"verify": false` on the cue, and naming the cue on
    the command line measures it anyway.
  </Accordion>
```

### Accordion "a/v offset: picture N ms from the click"

Append this paragraph at the end of the accordion body:

```mdx
    When no click is found near the cued word, the `a/v` column is empty and the JSON row
    carries the reason `NO_CLICK`. The cue is still judged on its `offset`.
```

---

## docs/guides/ci-and-offline.mdx

### Section "## Two flags that do not mix"

Delete the whole section, from the heading through the paragraph that ends "Use
`--strict` on voiced builds only." A silent build skips the loudness pass, so `--silent`
and `--strict` combine without a false failure.

---

## docs/concepts/how-it-works.mdx

### The stage table, the `verify` row

Replace the row with:

```mdx
| `verify` | Confirms that every section opens on a real frame, that the narration is quiet in the last 150 ms before every cut, and that each cue in `beats.json` changed the picture on time. After a silent build it also measures the picture against the placeholder click at the cued word. | nothing |
```

### Section "## What verify measures", the first paragraph

Replace the paragraph with:

```mdx
`build` verifies section starts and cuts. It probes each section shortly after its start,
past any dip to black, and reports `BLACK` when nothing is on screen. It then measures the
narration in the last `cut_window_seconds` before every cut, 150 ms by default, and
reports `SPEECH AT CUT` when the level is above `cut_max_db`, -40 dBFS by default, so a
narration tail that is too short is caught rather than heard. A clip section carries its
own audio and is exempt. `decktalk verify` also measures every cue in `beats.json` in the
finished mp4, in section order and then cue time, except the cues that `cues.json` marks
`"verify": false`. Naming cues as `SECTION:CUE` measures only those.
```

### Same section, the `result` row of the column table

Replace the row with:

```mdx
| `result` | `changed` when the best probe changed at least `min_changed_percent` of the pixels and beat its control by `min_margin_percent`, and the offset is within `max_offset_frames` of the cue, and the `a/v` value, when there is one, is within `max_av_frames`. `OFF CUE` when the picture changed but the onset sits further from the cue than that. `NO CHANGE` when the picture did not change enough. `UNRESOLVED` when a named cue is not in `beats.json`. `skipped` with a reason when nothing could be measured, which never fails the check. |
```

### Same section, after the paragraph that begins "The onset threshold is far below"

Add this subsection:

```mdx
### The reference frame and skipped cues

The reference frame sits `lead_seconds` before the cue. It never sits inside the section's
fade-in, where the picture is still coming up from black, and it always sits at least one
frame before the cue. A section whose page fades itself in has no fade-in in the cut, so
its reference may sit on the section's first frame. When no frame fits, the cue is
reported as `skipped` with a reason instead of being measured against the wrong frame.

| Reason | Meaning |
|---|---|
| `REFERENCE_CLAMPED` | The cue sits at the section start or inside its fade-in, which is usual for a `$start` cue. |
| `SECTION_NOT_ASSEMBLED` | The section has no `NN-section.mp4`. |
| `TOO_CLOSE_TO_END` | Every probe would fall past the end of the section. |
| `OPTED_OUT` | `cues.json` sets `"verify": false` on the cue. |

A reveal that is too small or too slow for a frame difference, such as a thin arrow or a
term that fades in over a second, is a good candidate for `"verify": false`. Check it
by eye with `decktalk shots --step ID --cue CUE` instead.
```

### Section "## Loudness", the last paragraph

Replace the paragraph that begins "A result more than one LU from the target" with:

```mdx
A result more than one LU from the target or above the ceiling is reported as a warning,
and `--strict` turns it into an error. The `--no-loudnorm` flag skips the pass, and the
targets are `[mix.loudnorm]` in [decktalk.toml](/reference/decktalk-toml#mixloudnorm).

A silent build skips the pass on its own, because its narration is clicks and silence and
there is no speech to normalize. The clicks stay at -24 dBFS, which is where the `a/v`
check listens for them, and the log says so.

```text
[loud] skipped: the narration is a silent placeholder, so there is no speech to normalize, and the clicks stay at -24 dBFS for the a/v check
```
```

---

## docs/reference/cli.mdx

### Section "### `decktalk beats`"

Replace the body with:

```mdx
Resolves every cue phrase to a second and prints a table of sections, their speech end,
their `min_seconds`, and their resolved cues. It also reads each section's page and
notes every cue id that the page never mentions as a quoted literal, as
`4.1answer: not in deck/index.html`. It writes `beats.json` either way, and exits 1 when a
phrase is not found or a cue id is unknown, unless `--allow-unknown` is given for the
unknown ids.
```

### Section "### `decktalk assemble`", the `--strict` row

Replace the row with:

```mdx
| `--strict` | Fail on a missing clip or recording instead of substituting a slate or black, and fail on a loudness miss. A silent build skips the loudness pass, so it has no loudness to fail on. |
```

### Section "### `decktalk verify [SECTION:CUE ...]`", the result table

Replace the `MISSING` row with these two rows:

```mdx
| `UNRESOLVED` | A cue named on the command line is not in `beats.json`. |
| `skipped` | Nothing was measured, and the note gives the reason: `REFERENCE_CLAMPED`, `SECTION_NOT_ASSEMBLED`, `TOO_CLOSE_TO_END`, or `OPTED_OUT`. A skipped row never fails the check. |
```

In the paragraph above the example, replace "With arguments, it also confirms that the
picture changed at each named cue" with:

```mdx
It also confirms that the picture changed at every cue in `beats.json`, except the cues
that `cues.json` marks `"verify": false`, or at only the cues named as arguments
```

### Section "### `decktalk shots`"

After the paragraph that begins "Step screenshots land at", add:

```mdx
With one `--step` and one or more `--cue`, each shot freezes the step at the moment that
cue fires and lands at `build/shots/step-<step>-cue-<cue>.png`, such as
`step-3.1-cue-3.1eq.png`. Giving `--cue` with no step or with several steps is an error.
```

### Section "### `decktalk build`"

Replace the first paragraph with:

```mdx
Runs narrate, beats, record, measure, check, assemble, and verify in order and prints
each stage's table. It stops with exit 1 after the beats table when a cue phrase is not
found, unless `--allow-unresolved` is given, and when a cue id appears nowhere in the page
that plays it, unless `--allow-unknown` is given. It exits 1 when assembly or
verification fails. The verify stage checks section starts and cuts, and a plain
`decktalk verify` afterwards measures every cue.
```

Add this row to its flag table, after `--allow-unresolved`:

```mdx
| `--allow-unknown` | Build even if some cue ids in `cues.json` appear nowhere in their page, for a page that builds its ids at run time. |
```

---

## docs/reference/cues-json.mdx

### Section "## Keys of a cue", the table

Replace the `offset` row and add a `verify` row after `case_sensitive`:

```mdx
| `offset` | no | Seconds added to the match. It may be negative. `$start` plus a negative offset is written as is, and the page fires it at t=0. A `$start` cue with no positive offset has no frame before it, so `verify` reports it as `skipped` with `REFERENCE_CLAMPED`. |
| `verify` | no | `false` leaves the cue out of a plain `decktalk verify`, for a reveal too small or too slow for a frame difference to measure. The row reads `skipped` with `OPTED_OUT`. Naming the cue as `SECTION:CUE` measures it anyway. The default is `true`. |
```

### Section "## Shape"

Append this paragraph:

```mdx
Every cue id must appear in the section's page as a quoted literal, such as
`data-cue="3.1eq"` or a key of a step's `cues` object. `beats` reports an id it cannot
find, and `build` stops on it unless `--allow-unknown` is given.
```

---

## docs/concepts/cues.mdx

### After the section "## Unresolved cues", a new section

```mdx
## Unknown cue ids

A phrase can resolve while its cue id is wrong. `beats` therefore reads each section's
page and looks for every cue id as a quoted literal, such as `data-cue="4.1answer"`, a key
of a step's `cues` object, or a key of an `on` handler. An id that appears nowhere in the
page is noted and counted, and `beats` exits 1.

```text
sec  speech   need  cues
 04    24.6      -  4.1change@0.0,4.1answer@2.51,4.1build@5.02
                    ! 4.1answer: not in deck/index.html
```

The `build` command stops after the beats table, because the page would never reveal the
cue. Add `data-cue="4.1answer"` to the step, fix the id in `cues.json`, or pass
`--allow-unknown` for a page that builds its ids at run time. Clip sections have no page
and are not checked. The runtime keeps its own warning for an id that the page mentions
but no step owns, which lands in the recorder's sidecar.
```

---

## docs/concepts/page-contract.mdx

### Section "## Cue ownership"

Append this paragraph:

```mdx
Before anything is recorded, `decktalk beats` checks the other direction. Every cue id in
`cues.json` must appear in the page as a quoted literal, whether as `data-cue="ID"`, a
key of a step's `cues` object, or a key of an `on` handler. An id that the page never
mentions stops `build`, because no step could ever reveal it. A page that assembles its
ids from pieces at run time passes `--allow-unknown`, and the runtime warning above is
then the only check.
```

---

## docs/reference/artifacts.mdx

### Section "## `build/audio/manifest.json`", the field table

Add this row after the `segments.NN.target_seconds` row:

```mdx
| `segments.NN.spoken` | The words the voice was given, without break tags, with the script's punctuation and case. Captions take their spelling from it, so they match the audio even after the script changes. A manifest without it falls back to the script. |
```

---

## docs/reference/python-api.mdx

### Section with the stage function table

Replace these rows:

```mdx
| `resolve_beats(project, *, allow_unknown)` | `BeatsResult` with `beats`, `sections`, `unresolved`, `unknown`, `estimated`, `problems`, and `to_dict(root)`. Unknown cue ids raise `UnknownCueError`, a `ConfigError` whose `result` is the full result, unless `allow_unknown` is true. `beats.json` is written either way. |
| `check(project, only)` | A list of `RecordingCheck`, each with the luma fields, `verdict`, `ok`, `file`, and `to_dict(root)`. |
| `verify(project, checks, only)` | `VerifyResult` with `final`, `total_seconds`, `silent`, `starts`, `cuts`, `cues`, `black_starts`, and `to_dict(root)`. `checks=None` measures every cue in `beats.json` except those opted out, an empty list measures none, and `only` keeps the cues of those sections. Each cue row has `verdict` and `reason`. Its `ok` is true when every start, cut, and measured cue passed, and skipped rows never count against it. |
| `shoot(project, *, pages, steps, section, at, cues)` | The screenshot paths. `cues` with a single step freezes the step at each cue. |
| `build(project, *, silent, force, only, nomix, loudnorm, strict, allow_unresolved, allow_unknown, report)` | `BuildResult` with one field per stage and `ok`, which mirrors the CLI's exit code. `report(stage, result)` is called after each stage. |
```
