# Methodology

Vote the Record matches your answers to hypothetical scenarios with what candidates have
actually done. This page explains where the information comes from, how it is checked,
and how match results are calculated.

## The short version

- **Everything has a source.** Every fact links to where it came from, plus a saved copy in case the original changes.
- **Official records count most.** A recorded vote beats a news story, and a news story beats a campaign's own claim.
- **A person checks every fact.** Until they do, it's marked **Unverified**, and it can't affect your quiz results.
- **The quiz is about the job.** Questions are what-ifs the office could really face. Party is never mentioned.
- **No record means no penalty.** If a candidate has no record on a question, it's left out, not counted against them.
- **Your answers stay with you.** The quiz runs in your browser. Nothing is saved or sent.

The details are below.

## Sources
Official government records come first: council and commissioners court minutes,
legislative roll calls, election filings, and campaign finance reports. News reports and
candidate statements are included, but they are labeled as such and count for less.

Every fact shows:

| Field | Meaning |
|---|---|
| Label | Official record, news report, candidate claim, or organization statement |
| Status | Documented, allegation, disputed, or contradicted |
| Source | Link to the original |
| Archive | Permanent copy, usually on the Wayback Machine |
| Fingerprint | For downloadable documents such as PDFs: a SHA-256 hash of the file we reviewed, so anyone can confirm it hasn't changed. Web pages don't get a fingerprint; the archived copy serves that purpose. |
| Verification | Verified (with date and reviewer) or Unverified |

An **organization statement** is an organization speaking for itself on its own website, for
example a group announcing that it endorses a candidate. These are shown for information and
never count toward match results.

**Status definitions**
- **Documented** — the source directly shows it.
- **Allegation** — reported but not established by a record.
- **Disputed** — credible sources disagree.
- **Contradicted** — other evidence shows it is false. This always links that evidence.

## Verification
Information is gathered by automated tools and research assistants, some of them AI.
Everything they gather starts as **Unverified** and carries a visible badge. A human
reviewer checks each item against its source and archived copy before marking it
**Verified**. A verified item always has an archived copy and a review date no earlier
than the date the source was retrieved. Only verified information counts toward match
results.

## The survey
Each office type (e.g. county commissioner) has a list of its legal powers. Every survey
question is a hypothetical scenario the office could actually face, and each one cites
the power(s) it rests on. Party is never mentioned.

## Connecting records to answers
A reviewer maps a candidate's record (a vote, a sponsored measure, a kept or broken
promise, or a statement) to the survey option or options it supports, and writes a
rationale for each mapping. Mappings are verified the same way facts are. All mappings
and rationales are public.

## How the match is calculated

**What counts:** a record is scored only if it is verified, its status is documented,
and its mapping is verified.

**Step 1 — Weight each record**

record weight = type weight × evidence factor

| Record type | Type weight |
|---|---|
| Vote | 3 |
| Sponsored measure | 2 |
| Promise kept or broken | 2 |
| Statement | 1 |

| Source label | Evidence factor |
|---|---|
| Official record | 1.0 |
| News report | 0.7 |
| Candidate claim | 0.4 |

**Step 2 — Count each event once**

Several records can describe the same event: for example, the official minutes of a vote,
a news story about that vote, and the candidate's own statement explaining it. Records
that describe the same event share a `same_event` group. When more than one record from
a group maps to the same question, only the one with the highest weight is scored. If two
records in a group tie on weight, the one whose ID comes first alphabetically is scored, so
the result never depends on the order records were entered. This keeps a single vote from
being counted several times just because it was widely reported.

**Step 3 — Score each question**

For each question you answered, the candidate's alignment is the weight of their records
that support your chosen option, divided by the weight of all their scored records on
that question (0% to 100%).

**Step 4 — Apply your importance**

You rate each question 1 (low) to 3 (high):

    match = Σ (importance × alignment) ÷ Σ importance

**Questions with no record are left out.** If a candidate has no scorable record on a
question, that question is excluded from their result. It is not counted as zero, because
having no record is not a disagreement.

**Minimum record.** A match percentage is shown only when at least **3** of your answered
questions have scorable records for that candidate. Otherwise the result reads
**"Not enough record to compare."** Every result says "Based on X of Y questions."

### Worked example
You answered 4 questions. On S-01 you picked B and rated it importance 3.
- The candidate's vote supports B (verified, official, documented): 3 × 1.0 = 3.0, match
- A news story about that same vote (same event group) also maps to S-01. It weighs
  3 × 0.7 = 2.1, less than the official record, so it is not scored.
- The candidate's statement supports A (verified, candidate claim): 1 × 0.4 = 0.4, no match
- Alignment on S-01 = 3.0 ÷ 3.4 = 88%

If the candidate also has scorable records on S-02 and S-03 but none on S-04, the result
is a percentage "Based on 3 of 4 questions." With records on only S-01 and S-02, the
result is "Not enough record to compare. Based on 2 of 4 questions."

## Beta mode
While the project is new, many candidates won't yet have enough verified records to
compare. For any candidate below the minimum, the site runs in **beta mode**:
- No overall match percentage is shown.
- Instead, each question you answered gets a breakdown listing the candidate's records
  mapped to it, the option(s) each record supports, and the rationale.
- Every item carries its verification badge. Unverified items are shown so you can see
  what is being reviewed, but they are never scored.

Once a candidate has scorable records on at least 3 of your answered questions, the match
percentage appears, with the same breakdown available underneath.

## Quiz for your whole ballot
After you enter your address, ZIP code, or precinct, the ballot page offers one quiz with
the questions from every race on your ballot that has them. Each race is scored on its
own, exactly as above. Nothing is added up across races.

**What you see first.** "We could compare candidates in X of your Y races." A race counts
when at least two of its candidates have records and every one of them has a match
percentage. Most races start out with too few
checked records, so X is often small at first and grows as we research more races.

**Candidates with no record.** Fair isn't the same as identical. We research every
candidate the same way and show the records we find. If we find no record at all for a
candidate (for example, a first-time write-in candidate), we say so plainly: "We found no
record for this candidate in the sources we reviewed." That candidate stays on the list in
alphabetical order but isn't part of the comparison.

**"Closest to your answers."** In a race, this mark goes to the candidate with the
highest match percentage, but only when:
- at least two candidates have records,
- every candidate with records has a percentage (enough checked record on at least 3 of
  your answered questions), and
- no one ties for the highest percentage (as shown, rounded to a whole number).

Otherwise no one is marked, and the race says why. A candidate whose records aren't
checked yet is never ranked below one with more. Candidates stay in alphabetical order
either way. The mark is a summary of the records, not a recommendation.

**No quiz for judges.** The Texas Code of Judicial Conduct, Canon 5(1)(i), bars judges
and judicial candidates from making "pledges or promises of conduct in office regarding
pending or impending cases, specific classes of cases, specific classes of litigants, or
specific propositions of law" ([Texas Code of Judicial Conduct](https://www.txcourts.gov/media/1462935/texas-state-code-of-judicial-conduct.pdf), [archived copy](https://web.archive.org/web/20261001092159/https://www.txcourts.gov/media/1462935/texas-state-code-of-judicial-conduct.pdf)).
A "what would you do?" quiz would ask for exactly that, so races for judges never have
one. The validator rejects quiz questions for any office marked as judicial.

**Topics.** "What matters to you?" highlights races whose office deals with the topics you
pick. Each of an office's legal powers lists the topics it directly covers, judged only
from what the law says the office does. For example, a county commissioner's road power is
tagged "Roads & transportation". A power that doesn't clearly fit a topic is left untagged.
Lawmakers can pass laws on many subjects, but their powers are tagged only where the power
itself names the topic (the budget and taxes). Topics never change the order of races and
never depend on the candidates.

**Your results stay with you.** Answers live only in the page while it's open. "Print or
save as PDF" uses your browser's print window. "Copy a private link" puts your answers in
the part of the link after `#`, which browsers never send to any server. Anyone you give the
link to can see your answers. When you open such a link, the page fills in the answers,
shows the results, and then clears them from the address bar.

## How your ballot is ordered
Each county ballot page lists every race, grouped by level of government, **closest to
home first**:

1. **Closest to home:** city, school district, and other local offices and questions
2. **Your county:** county government and the courts that serve the county
3. **Your state:** statewide offices, the legislature, and state courts
4. **National:** U.S. Senate and U.S. House

Local offices decide things that reach your daily life soonest (streets, schools, local
courts, property taxes), and fewer people vote in those races, so each vote carries more
weight. Within each level, races keep the order of the official ballot. The level of each
office is set once, for the office type, and is the same for everyone. The order never
depends on the candidates.

**Finding your races by ZIP code.** Enter a ZIP code to see only the races on your ballot.
The lookup runs in your browser: your ZIP code is not sent anywhere or saved. The ZIP
code list for each county cites where it comes from. A ZIP code can cross district lines;
when it does, every race you might have is shown and marked "Depends on your address".
For your exact ballot, use your county's official lookup, which each ballot page links.

**Finding your races by street address (exact).** Type your address and we find the
precinct it's in, which gives your exact list of races. Here's what happens to your address:
our server passes it to the U.S. Census Bureau's public geocoder, which returns only a map
location and county. The Census Bureau sees the address but not who you are, because the
request comes from our server, not your device. Your browser then matches that location
against the county's official precinct map. Nothing is stored or logged, by us or on your
device. If you live right on a precinct line, double-check with your county's official
lookup.

**Finding your races by precinct number (exact).** Your voting precinct number is printed
on your voter registration card. Enter it to see exactly the races on your ballot. Each
precinct's districts come from the county's official voting precinct map. Like the ZIP
lookup, it runs in your browser and nothing is sent or saved.

**Cities and other local areas.** City limits and school district boundaries come from the
Census Bureau; water districts (such as municipal utility districts) come from the Texas
Commission on Environmental Quality's official water district map. A precinct or ZIP code that is only partly inside a city shows that city's races
and propositions as "Depends on your address"; the address lookup checks the city outline
directly, so it gives the exact answer.

**Districts we can't map yet.** A few districts have no official boundary map we can use yet.
We never guess whether you live in one. After you look up your ballot, their contests are
listed separately under "Also on some ballots", with a link to your county's official lookup.

**How ZIP codes are matched.** ZIP codes aren't voting districts. We use the Census
Bureau's ZIP Code Tabulation Areas, which approximate ZIP codes, and overlay them on the
county's precinct map. A precinct counts for a ZIP code when it covers at least 1% of the
ZIP code's area in the county. When those precincts are in different districts, each
possible race is shown and marked "Depends on your address".

**Basic race pages.** We're adding every race on the ballot first, with what the office
does and the candidates as printed on the official sample ballot. A basic page says
"We haven't researched this candidate's record yet", which is different from "not found
in the sources reviewed". Records, sources, and quizzes are added race by race.

**Who holds the office now.** "Incumbent" means the candidate holds the seat on the
ballot, not a different office. We check the official list for that office (for example,
the Texas House member list, a court's list of judges, or a school board's member page),
link it under "Where this list of candidates comes from", and archive it on the Wayback
Machine. When we can't get or match an official list, the page says "We haven't confirmed
who holds this office now" and no candidate is marked either way.

**Money.** Every race page says where its candidates file campaign finance reports: the
Federal Election Commission for Congress, the Texas Ethics Commission for state offices and
district judges, and the county clerk or a local office for the rest (Texas Election Code,
chapter 252). Where the filing office publishes data we can read in bulk, each candidate's
card shows totals from their latest report for a period ending in 2025 or 2026: money
raised, money spent, cash on hand, and loans, exactly as reported, with the report period.
If no report turns up, the card says so. A missing report in the data does not prove that
none was filed. Totals are display-only and never affect a match.

## Promise tracker
For now, only the **current officeholder** (the incumbent) has a promise tracker. It
lists promises they made and the status of each:

| Status | Meaning |
|---|---|
| Kept | They did what they promised. |
| Not kept | They didn't do it. |
| Did the opposite | They acted against what they promised. |
| Still open | It can't be judged yet. |

Every status except "still open" must link evidence, and official records come first.

**Counts and kept rate.** The tracker shows how many promises were made and how many
are in each status, for example "Made 6 promises: 3 kept · 1 not kept · 1 did the
opposite · 1 still open." Kept, not kept, and did the opposite are *decided* promises.
The kept rate is kept ÷ decided ("Kept 3 of 5 decided promises (60%)"). Still-open
promises are left out of the rate, not counted against anyone. The rate appears only
once at least 3 promises are decided; before that, only the counts show.

Only checked promises count: the promise must be verified and documented, and so must
every piece of evidence for a decided promise. Unchecked ones are listed with an
"Unverified" badge and a note that they aren't counted yet. There are no letter grades.

The tracker is for information only and does not change the match percentage.

## Election results

After the polls close we show vote counts for every race and proposition on the ballots we cover, on a
Results page for each county and at the top of each race page.

- **Official sources only.** Every count comes from an official results report: the county elections
  office, or the Secretary of State for statewide and district races. Each count links to its report.
- **Unofficial, then official.** Election-night counts are marked *Unofficial* and can change as mail,
  provisional, and corrected ballots are added. When the county canvasses the votes, we replace them with
  the canvassed counts and mark them *Official*.
- **No winner before the canvass.** We mark a candidate elected (or headed to a runoff), or a proposition
  passed or failed, only from the official canvass. Before then we show the numbers and nothing more.
- **Same order as everywhere else.** Candidates stay in alphabetical order, never sorted by votes, and no
  party colors are used.
- **Partial counts are labeled.** A race that crosses county lines may show one county's count; the page
  says which voters it covers.
- **Unverified until checked.** Like every other fact, a result shows an *Unverified* badge until we have
  checked it against its report.

## Corrections
Found an error? [Submit a correction](CORRECTIONS_FORM_URL). Every reviewed correction is
logged publicly with the date, what changed, why, and who reviewed it.

Reports are private. Only the project owner reads them. We store only what you type and
the time it arrived: no IP address, no cookies, no tracking. Contact details are optional.
