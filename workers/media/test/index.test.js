import { env, SELF } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import worker from "../src/index.js";

const BASE = "https://media.decktalk.app";
const SITE = "https://decktalk.app";
const KEY = "decktalk-demo-20260914T151110Z-0810246f57b4.mp4";
const MISSING = "decktalk-demo-20990101T000000Z-000000000000.mp4";
const FILM = Uint8Array.from({ length: 1000 }, (_, i) => i % 251);

beforeEach(async () => {
  await env.MEDIA.put(KEY, FILM);
});

afterEach(() => {
  vi.restoreAllMocks();
});

function sign(key = KEY, init = { headers: { Origin: SITE } }) {
  return SELF.fetch(`${BASE}/sign/${key}`, init);
}

async function link(key = KEY) {
  const response = await sign(key);
  return (await response.json()).url;
}

// The Worker called directly, with some of its bindings replaced, and its console captured.
async function call(url, init = {}, bindings = {}) {
  const logs = [];
  for (const level of ["log", "warn", "error"]) {
    vi.spyOn(console, level).mockImplementation((entry) => logs.push(entry));
  }
  const response = await worker.fetch(new Request(url, init), { ...env, ...bindings });
  return { response, logs };
}

// A link signed the way the Worker signs, for expiries and keys the Worker would never hand out.
async function forge(key, expires) {
  const secret = new TextEncoder().encode(env.SIGNING_KEY);
  const hmac = await crypto.subtle.importKey("raw", secret, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", hmac, new TextEncoder().encode(`${key}\n${expires}`));
  const sig = btoa(String.fromCharCode(...new Uint8Array(mac)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/, "");
  return `${BASE}/v/${key}?exp=${expires}&sig=${sig}`;
}

const now = () => Math.floor(Date.now() / 1000);

const BASE64URL = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

// The link with the first character of its signature changed, which changes the signature's bytes.
function tamper(url) {
  const at = url.indexOf("sig=") + 4;
  const swapped = url[at] === "A" ? "B" : "A";
  return url.slice(0, at) + swapped + url.slice(at + 1);
}

describe("sign", () => {
  it("gives decktalk.app a link that expires in six hours", async () => {
    const response = await sign();
    expect(response.status).toBe(200);
    expect(response.headers.get("Access-Control-Allow-Origin")).toBe(SITE);
    expect(response.headers.get("Cache-Control")).toBe("no-store");
    const body = await response.json();
    expect(body.url).toMatch(new RegExp(`^${BASE}/v/${KEY.replaceAll(".", "\\.")}\\?exp=\\d+&sig=[\\w-]+$`));
    expect(Math.abs(body.expires - (now() + 21600))).toBeLessThanOrEqual(5);
  });

  it("refuses a request with no Origin", async () => {
    expect((await sign(KEY, {})).status).toBe(403);
  });

  it.each(["https://example.com", "https://decktalk.app.example.com", "http://decktalk.app", "null"])(
    "refuses the origin %s",
    async (origin) => {
      const response = await sign(KEY, { headers: { Origin: origin } });
      expect(response.status).toBe(403);
      expect(response.headers.get("Access-Control-Allow-Origin")).toBeNull();
    },
  );

  it.each([
    "secret.txt",
    "decktalk-demo-latest.mp4",
    "decktalk-demo-20260914T151110Z.mp4",
    "decktalk-demo-20260914T151110Z-0810246F57B4.mp4",
    "decktalk-demo-20260914T151110Z-0810246f57b.mp4",
    `${KEY}.html`,
  ])("signs nothing but a demo cut, not %s", async (key) => {
    expect((await sign(key)).status).toBe(404);
  });

  it("answers only GET", async () => {
    expect((await sign(KEY, { method: "POST", headers: { Origin: SITE } })).status).toBe(405);
  });

  it.each([
    ["an unknown key", "secret.txt", { headers: { Origin: SITE } }, 404],
    ["a POST", KEY, { method: "POST", headers: { Origin: SITE } }, 405],
  ])("lets decktalk.app read its refusal of %s, so the page does not retry it", async (_, key, init, code) => {
    const response = await sign(key, init);
    expect(response.status).toBe(code);
    expect(response.headers.get("Access-Control-Allow-Origin")).toBe(SITE);
    expect(response.headers.get("Vary")).toContain("Origin");
  });
});

describe("serve", () => {
  it("plays a signed link", async () => {
    const response = await SELF.fetch(await link());
    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toBe("video/mp4");
    expect(response.headers.get("Content-Length")).toBe("1000");
    expect(response.headers.get("ETag")).toMatch(/^".+"$/);
    expect(response.headers.get("X-Content-Type-Options")).toBe("nosniff");
    expect(response.headers.get("Cross-Origin-Resource-Policy")).toBe("same-site");
    expect(response.headers.get("Cache-Control")).toBe("private, max-age=3600");
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(FILM);
  });

  it("answers HEAD with the length and no body", async () => {
    const response = await SELF.fetch(await link(), { method: "HEAD" });
    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Length")).toBe("1000");
    expect((await response.arrayBuffer()).byteLength).toBe(0);
  });

  it("refuses a link with no signature", async () => {
    expect((await SELF.fetch(`${BASE}/v/${KEY}`)).status).toBe(403);
    expect((await SELF.fetch(`${BASE}/v/${KEY}?exp=${now() + 60}`)).status).toBe(403);
  });

  it("refuses a tampered signature", async () => {
    const url = await link();
    expect((await SELF.fetch(tamper(url))).status).toBe(403);
    expect((await SELF.fetch(url.replace(/sig=.*/, "sig=not*base64"))).status).toBe(403);
    expect((await SELF.fetch(url.replace(/sig=.*/, `sig=${"A".repeat(44)}`))).status).toBe(403);
  });

  it("refuses the same signature written with different unused bits", async () => {
    const url = await link();
    const last = url.at(-1);
    // Flipping the lowest bit of the last character changes only bits that decoding throws away.
    const respelled = url.slice(0, -1) + BASE64URL[BASE64URL.indexOf(last) ^ 1];
    expect((await SELF.fetch(respelled)).status).toBe(403);
    expect((await SELF.fetch(url)).status).toBe(200);
  });

  it("refuses a link whose expiry was pushed back", async () => {
    const url = await link();
    const later = url.replace(/exp=(\d+)/, (_, exp) => `exp=${Number(exp) + 86400}`);
    expect((await SELF.fetch(later)).status).toBe(403);
  });

  it("refuses an expired link, and plays the same link before it expires", async () => {
    expect((await SELF.fetch(await forge(KEY, now() - 1))).status).toBe(403);
    expect((await SELF.fetch(await forge(KEY, now() + 60))).status).toBe(200);
  });

  it("refuses a signature made for another key", async () => {
    const other = await forge(MISSING, now() + 60);
    expect((await SELF.fetch(other.replace(MISSING, KEY))).status).toBe(403);
  });

  it("refuses a signed link to anything but a demo cut", async () => {
    expect((await SELF.fetch(await forge("secret.txt", now() + 60))).status).toBe(403);
  });

  it("answers 404 for a signed link to a cut that is not in the bucket, for GET and HEAD", async () => {
    const url = await forge(MISSING, now() + 60);
    expect((await SELF.fetch(url)).status).toBe(404);
    expect((await SELF.fetch(url, { method: "HEAD" })).status).toBe(404);
  });

  it.each([
    ["bytes=0-99", "bytes 0-99/1000", 0, 100],
    ["bytes=0-0", "bytes 0-0/1000", 0, 1],
    ["bytes=500-", "bytes 500-999/1000", 500, 1000],
    ["bytes=900-5000", "bytes 900-999/1000", 900, 1000],
    ["bytes=-100", "bytes 900-999/1000", 900, 1000],
    ["bytes=-5000", "bytes 0-999/1000", 0, 1000],
  ])("answers Range: %s with %s", async (range, contentRange, start, end) => {
    const response = await SELF.fetch(await link(), { headers: { Range: range } });
    expect(response.status).toBe(206);
    expect(response.headers.get("Content-Range")).toBe(contentRange);
    expect(response.headers.get("Content-Length")).toBe(String(end - start));
    expect(new Uint8Array(await response.arrayBuffer())).toEqual(FILM.slice(start, end));
  });

  it.each(["bytes=2000-", "bytes=1000-1001", "bytes=-0"])("answers Range: %s with 416", async (range) => {
    const response = await SELF.fetch(await link(), { headers: { Range: range } });
    expect(response.status).toBe(416);
    expect(response.headers.get("Content-Range")).toBe("bytes */1000");
  });

  it.each(["bytes=abc", "bytes=0-1,5-6", "bytes=5-2", "items=0-9", "bytes=-"])(
    "sends the whole file for a Range it does not handle: %s",
    async (range) => {
      const response = await SELF.fetch(await link(), { headers: { Range: range } });
      expect(response.status).toBe(200);
      expect(new Uint8Array(await response.arrayBuffer())).toEqual(FILM);
    },
  );

  it("answers only GET and HEAD", async () => {
    expect((await SELF.fetch(await link(), { method: "POST" })).status).toBe(405);
  });
});

describe("failures", () => {
  it.each([
    ["no signing key", { SIGNING_KEY: undefined }],
    ["a signing key under 32 bytes", { SIGNING_KEY: "too short to be safe" }],
    ["a link length that is not a number", { LINK_SECONDS: "six hours" }],
    ["a link length over a day", { LINK_SECONDS: "999999" }],
    ["no allowed origin", { SITE_ORIGINS: " , " }],
  ])("refuses to sign or serve with %s, and logs why", async (_, bindings) => {
    const url = await link();
    const signed = await call(`${BASE}/sign/${KEY}`, { headers: { Origin: SITE } }, bindings);
    const served = await call(url, {}, bindings);
    expect(signed.response.status).toBe(500);
    expect(served.response.status).toBe(500);
    expect(served.logs).toContainEqual(expect.objectContaining({ level: "error", event: "misconfigured" }));
  });

  it("lets decktalk.app read a misconfigured Worker's 500, and gives other origins nothing", async () => {
    const ours = await call(`${BASE}/sign/${KEY}`, { headers: { Origin: SITE } }, { SIGNING_KEY: undefined });
    const theirs = await call(
      `${BASE}/sign/${KEY}`,
      { headers: { Origin: "https://example.com" } },
      { SIGNING_KEY: undefined },
    );
    expect(ours.response.status).toBe(500);
    expect(ours.response.headers.get("Access-Control-Allow-Origin")).toBe(SITE);
    expect(theirs.response.status).toBe(500);
    expect(theirs.response.headers.get("Access-Control-Allow-Origin")).toBeNull();
  });

  it("answers 503 and logs the error when R2 fails", async () => {
    const broken = {
      head: async () => {
        throw new Error("R2 is down");
      },
    };
    const { response, logs } = await call(await link(), {}, { MEDIA: broken });
    expect(response.status).toBe(503);
    expect(response.headers.get("Retry-After")).toBe("1");
    expect(logs).toContainEqual(expect.objectContaining({ level: "error", event: "r2 failed", error: "R2 is down" }));
  });

  it("answers 503 when the cut changes between reading its size and reading its bytes", async () => {
    const changing = {
      head: async () => ({ size: 1000, etag: "old", httpEtag: '"old"' }),
      get: async () => ({ size: 1000, etag: "new", httpEtag: '"new"' }),
    };
    const { response, logs } = await call(await link(), {}, { MEDIA: changing });
    expect(response.status).toBe(503);
    expect(logs).toContainEqual(expect.objectContaining({ reason: "cut changed while it was served" }));
  });

  it("answers 500 and logs the stack when something unexpected throws", async () => {
    const strange = { head: async () => ({ size: 1000, etag: "a", httpEtag: '"a"' }), get: null };
    const { response, logs } = await call(await link(), {}, { MEDIA: strange });
    expect(response.status).toBe(503);
    expect(logs).toContainEqual(expect.objectContaining({ event: "r2 failed" }));
  });
});

describe("logging", () => {
  it("logs a refused link with its reason, and never the signature or the key", async () => {
    const url = await link();
    const sig = new URL(url).searchParams.get("sig");
    const tampered = await call(tamper(url));
    const expired = await call(await forge(KEY, now() - 1));
    expect(tampered.logs).toEqual([
      expect.objectContaining({
        level: "warn",
        event: "refused",
        status: 403,
        reason: "bad signature",
        path: `/v/${KEY}`,
      }),
    ]);
    expect(expired.logs).toEqual([expect.objectContaining({ reason: "expired" })]);
    const written = JSON.stringify([...tampered.logs, ...expired.logs]);
    expect(written).not.toContain(sig.slice(0, -1));
    expect(written).not.toContain("sig=");
    expect(written).not.toContain(env.SIGNING_KEY);
  });

  it("logs a refused origin", async () => {
    const { logs } = await call(`${BASE}/sign/${KEY}`, { headers: { Origin: "https://example.com" } });
    expect(logs).toEqual([
      expect.objectContaining({
        event: "refused",
        status: 403,
        reason: "origin not allowed",
        origin: "https://example.com",
      }),
    ]);
  });

  it("logs nothing for a request that succeeds, unless LOG_LEVEL is debug", async () => {
    const url = await link();
    expect((await call(url)).logs).toEqual([]);
    const debug = await call(url, { headers: { Range: "bytes=0-9" } }, { LOG_LEVEL: "debug" });
    expect(debug.response.status).toBe(206);
    expect(debug.logs).toEqual([
      expect.objectContaining({ level: "debug", event: "served", key: KEY, offset: 0, length: 10, range: "bytes=0-9" }),
      expect.objectContaining({ level: "debug", event: "answered", status: 206 }),
    ]);
    expect(JSON.stringify(debug.logs)).not.toContain("sig=");
  });
});

it("answers 404 everywhere else, and logs it only in debug mode", async () => {
  expect((await SELF.fetch(`${BASE}/`)).status).toBe(404);
  expect((await SELF.fetch(`${BASE}/${KEY}`)).status).toBe(404);
  expect((await call(`${BASE}/wp-admin`)).logs).toEqual([]);
  expect((await call(`${BASE}/wp-admin`, {}, { LOG_LEVEL: "debug" })).logs).toContainEqual(
    expect.objectContaining({ reason: "unknown path" }),
  );
});
