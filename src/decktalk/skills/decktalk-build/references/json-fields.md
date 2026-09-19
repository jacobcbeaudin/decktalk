# The JSON a build reads

Every command with `--json` prints exactly one object on standard output and every log line on
standard error.

## The envelope

| Field | What it holds |
| --- | --- |
| `schema` | The envelope's shape version, which starts at 1 |
| `version` | The installed DeckTalk version |
| `command` | The command as typed |
| `ok` | True when the command found nothing and raised nothing |
| `exit_code` | The code the process is about to return |
| `summary` | A small object of counts, whose keys belong to the command |
| `findings` | `{"certain": n, "uncertain": n, "items": [...]}` |
| `written` | Every file the command wrote, as project-relative paths |
| `error` | Null on success and on a finding, and an object on an error |
| `<command>` | The command's own payload, under the first word of its name |

## findings.items[]

Every row has the same shape on every command, and the certain rows come first.

| Key | What it holds |
| --- | --- |
| `code` | The verdict code, such as `OFF_CUE` or `THIN_CHANGE` |
| `label` | The printed label, which nothing parses |
| `certain` | True when the finding is wrong for sure |
| `section` | The section number, or null |
| `cue` | The cue id, or null |
| `where` | The file, page or artifact the finding is about, or null |
| `detail` | One sentence of detail, or null |

A verdict inside a command's own payload is the same `{code, label, certain}` object, so a row is
matched by `verdict.code` and never by the label.

## error

| Key | What it holds |
| --- | --- |
| `code` | One of `CONFIG`, `MISSING_INPUT`, `PROVIDER`, `TOOL`, `USAGE` and `INTERNAL` |
| `message` | One sentence, which never carries a secret's value |
| `hint` | The smallest next action, or null |
| `path` | The project-relative file the error is about, or null |
| `line` | The line in that file, or null |

## Exit codes

| Code | What it means | What to do |
| --- | --- | --- |
| 0 | The command ran and found nothing | Go on |
| 1 | The command ran and found something, and `error` is null | Read `findings.items[]` and fix the project |
| 2 | The command line was wrong | Correct the command |
| 3 | DeckTalk could not run | Stop and tell the user what `error.message` says |
| 130 | The run was interrupted | No envelope is promised |

`--strict` makes an uncertain finding fail as a certain one does. `--exit-zero` exits 0 whatever was
found, and a skill never passes it, because the exit code is the guard.

## The payloads a build reads

`status.sections[]` carries `key`, `kind`, `source`, `recorded`, `cut` and `stale`, where `stale` is
one sentence saying why the recording no longer matches the project, or null when it still does.
Every `key` in every payload is the section number padded to two digits, as a string, such as `"04"`,
while `--only` takes the plain number, so `--only 4`.
`status.narration` carries `exists`, `estimated`, `total_seconds` and a `sections[]` of `key`,
`title`, `start`, `end` and `duration`, which is where each section lands once the takes are joined.
`status.final` carries the
mp4's `path`, `exists` and `duration`. `status.run` is null when no build is going, and otherwise
carries `pid`, `started`, `stage`, `sections_done`, `sections_total` and `alive`.

`narrate.sections[]` carries `key`, `chapter`, `status`, `reason`, `file`, `hash`,
`characters_sent`, `characters_spoken`, `characters_with_context`, `word_count`,
`estimated_seconds`, `placeholders` and `request`, whose `text` is the exact string the voice
receives. A `status` of `synthesize` is a section a voiced run will pay for, `cached` is a take
already on disk, and `unknown` is a section whose cache could not be checked because no voice was
set up. `narrate.totals` carries `synthesize`, `cached`, `unknown`, `characters_sent`,
`characters_spoken`, `characters_with_context`, `characters_unchecked`,
`price_per_1000_characters`, `estimated_cost` and `most_it_can_cost`. `narrate.voice` carries the
`provider`, the `model` and the settings, and `narrate.takes` is null on a dry run. `narrate` also
carries `note`, one sentence about the run or null, `notes`, the wording findings the script raised,
and `synthesized` and `cached`, the section keys this run actually voiced and actually reused. Those
two count work done, so both are empty on a dry run, and what a run would do is
`narrate.totals.synthesize` and `narrate.totals.cached`.

`align.sections[]` carries `key`, `speech_end_seconds`, `min_seconds`, `skipped`, a `cues` object that maps
each cue id to its second on the section clock, and `notes`, which holds one finding row for every
judgement and every advisory note that section made. `align` also
carries `unresolved`, `unknown` and `uncued` counts.

`preflight.takes[]` has the same rows as `narrate.sections[]`, and `preflight.placeholders` carries
one `PLACEHOLDER` finding row per placeholder still open. `preflight.cues[]` carries `section`, `cue`, `cue_seconds`, `slide`,
`changed_percent`, `verdict`, `reason`, `detail`, `note`, `before` and `after`. The `before` and
`after` values are PNG paths. The array is filled only when preflight freezes frames, so
`--no-frames` leaves it empty.

`record.recordings[]` carries `key`, `file`, `kept`, `seconds`, `t0_seconds`, `t0_method`,
`t0_guessed`, `duration`, `wanted`, `luma`, `verdicts`, `stall_ms`, `page_errors` and `assets`.

`verify.starts[]` carries `key`, `start`, `probe_at`, `yavg`, `ymax` and `verdict`.
`verify.cuts[]` carries `key`, `cut_at`, `rms_db` and `verdict`. `verify.seams[]` carries `key`,
`cut_at`, `last_at`, `first_at`, `changed_percent` and `verdict`. `verify.cues[]` carries `section`,
`cue`, `cue_seconds`, `final_seconds`, `changed_percent`, `control_percent`, `offset_ms`, `av_ms`,
`verdict` and `reason`. `verify.recordings[]` repeats what each recording log judged, as `key`,
`where`, `t0_method`, `verdicts` and `page_errors`.

`assemble.captions` carries the `srt`, `vtt`, `chapters` and `transcript` paths, and `assemble` also
carries `final`, `duration`, `stamped`, `sections[]`, `cuts`, `poster`, `loudness` and `warnings`.
`assemble.loudness.problems[]` holds one `LOUDNESS_MISS` finding row per target the pass could not hit,
and `assemble.substituted[]` holds one `SLATE` finding row per section a slate or a black frame
stands in for.

`screenshots.files[]` lists one row per PNG, each with `file`, `page`, `slide`, `cue`, `section`,
`at` and `page_errors`, and `written` lists the same paths. `--slide` shoots one slide of a page and
fills `page` and `slide`, leaving `section`, `cue` and `at` null. `--section` plays a section and
fills `section` and `at`, and it fills `cue` on the frame a cue names.

`build` carries one key per stage it ran, each holding that stage's own payload, plus `stages` for
the stages this run executed and `progress` for the log it wrote.

## build/progress.jsonl

One JSON object per line, appended as the run goes, and truncated when the run starts. Each line
carries `ts`, `pid`, `stage`, `stage_index`, `stage_count`, `section`, `event` and `detail`. The
`event` is `start`, `done`, `skip` or `fail`. Poll `decktalk status --json` rather than tailing this
file, and read the file when a run failed and you need the stage it failed in.
