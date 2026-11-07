"""Validate every YAML file under data/, offices/, and corrections/.

Checks each file against its JSON Schema (schemas/), then runs cross-file rules
that a schema can't express: path-based IDs, references between facts, mappings
against surveys, and date ordering. See CLAUDE.md for the rules themselves.

Usage: python src/validate/validate.py [repo_root]
Exit code 0 when clean, 1 when any error is found.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

REPO_ROOT = Path(__file__).resolve().parents[2]

# Party names may not appear in survey text (rule 5). Matched as whole words.
PARTY_TERMS = re.compile(
    r"\b(democrat(ic)?s?|republicans?|libertarians?|green party|gop|dnc|rnc)\b",
    re.IGNORECASE,
)

# Which schema applies to a file, by its path relative to the repo root.
FILE_KINDS = [
    (re.compile(r"^offices/[^/]+/powers\.yaml$"), "powers"),
    (re.compile(r"^offices/[^/]+/survey\.yaml$"), "survey"),
    (re.compile(r"^data/states/[a-z]{2}/state\.yaml$"), "state"),
    (re.compile(r"^data/states/[a-z]{2}/office-overrides/[^/]+\.yaml$"), "office_override"),
    (re.compile(r"^data/states/[a-z]{2}/voter-essentials/\d{4}-\d{2}-\d{2}\.yaml$"), "voter_essentials"),
    (re.compile(r"^data/states/[a-z]{2}/localities/[^/]+/locality\.yaml$"), "locality"),
    (re.compile(r"^data/states/[a-z]{2}/localities/[^/]+/actions/\d{4}-\d{2}-\d{2}\.yaml$"), "actions"),
    (re.compile(r"^data/states/[a-z]{2}/localities/[^/]+/elections/\d{4}-\d{2}-\d{2}/ballot\.yaml$"), "ballot"),
    (re.compile(r"^data/states/[a-z]{2}/localities/[^/]+/elections/\d{4}-\d{2}-\d{2}/zips\.yaml$"), "zips"),
    (re.compile(r"^data/states/[a-z]{2}/localities/[^/]+/elections/\d{4}-\d{2}-\d{2}/precincts\.yaml$"), "precincts"),
    (re.compile(r"^data/states/[a-z]{2}/(statewide|districts/[^/]+|localities/[^/]+)/elections/\d{4}-\d{2}-\d{2}/[^/]+/race\.yaml$"), "race"),
    (re.compile(r"^data/states/[a-z]{2}/(statewide|districts/[^/]+|localities/[^/]+)/elections/\d{4}-\d{2}-\d{2}/[^/]+/measure\.yaml$"), "measure"),
    (re.compile(r"^data/states/[a-z]{2}/(statewide|districts/[^/]+|localities/[^/]+)/elections/\d{4}-\d{2}-\d{2}/[^/]+/result\.yaml$"), "result"),
    (re.compile(r"^data/states/[a-z]{2}/(statewide|districts/[^/]+|localities/[^/]+)/elections/\d{4}-\d{2}-\d{2}/[^/]+/candidates/[^/]+\.yaml$"), "candidate"),
    (re.compile(r"^data/states/[a-z]{2}/(statewide|districts/[^/]+|localities/[^/]+)/offices/[^/]+\.yaml$"), "office"),
    (re.compile(r"^data/states/[a-z]{2}/people/[^/]+\.yaml$"), "person"),
    (re.compile(r"^data/states/[a-z]{2}/statewide/legislature/[0-9a-z]+/bills/[^/]+\.yaml$"), "legislature_bill"),
    (re.compile(r"^data/states/[a-z]{2}/statewide/legislature/[0-9a-z]+/members\.yaml$"), "legislature_members"),
    (re.compile(r"^data/states/[a-z]{2}/decisions/[^/]+\.yaml$"), "decisions"),
    (re.compile(r"^data/states/[a-z]{2}/who-decides\.yaml$"), "who_decides"),
    (re.compile(r"^corrections/log\.yaml$"), "corrections"),
]


class _NoDateLoader(yaml.SafeLoader):
    """SafeLoader that keeps dates as strings so they match the schemas."""


_NoDateLoader.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in resolvers if tag != "tag:yaml.org,2002:timestamp"]
    for ch, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)

    def error(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")


def load_validators(schemas_dir: Path) -> dict[str, Draft202012Validator]:
    schemas = {}
    for schema_file in schemas_dir.glob("*.schema.json"):
        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        schemas[schema["$id"]] = schema
    registry = Registry().with_resources(
        (uri, Resource.from_contents(s)) for uri, s in schemas.items()
    )

    def for_ref(ref: str) -> Draft202012Validator:
        return Draft202012Validator({"$ref": ref}, registry=registry)

    return {
        "candidate": for_ref("urn:vtr:schema:candidate"),
        "person": for_ref("urn:vtr:schema:person"),
        **{
            kind: for_ref(f"urn:vtr:schema:other#/$defs/{kind}")
            for kind in ("race", "actions", "powers", "office_override", "survey",
                         "state", "locality", "corrections", "voter_essentials", "ballot", "zips", "precincts", "measure",
                         "result", "decisions", "who_decides", "office",
                         "legislature_bill", "legislature_members")
        },
    }


def classify(rel: str) -> str | None:
    for pattern, kind in FILE_KINDS:
        if pattern.match(rel):
            return kind
    return None


def id_prefix(rel: str) -> str:
    """data/states/tx/a/b.yaml -> tx/a/b"""
    return rel.removeprefix("data/states/").removesuffix(".yaml")


def race_parts(rel: str) -> dict:
    """data/states/tx/<scope>/elections/<date>/<slug>/race.yaml -> its parts.

    scope is "statewide", "districts/<district>", or "localities/<locality>". "place" is
    the URL segment (statewide, the district, or the locality); "area" is the default
    voting area (the state code, the district, or the locality).
    """
    parts = rel.split("/")
    state = parts[2]
    if parts[3] == "statewide":
        place, area, rest = "statewide", state, parts[4:]
    else:
        place = area = parts[4]
        rest = parts[5:]
    return {"state": state, "scope": parts[3], "place": place, "default_area": area,
            "election_date": rest[1], "slug": rest[2]}


def race_area(rel: str, doc: dict) -> str:
    return doc.get("area") or race_parts(rel)["default_area"]


def measure_level(rel: str, doc: dict) -> str:
    return doc.get("level") or ("state" if race_parts(rel)["scope"] == "statewide" else "local")


def iter_facts(kind: str, doc: dict):
    """Yield (section, fact) for every fact-like item that carries a path-based ID."""
    if kind in ("race", "ballot", "zips", "precincts", "measure", "result", "office"):
        for fact in doc.get("sources") or []:
            yield "sources", fact
        for fact in doc.get("explained") or []:
            yield "explained", fact
    elif kind == "actions":
        for fact in doc.get("facts") or []:
            yield "facts", fact
    elif kind == "voter_essentials":
        for item in doc.get("items") or []:
            yield "items", item
        for item in doc.get("at_the_polls") or []:
            yield "at_the_polls", item
    elif kind == "candidate":
        for section in ("summary", "records", "funding", "endorsements"):
            for fact in doc.get(section, []):
                yield section, fact
        for section, facts in (doc.get("running_on") or {}).items():
            for fact in facts or []:
                yield f"running_on.{section}", fact
    elif kind == "person":
        for section in ("summary", "records", "statements", "funding"):
            for fact in doc.get(section) or []:
                yield section, fact
    elif kind == "legislature_bill":
        if doc.get("law"):
            yield "law", doc["law"]
        for vote in doc.get("votes") or []:
            yield "votes", vote.get("fact") or {}
    elif kind == "legislature_members":
        for member in doc.get("members") or []:
            yield "members", member.get("fact") or {}
        if doc.get("senate_roll"):
            yield "senate_roll", doc["senate_roll"].get("fact") or {}


def find_party_keys(node, path: str = ""):
    """Yield paths of any key mentioning 'party' other than ballot_party."""
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if "party" in str(key).lower() and key != "ballot_party":
                yield here
            yield from find_party_keys(value, here)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from find_party_keys(item, f"{path}[{i}]")


def validate(root: Path, schemas_dir: Path | None = None) -> Report:
    """Validate the data repo at root. schemas_dir defaults to root/schemas, else this repo's."""
    report = Report()
    if schemas_dir is None:
        schemas_dir = root / "schemas" if (root / "schemas").is_dir() else REPO_ROOT / "schemas"
    validators = load_validators(schemas_dir)

    docs: dict[str, tuple[str, object]] = {}
    for base in ("data", "offices", "corrections"):
        for file in sorted((root / base).rglob("*.yaml")):
            rel = file.relative_to(root).as_posix()
            kind = classify(rel)
            if kind is None:
                report.error(rel, "unrecognized file location; see CLAUDE.md repository layout")
                continue
            try:
                doc = yaml.load(file.read_text(encoding="utf-8"), Loader=_NoDateLoader)
            except yaml.YAMLError as exc:
                report.error(rel, f"YAML parse error: {exc}")
                continue
            for err in sorted(validators[kind].iter_errors(doc), key=lambda e: list(e.absolute_path)):
                where = "/".join(str(p) for p in err.absolute_path) or "(root)"
                report.error(rel, f"{where}: {err.message}")
            docs[rel] = (kind, doc)

    for rel, (kind, doc) in docs.items():
        for key_path in find_party_keys(doc):
            report.error(rel, f"{key_path}: party may only appear in ballot_party (rule 5)")

    # Offices: powers and surveys.
    powers: dict[str, set[str]] = {}
    surveys: dict[str, dict] = {}
    judicial: set[str] = set()
    for rel, (kind, doc) in docs.items():
        if not isinstance(doc, dict):
            continue
        office = rel.split("/")[1] if rel.startswith("offices/") else None
        if kind in ("powers", "survey") and doc.get("office_type") != office:
            report.error(rel, f"office_type must be '{office}' to match its folder")
        if kind == "powers" and doc.get("judicial"):
            judicial.add(office)
        if kind == "powers":
            # Merge, don't assign: state overrides (under data/) may already be loaded.
            powers.setdefault(office, set()).update(
                p.get("id") for p in doc.get("powers") or [] if isinstance(p, dict)
            )
        elif kind == "survey":
            surveys[office] = doc
        elif kind == "office_override":
            powers.setdefault(doc.get("office_type"), set()).update(
                p.get("id") for p in doc.get("powers") or [] if isinstance(p, dict)
            )

    # Plain-language summaries may only summarize powers that exist (rule 3).
    for rel, (kind, doc) in docs.items():
        if kind not in ("powers", "office_override") or not isinstance(doc, dict):
            continue
        summary = doc.get("plain_summary") or {}
        office = doc.get("office_type")
        for line in [summary.get("intro") or {}, *(summary.get("points") or [])]:
            for pid in line.get("powers") or []:
                if pid not in powers.get(office, set()):
                    report.error(rel, f"plain_summary: power {pid} does not exist for office '{office}'")
        # The intro shows on every race of this office type, so it may not rest on a power limited to some places.
        limited = {p.get("id") for p in doc.get("powers") or [] if isinstance(p, dict) and p.get("only_in")}
        for pid in (summary.get("intro") or {}).get("powers") or []:
            if pid in limited:
                report.error(rel, f"plain_summary: intro cites {pid}, which applies only in some places (only_in)")

    for office, survey in surveys.items():
        rel = f"offices/{office}/survey.yaml"
        if office in judicial and survey.get("scenarios"):
            report.error(rel, "judicial offices have no quiz: judges can't promise how they would rule")
        tags = set(survey.get("tags") or [])
        seen = set()
        for scenario in survey.get("scenarios") or []:
            if not isinstance(scenario, dict):
                continue
            sid = scenario.get("id")
            if sid in seen:
                report.error(rel, f"duplicate scenario id {sid}")
            seen.add(sid)
            for pid in scenario.get("powers") or []:
                if pid not in powers.get(office, set()):
                    report.error(rel, f"{sid}: power {pid} is not in offices/{office}/powers.yaml (rule 4)")
            texts = [scenario.get("scenario", "")]
            for option in scenario.get("options") or []:
                texts.append(option.get("text", ""))
                for tag in option.get("tags") or []:
                    if tag not in tags:
                        report.error(rel, f"{sid}/{option.get('id')}: tag '{tag}' is not in the survey's tag list")
                if PARTY_TERMS.search(" ".join(option.get("tags") or [])):
                    report.error(rel, f"{sid}/{option.get('id')}: tags may not name a party (rule 5)")
            for text in texts:
                if PARTY_TERMS.search(text or ""):
                    report.error(rel, f"{sid}: scenario text mentions a party (rule 5)")

    # Ballots: contests exist, match the election, and can be ordered; ZIPs reach every area.
    levels = {rel.split("/")[1]: doc.get("level") for rel, (kind, doc) in docs.items()
              if kind == "powers" and isinstance(doc, dict)}
    for rel, (kind, doc) in docs.items():
        if kind == "measure" and f"{rel.rsplit('/', 1)[0]}/race.yaml" in docs:
            report.error(rel, "a contest folder holds either race.yaml or measure.yaml, not both")
    urls: dict[str, str] = {}
    for rel, (kind, doc) in docs.items():
        if kind == "race" and isinstance(doc, dict):
            parts = race_parts(rel)
            url = f"{parts['state']}/{parts['place']}/{parts['election_date']}/{parts['slug']}"
            if url in urls:
                report.error(rel, f"race page URL clashes with {urls[url]}")
            urls[url] = rel
    ballot_areas: dict[str, set[str]] = {}
    unmapped_areas: dict[str, set[str]] = {}
    for rel, (kind, doc) in docs.items():
        if kind != "ballot" or not isinstance(doc, dict):
            continue
        folder_date = rel.split("/")[-2]
        if doc.get("election_date") != folder_date:
            report.error(rel, f"election_date must be {folder_date} to match its folder")
        seen, areas = set(), set()
        for contest in doc.get("contests") or []:
            if contest in seen:
                report.error(rel, f"contest '{contest}' is listed twice")
            seen.add(contest)
            race_rel = f"data/states/{contest}/race.yaml"
            race_kind, race = docs.get(race_rel, (None, None))
            if race_kind != "race":
                race_rel = f"data/states/{contest}/measure.yaml"
                race_kind, race = docs.get(race_rel, (None, None))
            if race_kind not in ("race", "measure") or not isinstance(race, dict):
                report.error(rel, f"contest '{contest}' has no race.yaml or measure.yaml")
                continue
            if race.get("election_date") != doc.get("election_date"):
                report.error(rel, f"contest '{contest}' is for a different election")
            if race_kind == "race" and not (race.get("level") or levels.get(race.get("office_type"))):
                report.error(rel, f"contest '{contest}': no level (set it in offices/{race.get('office_type')}/powers.yaml)")
            areas.add(race_area(race_rel, race))
        for area in doc.get("unmapped") or []:
            if area not in areas:
                report.error(rel, f"unmapped: area '{area}' is not the area of any contest on the ballot")
        ballot_areas[rel.rsplit("/", 1)[0]] = areas
        unmapped_areas[rel.rsplit("/", 1)[0]] = set(doc.get("unmapped") or [])
    for rel, (kind, doc) in docs.items():
        if kind not in ("zips", "precincts") or not isinstance(doc, dict):
            continue
        folder = rel.rsplit("/", 1)[0]
        noun = "ZIP code" if kind == "zips" else "precinct"
        if folder not in ballot_areas:
            report.error(rel, f"{kind}.yaml needs a ballot.yaml in the same folder")
            continue
        state, locality = folder.split("/")[2], folder.split("/")[4]
        implied = {state, locality}
        everywhere = set(doc.get("everywhere") or [])
        for area in sorted(everywhere):
            if area in implied:
                report.error(rel, f"everywhere: '{area}' is already implied; leave it out")
            elif area not in ballot_areas[folder]:
                report.error(rel, f"everywhere: area '{area}' is not the area of any contest on the ballot")
        used = set(everywhere)
        for key, entries in (doc.get(kind) or {}).items():
            flat = [a for e in entries or [] for a in (e if isinstance(e, list) else [e])]
            if len(flat) != len(set(flat)):
                report.error(rel, f"{key}: an area is listed more than once")
            for area in flat:
                used.add(area)
                if area in implied or area in everywhere:
                    report.error(rel, f"{key}: '{area}' is implied for every {noun}; leave it out")
                elif area not in ballot_areas[folder]:
                    report.error(rel, f"{key}: area '{area}' is not the area of any contest on the ballot")
                elif area in unmapped_areas[folder]:
                    report.error(rel, f"{key}: area '{area}' is listed as unmapped in ballot.yaml")
        for area in sorted(ballot_areas[folder] - implied - used - unmapped_areas[folder]):
            report.error(rel, f"area '{area}' is on the ballot but no {noun} reaches it")
        shapes_file = root / folder / "precinct-shapes.json"
        if kind == "precincts" and shapes_file.is_file():
            shapes_rel = f"{folder}/precinct-shapes.json"
            try:
                shapes = json.loads(shapes_file.read_text(encoding="utf-8"))
            except ValueError as exc:
                report.error(shapes_rel, f"not valid JSON: {exc}")
                continue
            if shapes.get("source") not in {s.get("id") for s in doc.get("sources") or []}:
                report.error(shapes_rel, "source must be the ID of one of precincts.yaml's sources")
            outlines, listed = set(shapes.get("precincts") or {}), set(doc.get("precincts") or {})
            for key in sorted(listed - outlines, key=int)[:5]:
                report.error(shapes_rel, f"precinct {key} has no outline")
            for key in sorted(outlines - listed, key=int)[:5]:
                report.error(shapes_rel, f"precinct {key} has an outline but is not in precincts.yaml")
        area_file = root / folder / "area-shapes.json"
        if kind == "precincts" and area_file.is_file():
            area_rel = f"{folder}/area-shapes.json"
            try:
                area_shapes = json.loads(area_file.read_text(encoding="utf-8"))
            except ValueError as exc:
                report.error(area_rel, f"not valid JSON: {exc}")
                continue
            if area_shapes.get("source") not in {s.get("id") for s in doc.get("sources") or []}:
                report.error(area_rel, "source must be the ID of one of precincts.yaml's sources")
            for area in sorted(area_shapes.get("areas") or {}):
                if area not in ballot_areas[folder]:
                    report.error(area_rel, f"area '{area}' is not the area of any contest on the ballot")

    # Facts: path-based IDs, uniqueness, date order.
    all_ids: dict[str, str] = {}
    for rel, (kind, doc) in docs.items():
        if not isinstance(doc, dict):
            continue
        prefix = id_prefix(rel)
        for section, fact in iter_facts(kind, doc):
            if not isinstance(fact, dict):
                continue
            fid = fact.get("id", "")
            if not str(fid).startswith(prefix + "#"):
                report.error(rel, f"{section}: id '{fid}' must start with '{prefix}#' (path-based IDs)")
            if fid in all_ids:
                report.error(rel, f"{section}: duplicate id '{fid}' (also in {all_ids[fid]})")
            all_ids[fid] = rel
            if fact.get("verification") == "verified":
                retrieved, verified_on = fact.get("retrieved"), fact.get("verified_on")
                if isinstance(retrieved, str) and isinstance(verified_on, str) and verified_on < retrieved:
                    report.error(rel, f"{fid}: verified_on {verified_on} is earlier than retrieved {retrieved}")

    # References between facts, mappings, and surveys.
    for rel, (kind, doc) in docs.items():
        if not isinstance(doc, dict):
            continue
        for section, fact in iter_facts(kind, doc):
            if not isinstance(fact, dict):
                continue
            for ref in fact.get("contradicted_by") or []:
                if ref not in all_ids:
                    report.error(rel, f"{fact.get('id')}: contradicted_by '{ref}' does not exist")
            for ref in fact.get("followups") or []:
                if ref not in all_ids:
                    report.error(rel, f"{fact.get('id')}: followup '{ref}' does not exist")
        if kind != "candidate":
            continue

        race_rel = rel.rsplit("/candidates/", 1)[0] + "/race.yaml"
        race_kind, race = docs.get(race_rel, (None, None))
        office = race.get("office_type") if isinstance(race, dict) else None
        if race_kind != "race":
            report.error(rel, f"missing {race_rel}")
        record_ids = {r.get("id") for r in doc.get("records") or [] if isinstance(r, dict)}

        photo = doc.get("photo")
        if isinstance(photo, dict) and photo.get("file"):
            if not (root / rel).parent.joinpath(photo["file"]).is_file():
                report.error(rel, f"photo: file '{photo['file']}' not found next to the candidate file")

        for i, mapping in enumerate(doc.get("mappings") or []):
            if not isinstance(mapping, dict):
                continue
            where = f"mappings[{i}]"
            if mapping.get("record") not in record_ids:
                report.error(rel, f"{where}: record '{mapping.get('record')}' is not in this candidate's records")
            question = str(mapping.get("question", ""))
            q_office, _, q_id = question.partition("/")
            if office and q_office != office:
                report.error(rel, f"{where}: question '{question}' is not for this race's office '{office}'")
                continue
            scenario = next(
                (s for s in (surveys.get(q_office) or {}).get("scenarios") or []
                 if isinstance(s, dict) and s.get("id") == q_id),
                None,
            )
            if scenario is None:
                report.error(rel, f"{where}: question '{question}' does not exist")
                continue
            option_ids = {o.get("id") for o in scenario.get("options") or []}
            for option in mapping.get("options") or []:
                if option not in option_ids:
                    report.error(rel, f"{where}: option '{option}' is not in {question}")

        if doc.get("promise_tracker") and doc.get("incumbent") is not True:
            report.error(rel, "promise_tracker: only incumbents have a promise tracker (for now)")
        for i, entry in enumerate(doc.get("promise_tracker") or []):
            if not isinstance(entry, dict):
                continue
            for ref in [entry.get("promise"), *(entry.get("evidence") or [])]:
                if ref not in all_ids:
                    report.error(rel, f"promise_tracker[{i}]: '{ref}' does not exist")

    check_decisions_and_issues(root, docs, powers, all_ids, report)
    check_results(docs, report)
    check_officials(root, docs, powers, all_ids, report)
    check_legislature(docs, report)
    return report


def check_legislature(docs: dict, report: Report) -> None:
    """Bills and members (docs/METHODOLOGY.md, Votes in the Texas Legislature): names add up to the tally, and no
    printed name belongs to more than one member of that chamber in that session."""
    printed: dict[tuple[str, str], dict[str, list[str]]] = {}
    rolls: dict[str, list[str]] = {}
    for rel, (kind, doc) in docs.items():
        if kind != "legislature_members" or not isinstance(doc, dict):
            continue
        state, folder = rel.split("/")[2], rel.split("/")[5]
        if doc.get("session", "").lower() != folder:
            report.error(rel, f"session must be '{folder.upper()}' to match its folder")
        roll = (doc.get("senate_roll") or {}).get("names")
        if roll is not None:
            rolls[folder] = roll
            if len(set(roll)) != 31:
                report.error(rel, f"senate_roll: {len(set(roll))} different names, not one for each of the 31 seats")
        for i, m in enumerate(doc.get("members") or []):
            if m.get("person") and docs.get(f"data/states/{state}/people/{m['person']}.yaml", (None,))[0] != "person":
                report.error(rel, f"members[{i}]: no person file for '{m['person']}'")
            for name in m.get("printed") or []:
                printed.setdefault((folder, m.get("chamber")), {}).setdefault(name, []).append(m.get("name"))
    for (folder, chamber), names in printed.items():
        for name, who in names.items():
            if len(who) > 1:
                report.error(f"data/states/tx/statewide/legislature/{folder}/members.yaml",
                             f"{chamber} name '{name}' is printed for more than one member: {who}")
    for rel, (kind, doc) in docs.items():
        if kind != "legislature_bill" or not isinstance(doc, dict):
            continue
        folder = rel.split("/")[5]
        if doc.get("session", "").lower() != folder:
            report.error(rel, f"session must be '{folder.upper()}' to match its folder")
        if Path(rel).stem != doc.get("bill", "").lower().replace(" ", "-"):
            report.error(rel, f"file name must be '{doc.get('bill', '').lower().replace(' ', '-')}.yaml'")
        for i, v in enumerate(doc.get("votes") or []):
            tally, names = v.get("tally") or {}, v.get("names") or {}
            for key in ("yeas", "nays", "present_not_voting"):
                if names.get(key) is not None and len(names[key]) != tally.get(key, 0):
                    report.error(rel, f"votes[{i}]: {len(names[key])} {key} names for a tally of {tally.get(key, 0)}")
            if v.get("every_member") and (v.get("chamber") != "senate" or tally.get("yeas", 0) + tally.get("nays", 0) != 31
                                          or len(names.get("nays") or []) != tally.get("nays", 0)):
                report.error(rel, f"votes[{i}]: every_member needs a Senate tally of all 31 seats with every nay named")
            elif v.get("every_member"):
                if folder not in rolls:
                    report.error(rel, f"votes[{i}]: every_member needs the session's senate_roll in members.yaml")
                elif set(names.get("nays") or []) - set(rolls[folder]):
                    report.error(rel, f"votes[{i}]: a nay isn't on the session's senate_roll: "
                                      f"{sorted(set(names['nays']) - set(rolls[folder]))}")
            for key, listed in names.items():
                if len(set(listed)) != len(listed):
                    report.error(rel, f"votes[{i}]: a name is listed twice under {key}")


def check_officials(root: Path, docs: dict, powers: dict, all_ids: dict, report: Report) -> None:
    """Seats and people (docs/OFFICIALS.md): every link between them, and to candidacies, exists."""
    for rel, (kind, doc) in docs.items():
        if not isinstance(doc, dict):
            continue
        state = rel.split("/")[2]
        if kind == "person":
            if doc.get("id") != Path(rel).stem:
                report.error(rel, f"id must be '{Path(rel).stem}' to match the file name")
            for cand in doc.get("candidacies") or []:
                if docs.get(f"data/states/{cand}.yaml", (None,))[0] != "candidate":
                    report.error(rel, f"candidacy '{cand}' is not a candidate file")
                elif not cand.startswith(f"{state}/"):
                    report.error(rel, f"candidacy '{cand}' is in another state")
            photo = doc.get("photo")
            if isinstance(photo, dict) and photo.get("file") and not (root / rel).parent.joinpath(photo["file"]).is_file():
                report.error(rel, f"photo: file '{photo['file']}' not found next to the person file")
            for i, entry in enumerate(doc.get("promise_tracker") or []):
                if isinstance(entry, dict):
                    for ref in [entry.get("promise"), *(entry.get("evidence") or [])]:
                        if ref not in all_ids:
                            report.error(rel, f"promise_tracker[{i}]: '{ref}' does not exist")
        elif kind == "office":
            if doc.get("office_type") not in powers:
                report.error(rel, f"office_type '{doc.get('office_type')}' has no offices/<type>/powers.yaml")
            own = {s.get("id") for s in doc.get("sources") or [] if isinstance(s, dict)}
            holders = [h for h in doc.get("holders") or [] if isinstance(h, dict)]
            if doc.get("vacant") and any(h.get("end") is None for h in holders):
                report.error(rel, "vacant: a seat marked vacant can't have a holder with no end date")
            for i, h in enumerate(holders):
                if docs.get(f"data/states/{state}/people/{h.get('person')}.yaml", (None,))[0] != "person":
                    report.error(rel, f"holders[{i}]: no person file data/states/{state}/people/{h.get('person')}.yaml")
                if h.get("basis") not in own:
                    report.error(rel, f"holders[{i}]: basis '{h.get('basis')}' is not one of this file's sources")
                if h.get("start") and h.get("end") and h["end"] < h["start"]:
                    report.error(rel, f"holders[{i}]: end {h['end']} is before start {h['start']}")


def check_results(docs: dict, report: Report) -> None:
    """result.yaml: sits beside a race or measure, counts exactly its candidates or choices, and names a sane outcome."""
    for rel, (kind, doc) in docs.items():
        if kind != "result" or not isinstance(doc, dict):
            continue
        folder = rel.rsplit("/", 1)[0]
        contest_kind, contest = next(((k, d) for k, d in (docs.get(f"{folder}/race.yaml", (None, None)),
                                                          docs.get(f"{folder}/measure.yaml", (None, None)))
                                      if k in ("race", "measure")), (None, None))
        if not isinstance(contest, dict):
            report.error(rel, "result.yaml needs a race.yaml or measure.yaml in the same folder")
            continue
        if doc.get("election_date") != contest.get("election_date"):
            report.error(rel, f"election_date must be {contest.get('election_date')} to match the contest")
        if str(doc.get("as_of", "")) < str(doc.get("election_date", "")):
            report.error(rel, "as_of may not be earlier than the election")
        counts = set(doc.get("counts") or {})
        outcome = doc.get("outcome") or {}
        named = set(outcome.get("candidates") or [])
        if contest_kind == "measure":
            if counts != set(contest.get("choices") or []):
                report.error(rel, f"counts must list exactly the measure's choices {contest.get('choices')}")
            if outcome and (outcome.get("result") not in ("passed", "failed") or named):
                report.error(rel, "outcome: a measure is passed or failed, with no candidates")
            continue
        cands = {d.get("id"): d for r, (k, d) in docs.items()
                 if k == "candidate" and r.startswith(folder + "/candidates/") and isinstance(d, dict)}
        for cid in sorted(counts - set(cands)):
            report.error(rel, f"counts: '{cid}' is not a candidate in this race")
        for cid in sorted(c for c in set(cands) - counts if not cands[c].get("write_in")):
            report.error(rel, f"counts: candidate '{cid}' is missing")
        if outcome:
            if outcome.get("result") not in ("elected", "runoff"):
                report.error(rel, "outcome: a race is elected or runoff")
            if not named or named - counts:
                report.error(rel, "outcome: candidates must name who won or advanced, from counts")
            if outcome.get("result") == "elected" and len(named) > (contest.get("seats") or 1):
                report.error(rel, f"outcome: more candidates elected than the race's {contest.get('seats') or 1} seat(s)")


def check_decisions_and_issues(root: Path, docs: dict, powers: dict, all_ids: dict, report: Report) -> None:
    """Big decisions and the who-decides guide: path-based IDs, real powers, facts, and offices; no party talk."""
    offices = {p.name for p in (root / "offices").iterdir() if p.is_dir()} if (root / "offices").is_dir() else set()
    decision_ids: set[str] = set()
    for rel, (kind, doc) in docs.items():
        if kind != "decisions" or not isinstance(doc, dict):
            continue
        state, office = rel.split("/")[2], rel.rsplit("/", 1)[1].removesuffix(".yaml")
        if doc.get("office_type") != office:
            report.error(rel, f"office_type must be '{office}' to match the file name")
        if str(doc.get("state", "")).lower() != state:
            report.error(rel, f"state must be '{state.upper()}' to match its folder")
        for d in doc.get("decisions") or []:
            if not isinstance(d, dict):
                continue
            did = d.get("id", "")
            if not str(did).startswith(id_prefix(rel) + "#"):
                report.error(rel, f"decisions: id '{did}' must start with '{id_prefix(rel)}#' (path-based IDs)")
            if did in decision_ids or did in all_ids:
                report.error(rel, f"decisions: duplicate id '{did}'")
            decision_ids.add(did)
            for pid in d.get("powers") or []:
                if pid not in powers.get(office, set()):
                    report.error(rel, f"{did}: power {pid} does not exist for office '{office}'")
            for ref in d.get("facts") or []:
                if ref not in all_ids:
                    report.error(rel, f"{did}: fact '{ref}' does not exist")
            loc = d.get("locality")
            if loc and not any((root / "data/states" / state / k / loc).is_dir() for k in ("localities", "districts")):
                report.error(rel, f"{did}: locality '{loc}' has no folder under data/states/{state}/")
            for key in ("title", "what_happened", "how_it_affects_you"):
                if PARTY_TERMS.search(d.get(key) or ""):
                    report.error(rel, f"{did}: {key} mentions a party (rule 5)")
    issue_ids: set[str] = set()
    for rel, (kind, doc) in docs.items():
        if kind != "who_decides" or not isinstance(doc, dict):
            continue
        state = rel.split("/")[2]
        if str(doc.get("state", "")).lower() != state:
            report.error(rel, f"state must be '{state.upper()}' to match its folder")
        for issue in doc.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            iid = issue.get("id", "")
            if not str(iid).startswith(id_prefix(rel) + "#"):
                report.error(rel, f"issues: id '{iid}' must start with '{id_prefix(rel)}#' (path-based IDs)")
            if iid in issue_ids:
                report.error(rel, f"issues: duplicate id '{iid}'")
            issue_ids.add(iid)
            texts = [issue.get("question"), issue.get("short_answer")]
            for decider in issue.get("deciders") or []:
                texts.append(decider.get("role"))
                for office in decider.get("office_types") or []:
                    if office not in offices:
                        report.error(rel, f"{iid}: office type '{office}' has no folder under offices/")
            for ref in issue.get("decisions") or []:
                if ref not in decision_ids:
                    report.error(rel, f"{iid}: decision '{ref}' does not exist")
            if any(PARTY_TERMS.search(t or "") for t in texts):
                report.error(rel, f"{iid}: text mentions a party (rule 5)")


def main(argv: list[str]) -> int:
    root = Path(argv[1]).resolve() if len(argv) > 1 else REPO_ROOT
    report = validate(root)
    for line in report.errors:
        print(f"ERROR {line}")
    print(f"{len(report.errors)} error(s)")
    return 1 if report.errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
