"""Beds and one-shots from ElevenLabs: ambience, sfx, and music, from decktalk.toml [soundscape].

Ambience and sfx use sound generation, billed per second when a duration is set, and
music is requested in chunks of at most max_music_chunk_seconds and joined with a
crossfade. Every output has a cache file with the request hash, so unchanged requests
are skipped. dry_run reports the requests without calling the API.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..jsonio import read_json, relative, write_json
from ..media import audio, ffmpeg
from ..model import MusicSpec, Project, SoundSpec
from ..pipeline import SoundscapeStatus
from ..settings import ElevenLabsConfig
from ..speech.elevenlabs import ElevenLabs, check_api_base
from ..verdicts import Findings

log = logging.getLogger(__name__)


@dataclass
class SoundscapeItem:
    """One generated sound: where it went, what was asked for, and whether it was already there."""

    name: str
    out: Path
    endpoint: str
    requests: list[dict[str, Any]]
    status: SoundscapeStatus
    duration_seconds: float | None = None

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {
            "name": self.name,
            "out": relative(self.out, root),
            "endpoint": self.endpoint,
            "status": self.status.value,
            "duration_seconds": self.duration_seconds,
            "requests": list(self.requests),
        }


@dataclass
class SoundscapeResult:
    """Everything one soundscape run planned or generated."""

    items: list[SoundscapeItem] = field(default_factory=list)

    @property
    def findings(self) -> Findings:
        """None. A request that fails raises, so a run that returns has nothing to judge."""
        return Findings()

    def to_dict(self, root: Path) -> dict[str, Any]:
        return {"items": [item.to_dict(root) for item in self.items]}


def request_hash(endpoint: str, body: Any) -> str:
    return hashlib.sha256(f"{endpoint}\n{json.dumps(body, sort_keys=True)}".encode()).hexdigest()[:16]


def _load(path: Path) -> dict[str, Any]:
    """The ledger of what this project already bought, or {} when there is none to read."""
    if path.exists():
        try:
            return read_json(path)
        except json.JSONDecodeError:
            pass
    return {}


def _save(path: Path, data: dict[str, Any]) -> None:
    """The ledger, written atomically, because a half-written one buys the same audio twice."""
    write_json(path, data)


def sound_body(spec: SoundSpec, cfg: ElevenLabsConfig, *, loop: bool) -> dict[str, Any]:
    body: dict[str, Any] = {
        "text": spec.text,
        "duration_seconds": spec.duration_seconds
        if spec.duration_seconds is not None
        else (cfg.ambience_seconds if loop else cfg.sfx_seconds),
        "prompt_influence": spec.prompt_influence
        if spec.prompt_influence is not None
        else (cfg.ambience_prompt_influence if loop else cfg.sfx_prompt_influence),
        "model_id": spec.model_id or cfg.sound_model,
    }
    if loop:
        body["loop"] = True
    return body


def music_chunks(spec: MusicSpec, cfg: ElevenLabsConfig) -> list[dict[str, Any]]:
    n = max(1, math.ceil(spec.seconds / cfg.max_music_chunk_seconds))
    per = spec.seconds / n
    bodies = []
    for i in range(n):
        body: dict[str, Any] = {
            "prompt": spec.prompt,
            "force_instrumental": spec.force_instrumental,
            "model_id": spec.model_id or cfg.music_model,
            "music_length_ms": int(per * 1000),
        }
        if n > 1:
            body["prompt"] += f" (part {i + 1} of {n}, same key and tempo, seamless mood)"
        bodies.append(body)
    return bodies


def _sound(
    name: str,
    spec: SoundSpec,
    out: Path,
    client: ElevenLabs | None,
    cfg: ElevenLabsConfig,
    fmt: str,
    *,
    base: str,
    loop: bool,
    force: bool,
) -> SoundscapeItem:
    body = sound_body(spec, cfg, loop=loop)
    endpoint = f"{base}/sound-generation"
    item = SoundscapeItem(name=name, out=out, endpoint=endpoint, requests=[body], status=SoundscapeStatus.PLANNED)
    if client is None:
        return item
    cache_path = out.with_suffix(".cache.json")
    digest = request_hash(endpoint, body)
    cache = _load(cache_path)
    if not force and out.exists() and cache.get("hash") == digest:
        item.status, item.duration_seconds = SoundscapeStatus.UNCHANGED, cache.get("duration_seconds")
        return item
    out.parent.mkdir(parents=True, exist_ok=True)
    log.info("[gen ] %s -> %s", name, out)
    out.write_bytes(client.sound_effect(body, output_format=fmt))
    item.duration_seconds = ffmpeg.probe_duration(out)
    _save(cache_path, {"hash": digest, "file": out.name, "duration_seconds": item.duration_seconds, "request": body})
    item.status = SoundscapeStatus.GENERATED
    return item


def _music(
    spec: MusicSpec,
    out: Path,
    client: ElevenLabs | None,
    cfg: ElevenLabsConfig,
    fmt: str,
    *,
    base: str,
    force: bool,
) -> SoundscapeItem:
    chunks = music_chunks(spec, cfg)
    endpoint = f"{base}/music"
    item = SoundscapeItem(name="music", out=out, endpoint=endpoint, requests=chunks, status=SoundscapeStatus.PLANNED)
    if client is None:
        return item
    cache_path = out.with_suffix(".cache.json")
    digest = request_hash(endpoint, {"chunks": chunks, "xfade": cfg.music_crossfade_seconds})
    cache = _load(cache_path)
    if not force and out.exists() and cache.get("hash") == digest:
        item.status, item.duration_seconds = SoundscapeStatus.UNCHANGED, cache.get("duration_seconds")
        return item
    out.parent.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    for i, body in enumerate(chunks):
        part = out.with_name(f"{out.stem}-part{i + 1}.mp3")
        part_hash = request_hash(endpoint, body)
        if not force and part.exists() and cache.get("parts", {}).get(part.name) == part_hash:
            log.info("[skip] %s unchanged", part.name)
        else:
            log.info("[gen ] %s ...", part.name)
            part.write_bytes(client.music(body, output_format=fmt))
            cache.setdefault("parts", {})[part.name] = part_hash
            _save(cache_path, cache)
        parts.append(part)
    audio.crossfade_join(parts, out, crossfade_seconds=cfg.music_crossfade_seconds, bitrate=cfg.music_bitrate)
    item.duration_seconds = ffmpeg.probe_duration(out)
    cache.update({"hash": digest, "file": out.name, "duration_seconds": item.duration_seconds, "request": chunks})
    _save(cache_path, cache)
    item.status = SoundscapeStatus.GENERATED
    return item


def soundscape(
    project: Project, *, only: list[str] | None = None, force: bool = False, dry_run: bool = False
) -> SoundscapeResult:
    spec = project.soundscape
    if spec.empty:
        log.info("no [soundscape] in decktalk.toml, so there is nothing to generate")
        return SoundscapeResult()
    cfg = project.settings.elevenlabs
    fmt = project.settings.narration.output_format
    # The base is checked whatever the run does, and the plan names the URL a real run would call,
    # because the ledger and the request hash are keyed by it.
    base = check_api_base(cfg.api_base).rstrip("/")
    client = None if dry_run else ElevenLabs(project.require_env("ELEVENLABS_API_KEY")[0], cfg)
    wanted = set(only or [])

    def want(name: str) -> bool:
        return not wanted or name in wanted

    items: list[SoundscapeItem] = []
    if spec.ambience and want("ambience"):
        out = project.path(spec.ambience.out or project.mix.ambience or "build/sfx/ambience.mp3")
        items.append(_sound("ambience", spec.ambience, out, client, cfg, fmt, base=base, loop=True, force=force))
    for name, s in spec.sfx.items():
        if want(name):
            items.append(
                _sound(
                    name,
                    s,
                    project.path(s.out or f"build/sfx/{name}.mp3"),
                    client,
                    cfg,
                    fmt,
                    base=base,
                    loop=False,
                    force=force,
                )
            )
    if spec.music and want("music"):
        out = project.path(spec.music.out or project.mix.music or "build/music/music.mp3")
        items.append(_music(spec.music, out, client, cfg, fmt, base=base, force=force))
    return SoundscapeResult(items=items)
