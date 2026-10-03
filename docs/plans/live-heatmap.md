# Plan: heatmaps from the live web app

Status: draft, 2026-09-30

## Goal

Studies in UX Testbench measure how recruited participants behave on a prototype or a
screenshot. That is good evidence for choosing between designs. It is weak evidence that a
shipped change actually helped the people who use the real product.

This plan closes that gap. A small script on the live web app (CIVD first) sends real users'
clicks and scroll depth to UX Testbench. The existing heatmap renderer draws them over a
screenshot of the page, and a comparison view puts one release next to another: where people
clicked, where they clicked on something that does nothing, how far they scrolled. Everything
is collected and stored on our own instance. No third-party analytics vendor sees the traffic.

The result a researcher should be able to hand to a product owner is: "Before release 2026.10,
38% of first clicks on the events page landed on the search box and 12% were dead clicks on the
date label. After it, 61% went to the new date filter and dead clicks fell to 3% (n = 1,840 and
2,115 visits)."

## What already exists and is reused

Most of the pipeline is already built for the first-click module. The new work is the
collector at the front and the comparison view at the back.

| Piece | Where | Reused as |
| --- | --- | --- |
| Density grid, blur, clipping, `min_n` rule | `testbench/heatmap.py` | Unchanged. `panel()` takes any list of points with a `session_id`, so a visit id fits |
| Device split and layout-drift exclusion | `heatmap.buckets`, `MOBILE_MAX_W`, `LAYOUT_TOLERANCE` | Unchanged. Matters more on live pages, where widths vary far more than in a study |
| Canvas renderer, Heat/Dots/Image controls, legend | `static/heatmap.js`, `modules/_heatmap.html` | Unchanged, pointed at a page screenshot instead of a task image |
| Click validation and clamping | `storage.clean_click` | Unchanged, plus two fields (`selector`, `rage`) |
| Rate limiting shared across workers | `testbench/rate_limit.py` | New context `collect`, keyed by site key and IP |
| Nightly housekeeping | `python -m testbench retention` | Extended to drop raw live clicks past their retention window |
| Bootstrapped confidence intervals | `docs/statistics.md` | Used for the before/after rates |

## How it works

```
CIVD page ──<script src=".../t.js" data-site="civd">──► collector POST /collect/civd
   click, scroll, page path, doc size, viewport,              │  validate, sample, rate-limit
   release tag, random visit id (sessionStorage)              ▼
                                                        project DB: web_visits, web_clicks
                                                               │
                         screenshot per page × device × release ◄── upload or capture job
                                                               ▼
                                   Live tab in the study: heatmap per page, release vs release
```

### 1. The snippet (`static/t.js`)

One script tag in the live app's layout:

```html
<script src="https://testbench.example.org/t.js" data-site="civd" data-release="2026.10" defer></script>
```

What it records, per click: `x`/`y` relative to the document (`pageX`/`pageY`), document
width and height, viewport width and height, time since page load, the page path, and a short
element descriptor (tag plus `id` or `data-testid`, never the element's text, never anything
typed into a field). Per page view it records maximum scroll depth.

It also classifies two kinds of click that are the strongest single signal of a confusing UI:

- **Dead click**: the target is not interactive (no link, button, input, label or click handler
  on it or an ancestor) and nothing on the page changes within 700 ms.
- **Rage click**: three or more clicks within one second inside a 30 px radius.

It batches events and flushes with `navigator.sendBeacon` on `visibilitychange` and every 10
seconds, sending `text/plain` so the browser needs no CORS preflight. The visit id is random
and lives in `sessionStorage`, so it ends when the tab closes and cannot follow a person across
days. The script is plain JavaScript with no dependencies, targeting under 3 KB gzipped.

It does nothing when `navigator.globalPrivacyControl` is set or Do Not Track is on, and nothing
until consent is given when the tag carries `data-consent="required"` (the app calls
`window.tbConsent(true)` from its existing cookie banner).

For single-page apps it listens to `history.pushState`/`popstate` and treats each route change
as a new page view.

### 2. The collector (`POST /collect/<site>`)

This is the only endpoint in the app that accepts cross-origin requests, so it is kept
deliberately narrow:

- **Origin allowlist per site.** A request whose `Origin` is not listed for that site key is
  dropped with 204. The key alone is public (it is in the page source), so it is not a secret
  and must not act like one.
- **Size and count caps.** At most 16 KB per request and `storage.CLICKS_MAX` clicks per visit
  and page. Anything invalid is dropped silently rather than raising an error.
- **Sampling.** `sample: 0.25` in config keeps a quarter of visits, decided once per visit id
  so a visit is either fully in or fully out.
- **Rate limit.** Per IP and site key through `rate_limit.py`. The IP is used for that check
  only and is never written to the project database.
- **No cookies** are read or set, and the response carries no body.

### 3. Storage

Live traffic has no participant or study session, so it gets its own tables in the project's
SQLite file (consistent with ADR 003, one database per project) instead of stretching
`sessions`:

| Table | Columns | Notes |
| --- | --- | --- |
| `web_pages` | `id`, `site`, `page_key`, `name` | `page_key` is the normalised path, e.g. `/events/:id` |
| `web_visits` | `id` (random), `site`, `release`, `started_at`, `device`, `sampled` | No IP, no user agent string, no user id |
| `web_clicks` | `visit_id`, `page_id`, `seq`, `x`, `y`, `doc_w`, `doc_h`, `viewport_w`, `viewport_h`, `t_ms`, `dead`, `rage`, `selector`, `created_at` | Same shape as `clicks`, plus three columns |
| `web_scroll` | `visit_id`, `page_id`, `max_depth_pct` | One row per page view |
| `web_shots` | `page_id`, `device`, `release`, `path`, `doc_w`, `doc_h` | The background image for a heatmap |

Rough volume for CIVD at 10,000 visits a day, 25% sampling and five clicks per visit is
12,500 rows a day. SQLite in WAL mode handles that without effort. If a report over 90 days
becomes slow, a nightly job stores the finished grid per page, device and release, and the raw
rows past the window are deleted.

### 4. Pages and releases (YAML)

The live site is configured in the project like any other module, so it stays in the Studio,
under version control and validated by `python -m testbench check` (ADR 004):

```yaml
type: live_heatmap
title: CIVD live usage
site: civd
origins: [https://civd.example.go.id]
sample: 0.25
retention_days: 90
pages:
  - match: /
    name: Home
  - match: /events/*
    name: Event detail
  - match: /search
    name: Search results
    keep_query: [q]          # every other query parameter is stripped before storage
```

A click on a path that matches no rule is counted, not stored, so the report can say how much
traffic falls outside the configured pages without keeping URLs nobody asked for.

The **release** is the unit of before/after comparison. It comes from `data-release` on the
script tag, which the app's build sets from its version or deploy tag. It does not have to be
a version number. Anything the team uses to name what was shipped works.

### 5. Screenshots

A heatmap is only readable over the page it was recorded on, and a live page cannot be read
from another origin. Two sources, in the order they will be built:

1. **Upload.** In the Studio, per page, device and release. Always works, including for pages
   behind a login.
2. **Capture job.** `python -m testbench capture civd --release 2026.10` opens each public
   configured page in headless Chromium (Playwright is already a dev dependency) at 1440 px
   and 390 px and saves full-page screenshots. Runs from CI after a deploy.

`heatmap.buckets` already excludes clicks recorded on a document width more than 10% away from
the median. On a live page it will also exclude clicks from a document whose height differs
greatly (a long search result page against a short one). Those are reported as excluded, never
rescaled onto the wrong layout.

### 6. The report

A **Live** tab in the study results, one section per configured page:

- The heatmap for the chosen release and device, with the existing Heat, Dots and Image only
  controls.
- **Release A vs release B**, side by side, on the same device.
- A table of the elements that got the most clicks, by `selector`. This is the part that
  survives a redesign that moves things around, where pixel positions stop being comparable.
- Rates with 95% bootstrapped intervals: dead-click rate, rage-click rate, share of first
  clicks on the element the researcher marks as the intended target, and median scroll depth.
  Each shows n visits.
- The same five-visit minimum the first-click report uses, applied per page, device and
  release.
- Export: CSV of the aggregates and PNG of each panel, for slides and reports.

The comparison is observational. Traffic mix, season and campaigns change between releases,
and the report says so under every comparison. When a change needs causal evidence, the
existing A/B module is the tool, and the Live tab links to it.

## Privacy and compliance

The live app's users have not signed up for a study, which raises the bar compared with
participants who consented to one. The plan is built to fit Indonesia's UU PDP (Law 27/2022)
and the equivalent GDPR principles:

- **Consent:** the snippet respects the app's cookie banner through `data-consent="required"`,
  and GPC/DNT always.
- **Data minimisation:** no text content, no input values, no IP, no user id, no persistent
  identifier. Query strings are stripped unless explicitly kept.
- **Retention:** raw clicks are deleted after `retention_days` (default 90); aggregated grids
  may be kept longer because they hold no per-visit data.
- **Transparency:** CIVD's privacy notice names the collection, its purpose (improving the
  interface) and the retention period. A draft paragraph is part of phase 1.
- **Masking hook:** any element with `data-tb-ignore` is not recorded at all. Use it on pages
  that show personal data.

## Phases

| Phase | Deliverable | Rough effort |
| --- | --- | --- |
| **1. Collect** | `t.js`, `/collect`, the `live_heatmap` module type with YAML validation, tables, origin allowlist, sampling, rate limit, consent/GPC handling, retention. Tests for validation and every drop path | 4–5 days |
| **2. See** | Screenshot upload in the Studio, the Live tab with one heatmap per page and device, element table, scroll depth | 3–4 days |
| **3. Compare** | Release selector, side-by-side view, rates with intervals, CSV and PNG export | 3 days |
| **4. Automate** | Playwright capture job and CI example, nightly aggregation of old grids | 2 days |
| **Pilot** | Deploy on CIVD staging with 100% sampling, check the numbers against a known test session, then production at 25% | 1 week of elapsed time |

Phase 1 is deployable on its own: it starts collecting a baseline before any report exists,
which is exactly what the first before/after comparison needs.

## Open questions

1. **CIVD's front end.** Server-rendered pages or a single-page app? It decides whether route
   changes need the `pushState` hook, and how the release tag gets into the script tag.
2. **Hosting.** The collector must be reachable over HTTPS from CIVD users' browsers. Is the
   testbench instance public, or does the collector need its own public host in front of it?
3. **CIVD's Content Security Policy** must allow our host in `script-src` and `connect-src`.
   Who owns that change?
4. **Pages behind a login.** Which of them matter most? They need uploaded screenshots, and
   probably `data-tb-ignore` on personal data.
5. **Traffic volume.** A real daily visit count sets the sampling rate. 25% is a guess.
6. **Legal review.** Who signs off the privacy notice text before production?

## Not in scope

- Session replay. Recording the DOM of real users' sessions is a much larger privacy
  commitment and is not needed for heatmap evidence.
- Importing data from Hotjar, Clarity or PostHog. It can be added later as another source
  for the same tables if a team already has years of history there.
- Funnels and conversion analytics. Product analytics tools already do this well.
