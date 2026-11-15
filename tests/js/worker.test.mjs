// Unit tests for the corrections-form Worker (src/worker/index.js). Uses Node's built-in
// Request/FormData and an in-memory stand-in for the KV namespace.
import { test } from "node:test";
import assert from "node:assert/strict";
import worker, { handleReport, validate } from "../../src/worker/index.js";

const ITEM = "tx/localities/harris-county/elections/2026-11-03/county-judge/candidates/letitia-plummer#R-01";
function kv() {
  const store = new Map();
  return { store, put: async (k, v) => { store.set(k, v); } };
}
function post(fields, headers = {}) {
  const body = new URLSearchParams(fields).toString();
  return new Request("https://example.org/api/report", {
    method: "POST", body,
    headers: { "Content-Type": "application/x-www-form-urlencoded", "Content-Length": String(body.length), ...headers },
  });
}

test("a valid report is stored privately with only the submitted fields and a timestamp", async () => {
  const env = { REPORTS: kv() };
  const res = await handleReport(post({ item: ITEM, problem: "The vote count here looks wrong.", source: "https://example.gov/minutes.pdf", contact: "me@example.com", website: "" }), env, new Date("2026-09-30T12:00:00Z"));
  assert.equal(res.status, 303);
  assert.equal(res.headers.get("Location"), "/report/thanks/");
  const [[key, value]] = [...env.REPORTS.store];
  assert.match(key, /^report:2026-09-30T12:00:00\.000Z:[0-9a-f]{8}$/);
  assert.deepEqual(JSON.parse(value), { received: "2026-09-30T12:00:00.000Z", item: ITEM,
    problem: "The vote count here looks wrong.", source: "https://example.gov/minutes.pdf", contact: "me@example.com" });
});

test("contact, source, and item are optional", async () => {
  const env = { REPORTS: kv() };
  const res = await handleReport(post({ problem: "A date on the home page is wrong." }), env);
  assert.equal(res.headers.get("Location"), "/report/thanks/");
  assert.equal(env.REPORTS.store.size, 1);
});

test("rejects missing or oversized text, bad IDs and bad links, without storing", async () => {
  for (const [fields, reason] of [
    [{ problem: "short" }, "missing-problem"],
    [{ problem: "x".repeat(3001) }, "too-long"],
    [{ problem: "Something is wrong here.", item: "not-an-id" }, "bad-item"],
    [{ problem: "Something is wrong here.", source: "javascript:alert(1)" }, "bad-source"],
  ]) {
    const env = { REPORTS: kv() };
    const res = await handleReport(post(fields), env);
    assert.equal(res.headers.get("Location"), `/report/error/?reason=${reason}`, reason);
    assert.equal(env.REPORTS.store.size, 0, reason);
  }
});

test("honeypot submissions look successful but are not stored", async () => {
  const env = { REPORTS: kv() };
  const res = await handleReport(post({ problem: "Buy cheap watches now!!!", website: "http://spam.example" }), env);
  assert.equal(res.headers.get("Location"), "/report/thanks/");
  assert.equal(env.REPORTS.store.size, 0);
});

test("only accepts form posts under 8 KB", async () => {
  const env = { REPORTS: kv() };
  const json = new Request("https://example.org/api/report", { method: "POST", body: "{}", headers: { "Content-Type": "application/json" } });
  assert.equal((await handleReport(json, env)).headers.get("Location"), "/report/error/?reason=format");
  const big = post({ problem: "x".repeat(20) }, { "Content-Length": "9000" });
  assert.equal((await handleReport(big, env)).headers.get("Location"), "/report/error/?reason=too-long");
});

test("routes: GET /api/report is refused; everything else goes to the static site", async () => {
  const env = { REPORTS: kv(), ASSETS: { fetch: async (req) => new Response("static:" + new URL(req.url).pathname) } };
  assert.equal((await worker.fetch(new Request("https://example.org/api/report"), env)).status, 405);
  assert.equal(await (await worker.fetch(new Request("https://example.org/report/"), env)).text(), "static:/report/");
});

test("validate never reads fields other than the form's own", () => {
  const form = new URLSearchParams({ problem: "A long enough problem.", extra: "ignored" });
  assert.deepEqual(Object.keys(validate(form).report).sort(), ["contact", "item", "problem", "source"]);
});

test("without the REPORTS binding, reports go to the 'unavailable' error page", async () => {
  const body = new URLSearchParams({ problem: "This vote count looks wrong." });
  const req = new Request("https://x.test/api/report", { method: "POST", body,
    headers: { "Content-Type": "application/x-www-form-urlencoded" } });
  const res = await handleReport(req, {});
  assert.equal(res.status, 303);
  assert.equal(res.headers.get("Location"), "/report/error/?reason=unavailable");
});

import * as workerModule from "../../src/worker/index.js";
import { cleanAddress, handleLocate } from "../../src/worker/index.js";
const GEOCODER = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress";

function locateRequest(body, type = "application/json") {
  return new Request("https://x.test/api/locate", { method: "POST", body: JSON.stringify(body), headers: { "Content-Type": type } });
}

const CENSUS_OK = { result: { addressMatches: [{
  matchedAddress: "1001 PRESTON ST, HOUSTON, TX, 77002",
  coordinates: { x: -95.361246, y: 29.761519 },
  geographies: { Counties: [{ NAME: "Harris County", STATE: "48", GEOID: "48201" }] },
}] } };

test("cleanAddress needs a number and a sane length", () => {
  assert.equal(cleanAddress("  1001   Preston St, Houston ") , "1001 Preston St, Houston");
  for (const bad of ["", "Main Street", "1 a", "9".repeat(201), null]) assert.equal(cleanAddress(bad), null, String(bad));
});

test("locate asks the Census geocoder and returns only the location and county", async () => {
  let asked;
  const res = await handleLocate(locateRequest({ address: "1001 Preston St, Houston, TX 77002" }), async (url) => {
    asked = url;
    return new Response(JSON.stringify(CENSUS_OK), { status: 200 });
  });
  assert.equal(res.status, 200);
  assert.equal(res.headers.get("Cache-Control"), "no-store");
  assert.ok(asked.startsWith(GEOCODER + "?"));
  assert.equal(new URL(asked).searchParams.get("address"), "1001 Preston St, Houston, TX 77002");
  assert.deepEqual(await res.json(), { matched: "1001 PRESTON ST, HOUSTON, TX, 77002", lon: -95.361246, lat: 29.761519,
    county: "Harris County", stateFips: "48" });
});

test("locate reports not-found, bad input, and an unavailable geocoder", async () => {
  const none = await handleLocate(locateRequest({ address: "123 Nowhere Rd" }),
    async () => new Response(JSON.stringify({ result: { addressMatches: [] } })));
  assert.equal(none.status, 404);
  assert.equal((await handleLocate(locateRequest({ address: "no numbers" }), async () => { throw new Error("called"); })).status, 400);
  assert.equal((await handleLocate(locateRequest({ address: "1 Main St" }, "text/plain"), async () => { throw new Error("called"); })).status, 400);
  const down = await handleLocate(locateRequest({ address: "1 Main St, Houston" }), async () => { throw new Error("network"); });
  assert.equal(down.status, 502);
  assert.deepEqual(await down.json(), { error: "unavailable" });
});

test("GET /api/locate is not allowed", async () => {
  const res = await worker.fetch(new Request("https://x.test/api/locate"), { ASSETS: { fetch: () => new Response("asset") } });
  assert.equal(res.status, 405);
});

test("the Worker module exports only functions and objects (strings break the Workers runtime)", () => {
  for (const [name, value] of Object.entries(workerModule)) {
    assert.ok(typeof value === "function" || (typeof value === "object" && value !== null), `${name} is a ${typeof value}`);
  }
});

test("the timer starts the publish workflow on main, and does nothing without a token", async () => {
  const { dispatchPublish, DISPATCH } = await import("../../src/worker/index.js");
  const calls = [];
  const fake = async (url, init) => { calls.push([url, init]); return new Response(null, { status: 204 }); };
  assert.equal(await dispatchPublish({}, fake), "no token");
  assert.equal(calls.length, 0);
  assert.equal(await dispatchPublish({ GITHUB_DISPATCH_TOKEN: "t" }, fake), "dispatched");
  const [[url, init]] = calls;
  assert.equal(url, DISPATCH.url);
  assert.match(url, /\/actions\/workflows\/social-publish\.yml\/dispatches$/);
  assert.equal(init.method, "POST");
  assert.equal(init.headers.Authorization, "Bearer t");
  assert.deepEqual(JSON.parse(init.body), { ref: "main" });
  const failing = async () => new Response("nope", { status: 401 });
  await assert.rejects(dispatchPublish({ GITHUB_DISPATCH_TOKEN: "t" }, failing), /HTTP 401/);
});
