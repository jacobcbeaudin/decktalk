"""The clips a project makes: a `[[clip]]` names a project file and the program that writes it.

`record` runs the program when the file is not there, before it records anything, so a pane or a clip
section can play footage a package cannot ship or the film draws from its own sources. Its words are
`{out}`, `{landings}`, `{python}` and `{ffmpeg}` as a render section's are, `{words}`, and `{fps}`,
`{width}` and `{height}`, the film's rate and frame, so a clip can be made at the rate its pane steps
at. Its landings and its words are written beside the clip, where verify reads a clip's own landings
from and `assemble` the speech a pane clip's captions carry. Its video is written beside the file
under another name and moved onto it once the program has finished, so a program that stopped half
way leaves no file that would be taken for a clip. The program runs as a render section's does, by
`decktalk.pictures.command.run_program`.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from decktalk.artifacts import words_beside
from decktalk.files import drafted
from decktalk.inputs.document import MadeClip
from decktalk.media import ffmpeg
from decktalk.media.clipframes import landings_of
from decktalk.pictures.command import run_program


def make_clip(clip: MadeClip, *, root: Path, fps: int, width: int, height: int, timeout: float) -> Path:
    """Run the command of one clip the project makes, and move what it wrote onto the clip's own file.

    The video is drafted beside the clip, as `files.drafted` names it, and moved onto the clip only
    once the command has finished, so the clip is whole or absent.
    """
    final = root / clip.file
    landings, said = landings_of(final), words_beside(final)
    landings.unlink(missing_ok=True)
    said.unlink(missing_ok=True)
    with drafted(final) as draft:
        words: dict[str, Callable[[], str]] = {
            "out": lambda: str(draft),
            "landings": lambda: str(landings),
            "words": lambda: str(said),
            "python": lambda: sys.executable,
            "ffmpeg": ffmpeg.ffmpeg,
            "fps": lambda: str(fps),
            "width": lambda: str(width),
            "height": lambda: str(height),
        }
        run_program(
            clip.command, root=root, out=draft, words=words, timeout=timeout, what=f"the command of {clip.file}"
        )
    return final


__all__ = ["make_clip"]
