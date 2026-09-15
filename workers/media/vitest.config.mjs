// The media Worker's tests run inside the Workers runtime with a local R2 bucket, so they need no
// Cloudflare account and no network. Run them with `npm test` in this directory.
import { cloudflareTest } from "@cloudflare/vitest-pool-workers";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: { configPath: "./wrangler.jsonc" },
      remoteBindings: false,
      miniflare: { bindings: { SIGNING_KEY: "a signing key for these tests only, never for production" } },
    }),
  ],
});
