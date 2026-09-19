"""The static page scan: what the measured catalog says about a slide, without looking at a picture.

The probe lays every slide out once and records one row per revealed element with its box in stage
pixels. That is enough to judge three things a model reading a frame could not state precisely: a
reveal placed outside the stage, a reveal under the band the captions occupy, and two cues so close
in time that one reveal is still playing when the next fires. The fourth judgement here is about a
page's own requests rather than its layout: an asset fetched from another origin while the page is
recorded is a file the film does not own.

Everything in this module is arithmetic over plain data, so `preflight` and `record` reach the same
verdicts from the catalog each of them already has, and neither imports the other to do it.
"""

from __future__ import annotations

from typing import Any

from .verdicts import Finding, Verdict

# The band the captions occupy, as a share of the stage's height measured from the bottom. A reveal
# under it is hidden from any viewer reading captions, which is the accessible-video rule the slide
# guidance states as "keep every cued element out of the bottom fifteen percent".
CAPTION_BAND = 0.15

# The crossfade the runtime plays between two slides, in seconds, which is `--dt-xfade`'s default. A
# page may set the property itself, and the scan cannot read a page's CSS, so this is the length the
# runtime promises rather than the one a particular deck chose.
REVEAL_SECONDS = 0.35


def _box(row: dict[str, Any]) -> tuple[int, int, int, int] | None:
    box = row.get("box")
    if not isinstance(box, dict):
        return None
    try:
        return int(box["x"]), int(box["y"]), int(box["w"]), int(box["h"])
    except (KeyError, TypeError, ValueError):
        return None


def off_stage(rows: list[dict[str, Any]], *, width: int, height: int, page: str, section: int | None) -> list[Finding]:
    """One row per cued element whose box is not wholly inside the stage, which no viewer ever sees."""
    out: list[Finding] = []
    for row in rows:
        cue, box = row.get("cue"), _box(row)
        if not cue or box is None:
            continue
        x, y, w, h = box
        if x >= 0 and y >= 0 and x + w <= width and y + h <= height:
            continue
        out.append(
            Finding(
                verdict=Verdict.OFF_STAGE,
                section=section,
                cue=cue,
                where=page,
                detail=(
                    f"the reveal at cue {cue!r} sits at {x},{y} and is {w} by {h}, which leaves the "
                    f"{width} by {height} stage, so part of it is never on screen."
                ),
            )
        )
    return out


def in_caption_band(
    rows: list[dict[str, Any]], *, height: int, page: str, section: int | None, band: float = CAPTION_BAND
) -> list[Finding]:
    """One row per cued element that reaches into the band the captions are drawn over."""
    top = round(height * (1 - band))
    out: list[Finding] = []
    for row in rows:
        cue, box = row.get("cue"), _box(row)
        if not cue or box is None:
            continue
        _, y, _, h = box
        if y + h <= top:
            continue
        out.append(
            Finding(
                verdict=Verdict.IN_CAPTION_BAND,
                section=section,
                cue=cue,
                where=page,
                detail=(
                    f"the reveal at cue {cue!r} reaches {y + h} pixels down, past the caption band "
                    f"that starts at {top}, so a viewer reading captions may not see it."
                ),
            )
        )
    return out


def cues_overlap(
    times: dict[str, float], *, page: str, section: int | None, gap: float = REVEAL_SECONDS
) -> list[Finding]:
    """One row per pair of cues closer together than a reveal lasts, whose animations run into each other."""
    ordered = sorted(times.items(), key=lambda item: (item[1], item[0]))
    out: list[Finding] = []
    for (first, at), (second, then) in zip(ordered, ordered[1:], strict=False):
        apart = round(then - at, 3)
        if apart >= gap:
            continue
        out.append(
            Finding(
                verdict=Verdict.CUES_OVERLAP,
                section=section,
                cue=second,
                where=page,
                detail=(
                    f"the cue {second!r} fires {apart:.2f}s after {first!r}, which is less than the "
                    f"{gap:.2f}s a reveal plays for, so the two run into each other."
                ),
            )
        )
    return out


def cdn_assets(origins: list[str], *, page: str, section: int | None = None) -> list[Finding]:
    """One row per other origin a page reached for, which the film does not own and cannot replay."""
    return [
        Finding(
            verdict=Verdict.CDN_ASSET,
            section=section,
            where=page,
            detail=(
                f"the page loaded an asset from {origin}, which the project does not own, so the "
                "recording depends on a host that may change or be unreachable."
            ),
        )
        for origin in sorted(set(origins))
    ]


def slide_findings(
    rows: list[dict[str, Any]],
    times: dict[str, float],
    *,
    width: int,
    height: int,
    page: str,
    section: int | None,
) -> list[Finding]:
    """Every static judgement one slide's measured rows and resolved cue times support, in one call."""
    return [
        *off_stage(rows, width=width, height=height, page=page, section=section),
        *in_caption_band(rows, height=height, page=page, section=section),
        *cues_overlap(times, page=page, section=section),
    ]
