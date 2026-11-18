"""IPO Query reader, model step.

    python reader_model.py

Runs after reader.py. For each deal whose offer terms the no-model reader has
confirmed, it cuts the sections that answer the site's questions out of the
prospectus and has two models read them independently. What they return goes
into the deal's breakdown only if it passes these checks:

    both readers give the same figure
    the figure is printed in the filing
    shares sold by the named holders add up to the number on the cover
    the price of the last funding round sits believably next to the IPO price
    (if it does not, a share split has usually been missed)
    any number inside a written summary is printed in the filing

Anything that fails is left out, with the reason recorded for the page.

data/model_compare.json logs where the two readers differed on each deal. It
also holds a one-off comparison in which a third, stronger model read 32 deals.

Needs ANTHROPIC_API_KEY. Without it, nothing happens. Only the Python standard
library is used.
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import reader
import tracker

PROMPT_VERSION = 3                     # bump to have the models read every deal again (costs money)
CHECKS_VERSION = 1                     # bump to apply changed checks to every deal again (free where the answers are saved)
MODEL_VERSION = [PROMPT_VERSION, CHECKS_VERSION]
READERS = ["claude-sonnet-5-5", "claude-haiku-5-5"]     # the first one's wording is used; figures need both to agree
REFEREE = "claude-opus-5-5"            # read 32 deals as a comparison in Nov 2026; the results are in data/model_compare.json
MAX_DEALS = 80                         # deals read per run
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00), "claude-opus-5-5": (4.00, 20.00)}    # US$ per million tokens in, out (list prices, Nov 2026)
API_URL = "https://api.anthropic.com/v1/messages"

# ---------- cutting the filing into sections ----------

HEADINGS = {
    "summary": r"prospectus summary|summary",
    "offering": r"the offering",
    "summary_financials": r"summary .{0,50}financial.{0,40}",
    "risks": r"risk factors",
    "forward": r"(?:special |cautionary )?note regarding forwardlooking statements.*|forwardlooking statements",
    "proceeds": r"use of proceeds",
    "dividends": r"dividend policy",
    "capitalization": r"capitalization",
    "dilution": r"dilution",
    "selected": r"selected .{0,50}financial.{0,40}",
    "mdna": r"managements discussion and analysis.*",
    "business": r"business|our business",
    "management": r"management",
    "compensation": r"executive (?:and director )?compensation",
    "related": r"certain relationships and related (?:party|person) transactions.*|related party transactions",
    "holders": r"principal (?:and selling )?(?:stockholders|shareholders)|selling (?:stockholders|shareholders)|security ownership of certain beneficial owners and management",
    "capital": r"description of (?:capital stock|share capital|securities|our securities)",
    "future_sale": r"shares eligible for future sales?",
    "tax": r"material .{0,60}tax.{0,60}|taxation",
    "underwriting": r"underwriting(?: conflicts? of interest)?|underwriters|plan of distribution",
    "legal": r"legal matters",
    "experts": r"experts",
    "more": r"where you can find (?:more|additional) information",
    "index": r"index to (?:the )?(?:consolidated |combined )?financial statements",
    "item15": r"(?:item )?recent sales of unregistered securities",
}
WANTED = {"cover": 7000, "summary": 3000, "offering": 9000, "summary_financials": 10000, "risks": 12000, "proceeds": 5000, "holders": 18000,
          "future_sale": 9000, "preferred": 14000, "item15": 9000}
PREFERRED = re.compile(r"(?:note )?\d*\.? ?(?:redeemable )?(?:convertible )?(?:redeemable )?preferred (?:stock|shares)(?: and .{0,60})?", re.I)


def to_lines(raw):
    """The filing as lines of text: one per paragraph or table row, capitals kept."""
    page = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    page = reader.DROP.sub(" ", page)
    page = reader.BREAK.sub("\n", page)
    page = reader.CELL.sub(" ", page)
    page = reader.html.unescape(reader.TAG.sub("", page))
    for old, new in ((" ", " "), ("’", "'"), ("‘", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-"), ("‑", "-")):
        page = page.replace(old, new)
    lines = (re.sub(r"\$\s+", "$", re.sub(r"[ \t\r\f\v]+", " ", line)).strip() for line in page.split("\n"))
    return [line for line in lines if line]


def cut(lines):
    """The sections the model reads, as {name: text}. A heading is a short line that is nothing but a known
    section title; of several such lines (the contents page lists them all) the one with the longest body wins."""
    hits = []
    for i, line in enumerate(lines):
        if len(line) > 110 or not line[0].isupper() or (line.endswith(".") and not line.lower().startswith("item")):
            continue                                     # a heading starts with a capital and is not a sentence
        plain = re.sub(r"\s+", " ", re.sub(r"[^a-z ]", "", line.lower())).strip()
        for name, pattern in HEADINGS.items():
            if re.fullmatch(pattern, plain):
                hits.append((i, name))
                break
    starts = [i for i, _ in hits] + [len(lines)]
    best = {}
    for n, (i, name) in enumerate(hits):
        body = lines[i + 1:starts[n + 1]]
        size = sum(len(line) for line in body)
        if name == "offering" and name in best and best[name][0] > 1000:
            continue                                     # "The Offering" comes early, in the summary; later lines with those words are something else
        if size > best.get(name, (0, None))[0]:
            best[name] = (size, i)
    out = {}

    def take(start, limit):
        text = ""
        for line in lines[start:]:
            if len(text) + len(line) > limit:
                break
            text += line + "\n"
        return text

    first = next((i for i, line in enumerate(lines) if re.search(r"subject to completion|filed pursuant to rule 424", line, re.I) or line.lower() == "prospectus"), 0)
    out["cover"] = take(first, WANTED["cover"])
    for name, limit in WANTED.items():
        if name in best:
            out[name] = take(best[name][1], limit)
    if "summary_financials" not in out:
        # no summary table under that name: use the selected financial data, or failing that the income statement itself
        statements = [i for i, line in enumerate(lines) if len(line) < 110 and re.fullmatch(
            r"(?:(?:unaudited|audited|condensed|interim) )*(?:consolidated |combined )*statements? of (?:operations|income|profit or loss|comprehensive (?:income|loss))(?: and .{0,60})?", line.strip(" .:").lower())]
        if "selected" in best:
            out["summary_financials"] = take(best["selected"][1], WANTED["summary_financials"])
        elif statements:
            bodies = [take(i, WANTED["summary_financials"]) for i in statements]
            out["summary_financials"] = max(bodies, key=lambda body: len(re.findall(r"\d,\d{3}", body)))
    if "risks" in best:
        # risk factors run to dozens of pages: keep the headings and short paragraphs, drop the long explanations
        start = best["risks"][1]
        short = [lines[start]] + [line for line in lines[start + 1:start + 500] if len(line) <= 320]
        out["risks"] = ""
        for line in short:
            if len(out["risks"]) + len(line) > WANTED["risks"]:
                break
            out["risks"] += line + "\n"
    # what each funding round paid: the preferred stock note in the financial statements, which is restated for share splits
    after = best.get("index", (0, 0))[1]
    spots = [i for i, line in enumerate(lines) if i > after and ((len(line) < 90 and PREFERRED.fullmatch(line.strip(" .:")))
                                                                or re.search(r"original issue price|issuance price|issue price per share", line, re.I))]
    scored = []
    for i in spots:
        body = take(max(after, i - 3), WANTED["preferred"])
        scored.append((len(re.findall(r"series [a-z]", body, re.I)) + 10 * len(re.findall(r"issue price|issuance price", body, re.I)), -i, body))
    if scored and max(scored)[0] > 0:
        out["preferred"] = max(scored)[2]
    return out


# ---------- asking the model ----------

SYSTEM = """You pull facts out of excerpts from an IPO prospectus, for a website that explains IPOs to everyday readers.

Rules:
- Use only the excerpts. If something is not stated there, give null or an empty list. Never estimate, calculate or fill in from memory.
- Copy every figure exactly as the filing prints it, as a JSON number: no commas, no currency signs, no words. A loss printed in brackets is a negative number.
- Text you write must be plain English a general reader understands at once: short sentences, no jargon, no opinions. Write about the company as "it" or by name, never "we" or "our".
- Reply with one JSON object and nothing else. Keep it compact.

The JSON object has exactly these keys:
"what": one sentence, at most 20 words, saying what the company does.
"short": the name people call the company, without Inc., Ltd. and so on.
"proceeds": one or two sentences on what the company says it will do with the money it raises. null if not stated.
"financials": {"unit": "dollars" | "thousands" | "millions" (how the summary financial table states its figures), "currency": e.g. "USD",
   "years": up to three full financial years, oldest first, each {"period_end": the last day of the year as "YYYY-MM-DD", "revenue": total revenue or null, "operating": profit or loss from operations or null, "net": net profit or loss or null},
   "part_year": null, or {"label": e.g. "Jan to Sep 2026", "period_end": "YYYY-MM-DD", "revenue", "operating", "net", "prior_label", "prior_revenue", "prior_operating", "prior_net"} for the latest period shorter than a year and the same period a year earlier,
   "cash": null, or {"value": cash and cash equivalents as printed, "date": the balance sheet date as "YYYY-MM-DD"}}
"share_classes": how many classes of shares the ownership table shows holdings for, as a number.
"sellers": only the existing holders who are selling shares in this offering, each {"name": exactly as printed, without footnote marks, "role": a few words such as "Co-founder, chief executive" or "Venture investor", "before": shares beneficially owned before the offering, "sold": shares being sold in the offering, not counting any option given to the underwriters}. Empty list if none.
"others": up to eight of the largest holders named in the ownership table who are not selling, each {"name", "role", "before"}.
"rounds": the company's series of preferred stock, oldest first, each {"name": e.g. "Series A", "when": the year it was first sold, or null if not stated, "price": the original issue price per share, "conversion_price": the conversion price per share the filing states for this series, or null if it states none}. Take both prices from the PREFERRED excerpt (the note to the financial statements), not from ITEM 15, which may predate a share split. Use ITEM 15 only for the year. Empty list if the PREFERRED excerpt gives no prices.
"preferred_converts": "one_for_one" if the filing says each preferred share converts into one share of common stock, "other" if it gives any other rate or says the rate was adjusted, null if it does not say.
"lockup": null, or {"days": the main lock-up length in days, "summary": one or two sentences on when insiders may sell, "early": true if the filing describes any release before that period ends}
"risks": the first five risk factors in the order the filing gives them, each rewritten as one plain sentence of at most 25 words."""


def ask(model, user, key):
    """Returns (answer, usage, error)."""
    body = json.dumps({"model": model, "max_tokens": 8000, "system": SYSTEM, "messages": [{"role": "user", "content": user}]}).encode()
    headers = {"x-api-key": key, "anthropic-version": os.environ.get("ANTHROPIC_VERSION", "2023-06-01"), "content-type": "application/json"}
    if os.environ.get("ANTHROPIC_WORKSPACE_ID", "").strip():        # only needed for a key that is not tied to one workspace
        headers["anthropic-workspace-id"] = os.environ["ANTHROPIC_WORKSPACE_ID"].strip()
    error = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(API_URL, data=body, headers=headers), timeout=600) as response:
                data = json.load(response)
            break
        except urllib.error.HTTPError as problem:
            error = f"HTTP {problem.code}: {problem.read().decode('utf-8', 'replace')[:300]}"
            if problem.code not in (429, 500, 502, 503, 529):
                return None, {}, error
            time.sleep(float(problem.headers.get("retry-after") or 0) or 10 * 3 ** attempt)
        except (urllib.error.URLError, TimeoutError, ConnectionError) as problem:
            error = f"{type(problem).__name__}: {problem}"[:300]
            time.sleep(10 * 3 ** attempt)
    else:
        return None, {}, error
    text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
    try:
        return json.loads(text[text.index("{"):text.rindex("}") + 1]), data.get("usage", {}), None
    except ValueError:
        return None, data.get("usage", {}), "the reply was not JSON: " + text[:200]


def cost(model, usage):
    price_in, price_out = PRICES[model]
    return round((usage.get("input_tokens", 0) * price_in + usage.get("output_tokens", 0) * price_out) / 1e6, 5)


# ---------- checking the answers ----------

def shapes(value):
    """The ways a filing may print this number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return set()
    value = abs(value)
    out = {f"{value:,.0f}"} if float(value).is_integer() else set()
    for places in (1, 2, 3, 4):
        text = f"{value:,.{places}f}"
        if float(text.replace(",", "")) == value:
            out.add(text)
    return out


def is_printed(value, text):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and abs(value) < 10 and float(value).is_integer():
        return True                                      # a single digit proves nothing either way
    return any(re.search(r"(?<![\d,.])" + re.escape(shape) + r"(?![\d,]|\.\d)", text) for shape in shapes(value))


def prose_ok(sentence, text):
    """Every number written into a summary must be printed in the filing."""
    return all(is_printed(reader.number(n), text) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", sentence or "") if n.strip(",."))


def norm(name):
    return re.sub(r"\s+", " ", re.sub(r"\(\d+\)|[^a-z0-9& ]", " ", str(name).lower())).strip()


def same(a, b):
    if a is None or b is None:
        return a is None and b is None
    try:
        return abs(float(a) - float(b)) <= abs(float(a)) * 1e-9
    except (TypeError, ValueError):
        return a == b


NUMERIC = ("revenue", "operating", "net", "prior_revenue", "prior_operating", "prior_net", "value", "before", "sold", "price", "conversion_price", "days")


def tidy(answer):
    """Small repairs to a reader's answer: figures given as text ("$1.25", "1,000") become numbers, and
    holders listed as sellers who sell nothing are moved to the other holders."""
    def fix(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in NUMERIC and isinstance(v, str) and re.fullmatch(r"\(?-?\$?-?[\d,]+(?:\.\d+)?\)?", v.strip()):
                    number = float(re.sub(r"[$,()]", "", v))
                    number = -abs(number) if v.strip().startswith("(") else number
                    node[k] = int(number) if number.is_integer() else number
                else:
                    fix(v)
        elif isinstance(node, list):
            for item in node:
                fix(item)
    fix(answer)
    sellers = [r for r in (answer.get("sellers") or []) if isinstance(r, dict)]
    answer["sellers"] = [r for r in sellers if r.get("sold")]
    answer["others"] = [r for r in sellers if not r.get("sold")] + [r for r in (answer.get("others") or []) if isinstance(r, dict)]
    return answer


def table(answer, key, name="name"):
    clean = norm if name == "name" else str
    return {clean(row[name]): row for row in (answer.get(key) or []) if isinstance(row, dict) and row.get(name)}


def differences(a, b):
    """Every figure on which two readers differ, as short notes."""
    out = []
    fa, fb = a.get("financials") or {}, b.get("financials") or {}
    if fa.get("unit") != fb.get("unit"):
        out.append(f"financial unit: {fa.get('unit')} / {fb.get('unit')}")
    ya, yb = table(fa, "years", "period_end"), table(fb, "years", "period_end")
    for end in sorted(set(ya) | set(yb)):
        for k in ("revenue", "operating", "net"):
            if not same(ya.get(end, {}).get(k), yb.get(end, {}).get(k)):
                out.append(f"{k} {end}: {ya.get(end, {}).get(k)} / {yb.get(end, {}).get(k)}")
    pa, pb = fa.get("part_year") or {}, fb.get("part_year") or {}
    for k in ("period_end", "revenue", "operating", "net", "prior_revenue", "prior_operating", "prior_net"):
        if not same(pa.get(k), pb.get(k)):
            out.append(f"part year {k}: {pa.get(k)} / {pb.get(k)}")
    for k in ("value", "date"):
        if not same((fa.get("cash") or {}).get(k), (fb.get("cash") or {}).get(k)):
            out.append(f"cash {k}: {(fa.get('cash') or {}).get(k)} / {(fb.get('cash') or {}).get(k)}")
    if a.get("preferred_converts") != b.get("preferred_converts"):
        out.append(f"preferred converts: {a.get('preferred_converts')} / {b.get('preferred_converts')}")
    for key, fields in (("sellers", ("before", "sold")), ("others", ("before",)), ("rounds", ("price", "conversion_price"))):
        ta, tb = table(a, key), table(b, key)
        for n in sorted(set(ta) | set(tb)):
            if key != "others" and (n not in ta or n not in tb):
                out.append(f"{key} {n}: named by one reader only")
            for k in fields:
                if n in ta and n in tb and not same(ta[n].get(k), tb[n].get(k)):
                    out.append(f"{key} {n} {k}: {ta[n].get(k)} / {tb[n].get(k)}")
    if not same((a.get("lockup") or {}).get("days"), (b.get("lockup") or {}).get("days")):
        out.append(f"lock-up days: {(a.get('lockup') or {}).get('days')} / {(b.get('lockup') or {}).get('days')}")
    return out


def merge(first, second, text, offer):
    """What goes into the breakdown: the first reader's answer, keeping a figure only where the second reader
    agrees and the checks pass. Returns (fields, checks, left out)."""
    out, checks, hidden = {}, [], []
    agree = "two AI readers gave the same figures"
    leave = lambda item, why: hidden.append({"item": item, "why": why, "by": "model"})
    keep = lambda item, how: checks.append({"item": item, "how": how, "by": "model"})

    # revenue, results and cash
    fa, fb = first.get("financials") or {}, second.get("financials") or {}
    ya, yb = table(fa, "years", "period_end"), table(fb, "years", "period_end")
    scale = {"dollars": 1e-6, "thousands": 1e-3, "millions": 1}.get(fa.get("unit"))
    keys = ("revenue", "operating", "net")
    measure = next((k for k in ("operating", "net") if ya and all(row.get(k) is not None for row in ya.values())), None)
    item = "Revenue, results and cash"
    if not ya:
        leave(item, "the readers found no full-year figures in the summary financial table")
    elif str(fa.get("currency", "")).upper() != "USD":
        leave(item, f"the company reports in {fa.get('currency')}, not US dollars")
    elif set(ya) != set(yb) or fa.get("unit") != fb.get("unit") or not all(same(ya[e].get(k), yb[e].get(k)) for e in ya for k in keys):
        leave(item, "the two AI readers gave different figures")
    elif not scale or not measure:
        leave(item, "the filing gives no operating or net result for every year")
    elif not all(is_printed(row[k], text) for row in ya.values() for k in keys if row.get(k) is not None):
        leave(item, "a figure the readers gave is not printed in the filing")
    else:
        m = lambda v: None if v is None else round(v * scale, 3)

        def label(end):
            day = dt.date.fromisoformat(end)
            return str(day.year) if day.month == 12 else f"Year to {reader.MONTHS[day.month - 1]} {day.year}"
        try:
            ends = sorted(ya, key=dt.date.fromisoformat)[-3:]
            block = {"unit": "USD millions", "measure": measure, "years": [{"label": label(e), "revenue": m(ya[e].get("revenue")), "operating": m(ya[e][measure])} for e in ends]}
        except ValueError:
            block = None
        if not block:
            leave(item, "the readers gave a year-end date that is not a date")
        else:
            latest, latest_label = ya[ends[-1]], block["years"][-1]["label"]
            pa, pb = fa.get("part_year") or {}, fb.get("part_year") or {}
            part = ("revenue", "operating", "net", "prior_revenue", "prior_operating", "prior_net")
            try:                                         # the part year runs from the day after the last full year to its own end date
                start = dt.date.fromisoformat(ends[-1]) + dt.timedelta(days=1)
                end = dt.date.fromisoformat(str(pa.get("period_end")))
                spans = (reader.period_label(str(start), str(end)), reader.period_label(str(start.replace(year=start.year - 1)), str(end.replace(year=end.year - 1)))) \
                    if 60 <= (end - start).days <= 300 and same(pa.get("period_end"), pb.get("period_end")) else None
            except ValueError:
                spans = None
            if spans and pa.get(measure) is not None and pa.get("prior_" + measure) is not None and all(same(pa.get(k), pb.get(k)) for k in part) \
                    and all(is_printed(pa[k], text) for k in part if pa.get(k) is not None):
                block["ytd"] = {"label": spans[0], "revenue": m(pa.get("revenue")), "operating": m(pa[measure]),
                                "prior_label": spans[1], "prior_revenue": m(pa.get("prior_revenue")), "prior_operating": m(pa["prior_" + measure])}
                latest, latest_label = pa, block["ytd"]["label"]
            block["facts"] = []
            ca, cb = fa.get("cash") or {}, fb.get("cash") or {}
            if ca.get("value") is not None and same(ca.get("value"), cb.get("value")) and ca.get("date") == cb.get("date") and is_printed(ca["value"], text):
                try:
                    block["facts"].append({"value": m(ca["value"]), "label": "cash on " + reader.long_date(ca["date"])})
                except (ValueError, TypeError):
                    pass
            if measure == "operating" and latest.get("net") is not None:
                block["facts"].append({"value": m(latest["net"]), "label": f"net result, {latest_label}", "signed": True})
            block["by"] = "model"
            out["financials"] = block
            keep("Revenue and results", agree + ", and each is printed in the filing")

    # who is selling
    sa, sb = table(first, "sellers"), table(second, "sellers")
    cover = offer.get("holder_shares") or 0
    item = "Who is selling"
    if cover:
        rows = list(sa.values())
        if not sa:
            leave(item, "the readers could not find the selling holders in the ownership table")
        elif set(sa) != set(sb) or not all(same(sa[n].get(k), sb[n].get(k)) for n in sa for k in ("before", "sold")):
            leave(item, "the two AI readers gave different names or figures")
        elif not all(isinstance(r.get("before"), (int, float)) and isinstance(r.get("sold"), (int, float)) and r["before"] >= r["sold"] > 0 for r in rows):
            leave(item, "a holder's figures do not make sense")
        elif sum(r["sold"] for r in rows) != cover:
            leave(item, f"the named holders' shares add up to {sum(r['sold'] for r in rows):,}, not the {cover:,} on the cover")
        elif not all(is_printed(r[k], text) for r in rows for k in ("before", "sold")):
            leave(item, "a figure the readers gave is not printed in the filing")
        else:
            out["sellers"] = [{"name": str(r["name"]), "role": str(r.get("role") or ""), "before": r["before"], "sold": r["sold"]} for r in rows]
            keep(item, "two AI readers agree, and the shares add up to the number on the cover")
    oa, ob = table(first, "others"), table(second, "others")
    others = [r for n, r in oa.items() if n in ob and n not in sa and isinstance(r.get("before"), (int, float)) and r["before"] > 0
              and same(r["before"], ob[n].get("before")) and is_printed(r["before"], text)]
    if first.get("share_classes") != 1 or second.get("share_classes") != 1:
        if others:                                       # with two classes a holder has two figures, and one alone would mislead
            leave("Other large holders", "the ownership table splits holdings between share classes, and they are not added up here")
    elif others:
        out["others"] = [{"name": str(r["name"]), "role": str(r.get("role") or ""), "before": r["before"], "sold": 0} for r in others[:8]]

    # what earlier rounds paid, per share of common stock. A preferred share does not always become one common share
    # (after a reverse split it rarely does), so the price that can be set against the IPO price is the conversion price,
    # or the issue price only where the filing says conversion is one for one.
    ra, rb = table(first, "rounds"), table(second, "rounds")
    item = "What earlier rounds paid"
    price = offer.get("price") or (offer["low"] + offer["high"]) / 2
    rows = list(ra.values())
    good = lambda k: bool(rows) and all(isinstance(r.get(k), (int, float)) and r[k] > 0 and same(r[k], rb.get(norm(r["name"]), {}).get(k)) and is_printed(r[k], text) for r in rows)
    if not ra:
        leave(item, "the readers found no prices for the company's preferred stock")
    elif set(ra) != set(rb):
        leave(item, "the two AI readers named different rounds")
    elif offer.get("unit") == "ADS":
        leave(item, "the offer is in depositary shares, so the prices cannot be compared directly")
    else:
        if good("conversion_price"):
            use, how, note = "conversion_price", "the conversion price of each round", "Prices are each round's conversion price: what it paid for each share of common stock it becomes."
        elif good("price") and first.get("preferred_converts") == second.get("preferred_converts") == "one_for_one":
            use, how, note = "price", "the issue price of each round, which converts one for one into common stock", "Prices are each round's original issue price. The filing says each preferred share becomes one share of common stock."
        else:
            use = None
        if not use:
            leave(item, "the filing does not say, in a way both AI readers agree on, what each round paid per share of common stock")
        elif not price / 4 <= max(r[use] for r in rows) <= price * 3:
            leave(item, "the highest round price is less than a quarter of the IPO price or more than three times it, which usually means a share split is not allowed for")
        else:
            out["rounds"] = [{"name": str(r["name"]), "when": str(r["when"]) if r.get("when") and same(r["when"], rb[norm(r["name"])].get("when")) else None, "price": r[use]} for r in rows]
            out["rounds_note"] = note
            keep(item, f"two AI readers agree on {how}, each is printed in the filing, and the highest sits believably next to the IPO price")

    # lock-up and the written summaries
    la, lb = first.get("lockup") or {}, second.get("lockup") or {}
    if la.get("summary") and la.get("days") is not None and same(la.get("days"), lb.get("days")) and is_printed(la["days"], text) and prose_ok(la["summary"], text):
        out["lockup"] = {"summary": str(la["summary"]), "days": la["days"]}
        keep("Lock-up length", "two AI readers agree on the number of days, and it is printed in the filing")
    else:
        leave("When insiders can sell", "the two AI readers did not agree on the lock-up, or gave a number that is not in the filing")
    for key in ("what", "short", "proceeds"):
        if isinstance(first.get(key), str) and first[key].strip() and prose_ok(first[key], text):
            out[key] = first[key].strip()
    risks = [r.strip() for r in (first.get("risks") or []) if isinstance(r, str) and r.strip() and prose_ok(r, text)][:5]
    if len(risks) >= 3:
        out["risks"], out["risks_note"] = risks, f"The first {len(risks)} risk factors, summarised by AI."
    return out, checks, hidden


# ---------- reading the deals ----------

MODEL_KEYS = ("financials", "sellers", "others", "rounds", "rounds_note", "lockup", "what", "short", "proceeds", "risks", "risks_note", "model")


def run(fetcher, today, list_path, breakdown_dir, compare_path, key, log=print, max_deals=MAX_DEALS):
    if not key:
        log("model step: no API key, nothing done")
        return {}
    records = {r["cik"]: r for r in tracker.load_json(list_path, {}).get("ipos", [])}
    compare = tracker.load_json(compare_path, {})
    compare.setdefault("deals", [])
    if compare.get("version") != MODEL_VERSION:              # a new version reads every deal again; the three-reader comparisons already made are kept
        compare.update(version=MODEL_VERSION, later=[])
    waiting = []
    for path in sorted(Path(breakdown_dir).glob("*.json")):
        b = tracker.load_json(path, {})
        if b.get("method") == "auto" and b.get("cik") in records and (b.get("model") or {}).get("key") != [b["doc"], MODEL_VERSION]:
            offer = b["offer"]
            raising = (offer.get("price") or (offer["low"] + offer["high"]) / 2) * (offer["company_shares"] + offer.get("holder_shares", 0))
            waiting.append(("price" in offer, -raising, path, b))
    waiting.sort(key=lambda item: item[:2])
    spent, done = 0.0, 0
    for _, _, path, b in waiting[:max_deals]:
        try:
            page = fetcher.get(b["doc"])
        except Exception as problem:
            log(f"model step: could not fetch {b['doc']}: {problem}")
            continue
        sections = cut(to_lines(page))
        text = reader.to_text(page)
        user = "\n\n".join(f"=== {name.upper().replace('_', ' ')} ===\n{body}" for name, body in sections.items())
        before = (b.get("model") or {}).get("key") or [None, None]
        if before[0] == b["doc"] and before[1] in (2, [2, 1]) and PROMPT_VERSION == 3 and "preferred" not in sections:
            b["model"]["key"] = [b["doc"], MODEL_VERSION]         # only the reading of preferred stock changed, and this filing has none
            path.write_text(json.dumps(b, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
            continue
        models = READERS
        saved_path = Path(breakdown_dir).parent / "model_answers" / f"{b['cik']}.json"
        saved = tracker.load_json(saved_path, {})
        if saved.get("key") == [b["doc"], PROMPT_VERSION] and all(model in saved["answers"] for model in READERS):
            replies = {model: (saved["answers"][model], {}, None) for model in READERS}
        else:
            with ThreadPoolExecutor(max_workers=len(models)) as pool:
                replies = dict(zip(models, pool.map(lambda model: ask(model, user, key), models)))
            if all(replies[model][0] for model in READERS):
                saved_path.parent.mkdir(parents=True, exist_ok=True)
                saved_path.write_text(json.dumps({"key": [b["doc"], PROMPT_VERSION], "answers": {model: replies[model][0] for model in READERS}}, ensure_ascii=False) + "\n", encoding="utf-8")
        costs = {model: cost(model, usage) for model, (_, usage, _) in replies.items()}
        spent += sum(costs.values())
        errors = {model: error for model, (_, _, error) in replies.items() if error}
        entry = {"cik": b["cik"], "name": records[b["cik"]]["name"], "doc": b["doc"], "read": today.isoformat(), "cost_usd": costs,
                 "sections_not_found": [name for name in WANTED if name not in sections],
                 "preferred_starts": sections.get("preferred", "")[:160].replace("\n", " / ")}
        tagged = b.get("financials") if (b.get("financials") or {}).get("by") != "model" else None      # figures from the SEC's tagged data stay
        for old in MODEL_KEYS:
            b.pop(old, None)
        b["checks"] = [c for c in b.get("checks", []) if c.get("by") != "model"]
        b["hidden"] = [h for h in b.get("hidden", []) if h.get("by") != "model" and h.get("item") != "Revenue, results and cash"]
        for reply in replies.values():
            if reply[0]:
                tidy(reply[0])
        first, second = replies[READERS[0]][0], replies[READERS[1]][0]
        if errors.get(READERS[0]) or errors.get(READERS[1]):
            entry["errors"] = errors
            if any("HTTP 4" in e for e in errors.values()):
                log(f"model step: stopped, the API refused a request: {next(iter(errors.values()))[:200]}")
                break                                    # a refused request will be refused again: stop rather than repeat it
            continue                                     # try this deal again on the next run
        fields, checks, hidden = merge(first, second, text, b["offer"])
        if tagged:
            fields["financials"] = tagged
            checks = [c for c in checks if c["item"] != "Revenue and results"]
            hidden = [h for h in hidden if h["item"] != "Revenue, results and cash"]
        b.update(fields)
        b["checks"] += checks
        b["hidden"] += hidden
        b["model"] = {"key": [b["doc"], MODEL_VERSION], "readers": READERS, "read": today.isoformat()}
        path.write_text(json.dumps(b, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        done += 1
        entry["kept"] = [c["item"] for c in checks]
        entry["left_out"] = {h["item"]: h["why"] for h in hidden}
        entry["readers_differ"] = differences(first, second)
        compare.setdefault("later", []).append({k: entry[k] for k in ("cik", "name", "cost_usd", "kept", "left_out", "readers_differ", "sections_not_found", "preferred_starts")})
    compare["spent_usd"] = round(compare.get("spent_usd", 0) + spent, 4)
    Path(compare_path).write_text(json.dumps(compare, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"model step: read {done} of {len(waiting)} waiting deals, about ${spent:.2f} this run")
    return compare


def main():
    fixtures = os.environ.get("READER_FIXTURES")
    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not fixtures and "@" not in user_agent:
        sys.exit("Set SEC_USER_AGENT to a name and contact email, for example: IPO Query info@ipoquery.com")
    run(tracker.Fetcher(user_agent, fixtures=fixtures), dt.datetime.now(dt.timezone.utc).date(), tracker.ROOT / "docs" / "data" / "ipos.json",
        tracker.ROOT / "data" / "breakdowns", tracker.ROOT / "data" / "model_compare.json", os.environ.get("ANTHROPIC_API_KEY", "").strip())


if __name__ == "__main__":
    main()
