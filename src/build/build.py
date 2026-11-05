"""Build the static site: validate -> load -> render -> write.

Usage:
  python src/build/build.py                        # real data -> site/dist
  python src/build/build.py --root tests/fixtures/demo --out site/dist-demo --demo

Exits 1 without writing anything if the validator reports any error.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

import markdown
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src" / "validate"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate import validate  # noqa: E402
from model import load_site, load_yaml  # noqa: E402

SITE = REPO / "site"
MARKER = ".vtr-build"  # marks a folder this script created, so it may be replaced
FORM_PLACEHOLDER = re.compile(r"\[([^\]]+)\]\(CORRECTIONS_FORM_URL\)")
COMING_SOON = "Corrections form coming soon"


def load_config(path: Path) -> tuple[dict, list[str]]:
    config = load_yaml(path) or {}
    errors = []
    form = config.get("corrections_form_url")
    topics = config.get("topics") or {}
    schema_topics = json.loads((REPO / "schemas/other.schema.json").read_text(encoding="utf-8"))["$defs"]["topic"]["enum"]
    if topics and set(topics) != set(schema_topics):
        errors.append(f"{path.name}: topics must list exactly the topics in schemas/other.schema.json")
    if form is not None and not (isinstance(form, str) and form.startswith(("https://", "/")) and "{id}" in form):
        errors.append(f"{path.name}: corrections_form_url must be null, or an https:// URL or /path containing {{id}}")
    for state, links in (config.get("official_links") or {}).items():
        for link in links:
            if not str(link.get("url", "")).startswith("https://"):
                errors.append(f"{path.name}: official_links.{state}: '{link.get('name')}' needs an https:// url")
    for state, check in (config.get("registration_check") or {}).items():
        if not (check.get("name") and str(check.get("url", "")).startswith("https://")):
            errors.append(f"{path.name}: registration_check.{state} needs a name and an https:// url")
    return config, errors


def finance_filer(config: dict, race: dict) -> dict | None:
    """Where this race's candidates file campaign finance reports (site.yaml campaign_finance)."""
    cf = config.get("campaign_finance") or {}
    if race.get("office_level") == "federal":
        return dict(cf["federal"], law_title=None, law_url=None) if cf.get("federal") else None
    state = cf.get(race["state"])
    if not state:
        return None
    law = {"law_title": state.get("law_title"), "law_url": state.get("law_url")}
    if race.get("office_level") == "state" or race["office_type"] in (state.get("state_office_types") or []):
        return dict(state["state"], **law)
    local = (state.get("localities") or {}).get(race["locality"]) or {}
    if race["office_type"] in (local.get("office_types") or []):
        # published_on: when the county posts the reports but no page names the office that receives them.
        return {"name": local["name"], "url": local["url"], "published_on": local.get("published_on"), **law}
    return {"name": None, "url": None, **law}


def make_env(config: dict, demo: bool) -> Environment:
    env = Environment(
        loader=FileSystemLoader(SITE / "templates"),
        autoescape=select_autoescape(["html"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    form = config.get("corrections_form_url")

    def form_url(item_id: str = "") -> str | None:
        return form.replace("{id}", quote(item_id, safe="")) if form else None

    env.globals.update(config=config, demo=demo, form_url=form_url, coming_soon=COMING_SOON)
    env.filters["longdate"] = longdate
    env.filters["shortdate"] = lambda v: f"{dt.date.fromisoformat(v):%b} {dt.date.fromisoformat(v).day}"
    env.filters["weekday"] = lambda v: f"{dt.date.fromisoformat(v):%A}"
    env.filters["anchor"] = anchor
    env.filters["json_script"] = json_script
    env.globals.update(LABELS=LABELS, STATUSES=STATUSES, RECORD_TYPES=RECORD_TYPES,
                       PROMISE_STATUSES=PROMISE_STATUSES)
    return env


LABELS = {"official_record": "Official record", "news_report": "News report",
          "candidate_claim": "Candidate's claim", "organization_statement": "Organization's statement"}
STATUSES = {"documented": "Documented", "allegation": "Allegation", "disputed": "Disputed",
            "contradicted": "Contradicted"}
RECORD_TYPES = {"vote": "Vote", "sponsored": "Sponsored measure", "promise_kept": "Promise kept",
                "promise_broken": "Promise broken", "statement": "Statement"}
PROMISE_STATUSES = {"kept": "Kept", "broken": "Not kept", "opposite": "Did the opposite", "pending": "Still open"}


def anchor(fact_id: str) -> str:
    """Turn a path-based fact ID into a valid, stable HTML id."""
    return "f-" + re.sub(r"[^A-Za-z0-9_-]+", "-", fact_id).strip("-")


def json_script(value) -> Markup:
    """JSON safe to embed inside <script type="application/json">."""
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    for code in (0x3C, 0x3E, 0x26, 0x2028, 0x2029):  # < > & and JS line separators
        text = text.replace(chr(code), "\\u%04x" % code)
    return Markup(text)


def longdate(value: str) -> str:
    """'2026-10-05' -> 'Monday, October 5, 2026'."""
    d = dt.date.fromisoformat(value)
    return f"{d:%A}, {d:%B} {d.day}, {d.year}"


def render_methodology(config: dict) -> Markup:
    text = (REPO / "docs" / "METHODOLOGY.md").read_text(encoding="utf-8")
    form = config.get("corrections_form_url")
    if form:
        text = FORM_PLACEHOLDER.sub(lambda m: f"[{m.group(1)}]({form.replace('{id}', '')})", text)
    else:
        text = FORM_PLACEHOLDER.sub(COMING_SOON, text)
    html = markdown.markdown(text, extensions=["tables", "toc", "sane_lists"], output_format="html")
    # The page template supplies the <h1>; demote nothing, but drop the document's own title.
    html = re.sub(r"^<h1[^>]*>.*?</h1>\s*", "", html, count=1, flags=re.S)
    html = re.sub(r'<a href="(https?://[^"]+)"', r'<a href="\1" rel="noopener noreferrer"', html)
    return Markup(html)


def write_page(out: Path, url_path: str, html: str) -> None:
    target = out / url_path.strip("/") / "index.html" if url_path.strip("/") else out / "index.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html, encoding="utf-8")


def replace_dir(tmp: Path, out: Path) -> None:
    if out.exists():
        if not (out / MARKER).exists() and any(out.iterdir()):
            raise SystemExit(f"refusing to replace {out}: it was not created by this build")
        # Empty the folder rather than deleting it: on Windows an open terminal or
        # Explorer window (e.g. a preview server) locks the folder itself.
        for child in out.iterdir():
            shutil.rmtree(child) if child.is_dir() else child.unlink()
    out.mkdir(parents=True, exist_ok=True)
    for child in tmp.iterdir():
        shutil.move(str(child), str(out / child.name))
    tmp.rmdir()


def build(root: Path, out: Path, *, demo: bool = False, config_path: Path | None = None,
          today: str | None = None) -> int:
    report = validate(root, schemas_dir=REPO / "schemas")
    config, config_errors = load_config(config_path or SITE / "site.yaml")
    errors = report.errors + config_errors
    if errors:
        for line in errors:
            print(f"ERROR {line}", file=sys.stderr)
        print(f"Build stopped: {len(errors)} validation error(s). Nothing was written.", file=sys.stderr)
        return 1

    today = today or dt.date.today().isoformat()
    site = load_site(root, today)
    env = make_env(config, demo)
    env.globals["has_results"] = bool(site["results"])
    pages = {
        "/": env.get_template("home.html").render(site=site, page="home"),
        "/methodology/": env.get_template("methodology.html").render(
            body=render_methodology(config), page="methodology"),
        "/corrections/": env.get_template("corrections.html").render(site=site, page="corrections"),
        "/about/": env.get_template("about.html").render(page="about"),
        "/who-decides/": env.get_template("who_decides.html").render(site=site, page="who-decides"),
        "/report/": env.get_template("report.html").render(page="report"),
        "/report/thanks/": env.get_template("report_done.html").render(page="report", ok=True),
        "/report/error/": env.get_template("report_done.html").render(page="report", ok=False),
    }
    ballot_template = env.get_template("ballot.html")
    for ballot in site["ballots"]:
        ballot["payload"]["topicLabels"] = config.get("topics") or {}
        ballot["payload"]["formUrl"] = config.get("corrections_form_url")
        pages[ballot["url"]] = ballot_template.render(ballot=ballot, page="ballot")
    pages["/results/"] = env.get_template("results.html").render(site=site, page="results")
    results_template = env.get_template("results_ballot.html")
    for ballot in site["results"]:
        pages[ballot["url"]] = results_template.render(ballot=ballot, page="results")
    measure_template = env.get_template("measure.html")
    for measure in site["measures"]:
        pages[measure["url"]] = measure_template.render(measure=measure, page="measure")
    race_template = env.get_template("race.html")
    for race in site["races"]:
        race["finance"] = finance_filer(config, race)
        payload = dict(race["payload"], corrections_form_url=config.get("corrections_form_url"))
        pages[race["url"]] = race_template.render(race=race, payload=payload, page="race")

    officials = site["officials"]
    pages["/officials/"] = env.get_template("officials.html").render(officials=officials, page="officials")
    place_template, person_template = env.get_template("officials_place.html"), env.get_template("person.html")
    for place in officials["places"]:
        pages[place["url"]] = place_template.render(place=place, page="officials")
    for person in officials["people"]:
        pages[person["url"]] = person_template.render(person=person, facts=site["facts"], page="officials")
    tmp = Path(tempfile.mkdtemp(prefix="vtr-build-"))
    for url_path, html in pages.items():
        write_page(tmp, url_path, html)
    (tmp / "404.html").write_text(env.get_template("404.html").render(page="404"), encoding="utf-8")
    shutil.copytree(SITE / "static", tmp / "static", ignore=shutil.ignore_patterns("_headers"))
    for race in site["races"]:
        for cand in race["candidates"]:
            if cand.get("photo_path"):
                target = tmp / cand["photo_src"].lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(root / cand["photo_path"], target)
    for ballot in site["ballots"]:
        for src, url in ((ballot["shapes_src"], ballot["payload"]["shapesUrl"]),
                         (ballot["area_shapes_src"], ballot["payload"]["areaShapesUrl"])):
            if src:
                target = tmp / url.lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src, target)
    shutil.copy(SITE / "static" / "_headers", tmp / "_headers")
    (tmp / MARKER).write_text("Generated by src/build/build.py. Safe to delete.\n", encoding="utf-8")
    # Wrangler uploads everything in the assets folder except what this file lists.
    (tmp / ".assetsignore").write_text(f"{MARKER}\n", encoding="utf-8")
    replace_dir(tmp, out)
    print(f"Built {len(pages) + 1} pages into {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=REPO, help="data repo root (default: this repo)")
    parser.add_argument("--out", type=Path, default=SITE / "dist", help="output folder (default: site/dist)")
    parser.add_argument("--demo", action="store_true", help="mark every page as fictional demo data")
    parser.add_argument("--config", type=Path, default=None, help="site config (default: site/site.yaml)")
    parser.add_argument("--today", default=None, help="YYYY-MM-DD used to pick upcoming elections")
    args = parser.parse_args(argv)
    return build(args.root.resolve(), args.out.resolve(), demo=args.demo,
                 config_path=args.config, today=args.today)


if __name__ == "__main__":
    sys.exit(main())
