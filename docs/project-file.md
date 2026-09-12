# decktalk.toml reference

One file per project. Paths are relative to the project directory. Unknown top-level
tables are an error, so a typo cannot silently do nothing.

## `[project]`

| Key | Default | Meaning |
|---|---|---|
| `name` | directory name | Output name: `build/out/<name>.mp4`. |
| `script` | `script.md` | The narration. |
| `cues` | `cues.json` | Cue phrases. Optional file; without it pages run their built-in timing. |
| `build` | `build` | Where generated files go. |

## `[voice]`

ElevenLabs voice settings for this presentation. `model` overrides the tool default
(`[narration] model`, normally `eleven_multilingual_v2`).

| Key | Default |
|---|---|
| `model` | tool default |
| `stability` | 0.55 |
| `similarity_boost` | 0.75 |
| `style` | 0.0 |
| `speaker_boost` | true |
| `speed` | 1.0 |

## `[[section]]`

One per `## N.` section of the script, any order; sorted by `number`. Every script
section must have one. A section is either a page or a clip.

| Key | Applies to | Default | Meaning |
|---|---|---|---|
| `number` | both | required | Matches `## N.` in the script. |
| `title` | both | `""` | Shown on slates and in status. |
| `page` | page | required | HTML file, e.g. `deck/index.html`. |
| `scene` | page | `number` | Value of `?scene=` the page receives. Number or string. |
| `extra_seconds` | page | 0.3 | Recorded past the narration span, so the cut never lands on an undrawn frame. |
| `hold_seconds` | page | 0 | Extra seconds the last frame holds. Allowed only on the last page section: the narration is continuous, so a hold earlier would push every later visual off its words. |
| `ambience` | page | false | Play the ambience bed under this section. |
| `params` | page | `{}` | Extra query parameters for the page, as a table: `[section.params] theme = "dark"`. |
| `clip` | clip | required | Your video file, with its own audio. |
| `slate_seconds` | clip | 5 | Length of the titled slate that plays when the clip is missing. |

## `[transition]`

| Key | Default | Meaning |
|---|---|---|
| `dips` | every cut | Boundaries that dip to black, as `[[from, to], …]` section numbers. Omit the key to dip at every cut; set `[]` for no dips. |
| `dip_seconds` | 0.15 | Fade length, video only. Audio never dips. |
| `page_fades_in` | true | Pages fade themselves up, so no fade-in is added on their side of a dip. |

## `[mix]`

| Key | Default | Meaning |
|---|---|---|
| `underscore` | none | Music bed file. Looped, ducked under speech. |
| `underscore_db` | -24 | Bed level. |
| `underscore_duck_db` | -6 | Additional attenuation under speech. |
| `underscore_fade_in`, `underscore_fade_out` | 2, 3 | Seconds. |
| `markers` | none | JSON file that swells or mutes the bed at spoken phrases (see below). |
| `ambience` | none | Ambience bed file, under sections flagged `ambience = true`. |
| `ambience_db` | -20 | |
| `slate` | rendered | A PNG to use instead of the rendered titled slate. |
| `[[mix.sfx]]` | none | One-shots: `file`, `section`, `cue`, `db` (-16), `offset` (0). Placed where `cue` resolved in that section. Skipped with a warning if unresolved. |
| `[mix.loudnorm]` | I -16, TP -1.5, LRA 11 | EBU R128 targets for the final mix. |

`markers.json`:

```json
{ "boost_db": 3, "boost_seconds": 2,
  "markers": [ { "name": "open", "section": 1, "on": "$start" },
               { "name": "zero", "section": 2, "on": "worth nothing", "mute_seconds": 0.6 } ] }
```

`on` is a phrase, `$start` or `$end`, like a cue; `occurrence`, `case_sensitive` and
`offset` refine it. A marker swells the bed by `boost_db` for `boost_seconds`; with
`mute_seconds` it silences the bed for that long first.

## `[soundscape]`

Prompts for `decktalk soundscape`. Outputs default to the paths named in `[mix]`.

| Table | Keys |
|---|---|
| `[soundscape.ambience]` | `text` (required), `duration_seconds` (25), `prompt_influence` (0.3), `model_id`, `out` |
| `[soundscape.sfx.<name>]` | `text` (required), `duration_seconds` (0.5), `prompt_influence` (0.5), `model_id`, `out` |
| `[soundscape.music]` | `prompt` (required), `seconds` (360), `force_instrumental` (true), `model_id`, `out` |

## Tuning tables

Any settings section can be overridden per project with a table of the same name, and
per run with `DECKTALK_<SECTION>_<FIELD>`. The fields and defaults are the dataclasses in
`decktalk/config.py`:

| Table | What it tunes |
|---|---|
| `[video]` | width, height, fps, preset, crf, audio_bitrate, sample_rate, channels, slate_color |
| `[narration]` | model, output_format, pacing estimate, break and tail lengths, context chars, timeout |
| `[record]` | settle_seconds, marker_ms, color_scheme, shot_settle_ms |
| `[align]` | magenta detection, painted and black thresholds, truncation slack |
| `[audio]` | duck and ambience ramps, marker ramps, limiter |
| `[verify]` | probe timing, diff level, minimum changed share, probe size |
| `[elevenlabs]` | API base, sound and music models, chunking, default durations, timeout |

## `cues.json`

```json
{ "sections": { "3": { "min_seconds": 25,
  "cues": [ { "step": "3.1", "on": "$start" },
            { "step": "3.1draw", "on": "curve draws" },
            { "step": "3.1eq", "on": "equation", "offset": 0.2, "occurrence": 1 } ] } } }
```

`step` is a cue id the page understands; `on` is a spoken phrase from that section
(first occurrence, case-insensitive, punctuation ignored), `$start`, or `$end`.
`min_seconds` warns when the narration is shorter than the visuals need.
