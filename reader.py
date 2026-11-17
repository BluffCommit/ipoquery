#!/usr/bin/env python3
"""IPO Query reader: reads each tracked deal's latest prospectus and writes the
breakdown the site shows.

    python reader.py

This is the no-cost part of the reader. It uses no model. It reads:

    the offer terms     price range or final price, shares sold by the company
                        and by existing holders, shares outstanding afterwards
    the financials      revenue, operating or net result and cash, from the
                        tagged (XBRL) financial statements

A figure is kept only if a consistency check passes: the filing has to state it
in two places, or two figures have to agree with each other. A figure that
fails is left out and the reason is recorded. If the offer terms themselves
fail, no breakdown is written and the IPO page says the filing is not read yet.

Reads   docs/data/ipos.json            the tracker's list
Writes  data/breakdowns/<CIK>.json     one file per deal (never replaces a hand-made one)
        data/reader.json               which document was read for each deal, and what happened

Only the Python standard library is used.
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import tracker

READ_FORMS = tracker.INITIAL | tracker.AMEND | tracker.PRICED
MAX_DOCS = 40                 # prospectuses downloaded per run; a backlog clears over a few runs
PRICED_DAYS = 120             # keep reading deals that priced this recently
FRONT = 400_000               # characters of text that hold the cover page and the summary
STATE_VERSION = 1
NO_RANGE = "no price range in this filing yet"

FORM_NAMES = {"S-1": "S-1", "F-1": "F-1", "S-1/A": "S-1 amendment", "F-1/A": "F-1 amendment", "424B4": "Final prospectus", "424B1": "Final prospectus"}


# ---------- turning the filing into plain text ----------

DROP = re.compile(r"<(head|script|style|ix:header)\b.*?</\1\s*>", re.S | re.I)
BREAK = re.compile(r"</?(?:p|div|tr|table|br|li|ul|ol|h[1-6]|hr|center|blockquote|title)\b[^>]*>", re.I)
CELL = re.compile(r"</?t[dh]\b[^>]*>", re.I)
TAG = re.compile(r"<[^>]+>")


def to_text(raw):
    """The filing's text, lower case, on one line, with punctuation made regular."""
    page = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    page = DROP.sub(" ", page)
    page = BREAK.sub("\n", page)
    page = CELL.sub(" ", page)
    page = html.unescape(TAG.sub("", page))
    for old, new in ((" ", " "), ("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-"), ("‑", "-")):
        page = page.replace(old, new)
    page = re.sub(r"\s+", " ", page).lower()
    page = re.sub(r"u\.?s\.?\s?\$", "$", page)
    page = re.sub(r"\$\s+", "$", page)
    return page


# ---------- the offer terms ----------

NUM = r"(\d{1,3}(?:,\d{3})+)"
USD = r"\$(\d[\d,]*(?:\.\d+)?)"
HOLDERS = r"selling (?:stockholders?|shareholders?|securityholders?)"
RANGE_A = re.compile(r"price[^$]{0,160}?between " + USD + r" and " + USD)
RANGE_B = re.compile(r"between " + USD + r" and " + USD + r" per (?:[a-z]+ ){0,3}?(?:share|ads)")
MIDPOINT = re.compile(r"offering price of " + USD + r" per (?:[a-z]+ ){0,4}?(?:share|ads)[^.]{0,80}?mid-?point")
FINAL = re.compile(r"public offering price(?: per (?:[a-z]+ ){0,3}?(?:share|ads))? (?:is|of|was) " + USD)
FINAL_ROW = re.compile(r"public offering price(?: per (?:[a-z]+ ){0,3}?(?:share|ads))?[ :]{1,3}" + USD + r" " + USD)
SELL_COVER = re.compile(HOLDERS + r"[^.;]{0,140}? (?:are|is) (?:offering|selling) (?:an additional |up to )?" + NUM + r" ")
SELL_ROW = re.compile(r"offered by (?:the )?" + HOLDERS + r"[^\d.]{0,60}" + NUM + r" ")
US_COVER = re.compile(r"(?:\bwe are|[a-z0-9 ,&'.-]{3,80}? is) (?:offering|selling) " + NUM + r" (?:shares|ordinary|american|adss?|class|common)")
US_ROW = re.compile(r"offered by us[ :.]{0,5}" + NUM + r" ")
AFTER = re.compile(r"outstanding (?:immediately )?after (?:this|the|our) offering[ :.]{0,5}" + NUM + r" (?:shares|ordinary|common|class)")
LISTING = re.compile(r"(?:list|listing|listed|quot|trad)[^.]{0,200}? on (?:the )?(nasdaq|new york stock exchange|nyse american|nyse|cboe)[^.]{0,80}?under the (?:ticker )?symbol \"?([a-z]{1,5}(?:\.[a-z])?)\b")
EXCHANGES = {"nasdaq": "Nasdaq", "new york stock exchange": "NYSE", "nyse": "NYSE", "nyse american": "NYSE American", "cboe": "Cboe"}


def number(text):
    return float(text.replace(",", "")) if "." in text else int(text.replace(",", ""))


def agree(first, second, text):
    """A share count is kept only if the filing states it in two places."""
    both = [n for n in first if n in second]
    if both:
        return Counter(both).most_common(1)[0][0], "stated on the cover and again in the offering summary"
    for n in dict.fromkeys(first + second):
        if text.count(f"{n:,}") >= 2:
            return n, "stated more than once in the filing"
    return None, None


def offer_terms(text, form):
    """Read the offer terms. Returns (offer, exchange, checks, hidden), or (None, None, [], reasons)
    when a term the page depends on could not be confirmed."""
    front = text[:FRONT]
    checks, hidden, offer = [], [], {}

    # price: a range before pricing, a final price in the final prospectus
    pairs = [(number(a), number(b)) for a, b in RANGE_A.findall(front) + RANGE_B.findall(front)]
    pairs = [(lo, hi) for lo, hi in pairs if 0 < lo < hi <= lo * 2]
    if form in tracker.PRICED:
        prices = Counter(number(p) for p in FINAL.findall(text))
        rows = [(number(p), number(total)) for p, total in FINAL_ROW.findall(front)]
        if not prices and not rows:
            return None, None, [], ["no final price found"]
        price = prices.most_common(1)[0][0] if prices else rows[0][0]
        offer["price"] = price
    else:
        if not pairs:
            return None, None, [], [NO_RANGE]
        (low, high), seen = Counter(pairs).most_common(1)[0]
        mids = {number(m) for m in MIDPOINT.findall(text)}
        if any(abs(m - (low + high) / 2) < 0.006 for m in mids):
            checks.append({"item": "Price range", "how": "the midpoint used elsewhere in the filing matches the range on the cover"})
        elif seen >= 2 and len(set(pairs)) == 1:
            checks.append({"item": "Price range", "how": "stated more than once in the filing, always the same"})
        else:
            return None, None, [], [f"price range {low} to {high} is not confirmed anywhere else in the filing"]
        offer["low"], offer["high"] = low, high

    # shares sold by existing holders: found first, so the company's own sentence is not confused with theirs
    sell_cover = [number(n) for n in SELL_COVER.findall(front)]
    sell_row = [number(n) for n in SELL_ROW.findall(front)]
    if sell_cover or sell_row:
        holder, how = agree(sell_cover, sell_row, text)
        if holder is None:
            return None, None, [], ["the number of shares sold by existing holders is not stated the same way twice"]
        checks.append({"item": "Shares sold by existing holders", "how": how})
    elif re.search(HOLDERS, front[:60_000]):
        return None, None, [], ["existing holders are selling, but the number of shares could not be read"]
    else:
        holder = 0
        checks.append({"item": "No existing holder is selling", "how": "the cover page and summary do not mention selling holders"})
    offer["holder_shares"] = holder

    # shares sold by the company
    cleared = SELL_COVER.sub(" ", front)
    us_cover = [number(n) for n in US_COVER.findall(cleared)]
    us_row = [number(n) for n in US_ROW.findall(front)]
    company, how = agree(us_cover, us_row, text)
    if company is None:
        if holder and not us_cover and not us_row:
            company, how = 0, "only existing holders are selling"       # a sale by holders alone
        else:
            return None, None, [], ["the number of shares the company is selling is not stated the same way twice"]
    offer["company_shares"] = company
    checks.append({"item": "Shares sold by the company", "how": how})

    if "price" in offer:
        total = (company + holder) * offer["price"]
        if any(p == offer["price"] and abs(t - total) <= total * 0.005 for p, t in rows):
            checks.append({"item": "Final price", "how": "price times shares sold equals the total on the cover"})
        elif prices and prices.most_common(1)[0][1] >= 2:
            checks.append({"item": "Final price", "how": "stated more than once in the filing"})
        else:
            return None, None, [], ["the final price is not confirmed by the total on the cover"]
        if pairs:                                   # a final prospectus rarely repeats the range, but keep it if it does
            offer["low"], offer["high"] = Counter(pairs).most_common(1)[0][0]

    # shares outstanding after the offering: needed for the company's value and the share that trades
    ads = "american depositary share" in front[:80_000]
    after = []
    for match in AFTER.finditer(front):
        label = front[max(0, match.start() - 110):match.start()]
        label = re.split(r"\d shares|\. ", label)[-1]
        share_class = re.search(r"class ([a-z])\b", label)
        after.append((number(match.group(1)), "total" in label, share_class.group(1) if share_class else ""))
    sold = company + holder
    shares_after = None
    if ads:
        offer["unit"] = "ADS"
        hidden.append({"item": "Company value and the share that trades", "why": "the offer is in depositary shares, which are not counted the same way as the shares outstanding"})
    elif not after:
        hidden.append({"item": "Company value and the share that trades", "why": "the number of shares outstanding after the offering could not be read"})
    else:
        totals = [n for n, is_total, _ in after if is_total]
        by_class = {}
        for n, _, share_class in after:
            by_class.setdefault(share_class, n)
        if totals:
            shares_after, how = totals[0], "the filing's own total"
        elif len(by_class) > 1 and "" not in by_class:
            shares_after, how = sum(by_class.values()), "the sum of each share class"
        else:
            shares_after, how = Counter(n for n, _, _ in after).most_common(1)[0][0], "from the offering summary"
        if sold and 0.002 <= sold / shares_after <= 0.9:
            offer["shares_after"] = shares_after
            checks.append({"item": "Shares outstanding after the offering", "how": how + "; larger than the number sold"})
        else:
            hidden.append({"item": "Company value and the share that trades", "why": "the shares outstanding after the offering do not fit with the number sold"})

    listing = LISTING.search(front)
    exchange = EXCHANGES.get(listing.group(1)) if listing else None
    return offer, exchange, checks, hidden


# ---------- the tagged financial statements ----------

REVENUE = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
           "RevenuesNetOfInterestExpense", "Revenue"]
OPERATING = ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"]
NET = ["NetIncomeLoss", "ProfitLoss"]
CASH = ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "CashAndCashEquivalents"]
CASH_FLOW = ["NetCashProvidedByUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivities"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def tagged(facts, names):
    """Values for the first of these tags that has any, as {(start, end): value}; the latest filing wins.
    Returns (values, currency)."""
    for taxonomy in ("us-gaap", "ifrs-full"):
        for name in names:
            units = facts.get("facts", {}).get(taxonomy, {}).get(name, {}).get("units", {})
            for currency, rows in units.items():
                out, filed = {}, {}
                for row in rows:
                    key = (row.get("start"), row["end"])
                    if key not in out or row.get("filed", "") >= filed[key]:
                        out[key], filed[key] = row["val"], row.get("filed", "")
                if out:
                    return out, currency
    return {}, None


def days(start, end):
    return (dt.date.fromisoformat(end) - dt.date.fromisoformat(start)).days


def period_label(start, end):
    a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    if days(start, end) > 340:
        return str(b.year) if b.month == 12 else f"Year to {MONTHS[b.month - 1]} {b.year}"
    return f"{MONTHS[a.month - 1]} to {MONTHS[b.month - 1]} {b.year}" if a.year == b.year else f"{MONTHS[a.month - 1]} {a.year} to {MONTHS[b.month - 1]} {b.year}"


def printed(value, text):
    """Is this tagged value also printed in the filing, in dollars, thousands or millions?"""
    value = abs(value)
    shapes = {f"{value:,.0f}", f"{value / 1e3:,.0f}", f"{value / 1e6:,.0f}", f"{value / 1e6:,.1f}", f"{value / 1e9:,.1f}", f"{value / 1e9:,.2f}"}
    return any(len(shape) >= 3 and re.search(r"(?<![\d,.])" + re.escape(shape) + r"(?![\d,]|\.\d)", text) for shape in shapes)


def financials(facts, text, doc_date):
    """Revenue, result and cash from the tagged statements. Returns (block, checks, reason it was left out)."""
    revenue, rev_currency = tagged(facts, REVENUE)
    result, currency = tagged(facts, OPERATING)
    measure = "operating"
    if not result:
        result, currency = tagged(facts, NET)
        measure = "net"
    if not result:
        return None, [], "the financial statements are not tagged with an operating or net result"
    if currency != "USD" or (revenue and rev_currency != "USD"):
        return None, [], f"the company reports in {currency}, not US dollars"
    years = sorted((k for k in result if k[0] and 350 <= days(*k) <= 380), key=lambda k: k[1])[-3:]
    if not years:
        return None, [], "no full-year figures are tagged"
    last = years[-1]
    if (dt.date.fromisoformat(doc_date) - dt.date.fromisoformat(last[1])).days > 800:
        return None, [], "the tagged figures are more than two years old"

    def near(key, values):                      # the same period, give or take a few days
        for other in values:
            if other[0] and abs(days(other[0], key[0])) <= 6 and abs(days(other[1], key[1])) <= 6:
                return values[other]
        return None

    def year_before(day):
        day = dt.date.fromisoformat(day)
        return str(day.replace(year=day.year - 1, day=min(day.day, 28) if day.month == 2 else day.day))

    to_print = [result[last]] + ([near(last, revenue)] if near(last, revenue) is not None else [])
    if not all(printed(v, text) for v in to_print):
        return None, [], "the tagged figures for the latest year could not be found in the printed statements"

    m = lambda v: None if v is None else round(v / 1e6, 3)
    block = {"unit": "USD millions", "measure": measure,
             "years": [{"label": period_label(*k), "revenue": m(near(k, revenue)), "operating": m(result[k])} for k in years]}
    part = [k for k in result if k[0] and k[1] > last[1] and 80 <= days(*k) <= 290 and abs(days(last[1], k[0])) <= 6]
    latest = last
    if part:
        k = max(part, key=lambda k: k[1])
        before = (year_before(k[0]), year_before(k[1]))
        prior = near(before, result)
        if prior is not None and printed(result[k], text):
            block["ytd"] = {"label": period_label(*k), "revenue": m(near(k, revenue)), "operating": m(result[k]),
                            "prior_label": period_label(*before), "prior_revenue": m(near(before, revenue)), "prior_operating": m(prior)}
            latest = k
    facts_out = []
    cash, cash_currency = tagged(facts, CASH)
    held = [k for k in cash if k[1] >= latest[1]]
    if held and cash_currency == "USD":
        k = max(held, key=lambda k: k[1])
        day = dt.date.fromisoformat(k[1])
        facts_out.append({"value": m(cash[k]), "label": f"cash on {MONTHS[day.month - 1]} {day.day}, {day.year}"})
    flow, flow_currency = tagged(facts, CASH_FLOW)
    if near(latest, flow) is not None and flow_currency == "USD":
        facts_out.append({"value": m(near(latest, flow)), "label": f"cash from operations, {period_label(*latest)}", "signed": True})
    if measure == "operating":
        net, net_currency = tagged(facts, NET)
        if near(latest, net) is not None and net_currency == "USD":
            facts_out.append({"value": m(near(latest, net)), "label": f"net result, {period_label(*latest)}", "signed": True})
    block["facts"] = facts_out
    return block, [{"item": "Financial figures", "how": "taken from the tagged statements, and the latest year's figures also appear in the printed statements"}], None


# ---------- reading one deal ----------

def document_for(record):
    """The newest prospectus-type document for this deal, as (form, date, url)."""
    for event in reversed(record.get("filings", [])):
        if event["form"] in READ_FORMS:
            return event["form"], event["date"], event["url"]
    return None


def wanted(record, today):
    if record["kind"] != "company" or record["stage"] == "withdrawn" or record.get("stalled") or record.get("unpriced"):
        return False
    if record["stage"] == "priced":
        return (today - dt.date.fromisoformat(record["priced_date"])).days <= PRICED_DAYS
    return True


def long_date(day):
    day = dt.date.fromisoformat(day)
    return f"{MONTHS[day.month - 1]} {day.day}, {day.year}"


def read_deal(fetcher, record, doc, earlier, today):
    """Returns (breakdown or None, notes)."""
    form, date, url = doc
    text = to_text(fetcher.get(url))
    offer, exchange, checks, hidden = offer_terms(text, form)
    if offer is None:
        return None, hidden
    if "low" not in offer and earlier and earlier.get("offer", {}).get("low"):      # keep the range from before pricing
        offer["low"], offer["high"] = earlier["offer"]["low"], earlier["offer"]["high"]
    out = {"cik": record["cik"], "method": "auto", "loaded": today.isoformat(), "basis": f"{FORM_NAMES[form]} filed {long_date(date)}",
           "doc": url, "doc_date": date, "offer": offer}
    if exchange:
        out["exchange"] = exchange
    if earlier and earlier.get("range_set"):
        out["range_set"] = earlier["range_set"]
    try:
        facts = json.loads(fetcher.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{record['cik']:010d}.json"))
    except tracker.NotFound:
        facts = None
    if facts is None:
        hidden.append({"item": "Revenue, results and cash", "why": "the financial statements in this filing are not tagged yet"})
    else:
        block, fin_checks, why = financials(facts, text, date)
        if block:
            out["financials"] = block
            checks += fin_checks
        else:
            hidden.append({"item": "Revenue, results and cash", "why": why})
    out["checks"], out["hidden"] = checks, hidden
    return out, []


def run(fetcher, today, list_path, breakdown_dir, state_path, max_docs=MAX_DOCS, log=print):
    records = tracker.load_json(list_path, {}).get("ipos", [])
    state = tracker.load_json(state_path, {})
    if state.get("version") != STATE_VERSION:
        state = {"version": STATE_VERSION, "deals": {}}
    deals = state["deals"]
    queue = []
    for record in records:
        doc = document_for(record) if wanted(record, today) else None
        if doc and deals.get(str(record["cik"]), {}).get("url") != doc[2]:
            queue.append((record["stage"] not in ("priced", "effective"), doc[1], record, doc))
    queue.sort(key=lambda item: (item[0], -dt.date.fromisoformat(item[1]).toordinal()))
    breakdown_dir = Path(breakdown_dir)
    written = 0
    for _, _, record, doc in queue[:max_docs]:
        cik = str(record["cik"])
        path = breakdown_dir / f"{cik}.json"
        earlier = tracker.load_json(path, None)
        entry = {"form": doc[0], "date": doc[1], "url": doc[2], "read": today.isoformat()}
        try:
            breakdown, notes = read_deal(fetcher, record, doc, earlier, today)
        except Exception as error:                      # one bad filing must not stop the run
            entry.update(result="error", notes=[f"{type(error).__name__}: {error}"[:200]])
            deals[cik] = entry
            continue
        if breakdown is None:
            entry.update(result="not confirmed", notes=notes)
        else:
            before = deals.get(cik, {})
            if "low" in breakdown["offer"] and "range_set" not in breakdown and before.get("notes") == [NO_RANGE]:
                breakdown["range_set"] = doc[1]         # the document before this one had no range
            entry.update(result="read", found=breakdown["offer"], left_out=[h["item"] for h in breakdown["hidden"]])
            if earlier and earlier.get("method") == "manual":
                entry["note"] = "a hand-made breakdown exists and was kept"
            else:
                breakdown_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(breakdown, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
                written += 1
        deals[cik] = entry
    tracked = {str(r["cik"]) for r in records}
    for cik in [c for c in deals if c not in tracked]:
        del deals[cik]
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    Path(state_path).write_text(json.dumps(state, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    counts = Counter(entry["result"] for entry in deals.values())
    log(f"read {min(len(queue), max_docs)} of {len(queue)} waiting prospectuses, wrote {written} breakdowns, {fetcher.requests} requests; "
        f"so far: {counts.get('read', 0)} read, {counts.get('not confirmed', 0)} not confirmed, {counts.get('error', 0)} errors")
    return {"queued": len(queue), "written": written, "results": dict(counts)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read each tracked deal's latest prospectus.")
    parser.add_argument("--list", default=str(tracker.ROOT / "docs" / "data" / "ipos.json"))
    parser.add_argument("--breakdowns", default=str(tracker.ROOT / "data" / "breakdowns"))
    parser.add_argument("--state", default=str(tracker.ROOT / "data" / "reader.json"))
    parser.add_argument("--max-docs", type=int, default=MAX_DOCS)
    parser.add_argument("--fixtures", help="read saved responses from this folder instead of the network (for tests)")
    parser.add_argument("--today", help="pretend today is this date, YYYY-MM-DD (for tests)")
    args = parser.parse_args(argv)
    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not args.fixtures and "@" not in user_agent:
        sys.exit("Set SEC_USER_AGENT to a name and contact email, for example: IPO Query info@ipoquery.com")
    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(dt.timezone.utc).date()
    run(tracker.Fetcher(user_agent, fixtures=args.fixtures), today, args.list, args.breakdowns, args.state, args.max_docs)


if __name__ == "__main__":
    main()
