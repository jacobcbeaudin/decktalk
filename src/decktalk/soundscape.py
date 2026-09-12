"""Generate beds and one-shots with ElevenLabs: ambience, sfx, and an underscore.

Configured in scenes.json under "soundscape"; every entry is optional:

    "soundscape": {
      "ambience": {"text": "quiet lecture hall room tone, ...", "duration_seconds": 25,
                   "out": "build/sfx/ambience.mp3"},
      "sfx": {"tick": {"text": "soft click", "duration_seconds": 0.5}},
      "music": {"prompt": "calm minimal piano underscore ...", "seconds": 360,
                "out": "build/music/underscore.mp3"}
    }

Ambience and sfx use POST /v1/sound-generation (40 credits per second when a
duration is set); music uses POST /v1/music, requested in chunks of at most five
minutes and joined with a 2 s crossfade. Every output has a manifest with the
request hash, so unchanged requests are skipped. `--dry-run` prints the requests.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .narrate import API_BASE, OUTPUT_FORMAT
from .project import Project
from .tools import ff, ffprobe_duration

SOUND_ENDPOINT = f"{API_BASE}/sound-generation?output_format={OUTPUT_FORMAT}"
MUSIC_ENDPOINT = f"{API_BASE}/music?output_format={OUTPUT_FORMAT}"
MAX_MUSIC_CHUNK = 300
MUSIC_CROSSFADE = 2


def request_hash(endpoint: str, body: Any) -> str:
    return hashlib.sha256(f"{endpoint}\n{json.dumps(body, sort_keys=True)}".encode()).hexdigest()[:16]


def post_audio(endpoint: str, body: dict[str, Any], api_key: str, out: Path) -> None:
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"xi-api-key": api_key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            out.write_bytes(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        sys.exit(f"error: ElevenLabs HTTP {exc.code} for {out.name}: {detail}")


def load_manifest(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def save_manifest(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def sound_body(spec: dict[str, Any], *, loop: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "text": spec["text"],
        "duration_seconds": float(spec.get("duration_seconds", 25 if loop else 0.5)),
        "prompt_influence": float(spec.get("prompt_influence", 0.3 if loop else 0.5)),
        "model_id": spec.get("model_id", "eleven_text_to_sound_v2"),
    }
    if loop:
        body["loop"] = True
    return body


def do_sound(name: str, spec: dict[str, Any], out: Path, api_key: str | None, *, loop: bool, force: bool) -> None:
    body = sound_body(spec, loop=loop)
    digest = request_hash(SOUND_ENDPOINT, body)
    manifest_path = out.with_suffix(".manifest.json")
    print(f"== {name} -> {out}")
    print(f"   POST {SOUND_ENDPOINT}")
    print(f"   {json.dumps(body)}")
    if api_key is None:
        return
    manifest = load_manifest(manifest_path)
    if not force and out.exists() and manifest.get("hash") == digest:
        print(f"   [skip] unchanged ({manifest.get('duration_seconds')}s)")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    post_audio(SOUND_ENDPOINT, body, api_key, out)
    dur = ffprobe_duration(out)
    save_manifest(manifest_path, {"hash": digest, "file": out.name, "duration_seconds": dur, "request": body})
    print(f"   [ok] {dur}s")


def music_chunks(spec: dict[str, Any]) -> list[dict[str, Any]]:
    target = int(spec.get("seconds", 360))
    n = max(1, math.ceil(target / MAX_MUSIC_CHUNK))
    per = target / n
    bodies = []
    for i in range(n):
        body: dict[str, Any] = {
            "prompt": spec["prompt"],
            "force_instrumental": bool(spec.get("force_instrumental", True)),
            "model_id": spec.get("model_id", "music_v2"),
            "music_length_ms": int(per * 1000),
        }
        if n > 1:
            body["prompt"] += f" (part {i + 1} of {n}, same key and tempo, seamless mood)"
        bodies.append(body)
    return bodies


def do_music(spec: dict[str, Any], out: Path, api_key: str | None, *, force: bool) -> None:
    chunks = music_chunks(spec)
    digest = request_hash(MUSIC_ENDPOINT, {"chunks": chunks, "xfade": MUSIC_CROSSFADE})
    manifest_path = out.with_suffix(".manifest.json")
    print(f"== music -> {out}  ({len(chunks)} x {chunks[0]['music_length_ms'] / 1000:.0f}s)")
    print(f"   POST {MUSIC_ENDPOINT}")
    for i, body in enumerate(chunks):
        print(f"   [{i + 1}] {json.dumps(body)}")
    if api_key is None:
        return
    manifest = load_manifest(manifest_path)
    if not force and out.exists() and manifest.get("hash") == digest:
        print(f"   [skip] unchanged ({manifest.get('duration_seconds')}s)")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for i, body in enumerate(chunks):
        part = out.with_name(f"{out.stem}-part{i + 1}.mp3")
        part_hash = request_hash(MUSIC_ENDPOINT, body)
        if not force and part.exists() and manifest.get("parts", {}).get(part.name) == part_hash:
            print(f"   [skip] {part.name} unchanged")
        else:
            print(f"   [gen ] {part.name} ...", end="", flush=True)
            post_audio(MUSIC_ENDPOINT, body, api_key, part)
            print(f" {ffprobe_duration(part)}s")
            manifest.setdefault("parts", {})[part.name] = part_hash
            save_manifest(manifest_path, manifest)
        parts.append(part)
    if len(parts) == 1:
        parts[0].replace(out)
    else:
        inputs: list[str] = []
        for p in parts:
            inputs += ["-i", str(p)]
        chain, prev = "", "[0:a]"
        for i in range(1, len(parts)):
            label = "[a]" if i == len(parts) - 1 else f"[m{i}]"
            chain += f"{prev}[{i}:a]acrossfade=d={MUSIC_CROSSFADE}:c1=tri:c2=tri{label};"
            prev = label
        ff(*inputs, "-filter_complex", chain.rstrip(";"), "-map", "[a]", "-c:a", "libmp3lame", "-b:a", "192k", str(out))
    dur = ffprobe_duration(out)
    manifest.update({"hash": digest, "file": out.name, "duration_seconds": dur, "request": chunks})
    save_manifest(manifest_path, manifest)
    print(f"   [ok] {dur}s")


def soundscape(project: Project, *, only: list[str] | None = None, force: bool = False, dry_run: bool = False) -> int:
    cfg = project.soundscape
    if not cfg:
        print('no "soundscape" block in scenes.json; nothing to generate')
        return 0
    api_key = None if dry_run else project.require_env("ELEVENLABS_API_KEY")[0]
    wanted = set(only or [])

    def want(name: str) -> bool:
        return not wanted or name in wanted

    amb = cfg.get("ambience")
    if amb and want("ambience"):
        out = project.path(amb.get("out") or project.mix.get("ambience") or "build/sfx/ambience.mp3")
        do_sound("ambience", amb, out, api_key, loop=True, force=force)
    for name, spec in (cfg.get("sfx") or {}).items():
        if want(name):
            out = project.path(spec.get("out") or f"build/sfx/{name}.mp3")
            do_sound(name, spec, out, api_key, loop=False, force=force)
    music = cfg.get("music")
    if music and want("music"):
        out = project.path(music.get("out") or project.mix.get("underscore") or "build/music/underscore.mp3")
        do_music(music, out, api_key, force=force)
    return 0
