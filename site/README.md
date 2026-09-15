# decktalk.app

The landing page. A single static file with nothing to build. It loads Inter Tight and
JetBrains Mono from Google Fonts.

The demo film is not in git. It lives in the private `decktalk-media` R2 bucket, and the media
Worker in `workers/media` serves it at media.decktalk.app only through signed links that expire.
The page asks the Worker for a link when it loads, so the film plays only from decktalk.app.

Each cut's name holds its upload time and the first 12 hex digits of its SHA-256, so a name is never
reused, and `bash scripts/publish_demo.sh --check KEY` confirms that the bytes behind a name are the
ones it was made from. To ship a new cut, run
`bash scripts/publish_demo.sh FILE.mp4`. Then put the key it prints in the `data-media` attribute
in `index.html`, in the same commit as the cut's poster and captions in `media/`.

To see what the player does, add `?debug` to the page's address, and it logs each step to the
console. The media Worker logs every refused request and failure to Workers Logs, and
`npx wrangler tail decktalk-media` streams them. `npx wrangler deploy --var LOG_LEVEL:debug` in
`workers/media` logs every request until the next plain deploy. `bash scripts/publish_demo.sh
--verbose FILE.mp4` prints each command it runs.

The page loads the film only from https://decktalk.app. A local copy of the page shows the poster
but cannot play the film, because the Worker signs links only for that origin.
