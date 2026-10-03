"""How late this machine presents a frame its browser has already drawn, which `decktalk doctor --measure` reports."""

from __future__ import annotations

import statistics

from ..errors import ToolError
from .browser import TRUSTED, chromium
from .pages import evaluate, open_document

MEASURED_FRAMES = 12
"""Calibration: how many frames the bias is measured over, which is enough for the middle one to settle."""

MEASURED_FRAME_MS = 60
"""Calibration: how long one measured frame is made to take, which is past the browser's own long-frame floor."""

# The page the bias is measured on. It draws nothing anyone looks at: it holds the main thread for
# longer than a frame, so the browser reports that frame with the work it did and the moment the
# compositor put it on the screen, and the gap between the two is the bias. A browser that reports
# no presentation time answers with an empty list, and the machine is told rather than given a guess.
BIAS_JS = """() => new Promise((done) => {
  const kinds = window.PerformanceObserver ? PerformanceObserver.supportedEntryTypes || [] : [];
  if (!kinds.includes("long-animation-frame")) { done([]); return; }
  const seen = [];
  const watch = new PerformanceObserver((list) => {
    for (const entry of list.getEntries()) {
      if (entry.presentationTime === undefined) continue;
      seen.push(entry.presentationTime - (entry.startTime + entry.duration));
    }
  });
  watch.observe({ type: "long-animation-frame" });
  let left = FRAMES;
  const hold = () => {
    const until = performance.now() + HOLD_MS;
    while (performance.now() < until) { /* holding the thread is what makes the frame a long one */ }
    document.documentElement.style.background = left % 2 ? "#000" : "#fff";
    left -= 1;
    if (left > 0) { requestAnimationFrame(hold); return; }
    requestAnimationFrame(() => setTimeout(() => { watch.disconnect(); done(seen); }, 0));
  };
  requestAnimationFrame(hold);
})"""
"""The measurement, with `FRAMES` and `HOLD_MS` standing where the two constants above go."""


def bias_script(frames: int, hold_ms: int) -> str:
    """The measurement as the page receives it, which is the one place the two constants are written in."""
    return BIAS_JS.replace("FRAMES", str(frames)).replace("HOLD_MS", str(hold_ms))


def measure_presentation_bias() -> float:
    """How long this machine takes to present a frame the page has already drawn, in milliseconds.

    This is what `decktalk doctor --measure` reports, which an author reads a late section's
    offsets against. It is the middle of a run of frames rather than the worst or the mean, so one
    frame the operating system held up moves nothing.

    It measures the browser this machine launches by default, which is the browser `doctor` reports
    on, rather than one a project names: a bias belongs to the machine and not to a deck.
    """
    # The page is DeckTalk's own and loads nothing, so it is trusted whatever the projects are.
    with chromium(policy=TRUSTED, spend=False) as opened:
        page = open_document(opened, "<!doctype html><title>bias</title>")
        answer = evaluate(page, bias_script(MEASURED_FRAMES, MEASURED_FRAME_MS))
    rows = answer if isinstance(answer, list) else []
    samples = [float(row) for row in rows if isinstance(row, (int, float)) and not isinstance(row, bool)]
    if not samples:
        raise ToolError(
            "this machine's browser reports no presentation times, so the bias cannot be measured.",
            hint="Read a late section's offsets as they are, since this machine cannot say how late it presents.",
        )
    return round(statistics.median(samples), 1)
