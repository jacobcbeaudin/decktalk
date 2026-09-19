"""The take plan: what a run would voice, what it already has, and what it would cost.

A take is identified by its content hash and by nothing else, so two sections with the same words
share one take and renumbering or retitling a section moves no file and voices nothing. The audio is
named by the hash, the section number is display only, and the cache is the take directory itself:
a digest whose mp3 and words file are both on disk is a hit.

The plan is also the approval stop. `narrate --dry-run` sends nothing and reports what each section
would send, what the stitching context adds, and the dollars at `[voice] price_per_1000_characters`.
A section whose cache could not be checked is priced apart, so a project that has paid before sees
what its run can cost as well as what it certainly costs. `script_rules.py` says whether a script
may be planned at all.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...artifacts import Takes
from ...errors import ConfigError
from ...model import Project
from ...model.script import Segment
from ...pipeline import TakeStatus
from ...settings import NarrationConfig
from ...speech import SpeechProvider, SpeechRequest, VoiceContext, get_provider

WITHOUT_A_VOICE = "the voice is not set up, so the cache cannot be checked"

# ---- the content hash ------------------------------------------------------------------


def text_hash(segment: Segment, cfg: NarrationConfig, provider_key: str, settings: dict[str, Any]) -> str:
    """The digest of one voiced take: provider identity, voice settings and the exact text sent.

    Every byte is paid for once, so a change here re-voices every project there is, which is why
    `tests/test_take_hash.py` holds the digests of the founder's film against it.
    """
    payload = f"{provider_key}\n{json.dumps(settings, sort_keys=True)}\n{segment.tts_text}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def silent_hash(segment: Segment, cfg: NarrationConfig) -> str:
    """The digest of one placeholder take, which is its clicks and its length rather than a voice."""
    payload = f"silent\n{cfg.silent_words_per_minute}\n{cfg.silent_beat_seconds}\n{segment.text}"
    return f"silent-{hashlib.sha256(payload.encode()).hexdigest()[:10]}"


def take_name(digest: str) -> str:
    return f"{digest}.mp3"


def words_name(digest: str) -> str:
    return f"{digest}.words.json"


def is_cached(digest: str, takes_dir: Path) -> bool:
    """True when the take of this digest and its words file are both on disk, which is the whole cache."""
    return (takes_dir / take_name(digest)).exists() and (takes_dir / words_name(digest)).exists()


def speech_provider(project: Project) -> SpeechProvider:
    """The provider `[voice] provider` names, built from this project's settings and its .env."""
    return get_provider(project.voice.provider, VoiceContext(settings=project.settings, secrets=project.env))


def requests_for(project: Project, targets: list[Segment], *, model: str) -> dict[str, SpeechRequest]:
    """One request per target section, each carrying the sections either side of it for prosody."""
    cfg, settings = project.settings.narration, project.voice.api_settings()
    return {
        seg.key: SpeechRequest(
            text=seg.tts_text,
            model=model,
            voice_settings=settings,
            output_format=cfg.output_format,
            previous_text=targets[i - 1].spoken if i else None,
            next_text=targets[i + 1].spoken if i + 1 < len(targets) else None,
        )
        for i, seg in enumerate(targets)
    }


# ---- the plan --------------------------------------------------------------------------


@dataclass(frozen=True)
class TakePlan:
    """What a run would do with one section, and why."""

    segment: Segment
    status: TakeStatus
    reason: str = ""
    chapter: str = ""
    digest: str | None = None
    request: SpeechRequest | None = None  # What a voiced run would send, before it is sent.

    @property
    def cached(self) -> bool:
        """The take this section plays is on disk already, so the run sends nothing for it."""
        return self.status is TakeStatus.CACHED

    @property
    def unchecked(self) -> bool:
        """The cache could not be checked at all, because there was no voice to ask."""
        return self.status is TakeStatus.UNKNOWN

    @property
    def characters_sent(self) -> int:
        return len(self.request.text) if self.request else 0

    @property
    def context_characters(self) -> int:
        """The neighbouring sections the request carries for prosody, which travel with the text."""
        if self.request is None:
            return 0
        return len(self.request.previous_text or "") + len(self.request.next_text or "")

    def to_dict(self, cfg: NarrationConfig) -> dict[str, Any]:
        seg = self.segment
        return {
            "key": seg.key, "chapter": self.chapter, "status": self.status.value, "reason": self.reason or None,
            "file": take_name(self.digest) if self.digest else None, "hash": self.digest,
            "characters_sent": self.characters_sent, "characters_spoken": len(seg.spoken),
            "characters_with_context": self.characters_sent + self.context_characters,
            "word_count": seg.word_count, "estimated_seconds": seg.estimated_seconds(cfg),
            "placeholders": seg.placeholders,
            "request": None if self.request is None else request_body(self.request),
        }  # fmt: skip


def request_body(request: SpeechRequest) -> dict[str, Any]:
    """The body a voiced run posts, which carries no credential.

    The settings come from `[voice]` in `decktalk.toml` and never from `.env`, and the key travels
    in a header this function never builds.
    """
    return {
        "text": request.text, "model_id": request.model, "voice_settings": request.voice_settings,
        "output_format": request.output_format, "previous_text": request.previous_text,
        "next_text": request.next_text,
    }  # fmt: skip


def plan_totals(plans: list[TakePlan], cfg: NarrationConfig, rate: float = 0.0) -> dict[str, Any]:
    """How many sections have each status, and what the ones that would be voiced send and cost at `rate`.

    A section whose cache could not be checked may turn out to need a take, so its characters are
    counted apart and priced apart. The approval stop then gives the figure the run certainly
    spends and the figure it can reach, and never a small number that hides a large one.
    """
    totals: dict[str, Any] = {s.value: sum(p.status is s for p in plans) for s in TakeStatus}
    voiced = [p for p in plans if p.status is TakeStatus.SYNTHESIZE]
    unchecked = [p for p in plans if p.unchecked]
    sent = sum(p.characters_sent for p in voiced)
    unknown_characters = sum(p.characters_sent for p in unchecked)
    totals["characters_sent"] = sent
    totals["characters_with_context"] = sent + sum(p.context_characters for p in voiced)
    totals["characters_spoken"] = sum(len(p.segment.spoken) for p in voiced)
    totals["characters_unchecked"] = unknown_characters
    totals["price_per_1000_characters"] = rate
    totals["estimated_cost"] = round(sent / 1000 * rate, 2) if rate else None
    totals["most_it_can_cost"] = round((sent + unknown_characters) / 1000 * rate, 2) if rate else None
    return totals


def miss_reason(previous: Takes | None, segment: Segment, digest: str, *, voiced: bool, elsewhere: set[str]) -> str:
    """Why this section's take is not on disk, in the words the plan table and the payload print.

    A take is found by its content, so the index is searched by content before it is searched by
    section number. `elsewhere` is the spoken text of every other section this run is planning, which
    is what tells an inserted section from an edited one: a row whose words now belong to another
    section says nothing about the section that took its number.
    """
    rows = previous.sections if previous is not None else {}
    if any(row.hash == digest for row in rows.values()):
        return "the mp3 or the words file is missing"
    changed = "the text, voice, model, or voice settings changed"
    same_words = [row for row in rows.values() if row.spoken == segment.spoken]
    if same_words:
        return "only a take without voice exists" if voiced and not any(r.voiced for r in same_words) else changed
    row = rows.get(segment.key)
    return changed if row is not None and row.spoken not in elsewhere else "no take yet"


def plan_takes(
    project: Project,
    targets: list[Segment],
    digests: dict[str, str] | None,
    *,
    requests: dict[str, SpeechRequest] | None = None,
    voiced: bool = True,
    force: bool = False,
) -> list[TakePlan]:
    """What a run would do with each target section, sending nothing and writing nothing.

    `digests` is None when the provider could not be set up, and a section already in the take
    index is then unknown while every other section still needs a take. The request is carried on
    every plan either way, so a run that cannot check the cache still prices what it would send.

    Two sections with the same words come to one digest, so the second of them is already covered
    by the first and is planned as cached rather than sent and paid for twice.
    """
    previous = project.takes()
    chapters = project.chapters()
    plans: list[TakePlan] = []
    planned: set[str] = set()
    for seg in targets:
        chapter = chapters.get(seg.index, seg.title)
        request = (requests or {}).get(seg.key)
        if digests is None:
            row = previous.sections.get(seg.key) if previous is not None else None
            if row is not None and row.voiced:
                plans.append(TakePlan(seg, TakeStatus.UNKNOWN, WITHOUT_A_VOICE, chapter=chapter, request=request))
            else:
                why = "only a take without voice exists" if row is not None else "no take yet"
                plans.append(TakePlan(seg, TakeStatus.SYNTHESIZE, why, chapter=chapter, request=request))
            continue
        digest = digests[seg.key]
        elsewhere = {other.spoken for other in targets if other.key != seg.key}
        if digest in planned:
            shared = "another section of this run voices these words"
            plans.append(TakePlan(seg, TakeStatus.CACHED, shared, chapter, digest, request))
        elif force:
            plans.append(TakePlan(seg, TakeStatus.SYNTHESIZE, "forced", chapter, digest, request))
            planned.add(digest)
        elif is_cached(digest, project.takes_dir):
            plans.append(TakePlan(seg, TakeStatus.CACHED, "", chapter, digest, request))
        else:
            reason = miss_reason(previous, seg, digest, voiced=voiced, elsewhere=elsewhere)
            plans.append(TakePlan(seg, TakeStatus.SYNTHESIZE, reason, chapter, digest, request))
            planned.add(digest)
    return plans


def voiced_plan(
    project: Project, targets: list[Segment], *, model: str, force: bool = False
) -> tuple[list[TakePlan], str | None]:
    """(the plan a voiced run would follow, why the provider could not be set up), spending nothing."""
    cfg, settings = project.settings.narration, project.voice.api_settings()
    requests = requests_for(project, targets, model=model)
    try:
        provider = speech_provider(project)
    except ConfigError as exc:
        return plan_takes(project, targets, None, requests=requests, force=force), str(exc)
    digests = {seg.key: text_hash(seg, cfg, provider.cache_key(requests[seg.key]), settings) for seg in targets}
    return plan_takes(project, targets, digests, requests=requests, force=force), None


def silent_plan(project: Project, targets: list[Segment], *, force: bool = False) -> list[TakePlan]:
    """The plan a run without voice follows, whose placeholder takes are cached by content too."""
    digests = {seg.key: silent_hash(seg, project.settings.narration) for seg in targets}
    return plan_takes(project, targets, digests, voiced=False, force=force)
