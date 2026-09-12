# decktalk.app

A single static page. Nothing to build.

Deploy it from the Cloudflare dashboard in either of two ways. Both redeploy on every push to `main`.

- **Workers** (the default flow): create an application from this repository, leave the build
  command empty, and set the deploy command to `npx wrangler deploy`. The `wrangler.jsonc` at the
  repository root tells wrangler to serve `site/` as static assets. Add `decktalk.app` under the
  Worker's Settings, Domains & Routes.
- **Pages** (the "legacy" link): connect the repository, leave the build command empty, set the
  build output directory to `site`, and add `decktalk.app` under Custom domains.

Cloudflare creates the DNS record itself because the zone is already in the account. Leave the
`docs` record alone; it points at Mintlify and must stay DNS-only.

The page loads Inter Tight and JetBrains Mono from Google Fonts and nothing else.
