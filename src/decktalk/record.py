"""Record HTML scenes to 1920x1080 webm with headless Chromium (Playwright).

One recording per page section in scenes.json, each lasting the section's span in
build/audio/timeline.json plus the section's extra_seconds. The page is opened as

    file:///<project>/<file>?scene=<scene>&<params>&t0=<settle>&beats=<id@t,...>

t0 is the number of seconds after load at which narration t=0 falls (the recorder
waits `settle` seconds after load, then starts the clock). Exactly then it flashes
the whole frame magenta for ~120 ms; `decktalk measure` finds the last magenta frame
so the assembler can trim the recording head to narration t=0 regardless of
Chromium's start-up latency. A page may set window.__sceneReady (a Promise) to
delay the clock until it has loaded fonts or data.

Chromium's recorder captures at ~25 fps with a variable frame clock, so the webm is
approximately the requested length; the assembler holds or trims each scene to its
exact span.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .project import Project, Section

WIDTH, HEIGHT = 1920, 1080
DEFAULT_SETTLE = 0.5
MARKER_MS = 120

MARKER_JS = """(ms) => {
  const d = document.createElement("div");
  d.id = "__t0marker";
  d.style.cssText = "position:fixed;inset:0;background:#ff00ff;z-index:2147483647;pointer-events:none";
  document.documentElement.appendChild(d);
  setTimeout(() => d.remove(), ms);
}"""

READY_JS = "() => (window.__sceneReady instanceof Promise ? window.__sceneReady : null)"


def scene_url(project: Project, file: str, scene: Any, params: dict[str, Any], settle: float) -> str:
    page = project.path(file)
    if not page.exists():
        raise SystemExit(f"error: scene file not found: {page}")
    query = {}
    if scene is not None:
        query["scene"] = str(scene)
    query.update({k: str(v) for k, v in params.items()})
    query["t0"] = str(settle)
    return page.resolve().as_uri() + "?" + urlencode(query)


def record_one(browser: Any, url: str, seconds: float, out: Path, settle: float) -> dict[str, Any]:
    tmp_dir = Path(tempfile.mkdtemp(prefix="decktalk-rec-"))
    context = browser.new_context(
        viewport={"width": WIDTH, "height": HEIGHT},
        device_scale_factor=1,
        color_scheme="light",
        reduced_motion="no-preference",
        record_video_dir=str(tmp_dir),
        record_video_size={"width": WIDTH, "height": HEIGHT},
    )
    created = time.monotonic()
    page = context.new_page()
    page.on("pageerror", lambda e: print(f"  [page error] {e}"))
    page.goto(url, wait_until="load")
    loaded = time.monotonic()
    try:
        page.evaluate(READY_JS)
    except Exception:
        pass
    page.wait_for_timeout(settle * 1000)
    started = time.monotonic()
    try:
        page.evaluate(MARKER_JS, MARKER_MS)
    except Exception:
        pass
    page.wait_for_timeout(seconds * 1000)
    video = page.video
    context.close()
    src = Path(video.path()) if video else None
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    if src is None or not src.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise SystemExit(f"error: no video produced for {out.name}")
    shutil.move(str(src), str(out))
    shutil.rmtree(tmp_dir, ignore_errors=True)
    sidecar = {
        "url": url,
        "requested_seconds": seconds,
        "settle_seconds": settle,
        "t0_seconds": settle,
        "load_seconds": round(loaded - created, 3),
        "lead_seconds": round(started - created, 3),
        "marker": f"magenta flash {MARKER_MS}ms at narration t=0; `decktalk measure` writes lead_in_seconds",
    }
    out.with_suffix(".json").write_text(json.dumps(sidecar, indent=2) + "\n")
    return {"out": out, "wall": round(time.monotonic() - started, 1), "lead": sidecar["lead_seconds"]}


def section_seconds(project: Project, section: Section, timeline: dict | None, manifest: dict) -> float | None:
    tl = (timeline or {}).get("sections", {}).get(section.key)
    if tl:
        return float(tl["end"]) - float(tl["start"])
    seg = manifest.get("segments", {}).get(section.key)
    return float(seg["duration_seconds"]) if seg else None


def record(
    project: Project,
    *,
    only: list[int] | None = None,
    seconds: float | None = None,
    settle: float = DEFAULT_SETTLE,
    no_beats: bool = False,
) -> int:
    from playwright.sync_api import sync_playwright

    timeline = project.timeline_data()
    manifest = project.manifest_data()
    beats = {} if no_beats else project.beats_data()
    if not manifest and seconds is None:
        raise SystemExit(f"error: {project.manifest} not found; run `decktalk narrate` first or pass --seconds N")

    jobs: list[dict[str, Any]] = []
    for section in project.sections:
        if only and section.index not in set(only):
            continue
        if section.is_video:
            print(f"[skip] section {section.key}: clip {section.data['video']} (assembled directly)")
            continue
        if not section.file:
            print(f"[skip] section {section.key}: no file")
            continue
        audio = section_seconds(project, section, timeline, manifest)
        length = seconds if seconds is not None else (audio + section.extra_seconds if audio else None)
        if not length:
            print(f"[skip] section {section.key}: no narration length yet and no --seconds")
            continue
        params = section.params
        if beats.get(section.key) and "beats" not in params:
            params["beats"] = beats[section.key]
        jobs.append(
            {
                "label": f"section {section.key} ({section.file}?scene={section.scene})",
                "url": scene_url(project, section.file, section.scene, params, settle),
                "seconds": length,
                "out": project.rec_dir / f"{section.key}-scene.webm",
            }
        )
    if not jobs:
        raise SystemExit("error: nothing to record")

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch()
        except Exception as exc:
            raise SystemExit(f"error: could not launch Chromium ({exc}). Run `decktalk setup`.") from exc
        try:
            for job in jobs:
                print(f"[rec ] {job['label']}  {job['seconds']:.1f}s ...", end="", flush=True)
                res = record_one(browser, job["url"], job["seconds"], job["out"], settle)
                rel = res["out"].relative_to(project.root)
                print(f" {rel}  ({res['wall']}s wall, lead {res['lead']:.2f}s)")
        finally:
            browser.close()
    return 0
