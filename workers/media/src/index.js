// Serves the demo film from the decktalk-media R2 bucket at media.decktalk.app, only through
// short-lived signed links.
//
//   GET /sign/<key>             From a page on SITE_ORIGINS: {"url": ".../v/<key>?exp=...&sig=...", "expires": ...}
//   GET|HEAD /v/<key>?exp&sig   The file, or one byte range of it, until the link expires.
//
// A signature is an HMAC-SHA256 of the key and its expiry under SIGNING_KEY, a Worker secret that
// exists nowhere else. Only a browser on decktalk.app gets a link, because a browser sets the Origin
// header itself. A script can forge that header, so this stops other sites from embedding the film
// and links from outliving a visit. It cannot stop a visitor from saving what they watch.
//
// Logs are JSON objects in Workers Logs. Every refused request and every failure is logged with its
// reason, and with LOG_LEVEL = "debug" every request that succeeds is logged too. These logs never hold
// a signature, a full link, or the signing key. Cloudflare's own log of each request would store the
// full link, so wrangler.jsonc turns it off. `npx wrangler tail decktalk-media` streams the logs live,
// and shows each request's full link there too, but keeps nothing.

// A cut's key holds its upload time and the first 12 hex digits of its SHA-256. The Worker does not
// hash what it serves. `bash scripts/publish_demo.sh --check KEY` checks a cut against its key.
const KEY_RE = /^decktalk-demo-\d{8}T\d{6}Z-[0-9a-f]{12}\.mp4$/;
const MIN_SIGNING_KEY_BYTES = 32;
const MAX_LINK_SECONDS = 86400;
const UNSATISFIABLE = Symbol("unsatisfiable");
const encoder = new TextEncoder();

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const log = logger(env, request, url);
    let response;
    try {
      const problem = configProblem(env);
      if (problem) {
        // Fail closed. Signing with a missing key would hand out links anyone could forge.
        log.error("misconfigured", { problem });
        response = status(500);
      } else if (url.pathname.startsWith("/sign/")) {
        response = await sign(request, env, url, log);
      } else if (url.pathname.startsWith("/v/")) {
        response = await serve(request, env, url, log);
      } else {
        // Scanners probe every host, so an unknown path is logged only in debug mode.
        log.debug("refused", { status: 404, reason: "unknown path" });
        response = status(404);
      }
    } catch (err) {
      log.error("failed", { error: err instanceof Error ? (err.stack ?? err.message) : String(err) });
      response = status(500);
    }
    log.debug("answered", { status: response.status });
    return withCors(request, env, url, response);
  },
};

async function sign(request, env, url, log) {
  if (request.method !== "GET") return refuse(log, 405, "method not allowed");
  const origin = request.headers.get("Origin");
  if (!allowedOrigins(env).includes(origin)) return refuse(log, 403, "origin not allowed");
  const key = url.pathname.slice("/sign/".length);
  if (!KEY_RE.test(key)) return refuse(log, 404, "not a demo cut");
  const expires = Math.floor(Date.now() / 1000) + Number(env.LINK_SECONDS);
  const sig = await hmac(env, `${key}\n${expires}`);
  log.debug("signed", { key, expires });
  return Response.json(
    { url: `${url.origin}/v/${key}?exp=${expires}&sig=${sig}`, expires },
    {
      headers: {
        "Access-Control-Allow-Origin": origin,
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        Vary: "Origin",
      },
    },
  );
}

async function serve(request, env, url, log) {
  if (request.method !== "GET" && request.method !== "HEAD") return refuse(log, 405, "method not allowed");
  const key = url.pathname.slice("/v/".length);
  const problem = await linkProblem(env, key, url.searchParams);
  if (problem) return refuse(log, 403, problem);

  let head;
  try {
    head = await env.MEDIA.head(key);
  } catch (err) {
    return unavailable(log, err);
  }
  if (head === null) return refuse(log, 404, "cut not in the bucket");

  const headers = new Headers({
    "Content-Type": "video/mp4",
    "Accept-Ranges": "bytes",
    ETag: head.httpEtag,
    // Every link is its own URL, so a shared cache would only fill up with copies.
    "Cache-Control": "private, max-age=3600",
    // A browser treats the file only as the video it says it is, and plays it only on a page of
    // decktalk.app, even if a link leaks to another site.
    "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Resource-Policy": "same-site",
  });
  // The range is worked out here from the file's size rather than left to R2, which rejects some
  // ranges and quietly ignores others, and differs between Cloudflare and the local R2 in the tests.
  const range = byteRange(request.headers.get("Range"), head.size);
  if (range === UNSATISFIABLE) {
    headers.set("Content-Range", `bytes */${head.size}`);
    log.warn("refused", { status: 416, reason: "range outside the file", size: head.size });
    return new Response(null, { status: 416, headers });
  }
  if (request.method === "HEAD") {
    headers.set("Content-Length", String(head.size));
    log.debug("served", { key, size: head.size });
    return new Response(null, { headers });
  }

  let object;
  try {
    // A cut is never replaced, but if one ever were between head() and here, its size and ETag
    // above would be wrong. etagMatches makes R2 return no body in that case instead.
    object = await env.MEDIA.get(key, { range: range ?? undefined, onlyIf: { etagMatches: head.etag } });
  } catch (err) {
    return unavailable(log, err);
  }
  if (object === null) return refuse(log, 404, "cut deleted while it was served");
  if (!("body" in object) || !object.body) {
    log.warn("refused", { status: 503, reason: "cut changed while it was served" });
    return status(503, { "Retry-After": "1" });
  }

  if (range === null) {
    headers.set("Content-Length", String(head.size));
    log.debug("served", { key, size: head.size });
    return new Response(object.body, { headers });
  }
  headers.set("Content-Range", `bytes ${range.offset}-${range.offset + range.length - 1}/${head.size}`);
  headers.set("Content-Length", String(range.length));
  log.debug("served", { key, size: head.size, offset: range.offset, length: range.length });
  return new Response(object.body, { status: 206, headers });
}

// One byte range from a Range header, as RFC 9110 defines it: {offset, length}, UNSATISFIABLE, or null
// for the whole file. A header this does not handle, such as several ranges or a malformed one, is
// ignored and the whole file is sent, which the RFC allows. Browsers ask for one range at a time.
function byteRange(header, size) {
  const match = /^bytes=(\d*)-(\d*)$/.exec((header ?? "").trim());
  if (!match || (match[1] === "" && match[2] === "")) return null;
  if (match[1] === "") {
    const tail = Number(match[2]);
    if (tail === 0 || size === 0) return UNSATISFIABLE;
    const length = Math.min(tail, size);
    return { offset: size - length, length };
  }
  const offset = Number(match[1]);
  if (offset >= size) return UNSATISFIABLE;
  const last = match[2] === "" ? size - 1 : Math.min(Number(match[2]), size - 1);
  if (last < offset) return null;
  return { offset, length: last - offset + 1 };
}

async function linkProblem(env, key, params) {
  if (!KEY_RE.test(key)) return "not a demo cut";
  const expires = Number(params.get("exp"));
  if (!params.has("exp") || !Number.isInteger(expires)) return "no expiry";
  if (expires <= Date.now() / 1000) return "expired";
  if (!(await verify(env, `${key}\n${expires}`, params.get("sig") ?? ""))) return "bad signature";
  return null;
}

function configProblem(env) {
  if (typeof env.SIGNING_KEY !== "string" || encoder.encode(env.SIGNING_KEY).length < MIN_SIGNING_KEY_BYTES) {
    return `SIGNING_KEY is missing or shorter than ${MIN_SIGNING_KEY_BYTES} bytes`;
  }
  const seconds = Number(env.LINK_SECONDS);
  if (!Number.isInteger(seconds) || seconds < 60 || seconds > MAX_LINK_SECONDS) {
    return `LINK_SECONDS must be a whole number from 60 to ${MAX_LINK_SECONDS}`;
  }
  if (allowedOrigins(env).length === 0) return "SITE_ORIGINS names no origin";
  return null;
}

function allowedOrigins(env) {
  return String(env.SITE_ORIGINS ?? "")
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean);
}

function logger(env, request, url) {
  const context = {
    method: request.method,
    path: url.pathname, // Never url.search, which holds the signature.
    origin: request.headers.get("Origin"),
    range: request.headers.get("Range"),
    ray: request.headers.get("cf-ray"),
  };
  const write = (level, event, fields) =>
    console[level === "debug" ? "log" : level]({ level, event, ...context, ...fields });
  return {
    debug: (event, fields = {}) => {
      if (env.LOG_LEVEL === "debug") write("debug", event, fields);
    },
    warn: (event, fields = {}) => write("warn", event, fields),
    error: (event, fields = {}) => write("error", event, fields),
  };
}

// A page on an allowed origin can read every answer from /sign/, refusals included, so it can tell a
// refusal it should not retry from a network failure. A browser hides a cross-origin answer without
// this header. A request from any other origin still gets no header, so it learns nothing.
function withCors(request, env, url, response) {
  if (!url.pathname.startsWith("/sign/") || response.headers.has("Access-Control-Allow-Origin")) return response;
  const origin = request.headers.get("Origin");
  if (!allowedOrigins(env).includes(origin)) return response;
  const answered = new Response(response.body, response);
  answered.headers.set("Access-Control-Allow-Origin", origin);
  answered.headers.append("Vary", "Origin");
  return answered;
}

function refuse(log, code, reason) {
  log.warn("refused", { status: code, reason });
  return status(code);
}

// R2 can fail for a moment. 503 tells the page to try again rather than give up on the film.
function unavailable(log, err) {
  log.error("r2 failed", { error: err instanceof Error ? err.message : String(err) });
  return status(503, { "Retry-After": "1" });
}

function status(code, headers = {}) {
  return new Response(null, { status: code, headers: { "Cache-Control": "no-store", ...headers } });
}

async function signingKey(env) {
  return crypto.subtle.importKey("raw", encoder.encode(env.SIGNING_KEY), { name: "HMAC", hash: "SHA-256" }, false, [
    "sign",
    "verify",
  ]);
}

async function hmac(env, message) {
  const mac = await crypto.subtle.sign("HMAC", await signingKey(env), encoder.encode(message));
  return btoa(String.fromCharCode(...new Uint8Array(mac)))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/, "");
}

async function verify(env, message, sig) {
  // An HMAC-SHA256 is 32 bytes, which is 43 characters of unpadded base64url. The last character
  // carries 2 bits that decoding throws away, so a signature is accepted only as the Worker writes
  // it. Otherwise one link could be rewritten into several that all work.
  if (!/^[A-Za-z0-9_-]{43}$/.test(sig)) return false;
  let mac;
  try {
    mac = Uint8Array.from(atob(sig.replaceAll("-", "+").replaceAll("_", "/")), (c) => c.charCodeAt(0));
  } catch {
    return false;
  }
  const canonical = btoa(String.fromCharCode(...mac))
    .replaceAll("+", "-")
    .replaceAll("/", "_")
    .replace(/=+$/, "");
  if (canonical !== sig) return false;
  // crypto.subtle.verify compares in constant time, so the check leaks nothing about the signature.
  return crypto.subtle.verify("HMAC", await signingKey(env), mac, encoder.encode(message));
}
