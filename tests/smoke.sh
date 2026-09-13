#!/usr/bin/env bash
# End-to-end smoke test with no API key: scaffold a project, add the optional clip section the
# way the comments in decktalk.toml describe, render placeholder narration, record the template
# deck, assemble with a synthetic clip and underscore, and verify every cue. A second clip of
# B-roll sits between page sections 2 and 3, so the narration pauses for it and resumes after it.
# The scaffold's own clip section, the B-roll slot after the lesson, has no file, so its slate plays.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
T=tests/out/smoke
rm -rf "$T"; mkdir -p tests/out
uv run decktalk init "$T" --name smoke
# Uncomment the clip section exactly as the template's comment block instructs.
uv run python - "$T" <<'EOF'
import sys
from pathlib import Path

root = Path(sys.argv[1])
toml = root / "decktalk.toml"
clip_lines = ("# [[section]]", "# number = 0", '# title = "On camera"', "# clip = ", "# slate_seconds = ")
lines = [line[2:] if line.startswith(clip_lines) else line for line in toml.read_text().splitlines(keepends=True)]
toml.write_text("".join(lines).replace("dips = []", "dips = [[0, 1]]", 1))
script = root / "script.md"
heading = "## 0. On camera\n\n[Your clip at media/open.mp4 plays here, with its own sound.]\n\n## 1. Open"
script.write_text(script.read_text().replace("## 1. Open", heading, 1))
EOF
# Renumber sections 3 and later, and put a B-roll clip in section 3 with a dip on both sides.
uv run python - "$T" <<'EOF'
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])


def bump(n: str) -> str:
    return str(int(n) + 1 if int(n) >= 3 else int(n))


toml = root / "decktalk.toml"
text = re.sub(r"^number = (\d+)$", lambda m: f"number = {bump(m[1])}", toml.read_text(), flags=re.M)
broll = '[[section]]\nnumber = 3\ntitle = "B-roll"\nclip = "media/broll.mp4"\n\n'
text = text.replace("[[section]]\nnumber = 4\n", broll + "[[section]]\nnumber = 4\n", 1)
toml.write_text(text.replace("dips = [[0, 1]]", "dips = [[0, 1], [2, 3], [3, 4]]", 1))
script = root / "script.md"
text = re.sub(r"^## (\d+)\.", lambda m: f"## {bump(m[1])}.", script.read_text(), flags=re.M)
heading = "## 3. B-roll\n\n[The clip at media/broll.mp4 plays here, with its own sound.]\n\n## 4. "
script.write_text(text.replace("## 4. ", heading, 1))
cues = root / "cues.json"
doc = json.loads(cues.read_text())
doc["sections"] = {bump(k): v for k, v in doc["sections"].items()}
cues.write_text(json.dumps(doc, indent=2) + "\n")
EOF
FF="$(uv run python -c 'from decktalk.media.ffmpeg import ffmpeg; print(ffmpeg().replace(chr(92), chr(47)))')"
# A synthetic "on camera" clip: 3 s, 1280x720 (exercises scale/pad), a 440 Hz tone as the voice.
"$FF" -hide_banner -loglevel error -y -f lavfi -i "testsrc2=s=1280x720:r=30" -f lavfi -i "sine=f=440:r=48000" -t 3 -c:v libx264 -pix_fmt yuv420p -c:a aac "$T/media/open.mp4"
# A synthetic B-roll clip for section 3: 2.5 s of testsrc with a 660 Hz tone.
"$FF" -hide_banner -loglevel error -y -f lavfi -i "testsrc=s=1920x1080:r=25" -f lavfi -i "sine=f=660:r=48000" -t 2.5 -c:v libx264 -pix_fmt yuv420p -c:a aac "$T/media/broll.mp4"
mkdir -p "$T/build/music"
"$FF" -hide_banner -loglevel error -y -f lavfi -i "sine=f=220:r=44100" -t 8 -af volume=0.5 -c:a libmp3lame "$T/build/music/underscore.mp3"
DECKTALK_VIDEO_PRESET=veryfast uv run decktalk -p "$T" build --silent
uv run decktalk -p "$T" shots
uv run decktalk -p "$T" shots --section 4 --at 8 --at 20 --at 38
uv run decktalk -p "$T" verify
uv run decktalk -p "$T" verify --json | uv run python -c 'import json,sys; d=json.load(sys.stdin); assert d["ok"], d["findings"]'
uv run decktalk -p "$T" status
uv run python -c "import decktalk; p = decktalk.Project.load('$T'); print('python api ok:', p.name, len(p.sections), 'sections')"
# post-production checks on the final file: picture and sound both start at 0, every section
# boundary is a real frame, chapters mark the section starts, and captions were written
uv run python - "$T" <<'EOF'
import json
import subprocess
import sys
from pathlib import Path

from decktalk.media.ffmpeg import ffprobe, probe_duration, rms_db

out = Path(sys.argv[1]) / "build" / "out"
final = out / "smoke.mp4"


def probe(*args: str) -> dict:
    cmd = [ffprobe(), "-v", "error", "-of", "json", *args, str(final)]
    return json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)


streams = probe("-show_entries", "stream=codec_type,start_time")["streams"]
starts = {s["codec_type"]: float(s["start_time"]) for s in streams if s["codec_type"] in ("video", "audio")}
assert starts == {"video": 0.0, "audio": 0.0}, f"streams do not start together: {starts}"
sections = sorted(out.glob("[0-9][0-9]-section.mp4"))
chapters = probe("-show_chapters")["chapters"]
assert len(chapters) == len(sections) >= 4, (len(chapters), len(sections))
frames = probe("-select_streams", "v", "-show_entries", "frame=pts_time")["frames"]
pts = {round(float(f["pts_time"]), 3) for f in frames}
t = 0.0
spans = {}
for chapter, section in zip(chapters, sections, strict=True):
    assert abs(float(chapter["start_time"]) - t) < 1e-6, f"chapter {chapter['id']} at {chapter['start_time']}, want {t}"
    assert round(t, 3) in pts, f"no frame starts at {t}"
    spans[section.name[:2]] = (t, t + probe_duration(section))
    t = spans[section.name[:2]][1]
assert abs(t - probe_duration(final)) < 0.05, (t, probe_duration(final))
for name in ("smoke.srt", "smoke.vtt", "smoke.chapters.txt"):
    assert (out / name).stat().st_size > 0, name
# The B-roll clip in section 3 carries its own chapter and its own sound, and no caption sits over it.
clip_at, clip_end = spans["03"]
assert chapters[3]["tags"]["title"] == "B-roll", chapters[3]
assert rms_db(final, clip_at + 0.5, clip_end - clip_at - 1.0) > -30, "the B-roll clip's sound is missing"


def seconds(stamp: str) -> float:
    hms, ms = stamp.split(",")
    h, m, s = hms.split(":")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


captions = [
    tuple(seconds(x) for x in line.split(" --> "))
    for line in (out / "smoke.srt").read_text().splitlines()
    if " --> " in line
]
over = [c for c in captions if c[0] < clip_end - 1e-3 and c[1] > clip_at + 1e-3]
assert not over, f"captions over the clip at {clip_at:.2f}-{clip_end:.2f}: {over}"
assert any(clip_end <= c[0] < spans["04"][1] for c in captions), "section 4 has no captions after the clip"
print(
    f"post-production ok: video/audio start at 0, {len(chapters)} chapters on frame-exact section starts, "
    f"captions written, B-roll audible at {clip_at:.2f}-{clip_end:.2f} with no caption over it"
)
EOF
echo "smoke test OK: $T/build/out/smoke.mp4"
