"""Generate a b-roll clip with a text-to-video model (Google Veo via Gemini, or Kling via fal.ai).

    decktalk broll --prompt "..." --name cold-open         # -> media/broll/cold-open.mp4
    decktalk broll --prompt "..." --name intro --takes 3   # intro-1..3.mp4, first copied to intro.mp4
    decktalk broll --prompt "..." --dry-run                # print provider, endpoint and request

Provider (auto): GEMINI_API_KEY in .env -> Gemini API, Veo 3.1 (predictLongRunning, polled);
else FAL_KEY -> fal.ai queue, Kling 2.5 Turbo Pro. Keys are never printed. Reference the
clip from your page (a <video> element the runtime can switch on a cue).
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .project import Project

DEFAULT_NEGATIVE = "text, captions, logos, watermark, people in the foreground, blur, distortion"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODEL = "veo-3.1-generate-preview"
FAL_BASE = "https://queue.fal.run"
FAL_MODEL = "fal-ai/kling-video/v2.5-turbo/pro/text-to-video"


@dataclass
class BrollRequest:
    prompt: str
    negative: str = ""
    duration: int = 8
    resolution: str = "1080p"
    aspect: str = "16:9"
    model: str = GEMINI_MODEL
    fal_model: str = FAL_MODEL
    poll: int = 10
    timeout: int = 900


def http_json(url: str, headers: dict[str, str], body: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:600]
        sys.exit(f"error: HTTP {exc.code} from {url.split('?')[0]}: {detail}")


def download(url: str, headers: dict[str, str], out: Path) -> None:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=600) as resp, out.open("wb") as fh:
        shutil.copyfileobj(resp, fh)


def gemini_request(r: BrollRequest) -> tuple[str, dict[str, Any]]:
    params: dict[str, Any] = {"aspectRatio": r.aspect, "durationSeconds": r.duration, "resolution": r.resolution}
    if r.negative:
        params["negativePrompt"] = r.negative
    return f"{GEMINI_BASE}/models/{r.model}:predictLongRunning", {
        "instances": [{"prompt": r.prompt}],
        "parameters": params,
    }


def gemini_generate(r: BrollRequest, key: str, out: Path) -> None:
    url, body = gemini_request(r)
    headers = {"x-goog-api-key": key, "Content-Type": "application/json"}
    op = http_json(url, headers, body)
    name = op.get("name")
    if not name:
        sys.exit(f"error: no operation name in response: {json.dumps(op)[:300]}")
    print(f"   operation {name}; polling every {r.poll}s ...", flush=True)
    deadline = time.time() + r.timeout
    while True:
        op = http_json(f"{GEMINI_BASE}/{name}", {"x-goog-api-key": key})
        if op.get("done"):
            break
        if time.time() > deadline:
            sys.exit(f"error: timed out after {r.timeout}s waiting for {name}")
        time.sleep(r.poll)
    if "error" in op:
        sys.exit(f"error: generation failed: {json.dumps(op['error'])[:600]}")
    samples = op.get("response", {}).get("generateVideoResponse", {}).get("generatedSamples", [])
    if not samples:
        sys.exit(f"error: no generatedSamples in response: {json.dumps(op)[:600]}")
    print(f"   downloading -> {out}", flush=True)
    download(samples[0]["video"]["uri"], {"x-goog-api-key": key}, out)


def fal_request(r: BrollRequest) -> tuple[str, dict[str, Any]]:
    body = {
        "prompt": r.prompt,
        "duration": "10" if r.duration > 5 else "5",
        "aspect_ratio": r.aspect,
        "negative_prompt": r.negative or DEFAULT_NEGATIVE,
    }
    return f"{FAL_BASE}/{r.fal_model}", body


def fal_generate(r: BrollRequest, key: str, out: Path) -> None:
    url, body = fal_request(r)
    headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
    sub = http_json(url, headers, body)
    status_url, response_url = sub.get("status_url"), sub.get("response_url")
    if not status_url or not response_url:
        sys.exit(f"error: unexpected submit response: {json.dumps(sub)[:300]}")
    print(f"   request {sub.get('request_id')}; polling every {r.poll}s ...", flush=True)
    deadline = time.time() + r.timeout
    while True:
        st = http_json(status_url, {"Authorization": f"Key {key}"})
        if st.get("status") == "COMPLETED":
            break
        if st.get("error"):
            sys.exit(f"error: fal request failed: {json.dumps(st)[:600]}")
        if time.time() > deadline:
            sys.exit(f"error: timed out after {r.timeout}s")
        time.sleep(r.poll)
    result = http_json(response_url, {"Authorization": f"Key {key}"})
    video_url = result.get("video", {}).get("url")
    if not video_url:
        sys.exit(f"error: no video.url in result: {json.dumps(result)[:600]}")
    print(f"   downloading -> {out}", flush=True)
    download(video_url, {}, out)


def broll(
    project: Project,
    r: BrollRequest,
    *,
    name: str = "broll",
    out_dir: Path | None = None,
    takes: int = 1,
    provider: str = "auto",
    dry_run: bool = False,
) -> int:
    gemini_key, fal_key = project.env("GEMINI_API_KEY"), project.env("FAL_KEY")
    if provider == "auto":
        provider = "gemini" if gemini_key else "fal" if fal_key else ""
    if provider == "fal":
        url, body = fal_request(r)
        have_key = bool(fal_key)
    else:
        url, body = gemini_request(r)
        have_key = bool(gemini_key)
    out_dir = out_dir or project.path("media/broll")
    print(f"provider: {provider or 'none (set GEMINI_API_KEY or FAL_KEY in .env)'}")
    print(f"POST {url}")
    print(json.dumps(body, indent=2))
    print(f"takes: {takes} -> {out_dir}/{name}-N.mp4, first copied to {name}.mp4")
    if dry_run:
        return 0
    if not provider or not have_key:
        sys.exit("error: no usable API key for the selected provider (keys are read from .env, never printed)")
    out_dir.mkdir(parents=True, exist_ok=True)
    made: list[Path] = []
    for i in range(1, takes + 1):
        out = out_dir / f"{name}-{i}.mp4"
        print(f"== take {i}/{takes}")
        if provider == "gemini":
            gemini_generate(r, gemini_key, out)
        else:
            fal_generate(r, fal_key, out)
        made.append(out)
    shutil.copyfile(made[0], out_dir / f"{name}.mp4")
    (out_dir / f"{name}.manifest.json").write_text(
        json.dumps({"provider": provider, "endpoint": url, "request": body, "takes": [p.name for p in made]}, indent=2)
        + "\n"
    )
    print(f"done: {out_dir / f'{name}.mp4'} (from {made[0].name})")
    return 0
