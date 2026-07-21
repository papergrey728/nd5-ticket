# なにわ男子 ND⁵ チケット価格トラッカー

Tracks resale ticket prices for the なにわ男子 "ND⁵" tour on **ticketjam.jp**
and **ticketen.jp**, once a day, segmented by concert date, by listing
category, and by sale status — and keeps a running dashboard of the trend.

## What's in this folder

| File | Purpose |
|---|---|
| `track_prices.py` | Run this daily. Fetches both sites, logs today's prices segmented by date/category/status, regenerates the dashboard. |
| `price_history.json` | The running log (one entry per day). Created automatically. |
| `dashboard.html` | Open this in a browser any time to see the charts and tables. Regenerated every run. |

Right now `dashboard.html` and `price_history.json` contain **randomly
generated sample data** so you can see what it looks like. The first real run
will replace it with actual data for today.

## 1. Requirements

- Python 3.9+ (no extra libraries needed — it only uses the standard library)
- Check with: `python3 --version`

## 2. Run it manually first

```bash
cd ticket_tracker
python3 track_prices.py
```

Expect this to take **1-2 minutes** — ticketen alone makes one API call per
tour date (42 of them), with a short pause between each to stay polite to
the site. You should see something like:

```
Tracking prices for 2026-07-16...
  チケジャム: 238 on sale (0 sold) across 39 dates, overall min=22000, median=70000, avg=68901
  ! チケジャム: no listings found for 3 expected date(s): 2026-09-20 13:00, 2026-09-21 14:00, 2026-10-11 13:00
  チケテン: 620 on sale (480 sold) across 42 dates, overall min=10350, median=45000, avg=46800

Saved to price_history.json
Dashboard updated: dashboard.html
```

A "missing date" warning isn't necessarily an error — it usually just means
nobody has a ticket listed for that date yet. See **Troubleshooting** if you
see many dates missing.

Then open `dashboard.html` in your browser to see it.

## 3. Automating it: recommended (runs in the cloud, works even when your PC is off)

Since a local schedule (cron / Task Scheduler) only fires while your computer
is on, the better option is to have **GitHub run the script for you every
day** and publish the dashboard to a URL you can open from any device — your
PC doesn't need to be on at all once this is set up.

This uses two free GitHub features: **GitHub Actions** (runs your script on
a schedule, in the cloud) and **GitHub Pages** (hosts `dashboard.html` at a
public URL). Both are free and unlimited for public repositories.

### One-time setup

**1. Create a free GitHub account** at [github.com](https://github.com) if
you don't already have one.

**2. Create a new repository**
- Click the **+** in the top-right corner → **New repository**
- Name it something like `nd5-ticket-tracker`
- Set visibility to **Public** (this is what makes Actions/Pages free —
  private repos have limited free minutes)
- Click **Create repository**

**3. Upload all the files from this folder**, keeping the folder structure
intact — this includes the hidden `.github/workflows/daily.yml` file, so
drag-and-drop the *whole* `ticket_tracker` folder contents rather than
individual files one at a time:
- On the new repo's page, click **Add file → Upload files**
- Drag in everything: `track_prices.py`, `dashboard.html`,
  `price_history.json`, `README.md`, `index.html`, and the `.github` folder
  (modern GitHub supports dragging folders directly, so the `.github/workflows/daily.yml`
  path is preserved automatically)
- Commit the upload

**4. Give the workflow permission to save its results**
- Go to **Settings → Actions → General**
- Under **Workflow permissions**, select **Read and write permissions**
- Click **Save**

(Without this, the daily job can fetch prices but can't save the updated
dashboard back to your repo — it needs write access to commit.)

**5. Enable GitHub Pages**
- Go to **Settings → Pages**
- Under **Build and deployment → Source**, choose **Deploy from a branch**
- Branch: `main`, folder: `/ (root)` → **Save**
- After ~1 minute, GitHub shows your site's URL at the top of that page —
  it'll look like `https://yourusername.github.io/nd5-ticket-tracker/`

**6. Test it manually before waiting for the schedule**
- Go to the **Actions** tab → click **Daily Ticket Price Tracker** on the
  left → click **Run workflow** (this manually triggers it — you don't have
  to wait until the scheduled time)
- Watch it run (takes 1-2 minutes). Click into the run to see the same
  console output you'd see running it locally
- If it succeeds, check your repo — `dashboard.html` and `price_history.json`
  should show a new commit from "github-actions[bot]"
- Visit your Pages URL from step 5 to see the live dashboard

Once this works, it runs **automatically every day at 9:00 AM JST** (00:00
UTC) without you doing anything — just check the URL whenever you want.

**⚠️ Important thing to verify on that first test run**: this conversation
already ran into ticketjam/ticketen occasionally serving bot-detection
challenge pages to automated requests (see Troubleshooting below). Requests
from GitHub's cloud servers come from well-known datacenter IP ranges, which
*can* be treated more suspiciously by anti-bot systems than a home
connection. Watch your first few scheduled runs' logs (Actions tab → run →
"Run tracker" step) to make sure real data is coming back, not just
zero-listing warnings site-wide. If cloud runs get blocked consistently
while local runs work fine, that's a sign this specific limitation applies
here, and running locally (see below) may be the more reliable option
despite requiring your PC to be on.

### Changing the schedule time

Cron schedules in GitHub Actions are always in **UTC**. The default
(`0 0 * * *`) is 00:00 UTC = 9:00 AM JST. To change it, edit the `cron:`
line in `.github/workflows/daily.yml` (e.g. `0 15 * * *` = 00:00 JST /
midnight).

### A note on repository inactivity

GitHub automatically disables scheduled workflows if a repository goes 60
days with no commits at all. Since this workflow commits an update every
day it runs, that resets the clock on its own — no action needed as long as
it's running successfully.

## 4. Alternative: running it locally instead

If you'd rather not use GitHub, or just want to test locally first, you can
still schedule it directly on your own machine — the tradeoff is that
`dashboard.html` only updates while your computer is on at the scheduled
time, and you view it by opening the file locally rather than a URL.

### macOS / Linux (cron)

1. Open your crontab: `crontab -e`
2. Add a line to run it every day at 9am (adjust the path to where you saved this folder):

```
0 9 * * * cd /full/path/to/ticket_tracker && /usr/bin/python3 track_prices.py >> run.log 2>&1
```

### Windows (Task Scheduler)

1. Open **Task Scheduler** → **Create Basic Task**
2. Trigger: Daily, pick a time
3. Action: **Start a program**
   - Program/script: `python`
   - Add arguments: `track_prices.py`
   - Start in: the full path to this `ticket_tracker` folder

Once scheduled, just re-open `dashboard.html` in your browser whenever you want
to check the latest trend — no need to re-run anything manually.

## 5. How each site is fetched

**ticketjam** — no API available, so this scrapes the rendered HTML across
all 3 listing pages (`event_groups/320224`, `?page=2`, `?page=3`) using
regex-based extraction. ticketjam never displays sold-out listings.

**ticketen** — uses ticketen's own internal JSON API directly
(`ticketen.jp/api/tickets/all`), found via browser DevTools rather than
scraping HTML. This is a much cleaner data source:
- One stable `eventId` covers the whole tour; a specific concert is
  selected via `date` and `startTime` query parameters — no need for the
  42 different per-date page URLs an earlier version of this script used
- Each ticket has an explicit `status` field (`"active"` or `"sold"`) — no
  more guessing sold-status from a 売り切れ text marker
- Pagination is clean `offset`/`limit`/`hasMore` fields, so **all** listings
  for every date are captured — no "load more" truncation like the HTML
  version had
- The `description` field is used directly for category classification —
  no need to guess where a listing's text starts and ends

If ticketen ever changes this API (unlikely, but possible), you'd need to
re-discover it: open a date page in Chrome, F12 → Network tab → filter to
Fetch/XHR, scroll to trigger "load more", and look for a request to
`/api/tickets/all`. Update `TICKETEN_EVENT_ID` / `TICKETEN_API_URL` near the
top of `track_prices.py` if anything changes.

## 6. What the tracker measures

**Segmentation**
1. **Overall** — min / median / average / count across all on-sale listings
2. **Per concert date** — same stats, broken out for each of the 42 tour
   dates (see `EXPECTED_DATES` in the script)
3. **Per category** — each listing is classified using its description text
   (both sites) and, for ticketen, its `tags` array as well (some tags like
   同行 only show up as a tag, not in the free-text description — combining
   both catches more accurately than either alone). Rules are single-keyword
   checks in strict priority order — first match wins, so a listing matching
   multiple keywords only ever lands in the highest-priority one:
   1. **ランダム**: contains ランダム
   2. **番手**: contains 番手
   3. **同行**: contains 同行
   4. **QR**: contains QRごと or QR毎
   5. **立見**: contains 立ち見 or 立見
   6. **一般**: contains 一般
   7. **制作開放席**: contains 制作開放席
   8. **Others**: anything that doesn't match the above

   Computed both overall and per concert date, and — for ticketen — also
   per sale status (on-sale vs sold), so the dashboard's category filter
   works whichever status you're looking at. Category stats otherwise only
   include
   **on-sale** listings by default.
4. **Sale status** — ticketen tracks both `active` and `sold` tickets
   cleanly via the API's `status` field. ticketjam never shows sold-out
   listings at all, so it has no sold-side data.

**Completeness check**
After each run, the script compares the dates it found against the 42 known
tour dates and prints a warning (not an error — the run still completes) for
any date with zero listings on either site.

**Past concerts**
As the tour progresses, dates that have already happened are automatically
excluded from:
- **ticketen fetching** — no API calls are made for past dates at all (this
  also speeds up the run and reduces load on the site over time)
- **The missing-date warning** — a past date with no listings isn't a
  problem, so it won't show up in the `! ... no listings found for...` line
- **The dashboard** — past dates are labeled "(終了)" and shown dimmed/greyed
  out in the per-date dropdown and the snapshot table, so they read as
  "concert happened" rather than "something's wrong with the data"

A concert's own day still counts as "upcoming" (not past) — the cutoff is
based on comparing today's date to each concert's date, so a show doesn't
get excluded until the day after it happens. ticketjam still fetches all 3
pages regardless of past/upcoming (there's no per-date fetch to skip there),
but past dates are still excluded from its missing-date warning.

Historical price data for a past concert isn't deleted — everything
recorded in `price_history.json` while it was still upcoming stays intact,
you just won't get new data points for it going forward.

## 7. What's on the dashboard

- **Top summary cards** — ticketjam and ticketen's current **median** price
  (not the cheapest listing), with count / min / average as supporting
  detail underneath. ticketen's card also shows a sold-listings summary.
- **全体推移** (overall trend) and **公演日別トレンド** (per-date trend)
  charts — both plot **median** price over time, not min. Median is less
  swayed by a single unusually cheap or expensive outlier listing, so it
  tracks the "typical" price better day to day.
- **選択した公演日のカテゴリー別内訳** — category breakdown for whichever
  date is selected in the per-date trend dropdown above it.
- **公演日別 最新スナップショット** — the main per-date table, with two
  filters above it:
  - **Category filter** — narrow the table to one category (ランダム, 番手,
    同行, QR, 立見, 一般, 制作開放席) or view all combined
  - **Status filter** — 出品中のみ (on-sale only, default), 売り切れのみ
    (sold only — ticketen only, since ticketjam has no sold data), or
    出品中＋売り切れ 両方 (both side by side)

  These two filters combine — e.g. "番手" + "売り切れのみ" shows only
  sold 番手 listings per date.
- **カテゴリー別 最新スナップショット** — category breakdown combined
  across all dates (on-sale only), for a tour-wide view.

## 8. Things to keep in mind

- These are resale marketplaces — prices reflect what individual sellers are
  asking, not face value, and can include per-listing fees or conditions not
  captured here.
- Running this daily makes roughly 45 requests total (3 to ticketjam, ~42+
  to ticketen depending on pagination), each spaced out with a short pause.
  Be mindful of each site's terms of service if you increase frequency.
- The 42 expected tour dates are hardcoded as of today (July 2026) in
  `EXPECTED_DATES`. If STARTO announces additional dates or cancels any,
  update that list — ticketen's API doesn't need anything else changed since
  it just takes date/time as parameters; ticketjam should pick up new dates
  automatically as long as they appear somewhere in its 3 pages.
- If either site redesigns their page or API, the relevant patterns/fields
  in `track_prices.py` will need updating to match.

## Troubleshooting

**Run with `--debug` to see what's actually happening:**

```bash
python3 track_prices.py --debug
```

This saves every raw response to a `debug/` folder — HTML pages for
ticketjam (`debug/ticketjam_N.html`), JSON responses for ticketen
(`debug/ticketen_<date>_offset<N>.json`) — and prints extra diagnostics like
how many date/price pattern matches were found.

**ticketjam: "0 listings found" but the script ran without errors**
The site likely changed its HTML structure. Open a `debug/ticketjam_N.html`
file, find how a price is written now (e.g. `XX,XXX円`), and update the
`price_pattern` regex in `track_prices.py`.

**ticketen: errors or 0 listings across all dates**
Check a `debug/ticketen_*.json` file — if it's not valid JSON or doesn't
have a `"success": true` field, the API may have changed shape or moved.
Re-discover it via DevTools (see section 5 above) and update
`TICKETEN_EVENT_ID` / `TICKETEN_API_URL`.

**Bot-detection / blocked responses**
The script automatically detects common bot-blocking pages (Cloudflare
challenges, CAPTCHAs) and will print a clear message like:
`response looks like a bot-detection page (matched: 'Just a moment'), not the real site content`
If you see this, wait a while before retrying rather than increasing
request frequency.

**Many dates showing up as "missing"**
- For ticketjam: confirm there are still only 3 pages of results — if the
  tour becomes more popular, a 4th page might appear.
- For ticketen: this would be unusual given the API is queried per-date
  directly — check the debug JSON for that date to see what the API
  actually returned.

**GitHub Actions: workflow runs but nothing gets committed**
Check **Settings → Actions → General → Workflow permissions** is set to
"Read and write permissions" (see section 3, step 4) — without this, the
job can fetch data but can't push the update back to your repo, and the
"Commit and push" step will fail silently-ish (visible as a red X in the
Actions tab, but easy to miss if you're not looking).

**GitHub Actions: dashboard URL shows an old version**
GitHub Pages can take a minute or two to pick up a new commit. If it's been
longer than that, check **Settings → Pages** to confirm the source branch
is `main` and the deployment shows a recent timestamp.

**GitHub Actions: scheduled runs seem to have stopped**
Check the **Actions** tab for a red X on recent runs — GitHub emails you
automatically when a scheduled workflow fails, so check that inbox too.
Also confirm the repo hasn't gone fully inactive (see "A note on repository
inactivity" in section 3).
