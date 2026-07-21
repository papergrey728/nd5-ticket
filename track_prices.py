#!/usr/bin/env python3
"""
Naniwa Danshi (ND5 tour) ticket price tracker
Fetches current resale listing prices from ticketjam.jp (3 pages) and
ticketen.jp, and logs a daily summary segmented multiple ways:

  1. Overall (all listings)
  2. Per concert date (all 42 tour dates)
  3. Per category (FC Bante / FC Random / FC QR / Ippan Bante / Ippan /
     Other) based on each listing's description text — computed per
     concert date
  4. Per sale status (on sale / sold) — ticketen only, since ticketjam does
     not display sold-out listings

After each run, the script checks the scraped dates against the known list
of 42 tour dates and reports any that are missing, so nothing silently
falls through the cracks.

Regenerates a self-contained HTML dashboard each run.
Run this once a day (see README.md for scheduling instructions).
"""

import argparse
import bisect
import json
import re
import statistics
import sys
import time
from datetime import datetime, date
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode

# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------

CATEGORY_ORDER = ["ランダム", "番手", "同行", "QR", "立見", "一般", "制作開放席", "Others"]


def categorize(text: str) -> str:
    """Classify a listing based on keywords in its description text (and,
    for ticketen, its tag labels — both are combined into one string before
    being passed here). Checked in priority order; first match wins."""
    if "ランダム" in text:
        return "ランダム"
    if "番手" in text:
        return "番手"
    if "同行" in text:
        return "同行"
    if ("QRごと" in text) or ("QR毎" in text):
        return "QR"
    if ("立ち見" in text) or ("立見" in text):
        return "立見"
    if "一般" in text:
        return "一般"
    if "制作開放席" in text:
        return "制作開放席"
    return "Others"


# The 42 confirmed ND5 tour dates (as of writing) between now and Oct 28,
# 2026, in the same "YYYY-MM-DD HH:MM" key format used elsewhere.
# Update this list if the tour schedule changes.
EXPECTED_DATES = [
    "2026-07-18 13:30", "2026-07-18 18:00",
    "2026-07-19 13:00", "2026-07-19 17:30",
    "2026-07-28 18:00",
    "2026-07-29 13:30", "2026-07-29 18:00",
    "2026-08-05 13:00", "2026-08-05 18:00",
    "2026-08-06 13:00", "2026-08-06 18:00",
    "2026-08-07 13:00", "2026-08-07 18:00",
    "2026-08-15 13:30", "2026-08-15 18:00",
    "2026-08-16 13:00", "2026-08-16 17:30",
    "2026-08-25 18:00",
    "2026-08-26 13:30", "2026-08-26 18:00",
    "2026-09-05 13:30", "2026-09-05 18:00",
    "2026-09-06 13:00", "2026-09-06 17:30",
    "2026-09-19 13:30", "2026-09-19 18:00",
    "2026-09-20 13:00", "2026-09-20 17:30",
    "2026-09-21 14:00",
    "2026-10-10 13:30", "2026-10-10 18:00",
    "2026-10-11 13:00", "2026-10-11 17:30",
    "2026-10-12 14:00",
    "2026-10-24 13:00", "2026-10-24 18:00",
    "2026-10-25 12:30", "2026-10-25 17:30",
    "2026-10-26 18:00",
    "2026-10-27 13:00", "2026-10-27 18:00",
    "2026-10-28 15:00",
]
assert len(EXPECTED_DATES) == 42


def parse_date_key(date_key: str) -> date:
    """'2026-07-18 13:30' -> date(2026, 7, 18) (drops the time — we only
    need day-level granularity to decide if a concert has already happened)."""
    y, m, d = map(int, date_key.split(" ")[0].split("-"))
    return date(y, m, d)


def split_past_dates(today: date) -> tuple:
    """Split EXPECTED_DATES into (past, upcoming) relative to today. A
    concert's own day still counts as "upcoming" (not past) — ticket
    transactions can still happen right up to showtime, and this avoids any
    timezone-precision issues around exactly when a show "ends"."""
    past = [d for d in EXPECTED_DATES if parse_date_key(d) < today]
    upcoming = [d for d in EXPECTED_DATES if parse_date_key(d) >= today]
    return past, upcoming


# ticketen's real ticket data comes from a JSON API (discovered via browser
# DevTools — the rendered HTML pages only show a "load more"-limited subset).
# One stable eventId covers the whole tour; a specific concert is selected
# via the `date` and `startTime` query params, so we don't need per-date URL
# codes at all. If STARTO adds a new performer page for a different event,
# find the new eventId the same way: DevTools → Network → XHR while loading
# any date page for that event, look for a request to /api/tickets/all.
TICKETEN_EVENT_ID = "oi04S6aJMUNBTATTvUxn"
TICKETEN_API_URL = "https://ticketen.jp/api/tickets/all"
TICKETEN_API_PAGE_SIZE = 50

TICKETJAM_LABEL = "チケジャム"
TICKETEN_LABEL = "チケテン"

SITES = {
    "ticketjam": {
        "label": TICKETJAM_LABEL,
        "urls": [
            "https://ticketjam.jp/tickets/naniwa-dannshi/event_groups/320224",
            "https://ticketjam.jp/tickets/naniwa-dannshi/event_groups/320224?page=2",
            "https://ticketjam.jp/tickets/naniwa-dannshi/event_groups/320224?page=3",
        ],
        # e.g. "23,000 円/枚"
        "price_pattern": re.compile(r"([\d,]{3,})\s*円/枚"),
        # e.g. "2026/08/06(木) 13:00"
        "date_pattern": re.compile(r"(\d{4}/\d{2}/\d{2})\([月火水木金土日]\)\s*(\d{1,2}:\d{2})"),
        "date_formatter": lambda m: f"{m.group(1).replace('/', '-')} {m.group(2)}",
        # ticketjam lists the description BEFORE the price
        "description_direction": "before_price",
        # ticketjam never shows sold-out listings
        "sold_marker": None,
    },
}

DATA_DIR = Path(__file__).parent
HISTORY_FILE = DATA_DIR / "price_history.json"
DASHBOARD_FILE = DATA_DIR / "dashboard.html"
DEBUG_DIR = DATA_DIR / "debug"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# A plain User-Agent header alone is often enough to get flagged as a bot by
# sites with anti-scraping protection (Cloudflare, etc). These additional
# headers make the request look more like an actual browser tab.
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Strings that suggest the response was a bot-detection / challenge page
# rather than the real site content — used to give a clearer error message
# than "0 listings found".
BLOCK_INDICATORS = [
    "Just a moment", "cf-browser-verification", "Attention Required",
    "Access denied", "アクセスが集中", "自動アクセス", "captcha", "reCAPTCHA",
    "ロボットではありません",
]

MIN_REASONABLE_PRICE = 500       # filter out stray/garbage numbers
MAX_REASONABLE_PRICE = 500_000

TAG_BLOCK_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
WHITESPACE_RE = re.compile(r"\s+")

# ----------------------------------------------------------------------------
# SCRAPING
# ----------------------------------------------------------------------------


def fetch_html(url: str) -> str:
    req = Request(url, headers=REQUEST_HEADERS)
    with urlopen(req, timeout=20) as resp:
        raw = resp.read()
    try:
        html = raw.decode("utf-8")
    except UnicodeDecodeError:
        html = raw.decode("utf-8", errors="ignore")

    hit = next((marker for marker in BLOCK_INDICATORS if marker in html), None)
    if hit:
        raise RuntimeError(
            f"response looks like a bot-detection page (matched: {hit!r}), not the real site content"
        )
    return html


def html_to_text(html: str) -> str:
    """Flatten HTML to plain text, preserving document order, so regexes can
    find dates, prices, and description text in sequence regardless of the
    surrounding tags."""
    html = TAG_BLOCK_RE.sub(" ", html)
    text = TAG_RE.sub(" ", html)
    text = text.replace("&nbsp;", " ")
    text = WHITESPACE_RE.sub(" ", text)
    return text


def build_listings(text: str, cfg: dict) -> list:
    """Walk the flattened page text and reconstruct each individual listing:
    its concert date, price, sold status, and FC category — by using each
    price occurrence as an anchor and looking at the nearest date before it,
    plus a description window either before or after the price depending on
    how this site lays listings out."""
    date_matches = sorted(
        [(m.start(), m.end(), cfg["date_formatter"](m))
         for m in cfg["date_pattern"].finditer(text)],
        key=lambda d: d[0],
    )
    if not date_matches:
        return []
    date_starts = [d[0] for d in date_matches]

    price_matches = sorted(
        [(m.start(), m.end(), m.group(1)) for m in cfg["price_pattern"].finditer(text)],
        key=lambda p: p[0],
    )

    listings = []
    for p_start, p_end, raw_price in price_matches:
        try:
            price = int(raw_price.replace(",", ""))
        except ValueError:
            continue
        if not (MIN_REASONABLE_PRICE <= price <= MAX_REASONABLE_PRICE):
            continue

        idx = bisect.bisect_right(date_starts, p_start) - 1
        if idx < 0:
            continue
        date_key = date_matches[idx][2]

        if cfg["description_direction"] == "before_price":
            window_start = date_matches[idx][1]
            window_end = p_start
        else:  # after_price
            window_start = p_end
            window_end = date_matches[idx + 1][0] if idx + 1 < len(date_matches) else len(text)

        description = text[window_start:window_end]

        sold = bool(cfg["sold_marker"]) and (cfg["sold_marker"] in description)
        category = categorize(description)

        listings.append({
            "date": date_key,
            "price": price,
            "sold": sold,
            "category": category,
        })

    return listings


def summarize(prices: list) -> dict:
    if not prices:
        return {"min": None, "median": None, "avg": None, "count": 0}
    return {
        "min": min(prices),
        "median": round(statistics.median(prices)),
        "avg": round(statistics.mean(prices)),
        "count": len(prices),
    }


def aggregate_listings(listings: list, label: str, track_sold: bool,
                        past_dates: list = None, errors: list = None) -> dict:
    """Turn a flat list of {date, price, sold, category} listings into the
    overall/by_date/by_category/by_date_category summary shape used
    everywhere else in the script. Shared by both sites since they end up
    with the same listing shape despite very different fetch mechanisms."""
    empty = {"min": None, "median": None, "avg": None, "count": 0}
    past_dates = past_dates or []

    if not listings and errors:
        return {
            "overall": dict(empty, error="; ".join(errors)),
            "overall_sold": dict(empty) if track_sold else None,
            "by_date": {},
            "by_date_sold": {},
            "by_category": {},
            "by_date_category": {},
            "by_date_category_sold": {},
        }

    on_sale = [l for l in listings if not l["sold"]]
    sold = [l for l in listings if l["sold"]]

    overall = summarize([l["price"] for l in on_sale])
    overall_sold = summarize([l["price"] for l in sold]) if track_sold else None

    by_date_raw = {}
    by_date_sold_raw = {}
    for l in on_sale:
        by_date_raw.setdefault(l["date"], []).append(l["price"])
    for l in sold:
        by_date_sold_raw.setdefault(l["date"], []).append(l["price"])

    by_date = {dt: summarize(prices) for dt, prices in sorted(by_date_raw.items())}
    by_date_sold = {dt: summarize(prices) for dt, prices in sorted(by_date_sold_raw.items())}

    by_category_raw = {}
    for l in on_sale:
        by_category_raw.setdefault(l["category"], []).append(l["price"])
    by_category = {cat: summarize(by_category_raw.get(cat, [])) for cat in CATEGORY_ORDER}

    all_dates = sorted(set(by_date_raw.keys()) | set(by_date_sold_raw.keys()))

    by_date_category_raw = {}
    for l in on_sale:
        by_date_category_raw.setdefault(l["date"], {}).setdefault(l["category"], []).append(l["price"])
    by_date_category = {
        dt: {cat: summarize(by_date_category_raw.get(dt, {}).get(cat, [])) for cat in CATEGORY_ORDER}
        for dt in all_dates
    }

    by_date_category_sold_raw = {}
    for l in sold:
        by_date_category_sold_raw.setdefault(l["date"], {}).setdefault(l["category"], []).append(l["price"])
    by_date_category_sold = {
        dt: {cat: summarize(by_date_category_sold_raw.get(dt, {}).get(cat, [])) for cat in CATEGORY_ORDER}
        for dt in all_dates
    } if track_sold else {}

    print(f"  {label}: {len(on_sale)} on sale ({len(sold)} sold) across "
          f"{len(by_date)} dates, overall min={overall['min']}, "
          f"median={overall['median']}, avg={overall['avg']}")

    missing = sorted(
        set(EXPECTED_DATES) - set(by_date.keys()) - set(by_date_sold.keys()) - set(past_dates)
    )
    if missing:
        print(f"  ! {label}: no listings found for {len(missing)} expected date(s): "
              f"{', '.join(missing)}", file=sys.stderr)

    return {
        "overall": overall,
        "overall_sold": overall_sold,
        "by_date": by_date,
        "by_date_sold": by_date_sold,
        "by_category": by_category,
        "by_date_category": by_date_category,
        "by_date_category_sold": by_date_category_sold,
    }


def scrape_ticketjam(cfg: dict, debug: bool = False, past_dates: list = None) -> dict:
    """ticketjam has no JSON API available, so this scrapes the rendered
    HTML across all 3 listing pages using regex-based extraction. There's
    no per-date fetch to skip here (all 3 pages are fetched regardless),
    so past_dates is only used to quiet the missing-date warning."""
    listings = []
    errors = []

    for i, url in enumerate(cfg["urls"]):
        try:
            html = fetch_html(url)
        except (URLError, HTTPError, TimeoutError, RuntimeError) as exc:
            print(f"  ! {cfg['label']}: fetch failed for {url} ({exc})", file=sys.stderr)
            errors.append(str(exc))
            continue

        if debug:
            DEBUG_DIR.mkdir(exist_ok=True)
            debug_path = DEBUG_DIR / f"ticketjam_{i}.html"
            debug_path.write_text(html, encoding="utf-8")
            print(f"  [debug] saved raw response ({len(html):,} chars) to {debug_path}")

        text = html_to_text(html)
        page_listings = build_listings(text, cfg)
        listings.extend(page_listings)

        if debug and not page_listings:
            date_hits = len(cfg["date_pattern"].findall(text))
            price_hits = len(cfg["price_pattern"].findall(text))
            print(f"  [debug] {url}: 0 listings parsed from {len(text):,} chars of text "
                  f"(date pattern matches: {date_hits}, price pattern matches: {price_hits}) "
                  f"— open the saved debug HTML file to see what was actually returned")

        if i < len(cfg["urls"]) - 1:
            time.sleep(1)  # polite pause between pages

    return aggregate_listings(listings, cfg["label"], track_sold=False,
                               past_dates=past_dates, errors=errors)


def scrape_ticketen(debug: bool = False, past_dates: list = None) -> dict:
    """ticketen's real ticket data comes from a JSON API (see TICKETEN_API_URL
    above) rather than rendered HTML — this queries it directly for every
    known tour date, paginating with offset/limit until each date is
    exhausted. This avoids the "load more" truncation the HTML version hit,
    and gives us a clean status field instead of guessing sold/on-sale from
    a text marker.

    past_dates are skipped entirely (no API calls made for them) — a
    concert that already happened has no reason to be queried daily, and
    this also keeps ticketen's response consistent since we don't know
    whether the API even returns anything meaningful for past shows."""
    listings = []
    errors = []
    past_dates = set(past_dates or [])
    dates_to_fetch = [d for d in EXPECTED_DATES if d not in past_dates]

    if past_dates:
        print(f"  {TICKETEN_LABEL}: skipping {len(past_dates)} past date(s) (already happened)")

    for date_key in dates_to_fetch:
        date_part, time_part = date_key.split(" ")
        offset = 0

        while True:
            params = {
                "context": "date",
                "eventId": TICKETEN_EVENT_ID,
                "date": date_part,
                "startTime": time_part,
                "activeOnly": "0",
                "limit": str(TICKETEN_API_PAGE_SIZE),
                "offset": str(offset),
                "sortBy": "pricePerTicket",
                "sortOrder": "asc",
            }
            url = f"{TICKETEN_API_URL}?{urlencode(params)}"

            try:
                raw = fetch_html(url)
            except (URLError, HTTPError, TimeoutError, RuntimeError) as exc:
                print(f"  ! {TICKETEN_LABEL}: fetch failed for {date_key} "
                      f"(offset {offset}) ({exc})", file=sys.stderr)
                errors.append(str(exc))
                break

            if debug:
                DEBUG_DIR.mkdir(exist_ok=True)
                safe_key = date_key.replace(" ", "_").replace(":", "")
                debug_path = DEBUG_DIR / f"ticketen_{safe_key}_offset{offset}.json"
                debug_path.write_text(raw, encoding="utf-8")

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                print(f"  ! {TICKETEN_LABEL}: bad JSON for {date_key} "
                      f"(offset {offset}) ({exc})", file=sys.stderr)
                errors.append(str(exc))
                break

            if not data.get("success"):
                print(f"  ! {TICKETEN_LABEL}: API returned success=false for {date_key}",
                      file=sys.stderr)
                break

            for ticket in data.get("tickets", []):
                price = ticket.get("pricePerTicket")
                if price is None or not (MIN_REASONABLE_PRICE <= price <= MAX_REASONABLE_PRICE):
                    continue
                tag_labels = " ".join(
                    t.get("label", "") for t in (ticket.get("tags") or []) if isinstance(t, dict)
                )
                category_text = f"{ticket.get('description') or ''} {tag_labels}"
                listings.append({
                    "date": date_key,
                    "price": price,
                    "sold": ticket.get("status") == "sold",
                    "category": categorize(category_text),
                })

            if not data.get("hasMore"):
                break
            next_offset = data.get("nextOffset")
            if next_offset is None or next_offset <= offset:
                break
            offset = next_offset
            time.sleep(0.5)  # polite pause between pages of the same date

        time.sleep(0.5)  # polite pause between dates

    return aggregate_listings(listings, TICKETEN_LABEL, track_sold=True,
                               past_dates=list(past_dates), errors=errors)


# ----------------------------------------------------------------------------
# HISTORY STORAGE
# ----------------------------------------------------------------------------


def load_history() -> list:
    if HISTORY_FILE.exists():
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    return []


def save_history(history: list) -> None:
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def upsert_today(history: list, today_entry: dict) -> list:
    today_str = today_entry["date"]
    history = [row for row in history if row["date"] != today_str]
    history.append(today_entry)
    history.sort(key=lambda r: r["date"])
    return history


# ----------------------------------------------------------------------------
# DASHBOARD GENERATION
# ----------------------------------------------------------------------------


DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<title>なにわ男子 ND5 - チケット価格トラッカー</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #16171d;
    --panel: #1e2029;
    --ink: #f2f0ea;
    --muted: #8b8d97;
    --jam: #ff6a5c;
    --ten: #4fd6c4;
    --sold: #6b6d78;
    --line: #2c2e39;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: -apple-system, "Hiragino Kaku Gothic ProN", "Yu Gothic", sans-serif;
    padding: 32px 20px 60px;
  }
  .wrap { max-width: 1020px; margin: 0 auto; }
  h1 { font-size: 22px; font-weight: 700; margin: 0 0 4px; letter-spacing: 0.01em; }
  h2 { font-size: 15px; font-weight: 700; margin: 40px 0 14px; color: var(--ink); }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 28px; }
  .cards { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 10px; }
  .card { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 18px 20px; }
  .card .site {
    font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--muted); margin-bottom: 10px; display: flex; align-items: center; gap: 6px;
  }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
  .jam-dot { background: var(--jam); }
  .ten-dot { background: var(--ten); }
  .price { font-size: 30px; font-weight: 700; font-variant-numeric: tabular-nums; }
  .price .yen { font-size: 16px; color: var(--muted); font-weight: 500; margin-left: 4px;}
  .meta { color: var(--muted); font-size: 12px; margin-top: 6px; }
  .meta .sold-line { color: var(--sold); margin-top: 2px; }
  .chart-panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; padding: 20px; height: 340px; }
  .empty { color: var(--muted); font-size: 14px; text-align: center; padding: 60px 0; }
  select {
    background: var(--panel); color: var(--ink); border: 1px solid var(--line);
    border-radius: 6px; padding: 6px 10px; font-size: 13px; margin-bottom: 14px;
  }
  table {
    width: 100%; border-collapse: collapse; font-size: 13px; background: var(--panel);
    border: 1px solid var(--line); border-radius: 10px; overflow: hidden;
  }
  th, td {
    padding: 10px 12px; text-align: right; border-bottom: 1px solid var(--line);
    font-variant-numeric: tabular-nums; white-space: nowrap;
  }
  th:first-child, td:first-child { text-align: left; }
  th { color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: 0.04em; }
  tr:last-child td { border-bottom: none; }
  .site-head-jam { color: var(--jam); }
  .site-head-ten { color: var(--ten); }
  .table-wrap, .cat-table-wrap { overflow-x: auto; }
  .ended-row td { opacity: 0.55; }
  .ended-row td:first-child { font-style: italic; }
  .filter-row { display: flex; gap: 10px; flex-wrap: wrap; }
  .filter-row select { margin-bottom: 14px; }
</style>
</head>
<body>
<div class="wrap">
  <h1>なにわ男子 LIVE TOUR 2026「ND⁵」 リセール価格トラッカー</h1>
  <div class="sub">チケジャム ＆ チケテン - 最終更新: __LAST_UPDATED__</div>

  <div class="cards">
    <div class="card">
      <div class="site"><span class="dot jam-dot"></span>チケジャム 出品中 中央値</div>
      <div class="price">__JAM_MEDIAN__<span class="yen">円</span></div>
      <div class="meta">__JAM_COUNT__ 件の出品 ・ 最安値 __JAM_MIN__円 ・ 平均 __JAM_AVG__円</div>
    </div>
    <div class="card">
      <div class="site"><span class="dot ten-dot"></span>チケテン 出品中 中央値</div>
      <div class="price">__TEN_MEDIAN__<span class="yen">円</span></div>
      <div class="meta">__TEN_COUNT__ 件の出品中 ・ 最安値 __TEN_MIN__円 ・ 平均 __TEN_AVG__円</div>
      <div class="meta sold-line">売り切れ: __TEN_SOLD_COUNT__ 件 ・ 中央値だった __TEN_SOLD_MEDIAN__円 ・ 平均 __TEN_SOLD_AVG__円</div>
    </div>
  </div>

  <h2>全体推移（出品中・全公演日合算・中央値）</h2>
  <div class="chart-panel">
    __OVERALL_CHART_OR_EMPTY__
  </div>

  <h2>公演日別トレンド（出品中・中央値）</h2>
  <select id="dateSelect"></select>
  <div class="chart-panel">
    __PERDATE_CHART_OR_EMPTY__
  </div>

  <h2>選択した公演日のカテゴリー別内訳（出品中・最新）</h2>
  <div class="cat-table-wrap" id="perDateCategoryWrap">
    __PERDATE_CATEGORY_TABLE_OR_EMPTY__
  </div>

  <h2>公演日別 最新スナップショット</h2>
  <div class="filter-row">
    <select id="categoryFilterSelect"></select>
    <select id="statusFilterSelect"></select>
  </div>
  <div class="table-wrap">
    __TABLE_OR_EMPTY__
  </div>

  <h2>カテゴリー別 最新スナップショット（出品中のみ・全公演日合算）</h2>
  <div class="cat-table-wrap" id="allDatesCategoryWrap">
    __CATEGORY_TABLE_OR_EMPTY__
  </div>
</div>

<script>
const history = __HISTORY_JSON__;
const CATEGORY_ORDER = __CATEGORY_ORDER_JSON__;
const PAST_DATES = new Set(__PAST_DATES_JSON__);
const TODAY_STR = __TODAY_STR_JSON__;

function fmt(v) { return (v === null || v === undefined) ? '—' : v.toLocaleString('ja-JP'); }
function dateLabel(k) { return PAST_DATES.has(k) ? `${k} (終了)` : k; }

// Prefer an exact match on today's date over trusting array order — guards
// against any stray future-dated entry (e.g. leftover test data) silently
// outranking the real latest run.
function getLatestEntry() {
  if (history.length === 0) return null;
  if (TODAY_STR) {
    const exact = history.find(h => h.date === TODAY_STR);
    if (exact) return exact;
  }
  return history[history.length - 1];
}

if (history.length > 0) {
  // ---- Overall chart (on-sale only) ----
  const ctx1 = document.getElementById('overallChart').getContext('2d');
  new Chart(ctx1, {
    type: 'line',
    data: {
      labels: history.map(r => r.date),
      datasets: [
        { label: 'チケジャム 中央値', data: history.map(r => r.ticketjam.overall.median), borderColor: '#ff6a5c', backgroundColor: '#ff6a5c', tension: 0.25, spanGaps: true },
        { label: 'チケテン 中央値（出品中）', data: history.map(r => r.ticketen.overall.median), borderColor: '#4fd6c4', backgroundColor: '#4fd6c4', tension: 0.25, spanGaps: true },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#8b8d97' } } },
      scales: {
        x: { ticks: { color: '#8b8d97' }, grid: { color: '#2c2e39' } },
        y: { ticks: { color: '#8b8d97' }, grid: { color: '#2c2e39' } }
      }
    }
  });

  // ---- Per-date trend chart ----
  const dateKeySet = new Set();
  history.forEach(day => {
    ['ticketjam', 'ticketen'].forEach(site => {
      Object.keys(day[site].by_date || {}).forEach(k => dateKeySet.add(k));
    });
  });
  const dateKeys = Array.from(dateKeySet).sort();

  const select = document.getElementById('dateSelect');
  dateKeys.forEach(k => {
    const opt = document.createElement('option');
    opt.value = k;
    opt.textContent = dateLabel(k);
    select.appendChild(opt);
  });

  let perDateChart = null;
  function renderPerDateChart(dateKey) {
    const jamSeries = history.map(day => (day.ticketjam.by_date || {})[dateKey]?.median ?? null);
    const tenSeries = history.map(day => (day.ticketen.by_date || {})[dateKey]?.median ?? null);
    const cfg = {
      type: 'line',
      data: {
        labels: history.map(r => r.date),
        datasets: [
          { label: 'チケジャム 中央値', data: jamSeries, borderColor: '#ff6a5c', backgroundColor: '#ff6a5c', tension: 0.25, spanGaps: true },
          { label: 'チケテン 中央値（出品中）', data: tenSeries, borderColor: '#4fd6c4', backgroundColor: '#4fd6c4', tension: 0.25, spanGaps: true },
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { labels: { color: '#8b8d97' } } },
        scales: {
          x: { ticks: { color: '#8b8d97' }, grid: { color: '#2c2e39' } },
          y: { ticks: { color: '#8b8d97' }, grid: { color: '#2c2e39' } }
        }
      }
    };
    if (perDateChart) { perDateChart.data = cfg.data; perDateChart.update(); }
    else { perDateChart = new Chart(document.getElementById('perDateChart').getContext('2d'), cfg); }
  }

  function renderPerDateCategoryTable(dateKey) {
    const latestForCat = getLatestEntry();
    const jamCat = (latestForCat.ticketjam.by_date_category || {})[dateKey] || {};
    const tenCat = (latestForCat.ticketen.by_date_category || {})[dateKey] || {};
    const rows = CATEGORY_ORDER.map(cat => {
      const j = jamCat[cat] || {};
      const t = tenCat[cat] || {};
      return `<tr>
        <td>${cat}</td>
        <td>${fmt(j.min)}</td><td>${fmt(j.median)}</td><td>${fmt(j.avg)}</td><td>${fmt(j.count)}</td>
        <td>${fmt(t.min)}</td><td>${fmt(t.median)}</td><td>${fmt(t.avg)}</td><td>${fmt(t.count)}</td>
      </tr>`;
    }).join('');

    document.getElementById('perDateCategoryWrap').innerHTML = `
      <table>
        <thead>
          <tr>
            <th rowspan="2">カテゴリー</th>
            <th colspan="4" class="site-head-jam">チケジャム</th>
            <th colspan="4" class="site-head-ten">チケテン</th>
          </tr>
          <tr>
            <th>最安値</th><th>中央値</th><th>平均</th><th>件数</th>
            <th>最安値</th><th>中央値</th><th>平均</th><th>件数</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  }

  if (dateKeys.length > 0) {
    select.value = dateKeys[0];
    renderPerDateChart(dateKeys[0]);
    renderPerDateCategoryTable(dateKeys[0]);
    select.addEventListener('change', () => {
      renderPerDateChart(select.value);
      renderPerDateCategoryTable(select.value);
    });
  }

  // ---- Latest snapshot table (per date), filterable by category + status ----
  const latest = getLatestEntry();
  const tableDateSet = new Set([
    ...Object.keys(latest.ticketjam.by_date || {}),
    ...Object.keys(latest.ticketen.by_date || {}),
    ...Object.keys(latest.ticketen.by_date_sold || {}),
  ]);
  const tableDates = Array.from(tableDateSet).sort();

  const categoryFilterSelect = document.getElementById('categoryFilterSelect');
  const statusFilterSelect = document.getElementById('statusFilterSelect');

  const CATEGORY_FILTER_OPTIONS = [['__ALL__', 'すべてのカテゴリー'], ...CATEGORY_ORDER.map(c => [c, c])];
  CATEGORY_FILTER_OPTIONS.forEach(([value, text]) => {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = text;
    categoryFilterSelect.appendChild(opt);
  });

  const STATUS_FILTER_OPTIONS = [
    ['onsale', '出品中のみ'],
    ['sold', '売り切れのみ（チケテンのみ）'],
    ['both', '出品中＋売り切れ 両方'],
  ];
  STATUS_FILTER_OPTIONS.forEach(([value, text]) => {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = text;
    statusFilterSelect.appendChild(opt);
  });

  function getStatsFor(site, dateKey, category, sold) {
    // site: 'ticketjam' | 'ticketen'; sold: boolean
    const entry = latest[site];
    if (category === '__ALL__') {
      const bucket = sold ? (entry.by_date_sold || {}) : (entry.by_date || {});
      return bucket[dateKey] || {};
    }
    const bucket = sold ? (entry.by_date_category_sold || {}) : (entry.by_date_category || {});
    return (bucket[dateKey] || {})[category] || {};
  }

  function renderSnapshotTable() {
    if (tableDates.length === 0) return;
    const category = categoryFilterSelect.value;
    const status = statusFilterSelect.value;
    const showOnSale = status === 'onsale' || status === 'both';
    const showSold = status === 'sold' || status === 'both';

    const headCols = [];
    if (showOnSale) headCols.push({ label: 'チケジャム（出品中）', span: 4, cls: 'site-head-jam' });
    if (showOnSale) headCols.push({ label: 'チケテン（出品中）', span: 4, cls: 'site-head-ten' });
    if (showSold) headCols.push({ label: 'チケテン（売り切れ）', span: 4, cls: 'site-head-ten' });

    const subHeadCells = [];
    for (let i = 0; i < headCols.length; i++) {
      subHeadCells.push('<th>最安値</th><th>中央値</th><th>平均</th><th>件数</th>');
    }

    const rows = tableDates.map(dk => {
      const cells = [];
      if (showOnSale) {
        const j = getStatsFor('ticketjam', dk, category, false);
        cells.push(`<td>${fmt(j.min)}</td><td>${fmt(j.median)}</td><td>${fmt(j.avg)}</td><td>${fmt(j.count)}</td>`);
      }
      if (showOnSale) {
        const t = getStatsFor('ticketen', dk, category, false);
        cells.push(`<td>${fmt(t.min)}</td><td>${fmt(t.median)}</td><td>${fmt(t.avg)}</td><td>${fmt(t.count)}</td>`);
      }
      if (showSold) {
        const ts = getStatsFor('ticketen', dk, category, true);
        cells.push(`<td>${fmt(ts.min)}</td><td>${fmt(ts.median)}</td><td>${fmt(ts.avg)}</td><td>${fmt(ts.count)}</td>`);
      }
      const rowClass = PAST_DATES.has(dk) ? ' class="ended-row"' : '';
      return `<tr${rowClass}><td>${dateLabel(dk)}</td>${cells.join('')}</tr>`;
    }).join('');

    document.querySelector('.table-wrap').innerHTML = `
      <table>
        <thead>
          <tr>
            <th rowspan="2">公演日時</th>
            ${headCols.map(c => `<th colspan="${c.span}" class="${c.cls}">${c.label}</th>`).join('')}
          </tr>
          <tr>${subHeadCells.join('')}</tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  }

  if (tableDates.length > 0) {
    renderSnapshotTable();
    categoryFilterSelect.addEventListener('change', renderSnapshotTable);
    statusFilterSelect.addEventListener('change', renderSnapshotTable);
  }

  // ---- Category snapshot table (on-sale only, all dates combined) ----
  const jamCat = latest.ticketjam.by_category || {};
  const tenCat = latest.ticketen.by_category || {};
  const catRows = CATEGORY_ORDER.map(cat => {
    const j = jamCat[cat] || {};
    const t = tenCat[cat] || {};
    return `<tr>
      <td>${cat}</td>
      <td>${fmt(j.min)}</td><td>${fmt(j.median)}</td><td>${fmt(j.avg)}</td><td>${fmt(j.count)}</td>
      <td>${fmt(t.min)}</td><td>${fmt(t.median)}</td><td>${fmt(t.avg)}</td><td>${fmt(t.count)}</td>
    </tr>`;
  }).join('');

  document.getElementById('allDatesCategoryWrap').innerHTML = `
    <table>
      <thead>
        <tr>
          <th rowspan="2">カテゴリー</th>
          <th colspan="4" class="site-head-jam">チケジャム</th>
          <th colspan="4" class="site-head-ten">チケテン</th>
        </tr>
        <tr>
          <th>最安値</th><th>中央値</th><th>平均</th><th>件数</th>
          <th>最安値</th><th>中央値</th><th>平均</th><th>件数</th>
        </tr>
      </thead>
      <tbody>${catRows}</tbody>
    </table>
  `;
}
</script>
</body>
</html>
"""


def fmt(value):
    return f"{value:,}" if value is not None else "—"


def get_latest_entry(history: list, today_str: str = None):
    """Pick the entry that represents 'now' for dashboard summary purposes.
    Prefers an exact match on today's date over trusting history[-1] / sort
    order — this guards against any stray entry with a bogus/future date
    (e.g. leftover test data) silently outranking the real latest run."""
    if not history:
        return None
    if today_str:
        exact = next((h for h in history if h["date"] == today_str), None)
        if exact:
            return exact
    return history[-1]


def render_dashboard(history: list, past_dates: list = None, today_str: str = None) -> str:
    html = DASHBOARD_TEMPLATE
    past_dates = past_dates or []

    empty_summary = {"min": None, "median": None, "avg": None, "count": 0}

    latest = get_latest_entry(history, today_str)
    if latest:
        jam = latest["ticketjam"]["overall"]
        ten = latest["ticketen"]["overall"]
        ten_sold = latest["ticketen"].get("overall_sold") or empty_summary
        last_updated = latest["date"]
    else:
        jam = ten = empty_summary
        ten_sold = empty_summary
        last_updated = "データなし"

    if history:
        overall_chart = '<canvas id="overallChart"></canvas>'
        perdate_chart = '<canvas id="perDateChart"></canvas>'
        table_html = '<div class="empty">日付別データがまだありません。</div>'
        category_table_html = '<div class="empty">カテゴリー別データがまだありません。</div>'
        perdate_category_table_html = '<div class="empty">公演日を選択してください。</div>'
    else:
        overall_chart = '<div class="empty">まだデータがありません。track_prices.py を実行してください。</div>'
        perdate_chart = '<div class="empty">まだデータがありません。</div>'
        table_html = '<div class="empty">まだデータがありません。</div>'
        category_table_html = '<div class="empty">まだデータがありません。</div>'
        perdate_category_table_html = '<div class="empty">まだデータがありません。</div>'

    html = html.replace("__LAST_UPDATED__", last_updated)
    html = html.replace("__JAM_MIN__", fmt(jam["min"]))
    html = html.replace("__JAM_MEDIAN__", fmt(jam["median"]))
    html = html.replace("__JAM_AVG__", fmt(jam.get("avg")))
    html = html.replace("__JAM_COUNT__", str(jam["count"]))
    html = html.replace("__TEN_MIN__", fmt(ten["min"]))
    html = html.replace("__TEN_MEDIAN__", fmt(ten["median"]))
    html = html.replace("__TEN_AVG__", fmt(ten.get("avg")))
    html = html.replace("__TEN_COUNT__", str(ten["count"]))
    html = html.replace("__TEN_SOLD_COUNT__", str(ten_sold["count"]))
    html = html.replace("__TEN_SOLD_MIN__", fmt(ten_sold["min"]))
    html = html.replace("__TEN_SOLD_MEDIAN__", fmt(ten_sold.get("median")))
    html = html.replace("__TEN_SOLD_AVG__", fmt(ten_sold.get("avg")))
    html = html.replace("__OVERALL_CHART_OR_EMPTY__", overall_chart)
    html = html.replace("__PERDATE_CHART_OR_EMPTY__", perdate_chart)
    html = html.replace("__PERDATE_CATEGORY_TABLE_OR_EMPTY__", perdate_category_table_html)
    html = html.replace("__TABLE_OR_EMPTY__", table_html)
    html = html.replace("__CATEGORY_TABLE_OR_EMPTY__", category_table_html)
    html = html.replace("__HISTORY_JSON__", json.dumps(history, ensure_ascii=False))
    html = html.replace("__CATEGORY_ORDER_JSON__", json.dumps(CATEGORY_ORDER, ensure_ascii=False))
    html = html.replace("__PAST_DATES_JSON__", json.dumps(past_dates, ensure_ascii=False))
    html = html.replace("__TODAY_STR_JSON__", json.dumps(today_str))
    return html


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Track ND5 ticket prices")
    parser.add_argument(
        "--debug", action="store_true",
        help="Save raw fetched responses (HTML/JSON) to debug/ and print extra diagnostics "
             "(use this if a site returns 0 listings unexpectedly)"
    )
    args = parser.parse_args()

    today = date.today()
    today_str = today.isoformat()
    print(f"Tracking prices for {today_str}...")
    if args.debug:
        print(f"[debug mode] raw responses will be saved to {DEBUG_DIR}")

    past_dates, upcoming_dates = split_past_dates(today)
    if past_dates:
        print(f"{len(past_dates)} of {len(EXPECTED_DATES)} tour dates have already "
              f"happened and will be excluded from fetching/warnings.")

    results = {
        "ticketjam": scrape_ticketjam(SITES["ticketjam"], debug=args.debug, past_dates=past_dates),
        "ticketen": scrape_ticketen(debug=args.debug, past_dates=past_dates),
    }

    today_entry = {
        "date": today_str,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "ticketjam": results["ticketjam"],
        "ticketen": results["ticketen"],
    }

    history = load_history()
    history = upsert_today(history, today_entry)
    save_history(history)

    dashboard_html = render_dashboard(history, past_dates=past_dates, today_str=today_str)
    DASHBOARD_FILE.write_text(dashboard_html, encoding="utf-8")

    print(f"\nSaved to {HISTORY_FILE.name}")
    print(f"Dashboard updated: {DASHBOARD_FILE.name}")


if __name__ == "__main__":
    main()
