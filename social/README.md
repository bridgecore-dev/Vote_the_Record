# Social posts

Instagram posts for Vote the Record. Same rules as the site (CLAUDE.md): official sources first, every post
cites its sources, neutral wording, party only as printed on a ballot, never infer past what a source says.

- `posts/<YYYY-MM-DD>-<slug>.yaml` — one file per post (format below). The file name is the post's ID.
  **Every file here on `main` is published** (src/social/publish.py) once its `publish_after` passes, so a
  post reaches `posts/` only through its own pull request, where merging it is the approval.
- `examples/` — sample posts for trying the builder; never published.
- `out/<post>/` — built files (not committed): `slide-NN.png`, `story.png`, `reel.mp4` + `cover.png`, `manifest.json`.
- `candidates/` — researched facts waiting to be picked.
- `design/` — the original style studies and the profile picture.

Build (preview first with `--check`):

    python src/social/build.py --check social/posts/<post>.yaml
    python src/social/build.py social/posts/<post>.yaml

Needs Chrome (renders slides) and, for Reels, ffmpeg.

## Post file

```yaml
kind: image            # image | carousel (2-10 slides) | story (1-10, 1080x1920) | reel (2-20, 1080x1920 MP4)
level: state           # congress | state | local
place: Texas           # state, or "Harris County, Texas" / "Houston, Texas"; omit for congress
topic: Calendar        # free text; drives highlights later
approval: auto         # auto: publishes without the owner's tap; review: waits for it
layout: card           # optional: card | band | stat; rotates by post name when omitted
story: true            # image only: also render story.png from the first slide
sources:               # at least one; each needs title, url, retrieved
- {title: Texas Legislative Council, Dates of Interest, url: https://tlc.texas.gov/..., retrieved: '2026-11-10'}
slides:                # each needs head or big; the rest is optional
- chip: Water                       # small rounded label
  kicker: Mark the calendar         # small caps line
  big: Jan. 12                      # the big number, date, or bill ID
  head: The 90th Texas Legislature convenes at noon on Jan. 12, 2027.
  text: One or two plain sentences.
  quote: {label: Official caption, text: exact words from the source}
  by: "Author: Walle · Filed Nov. 9, 2026"
  seconds: 4                        # reel only; default 4
caption: |-            # the post text; sources and hashtags are added below it automatically
  Two months to go. ...
hashtags: [txlege, texas]           # at most 5, without '#'
record_ids: [tx/90R/calendar/convenes]   # what this post covers; the builder refuses an ID another post has
publish_after: '2026-11-14T08:00:00-06:00'  # optional; ISO date-time with a time zone
found_via: https://news.example/story      # optional; the news item that led us here. Reviewer only: never
                                           # printed, never a source, and it forces approval: review
rule: How items were picked, when the post is a selection (also say it on a slide).
notes: Anything the reviewer should know; not published.
```

**Approval.** `auto` is only for routine items: calendar dates and official tallies from official feeds. The
builder refuses `auto` unless every source URL is on a `.gov`, `.us`, or `.mil` domain. Anything resting on
news, or any judgment call (picking "notable" bills, a summary in our words), uses `review`.

**Record IDs.** `<jurisdiction>/<session or year>/<record>/<event>`, lower-case jurisdiction, the record as
officially written: `tx/90R/HB 101/filed`, `tx/90R/HB 101/house-passed`, `us/119/house/roll/316`,
`us/119/public-law/138`, `tx/90R/calendar/convenes`. The event part lets a bill's later actions post again;
the same event never does. A recap post lists every record it covers.

**Words.** Quote official text exactly in `quote`; our own words go in `head`/`text` and must not go beyond
the source. Same wording and structure for every person. Never name or imply a party.

## manifest.json

Written next to the files for the publishing step:

```json
{"post": "2026-11-13-texas-session-date", "kind": "image", "level": "state", "place": "Texas",
 "topic": "Calendar", "layout": "stat", "approval": "auto",
 "files": [{"file": "slide-01.png", "alt_text": "..."}, {"file": "story.png", "alt_text": "...", "story": true}],
 "caption": "post text\n\nSource:\nTitle: URL\n\n#tags", "sources": [...],
 "publish_after": "2026-11-14T08:00:00-06:00", "record_ids": ["tx/90R/calendar/convenes"], "found_via": null}
```

`files` is in posting order. A Reel's entry is `{"file": "reel.mp4", "cover": "cover.png", "alt_text": ...}`.
Music, interactive stickers, and GIFs are added in the Instagram app; the Reel has a silent audio track.
