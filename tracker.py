#!/usr/bin/env python3
"""IPO tracker.

Finds companies that have filed to go public in the US by reading SEC EDGAR,
works out where each deal stands from the *types* of filings made (no document
reading, no AI model), and writes:

    docs/data/ipos.json   the list, for other tools
    docs/index.html       the site: site/report.html with the data written into it
    data/state.json       what has already been looked at, so runs stay small

Run it on a schedule (see .github/workflows/update.yml). Python 3.9+, no
third-party packages.

SEC rules this script follows: every request carries a User-Agent that names
you and a contact email (set SEC_USER_AGENT), and requests stay well under
10 per second.
"""
import argparse
import datetime as dt
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent

INITIAL = {"S-1", "F-1"}                      # first public registration statement
AMEND = {"S-1/A", "F-1/A"}                    # amendments to it
PRICED = {"424B4", "424B1"}                   # final prospectus: the deal has priced
WITHDRAWN = {"RW"}                            # request to withdraw the registration
EFFECTIVE = {"EFFECT"}                        # SEC declared the registration effective
LISTING = {"8-A12B", "CERT", "CERTNYS", "CERTNAS", "CERTARCA", "CERTBATS", "CERTAMX"}
DRAFT = {"DRS", "DRS/A"}                      # confidential drafts, published later
MARKETING = {"FWP"}                           # roadshow and other marketing documents
PERIODIC = {"10-K", "10-K/A", "10-KT", "10-Q", "10-Q/A", "10-QT", "20-F", "20-F/A", "40-F"}
TRACKED = INITIAL | AMEND | PRICED | WITHDRAWN | EFFECTIVE | LISTING | DRAFT | MARKETING

STALLED_AFTER_DAYS = 270       # no filing for this long and not priced: shown as stalled
REFRESH_PENDING_DAYS = 540     # stop re-checking a pending deal after this long without activity
REFRESH_PRICED_DAYS = 45       # keep re-checking a priced deal this long
UNPRICED_AFTER_DAYS = 30       # cleared by the SEC this long ago with no pricing: not a normal IPO any more
MAX_HISTORY_FILES = 5          # companies with more pages of filing history than this are long-established
STATE_VERSION = 2              # bump to make every run start again from the index


class NotFound(Exception):
    pass


class Fetcher:
    """Polite HTTP GET for sec.gov, or reads saved files when testing."""

    def __init__(self, user_agent, fixtures=None, pause=0.15):
        self.user_agent = user_agent
        self.fixtures = Path(fixtures) if fixtures else None
        self.pause = pause
        self._last = 0.0
        self.requests = 0

    @staticmethod
    def fixture_name(url):
        return re.sub(r"[^A-Za-z0-9.]+", "_", url.split("://", 1)[1])

    def get(self, url):
        self.requests += 1
        if self.fixtures is not None:
            path = self.fixtures / self.fixture_name(url)
            if not path.exists():
                raise NotFound(url)
            return path.read_bytes()
        request = urllib.request.Request(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"})
        last_error = None
        for attempt in range(5):
            wait = self.pause - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    body = response.read()
                    encoding = (response.headers.get("Content-Encoding") or "").lower()
                    if encoding == "gzip":
                        body = gzip.decompress(body)
                    elif encoding == "deflate":
                        body = zlib.decompress(body)
                    return body
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    raise NotFound(url)
                last_error = error
                if error.code not in (403, 429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                last_error = error
            time.sleep(2 ** attempt * 3)       # 3, 6, 12, 24, 48 seconds
        raise RuntimeError(f"could not fetch {url}: {last_error}")


# ---------- EDGAR index ----------

def quarter_of(day):
    return day.year, (day.month - 1) // 3 + 1


def quarters_between(start, end):
    year, quarter = quarter_of(start)
    out = []
    while (year, quarter) <= quarter_of(end):
        out.append((year, quarter))
        year, quarter = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
    return out


def index_url(year, quarter):
    return f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/master.idx"


def parse_index(data):
    """Yield (cik, form, date) from a master index: CIK|Company Name|Form Type|Date Filed|Filename."""
    for line in data.decode("latin-1").splitlines():
        parts = line.split("|")
        if len(parts) != 5 or not parts[0].strip().isdigit():
            continue
        date = parts[3].strip()
        if re.fullmatch(r"\d{8}", date):
            date = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        yield int(parts[0]), parts[2].strip(), date


# ---------- one company ----------

def submissions_url(cik):
    return f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json"


def filing_url(cik, accession, document):
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/"
    return base + document if document else base


def sector_for(sic):
    """Map a 4-digit SIC code to one of a few plain sectors."""
    if not sic:
        return "Not yet classified"
    two = sic // 100
    if sic in (2833, 2834, 2835, 2836, 8731):
        return "Biotech and pharma"
    if sic in (3841, 3842, 3843, 3844, 3845, 3851, 8071) or two == 80:
        return "Health care"
    if two == 73 and 7370 <= sic <= 7379:
        return "Software and internet"
    if sic in (3570, 3571, 3572, 3575, 3576, 3577, 3578, 3661, 3663, 3669, 3672, 3674, 3677, 3678, 3679):
        return "Hardware and chips"
    if two in (10, 12, 13, 14, 29, 46, 49):
        return "Energy and utilities"
    if sic in (6500, 6510, 6512, 6513, 6519, 6531, 6532, 6552, 6798):
        return "Real estate"
    if 60 <= two <= 67:
        return "Finance"
    if two == 48 or two in (27, 78):
        return "Telecom and media"
    if 52 <= two <= 59 or two in (20, 21, 22, 23, 31, 70, 79):
        return "Consumer and retail"
    if two in (72, 75, 76, 82):
        return "Consumer and retail"
    if 15 <= two <= 17 or 24 <= two <= 39 or 40 <= two <= 47 or two in (50, 51):
        return "Industrials"
    if two in (73, 81, 87, 89):
        return "Business services"
    return "Other"


def kind_for(sic, name):
    """company, spac (blank-check shell) or fund (commodity or crypto trust, ETF)."""
    if sic == 6770 or re.search(r"\bacquisition\b", name, re.I):
        return "spac"                                  # new registrants named "... Acquisition ..." are shells
    if sic == 6221 or re.search(r"\bETF\b", name, re.I):
        return "fund"
    if sic in (6199, 6211, 6722, 6726) and re.search(r"\b(TRUST|FUND)\b", name, re.I):
        return "fund"
    return "company"


def filings_of(sub):
    recent = (sub.get("filings") or {}).get("recent") or {}
    forms = recent.get("form") or []
    count = len(forms)

    def column(key):
        values = recent.get(key) or []
        return values if len(values) == count else [""] * count

    rows = [
        {"form": f, "date": d, "accession": a, "document": p, "accepted": t}
        for f, d, a, p, t in zip(forms, column("filingDate"), column("accessionNumber"), column("primaryDocument"), column("acceptanceDateTime"))
    ]
    rows.sort(key=lambda r: (r["date"], r["accepted"]))     # oldest first
    return rows


def classify(sub, today):
    """Return (record, None) for an IPO candidate, or (None, reason) to skip the company."""
    rows = filings_of(sub)
    cik = int(sub.get("cik") or 0)
    name = (sub.get("name") or "").strip()
    first_registration = next((r for r in rows if r["form"] in INITIAL), None)
    if first_registration is None:
        return None, "no_registration_statement"
    if (sub.get("filings") or {}).get("files"):
        return None, "established_filer"               # more than a page of filing history
    if any(r["form"] in PERIODIC and r["date"] < first_registration["date"] for r in rows):
        return None, "already_public"                  # filed annual or quarterly reports first

    cycle, prior_attempts = None, 0
    for r in rows:
        form = r["form"]
        if form in INITIAL:
            if cycle is None or cycle["withdrawn"]:
                if cycle is not None:
                    prior_attempts += 1
                cycle = {"form": form, "start": r["date"], "amendments": 0, "effective": None, "listing": None,
                         "priced": None, "withdrawn": None, "marketing": False, "events": []}
            elif cycle["priced"]:
                continue                                # a later offering by a now-public company
        elif cycle is None or cycle["priced"]:
            continue
        elif form in AMEND:
            cycle["amendments"] += 1
            cycle["withdrawn"] = None
        elif form in WITHDRAWN:
            cycle["withdrawn"] = r["date"]
        elif form in EFFECTIVE:
            cycle["effective"] = r["date"]
        elif form in LISTING:
            cycle["listing"] = r["date"]
        elif form in MARKETING:
            cycle["marketing"] = True
        elif form in PRICED:
            cycle["priced"] = r["date"]
        else:
            continue
        cycle["events"].append({"form": form, "date": r["date"], "url": filing_url(cik, r["accession"], r["document"])})

    if cycle["priced"]:
        stage = "priced"
    elif cycle["withdrawn"]:
        stage = "withdrawn"
    elif cycle["effective"]:
        stage = "effective"
    elif cycle["amendments"]:
        stage = "amended"
    else:
        stage = "filed"

    last_activity = cycle["events"][-1]["date"]
    idle_days = (today - dt.date.fromisoformat(last_activity)).days
    drafts = [r["date"] for r in rows if r["form"] in DRAFT and r["date"] <= cycle["start"]]
    addresses = sub.get("addresses") or {}
    business, mailing = addresses.get("business") or {}, addresses.get("mailing") or {}
    location = next((a.get(k) for a in (business, mailing) for k in ("stateOrCountryDescription", "stateOrCountry", "country", "foreignStateTerritory") if a.get(k)), "")
    cleared_days = (today - dt.date.fromisoformat(cycle["effective"])).days if cycle["effective"] else 0
    sic = int(sub["sic"]) if str(sub.get("sic") or "").isdigit() else 0
    documents = [e for e in cycle["events"] if e["form"] in INITIAL | AMEND | PRICED]
    record = {
        "cik": cik,
        "name": name,
        "tickers": [t for t in (sub.get("tickers") or []) if t],
        "exchanges": [e for e in (sub.get("exchanges") or []) if e],
        "sic": sic,
        "industry": sub.get("sicDescription") or "",
        "sector": sector_for(sic),
        "kind": kind_for(sic, name),
        "location": location,
        "foreign": bool(business.get("isForeignLocation")) or cycle["form"] == "F-1",
        "form": cycle["form"],
        "stage": stage,
        "stalled": stage in ("filed", "amended") and idle_days > STALLED_AFTER_DAYS,
        "unpriced": stage == "effective" and cleared_days > UNPRICED_AFTER_DAYS,
        "first_filed": cycle["start"],
        "draft_date": min(drafts) if drafts else None,
        "amendments": cycle["amendments"],
        "marketing": cycle["marketing"],
        "effective_date": cycle["effective"],
        "listing_date": cycle["listing"],
        "priced_date": cycle["priced"],
        "withdrawn_date": cycle["withdrawn"],
        "last_activity": last_activity,
        "prior_attempts": prior_attempts,
        "latest": (documents or cycle["events"])[-1],
        "filings": cycle["events"][-12:],
        "edgar_url": f"https://www.sec.gov/edgar/browse/?CIK={cik}",
    }
    return record, None


def add_older_filings(fetcher, sub):
    """A busy company's history is split over several files. Merge the older ones in, if there are only a few."""
    filings = sub.get("filings") or {}
    files = filings.get("files") or []
    if not files or len(files) > MAX_HISTORY_FILES:
        return
    recent = filings.setdefault("recent", {})
    columns = ("form", "filingDate", "accessionNumber", "primaryDocument", "acceptanceDateTime")
    merged = {c: list(recent.get(c) or []) for c in columns}
    for entry in files:
        try:
            older = json.loads(fetcher.get("https://data.sec.gov/submissions/" + entry["name"]))
        except (NotFound, KeyError, TypeError, json.JSONDecodeError):
            return                                     # leave "files" in place: classify() will skip the company
        older = older if "form" in older else ((older.get("filings") or {}).get("recent") or {})
        count = len(older.get("form") or [])
        if not count or len(older.get("filingDate") or []) != count:
            return
        for c in columns:
            values = older.get(c) or []
            merged[c] += values if len(values) == count else [""] * count
    recent.update(merged)
    filings["files"] = []


def is_active(record, today):
    """Should this deal be re-checked on every run?"""
    idle = (today - dt.date.fromisoformat(record["last_activity"])).days
    if record["stage"] in ("filed", "amended", "effective"):
        return idle <= REFRESH_PENDING_DAYS
    if record["stage"] == "priced":
        return idle <= REFRESH_PRICED_DAYS
    return False


# ---------- the run ----------

def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def run(fetcher, today, lookback_days, state_path, out_dir, force_index=False, log=print):
    state = load_json(state_path, {})
    if state.get("version") != STATE_VERSION:
        state = {"version": STATE_VERSION}             # older or missing state: start again from the index
    state.setdefault("quarters_done", [])
    state.setdefault("index_day", None)
    state.setdefault("skip", {})
    state.setdefault("companies", {})
    companies, skip = state["companies"], state["skip"]
    cutoff = (today - dt.timedelta(days=lookback_days)).isoformat()

    # 1. Read the quarterly index to find companies with registration filings.
    candidates = set()
    for year, quarter in quarters_between(today - dt.timedelta(days=lookback_days), today):
        key, current = f"{year}Q{quarter}", (year, quarter) == quarter_of(today)
        if key in state["quarters_done"]:
            continue
        if current and state["index_day"] == today.isoformat() and not force_index:
            continue
        try:
            data = fetcher.get(index_url(year, quarter))
        except NotFound:
            log(f"index {key}: not published yet")
            continue
        found = {cik for cik, form, date in parse_index(data) if form in INITIAL | AMEND and date >= cutoff}
        candidates |= found
        log(f"index {key}: {len(found)} companies with S-1 or F-1 filings")
        if current:
            state["index_day"] = today.isoformat()
        else:
            state["quarters_done"].append(key)

    # 2. Look up each company not seen before.
    checked = set()

    def look_up(cik):
        checked.add(cik)
        try:
            sub = json.loads(fetcher.get(submissions_url(cik)))
            add_older_filings(fetcher, sub)
        except NotFound:
            return
        except (RuntimeError, json.JSONDecodeError) as error:
            log(f"CIK {cik}: skipped this run ({error})")
            return
        record, reason = classify(sub, today)
        if record and record["stage"] in ("priced", "withdrawn") and (record["priced_date"] or record["withdrawn_date"]) < cutoff:
            record, reason = None, "outside_window"     # an old IPO that only surfaced through a later filing
        key = str(cik)
        if record:
            companies[key] = record
            skip.pop(key, None)
        else:
            skip[key] = {"reason": reason, "name": (sub.get("name") or "").strip()}
            companies.pop(key, None)

    new = sorted(c for c in candidates if str(c) not in companies and str(c) not in skip)
    for cik in new:
        look_up(cik)

    # 3. Re-check deals that are still in motion, so amendments, pricing and withdrawals show up within the hour.
    refreshed = [int(k) for k, r in list(companies.items()) if int(k) not in checked and is_active(r, today)]
    for cik in refreshed:
        look_up(cik)

    # 4. Write the outputs, only when the list has changed.
    out_dir = Path(out_dir)
    ipos = sorted(companies.values(), key=lambda r: (r["last_activity"], r["name"]), reverse=True)
    json_path = out_dir / "data" / "ipos.json"
    payload = load_json(json_path, {})
    changed = payload.get("ipos") != ipos
    if changed:
        counts = {}
        for r in ipos:
            counts[r["stage"]] = counts.get(r["stage"], 0) + 1
        payload = {"source": "SEC EDGAR", "data_changed": today.isoformat(), "counts": counts, "ipos": ipos}
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    changed = write_page(payload, out_dir) or changed      # also picks up a new template or breakdown file
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    Path(state_path).write_text(json.dumps(state, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    log(f"{len(new)} new companies looked up, {len(refreshed)} re-checked, {len(ipos)} tracked, "
        f"{fetcher.requests} requests, output {'updated' if changed else 'unchanged'}")
    return {"new": len(new), "refreshed": len(refreshed), "tracked": len(ipos), "changed": changed}


PAGE_FIELDS = ("cik", "name", "tickers", "exchanges", "sector", "kind", "location", "stage", "stalled", "unpriced", "first_filed", "draft_date",
               "amendments", "marketing", "effective_date", "priced_date", "withdrawn_date", "last_activity", "latest", "edgar_url")


def load_breakdowns(ciks):
    """Per-deal breakdown files in data/breakdowns/<cik>.json, for deals that are being tracked."""
    out = {}
    for path in sorted((ROOT / "data" / "breakdowns").glob("*.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if item.get("cik") in ciks:
            out[str(item["cik"])] = item
    return out


def render_page(payload):
    """Build the site page: the report template with the data it needs written into it."""
    ipos = []
    read = load_json(ROOT / "data" / "reader.json", {}).get("deals", {})
    for r in payload["ipos"]:
        slim = {k: r.get(k) for k in PAGE_FIELDS}
        if read.get(str(r["cik"]), {}).get("result") == "resale":
            slim["direct"] = True                        # the reader found a resale by existing holders, not a priced offering
        base = f"https://www.sec.gov/Archives/edgar/data/{r['cik']}/"
        slim["ev"] = [[e["form"], e["date"], e["url"].replace(base, "")] for e in r.get("filings", [])[-10:]]
        ipos.append(slim)
    news = {cik: deal["items"] for cik, deal in load_json(ROOT / "data" / "headlines.json", {}).get("deals", {}).items() if deal.get("items")}
    data = {"data_changed": payload["data_changed"], "ipos": ipos, "breakdowns": load_breakdowns({r["cik"] for r in payload["ipos"]}), "headlines": news}
    template = (ROOT / "site" / "report.html").read_text(encoding="utf-8")
    return template.replace("/*DATA*/null", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))


def write_page(payload, out_dir):
    """Write index.html if its content has changed. Returns True when it was rewritten."""
    path, page = Path(out_dir) / "index.html", render_page(payload)
    if path.exists() and path.read_text(encoding="utf-8") == page:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the IPO tracker list from SEC EDGAR.")
    parser.add_argument("--lookback-days", type=int, default=365, help="how far back the first run looks for registration filings")
    parser.add_argument("--state", default=str(ROOT / "data" / "state.json"))
    parser.add_argument("--out", default=str(ROOT / "docs"))
    parser.add_argument("--force-index", action="store_true", help="re-read this quarter's index even if already read today")
    parser.add_argument("--render-only", action="store_true", help="rebuild index.html from the saved list without contacting the SEC")
    parser.add_argument("--fixtures", help="read saved responses from this folder instead of the network (for tests)")
    parser.add_argument("--today", help="pretend today is this date, YYYY-MM-DD (for tests)")
    args = parser.parse_args(argv)

    if args.render_only:
        payload = load_json(Path(args.out) / "data" / "ipos.json", None)
        if not payload:
            sys.exit("No saved list yet. Run the tracker once first.")
        print("page rewritten" if write_page(payload, args.out) else "page unchanged")
        return
    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not args.fixtures and "@" not in user_agent:
        sys.exit("Set SEC_USER_AGENT to your site or name plus a contact email, for example:\n"
                 '  SEC_USER_AGENT="IPO Query info@ipoquery.com"\n'
                 "The SEC blocks automated requests that do not identify themselves.")
    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(dt.timezone.utc).date()
    run(Fetcher(user_agent, args.fixtures), today, args.lookback_days, args.state, args.out, args.force_index)


if __name__ == "__main__":
    main()
