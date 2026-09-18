# decktalk.app

The landing page. A single static file with nothing to build. It loads Inter Tight and
JetBrains Mono from Google Fonts.

The demo film is not in git. media.decktalk.ai serves it from a public bucket, and the player's
`src` is that address. The poster and the captions live in `media/`, next to the page, so the text
track is same-origin. To ship a new cut, upload it to the bucket, then change the film's address in
`index.html`, the poster, and the captions in the same commit.
