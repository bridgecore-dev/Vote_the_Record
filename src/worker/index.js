// Cloudflare Worker for Vote the Record.
// Serves the static site (env.ASSETS) and handles two endpoints:
// - POST /api/report: the public corrections form. Reports are stored privately in the
//   REPORTS KV namespace for the project owner to review; nothing is published
//   automatically (CLAUDE.md rule 8). Stores only what the person typed, plus the time
//   received. No IP address, user agent, cookies, or other identifiers are read or kept.
// - POST /api/locate: finds an address on the map for the ballot lookup. The address is
//   passed to the U.S. Census Bureau geocoder and only the location and county come back.
//   Nothing is stored or logged, and the visitor's IP address is not passed on.
// On a timer (wrangler.jsonc "triggers"), it also asks GitHub to run the social-publish workflow, because GitHub
// drops most of that workflow's own scheduled runs. It does nothing until the GITHUB_DISPATCH_TOKEN secret is set.

export const LIMITS = { item: 200, problem: 3000, source: 500, contact: 200 };
const MIN_PROBLEM = 10;
const ITEM_PATTERN = /^[a-z]{2}(\/[a-z0-9][a-z0-9._-]*)+#[A-Za-z0-9-]+$/;

const SECURITY_HEADERS = {
  "Referrer-Policy": "no-referrer",
  "X-Content-Type-Options": "nosniff",
  "Cache-Control": "no-store",
};

function redirect(location) {
  return new Response(null, { status: 303, headers: { Location: location, ...SECURITY_HEADERS } });
}

// Returns { report } on success or { error } with a short reason code.
export function validate(form) {
  const get = (k) => (form.get(k) ?? "").toString().trim();
  if (get("website")) return { error: "spam" };  // honeypot: people never see this field
  const report = { item: get("item"), problem: get("problem"), source: get("source"), contact: get("contact") };
  for (const [k, max] of Object.entries(LIMITS)) if (report[k].length > max) return { error: "too-long" };
  if (report.problem.length < MIN_PROBLEM) return { error: "missing-problem" };
  if (report.item && !ITEM_PATTERN.test(report.item)) return { error: "bad-item" };
  if (report.source && !/^https?:\/\/\S+$/.test(report.source)) return { error: "bad-source" };
  return { report };
}

export async function handleReport(request, env, now = new Date()) {
  if (!env.REPORTS) return redirect("/report/error/?reason=unavailable");  // storage not bound (e.g. a preview)
  const type = request.headers.get("Content-Type") || "";
  if (!type.startsWith("application/x-www-form-urlencoded")) return redirect("/report/error/?reason=format");
  const length = Number(request.headers.get("Content-Length") || 0);
  if (length > 8192) return redirect("/report/error/?reason=too-long");
  const { report, error } = validate(await request.formData());
  if (error === "spam") return redirect("/report/thanks/");  // don't tell bots they were caught
  if (error) return redirect(`/report/error/?reason=${error}`);
  const received = now.toISOString();
  const key = `report:${received}:${crypto.randomUUID().slice(0, 8)}`;
  await env.REPORTS.put(key, JSON.stringify({ received, ...report }));
  return redirect("/report/thanks/");
}

const GEOCODER = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress";
const ADDRESS_MAX = 200;

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json", ...SECURITY_HEADERS },
  });
}

/** Clean a typed address, or return null if it can't be a street address. */
export function cleanAddress(text) {
  const address = String(text ?? "").replace(/\s+/g, " ").trim();
  if (address.length < 5 || address.length > ADDRESS_MAX || !/\d/.test(address)) return null;
  return address;
}

export async function handleLocate(request, fetchFn = fetch) {
  if (!(request.headers.get("Content-Type") || "").startsWith("application/json")) return json({ error: "format" }, 400);
  if (Number(request.headers.get("Content-Length") || 0) > 1024) return json({ error: "too-long" }, 400);
  let address;
  try {
    address = cleanAddress((await request.json()).address);
  } catch {
    return json({ error: "format" }, 400);
  }
  if (!address) return json({ error: "bad-address" }, 400);
  const url = `${GEOCODER}?${new URLSearchParams({
    address, benchmark: "Public_AR_Current", vintage: "Current_Current", layers: "Counties", format: "json",
  })}`;
  let data;
  try {
    const res = await fetchFn(url, { headers: { Accept: "application/json" } });
    if (!res.ok) return json({ error: "unavailable" }, 502);
    data = await res.json();
  } catch {
    return json({ error: "unavailable" }, 502);
  }
  const match = data?.result?.addressMatches?.[0];
  if (!match) return json({ error: "not-found" }, 404);
  const county = match.geographies?.Counties?.[0] || {};
  return json({
    matched: match.matchedAddress,
    lon: match.coordinates.x,
    lat: match.coordinates.y,
    county: county.NAME || null,
    stateFips: county.STATE || null,
  });
}

// The publish workflow to start on each timer tick (docs/SOCIAL_ROUTINE.md, src/social/publish.py).
export const DISPATCH = {
  url: "https://api.github.com/repos/bridgecore-dev/Vote_the_Record/actions/workflows/social-publish.yml/dispatches",
  ref: "main",
};

// Starts one publish run. The token is a fine-grained GitHub token for this repository only, with
// Actions read and write; it is a Worker secret, never in the repo, and never logged.
export async function dispatchPublish(env, fetcher = fetch) {
  if (!env.GITHUB_DISPATCH_TOKEN) return "no token";
  const res = await fetcher(DISPATCH.url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "vote-the-record-worker",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: DISPATCH.ref }),
  });
  if (res.status !== 204) throw new Error(`GitHub dispatch failed: HTTP ${res.status}`);
  return "dispatched";
}

export default {
  async scheduled(controller, env, ctx) {
    ctx.waitUntil(dispatchPublish(env));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/api/report") {
      if (request.method !== "POST") return new Response("Method not allowed", { status: 405, headers: { Allow: "POST", ...SECURITY_HEADERS } });
      return handleReport(request, env);
    }
    if (url.pathname === "/api/locate") {
      if (request.method !== "POST") return new Response("Method not allowed", { status: 405, headers: { Allow: "POST", ...SECURITY_HEADERS } });
      return handleLocate(request);
    }
    return env.ASSETS.fetch(request);
  },
};
