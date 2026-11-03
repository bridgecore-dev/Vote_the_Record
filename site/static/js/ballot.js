// Ballot pages and the home page: ZIP/precinct/address lookup, topics, and the ballot-wide quiz.
// ZIP codes, precincts, topics, and quiz answers never leave the browser. Only the address
// lookup calls our own Worker (see locate below).
import { parseHash, buildHash, encodeAnswers, decodeAnswers, topicMatches, scoreBallot, coverageText } from "./quiz.js";
import { el, readAnswers, renderGrid, renderCandidate, comparisonNote } from "./survey.js";

/** "77002", " 77002-1234 " -> "77002"; anything else -> null. */
export function cleanZip(text) {
  const m = /^\s*(\d{5})(?:-\d{4})?\s*$/.exec(text || "");
  return m ? m[1] : null;
}

/**
 * Races on this ballot for a ZIP code, in ballot order, or null if the ZIP isn't covered.
 * payload.zips[zip] lists areas; a nested list means "one of these, depending on your
 * address", so those races are flagged split. Statewide and county-wide areas are implied.
 */
export function racesForZip(payload, zip) {
  const entries = payload.zips[zip];
  if (!entries) return null;
  const sure = new Set([...payload.implied, ...(payload.zipEverywhere || [])]);
  const maybe = new Set();
  for (const e of entries) {
    if (Array.isArray(e)) e.forEach((a) => maybe.add(a));
    else sure.add(e);
  }
  return payload.races
    .filter((r) => sure.has(r.area) || maybe.has(r.area))
    .map((r) => ({ n: r.n, split: maybe.has(r.area) && !sure.has(r.area) }));
}

/**
 * "0123", " 123 " -> "123"; "32a0" -> "32A0" (a few Dallas County precincts include letters).
 * Anything that isn't 1-6 letters and digits starting with a digit -> null.
 */
export function cleanPrecinct(text) {
  const m = /^\s*0*(\d[0-9A-Za-z]{0,5})\s*$/.exec(text || "");
  return m && m[1] !== "0" ? m[1].toUpperCase() : null;
}

/**
 * Races for a voting precinct, or null if the precinct isn't on this ballot. A nested entry
 * means the precinct is split (or, with one item, only partly in that area): those races are
 * flagged split, unless `atPoint` (the local areas containing the voter's exact location)
 * settles them.
 */
export function racesForPrecinct(payload, precinct, atPoint = null) {
  const codes = (payload.precincts || {})[precinct];
  if (!codes) return null;
  const known = new Set(payload.localAreas || []);
  const sure = new Set([...payload.implied, ...(payload.precinctEverywhere || [])]);
  const maybe = new Set();
  for (const code of codes) {
    if (!Array.isArray(code)) {
      sure.add(payload.areas[code]);
      continue;
    }
    for (const i of code) {
      const area = payload.areas[i];
      if (atPoint && known.has(area)) {
        if (atPoint.has(area)) sure.add(area);
      } else {
        maybe.add(area);
      }
    }
  }
  return payload.races
    .filter((r) => sure.has(r.area) || maybe.has(r.area))
    .map((r) => ({ n: r.n, split: maybe.has(r.area) && !sure.has(r.area) }));
}

/** Local areas (cities, districts) whose outlines contain the point. */
export function areasAt(areaShapes, lon, lat) {
  const inside = new Set();
  for (const [area, rings] of Object.entries(areaShapes.areas || {})) {
    if (pointInRings(rings, lon, lat)) inside.add(area);
  }
  return inside;
}

/** Even-odd point-in-polygon over all of a precinct's rings (holes included). */
export function pointInRings(rings, x, y) {
  let inside = false;
  for (const ring of rings) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
  }
  return inside;
}

/** The precinct whose outline contains the point, or null. */
export function findPrecinct(shapes, lon, lat) {
  for (const [id, rings] of Object.entries(shapes.precincts)) {
    if (pointInRings(rings, lon, lat)) return id;
  }
  return null;
}

/** "Harris County" -> "harris-county" (matches locality folder names). */
export function countySlug(name) {
  return String(name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

export const LOCATE_ERRORS = {
  "not-found": "We couldn't find that address. Check the house number and spelling, or use your ZIP code or precinct number.",
  "bad-address": "Enter a street address with a house number, like 1001 Preston St, Houston.",
  unavailable: "The address lookup isn't available right now. Use your ZIP code or precinct number instead.",
};

/** Ask our own Worker to find the address. Nothing is stored anywhere. */
export async function locate(address, fetchFn = fetch) {
  let res;
  try {
    res = await fetchFn("/api/locate", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ address }),
    });
  } catch {
    return { error: "unavailable" };
  }
  let data = {};
  try {
    data = await res.json();
  } catch {
    /* no body */
  }
  if (!res.ok) return { error: LOCATE_ERRORS[data.error] ? data.error : "unavailable" };
  return data;
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

function readJson(id) {
  const el = document.getElementById(id);
  return el ? JSON.parse(el.textContent) : null;
}

function initBallotPage(payload) {
  const form = document.getElementById("zip-form");
  const input = document.getElementById("zip");
  const pctInput = document.getElementById("precinct");
  const addrInput = document.getElementById("address");
  let shapesPromise = null;
  let areaShapesPromise = null;
  const status = document.getElementById("zip-status");
  const reset = document.getElementById("zip-reset");
  const rows = [...document.querySelectorAll("[data-race]")];
  const unmappedBox = document.getElementById("unmapped-box");
  const groups = [...document.querySelectorAll("[data-group]")];
  const picker = document.getElementById("topic-picker");
  const topicOnly = document.getElementById("topic-only");
  const topicStatus = document.getElementById("topic-status");
  const quizForm = document.getElementById("ballot-quiz-form");
  const quizOut = document.getElementById("ballot-results");
  const quizNone = document.getElementById("quiz-none");
  const quizBlocks = [...document.querySelectorAll("[data-quiz-race]")];
  const labels = payload.topicLabels || {};
  const quizRaces = payload.quiz || [];
  // Where the visitor is (null = whole ballot), and their topics. Kept in page memory only.
  const state = { found: null, zip: null, precinct: null, topics: [] };
  if (form) form.hidden = false;
  if (picker) picker.hidden = false;
  if (quizForm) quizForm.hidden = false;

  function render() {
    const matches = topicMatches(payload.races, state.topics);
    rows.forEach((row) => {
      const n = Number(row.dataset.race);
      const hit = state.found ? state.found.get(n) : null;
      const onBallot = !state.found || Boolean(hit);
      const topicHit = matches.get(n);
      row.hidden = !onBallot || (Boolean(topicOnly && topicOnly.checked) && state.topics.length > 0 && !topicHit);
      row.querySelector(".split-chip").hidden = !(hit && hit.split);
      row.classList.toggle("topic-hit", Boolean(topicHit));
      const chip = row.querySelector(".topic-chip");
      chip.hidden = !topicHit;
      chip.textContent = topicHit ? `Matches: ${topicHit.map((t) => labels[t] || t).join(", ")}` : "";
    });
    groups.forEach((g) => { g.hidden = !g.querySelector("[data-race]:not([hidden])"); });
    reset.hidden = !state.found;
    if (unmappedBox) unmappedBox.hidden = !state.found;
    if (topicStatus) {
      const onBallot = payload.races.filter((r) => !state.found || state.found.has(r.n));
      const count = onBallot.filter((r) => matches.has(r.n)).length;
      topicStatus.textContent = state.topics.length
        ? `${plural(count, "race", "races")} ${state.found ? "on your ballot " : ""}${count === 1 ? "deals" : "deal"} with ${state.topics.length === 1 ? "this topic" : "these topics"}.`
        : "";
    }
    // The quiz covers the races on your ballot (topics never hide quiz questions).
    let shown = 0;
    quizBlocks.forEach((block) => {
      block.hidden = Boolean(state.found) && !state.found.has(Number(block.dataset.quizRace));
      if (!block.hidden) shown += 1;
    });
    if (quizNone) quizNone.hidden = shown > 0;
  }

  function showAll() {
    state.found = null;
    state.zip = null;
    state.precinct = null;
    render();
  }

  function show(found) {
    state.found = new Map(found.map((r) => [r.n, r]));
    render();
  }

  function locationHash() {
    return buildHash({ zip: state.zip, precinct: state.precinct });
  }

  function applyZip(zip, updateHash) {
    const clean = cleanZip(zip);
    if (!clean) {
      status.textContent = "Enter a 5-digit ZIP code.";
      return;
    }
    const found = racesForZip(payload, clean);
    if (!found) {
      showAll();
      status.textContent = `ZIP code ${clean} isn't in our ${payload.name} data. Showing every race on this ballot.`;
      return;
    }
    state.zip = clean;
    state.precinct = null;
    show(found);
    const split = found.filter((r) => r.split).length;
    status.textContent = `ZIP code ${clean}: ${plural(found.length, "race", "races")} on your ballot, closest to home first.` +
      (split ? ` ${plural(split, "race depends", "races depend")} on your exact address. Add your precinct number to be sure.` : "");
    if (updateHash) history.replaceState(null, "", locationHash());
  }

  function loadShapes() {
    if (!shapesPromise) {
      shapesPromise = fetch(payload.shapesUrl).then((r) => {
        if (!r.ok) throw new Error("shapes");
        return r.json();
      });
    }
    return shapesPromise;
  }

  async function placeAt(lat, lon, label) {
    status.textContent = "Finding your precinct...";
    let shapes;
    try {
      shapes = await loadShapes();
    } catch {
      shapesPromise = null;
      status.textContent = "We couldn't load the precinct map. Use your ZIP code or precinct number instead.";
      return;
    }
    let atPoint = null;
    if (payload.areaShapesUrl) {
      try {
        if (!areaShapesPromise) areaShapesPromise = fetch(payload.areaShapesUrl).then((r) => r.json());
        atPoint = areasAt(await areaShapesPromise, lon, lat);
      } catch {
        areaShapesPromise = null;  // local races then show as "depends on your address"
      }
    }
    const precinct = findPrecinct(shapes, lon, lat);
    if (!precinct) {
      showAll();
      status.textContent = `${label}: that spot isn't inside a precinct in our ${payload.name} data. Showing every race.`;
      return;
    }
    if (pctInput) pctInput.value = precinct;
    applyPrecinct(precinct, true, label, atPoint);
  }

  async function applyAddress(text) {
    status.textContent = "Looking up your address...";
    const found = await locate(text);
    if (found.error) {
      status.textContent = LOCATE_ERRORS[found.error];
      return;
    }
    if (countySlug(found.county) !== payload.locality) {
      status.textContent = `That address is in ${found.county || "another county"}, which isn't on this ballot.`;
      return;
    }
    await placeAt(found.lat, found.lon, `Found ${found.matched}`);
  }

  function applyPrecinct(text, updateHash, label = "", atPoint = null) {
    const clean = cleanPrecinct(text);
    if (!clean) {
      status.textContent = "Enter your precinct number as it appears on your voter registration card" +
        (input ? ", or leave it blank and use your ZIP code." : ".");
      return;
    }
    const found = racesForPrecinct(payload, clean, atPoint);
    if (!found) {
      showAll();
      status.textContent = `Precinct ${clean} isn't in our ${payload.name} data. Showing every race on this ballot.`;
      return;
    }
    state.precinct = clean;
    state.zip = null;
    show(found);
    const split = found.filter((r) => r.split).length;
    status.textContent = `${label ? label + ". " : ""}Precinct ${clean}: ${plural(found.length, "race", "races")} on your ballot, closest to home first.` +
      (split ? ` ${plural(split, "race depends", "races depend")} on your exact address.` : "");
    if (updateHash) history.replaceState(null, "", locationHash());
  }

  // --- Topics ---
  function setTopics(topics) {
    state.topics = topics.filter((t) => t in labels);
    if (picker) {
      picker.querySelectorAll('input[name="topic"]').forEach((box) => { box.checked = state.topics.includes(box.value); });
    }
    render();
  }
  if (picker) {
    picker.addEventListener("change", () => {
      setTopics([...picker.querySelectorAll('input[name="topic"]:checked')].map((box) => box.value));
    });
  }

  // --- Ballot-wide quiz ---
  function readQuiz() {
    const answers = {};
    for (const race of quizRaces) answers[race.key] = readAnswers(quizForm, race.questions, `${race.n}-`);
    return answers;
  }

  function fillQuiz(answers) {
    for (const race of quizRaces) {
      for (const [qid, a] of Object.entries(answers[race.key] || {})) {
        const pick = quizForm.querySelector(`input[name="${CSS.escape(`answer-${race.n}-${qid}`)}"][value="${a.option}"]`);
        const weight = quizForm.querySelector(`input[name="${CSS.escape(`importance-${race.n}-${qid}`)}"][value="${a.importance}"]`);
        if (pick) pick.checked = true;
        if (weight) weight.checked = true;
      }
    }
  }

  function privateLink(answers) {
    const visible = quizRaces.filter((r) => !state.found || state.found.has(r.n));
    const mine = Object.fromEntries(visible.map((r) => [r.key, answers[r.key] || {}]));
    return window.location.origin + window.location.pathname +
      buildHash({ zip: state.zip, precinct: state.precinct, topics: state.topics, answers: encodeAnswers(mine) });
  }

  function showResults(answers) {
    const visible = quizRaces.filter((r) => !state.found || state.found.has(r.n));
    const scored = scoreBallot(visible, answers);
    const heading = el("h2", { id: "ballot-results-h", tabindex: "-1", text: "Your results" });
    quizOut.replaceChildren(heading);
    if (!scored.length) {
      quizOut.append(el("p", { text: "Answer at least one question to see results." }));
    } else {
      const candidateRaces = payload.races.filter((r) => r.kind !== "measure" && (!state.found || state.found.has(r.n))).length;
      quizOut.append(
        el("p", { class: "coverage", text: `${coverageText(scored, candidateRaces)} The rest don't have enough checked records yet, or are races for judges, which have no quiz.` }),
        el("p", { class: "muted", text: "How your answers line up with each candidate's verified record. Candidates are listed alphabetically. This is not a recommendation." }));
      for (const { race, results, closest } of scored) {
        const questionsById = new Map(race.questions.map((q) => [q.id, q]));
        const block = el("section", { class: "result-race", "aria-labelledby": `rr-${race.n}` },
          el("h3", { id: `rr-${race.n}` }, el("a", { href: race.url, text: race.name })));
        block.append(renderGrid({ questions: race.questions }, results, questionsById), comparisonNote(results));
        for (const { candidate, result } of results) {
          block.append(renderCandidate(candidate, result, questionsById, payload.formUrl, candidate.id === closest, `${race.n}-`));
        }
        quizOut.append(block);
      }
      const linkBox = el("input", { type: "text", readonly: "", class: "link-box", "aria-label": "Private link to these results", hidden: "" });
      const copyStatus = el("p", { class: "muted", role: "status" });
      const printBtn = el("button", { type: "button", text: "Print or save as PDF" });
      const copyBtn = el("button", { type: "button", class: "button-secondary", text: "Copy a private link" });
      printBtn.addEventListener("click", () => window.print());
      copyBtn.addEventListener("click", async () => {
        const link = privateLink(answers);
        linkBox.value = link;
        try {
          await navigator.clipboard.writeText(link);
          copyStatus.textContent = "Link copied. Your answers are in the part after #, which browsers never send to any server. Anyone you give the link to can see them.";
        } catch {
          linkBox.hidden = false;
          linkBox.select();
          copyStatus.textContent = "Copy this link. Your answers are in the part after #, which browsers never send to any server.";
        }
      });
      quizOut.append(el("div", { class: "form-actions result-actions" }, printBtn, copyBtn), linkBox, copyStatus);
    }
    quizOut.hidden = false;
    heading.focus();
  }

  if (quizForm) {
    quizForm.addEventListener("submit", (event) => {
      event.preventDefault();
      showResults(readQuiz());
    });
    quizForm.addEventListener("reset", () => {
      quizOut.replaceChildren();
      quizOut.hidden = true;
    });
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (addrInput && addrInput.value.trim()) applyAddress(addrInput.value);
    else if (pctInput && pctInput.value.trim()) applyPrecinct(pctInput.value, true);
    else if (input) applyZip(input.value, true);
    else applyPrecinct(pctInput ? pctInput.value : "", true);
  });
  if (topicOnly) topicOnly.addEventListener("change", render);
  reset.addEventListener("click", () => {
    showAll();
    status.textContent = "Showing every race on this ballot.";
    history.replaceState(null, "", window.location.pathname);
    (input || pctInput).focus();
  });

  const hash = parseHash(window.location.hash);
  if (hash.topics.length) setTopics(hash.topics);
  if (hash.at && payload.shapesUrl) {
    // From the home page's address box. The map location is dropped from the URL right
    // away; placeAt then records only the precinct number.
    history.replaceState(null, "", window.location.pathname);
    placeAt(hash.at[0], hash.at[1], "Found your address");
  } else if (hash.at) {
    // An address from the home page, but this county has no precinct map: say so and drop the location.
    history.replaceState(null, "", window.location.pathname);
    render();
    status.textContent = "We don't have a precinct map for this county yet. Enter your precinct number " +
      "(it's on your voter registration card) or your ZIP code to narrow the list.";
  } else if (hash.precinct && pctInput) {
    pctInput.value = hash.precinct;
    applyPrecinct(hash.precinct, false);
  } else if (hash.zip && input) {
    input.value = hash.zip;
    applyZip(hash.zip, false);
  } else {
    render();
  }
  if (hash.answers && quizForm) {
    // A private results link: fill in the answers, show the results, then drop the answers and
    // topics from the address bar so they don't linger in the browser's history.
    const answers = decodeAnswers(hash.answers, quizRaces);
    fillQuiz(answers);
    showResults(readQuiz());
    history.replaceState(null, "", locationHash() || window.location.pathname);
  }
}

function initHomeFinder(index, counties) {
  const form = document.getElementById("zip-find");
  const input = document.getElementById("zip-home");
  const addrInput = document.getElementById("address-home");
  const status = document.getElementById("zip-home-status");
  const choices = document.getElementById("zip-home-choices");
  form.hidden = false;

  async function findByAddress(text) {
    status.textContent = "Looking up your address...";
    const found = await locate(text);
    if (found.error) {
      status.textContent = LOCATE_ERRORS[found.error];
      return;
    }
    const ballots = counties[countySlug(found.county)] || [];
    if (!ballots.length) {
      status.textContent = `Found ${found.matched}, in ${found.county || "a county"} we don't cover yet. See the official voting info below.`;
      return;
    }
    // The location goes in the # part of the link, which browsers never send to a server.
    window.location.href = `${ballots[0].url}#at=${found.lat.toFixed(6)},${found.lon.toFixed(6)}`;
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    choices.replaceChildren();
    if (addrInput && addrInput.value.trim()) {
      findByAddress(addrInput.value);
      return;
    }
    const clean = cleanZip(input.value);
    if (!clean) {
      status.textContent = "Enter a 5-digit ZIP code.";
      return;
    }
    const ballots = index[clean] || [];
    if (ballots.length === 1) {
      window.location.href = `${ballots[0].url}#zip=${clean}`;
      return;
    }
    if (ballots.length === 0) {
      status.textContent = `We don't have ballot information for ZIP code ${clean} yet. See the official voting info below.`;
      return;
    }
    status.textContent = `ZIP code ${clean} crosses county lines. Which county do you live in?`;
    for (const b of ballots) {
      const li = document.createElement("li");
      const a = document.createElement("a");
      a.href = `${b.url}#zip=${clean}`;
      a.textContent = b.name;
      li.append(a);
      choices.append(li);
    }
  });
}

function init() {
  const ballot = readJson("ballot-data");
  if (ballot) initBallotPage(ballot);
  const index = readJson("zip-index");
  if (index) initHomeFinder(index, readJson("county-index") || {});
}

if (typeof document !== "undefined") init();
