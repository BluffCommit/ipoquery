#!/usr/bin/env python3
"""IPO Query reader, model step. For now this only runs a test:

    python reader_model.py

It takes the newest prospectus of a few test deals, cuts out the sections that
answer the site's questions, and asks a model to read them. Nothing it produces
goes on the site yet. Everything is written to data/model_test.json so the
result, the checks and the cost can be judged first.

Without ANTHROPIC_API_KEY it still fetches and cuts the filing and reports how
much text the model would be sent. With the key it also asks each model in
MODELS and checks the answers:

    every figure the model gives must be printed in the filing
    shares sold by the named holders must add up to the number on the cover,
    which the no-model reader has already confirmed
    where a hand-made breakdown exists, the answers are compared with it

Only the Python standard library is used.
"""
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import reader
import tracker

TEST_VERSION = 1                       # bump to run the test again
TEST_CIKS = [2130357, 1698506]         # Anthropic (a hand-made breakdown exists to compare with) and an ordinary small deal
MODELS = ["claude-haiku-5-5", "claude-sonnet-5-5"]
PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.00, 10.00)}    # US$ per million tokens in, out (list prices, Nov 2026)
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
WANTED = {"cover": 7000, "summary": 3000, "offering": 9000, "summary_financials": 9000, "risks": 9000, "proceeds": 5000, "holders": 18000,
          "future_sale": 9000, "item15": 9000, "preferred": 6000}


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
        if len(line) > 110:
            continue
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
    after = best.get("index", (0, 0))[1]
    issue = next((i for i, line in enumerate(lines) if i > after and re.search(r"original issue price|issuance price|issue price per share", line, re.I)), None)
    if issue is not None:
        out["preferred"] = take(max(after, issue - 12), WANTED["preferred"])
    return out


# ---------- asking the model ----------

SYSTEM = """You pull facts out of excerpts from an IPO prospectus, for a website that explains IPOs to everyday readers.

Rules:
- Use only the excerpts. If something is not stated there, give null or an empty list. Never estimate, calculate or fill in from memory.
- Copy every figure exactly as the filing prints it, as a plain number with no commas. A loss printed in brackets is a negative number.
- Text you write must be plain English a general reader understands at once: short sentences, no jargon, no opinions.
- Reply with one JSON object and nothing else.

The JSON object has exactly these keys:
"what": one sentence, at most 20 words, saying what the company does.
"short": the name people call the company, without Inc., Ltd. and so on.
"proceeds": one or two sentences on what the company says it will do with the money it raises. null if not stated.
"financials": {"unit": "dollars" | "thousands" | "millions" (how the summary financial table states its figures), "currency": e.g. "USD",
   "years": up to three full financial years, oldest first, each {"label": e.g. "2025", "revenue": number or null, "operating": operating profit or loss, or null, "net": net profit or loss, or null},
   "part_year": null, or {"label": e.g. "Jan to Sep 2026", "revenue", "operating", "net", "prior_label", "prior_revenue", "prior_operating", "prior_net"} for the latest period shorter than a year and the same period a year earlier}
"sellers": the existing holders selling shares in this offering, each {"name", "role": a few words such as "Co-founder, chief executive" or "Venture investor", "before": shares owned before the offering, "sold": shares being sold in the offering, not counting any option given to the underwriters}. Empty list if none.
"others": up to eight of the largest holders named in the ownership table who are not selling, each {"name", "role", "before"}.
"rounds": the company's past sales of preferred stock, oldest first, each {"name": e.g. "Series A", "when": the year or null if not stated, "price": price per share as printed}.
"lockup": null, or {"days": the main lock-up length in days, "summary": one or two sentences on when insiders may sell, "early": true if the filing describes any release before that period ends}
"risks": the first five risk factors in the order the filing gives them, each rewritten as one plain sentence of at most 25 words."""


def ask(model, user, key):
    body = json.dumps({"model": model, "max_tokens": 5000, "system": SYSTEM, "messages": [{"role": "user", "content": user}]}).encode()
    request = urllib.request.Request(API_URL, data=body, headers={
        "x-api-key": key, "anthropic-version": os.environ.get("ANTHROPIC_VERSION", "2023-06-01"), "content-type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        return None, {}, f"HTTP {error.code}: {error.read().decode('utf-8', 'replace')[:300]}"
    except (urllib.error.URLError, TimeoutError) as error:
        return None, {}, f"{type(error).__name__}: {error}"[:300]
    text = "".join(block.get("text", "") for block in data.get("content", []) if block.get("type") == "text")
    try:
        return json.loads(text[text.index("{"):text.rindex("}") + 1]), data.get("usage", {}), None
    except ValueError:
        return None, data.get("usage", {}), "the reply was not JSON: " + text[:200]


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
    return {shape for shape in out if len(shape) >= 2}


def is_printed(value, text):
    return any(re.search(r"(?<![\d,.])" + re.escape(shape) + r"(?![\d,]|\.\d)", text) for shape in shapes(value))


def check(answer, text, offer, manual):
    """Returns a report: which figures are printed in the filing, whether sellers add up, and how the answer compares
    with a hand-made breakdown where there is one."""
    figures = []
    fin = answer.get("financials") or {}
    for row in (fin.get("years") or []) + ([fin["part_year"]] if fin.get("part_year") else []):
        figures += [(f"financials {row.get('label')} {k}", v) for k, v in row.items() if k != "label" and k != "prior_label" and v is not None]
    for person in (answer.get("sellers") or []):
        figures += [(f"seller {person.get('name')} {k}", person.get(k)) for k in ("before", "sold") if person.get(k) is not None]
    for person in (answer.get("others") or []):
        figures += [(f"holder {person.get('name')} before", person.get("before"))] if person.get("before") is not None else []
    for item in (answer.get("rounds") or []):
        figures += [(f"round {item.get('name')} price", item.get("price"))] if item.get("price") is not None else []
    if (answer.get("lockup") or {}).get("days") is not None:
        figures.append(("lock-up days", answer["lockup"]["days"]))
    missing = [label for label, value in figures if not is_printed(value, text)]
    report = {"figures_given": len(figures), "figures_not_printed_in_filing": missing}
    sold = sum(person.get("sold") or 0 for person in (answer.get("sellers") or []))
    if offer:
        report["sellers_add_up"] = {"model_total": sold, "cover": offer.get("holder_shares"), "agree": sold == offer.get("holder_shares")}
    if manual:
        diff = []
        by_name = {p["name"].lower(): p for p in manual.get("sellers", []) + manual.get("others", [])}
        for person in (answer.get("sellers") or []) + (answer.get("others") or []):
            known = by_name.get(str(person.get("name", "")).lower())
            if known and (known["before"] != person.get("before") or known.get("sold", 0) != (person.get("sold") or 0)):
                diff.append(f"{person['name']}: model {person.get('before')}/{person.get('sold')}, hand-made {known['before']}/{known.get('sold', 0)}")
        missed = [p["name"] for p in manual.get("sellers", []) if p["name"].lower() not in {str(x.get("name", "")).lower() for x in (answer.get("sellers") or [])}]
        hand_rounds = {r["name"].lower(): r["price"] for r in manual.get("rounds", [])}
        got_rounds = {str(r.get("name", "")).lower(): r.get("price") for r in (answer.get("rounds") or [])}
        scale = {"dollars": 1e-6, "thousands": 1e-3, "millions": 1}.get(fin.get("unit"), None)
        hand_years = {y["label"]: y for y in manual.get("financials", {}).get("years", [])}
        years = []
        for row in fin.get("years") or []:
            known = hand_years.get(str(row.get("label")))
            if known and scale:
                same = all(row.get(k) is not None and abs(row[k] * scale - known[k]) <= max(1, abs(known[k]) * 0.001) for k in ("revenue", "operating"))
                years.append(f"{row['label']}: {'same' if same else 'DIFFERENT'}")
        report["against_hand_made"] = {"sellers_missed": missed, "holder_differences": diff,
                                       "rounds_same": sum(1 for k, v in hand_rounds.items() if got_rounds.get(k) is not None and abs(got_rounds[k] - v) < 0.0006),
                                       "rounds_in_hand_made": len(hand_rounds), "rounds_from_model": len(got_rounds), "years": years}
    return report


# ---------- the test ----------

def run(fetcher, today, list_path, breakdown_dir, out_path, key, log=print):
    previous = tracker.load_json(out_path, {})
    if previous.get("version") == TEST_VERSION and (previous.get("asked_model") or not key):
        log("model test: nothing new to do")
        return previous
    records = {r["cik"]: r for r in tracker.load_json(list_path, {}).get("ipos", [])}
    report = {"version": TEST_VERSION, "run": today.isoformat(), "asked_model": bool(key), "deals": []}
    for cik in TEST_CIKS:
        record = records.get(cik)
        doc = reader.document_for(record) if record else None
        if not doc:
            continue
        page = fetcher.get(doc[2])
        sections = cut(to_lines(page))
        text = reader.to_text(page)
        offer = reader.offer_terms(text, doc[0])[0]
        user = "\n\n".join(f"=== {name.upper().replace('_', ' ')} ===\n{body}" for name, body in sections.items())
        entry = {"cik": cik, "name": record["name"], "form": doc[0], "date": doc[1], "doc": doc[2],
                 "sections": {name: {"characters": len(body), "starts": body[:90].replace("\n", " / ")} for name, body in sections.items()},
                 "sections_not_found": [name for name in WANTED if name not in sections],
                 "characters_sent": len(user) + len(SYSTEM), "tokens_estimated": round((len(user) + len(SYSTEM)) / 3.5), "models": {}}
        manual = tracker.load_json(Path(breakdown_dir) / f"{cik}.json", None)
        manual = manual if manual and manual.get("method") == "manual" else None
        for model in MODELS if key else []:
            answer, usage, error = ask(model, user, key)
            result = {"tokens": {k: usage.get(k) for k in ("input_tokens", "output_tokens") if k in usage}}
            if "input_tokens" in usage:
                price_in, price_out = PRICES[model]
                result["cost_usd"] = round((usage["input_tokens"] * price_in + usage.get("output_tokens", 0) * price_out) / 1e6, 5)
            if error:
                result["error"] = error
            else:
                result["checks"] = check(answer, text, offer, manual)
                result["answer"] = answer
            entry["models"][model] = result
        report["deals"].append(entry)
    Path(out_path).write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    log(f"model test: {len(report['deals'])} filings cut" + (f", asked {len(MODELS)} models" if key else ", no API key so no model was asked"))
    return report


def main():
    fixtures = os.environ.get("READER_FIXTURES")
    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if not fixtures and "@" not in user_agent:
        sys.exit("Set SEC_USER_AGENT to a name and contact email, for example: IPO Query info@ipoquery.com")
    run(tracker.Fetcher(user_agent, fixtures=fixtures), dt.datetime.now(dt.timezone.utc).date(), tracker.ROOT / "docs" / "data" / "ipos.json",
        tracker.ROOT / "data" / "breakdowns", tracker.ROOT / "data" / "model_test.json", os.environ.get("ANTHROPIC_API_KEY", "").strip())


if __name__ == "__main__":
    main()
