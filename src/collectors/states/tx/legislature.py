"""Texas Legislature: every bill that became law, and each chamber's final-passage votes (docs/METHODOLOGY.md).

Reads files saved from Texas Legislature Online (bill histories, the lists of bills signed by the Governor and
filed without signature) and the House and Senate Journal PDFs, and writes one file per bill under
data/states/tx/statewide/legislature/<session>/bills/. Which votes: in each chamber, the vote that passed the
bill, and the vote to concur in the other chamber's amendments or adopt a conference committee report. Names
are exactly as the journal prints them. A Senate vote printed only as a tally names senators only when the
tally accounts for all 31 seats and every "nay" is named.

Usage (preview first; --write to apply):
  python src/collectors/states/tx/legislature.py --saved <downloads/legislature> --session 89R --retrieved YYYY-MM-DD [--write]

Everything written is unverified.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import re
import sys
import unicodedata
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[4]
SESSIONS = {"89R": ("89r", "89th Legislature, Regular Session"), "892": ("892", "89th Legislature, 2nd Called Session")}
SENATE_SEATS = 31  # Tex. Const. art. III, sec. 2

# Final-passage actions in the bill history, by chamber: (history wording, what the vote did).
PASSAGE = {"Passed": "passed", "Passed as amended": "passed as amended"}
FINAL_TEXT = {"House concurs in Senate amendment(s)": "concurred in the Senate's amendments",
              "Senate concurs in House amendment(s)": "concurred in the House's amendments",
              "Conference committee report adopted": "adopted the conference committee report",
              "House adopts conference committee report": "adopted the conference committee report",
              "Senate adopts conference committee report": "adopted the conference committee report"}

ROW = re.compile(r'<td data-label="Action Chamber">(.*?)</td>\s*<td data-label="Action Description">(.*?)</td>\s*'
                 r'<td data-label="Action Comment"[^>]*>(.*?)</td>\s*<td data-label="Action Date">(.*?)</td>\s*'
                 r'<td data-label="Action Time">(.*?)</td>\s*<td data-label="Journal Page Number"[^>]*>(.*?)</td>', re.S)


def clean(s: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", "", s)).replace("\xa0", " ").split())


def history(text: str) -> dict:
    """Caption and actions (oldest first) from a TLO bill history page."""
    cap = re.search(r'id="lblCaptionText">(.*?)</span>', text, re.S)
    actions = []
    for chamber, desc, comment, date, _time, page in ROW.findall(text):
        link = re.search(r'href="(https?://journals\.[^"#]+)(?:#page=(\d+))?', desc)
        m, d, y = clean(date).split("/")
        actions.append({"chamber": clean(chamber), "action": clean(desc), "comment": clean(comment),
                        "date": f"{y}-{m}-{d}", "journal": link[1].replace("http://", "https://") if link else None,
                        "pdf_page": int(link[2]) if link and link[2] else None,
                        "page": int(clean(page)) if clean(page).isdigit() else None})
    actions.reverse()
    return {"caption": clean(cap[1]) if cap else None, "actions": actions}


def final_votes(actions: list[dict]) -> list[dict]:
    """The final-passage actions, each with its record vote number (House) when one was taken."""
    out = []
    for i, a in enumerate(actions):
        what = PASSAGE.get(a["action"]) or FINAL_TEXT.get(a["action"])
        if not what or a["chamber"] not in ("H", "S"):
            continue
        rv = None
        for b in actions[i + 1:i + 4]:  # the "Record vote" row follows the action it records, same day
            if b["action"] == "Record vote" and b["chamber"] == a["chamber"] and b["date"] == a["date"]:
                rv = b
                break
            if PASSAGE.get(b["action"]) or FINAL_TEXT.get(b["action"]):
                break
        number = re.search(r"RV#(\d+)", rv["comment"]) if rv else None
        out.append({**a, "what": what, "record_vote": bool(rv), "record": int(number[1]) if number else None})
    return out


def journal_text(raw: str) -> str:
    """pypdf text with the journals' non-breaking spaces (read as 'i') and running page headers removed."""
    t = raw.replace("—", "—")
    t = re.sub(r"(?<=[A-Z]{2})i(?=\d)|(?<=Yeas)i(?=\d)|(?<=Nays)i(?=\d)|(?<=[.,:])i{1,2}(?=[A-Z])", " ", t)
    t = re.sub(r"\d+\s+89th LEGISLATURE — [A-Z ]*SESSION", " ", t)
    t = re.sub(r"\n\d+\s+89th Legislature — [A-Za-z ]*Session(?: \d+\w* Day(?: \(Cont\.\))?)?", "\n", t)
    t = re.sub(r"[A-Z][a-z]+day, [A-Z][a-z]+ \d{1,2}, \d{4}\s+(?:HOUSE|SENATE) JOURNAL(?:\s*—\s*[\w-]+ Day(?: \(Continued\))?)?\s*\d*", " ", t)
    return t


HOUSE_CATS = [("Present, not voting", "present_not_voting"), ("Absent, Excused, Committee Meeting", "absent_excused_committee"),
              ("Absent, Excused", "absent_excused"), ("Absent", "absent"), ("Yeas", "yeas"), ("Nays", "nays")]


def loose(pattern: str) -> str:
    """The regex with each space matching any whitespace or none: pypdf sometimes drops or breaks the spaces."""
    return pattern.replace(" ", r"\s*")


def quotable(quote: str) -> bool:
    """False when the text lost its spaces ("Themotionprevailedbythefollowingvote") and can't be quoted as is."""
    return not re.search(r"[A-Za-z]{22,}", quote)


def house_roll_call(text: str, record: int) -> dict | None:
    """Tally and names for House Record <record>; None when the journal text doesn't hold it."""
    m = re.search(loose(rf"\(Record {record}\): (\d+) Yeas?, (\d+) Nays?, (\d+) Present, not voting\."), text)
    if not m:
        return None
    tally = {"yeas": int(m[1]), "nays": int(m[2]), "present_not_voting": int(m[3])}
    body = text[m.end():m.end() + 6000]
    stop = re.search(r"\n[A-Z][A-Z ,()'-]{6,}\n|STATEMENTS? OF VOTE|REASONS? FOR VOTE|\(Record\s*\d+\)", body)
    body = body[:stop.start()] if stop else body
    names: dict[str, list[str]] = {}
    pattern = "|".join(loose(re.escape(label).replace("\\ ", " ")) for label, _ in HOUSE_CATS)
    parts = re.split(rf"(?:^|\.\s*|\n)({pattern})\s*—\s*", " " + body)
    keys = {label.replace(" ", ""): key for label, key in HOUSE_CATS}
    for label, chunk in zip(parts[1::2], parts[2::2]):
        key = keys[re.sub(r"\s", "", label)]
        chunk = chunk.split(".\n")[0] if ".\n" in chunk else chunk
        listed = [" ".join(n.split()) for n in re.split(r";\s*", chunk.strip().rstrip("."))]
        # the list's closing period is dropped; an initial keeps its own ("Jones, V.")
        names[key] = [n.rstrip(".") + ("." if re.search(r", [A-Z]\.?$", n) else "") for n in listed if n]
    for key in ("yeas", "nays", "present_not_voting"):
        if len(names.get(key, [])) != tally[key]:
            return {"tally": tally, "names": None, "problem": f"{key}: {len(names.get(key, []))} names for a tally of {tally[key]}"}
    # the record's words with their spacing restored; the sentence leading into it only if it kept its spaces
    quote = f"(Record {record}): {tally['yeas']} Yeas, {tally['nays']} Nays, {tally['present_not_voting']} Present, not voting."
    before = " ".join(sentence_before(text, m.start()).split())
    if before and quotable(before):
        quote = f"{before} {quote}"
    # a calendar passed by one record vote: each bill's own result follows, as changes from this roll call
    block = bool(re.search(loose(r"shown following the bill number"), text[max(0, m.start() - 400):m.start()]))
    return {"tally": tally, "names": names, "problem": None, "quote": quote, "block": block, "end": m.end()}


BLOCK_TO = {"no": "nays", "yes": "yeas", "present,notvoting": "present_not_voting"}


def house_block_vote(text: str, record: int, bill: str) -> dict | None:
    """One bill's vote on a calendar the House passed by a single record vote.

    The journal prints the calendar's roll call (Record 552), then each bill with the members whose votes differ
    from it and the bill's own tally: "HB 1318 (Bumgarner, Gerdes, and Vasut — no; Little — yes) (112 - 19 - 3)".
    Those changes are applied to the roll call, and the result must match the bill's tally.
    """
    base = house_roll_call(text, record)
    if not base or base["problem"] or not base["block"]:
        return None
    rest = text[base["end"]:]
    nxt = re.search(r"\(Record\s*\d+\)\s*:", rest)
    rest = rest[:nxt.start()] if nxt else rest
    prefix, num = bill.split()
    rest = re.sub(r"(?<=\w)i-i(?=no\b|yes\b|present)", " — ", rest)
    rest = re.sub(r"(?<=\d)i-i(?=\d)", "-", rest)
    m = list(re.finditer(loose(rf"(?<![A-Z]){prefix} {num}(?!\d) ") + r"(?:\(([^()]*)\)\s*)?\((\d+)\s*-\s*(\d+)\s*-\s*(\d+)\)", rest))
    if not m:
        # a bill printed alone on its line had no changes: its vote is the calendar's roll call
        bare = re.findall(loose(rf"\n{prefix} {num}(?!\d)") + r"[ \t]*(?=\n)", rest)
        if len(bare) == 1:
            return {"tally": base["tally"], "names": base["names"], "problem": None, "quote": f"{bill}",
                    "base_quote": base["quote"]}
    if len(m) != 1:
        return None
    m = m[0]
    tally = {"yeas": int(m[2]), "nays": int(m[3]), "present_not_voting": int(m[4])}
    names = {k: list(v) for k, v in base["names"].items()}
    problem = None
    for part in (m[1] or "").split(";"):
        if not part.strip():
            continue
        who, _, to = part.rpartition("—")
        key = BLOCK_TO.get(re.sub(r"\s", "", to).rstrip("."))
        if not key:
            problem = f"unknown change {part.strip()!r}"
            break
        listed = [x.strip() for x in re.split(r",\s*|\s+and\s+", " ".join(who.split())) if x.strip() and x.strip() != "and"]
        joined: list[str] = []  # "Davis, A." is one name
        for x in listed:
            x = re.sub(r"^and\s+", "", x)
            x = re.sub(r"^([A-Z]\.) (.+)$", r"\2, \1", x)  # "K. Bell" is the roll call's "Bell, K."
            if re.fullmatch(r"[A-Z]\.", x) and joined:
                joined[-1] += f", {x}"
            else:
                joined.append(x)
        for person in joined:
            found = [k for k, v in names.items() if person in v]
            if len(found) != 1:
                problem = f"{person!r} is in {len(found)} lists of Record {record}"
                break
            names[found[0]].remove(person)
            names.setdefault(key, []).append(person)
        if problem:
            break
    if not problem:
        for key in ("yeas", "nays", "present_not_voting"):
            if len(names.get(key, [])) != tally[key]:
                problem = f"{key}: {len(names.get(key, []))} names after the changes for a tally of {tally[key]}"
                break
    if problem:
        return {"tally": tally, "names": None, "problem": problem}
    changes = " ".join((m[1] or "").split())
    quote = f"{bill} ({changes}) ({tally['yeas']} - {tally['nays']} - {tally['present_not_voting']})" if changes else \
        f"{bill} ({tally['yeas']} - {tally['nays']} - {tally['present_not_voting']})"
    return {"tally": tally, "names": {k: v for k, v in names.items() if v}, "problem": None, "quote": quote,
            "base_quote": base["quote"]}


SENATE_CATS = [("Present-not voting", "present_not_voting"), ("Absent-excused", "absent_excused"), ("Absent", "absent"),
               ("Yeas", "yeas"), ("Nays", "nays")]


VOTE = loose(r"by the following vote: Yeas (\d+), Nays (\d+)") + r"(?:,\s*Present-not\s*voting[\si]*(\d+))?\."  # "votingi1": a lost space
CHAIR = r"\s*\((?:Senator\s*[^\s()]+|President)\s*in\s*Chair\)"  # "(Senator Hall in Chair)": who presided, not a vote


def senator(name: str) -> str:
    """A printed senator's name, spaced as the roll prints it: "A.Hinojosa" -> "A. Hinojosa"."""
    return re.sub(r"^([A-Z]\.)\s*", r"\1 ", " ".join(name.split()))


def senate_names(tail: str) -> dict[str, list[str]]:
    """The name lists printed right after a Senate vote ("Yeas: ... Nays: Hall, Hughes."), in any order."""
    names: dict[str, list[str]] = {}
    while True:
        for label_text, key in SENATE_CATS:
            n = re.match(rf"\s*{loose(re.escape(label_text).replace(chr(92) + ' ', ' '))}:\s*(.*?)(?<![\s,][A-Z])\.(?=\s|$)", tail, re.S)  # "A. Hinojosa" isn't the end
            if n and key not in names:
                names[key] = [senator(x) for x in re.sub(CHAIR, "", n[1]).split(",") if x.strip()]
                tail = tail[n.end():]
                break
        else:
            return names
WORDS = {"HB": "HOUSE BILL", "SB": "SENATE BILL"}


def senate_roll_call(text: str, bill: str, what: str) -> dict | None:
    """The Senate vote on <bill> ("SB 5") for this kind of action, from the journal page text.

    The journal heads each item ("HOUSE BILL 143 ON THIRD READING", "SENATE BILL 5 WITH HOUSE AMENDMENTS",
    "CONFERENCE COMMITTEE REPORT ON SENATE BILL 1"); the vote is the first one after the heading that does
    what the action did. A motion to suspend the rules, voted on first, never counts.
    """
    prefix, num = bill.split()
    words = WORDS[prefix]
    if what.startswith("passed"):
        heading, verb = rf"{words} {num}(?!\d) ON THIRD READING",r"was (?:again )?(?:read third time and was )?(?:again )?(?:finally )?passed"
        inline = rf"\b(?:CS)?{prefix} {num}(?: as amended)? was (?:finally )?passed {VOTE}"
    elif what.startswith("concurred"):
        heading, verb = rf"{words} {num}(?!\d) WITH HOUSE AMENDMENTS?",r"(?:motion prevailed|concurred)"
        # the heading can sit pages back, before the printed amendments; the motion names the bill again
        inline = rf"moved to concur in the House amendments? to (?:CS)?{prefix} {num}(?!\d)\. The motion prevailed {VOTE}"
    else:
        # headings can lose their spaces ("SENATEBILL763ADOPTED"), so the number ends at the next non-digit
        heading, verb = rf"CONFERENCE COMMITTEE REPORT ON\s+(?:COMMITTEE SUBSTITUTE\s+)?{words} {num}(?!\d)",r"(?:adopted|motion prevailed)"
        inline = (rf"(?:moved to adopt the Conference Committee Report on (?:CS)?{prefix} {num}(?!\d)\. The motion prevailed"
                  rf"|Conference Committee Report on (?:CS)?{prefix} {num}(?!\d) was adopted) {VOTE}")
    heading, verb, inline = loose(heading), loose(verb + " "), loose(inline) if inline else None
    hits = []
    if what.startswith("passed"):
        # after "VOTE RECONSIDERED ON HOUSE BILL 14" the bill is "again finally passed": that vote replaces the first
        hits = [(text, m) for m in re.finditer(loose(rf"\b(?:CS)?{prefix} {num}(?: as amended was again| as again amended was| was again) finally passed {VOTE}"), text)]
        if len(hits) > 1:
            return None
    for h in re.finditer(heading, text) if not hits else ():
        after = text[h.end():h.end() + 40000]  # printed remarks can run for pages before the vote
        nxt = re.search(loose(r"\n(?:COMMITTEE SUBSTITUTE )?(?:HOUSE|SENATE) (?:JOINT RESOLUTION|BILL|CONCURRENT RESOLUTION) \d+"), after)
        after = after[:nxt.start()] if nxt else after
        m = re.search(verb + VOTE, after)
        if m:
            hits.append((after, m))
    again = [(a, m) for a, m in hits if re.search(r"\bagain\b", m[0])]
    if len(hits) > 1 and len(again) == 1:
        hits = again  # the bill was passed, reconsidered, and "again finally passed": the last vote stands
    if not hits and inline:
        hits = [(text, m) for m in re.finditer(inline, text, re.I)]
    if len(hits) != 1:
        return None
    after, m = hits[0]
    text = after
    tally = {"yeas": int(m[1]), "nays": int(m[2])} | ({"present_not_voting": int(m[3])} if m[3] else {})
    tail = text[m.end():m.end() + 2500]
    same = re.match(loose(r" \(Same as previous roll(?: call)?\)"), tail)  # "roll" alone once, 89(2) HB 26
    if same:
        # the journal points to the roll call just before, on the same bill: its names, if its tally is the same
        prev = list(re.finditer(VOTE, text[:m.start()]))
        if not prev or (int(prev[-1][1]), int(prev[-1][2])) != (tally["yeas"], tally["nays"]):
            return {"tally": tally, "names": None, "problem": "'same as previous roll call' without a matching roll call"}
        names = senate_names(text[prev[-1].end():prev[-1].end() + 2500])
    else:
        names = senate_names(tail)
    if names.get("yeas") and len(names["yeas"]) != tally["yeas"]:
        return {"tally": tally, "names": None, "problem": "yea names don't match the tally"}
    if tally["nays"] and len(names.get("nays", [])) != tally["nays"]:
        return {"tally": tally, "names": None, "problem": "nay names don't match the tally"}
    present = f", Present-not voting {tally['present_not_voting']}" if "present_not_voting" in tally else ""
    quote = f"by the following vote: Yeas {tally['yeas']}, Nays {tally['nays']}{present}."
    lead = " ".join((sentence_before(text, m.start()) + " " + text[m.start():m.end()].split("by")[0]).split())
    lead = re.sub(r"\s*by$", "", lead)
    if lead and quotable(lead):
        quote = f"{lead} {quote}"
    if same:
        quote += " (Same as previous roll call)" if same[0].rstrip(")").endswith("call") else " (Same as previous roll)"
    return {"tally": tally, "names": names or None, "problem": None, "quote": quote}


CALENDAR = loose(r"All Members are deemed to have voted \"Yea\" on viva voce votes unless otherwise indicated.")
ENTRY = r"\n(?:CS)?(?:HB|SB|HCR|SCR|HJR|SJR)\s*\d+\s*\("


def senate_calendar_vote(text: str, bill: str) -> dict | None:
    """The final-passage vote on <bill> from the Senate's Local and Uncontested Calendar.

    The journal lists each bill with its votes after the caption, final passage last:
    "SB 3038 (Huffman) Relating to ... (viva voce vote) (27-4) "Nays" Hall, Hughes, Middleton, Sparks (27-4) "Nays" ...".
    """
    prefix, num = bill.split()
    found = {}
    for c in re.finditer(CALENDAR, text):
        # headings such as "ORDERED NOT PRINTED" can sit inside the calendar, so each entry ends on its own
        for e in re.finditer(loose(rf"\n(?:CS)?{prefix} {num}(?!\d) \("), text[c.end():]):
            part = text[c.end() + e.start():]
            nxt = re.search(ENTRY, part[1:])
            entry = part[:1 + nxt.start()] if nxt else part
            end = re.search(r"\n[A-Z][A-Z0-9 ,.'()-]{8,}\n", entry)  # nor past the next heading (no lowercase)
            entry = entry[:end.start()] if end else entry
            if re.search(loose(r"\(viva voce vote\)"), entry):
                found[c.end() + e.start()] = entry
    if len(found) != 1:
        return None
    entry = next(iter(found.values()))
    v = re.search(loose(r"\(viva voce vote\)"), entry)
    marks = list(re.finditer(r"\((\d+)\s*-\s*(\d+)(?:\s*-\s*(\d+))?\)", entry[v.end():]))  # (28-2-1): 1 present, not voting
    if len(marks) != 2:
        return {"tally": None, "names": None, "problem": f"{len(marks)} tallies after the caption, not 2"}
    final = marks[-1]
    tally = {"yeas": int(final[1]), "nays": int(final[2])} | ({"present_not_voting": int(final[3])} if final[3] else {})
    rest = " ".join(re.sub(CHAIR, "", entry[v.end() + final.end():]).split())
    names: dict[str, list[str]] = {}
    lists = re.fullmatch(r"(?:\"Nays?\"\s*([^\"]+?))?\s*(?:\"Present-\s*not\s*voting\"\s*([^\"]+))?", rest)
    if not lists:
        return {"tally": tally, "names": None, "problem": f"unread text after the final tally: {rest[:60]!r}"}
    for key, listed in (("nays", lists[1]), ("present_not_voting", lists[2])):
        if listed:
            names[key] = [senator(x) for x in listed.split(",") if x.strip()]
    for key in ("nays", "present_not_voting"):
        if len(names.get(key, [])) != tally.get(key, 0):
            return {"tally": tally, "names": None, "problem": f"{key.replace('_', ' ')} names don't match the tally"}
    after = " ".join(entry[v.start():].split())
    quote = f'All Members are deemed to have voted "Yea" on viva voce votes unless otherwise indicated. ... {after}'
    return {"tally": tally, "names": names or None, "problem": None, "quote": quote}


# ---------------------------------------------------------------------------------------------------------------
# Members: the names the journals print, matched to each member's Texas Legislature Online page and our profiles.

def member_pages(saved: Path, session: str) -> list[dict]:
    """Every member whose TLO page gives a district: chamber, district, name, list name, URL."""
    out = []
    for chamber, code in (("house", "H"), ("senate", "S")):
        listing = (saved / f"members-{code}.html").read_text(encoding="utf-8", errors="replace")
        for mcode, list_name in re.findall(rf"MemberInfo\.aspx\?Leg=89&Chamber={code}&Code=([A-Z0-9]+)'>\s*([^<]+?)\s*</a>", listing):
            page = saved / "members" / f"{code}-{mcode}.html"
            if not page.exists():
                continue
            text = page.read_text(encoding="utf-8", errors="replace")
            name = re.search(r"Information for (?:Rep\.|Sen\.)\s*([^<]+?)\s*<", text)
            district = re.search(r"District:.*?(\d+)", re.sub(r"<[^>]+>", " ", text), re.S)
            # every page lists every session ("89(R) - 2025") whether or not the member served in it, so service
            # comes from the journals: a member is kept only when a roll call prints their name
            if not (name and district):
                continue
            out.append({"chamber": chamber, "district": int(district[1]), "name": clean(name[1]), "list_name": clean(list_name),
                        "url": f"https://capitol.texas.gov/Members/MemberInfo.aspx?Leg=89&Chamber={code}&Code={mcode}"})
    return out


def fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().casefold()


def printed_match(printed: str, members: list[dict]) -> list[dict]:
    """Members a journal name can mean: "Bell, C." -> "Bell, Cecil"; "Garcia Hernandez" -> "Garcia Hernandez, Cassandra"."""
    printed = re.sub(r"\s*\(C\)$", "", printed)  # "(C)": the member was in the chair
    printed = re.sub(r"^([A-Z])\.\s*(.+)$", r"\2, \1.", printed)  # the Senate's "A. Hinojosa"
    last, _, initial = (re.sub(r"\s", "", fold(x)) for x in printed.partition(","))  # "GarciaHernandez" lost its space
    hits = []
    for m in members:
        mlast, _, first = (re.sub(r"\s", "", fold(x)) for x in m["list_name"].partition(","))
        if mlast == last and (not initial or first.startswith(initial.rstrip("."))):
            hits.append(m)
    return hits


def holder(root: Path, chamber: str, district: int, tlo_name: str) -> str | None:
    """Our profile for this member: the one seat holder whose surname appears in the TLO name."""
    room, title = ("house", "representative") if chamber == "house" else ("senate", "senator")
    seat = root / "data/states/tx/districts" / f"state-{room}-{district}" / "offices" / f"state-{title}.yaml"
    if not seat.exists():
        return None
    words = set(re.findall(r"[a-z]+", fold(tlo_name)))
    hits = []
    for h in yaml.safe_load(seat.read_text(encoding="utf-8"))["holders"]:
        person = root / "data/states/tx/people" / f"{h['person']}.yaml"
        name = yaml.safe_load(person.read_text(encoding="utf-8"))["name"] if person.exists() else ""
        surname = [w for w in re.findall(r"[a-z]+", fold(name)) if w not in {"jr", "sr", "ii", "iii", "iv"}][-1:]
        if surname and surname[0] in words:
            hits.append(h["person"])
    return hits[0] if len(hits) == 1 else None


# ---------------------------------------------------------------------------------------------------------------
# Bills.

def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def longdate(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


def journal_title(url: str, session: str, page: int | None) -> str:
    name = url.rsplit("/", 1)[1].upper()
    where = SESSIONS[session][1]
    if m := re.search(r"DAY(\d+)", name):
        n = int(m[1])
        suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
        title = f"Texas House Journal, {where}, {n}{suffix} Day"
    elif m := re.search(r"SJ(\d\d)-(\d\d)", name):
        title = f"Texas Senate Journal, {where}, {longdate(f'2025-{m[1]}-{m[2]}')}"
    else:
        title = f"Texas {'Senate' if 'SJ' in name else 'House'} Journal, {where}"
    return f"{title} (page {page})" if page else title


def base_fact(fid: str, statement: str, url: str, title: str, kind: str, sha: str | None, event: str,
              retrieved: str, headline: str) -> dict:
    return {"id": fid, "headline": headline, "statement": statement, "label": "official_record",
            "claim_status": "documented", "contradicted_by": [], "source_url": url, "source_title": title,
            "source_kind": kind, "sha256": sha, "archive_url": None, "event_date": event, "retrieved": retrieved,
            "verification": "unverified", "verified_on": None, "reviewer": None}


_PDF_CACHE: dict[Path, list[str]] = {}


def pdf_pages(path: Path, first: int, count: int = 3) -> str:
    import pypdf
    if path not in _PDF_CACHE:
        _PDF_CACHE.clear()  # one journal at a time is enough: bills cite the same day in a row
        _PDF_CACHE[path] = [p.extract_text() or "" for p in pypdf.PdfReader(path).pages]
    pages = _PDF_CACHE[path]
    lo = max(0, first - 1)
    return journal_text("\n".join(pages[lo:lo + count]))


def sentence_before(text: str, i: int) -> str:
    start = max(text.rfind(". ", 0, i), text.rfind(".\n", 0, i), text.rfind(":\n", 0, i))
    return " ".join(text[start + 1:i].split())


LAW_ACTIONS = {"Signed by the Governor": "Signed by the Governor",
               "Signed by the Governor/line item veto": "Signed by the Governor, line item veto",  # TLO's wording; it doesn't say how many
               "Filed without the Governor's signature": "Filed without the Governor's signature"}


def bill_doc(saved: Path, session: str, bill_code: str, became: str, retrieved: str, log: list[str]) -> dict | None:
    folder = SESSIONS[session][0]
    label = re.sub(r"(\D+)(\d+)", r"\1 \2", bill_code)
    rel = f"tx/statewide/legislature/{folder}/bills/{label.lower().replace(' ', '-')}"
    hist_url = f"https://capitol.texas.gov/BillLookup/History.aspx?LegSess={session}&Bill={bill_code}"
    h = history((saved / session / "history" / f"{bill_code}.html").read_text(encoding="utf-8", errors="replace"))
    if not h["caption"]:
        log.append(f"BY HAND {label}: no caption in the bill history")
        return None
    law_row = next((a for a in reversed(h["actions"]) if a["action"] in LAW_ACTIONS), None)
    if not law_row:
        log.append(f"BY HAND {label}: the bill history has no signing or filing action")
        return None
    hist_title = f"Texas Legislature Online: {label}, {SESSIONS[session][1]}, bill history"
    how = LAW_ACTIONS[law_row["action"]]
    law = base_fact(f"{rel}#L-01", f'The bill history records "{law_row["action"]}" on {longdate(law_row["date"])}.',
                    hist_url, hist_title, "page", None, law_row["date"], retrieved, f"{how} ({longdate(law_row['date'])})")
    votes = []
    for v in final_votes(h["actions"]):
        chamber = "house" if v["chamber"] == "H" else "senate"
        fid = f"{rel}#V-{len(votes) + 1:02d}"
        room = "House" if chamber == "house" else "Senate"
        entry = {"chamber": chamber, "action": v["what"], "date": v["date"], "record": v["record"], "tally": None,
                 "names": None, "every_member": False}
        if not v["record_vote"]:
            entry["fact"] = base_fact(fid, f'The bill history records "{v["action"]}" in the {room} on {longdate(v["date"])}, '
                                      "with no record vote.", hist_url, hist_title, "page", None, v["date"], retrieved,
                                      f"{room}: {v['what']} without a record vote ({longdate(v['date'])})")
            votes.append(entry)
            continue
        pdf = saved / session / "journals" / (v["journal"] or "").rsplit("/", 1)[-1].upper()
        if not v["journal"] or not pdf.exists() or not v["pdf_page"]:
            log.append(f"BY HAND {label}: {room} {v['what']} {v['date']}: journal not saved ({v['journal']})")
            continue
        # a roll call can start on the page before the one cited; a Senate vote can follow pages of remarks
        text = pdf_pages(pdf, v["pdf_page"] - 1, 4 if chamber == "house" else 12)
        if chamber == "house":
            rc = house_roll_call(text, v["record"]) if v["record"] else None
            if v["record"] and (not rc or rc.get("problem")):
                # a printed conference report can push the vote many pages past the page cited, or its names
                # past the window's last page; a record number appears once in a day's journal
                whole = pdf_pages(pdf, 1, 10 ** 6)
                if len(re.findall(loose(rf"\(Record {v['record']}\):"), whole)) == 1:
                    rc = house_roll_call(whole, v["record"])
            if v["record"] and (not rc or rc.get("block")):
                # a calendar's single roll call can be printed pages before the bill's own line
                rc = house_block_vote(pdf_pages(pdf, 1, 10 ** 6), v["record"], label)
                if rc and not rc["problem"]:
                    statement = f'The House Journal records a calendar of bills passed by one record vote, "{rc["base_quote"]}", '
                    statement += (f"and lists this bill on its own line, with no member's vote differing from that roll call."
                                  if rc["quote"] == label else
                                  f'and lists this bill with the members whose votes differ from it and its own tally: "{rc["quote"]}"')
            if not rc or rc["problem"]:
                log.append(f"BY HAND {label}: House Record {v['record']}: {rc['problem'] if rc else 'not found in the journal'}")
                continue
            if "base_quote" not in rc:
                statement = f'The House Journal records that "{rc["quote"]}"'
        else:
            rc = senate_roll_call(text, label, v["what"])
            if not rc and v["what"] == "passed":
                rc = senate_calendar_vote(pdf_pages(pdf, 1, 10 ** 6), label)
            if not rc or rc["problem"]:
                log.append(f"BY HAND {label}: Senate {v['what']} {v['date']}: {rc['problem'] if rc else 'vote not found on the journal page'}")
                continue
            statement = f'The Senate Journal records that "{rc["quote"]}"'
            names = rc["names"] or {}
            if rc["tally"]["yeas"] + rc["tally"]["nays"] == SENATE_SEATS and len(names.get("nays", [])) == rc["tally"]["nays"] \
                    and not names.get("yeas"):
                entry["every_member"] = True
                statement += f" With {SENATE_SEATS} seats in the Senate, every senator not listed as voting no voted yes."
        if any(o["fact"]["source_url"] == v["journal"] and o["fact"]["statement"] == statement for o in votes if "tally" in o and o["tally"]):
            # two actions read as the same printed vote (e.g. passed, reconsidered, and passed again that day)
            log.append(f"BY HAND {label}: {room} {v['what']} {v['date']}: reads as the same vote as an earlier action")
            continue
        entry["tally"], entry["names"] = rc["tally"], rc["names"] or None
        t = rc["tally"]
        entry["fact"] = base_fact(fid, statement, v["journal"], journal_title(v["journal"], session, v["page"]), "document",
                                  sha256(pdf), v["date"], retrieved, f"{room}: {v['what']}, {t['yeas']}-{t['nays']} ({longdate(v['date'])})")
        votes.append(entry)
    return {"session": session, "bill": label, "caption": h["caption"], "became_law": became, "law": law, "votes": votes}


def laws(saved: Path, session: str) -> list[tuple[str, str]]:
    out = {}
    for rep, became in (("signedbygov", "signed"), ("filedwogovsign", "filed_without_signature")):
        page = saved / f"{session}-{rep}.html"
        if page.exists():
            for b in re.findall(r"Bill=((?:HB|SB)\d+)", page.read_text(encoding="utf-8", errors="replace")):
                out.setdefault(b, became)
    return sorted(out.items(), key=lambda kv: (kv[0][:2], int(kv[0][2:])))


def plan(root: Path, saved: Path, session: str, retrieved: str) -> tuple[dict[Path, dict], list[str]]:
    folder = SESSIONS[session][0]
    out_dir = root / "data/states/tx/statewide/legislature" / folder
    log: list[str] = []
    bills = {}
    for code, became in laws(saved, session):
        if not (saved / session / "history" / f"{code}.html").exists():
            log.append(f"BY HAND {code}: bill history not saved")
            continue
        doc = bill_doc(saved, session, code, became, retrieved, log)
        if doc:
            bills[out_dir / "bills" / f"{doc['bill'].lower().replace(' ', '-')}.yaml"] = doc
    # Members: every printed name in these votes, matched to exactly one TLO member page.
    pages = member_pages(saved, session)
    roll = senate_roll(saved, session, retrieved, log) if any(v["every_member"] for d in bills.values() for v in d["votes"]) else None
    if not roll:
        for doc in bills.values():
            for v in doc["votes"]:
                v["every_member"] = False  # without the printed roll, only the named votes count
    printed = sorted({(v["chamber"], n) for doc in bills.values() for v in doc["votes"]
                      for names in (v["names"] or {}).values() for n in names}
                     | {("senate", n) for n in (roll or {}).get("names", [])})
    members: dict[str, dict] = {}
    for chamber, n in printed:
        if re.match(r"Mr\.\s*Speaker", n):
            log.append(f"SPEAKER {n!r}: shown on bills as printed; not linked to a member (the roll call doesn't name them)")
            continue
        hits = printed_match(n, [m for m in pages if m["chamber"] == chamber])
        if len(hits) != 1:
            log.append(f"UNMATCHED {chamber} {n!r}: {len(hits)} member pages match")
            continue
        members.setdefault(hits[0]["url"], {**hits[0], "printed": []})["printed"].append(n)
    rows = []
    for i, m in enumerate(sorted(members.values(), key=lambda m: (m["chamber"], m["district"])), 1):
        room = "House" if m["chamber"] == "house" else "Senate"
        row = {"chamber": m["chamber"], "district": m["district"], "name": m["name"], "printed": sorted(m["printed"]),
               "person": holder(root, m["chamber"], m["district"], m["name"]),
               "fact": base_fact(f"tx/statewide/legislature/{folder}/members#S-{i:03d}",
                                 f"Texas Legislature Online's member page lists {m['name']} as the member for "
                                 f"{room} District {m['district']}.",
                                 m["url"], f"Texas Legislature Online: {m['name']}, member information", "page", None,
                                 retrieved, retrieved, f"{room} District {m['district']}: {m['name']}")}
        rows.append(row)
        if not row["person"]:
            log.append(f"NO PROFILE {room} District {m['district']}: {m['name']} (no seat holder on file with that surname)")
    docs: dict[Path, dict] = dict(bills)
    docs[out_dir / "members.yaml"] = {"session": session, "members": rows, "senate_roll": roll}
    return docs, log


ROLL = loose("The roll was called and the following Senators were present: ") + r"(.*?)(?<![\s,][A-Z])\.(?=\s)"


def senate_roll(saved: Path, session: str, retrieved: str, log: list[str]) -> dict | None:
    """The senators every roll call in the session's Senate Journals lists (present plus absent), when every one
    of those roll calls lists the same 31; otherwise None, and tally-only votes name nobody."""
    folder = SESSIONS[session][0]
    urls = {}  # the journal links as the bill histories give them
    for f in (saved / session / "history").glob("*.html"):
        for a in history(f.read_text(encoding="utf-8", errors="replace"))["actions"]:
            if a["journal"]:
                urls[a["journal"].rsplit("/", 1)[-1].upper()] = a["journal"]
    seen, first, count = set(), None, 0
    for pdf in sorted((saved / session / "journals").glob("*SJ*.PDF")):
        text = pdf_pages(pdf, 1, 10 ** 6)
        for m in re.finditer(ROLL, text, re.S):
            names = [" ".join(x.split()) for x in m[1].split(",")]
            rest = text[m.end():m.end() + 600]
            while a := re.match(r"\s*(?:Absent-excused|Absent):\s*(.*?)(?<![\s,][A-Z])\.(?=\s)", rest, re.S):
                names += [" ".join(x.split()) for x in a[1].split(",")]
                rest = rest[a.end():]
            seen.add(frozenset(names))
            count += 1
            if first is None and pdf.name in urls and len(set(names)) == SENATE_SEATS:
                first = (pdf, m[0], sorted(set(names)))
    if len(seen) != 1 or not first or len(next(iter(seen))) != SENATE_SEATS:
        log.append(f"SENATE ROSTER: the session's roll calls list {sorted(len(s) for s in seen)} senators, not the same "
                   f"{SENATE_SEATS} each time; Senate votes printed only as a tally name nobody")
        return None
    pdf, quote, names = first
    mm, dd = re.search(r"SJ(\d\d)-(\d\d)", pdf.name).groups()
    date = f"2025-{mm}-{dd}"
    url = urls[pdf.name]
    quote = " ".join(quote.split())
    fact = base_fact(f"tx/statewide/legislature/{folder}/members#R-01",
                     f'The Senate Journal for {longdate(date)} records: "{quote}"', url,
                     journal_title(url, session, None), "document", sha256(pdf), date, retrieved,
                     f"Senate roll call ({longdate(date)})")
    fact["notes"] = (f"Each of the {count} roll calls printed in this session's Senate Journals lists these same "
                     f"{SENATE_SEATS} senators, as present or absent.")
    return {"names": names, "fact": fact}


def has_verified(doc) -> bool:
    if isinstance(doc, dict):
        return doc.get("verification") == "verified" or any(has_verified(v) for v in doc.values())
    if isinstance(doc, list):
        return any(has_verified(v) for v in doc)
    return False


def facts_in(doc):
    if isinstance(doc, dict):
        if "id" in doc and "source_url" in doc:
            yield doc
        for v in doc.values():
            yield from facts_in(v)
    elif isinstance(doc, list):
        for v in doc:
            yield from facts_in(v)


def keep_archives(old, new) -> None:
    """Carry each archive_url (filled in later by src/tools/archive.py) onto the new fact with the same ID,
    source, and file hash, so a re-run doesn't drop it."""
    saved = {f["id"]: f for f in facts_in(old) if f.get("archive_url")}
    for f in facts_in(new):
        o = saved.get(f["id"])
        if o and not f["archive_url"] and (o["source_url"], o.get("sha256")) == (f["source_url"], f.get("sha256")):
            f["archive_url"] = o["archive_url"]


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(REPO / "src" / "collectors"))
    from yamlio import to_yaml
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--saved", type=Path, required=True, help="folder with the saved TLO pages and journals")
    parser.add_argument("--session", choices=sorted(SESSIONS), required=True)
    parser.add_argument("--retrieved", required=True, type=dt.date.fromisoformat,
                        help="the date the pages in --saved were downloaded (YYYY-MM-DD), not the date of this run")
    parser.add_argument("--root", type=Path, default=REPO)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    docs, log = plan(args.root, args.saved, args.session, args.retrieved.isoformat())
    header = "# Texas Legislature (docs/METHODOLOGY.md, Votes in the Texas Legislature). Unverified; written by a collector.\n"
    changed = 0
    for path, doc in docs.items():
        old = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
        keep_archives(old, doc)
        text = header + to_yaml(doc)
        if path.exists():
            if path.read_text(encoding="utf-8") == text:
                continue
            if has_verified(old):
                log.append(f"KEPT {path.name}: it has verified facts; change it by hand")
                continue
        changed += 1
        if args.write:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    for line in log:
        print(line)
    members = next(d for p, d in docs.items() if p.name == "members.yaml")["members"]
    print(f"{len(docs) - 1} bills, {sum(len(d.get('votes', [])) for d in docs.values())} votes, {len(members)} members; "
          f"{changed} file(s) {'written' if args.write else 'would change'}. Everything is unverified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
