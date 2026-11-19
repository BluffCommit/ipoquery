#!/usr/bin/env python3
"""IPO Query headlines: recent news coverage and company announcements for each deal.

    python headlines.py

The source is the GDELT Project's open index of world news (gdeltproject.org),
which may be used for any purpose with credit. It also indexes the press-release
wires, so a company's own announcements arrive the same way and are marked as
such. For each deal only the headline, the outlet, the date and the link are
kept. No article text or image is ever stored or shown.

A headline is kept only if it names the company in the headline itself; the
search already requires the article to be about an IPO or listing.

Reads   docs/data/ipos.json, data/breakdowns/
Writes  data/headlines.json

Each deal is looked up at most once every RECHECK_HOURS, at most PER_RUN deals
a run, one request every six seconds. Only the Python standard library is used.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import reader
import tracker

API = "https://api.gdeltproject.org/api/v2/doc/doc"
PER_RUN = 45
TIME_LIMIT = 420                       # seconds a run may spend; the rest waits for the next run
TIMEOUT = 25                           # seconds to wait for one answer
RECHECK_HOURS = 20
KEEP = 6                               # headlines kept per deal
WIRES = {"globenewswire.com": "GlobeNewswire", "prnewswire.com": "PR Newswire", "businesswire.com": "Business Wire",
         "accessnewswire.com": "ACCESS Newswire", "accesswire.com": "ACCESS Newswire", "newsfilecorp.com": "Newsfile"}
LEGAL = r"(?:inc|incorporated|corp|corporation|co|company|ltd|limited|llc|lp|plc|pbc|ag|sa|nv|se|ab|holdings?|group)"
STATE_VERSION = 1


class Lookup:
    """One request at a time to the news index, never faster than every six seconds, and never waiting long:
    a slow or failed lookup is simply tried again on a later run."""

    def __init__(self, contact, fixtures=None):
        self.contact, self.fixtures, self._last = contact, Path(fixtures) if fixtures else None, 0.0

    def get(self, url):
        if self.fixtures is not None:
            path = self.fixtures / tracker.Fetcher.fixture_name(url)
            if not path.exists():
                raise RuntimeError("no answer")
            return path.read_text(encoding="utf-8")
        time.sleep(max(0.0, 6 - (time.monotonic() - self._last)))
        self._last = time.monotonic()
        request = urllib.request.Request(url, headers={"User-Agent": self.contact})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read().decode("utf-8", "replace")


def search_name(record, breakdown):
    """The name to search for: what people call the company, without Inc., Ltd. and the like."""
    name = (breakdown or {}).get("short") or record["name"]
    name = re.sub(r"[^A-Za-z0-9&' -]", " ", name)
    name = re.sub(r"(?:\s+" + LEGAL + r")+\s*$", "", " " + name.strip(), flags=re.I)
    return re.sub(r"\s+", " ", name).strip()


def plain(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def query_url(name):
    query = f'"{name}" (ipo OR "initial public offering" OR "public offering" OR listing) sourcelang:english'
    return API + "?" + urllib.parse.urlencode({"query": query, "mode": "artlist", "format": "json", "maxrecords": 40, "sort": "datedesc", "timespan": "3m"})


def pick(articles, name):
    """The headlines worth showing: they name the company, one per story, newest first, announcements marked."""
    wanted, seen, out = plain(name), set(), []
    for article in articles:
        title = re.sub(r"\s+([,.:;!?%])", r"\1", re.sub(r"\s+", " ", str(article.get("title", "")))).strip()
        url, stamp = str(article.get("url", "")), str(article.get("seendate", ""))
        key = plain(title)
        if not re.match(r"https?://", url) or len(stamp) < 8 or not stamp[:8].isdigit() or f" {wanted} " not in f" {key} " or key in seen or len(title) > 300:
            continue
        seen.add(key)
        domain = re.sub(r"^www\.", "", str(article.get("domain", "")).lower()) or urllib.parse.urlparse(url).netloc
        item = {"title": title, "source": WIRES.get(domain, domain), "date": f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:8]}", "url": url}
        if domain in WIRES:
            item["release"] = True                       # the company's own announcement
        out.append(item)
    out.sort(key=lambda item: item["date"], reverse=True)
    releases = [item for item in out if item.get("release")][:2]          # always leave room for the company's own word
    rest = [item for item in out if item not in releases][:KEEP - len(releases)]
    return sorted(releases + rest, key=lambda item: item["date"], reverse=True)


def run(fetcher, now, list_path, breakdown_dir, state_path, per_run=PER_RUN, log=print):
    records = tracker.load_json(list_path, {}).get("ipos", [])
    state = tracker.load_json(state_path, {})
    if state.get("version") != STATE_VERSION:
        state = {"version": STATE_VERSION, "deals": {}}
    deals = state["deals"]
    queue = []
    for record in records:
        breakdown = tracker.load_json(Path(breakdown_dir) / f"{record['cik']}.json", None)
        if not breakdown and not (record.get("tickers") and reader.wanted(record, now.date())):
            continue                                     # only deals with a breakdown, or upcoming ones far enough along to have a ticker
        name = search_name(record, breakdown)
        if len(name) < 4:
            continue
        checked = deals.get(str(record["cik"]), {}).get("checked", "")
        if not checked or (now - dt.datetime.fromisoformat(checked)).total_seconds() > RECHECK_HOURS * 3600:
            queue.append((checked, bool(not breakdown), record, name))
    queue.sort(key=lambda item: item[:2])
    found = failed = in_a_row = 0
    started = time.monotonic()
    for _, _, record, name in queue[:per_run]:
        if in_a_row >= 3 or time.monotonic() - started > TIME_LIMIT:
            break                                        # the service is not answering, or time is up: leave the rest for the next run
        entry = {"checked": now.isoformat(timespec="minutes"), "name": name, "items": deals.get(str(record["cik"]), {}).get("items", [])}
        try:
            body = fetcher.get(query_url(name)).strip()
            articles = json.loads(body).get("articles", []) if body.startswith("{") else None
        except Exception as problem:                     # a failed lookup keeps the headlines it had
            articles, body = None, f"{type(problem).__name__}: {problem}"
        if articles is None:
            entry["problem"] = body[:160]
            failed += 1
            in_a_row += 1
        else:
            in_a_row = 0
            entry["items"] = pick(articles, name)
            found += bool(entry["items"])
        deals[str(record["cik"])] = entry
    tracked = {str(r["cik"]) for r in records}
    for cik in [c for c in deals if c not in tracked]:
        del deals[cik]
    Path(state_path).write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"headlines: looked up {min(len(queue), per_run)} of {len(queue)} deals due, {found} with headlines, {failed} lookups failed")
    return {"due": len(queue), "found": found, "failed": failed}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Collect recent headlines for each deal.")
    parser.add_argument("--fixtures", help="read saved responses from this folder instead of the network (for tests)")
    args = parser.parse_args(argv)
    contact = os.environ.get("SEC_USER_AGENT", "").strip()
    if not args.fixtures and "@" not in contact:
        sys.exit("Set SEC_USER_AGENT to a name and contact email, for example: IPO Query info@ipoquery.com")
    run(Lookup(contact, args.fixtures), dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
        tracker.ROOT / "docs" / "data" / "ipos.json", tracker.ROOT / "data" / "breakdowns", tracker.ROOT / "data" / "headlines.json")


if __name__ == "__main__":
    main()
