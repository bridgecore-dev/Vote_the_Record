"""Stage 1 site build tests: generator, validator gate, pages, headers, CSP hygiene."""

import json
import shutil
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "build"))
from build import build  # noqa: E402

TODAY = "2026-09-28"
ESS_REL = "data/states/tx/voter-essentials/2026-11-03.yaml"
ESS_PREFIX = "tx/voter-essentials/2026-11-03"


def essential(local_id, name, **fields):
    item = {
        "id": f"{ESS_PREFIX}#{local_id}", "name": name,
        "source_url": "https://example.gov/dates", "source_title": "Important dates",
        "source_kind": "page", "sha256": None, "archive_url": None, "retrieved": "2026-09-28",
        "verification": "unverified", "verified_on": None, "reviewer": None,
    }
    item.update(fields)
    return item


ESSENTIALS = {
    "state": "TX", "election_date": "2026-11-03",
    "items": [
        essential("election-day", "Election Day", date="2026-11-03"),
        essential("registration-deadline", "Last day to register to vote", date="2026-10-05"),
        essential("early-voting", "Early voting in person", start="2026-10-19", end="2026-10-30"),
    ],
}


def make_repo(tmp_path, files):
    root = tmp_path / "repo"
    for rel, doc in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    for base in ("data", "offices", "corrections"):
        (root / base).mkdir(parents=True, exist_ok=True)
    return root


def make_config(tmp_path, **overrides):
    config = yaml.safe_load((REPO / "site" / "site.yaml").read_text(encoding="utf-8"))
    config.update(overrides)
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def read(out, page):
    return (out / page).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def real_site(tmp_path_factory):
    out = tmp_path_factory.mktemp("real") / "dist"
    assert build(REPO, out, today=TODAY) == 0
    return out


def test_real_repo_builds_all_stage1_pages(real_site):
    for page in ("index.html", "about/index.html", "methodology/index.html",
                 "corrections/index.html", "404.html"):
        assert (real_site / page).is_file(), page


def test_home_has_beta_banner_official_links_and_race_list(real_site):
    home = read(real_site, "index.html")
    assert "<strong>Beta.</strong>" in home
    assert 'href="https://www.votetexas.gov/"' in home
    assert 'href="https://goelect.txelections.civixapps.com/ivis-mvp-ui/#/login"' in home
    assert "Check your voter registration" in home
    assert 'href="/ballot/tx/harris-county/2026-11-03/"' in home
    ballot = read(real_site, "ballot/tx/harris-county/2026-11-03/index.html")
    assert 'href="/races/tx/harris-county/2026-11-03/county-judge/"' in ballot


def test_empty_repo_shows_no_races_state(tmp_path):
    out = tmp_path / "dist"
    assert build(make_repo(tmp_path, {}), out, today=TODAY) == 0
    assert "No races published yet" in read(out, "index.html")


def test_beta_banner_is_sitewide(real_site):
    for page in ("about/index.html", "methodology/index.html", "corrections/index.html"):
        assert "<strong>Beta.</strong>" in read(real_site, page), page


def test_headers_file_has_csp_and_privacy_headers(real_site):
    headers = real_site / "_headers"
    assert headers.is_file()
    text = headers.read_text(encoding="utf-8")
    assert ("Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self'; "
            "font-src 'self'; img-src 'self'; connect-src 'self'; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'") in text
    assert "Referrer-Policy: no-referrer" in text
    assert "Permissions-Policy:" in text


def test_methodology_renders_markdown_tables_without_placeholder(real_site):
    page = read(real_site, "methodology/index.html")
    assert page.count("<table>") >= 3
    assert "CORRECTIONS_FORM_URL" not in page
    assert page.count("<h1") == 1


def test_build_fails_and_writes_nothing_when_validator_fails(tmp_path):
    bad = dict(ESSENTIALS, items=[essential("x", "Bad", date="2026-10-05", verification="verified",
                                            verified_on="2026-09-28", reviewer="owner")])
    root = make_repo(tmp_path, {ESS_REL: bad})  # verified without archive_url
    out = tmp_path / "dist"
    assert build(root, out, today=TODAY) == 1
    assert not out.exists()


def test_cli_exits_nonzero_on_validation_error(tmp_path):
    bad = dict(ESSENTIALS, items=[essential("x", "Bad", date="2026-10-05", start="2026-10-01")])
    root = make_repo(tmp_path, {ESS_REL: bad})
    result = subprocess.run(
        [sys.executable, str(REPO / "src" / "build" / "build.py"), "--root", str(root),
         "--out", str(tmp_path / "dist"), "--today", TODAY],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "Nothing was written" in result.stderr
    assert not (tmp_path / "dist").exists()


def test_bad_corrections_form_config_fails(tmp_path):
    root = make_repo(tmp_path, {})
    config = make_config(tmp_path, corrections_form_url="http://example.org/form")
    assert build(root, tmp_path / "dist", config_path=config, today=TODAY) == 1


def test_voter_essentials_render_sorted_with_badges_and_sources(tmp_path):
    root = make_repo(tmp_path, {ESS_REL: ESSENTIALS})
    out = tmp_path / "dist"
    assert build(root, out, today=TODAY) == 0
    home = read(out, "index.html")
    grid = home[home.index('class="date-grid"'):home.index("</ul>", home.index('class="date-grid"'))]
    reg, early, eday = grid.index("Last day to register"), grid.index("Early voting in person"), grid.index("Election Day")
    assert reg < early < eday  # sorted by date
    assert '<span class="date-big">Oct 5</span>' in grid
    assert '<span class="date-big">Oct 19–30</span>' in grid
    assert "Monday" in grid
    assert home.count('class="badge badge-unverified"') >= 3 + 1  # items + banner
    assert home.count('href="https://example.gov/dates"') == 3
    assert 'href="/report/?item=tx%2Fvoter-essentials%2F2026-11-03%23election-day"' in home


def test_past_elections_are_not_listed(tmp_path):
    root = make_repo(tmp_path, {ESS_REL: ESSENTIALS})
    out = tmp_path / "dist"
    assert build(root, out, today="2026-11-04") == 0
    assert "Last day to register" not in read(out, "index.html")


def test_unconfigured_form_renders_plain_text_not_links(tmp_path):
    root = make_repo(tmp_path, {ESS_REL: ESSENTIALS})
    config = make_config(tmp_path, corrections_form_url=None)
    out = tmp_path / "dist"
    assert build(root, out, config_path=config, today=TODAY) == 0
    for page in ("index.html", "corrections/index.html", "methodology/index.html", "about/index.html"):
        html = read(out, page)
        assert "Corrections form coming soon" in html, page
        assert "Report a problem</a>" not in html, page


def test_configured_form_links_carry_encoded_item_id(tmp_path):
    root = make_repo(tmp_path, {ESS_REL: ESSENTIALS})
    config = make_config(tmp_path, corrections_form_url="https://forms.example.org/vtr?item={id}")
    out = tmp_path / "dist"
    assert build(root, out, config_path=config, today=TODAY) == 0
    home = read(out, "index.html")
    assert ('href="https://forms.example.org/vtr?item=tx%2Fvoter-essentials%2F2026-11-03'
            '%23registration-deadline"') in home
    assert "Corrections form coming soon" not in home
    assert 'href="https://forms.example.org/vtr?item="' in read(out, "corrections/index.html")


def test_report_pages_post_to_the_worker_with_a_honeypot(real_site):
    form = read(real_site, "report/index.html")
    assert '<form class="card report-form" method="post" action="/api/report">' in form
    for name in ("item", "problem", "source", "contact", "website"):
        assert f'name="{name}"' in form, name
    assert 'tabindex="-1"' in form  # honeypot is skipped by keyboard users
    assert '<script src="/static/js/report.js" defer></script>' in form
    assert "no IP address" in form
    assert "Thanks, we got it." in read(real_site, "report/thanks/index.html")
    error = read(real_site, "report/error/index.html")
    assert 'id="report-error"' in error and 'href="/report/"' in error


def test_real_site_links_the_built_in_report_form(real_site):
    assert 'href="/report/?item="' in read(real_site, "corrections/index.html")


@pytest.mark.parametrize("url", ["http://forms.example.org/?item={id}", "/report/", "report/?item={id}"])
def test_bad_form_urls_stop_the_build(tmp_path, url):
    root = make_repo(tmp_path, {})
    config = make_config(tmp_path, corrections_form_url=url)
    assert build(root, tmp_path / "dist", config_path=config, today=TODAY) == 1


def test_corrections_log_is_newest_first(tmp_path):
    entry = {"target": "tx/x#F-001", "change": "c", "reason": "r",
             "source_url": "https://example.gov/e", "reviewer": "owner"}
    log = [dict(entry, date="2026-01-02", change="older"), dict(entry, date="2026-03-04", change="newer")]
    root = make_repo(tmp_path, {"corrections/log.yaml": log})
    out = tmp_path / "dist"
    assert build(root, out, today=TODAY) == 0
    page = read(out, "corrections/index.html")
    assert page.index("newer") < page.index("older")


def test_refuses_to_replace_a_folder_it_did_not_create(tmp_path):
    out = tmp_path / "precious"
    out.mkdir()
    (out / "keep.txt").write_text("mine")
    with pytest.raises(SystemExit):
        build(make_repo(tmp_path, {}), out, today=TODAY)
    assert (out / "keep.txt").exists()


def test_rebuild_replaces_previous_output(tmp_path):
    out = tmp_path / "dist"
    root = make_repo(tmp_path, {})
    assert build(root, out, today=TODAY) == 0
    (out / "stale.html").write_text("old")
    assert build(root, out, today=TODAY) == 0
    assert not (out / "stale.html").exists()


# CSP hygiene: pages must work under script-src/style-src 'self' with no inline code.
INLINE_PATTERNS = {
    "style attribute": re.compile(r"<[^>]+\sstyle\s*=", re.I),
    "style element": re.compile(r"<style[\s>]", re.I),
    "event handler": re.compile(r"<[^>]+\son[a-z]+\s*=", re.I),
    "inline script": re.compile(r"<script(?![^>]*\bsrc=)(?![^>]*type=\"application/json\")[^>]*>", re.I),
}


def test_pages_have_no_inline_code_or_third_party_assets(real_site):
    for page in real_site.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        for name, pattern in INLINE_PATTERNS.items():
            assert not pattern.search(html), f"{name} in {page}"
        for tag, attr in (("script", "src"), ("link", "href")):
            for url in re.findall(rf'<{tag}\b[^>]*\b{attr}="([^"]+)"', html):
                assert url.startswith("/") and not url.startswith("//"), f"third-party {tag} {url} in {page}"
    css = (real_site / "static" / "css" / "site.css").read_text(encoding="utf-8")
    for url in re.findall(r"url\(\"?([^\")]+)", css):
        assert url.startswith("/static/"), url
    assert "@import" not in css


# ---- Stage 2: race pages from the fictional demo fixture ----
DEMO = REPO / "tests" / "fixtures" / "demo"
BALLOT_PAGE = "ballot/tx/demo-county/2026-11-03/index.html"
RACE_PAGE = "races/tx/demo-county/2026-11-03/commissioner-precinct-9/index.html"


@pytest.fixture(scope="module")
def demo_site(tmp_path_factory):
    out = tmp_path_factory.mktemp("demo") / "dist"
    assert build(DEMO, out, demo=True, today="2026-09-29") == 0
    return out


def test_demo_banner_on_every_page(demo_site):
    for page in demo_site.rglob("index.html"):
        assert "Demonstration only." in page.read_text(encoding="utf-8"), page


def test_home_links_the_ballot_which_links_the_race(demo_site):
    assert 'href="/ballot/tx/demo-county/2026-11-03/"' in read(demo_site, "index.html")
    assert 'href="/races/tx/demo-county/2026-11-03/commissioner-precinct-9/"' in read(demo_site, BALLOT_PAGE)


def test_race_sections_in_required_order(demo_site):
    html = read(demo_site, RACE_PAGE)
    ids = ["office-h", "why-h", "decisions-h", "issues-h", "candidates-h", "survey-h", "running-h", "promises-h", "how-h"]
    positions = [html.index(f'id="{i}"') for i in ids]
    assert positions == sorted(positions)


def test_race_page_shows_decisions_local_first_with_sources_and_badges(demo_site):
    html = read(demo_site, RACE_PAGE)
    section = html[html.index('id="decisions-h"'):html.index('id="issues-h"')]
    assert section.index("Demo County cut department spending") < section.index("Another county")
    assert "Being decided now" in section and "Decided" in section
    assert "How it affects you:" in section
    assert 'href="https://example.org/demo-county/minutes/2025-03-12"' in section
    assert section.count("Unverified") >= 2


def test_race_page_links_issues_this_office_has_a_say_in(demo_site):
    html = read(demo_site, RACE_PAGE)
    assert 'href="/who-decides/#demo-potholes"' in html
    assert "Decide which county roads get fixed and pay for it." in html


def test_who_decides_page_lists_issues_with_offices_laws_and_examples(demo_site):
    html = read(demo_site, "who-decides/index.html")
    assert 'id="demo-potholes"' in html
    assert "County commissioner:" in html
    assert 'href="https://example.org/demo-law"' in html
    assert "Demo County cut department spending by 3 percent" in html
    assert 'aria-current="page"' in html and ">Who decides?</a>" in html


def test_validator_checks_decisions_and_issues(tmp_path):
    from validate import validate
    import shutil
    root = tmp_path / "repo"
    shutil.copytree(DEMO, root)
    path = root / "data/states/tx/decisions/county-commissioner.yaml"
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    doc["decisions"][0]["powers"] = ["P-NOPE"]
    doc["decisions"][0]["facts"] = ["tx/nowhere#X"]
    doc["decisions"][0]["what_happened"] = "Republicans cut the budget."
    doc["decisions"][1]["id"] = "tx/elsewhere#wrong"
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
    wd = root / "data/states/tx/who-decides.yaml"
    issues = yaml.safe_load(wd.read_text(encoding="utf-8"))
    issues["issues"][0]["deciders"][0]["office_types"] = ["no-such-office"]
    wd.write_text(yaml.safe_dump(issues, sort_keys=False), encoding="utf-8")
    errors = " | ".join(validate(root, REPO / "schemas").errors)
    assert "power P-NOPE does not exist" in errors
    assert "fact 'tx/nowhere#X' does not exist" in errors
    assert "mentions a party" in errors
    assert "must start with 'tx/decisions/county-commissioner#'" in errors
    assert "office type 'no-such-office' has no folder" in errors


def test_candidates_alphabetical_not_file_order(demo_site):
    html = read(demo_site, RACE_PAGE)
    names = re.findall(r'<h3 id="cand-[^"]+">([^<]+)</h3>', html)
    assert names == ["Avery Example", "Blake Sample", "Casey Placeholder"]


def test_ballot_party_only_when_not_null(demo_site):
    html = read(demo_site, RACE_PAGE)
    assert "On the ballot as: Sample Party" in html
    assert "On the ballot as: Other Sample Party" in html
    blake = html[html.index('id="cand-blake-sample"'):html.index('id="cand-casey-placeholder"')]
    assert "On the ballot as" not in blake
    assert "Not the incumbent" in blake


def test_most_specific_why_it_matters_wins(demo_site):
    html = read(demo_site, RACE_PAGE)
    assert "The commissioners court sets the county tax rate" in html
    assert "OFFICE-LEVEL" not in html and "LOCALITY-LEVEL" not in html


def demo_facts():
    facts = []
    for file in (DEMO / "data").rglob("candidates/*.yaml"):
        doc = yaml.safe_load(file.read_text(encoding="utf-8"))
        for section in ("summary", "records", "funding", "endorsements"):
            facts += doc.get(section) or []
        for section in (doc.get("running_on") or {}).values():
            facts += section or []
    return facts


def test_every_fact_card_shows_its_verification_badge(demo_site):
    html = read(demo_site, RACE_PAGE)
    facts = demo_facts()
    assert any(f["verification"] == "unverified" for f in facts)
    for fact in facts:
        anchor = "f-" + re.sub(r"[^A-Za-z0-9_-]+", "-", fact["id"]).strip("-")
        card = re.search(rf'<li class="card item fact" id="{anchor}">(.*?)</li>', html, re.S)
        assert card, fact["id"]
        expected = "badge-unverified" if fact["verification"] == "unverified" else "badge-verified"
        assert expected in card.group(1), fact["id"]


def test_running_on_definitions_and_empty_categories(demo_site):
    html = read(demo_site, RACE_PAGE)
    for term in ("Policy proposal", "Attack messaging", "Contested claim"):
        assert f"<dt>{term}</dt>" in html
    running = html[html.index('id="running-h"'):html.index('id="promises-h"')]
    assert running.count("Not found in the sources reviewed.") >= 5


def test_promise_tracker_resolves_promises_and_evidence(demo_site):
    html = read(demo_site, RACE_PAGE)
    promises = html[html.index('id="promises-h"'):html.index('id="how-h"')]
    assert '<span class="status status-kept">Kept</span>' in promises
    assert '<span class="status status-pending">Still open</span>' in promises
    # the still-open promise is unverified, so only the kept one is counted
    assert "Made 1 promise:" in promises
    assert "1 kept · 0 not kept · 0 did the opposite · 0 still open" in promises
    assert "1 more not counted yet" in promises
    assert "A kept rate appears once at least 3 checked promises are decided." in promises
    # only incumbents get a card
    assert 'id="prom-' in promises and promises.count('<article class="card"') == 1
    assert "Evidence: Sponsored a road plan" in promises


def test_embedded_race_data_is_safe_json_with_alphabetical_candidates(demo_site):
    html = read(demo_site, RACE_PAGE)
    raw = re.search(r'<script type="application/json" id="race-data">(.*?)</script>', html, re.S).group(1)
    assert "<" not in raw
    import json
    data = json.loads(raw)
    assert [c["name"] for c in data["candidates"]] == ["Avery Example", "Blake Sample", "Casey Placeholder"]
    assert len(data["questions"]) == 5
    assert data["corrections_form_url"] == "/report/?item={id}"


def test_demo_pages_have_no_inline_code_or_third_party_assets(demo_site):
    test_pages_have_no_inline_code_or_third_party_assets(demo_site)


def test_survey_js_never_writes_html_or_inline_styles():
    js = (REPO / "site" / "static" / "js" / "survey.js").read_text(encoding="utf-8")
    js = re.sub(r"/\*.*?\*/|^\s*//.*$", "", js, flags=re.S | re.M)  # ignore comments
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", ".style",
                      "setAttribute(\"style\"", "localStorage", "sessionStorage", "document.cookie",
                      "fetch(", "XMLHttpRequest", "sendBeacon"):
        assert forbidden not in js, forbidden


def test_plain_job_summary_comes_first_with_law_one_click_away(demo_site):
    html = read(demo_site, RACE_PAGE)
    office = html[html.index('id="office-h"'):html.index('id="candidates-h"')]
    assert "DEMO plain intro about the commissioners court." in html
    assert office.index("DEMO votes on the budget") < office.index("See the exact law")
    assert "<details" in office and "Votes on the county budget and the property tax rate" in office


def test_fact_cards_lead_with_headline_and_fold_exact_wording(demo_site):
    html = read(demo_site, RACE_PAGE)
    anchor = "f-" + re.sub(r"[^A-Za-z0-9_-]+", "-",
        "tx/localities/demo-county/elections/2026-11-03/commissioner-precinct-9/candidates/02-avery-example#R-04")
    card = re.search(rf'<li class="card item fact" id="{anchor}">(.*?)</li>', html, re.S).group(1)
    main, rest = card.split("<details", 1)
    assert "DEMO HEADLINE voted for competitive bids" in main
    assert "Voted to require competitive bids on a storm-debris contract." in rest


def test_candidate_glance_is_built_from_facts_on_file(demo_site):
    html = read(demo_site, RACE_PAGE)
    avery = html[html.index('id="cand-avery-example"'):html.index('id="cand-blake-sample"')]
    glance = avery[avery.index('class="glance"'):avery.index("</ul>", avery.index('class="glance"'))]
    assert "<strong>Priorities:</strong> Transparency" in glance  # topic from the headline
    assert "Has served as Precinct 9 commissioner since 2023." in glance  # summary fact
    casey = html[html.index('id="cand-casey-placeholder"'):]
    assert "No record on file yet" in casey


def test_record_block_leads_the_card_newest_first_with_results(demo_site):
    html = read(demo_site, RACE_PAGE)
    start = html.rindex("<article", 0, html.index('id="cand-avery-example"'))
    card = html[start:html.index("</article>", start)]
    assert card.index('class="on-record"') < card.index('class="glance"')  # record comes first
    block = card[card.index('class="on-record"'):card.index('class="glance"')]
    shown = re.findall(r'<span class="kind">([^<·]*?)\s*· (\d{4}-?\d*)</span>', block)
    assert len(shown) == 3 and "more in" in block  # Avery has 6 records, 3 shown
    assert all(kind == "Vote" for kind, _ in shown)  # votes come before other actions
    assert "DEMO RESULT passed 4-1" in block  # R-04 is a vote, so it is on the card
    assert "What happened next:" in block and "Sponsored a road plan" in block


def test_record_order_votes_first_then_newest(demo_site):
    import sys
    sys.path.insert(0, str(REPO / "src" / "build"))
    from model import record_top
    recs = [{"id": "a", "record_type": "statement", "event_date": "2026-01-01", "statement": "s"},
            {"id": "b", "record_type": "vote", "event_date": "2020-01-01", "statement": "old vote"},
            {"id": "c", "record_type": "vote", "event_date": "2024-01-01", "statement": "new vote"},
            {"id": "d", "record_type": "sponsored", "event_date": "2025-01-01", "statement": "sp"}]
    order = [i["fact"]["id"] for i in record_top({"records": recs}, {})["items"]]
    assert order == ["c", "b", "a"]  # votes newest-first, then the newest other action


def test_avatar_shows_initials_without_a_photo(demo_site):
    html = read(demo_site, RACE_PAGE)
    card_start = html.rindex("<article", 0, html.index('id="cand-avery-example"'))
    avery_card = html[card_start:html.index("</article>", card_start)]
    assert 'class="avatar avatar-initials"' in avery_card and ">AE<" in avery_card


def test_race_without_an_incumbent_has_no_promise_tracker(real_site):
    page = real_site / "races/tx/harris-county/2026-11-03/county-judge/index.html"
    html = page.read_text(encoding="utf-8")
    assert 'id="candidates-h"' in html
    assert 'id="promises-h"' not in html


# --- Ballot pages, ZIP finder, basic race pages -----------------------------

def test_ballot_is_ordered_closest_to_home_then_by_ballot_order(demo_site):
    html = read(demo_site, BALLOT_PAGE)
    levels = re.findall(r'<h2 id="lvl-([a-z]+)">', html)
    assert levels == ["local", "county", "state", "federal"]
    names = re.findall(r'<h3 class="ballot-race-name"><a href="[^"]+">([^<]+)</a></h3>', html)
    assert names == ["Demo City Council, Place 1", "Demo City, Proposition A", "Demo County Commissioner, Precinct 9",
                     "Justice of the Peace, Precinct 1", "Justice of the Peace, Precinct 2", "Governor",
                     "U.S. Representative, District 1", "U.S. Representative, District 2"]
    rows = re.findall(r'data-race="(\d+)"', html)
    assert rows == [str(i) for i in range(8)]  # matches the embedded payload order


def test_ballot_lists_candidates_a_to_z_with_ballot_labels_and_write_ins(demo_site):
    html = read(demo_site, BALLOT_PAGE)
    gov = html[html.index(">Governor</a>"):html.index("U.S. Representative, District 1")]
    names = re.findall(r'<span class="cand-name">([^<]+)</span>', gov)
    assert names == ["Dana Demo", "Evan Example", "Wren Writein"]
    assert "Listed on ballot as: Sample Party" in gov
    assert '<span class="chip">Write-in</span>' in gov


def test_ballot_zip_tools_are_private_and_progressive(demo_site):
    html = read(demo_site, BALLOT_PAGE)
    assert '<form id="zip-form" class="zip-form" role="search" hidden>' in html  # shown by JS only
    assert "nothing is sent or saved" in html
    assert 'href="https://example.gov/whats-on-my-ballot"' in html
    assert html.count('class="chip split-chip" hidden') == 8
    raw = re.search(r'<script type="application/json" id="ballot-data">(.*?)</script>', html, re.S).group(1)
    data = json.loads(raw)
    assert data["implied"] == ["tx", "demo-county"]
    assert data["zips"]["22222"] == [["us-house-1", "us-house-2"], "demo-county/jp-2"]
    assert [r["area"] for r in data["races"]][:3] == ["demo-city", "demo-city", "demo-county/commissioner-9"]
    assert "Unverified" in html  # ballot sources carry badges


def test_home_zip_finder_maps_zips_to_ballots(demo_site):
    html = read(demo_site, "index.html")
    raw = re.search(r'<script type="application/json" id="zip-index">(.*?)</script>', html, re.S).group(1)
    index = json.loads(raw)
    assert sorted(index) == ["11111", "22222", "33333"]
    assert index["11111"] == [{"name": "Demo County ballot", "url": "/ballot/tx/demo-county/2026-11-03/"}]
    assert '<form id="zip-find" class="zip-form" role="search" hidden>' in html


def test_basic_race_page_is_honest_about_what_is_missing(demo_site):
    html = read(demo_site, "races/tx/statewide/2026-11-03/governor/index.html")
    assert html.count("We haven't researched this candidate's record yet.") == 3
    assert "Not found in the sources reviewed" not in html
    for section in ('id="survey-h"', 'id="running-h"', 'id="promises-h"', 'id="how-h"', "survey.js"):
        assert section not in html, section
    assert 'id="coming-h"' in html
    assert "Write-in candidate" in html
    assert "Incumbent" not in html and "Not the incumbent" not in html  # incumbent: null = not checked
    assert "Where this list of candidates comes from" in html


def test_district_races_get_their_own_pages(demo_site):
    html = read(demo_site, "races/tx/us-house-2/2026-11-03/us-representative/index.html")
    assert "<h1>U.S. Representative, District 2</h1>" in html


def test_partial_ballot_says_races_are_still_being_added(tmp_path):
    src = REPO / "tests" / "fixtures" / "demo"
    root = tmp_path / "demo"
    shutil.copytree(src, root)
    ballot = root / "data/states/tx/localities/demo-county/elections/2026-11-03/ballot.yaml"
    ballot.write_text(ballot.read_text(encoding="utf-8") + "complete: false\n", encoding="utf-8")
    out = tmp_path / "dist"
    assert build(root, out, demo=True, today="2026-09-29") == 0
    html = read(out, BALLOT_PAGE)
    assert "We're still adding races to this page." in html
    assert 'href="https://example.gov/demo-sample-ballot.pdf"' in html
    assert "8 races added so far." in html
    assert "Every race on this ballot" not in html
    assert "8 races so far" in read(out, "index.html")


def test_ballot_payload_packs_precincts_as_area_indexes(demo_site):
    html = read(demo_site, BALLOT_PAGE)
    raw = re.search(r'<script type="application/json" id="ballot-data">(.*?)</script>', html, re.S).group(1)
    data = json.loads(raw)
    assert data["areas"] == ["demo-city", "demo-county/commissioner-9", "demo-county/jp-1", "demo-county/jp-2",
                             "us-house-1", "us-house-2"]
    assert data["precincts"]["102"] == [5, 3]
    assert '<input id="precinct" name="precinct"' in html
    assert "on your voter registration card" in html



def test_precinct_outlines_are_published_for_the_address_lookup(demo_site):
    shapes = demo_site / "geo/tx/demo-county/2026-11-03/precinct-shapes.json"
    assert shapes.is_file()
    assert sorted(json.loads(shapes.read_text(encoding="utf-8"))["precincts"]) == ["101", "102", "103"]
    html = read(demo_site, BALLOT_PAGE)
    raw = re.search(r'<script type="application/json" id="ballot-data">(.*?)</script>', html, re.S).group(1)
    data = json.loads(raw)
    assert data["shapesUrl"] == "/geo/tx/demo-county/2026-11-03/precinct-shapes.json"
    assert data["locality"] == "demo-county"
    assert '<input id="address" name="address"' in html
    assert "U.S. Census Bureau through our server and never stored" in html


def test_home_address_box_maps_counties_to_ballots(demo_site):
    html = read(demo_site, "index.html")
    raw = re.search(r'<script type="application/json" id="county-index">(.*?)</script>', html, re.S).group(1)
    assert json.loads(raw) == {"demo-county": [{"name": "Demo County ballot", "url": "/ballot/tx/demo-county/2026-11-03/"}]}
    assert '<input id="address-home" name="address"' in html



def test_proposition_is_listed_closest_to_home_with_its_choices(demo_site):
    html = read(demo_site, BALLOT_PAGE)
    local = html[html.index('id="lvl-local"'):html.index('id="lvl-county"')]
    assert '<span class="chip chip-measure">Proposition</span>' in local
    assert "Lets the city borrow up to $10 million for parks" in local
    assert "Your choices: FOR · AGAINST" in local
    assert 'href="/measures/tx/demo-city/2026-11-03/prop-a/"' in local


def test_proposition_page_shows_exact_wording_choices_meaning_and_sources(demo_site):
    html = read(demo_site, "measures/tx/demo-city/2026-11-03/prop-a/index.html")
    assert "<h1>Demo City, Proposition A</h1>" in html
    assert '<blockquote class="ballot-text">DEMO CITY, PROPOSITION A. THIS IS A PROPERTY TAX INCREASE.' in html
    assert "<li>FOR</li>" in html and "<li>AGAINST</li>" in html
    meaning = html[html.index('id="meaning-h"'):html.index('id="sources-h"')]
    assert "Lets the city borrow up to $10 million for parks" in meaning
    assert 'badge badge-unverified' in meaning
    assert "DEMO County Clerk: Sample Ballot" in html



def test_unmapped_districts_get_their_own_box_and_multi_seat_races_say_so(tmp_path):
    root = tmp_path / "demo"
    shutil.copytree(REPO / "tests" / "fixtures" / "demo", root)
    folder = root / "data/states/tx/localities/demo-esd/elections/2026-11-03/commissioners"
    (folder / "candidates").mkdir(parents=True)
    src = {"id": "tx/localities/demo-esd/elections/2026-11-03/commissioners/race#S-01", "statement": "DEMO ballot.",
           "label": "official_record", "claim_status": "documented", "contradicted_by": [],
           "source_url": "https://example.gov/b.pdf", "source_title": "DEMO", "source_kind": "page", "sha256": None,
           "archive_url": None, "event_date": "2026-11-03", "retrieved": "2026-09-01", "verification": "unverified",
           "verified_on": None, "reviewer": None}
    (folder / "race.yaml").write_text(yaml.safe_dump({"name": "Demo ESD Commissioners", "office_type": "city-council-member",
                                                      "election_date": "2026-11-03", "seats": 3, "detail": "basic",
                                                      "sources": [src]}), encoding="utf-8")
    for slug, name in (("a-one", "A One"), ("b-two", "B Two")):
        (folder / "candidates" / f"{slug}.yaml").write_text(yaml.safe_dump({
            "id": slug, "name": name, "incumbent": None, "ballot_party": None, "records": [], "funding": [],
            "endorsements": [], "running_on": {"policy_proposals": [], "attack_messaging": [], "contested_claims": []},
            "mappings": [], "promise_tracker": []}), encoding="utf-8")
    ballot = root / "data/states/tx/localities/demo-county/elections/2026-11-03/ballot.yaml"
    ballot.write_text(ballot.read_text(encoding="utf-8")
                      + "- tx/localities/demo-esd/elections/2026-11-03/commissioners\nunmapped:\n- demo-esd\n", encoding="utf-8")
    out = tmp_path / "dist"
    assert build(root, out, demo=True, today="2026-09-29") == 0
    html = read(out, BALLOT_PAGE)
    box = html[html.index('id="unmapped-box"'):html.index("</aside>", html.index('id="unmapped-box"'))]
    assert "hidden" in html[html.index('<aside id="unmapped-box"'):html.index('<aside id="unmapped-box"') + 60]
    assert "We can't map these districts yet" in box
    assert 'href="/races/tx/demo-esd/2026-11-03/commissioners/"' in box
    assert "Vote for up to 3." in html
    data = json.loads(re.search(r'id="ballot-data">(.*?)</script>', html, re.S).group(1))
    assert data["unmapped"] == ["demo-esd"]
    race = read(out, "races/tx/demo-esd/2026-11-03/commissioners/index.html")
    assert "<strong>Vote for up to 3.</strong> This race fills 3 seats." in race


def test_finance_filer_picks_the_filing_office_by_level_type_and_locality():
    from build import finance_filer
    config = {"campaign_finance": {
        "federal": {"name": "FEC", "url": "https://fec.example/"},
        "tx": {"law_title": "Code ch. 252", "law_url": "https://law.example/252",
               "state": {"name": "TEC", "url": "https://tec.example/"}, "state_office_types": ["district-judge"],
               "localities": {"harris-county": {"name": "County Clerk", "url": "https://clerk.example/",
                                                "office_types": ["county-clerk"]}}}}}

    def race(level, office, locality="harris-county", state="tx"):
        return {"office_level": level, "office_type": office, "locality": locality, "state": state}

    assert finance_filer(config, race("federal", "us-representative"))["name"] == "FEC"
    assert finance_filer(config, race("state", "governor", "statewide"))["name"] == "TEC"
    assert finance_filer(config, race("county", "district-judge"))["name"] == "TEC"
    assert finance_filer(config, race("county", "county-clerk"))["url"] == "https://clerk.example/"
    local = finance_filer(config, race("local", "mayor", "some-city"))
    assert local["name"] is None and local["law_url"] == "https://law.example/252"
    assert finance_filer(config, race("county", "county-clerk", state="ca")) is None
    assert finance_filer({}, race("federal", "us-representative")) is None
    config["campaign_finance"]["tx"]["localities"]["dallas-county"] = {
        "name": "Dallas County", "published_on": "the county's reports page", "url": "https://dallas.example/",
        "office_types": ["constable"]}
    posted = finance_filer(config, race("county", "constable", "dallas-county"))
    assert posted["published_on"] == "the county's reports page" and posted["url"] == "https://dallas.example/"
    assert finance_filer(config, race("county", "county-clerk"))["published_on"] is None


def test_basic_race_shows_money_lines_and_unconfirmed_incumbents(tmp_path):
    root = tmp_path / "demo"
    shutil.copytree(REPO / "tests" / "fixtures" / "demo", root)
    folder = root / "data/states/tx/localities/demo-esd/elections/2026-11-03/commissioners"
    (folder / "candidates").mkdir(parents=True)
    base = {"label": "official_record", "claim_status": "documented", "contradicted_by": [], "source_kind": "page",
            "sha256": None, "archive_url": None, "event_date": "2026-09-01", "retrieved": "2026-09-01",
            "verification": "unverified", "verified_on": None, "reviewer": None}
    src = dict(base, id="tx/localities/demo-esd/elections/2026-11-03/commissioners/race#S-01", statement="DEMO ballot.",
               source_url="https://example.gov/b", source_title="DEMO")
    check = dict(base, id="tx/localities/demo-esd/elections/2026-11-03/commissioners/race#S-FUND",
                 statement="DEMO: no report for B TWO.", source_url="https://example.gov/cf",
                 source_title="Demo Ethics Office: bulk data")
    (folder / "race.yaml").write_text(yaml.safe_dump({"name": "Demo ESD Commissioners", "office_type": "city-council-member",
                                                      "election_date": "2026-11-03", "detail": "basic",
                                                      "sources": [src, check]}), encoding="utf-8")
    money = dict(base, id="tx/localities/demo-esd/elections/2026-11-03/commissioners/candidates/a-one#M-01",
                 headline="Raised $10 and had $5 on hand (DEMO)", statement="DEMO report.",
                 source_url="https://example.gov/r1", source_title="DEMO report")
    older = dict(money, id=money["id"].replace("#M-01", "#M-00"), headline="Raised $1 (OLDER DEMO)", event_date="2026-01-15")
    for slug, name, funding in (("a-one", "A One", [older, money]), ("b-two", "B Two", [])):
        (folder / "candidates" / f"{slug}.yaml").write_text(yaml.safe_dump({
            "id": slug, "name": name, "incumbent": None, "ballot_party": None, "records": [], "funding": funding,
            "endorsements": [], "running_on": {"policy_proposals": [], "attack_messaging": [], "contested_claims": []},
            "mappings": [], "promise_tracker": []}), encoding="utf-8")
    out = tmp_path / "dist"
    assert build(root, out, demo=True, today="2026-09-29") == 0
    race = read(out, "races/tx/demo-esd/2026-11-03/commissioners/index.html")
    assert "We haven't confirmed who holds this office now." in race
    assert "Raised $10 and had $5 on hand (DEMO)" in race and 'href="https://example.gov/r1"' in race
    assert race.index("Raised $10 and had $5 on hand (DEMO)") < race.index("Earlier reports (1)") < race.index("(OLDER DEMO)")
    assert "No 2025–2026 report found in Demo Ethics Office data." in race
    assert "Campaign finance reports for this office are filed locally, not with the state." in race
    assert "Texas Election Code, chapter 252</a> lists the filing office" in race


def test_researched_race_says_pending_for_a_candidate_not_yet_researched(tmp_path):
    root = tmp_path / "demo"
    shutil.copytree(REPO / "tests" / "fixtures" / "demo", root)
    folder = root / "data/states/tx/localities/demo-esd/elections/2026-11-03/commissioners"
    (folder / "candidates").mkdir(parents=True)
    base = {"label": "official_record", "claim_status": "documented", "contradicted_by": [], "source_kind": "page",
            "sha256": None, "archive_url": None, "event_date": "2026-09-01", "retrieved": "2026-09-01",
            "verification": "unverified", "verified_on": None, "reviewer": None}
    src = dict(base, id="tx/localities/demo-esd/elections/2026-11-03/commissioners/race#S-01", statement="DEMO ballot.",
               source_url="https://example.gov/b", source_title="DEMO")
    (folder / "race.yaml").write_text(yaml.safe_dump({"name": "Demo ESD Commissioners", "office_type": "city-council-member",
                                                      "election_date": "2026-11-03", "sources": [src]}), encoding="utf-8")
    vote = dict(base, id="tx/localities/demo-esd/elections/2026-11-03/commissioners/candidates/a-one#R-01",
                record_type="vote", headline="Voted yes on the DEMO budget", statement="DEMO vote.",
                source_url="https://example.gov/m", source_title="DEMO minutes")
    for slug, name, records, extra in (("a-one", "A One", [vote], {}), ("b-two", "B Two", [], {"research": "pending"})):
        (folder / "candidates" / f"{slug}.yaml").write_text(yaml.safe_dump({
            "id": slug, "name": name, "incumbent": None, "ballot_party": None, **extra, "records": records, "funding": [],
            "endorsements": [], "running_on": {"policy_proposals": [], "attack_messaging": [], "contested_claims": []},
            "mappings": [], "promise_tracker": []}), encoding="utf-8")
    out = tmp_path / "dist"
    assert build(root, out, demo=True, today="2026-09-29") == 0
    race = read(out, "races/tx/demo-esd/2026-11-03/commissioners/index.html")
    assert "Voted yes on the DEMO budget" in race
    b_two = race[race.index('id="cand-b-two"'):]
    assert b_two.index("We haven't researched this candidate's record yet.") < b_two.index("</article>")
    run_b = race[race.index('id="run-b-two"'):]
    assert run_b.index("We haven't researched this candidate's record yet.") < run_b.index("</article>")
    assert "Not found in the sources reviewed" not in run_b[:run_b.index("</article>")]


def test_ballot_page_has_topics_and_a_quiz_without_judges(tmp_path):
    out = tmp_path / "dist"
    assert build(REPO / "tests" / "fixtures" / "demo", out, demo=True, today="2026-09-29") == 0
    html = read(out, BALLOT_PAGE)
    assert 'id="topic-picker"' in html and 'value="taxes-budget"' in html and 'value="roads-transportation"' in html
    assert 'value="schools"' not in html  # only topics some race on this ballot deals with
    assert "About: Taxes &amp; budget · Disasters &amp; emergencies · Roads &amp; transportation" in html
    data = json.loads(re.search(r'id="ballot-data">(.*?)</script>', html, re.S).group(1))
    keys = [q["key"] for q in data["quiz"]]
    assert keys == ["demo-county/commissioner-precinct-9"]  # JP races are judicial: never a quiz
    assert data["topicLabels"]["schools"] == "Schools"
    quiz = html[html.index('id="ballot-quiz"'):]
    n = data["quiz"][0]["n"]
    assert f'name="answer-{n}-county-commissioner/S-01"' in quiz
    assert "Texas Code of Judicial Conduct" in quiz
    assert "Closest to your answers" not in html  # only ever added by JS, under the rules in quiz.js


def test_topic_labels_must_match_the_schema(tmp_path):
    config = yaml.safe_load((REPO / "site" / "site.yaml").read_text(encoding="utf-8"))
    config["topics"].pop("energy")
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    assert build(REPO / "tests" / "fixtures" / "demo", tmp_path / "dist", demo=True, config_path=path) == 1


def test_second_state_builds_with_its_own_wording(tmp_path):
    """The demo repo moved to Washington (states/wa) builds, and its ballot page uses Washington wording."""
    root = tmp_path / "wa"
    shutil.copytree(DEMO, root)
    shutil.move(root / "data/states/tx", root / "data/states/wa")
    for f in (root / "data/states/wa").rglob("*"):
        if f.suffix in (".yaml", ".json"):
            text = f.read_text(encoding="utf-8")
            text = re.sub(r"\btx/", "wa/", text).replace("state: TX", "state: WA").replace('"TX"', '"WA"')
            f.write_text(text, encoding="utf-8")
    out = tmp_path / "dist"
    assert build(root, out, demo=True, today="2026-09-29") == 0
    html = read(out, "ballot/wa/demo-county/2026-11-03/index.html")
    assert "Washington government" in html and "Texas" not in html
    assert 'placeholder="98104"' in html
    if 'id="ballot-quiz"' in html:
        assert "the Washington Code of Judicial Conduct bars" in html


def test_quiz_asks_only_scenarios_written_for_the_state_whose_powers_apply(tmp_path):
    """A scenario marked `states: [TX]` stays off Washington pages, and one resting on a power that doesn't apply
    to the race is never asked."""
    def make(state):
        root = tmp_path / state
        shutil.copytree(DEMO, root)
        survey = root / "offices/county-commissioner/survey.yaml"
        text = survey.read_text(encoding="utf-8")
        text = text.replace("    powers: [P-BUDGET]\n", "    powers: [P-BUDGET]\n    states: [TX]\n", 1)
        text = text.replace("    powers: [P-EMERGENCY]\n", "    powers: [P-EMERGENCY, P-NOT-HERE]\n", 1)
        survey.write_text(text, encoding="utf-8")
        powers = root / "offices/county-commissioner/powers.yaml"
        powers.write_text(powers.read_text(encoding="utf-8").replace(
            "powers:\n", "powers:\n  - id: P-NOT-HERE\n    description: Applies only elsewhere.\n"
            "    statute: Demo Code § 1\n    source_url: https://example.org/demo\n    only_in: [nowhere-county]\n", 1),
            encoding="utf-8")
        if state == "wa":
            shutil.move(root / "data/states/tx", root / "data/states/wa")
            for f in (root / "data/states/wa").rglob("*"):
                if f.suffix in (".yaml", ".json"):
                    t = f.read_text(encoding="utf-8")
                    f.write_text(re.sub(r"\btx/", "wa/", t).replace("state: TX", "state: WA").replace('"TX"', '"WA"'),
                                 encoding="utf-8")
        out = tmp_path / f"dist-{state}"
        assert build(root, out, demo=True, today="2026-09-29") == 0
        return read(out, f"ballot/{state}/demo-county/2026-11-03/index.html")

    tx, wa = make("tx"), make("wa")
    q1, q2 = '"id":"county-commissioner/S-01"', '"id":"county-commissioner/S-02"'
    assert q1 in tx and q1 not in wa
    assert q2 not in tx and q2 not in wa
