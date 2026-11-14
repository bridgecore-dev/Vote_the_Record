"""Draft a weekly or monthly "What Congress did" recap post from official feeds only.

    python src/social/recap.py --week 2026-11-09          # the Monday-Sunday week containing that date
    python src/social/recap.py --month 2026-11
    python src/social/recap.py --week 2026-11-09 --reel   # a Reel instead of a carousel
    add --write to save social/posts/<today>-congress-week-<monday>.yaml (or -month-<YYYY-MM>); default prints it.

Sources (no news, no social sites):
- New public laws: the GovInfo RSS feed (https://www.govinfo.gov/rss/plaw.xml) and each law's GovInfo MODS
  record, which gives the approval date, the short title, and the bill it came from.
- House roll calls: the Clerk's roll-call list (clerk.house.gov/evslegacy/<year>/) and each roll's XML
  (clerk.house.gov/evs/<year>/rollNNN.xml).
- Senate roll calls: the Senate's vote menu XML (senate.gov/legislative/LIS/roll_call_lists/vote_menu_<c>_<s>.xml).
- What each bill does: the Congressional Research Service summary from api.congress.gov, with the key in the
  CONGRESS_API_KEY environment variable (an api.data.gov key; sent as a header, never printed). Without the
  key, or when a bill has no CRS summary yet, the bill's `does`/`affects`/`summary` stay empty and `notes`
  says so, so `build.py --check` fails until a person fills them from the official summary.

`affects` is always left for a person to write: it is our words, and it must not go past the summary. `does`
is filled only when the summary's first sentence fits (140 characters), and then it is that sentence verbatim.
The draft always uses `approval: review` (it names bills). One request at a time, a short pause between
requests, and no retries: if a site refuses, the run stops and says which one.
"""
import argparse
import datetime as dt
import html
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

# The post checker (slide limits, caption length), loaded by path: "build" is also the site builder's name.
_spec = importlib.util.spec_from_file_location("social_build", Path(__file__).resolve().parent / "build.py")
build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build)

ROOT = Path(__file__).resolve().parents[2]
USER_AGENT = "VoteTheRecord-recap/1.0 (nonpartisan civic records project)"
PLAW_RSS = "https://www.govinfo.gov/rss/plaw.xml"
MODS = "https://www.govinfo.gov/metadata/pkg/PLAW-{c}publ{n}/mods.xml"
HOUSE_INDEX = "https://clerk.house.gov/evslegacy/{y}/index.asp"
HOUSE_PAGE = "https://clerk.house.gov/evslegacy/{y}/{page}"
HOUSE_ROLL = "https://clerk.house.gov/evs/{y}/roll{n:03d}.xml"
SENATE_MENU = "https://www.senate.gov/legislative/LIS/roll_call_lists/vote_menu_{c}_{s}.xml"
SUMMARIES = "https://api.congress.gov/v3/bill/{c}/{t}/{n}/summaries?format=json"
BILL_PAGE = "https://www.congress.gov/bill/{c}th-congress/{slug}/{n}"
MODS_NS = {"m": "http://www.loc.gov/mods/v3"}

# Measures that can become law, as each feed writes them once spaces and dots are dropped.
KINDS = {"HR": ("H.R.", "hr", "house-bill"), "S": ("S.", "s", "senate-bill"),
         "HJRES": ("H.J.Res.", "hjres", "house-joint-resolution"),
         "SJRES": ("S.J.Res.", "sjres", "senate-joint-resolution")}
# Final passage: the vote that sends a bill or joint resolution on (or ends it), in each chamber's own words.
HOUSE_FINAL = ("On Passage", "On Motion to Suspend the Rules and Pass", "On Agreeing to the Conference Report",
               "On Motion to Concur in the Senate Amendment", "On Motion to Agree to the Senate Amendment",
               "On Motion to Suspend the Rules and Concur", "On Overriding the Veto")
SENATE_FINAL = ("On Passage of the Bill", "On the Joint Resolution", "On the Conference Report", "On Overriding the Veto",
                "On the Motion to Concur")
PARTY = re.compile(r"\b(Republicans?|Democrats?|Democratic Party|Republican Party|GOP)\b")
MONTHS = ["Jan.", "Feb.", "March", "April", "May", "June", "July", "Aug.", "Sept.", "Oct.", "Nov.", "Dec."]
ROWS_PER_SLIDE = 8
RULE = ("Counts: every roll-call vote in the House Clerk's and the Senate's official lists, and every public law "
        "GovInfo shows approved {span}. Listed: every bill that became law, then every final-passage vote on a bill "
        "or joint resolution (passage, suspend and pass, conference report, agreeing to the other chamber's "
        "amendment, veto override), in date order. If the post runs out of room, the list stops in that order and "
        "the rest are counted on the last slide.")


class Blocked(Exception):
    """A source refused or failed a request. We never retry; the run reports it."""

    def __init__(self, url: str, why):
        super().__init__(f"{url} answered {why}")
        self.url, self.why = url, why


class Fetcher:
    """Polite GETs: a project User-Agent, a pause between requests, one try each, cached for the run."""

    def __init__(self, delay: float = 0.6):
        self.delay, self.cache, self.last = delay, {}, 0.0

    def get(self, url: str, headers: dict | None = None) -> bytes:
        if url in self.cache:
            return self.cache[url]
        wait = self.last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            raise Blocked(url, e.code) from None
        except (urllib.error.URLError, TimeoutError) as e:
            raise Blocked(url, getattr(e, "reason", e)) from None
        finally:
            self.last = time.monotonic()
        self.cache[url] = data
        return data


# ---------- dates ----------

def period(week: str | None = None, month: str | None = None) -> tuple[dt.date, dt.date, str]:
    """(first day, last day, 'week' or 'month')."""
    if week:
        d = dt.date.fromisoformat(week)
        start = d - dt.timedelta(days=d.weekday())
        return start, start + dt.timedelta(days=6), "week"
    y, m = map(int, month.split("-"))
    nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
    return dt.date(y, m, 1), nxt - dt.timedelta(days=1), "month"


def day(d: dt.date, year: bool = False) -> str:
    return f"{MONTHS[d.month - 1]} {d.day}" + (f", {d.year}" if year else "")


def span(start: dt.date, end: dt.date) -> str:
    """Nov. 9-15, 2026 · Nov. 30-Dec. 6, 2026 · Dec. 28, 2026-Jan. 3, 2027 · November 2026"""
    if start.day == 1 and (end + dt.timedelta(days=1)).day == 1 and start.month == end.month:
        return f"{start:%B %Y}"
    if start.year != end.year:
        return f"{day(start, True)}-{day(end, True)}"
    if start.month != end.month:
        return f"{day(start)}-{day(end, True)}"
    return f"{day(start)}-{end.day}, {end.year}"


def congress_of(year: int) -> tuple[int, int]:
    return (year - 1789) // 2 + 1, 1 if year % 2 else 2


def text(el, path: str, ns=None) -> str:
    return " ".join((el.findtext(path, namespaces=ns) or "").split())


# ---------- bills ----------

def bill(raw: str, congress: int) -> dict | None:
    """A law-capable measure from 'H R 9612', 'H.J.Res. 140', 'S. 12'; None for resolutions and nominations."""
    m = re.fullmatch(r"(HR|S|HJRES|SJRES)(\d+)", re.sub(r"[\s.]", "", raw or "").upper())
    if not m:
        return None
    label, api, slug = KINDS[m[1]]
    return {"id": f"{label} {int(m[2])}", "type": api, "number": int(m[2]), "congress": congress,
            "page": BILL_PAGE.format(c=congress, slug=slug, n=int(m[2]))}


# ---------- sources ----------

def mods(fetch, c: int, n: int):
    """A public law's GovInfo MODS record, or None when GovInfo has no such law (yet)."""
    try:
        data = fetch.get(MODS.format(c=c, n=n))
    except Blocked as e:
        if e.why == 404:
            return None
        raise
    # GovInfo answers a law it doesn't have with a redirect to its "not found" page, not a 404
    return ET.fromstring(data) if data.lstrip().startswith(b"<mods") else None


def law(fetch, c: int, n: int) -> dict | None:
    m = mods(fetch, c, n)
    if m is None:
        return None
    short = text(m, "m:extension/m:shortTitle", MODS_NS) or text(m, "m:titleInfo/m:title", MODS_NS)
    b = None
    for el in m.iterfind("m:extension/m:bill", MODS_NS):
        if el.get("priority") == "primary":
            b = bill(el.get("type", "") + el.get("number", ""), int(el.get("congress") or c))
            break
    return {"congress": c, "number": n, "title": short, "short": text(m, "m:extension/m:shortTitle", MODS_NS),
            "bill": b, "url": MODS.format(c=c, n=n),
            "approved": dt.date.fromisoformat(text(m, "m:originInfo/m:dateIssued", MODS_NS))}


def newest(fetch, c: int, known: int) -> int:
    """The highest public law number GovInfo has for a Congress. The RSS feed can lag or drop items, so start
    from the highest number it lists and probe upward (doubling, then halving): a few requests, stopping at 404s."""
    if known < 1:
        if law(fetch, c, 1) is None:
            return 0
        known = 1
    lo, step = known, 1
    while law(fetch, c, lo + step) is not None:
        lo, step = lo + step, step * 2
    hi = lo + step  # missing
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if law(fetch, c, mid) is not None else (lo, mid)
    return lo


def laws(fetch, start: dt.date, end: dt.date) -> list[dict]:
    """Public laws approved in the period. Laws are numbered in the order they are approved, so this walks down
    from the newest law GovInfo has until one was approved before the period (or 3 numbers in a row are missing)."""
    root = ET.fromstring(fetch.get(PLAW_RSS))
    known: dict[int, int] = {}
    for item in root.iter("item"):
        m = re.fullmatch(r"PLAW-(\d+)publ(\d+)", text(item, "guid"))
        if m:
            known[int(m[1])] = max(known.get(int(m[1]), 0), int(m[2]))
    congresses = sorted({congress_of(y)[0] for y in range(start.year, end.year + 1)})
    if start.year % 2 and start.month == 1 and start.day <= 3:  # a new Congress begins Jan. 3
        congresses.insert(0, congresses[0] - 1)
    out = []
    for c in congresses:
        n, missing = newest(fetch, c, known.get(c, 0)), 0
        while n > 0 and missing < 3:
            x = law(fetch, c, n)
            n -= 1
            if x is None:
                missing += 1
                continue
            missing = 0
            if x["approved"] < start:
                break
            if x["approved"] <= end:
                out.append(x)
    out.sort(key=lambda x: (x["approved"], x["number"]))
    return out


def cells(row: str) -> list[str]:
    return [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).split())
            for c in re.findall(r"<TD[^>]*>(.*?)</TD>", row, flags=re.S | re.I)]


def house(fetch, start: dt.date, end: dt.date) -> tuple[int, list[dict], list[dict]]:
    """(roll calls in the period, final-passage votes on law-capable measures, pages used)."""
    count, final, sources = 0, [], []
    for year in range(start.year, end.year + 1):
        try:
            index = fetch.get(HOUSE_INDEX.format(y=year)).decode("utf-8", "replace")
        except Blocked as e:
            if e.why == 404:  # no votes that year yet
                continue
            raise
        pages = sorted(set(re.findall(r"ROLL_\d+\.asp", index)), key=lambda p: -int(re.sub(r"\D", "", p)))
        for page in pages:
            url = HOUSE_PAGE.format(y=year, page=page)
            listing = fetch.get(url).decode("utf-8", "replace")
            dates, used = [], False
            for row in re.findall(r"<TR>(.*?)</TR>", listing, flags=re.S | re.I):
                c = cells(row)
                if len(c) < 6 or not c[0].isdigit():
                    continue
                when = dt.datetime.strptime(f"{c[1]}-{year}", "%d-%b-%Y").date()
                dates.append(when)
                if not start <= when <= end:
                    continue
                count, used = count + 1, True
                congress = congress_of(year)[0]
                if c[3].startswith(HOUSE_FINAL) and bill(c[2], congress):
                    final.append({"year": year, "roll": int(c[0])})
            if used:
                sources.append({"title": f"Office of the Clerk, U.S. House, Roll Call Votes {year} ({page[:-4]})",
                                "url": url})
            if dates and min(dates) < start:
                break
    votes = []
    for f in final:
        url = HOUSE_ROLL.format(y=f["year"], n=f["roll"])
        meta = ET.fromstring(fetch.get(url)).find("vote-metadata")
        congress = int(text(meta, "congress"))
        tot = meta.find("vote-totals/totals-by-vote")  # whole-chamber totals only; never the per-party rows
        votes.append({"chamber": "House", "congress": congress, "number": f["roll"],
                      "date": dt.datetime.strptime(text(meta, "action-date"), "%d-%b-%Y").date(),
                      "bill": bill(text(meta, "legis-num"), congress), "question": text(meta, "vote-question"),
                      "result": text(meta, "vote-result"), "title": text(meta, "vote-desc"),
                      "yeas": int(text(tot, "yea-total")), "nays": int(text(tot, "nay-total")),
                      "record": f"us/{congress}/{congress_of(int(f["year"]))[1]}/house/roll/{f['roll']}",
                      "source": {"title": f"House Clerk, Roll Call {f['roll']} ({f['year']})", "url": url}})
    return count, votes, sources


def senate(fetch, start: dt.date, end: dt.date) -> tuple[int, list[dict], list[dict]]:
    count, votes, sources = 0, [], []
    for year in range(start.year, end.year + 1):
        c, s = congress_of(year)
        url = SENATE_MENU.format(c=c, s=s)
        try:
            root = ET.fromstring(fetch.get(url))
        except Blocked as e:
            if e.why == 404:
                continue
            raise
        used = False
        for v in root.find("votes").findall("vote"):
            when = dt.datetime.strptime(f"{text(v, 'vote_date')}-{text(root, 'congress_year')}", "%d-%b-%Y").date()
            if not start <= when <= end:
                continue
            count, used = count + 1, True
            q, title, b = text(v, "question"), text(v, "title"), bill(text(v, "issue"), c)
            final = q.startswith(SENATE_FINAL) or (q == "On the Motion" and title.startswith("Motion to Concur"))
            if final and b:
                n = int(text(v, "vote_number"))
                votes.append({"chamber": "Senate", "congress": c, "number": n, "date": when, "bill": b,
                              "question": q, "result": text(v, "result"), "title": title.split("; ", 1)[-1],
                              "yeas": int(text(v, "vote_tally/yeas")), "nays": int(text(v, "vote_tally/nays")),
                              "record": f"us/{c}/{s}/senate/roll/{n}", "source": None})
        if used:
            sources.append({"title": f"U.S. Senate, Roll Call Votes, {c}th Congress, Session {s}", "url": url})
    return count, votes, sources


def clean(summary_html: str) -> str:
    """CRS summary as plain text, without the lead-in paragraphs before the summary itself: the bold title line
    and any italic note (e.g. "The following analysis is based on draft bill text ...")."""
    s, lead = summary_html, re.compile(r"^\s*<p>\s*<(em|strong|b|i)>.*?</\1>\s*</p>", re.S)
    while lead.match(s):
        s = lead.sub("", s, count=1)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


def quote(s: str, limit: int = 300) -> str:
    """Whole sentences of the summary, word for word, up to the limit; a cut first sentence ends with '…'."""
    out = ""
    for sentence in re.split(r"(?<=\.)\s+(?=[A-Z])", s):
        if len(out) + len(sentence) + (1 if out else 0) > limit:
            break
        out = f"{out} {sentence}".strip()
    if not out:
        out = s[:limit - 1].rsplit(" ", 1)[0] + "…"
    return out


def crs(fetch, b: dict, key: str | None, today: dt.date) -> tuple[dict | None, str | None]:
    """(the CRS summary as a bills `summary`, plus its first sentence) or (None, why not)."""
    if not key:
        return None, "CONGRESS_API_KEY is not set"
    url = SUMMARIES.format(c=b["congress"], t=b["type"], n=b["number"])
    data = json.loads(fetch.get(url, {"X-Api-Key": key}))
    found = data.get("summaries") or []
    if not found:
        return None, "CRS has no summary for it yet"
    latest = max(found, key=lambda x: (x.get("actionDate") or "", x.get("updateDate") or ""))
    body = clean(latest.get("text") or "")
    if not body:
        return None, "the CRS summary is empty"
    return {"text": quote(body), "title": f"CRS summary of {b['id']}",
            "url": b["page"], "retrieved": today.isoformat(),
            "_first": re.split(r"(?<=\.)\s+(?=[A-Z])", body)[0],
            "_version": f"{latest.get('actionDesc')} ({latest.get('actionDate')})"}, None


# ---------- the post ----------

def gather_items(law_list: list[dict], votes: list[dict]) -> list[dict]:
    """One item per bill, in the listing order: bills that became law, then other bills by their first vote."""
    items, by = [], {}
    for law in law_list:
        key = law["bill"]["id"] if law["bill"] else f"PL {law['congress']}-{law['number']}"
        by[key] = {"bill": law["bill"], "law": law, "votes": []}
        items.append(by[key])
    for v in sorted(votes, key=lambda v: (v["date"], v["chamber"] != "House", v["number"])):
        key = v["bill"]["id"]
        if key not in by:
            by[key] = {"bill": v["bill"], "law": None, "votes": []}
            items.append(by[key])
        by[key]["votes"].append(v)
    return items


SHORT_TITLE = re.compile(r"(?!(A|An) (bill|act|joint resolution)\b|To |Providing )[A-Z].{0,118}\b(Act|Resolution)( of \d{4})?")


def short_title(item: dict) -> str | None:
    """The bill's official short title, for bills[].title: the law's (GovInfo MODS shortTitle), else the first
    vote description that is one (the Clerk's or Senate's, e.g. 'Major Richard Star Act'), never a long title."""
    if item.get("law") and item["law"].get("short"):
        return item["law"]["short"]
    for v in item["votes"]:
        if SHORT_TITLE.fullmatch(v.get("title") or ""):
            return v["title"]
    return None


def tally(v: dict) -> str:
    return f"{v['chamber']}, {day(v['date'])}: {v['result']}, {v['yeas']}-{v['nays']}"


def where(item: dict) -> str:
    if item["law"]:
        law = item["law"]
        return f"Became Public Law {law['congress']}-{law['number']} on {day(law['approved'], True)}."
    v = item["votes"][-1]
    return f"{v['chamber']} vote {day(v['date'], True)}: {v['result']}, {v['yeas']}-{v['nays']}."


def chunks(rows: list, n: int = ROWS_PER_SLIDE) -> list[list]:
    return [rows[i:i + n] for i in range(0, len(rows), n)]


def assemble(start, end, kind, counts, items, shown, sources, summaries, reel, today, taken) -> dict:
    s = span(start, end)
    H, S, L = counts["house"], counts["senate"], counts["laws"]
    show, rest = items[:shown], items[shown:]
    laws_shown = [i for i in show if i["law"]]
    votes_shown = [v for i in show for v in i["votes"]]
    votes_shown.sort(key=lambda v: (v["date"], v["chamber"] != "House", v["number"]))
    hidden_laws = sum(1 for i in rest if i["law"])
    hidden_votes = sum(len(i["votes"]) for i in rest)
    slides = [{"kicker": f"{'Weekly' if kind == 'week' else 'Monthly'} recap · Congress",
               "head": f"What Congress did, {s}",
               "rows": [["New public laws", str(L)], ["House roll-call votes", str(H)], ["Senate roll-call votes", str(S)]],
               "text": "Listed: every new law, then every vote to pass a bill or joint resolution, in date order."}]
    for k, part in enumerate(chunks(laws_shown)):
        slides.append({"kicker": "Became law" + (" (cont.)" if k else ""),
                       "head": f"{len(laws_shown)} new public law{'s' if len(laws_shown) != 1 else ''} approved {s}",
                       "rows": [[i["bill"]["id"] if i["bill"] else f"Public Law {i['law']['congress']}-{i['law']['number']}",
                                 f"{i['law']['title']} · PL {i['law']['congress']}-{i['law']['number']}, {day(i['law']['approved'])}"]
                                for i in part]})
    for k, part in enumerate(chunks(votes_shown)):
        slides.append({"kicker": "Final-passage votes" + (" (cont.)" if k else ""),
                       "head": "Votes to pass bills and joint resolutions",
                       "rows": [[v["bill"]["id"], tally(v)] for v in part],
                       "by": "Tallies: yeas-nays, as each chamber recorded them."})
    if rest:
        bits = [f"{n} more {w}{'s' if n != 1 else ''}" for n, w in ((hidden_laws, "new law"), (hidden_votes, "final-passage vote")) if n]
        slides.append({"kicker": "Not shown", "head": f"{' and '.join(bits).capitalize()} {s}.",
                       "text": "Each one is in the official lists cited in the caption. We list items in date order "
                               "and stop when the post is full."})
    if not laws_shown and not votes_shown and not rest:
        slides.append({"kicker": "On the record", "head": "No new laws and no final-passage votes in this period.",
                       "text": "Every roll-call vote counted here was on a nomination, a procedural motion, an "
                               "amendment, or a resolution."})
    if reel:
        for sl in slides:
            sl["seconds"] = 6 if sl.get("rows") else 4

    lines = [f"What Congress did, {s}.", f"New public laws: {L}. House roll-call votes: {H}. Senate roll-call votes: {S}."]
    if laws_shown:
        lines.append("Became law:\n" + "\n".join(
            f"• {i['law']['title']} ({(i['bill']['id'] + ', ') if i['bill'] else ''}Public Law "
            f"{i['law']['congress']}-{i['law']['number']}, approved {day(i['law']['approved'])})" for i in laws_shown))
    elif not L:
        lines.append("GovInfo lists no public laws approved in this period yet.")
    if votes_shown:
        ids = list(dict.fromkeys(v["bill"]["id"] for v in votes_shown))
        lines.append(f"Final-passage votes: {len(votes_shown)}, on {', '.join(ids)}. "
                     "Each bill's tally and what it does are below.")
    if rest:
        lines.append(f"Not shown for space: {slides[-1]['head'][0].lower()}{slides[-1]['head'][1:]}")
    if show:
        lines.append("Swipe for what each bill does, from the Congressional Research Service summary.")

    bills, todo = [], []
    for i in show:
        if not i["bill"]:
            continue
        b, (summ, why) = i["bill"], summaries.get(i["bill"]["id"], (None, "not looked up"))
        entry = {"id": b["id"], **({"title": t} if (t := short_title(i)) else {}), "does": "", "affects": "",
                 "where": where(i)}
        if summ:
            first = summ["_first"]
            if len(first) <= build.BILL_LIMITS["does"]:
                entry["does"] = first
            entry["summary"] = {k: v for k, v in summ.items() if not k.startswith("_")}
            todo.append(f"{b['id']}: write `affects`" + ("" if entry["does"] else " and `does`")
                        + f" from the CRS summary ({summ['_version']}); never past it.")
        else:
            entry["summary"] = {"text": "", "title": f"CRS summary of {b['id']}",
                                "url": b["page"], "retrieved": today.isoformat()}
            todo.append(f"{b['id']}: no CRS summary ({why}); fill does, affects and summary.text from the "
                        "official summary at the url given, or drop the bill and say so in the rule.")
        bills.append(entry)

    srcs = list(sources["laws_feed"])
    srcs += [{"title": f"GovInfo, Public Law {i['law']['congress']}-{i['law']['number']} (metadata)", "url": i["law"]["url"]}
             for i in laws_shown]
    srcs += sources["house"] + [v["source"] for v in votes_shown if v["source"]] + sources["senate"]
    for x in srcs:
        x["retrieved"] = today.isoformat()

    key = f"us/{congress_of(start.year)[0]}/recap/{kind}-{start.isoformat() if kind == 'week' else start.strftime('%Y-%m')}"
    own = [f"us/{i['law']['congress']}/public-law/{i['law']['number']}" for i in laws_shown] + [v["record"] for v in votes_shown]
    record_ids = [key] + [r for r in own if r not in taken]
    already = [f"{r} ({taken[r]})" for r in own if r in taken]
    notes = [f"Draft from recap.py on {today.isoformat()}. Selection rule in `rule`.",
             "GovInfo posts a law days after it is signed: a law approved late in the period may not be counted yet.",
             "Senate tallies are yeas-nays from the Senate's vote menu; House tallies are the whole-chamber totals in "
             "each roll's XML. Party totals in the feeds are never used."]
    if already:
        notes.append("Already in other posts, so listed here but left out of record_ids: " + ", ".join(already) + ".")
    if any(not b["does"] or not b["affects"] for b in bills):
        notes.append(f"Caption room assumes does <= {BUDGET['does']} and affects <= {BUDGET['affects']} characters.")
    if rest:
        notes.append(f"{len(rest)} bill(s) left off for space (slide or caption limit); counted on the last slide.")
    notes += [f"TODO {t}" for t in todo]

    post = {"kind": "reel" if reel else "carousel", "level": "congress", "topic": "Congress recap", "approval": "review",
            "rule": RULE.format(span=s), "record_ids": record_ids, "sources": srcs, "slides": slides,
            "caption": "\n\n".join(lines), "hashtags": ["congress", "rollcall", "newlaws", "civics", "votetherecord"],
            "notes": " ".join(notes), "bills": bills}
    return post


# Room kept in the caption for each blank a person fills (build.py adds every bill's does/affects/where to it).
BUDGET = {"does": 105, "affects": 90}


def fits(post: dict) -> bool:
    """Room for the slides (with one explainer per bill) and the caption, once each blank is filled to BUDGET."""
    trial = json.loads(json.dumps(post))
    for b in trial["bills"]:
        b["does"] = b["does"] or "x" * BUDGET["does"]
        b["affects"] = b["affects"] or "x" * BUDGET["affects"]
        b["summary"]["text"] = b["summary"]["text"] or "x" * 300
    lo, hi = (2, 20) if post["kind"] == "reel" else (2, 10)
    return lo <= len(build.expanded(trial)) <= hi and len(build.full_caption(trial)) <= 2200


def taken_records() -> dict[str, str]:
    out = {}
    for p in sorted((ROOT / "social/posts").glob("*.yaml")):
        for r in (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("record_ids") or []:
            out.setdefault(r, p.name)
    return out


def draft(fetch, start: dt.date, end: dt.date, kind: str, reel: bool = False, key: str | None = None,
          today: dt.date | None = None, taken: dict | None = None) -> dict:
    today = today or dt.date.today()
    law_list = laws(fetch, start, end)
    h_count, h_votes, h_src = house(fetch, start, end)
    s_count, s_votes, s_src = senate(fetch, start, end)
    items = gather_items(law_list, h_votes + s_votes)
    counts = {"house": h_count, "senate": s_count, "laws": len(law_list)}
    sources = {"laws_feed": [{"title": "GovInfo, Public and Private Laws RSS feed", "url": PLAW_RSS}],
               "house": h_src, "senate": s_src}
    taken = taken_records() if taken is None else taken
    def make(n, summ):
        return assemble(start, end, kind, counts, items, n, sources, summ, reel, today, taken)

    shown = next(n for n in range(len(items), -1, -1) if n == 0 or fits(make(n, {})))
    summaries, api = {}, key
    for i in items[:shown]:  # only the bills that will be shown: no requests for the rest
        if i["bill"] and i["bill"]["id"] not in summaries:
            try:
                summaries[i["bill"]["id"]] = crs(fetch, i["bill"], api, today)
            except Blocked as e:
                if e.why == 404:
                    summaries[i["bill"]["id"]] = (None, "api.congress.gov has no record of it")
                    continue
                api = None  # stop asking; a person fills the rest
                summaries[i["bill"]["id"]] = (None, f"api.congress.gov refused the request ({e.why}); not retried")
    while shown and not fits(make(shown, summaries)):  # a filled-in summary can take more room than the estimate
        shown -= 1
    post = make(shown, summaries)
    dumped = yaml.safe_dump(post, sort_keys=False, allow_unicode=True)
    if PARTY.search(dumped):  # rule 5: a feed's text named a party; a person must look before this posts
        raise SystemExit(f"the draft contains a party name ({PARTY.search(dumped)[0]}); not writing it")
    return post


def filename(start: dt.date, kind: str, reel: bool, today: dt.date) -> str:
    tail = start.isoformat() if kind == "week" else start.strftime("%Y-%m")
    return f"{today.isoformat()}-congress-{kind}-{tail}{'-reel' if reel else ''}.yaml"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--week", help="any date in the Monday-Sunday week, YYYY-MM-DD")
    g.add_argument("--month", help="YYYY-MM")
    ap.add_argument("--reel", action="store_true", help="a Reel (2-20 slides) instead of a carousel")
    ap.add_argument("--write", action="store_true", help="save to social/posts/ instead of printing")
    args = ap.parse_args()
    start, end, kind = period(args.week, args.month)
    today = dt.date.today()
    try:
        post = draft(Fetcher(), start, end, kind, args.reel, os.environ.get("CONGRESS_API_KEY") or None, today)
    except Blocked as e:
        sys.exit(f"stopped: {e.url} refused the request ({e.why}). Not retrying; try later or skip this source.")
    out = yaml.safe_dump(post, sort_keys=False, allow_unicode=True, width=120)
    if not args.write:
        sys.stdout.reconfigure(encoding="utf-8")
        print(out)
        return
    path = ROOT / "social/posts" / filename(start, kind, args.reel, today)
    if path.exists():
        sys.exit(f"{path} already exists; not overwriting")
    path.write_text(out, encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}; next: python src/social/build.py --check {path.relative_to(ROOT).as_posix()}")


if __name__ == "__main__":
    main()
