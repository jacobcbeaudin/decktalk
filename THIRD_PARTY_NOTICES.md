# Third-party notices

DeckTalk is licensed under Apache-2.0 (see LICENSE). It depends on, downloads, or embeds
the following third-party software, each under its own license.

| Component | How DeckTalk uses it | License |
|---|---|---|
| [Playwright](https://playwright.dev/python/) | A Python dependency. It drives headless Chromium to record pages and take screenshots. | Apache-2.0 |
| Chromium (headless shell) | Downloaded by `decktalk setup` through Playwright. | BSD-3-Clause and the licenses of its components |
| [static-ffmpeg](https://github.com/zackees/static_ffmpeg) | A Python dependency. It downloads ffmpeg and ffprobe binaries into the Python environment, and `decktalk setup` runs that download ahead of time. DeckTalk uses an ffmpeg on PATH only when static-ffmpeg cannot provide one. | BSD-3-Clause |
| [ffmpeg](https://ffmpeg.org/) | Run as a separate process to cut, mix, and analyze audio and video. The builds that static-ffmpeg fetches include libx264 and are therefore GPL builds. The Linux, Windows, and Intel macOS builds are configured with `--enable-gpl --enable-version3 --enable-libx264`, and the Apple silicon macOS build with `--enable-gpl --enable-libx264`. DeckTalk does not link against them. | GPL-3.0-or-later for the Linux, Windows, and Intel macOS builds, GPL-2.0-or-later for the Apple silicon macOS build, and LGPL-2.1-or-later for ffmpeg itself |
| [Inter Tight](https://rsms.me/inter/) | A subset of the variable font is embedded in the README graphics under `assets/`, and the latin and greek subsets of the variable font are bundled as woff2 in the scaffold deck (`deck/fonts/`, with `InterTight-OFL.txt`). | SIL Open Font License 1.1 |
| [Inter](https://rsms.me/inter/) | The latin and greek subsets of the variable font are bundled as woff2 in the scaffold deck (`deck/fonts/`, with `Inter-OFL.txt`). | SIL Open Font License 1.1 |
| [JetBrains Mono](https://www.jetbrains.com/lp/mono/) | The latin and greek subsets of the variable font are bundled as woff2 in the scaffold deck (`deck/fonts/`, with `JetBrainsMono-OFL.txt`). | SIL Open Font License 1.1 |
| [KaTeX](https://katex.org/) | It typesets equations in the scaffold deck. `decktalk setup` downloads a KaTeX release into a per-user cache, and `decktalk init` copies it into the project's `deck/katex/`. The wheel does not include it. When the download has failed, `decktalk init` warns and points the deck at a CDN copy instead. | MIT |
| [ElevenLabs](https://elevenlabs.io/) | A web API for speech, sound effects, and music, called with your own key under your own account terms. | ElevenLabs terms of service |
