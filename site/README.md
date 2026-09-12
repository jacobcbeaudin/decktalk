# decktalk.app

A single static page. Nothing to build.

Deploy with Cloudflare Pages: create a project from this repository, leave the build
command empty, and set the build output directory to `site`. Add `decktalk.app` as the
custom domain; Cloudflare creates the DNS record itself because the zone is already
there. Every push to `main` redeploys.

The page loads Inter Tight and JetBrains Mono from Google Fonts and nothing else.
