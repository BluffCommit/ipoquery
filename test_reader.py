#!/usr/bin/env python3
"""Offline tests for reader.py. Run:  python test_reader.py

Every filing here is made up, written in the wording real prospectuses use.
The reader has not yet been run against real filings.
"""
import datetime as dt
import json
import shutil
import tempfile
from pathlib import Path

import reader
import tracker

TODAY = dt.date(2026, 11, 16)


def page(*blocks):
    return ("<html><head><title>S-1</title><style>p{margin:0}</style></head><body><div style='display:none'><ix:header><ix:hidden>801,351,354 9,999,999</ix:hidden></ix:header></div>"
            + "".join(blocks) + "</body></html>").encode()


def p(text):
    return f"<p style='font:10pt Times'><font>{text}</font></p>\n"


def table(*rows):
    cells = lambda row: "".join(f"<td>{c}</td>" for c in row)
    return "<table>" + "".join(f"<tr>{cells(row)}</tr>" for row in rows) + "</table>\n"


FILLER = p("Risk factors. Investing in our common stock involves risk. " * 40)

BIG = page(
    p("Subject to completion, dated November 10, 2026"), p("824,999,999&nbsp;Shares"), p("Class A Common Stock"),
    p("This is an initial public offering of shares of Class&nbsp;A common stock of Testco AI, PBC. We are offering 801,351,354 shares of our Class A common stock "
      "and the selling stockholders identified in this prospectus are offering 23,648,646 shares of our Class A common stock. We will not receive any proceeds from "
      "the sale of shares by the selling stockholders."),
    p("It is currently estimated that the initial public offering price will be between $70.00 and $78.00 per share. We have applied to list our Class A common "
      "stock on the Nasdaq Global Select Market under the symbol &#8220;TAI.&#8221;"),
    p("THE OFFERING"),
    table(("Class A common stock offered by us", "801,351,354 shares"), ("Class A common stock offered by the selling stockholders", "23,648,646 shares"),
          ("Class A common stock to be outstanding after this offering", "9,000,000,000 shares"), ("Class B common stock to be outstanding after this offering", "4,086,672,593 shares"),
          ("Total Class A and Class B common stock to be outstanding after this offering", "13,086,672,593 shares")),
    p("Unless otherwise indicated, this prospectus assumes an initial public offering price of $74.00 per share, which is the midpoint of the estimated offering price range set forth on the cover page."),
    FILLER, p("Consolidated statements of operations (in millions)"),
    table(("", "2023", "2024", "2025"), ("Revenue", "$", "41", "$", "386", "$", "4,585"), ("Loss from operations", "(551)", "(2,980)", "(8,060)")),
    table(("Nine months ended September 30", "2025", "2026"), ("Revenue", "2,545", "33,242"), ("Loss from operations", "(5,224)", "(1,260)")))

SMALL = page(
    p("PRELIMINARY PROSPECTUS"), p("Testco Trading Ltd"),
    p("This is an initial public offering of our ordinary shares. We are offering 1,500,000 ordinary shares, par value US$0.0001 per share. Prior to this offering, there has "
      "been no public market for our ordinary shares. We anticipate that the initial public offering price will be between US$4.00 and US$6.00 per ordinary share."),
    p("We have applied to list our ordinary shares on the Nasdaq Capital Market under the symbol \"TTL\"."),
    table(("Ordinary shares offered by us", "1,500,000 ordinary shares"), ("Ordinary shares outstanding immediately after this offering", "12,500,000 ordinary shares")),
    p("assuming an initial public offering price of US$5.00 per ordinary share, being the mid-point of the estimated range of the initial public offering price"), FILLER)

BLANK = page(p("We are offering&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;shares of our common stock. It is currently estimated that the initial public offering price will be between $&nbsp;&nbsp;&nbsp; and $&nbsp;&nbsp;&nbsp; per share."), FILLER)

PRICED = page(
    p("PROSPECTUS"), p("6,000,000 Shares"),
    p("This is the initial public offering of Testco Chips, Inc. We are offering 5,000,000 shares of our common stock, and the selling stockholders are offering 1,000,000 shares. "
      "The initial public offering price is $21.00 per share. Our common stock has been approved for listing on the New York Stock Exchange under the symbol \"TCHP\"."),
    table(("", "Per Share", "Total"), ("Initial public offering price", "$", "21.00", "$", "126,000,000"), ("Underwriting discounts", "$", "1.26", "$", "7,560,000")),
    table(("Common stock offered by us", "5,000,000 shares"), ("Common stock offered by the selling stockholders", "1,000,000 shares"),
          ("Common stock to be outstanding after this offering", "48,000,000 shares")), FILLER)

DISAGREE = page(
    p("We are offering 5,000,000 shares of our common stock. We expect the initial public offering price to be between $10.00 and $12.00 per share."),
    table(("Common stock offered by us", "5,500,000 shares"), ("Common stock to be outstanding after this offering", "30,000,000 shares")),
    p("an assumed initial public offering price of $11.00 per share, the midpoint of the price range"), FILLER)

HOLDERS_UNREAD = page(
    p("We are offering 4,000,000 shares of our common stock. The selling stockholders have granted the underwriters an option to purchase additional shares. "
      "We expect the initial public offering price to be between $10.00 and $12.00 per share."),
    table(("Common stock offered by us", "4,000,000 shares"), ("Common stock to be outstanding after this offering", "30,000,000 shares")),
    p("an assumed initial public offering price of $11.00 per share, the midpoint of the price range"), FILLER)

ADS = page(
    p("Testco Holdings is offering 7,500,000 American depositary shares, or ADSs. Each ADS represents two Class A ordinary shares. We expect the initial public offering price "
      "to be between US$9.00 and US$11.00 per ADS."),
    table(("ADSs offered by us", "7,500,000 ADSs"), ("Ordinary shares outstanding immediately after this offering", "150,000,000 ordinary shares")),
    p("an assumed initial public offering price of US$10.00 per ADS, the midpoint of the estimated range"), FILLER)

UNCONFIRMED = page(p("We are offering 3,000,000 shares of our common stock. We expect the initial public offering price to be between $10.00 and $12.00 per share."), FILLER)


def fact(start, end, val, form="S-1/A", filed="2026-11-10"):
    row = {"end": end, "val": val, "form": form, "filed": filed, "accn": "0000000000-26-000001"}
    if start:
        row["start"] = start
    return row


FACTS = {"cik": 9100001, "entityName": "Testco AI", "facts": {"us-gaap": {
    "Revenues": {"units": {"USD": [fact("2023-01-01", "2023-12-31", 41e6), fact("2024-01-01", "2024-12-31", 386e6), fact("2025-01-01", "2025-12-31", 4585e6),
                                   fact("2025-01-01", "2025-12-31", 4000e6, filed="2026-10-26"),
                                   fact("2025-01-01", "2025-09-30", 2545e6), fact("2026-01-01", "2026-09-30", 33242e6)]}},
    "OperatingIncomeLoss": {"units": {"USD": [fact("2023-01-01", "2023-12-31", -551e6), fact("2024-01-01", "2024-12-31", -2980e6), fact("2025-01-01", "2025-12-31", -8060e6),
                                              fact("2025-01-01", "2025-09-30", -5224e6), fact("2026-01-01", "2026-09-30", -1260e6)]}},
    "NetIncomeLoss": {"units": {"USD": [fact("2025-01-01", "2025-12-31", -9000e6), fact("2026-01-01", "2026-09-30", -104000e6)]}},
    "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [fact(None, "2025-12-31", 20000e6), fact(None, "2026-09-30", 131845e6)]}},
    "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [fact("2026-01-01", "2026-09-30", 6569e6)]}}}}}
EURO = {"facts": {"ifrs-full": {"ProfitLoss": {"units": {"EUR": [fact("2025-01-01", "2025-12-31", -3e6)]}}}}}
NOT_PRINTED = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [fact("2025-01-01", "2025-12-31", -7654321)]}}}}}

# cik: (name, stage, form of the document, date, document, tagged facts or None)
DEALS = {
    9100001: ("TESTCO AI PBC", "amended", "S-1/A", "2026-11-10", BIG, FACTS),
    9100002: ("Testco Trading Ltd", "amended", "F-1/A", "2026-11-12", SMALL, None),
    9100003: ("TESTCO BLANK INC", "filed", "S-1", "2026-11-03", BLANK, None),
    9100004: ("TESTCO CHIPS INC", "priced", "424B4", "2026-11-13", PRICED, NOT_PRINTED),
    9100005: ("TESTCO DISAGREE INC", "amended", "S-1/A", "2026-11-05", DISAGREE, None),
    9100006: ("TESTCO HOLDERS INC", "amended", "S-1/A", "2026-11-04", HOLDERS_UNREAD, None),
    9100007: ("Testco Holdings", "amended", "F-1/A", "2026-11-06", ADS, EURO),
    9100008: ("TESTCO UNCONFIRMED INC", "amended", "S-1/A", "2026-11-02", UNCONFIRMED, None),
    9100009: ("TESTCO HAND INC", "amended", "S-1/A", "2026-11-01", SMALL, None),
    9100010: ("TESTCO ACQUISITION CORP", "amended", "S-1/A", "2026-11-01", SMALL, None),
    9100011: ("TESTCO WITHDRAWN INC", "withdrawn", "S-1/A", "2026-06-01", SMALL, None),
}


def build(folder):
    fixtures, ipos = folder / "fixtures", []
    fixtures.mkdir()
    for cik, (name, stage, form, date, doc, facts) in DEALS.items():
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/000000000026000001/doc.htm"
        (fixtures / tracker.Fetcher.fixture_name(url)).write_bytes(doc)
        if facts:
            (fixtures / tracker.Fetcher.fixture_name(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")).write_text(json.dumps(facts))
        ipos.append({"cik": cik, "name": name, "kind": "spac" if "ACQUISITION" in name else "company", "stage": stage, "stalled": False, "unpriced": False,
                     "priced_date": date if stage == "priced" else None,
                     "filings": [{"form": "S-1", "date": "2026-09-01", "url": url.replace("doc.htm", "first.htm")}, {"form": form, "date": date, "url": url},
                                 {"form": "FWP", "date": "2026-11-14", "url": url.replace("doc.htm", "fwp.htm")}]})
    (folder / "ipos.json").write_text(json.dumps({"ipos": ipos}))
    (folder / "breakdowns").mkdir()
    (folder / "breakdowns" / "9100009.json").write_text(json.dumps({"cik": 9100009, "method": "manual", "offer": {"low": 1, "high": 2}}))


def check(label, got, want):
    if got != want:
        raise SystemExit(f"FAILED  {label}\n  got   {got!r}\n  want  {want!r}")
    print(f"  ok  {label}")


def main():
    folder = Path(tempfile.mkdtemp())
    try:
        build(folder)
        go = lambda: reader.run(tracker.Fetcher("test", fixtures=folder / "fixtures"), TODAY, folder / "ipos.json", folder / "breakdowns", folder / "reader.json", log=lambda *_: None)
        first = go()
        state = json.loads((folder / "reader.json").read_text())["deals"]
        load = lambda cik: json.loads((folder / "breakdowns" / f"{cik}.json").read_text())
        check("shells and withdrawn deals are not read", (first["queued"], "9100010" in state, "9100011" in state), (9, False, False))

        big = load(9100001)
        check("large deal: offer terms", big["offer"], {"low": 70.0, "high": 78.0, "holder_shares": 23648646, "company_shares": 801351354, "shares_after": 13086672593})
        check("large deal: exchange and source", (big["exchange"], big["basis"], big["method"]), ("Nasdaq", "S-1 amendment filed Nov 10, 2026", "auto"))
        fin = big["financials"]
        check("large deal: years", fin["years"], [{"label": "2023", "revenue": 41.0, "operating": -551.0}, {"label": "2024", "revenue": 386.0, "operating": -2980.0},
                                                  {"label": "2025", "revenue": 4585.0, "operating": -8060.0}])
        check("large deal: part year", fin["ytd"], {"label": "Jan to Sep 2026", "revenue": 33242.0, "operating": -1260.0, "prior_label": "Jan to Sep 2025",
                                                    "prior_revenue": 2545.0, "prior_operating": -5224.0})
        check("large deal: facts", [(f["value"], f["label"]) for f in fin["facts"]],
              [(131845.0, "cash on Sep 30, 2026"), (6569.0, "cash from operations, Jan to Sep 2026"), (-104000.0, "net result, Jan to Sep 2026")])
        check("large deal: nothing left out, five checks", (big["hidden"], len(big["checks"])), ([], 5))

        small = load(9100002)
        check("small foreign deal: offer terms", small["offer"], {"low": 4.0, "high": 6.0, "holder_shares": 0, "company_shares": 1500000, "shares_after": 12500000})
        check("small foreign deal: untagged financials are left out", ("financials" in small, [h["item"] for h in small["hidden"]]), (False, ["Revenue, results and cash"]))

        check("blank price range: no breakdown", (state["9100003"]["result"], state["9100003"]["notes"]), ("not confirmed", ["no price range in this filing yet"]))

        priced = load(9100004)
        check("priced deal: final price and shares", priced["offer"], {"price": 21.0, "holder_shares": 1000000, "company_shares": 5000000, "shares_after": 48000000})
        check("priced deal: price checked against the cover total", priced["checks"][-2]["how"], "price times shares sold equals the total on the cover")
        check("priced deal: tagged figures not in the printed statements are left out", ("financials" in priced, priced["hidden"][0]["why"]),
              (False, "the tagged figures for the latest year could not be found in the printed statements"))

        check("cover and summary disagree: no breakdown", (state["9100005"]["result"], (folder / "breakdowns" / "9100005.json").exists()), ("not confirmed", False))
        check("holders selling an unread number: no breakdown", state["9100006"]["notes"], ["existing holders are selling, but the number of shares could not be read"])
        ads = load(9100007)
        check("depositary shares: no company value, non-dollar accounts left out", ("shares_after" in ads["offer"], ads["offer"]["company_shares"], len(ads["hidden"]),
              ads["hidden"][1]["why"]), (False, 7500000, 2, "the company reports in EUR, not US dollars"))
        check("range stated once and nowhere else: no breakdown", state["9100008"]["result"], "not confirmed")
        check("a hand-made breakdown is kept", (load(9100009)["method"], state["9100009"]["note"]), ("manual", "a hand-made breakdown exists and was kept"))

        second = go()
        check("second run reads nothing again", (second["queued"], second["written"]), (0, 0))

        # the blank filing is amended and now has a range: the date the range appeared is recorded
        ipos = json.loads((folder / "ipos.json").read_text())
        for r in ipos["ipos"]:
            if r["cik"] == 9100003:
                url = r["filings"][1]["url"].replace("doc.htm", "doc2.htm")
                r["filings"].append({"form": "S-1/A", "date": "2026-11-15", "url": url})
                (folder / "fixtures" / tracker.Fetcher.fixture_name(url)).write_bytes(SMALL)
        (folder / "ipos.json").write_text(json.dumps(ipos))
        third = go()
        check("an amended filing is read again and the range date recorded", (third["queued"], load(9100003)["range_set"]), (1, "2026-11-15"))
        check("text: hidden tagging data is ignored", "9,999,999" in reader.to_text(BIG), False)
        print("\nall checks passed")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
