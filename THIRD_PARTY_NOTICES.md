# Third-party notices

DeckTalk is licensed under Apache-2.0 (see LICENSE). This file lists the third-party software that DeckTalk depends on, downloads, or embeds, and the license of each.

| Component | How DeckTalk uses it | License |
|---|---|---|
| [Playwright](https://playwright.dev/python/) | Playwright is a Python dependency. It drives headless Chromium to record pages and take screenshots. | Apache-2.0 |
| Chromium (headless shell) | `decktalk setup` downloads it through Playwright. | BSD-3-Clause and the licenses of its components |
| [static-ffmpeg](https://github.com/zackees/static_ffmpeg) | static-ffmpeg is a Python dependency. It downloads the ffmpeg and ffprobe binaries into the Python environment, and `decktalk setup` runs that download ahead of time. | BSD-3-Clause |
| [ffmpeg](https://ffmpeg.org/) source | DeckTalk runs ffmpeg as a separate process to cut, mix, and analyze audio and video. DeckTalk does not link against ffmpeg. | LGPL-2.1-or-later |
| ffmpeg builds for Linux, Windows, and Intel macOS | static-ffmpeg fetches these builds. They include libx264 and use `--enable-gpl --enable-version3 --enable-libx264`. | GPL-3.0-or-later |
| ffmpeg build for Apple silicon macOS | static-ffmpeg fetches this build. It includes libx264 and uses `--enable-gpl --enable-libx264`. | GPL-2.0-or-later |
| [Inter Tight](https://rsms.me/inter/) | The scaffold deck bundles the latin and greek woff2 subsets in `deck/fonts/`, with `InterTight-OFL.txt`. A subset is also embedded in the README graphics under `assets/`. | SIL Open Font License 1.1 |
| [Inter](https://rsms.me/inter/) | The scaffold deck bundles the latin and greek woff2 subsets in `deck/fonts/`, with `Inter-OFL.txt`. | SIL Open Font License 1.1 |
| [JetBrains Mono](https://www.jetbrains.com/lp/mono/) | The scaffold deck bundles the latin and greek woff2 subsets in `deck/fonts/`, with `JetBrainsMono-OFL.txt`. | SIL Open Font License 1.1 |
| [KaTeX](https://katex.org/) | KaTeX typesets equations in the scaffold deck. `decktalk setup` downloads a release into a per-user cache, and `decktalk init` copies it into `deck/katex/`. | MIT |
| [ElevenLabs](https://elevenlabs.io/) | ElevenLabs is a web API for speech, sound effects, and music. DeckTalk calls it with your own key, under your own account terms. | ElevenLabs terms of service |

Notes:

- DeckTalk uses an ffmpeg on your PATH only when static-ffmpeg cannot give one. To use a build of your own, set `DECKTALK_FFMPEG` and `DECKTALK_FFPROBE`.
- The wheel does not include KaTeX. If the KaTeX download fails, `decktalk setup` warns and continues. If KaTeX is not cached, `decktalk init` warns, and the pages load KaTeX from a CDN.
