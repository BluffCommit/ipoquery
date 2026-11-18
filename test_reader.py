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
          ("Class A common stock to be outstanding after this offering and the concurrent private placement", "7,427,991,791 shares (7,551,741,791 shares if the underwriters exercise their option in full)"),
          ("Class N common stock to be outstanding after this offering and the concurrent private placement", "5,658,680,800 shares"),
          ("Class T common stock to be outstanding after this offering and the concurrent private placement", "1 share"),
          ("Class F common stock, all classes together, to be outstanding after this offering and the concurrent private placement", "13,086,672,593 shares")),
    p("Unless otherwise indicated, this prospectus assumes an initial public offering price of $74.00 per share, which is the midpoint of the estimated offering price range set forth on the cover page."),
    FILLER, p("Consolidated statements of operations (in millions)"),
    table(("", "2023", "2024", "2025"), ("Revenue", "$", "41", "$", "386", "$", "4,585"), ("Loss from operations", "(551)", "(2,980)", "(8,060)")),
    table(("Nine months ended September 30", "2025", "2026"), ("Revenue", "2,545", "33,242"), ("Loss from operations", "(5,224)", "(1,260)")))

SMALL = page(
    p("PRELIMINARY PROSPECTUS"), p("Testco Trading Ltd"),
    p("This is an initial public offering of our ordinary shares. We are offering an aggregate of 1,500,000 ordinary shares, par value US$0.0001 per share. Prior to this offering, there has "
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

BEFORE_PRICING = page(p("We are offering 5,000,000 shares of our common stock. We expect the initial public offering price to be between $19.00 and $22.00 per share."),
                      p("an assumed initial public offering price of $20.50 per share, which is the midpoint of the price range"), FILLER)

# wordings met on the first live run
IN_THIS_OFFERING = page(
    p("This is the initial public offering of 20,000,000 shares of Class A common stock of Testco Insurance Group Inc."),
    table(("", "Per Share", "Total"), ("Initial public offering price", "$", "12.00", "$", "240,000,000.00")),
    table(("Class A common stock offered by us in this offering", "20,000,000 shares (or 23,000,000 shares if the underwriters exercise their option in full)"),
          ("Class A common stock to be outstanding immediately after this offering", "39,097,192 shares (or 42,097,192 shares if the underwriters exercise their option in full)"),
          ("Class B common stock to be outstanding immediately after this offering", "59,935,260 shares"),
          ("Total common stock to be outstanding immediately after this offering", "99,032,452 shares")), FILLER)
SHARES_OFFERED = page(
    p("1,875,000 Ordinary Shares"), p("This is the initial public offering of our ordinary shares."),
    table(("", "Per Share", "Total"), ("Initial public offering price", "$", "8.00", "$", "15,000,000")),
    table(("Shares offered:", "1,875,000 Ordinary Shares (or 2,156,250 shares if the underwriters exercise the over-allotment in full)."),
          ("Shares to be outstanding after this offering: (1)", "12,064,525 Ordinary Shares (or 12,345,775 shares if the underwriters exercise the over-allotment option in full).")), FILLER)
UNLIKE_CLASSES = page(
    p("We are offering a total of 9,722,223 Class A ordinary shares, CHF 0.006 par value, of Testco Holding AG."),
    table(("", "Per Share", "Total"), ("Public offering price", "$", "18.00", "$", "175,000,014.00")),
    table(("Class A Ordinary Shares Offered by Us", "9,722,223 Class A ordinary shares"),
          ("Class A Ordinary Shares to be Outstanding After This Offering", "38,594,629 Class A ordinary shares (40,052,962 if the over-allotment option is exercised in full)"),
          ("Class B Voting Rights Shares to be Outstanding After This Offering", "143,888,790 Class B voting rights shares")), FILLER)
WEAK_PRICE = page(p("We are offering 300,000 shares of our common stock. The public offering price is $1.00 per share. The public offering price of $1.00 per share was set by us."),
                  table(("Common stock offered by us", "300,000 shares"), ("Common stock to be outstanding after this offering", "15,990,000 shares")), FILLER)
MOST_OF_COMPANY = page(p("We are offering 16,666,667 shares of our common stock. We expect the initial public offering price to be between $11.00 and $13.00 per share."),
                       table(("Common stock offered by us", "16,666,667 shares"), ("Common stock to be outstanding immediately after this offering", "20,776,793 shares")),
                       p("an assumed initial public offering price of $12.00 per share, which is the midpoint of the price range"), FILLER)
# the cover names another seller in words the company's own sentence uses; only the total on the cover settles it
TWO_STORIES = page(
    p("Subject to completion, dated November 6, 2026"), p("2,500,000 Shares"), p("Common Stock"),
    p("This is a firm commitment initial public offering. Testco Parent Co., Ltd. is offering 1,000,000 shares and the selling stockholders are offering 1,000,000 shares in total. "
      "We expect the initial public offering price to be between $18.00 and $22.00 per share."),
    table(("Common Stock Offered by us", "1,500,000 shares offered by the Company"), ("Common Stock offered by the selling stockholders", "1,000,000 shares"),
          ("Common Stock to be outstanding after this offering", "14,750,000 shares")),
    p("an assumed initial public offering price of $20.00 per share, the midpoint of the price range"), FILLER)
DIRECT = page(p("This prospectus relates to the registration of the resale of up to 70,728,866 shares of our common stock by the registered stockholders. "
                "We are not selling any shares and will not receive any proceeds."), FILLER)

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
    9100012: ("TESTCO WEAK PRICE INC", "priced", "424B4", "2026-10-19", WEAK_PRICE, None),
    9100013: ("TESTCO INSURANCE GROUP INC", "priced", "424B4", "2026-10-20", IN_THIS_OFFERING, None),
    9100014: ("Testco Tickets Ltd", "priced", "424B4", "2026-10-21", SHARES_OFFERED, None),
    9100015: ("Testco Holding AG", "priced", "424B4", "2026-10-22", UNLIKE_CLASSES, None),
    9100016: ("TESTCO DIRECT INC", "priced", "424B4", "2026-10-23", DIRECT, None),
    9100017: ("TESTCO NONVOTING INC", "amended", "S-1/A", "2026-11-09", MOST_OF_COMPANY, None),
    9100018: ("TESTCO TWO STORIES CORP", "amended", "S-1/A", "2026-11-06", TWO_STORIES, None),
}


def build(folder):
    fixtures, ipos = folder / "fixtures", []
    fixtures.mkdir()
    for cik, (name, stage, form, date, doc, facts) in DEALS.items():
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/000000000026000001/doc.htm"
        (fixtures / tracker.Fetcher.fixture_name(url)).write_bytes(doc)
        if doc is PRICED:
            (fixtures / tracker.Fetcher.fixture_name(url.replace("doc.htm", "first.htm"))).write_bytes(BEFORE_PRICING)
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


LONG = page(
    p("Subject to completion, dated November 10, 2026"), p("We are offering 2,000,000 shares and the selling stockholders are offering 1,000,000 shares."),
    p("TABLE OF CONTENTS"), table(("Prospectus Summary", "1"), ("Risk Factors", "14")), p("Risk Factors"), p("Use of Proceeds"), p("Principal and Selling Stockholders"),
    p("PROSPECTUS SUMMARY"), p("Testco makes widgets for hospitals. " * 30), p("The Offering"), table(("Common stock offered by us", "2,000,000 shares")),
    p("Summary Consolidated Financial Data"), table(("(in thousands)", "2024", "2025"), ("Revenue", "$", "12,345", "$", "23,456"), ("Loss from operations", "(1,111)", "(2,222)"),
                                                    ("Net loss", "(1,500)", "(2,900)"), ("Cash and cash equivalents", "", "8,765")),
    p("RISK FACTORS"), p("We have a history of losses and may never be profitable."), p("We have incurred losses in every year. " * 30),
    p("We depend on a single supplier for our main component."), p("Our supplier could stop supplying us. " * 30), p("USE OF PROCEEDS"),
    p("We will use the money for general corporate purposes, including about 40% for a new factory. " * 3),
    p("PRINCIPAL AND SELLING STOCKHOLDERS"), table(("Jane Roe(1)", "5,000,000", "600,000"), ("Venture Fund I", "9,000,000", "400,000"), ("John Doe", "3,000,000", "-")),
    p("SHARES ELIGIBLE FOR FUTURE SALE"), p("Our directors and officers have agreed not to sell shares for 180 days after the date of this prospectus. " * 3),
    p("The underwriting discount is $1.26 per share."), p("Convertible notes"), p("The notes have an issuance price of $125.00 per unit."),
    p("INDEX TO CONSOLIDATED FINANCIAL STATEMENTS"), p("Notes. " * 50), p("8. Convertible Preferred Stock"),
    table(("Series", "Shares", "Original issue price", "Conversion price"), ("Series A", "4,000,000", "$1.25", "$9.375"), ("Series B", "2,000,000", "$6.00", "$45.00")),
    p("Item 15. Recent Sales of Unregistered Securities."), p("In March 2024 we sold 4,000,000 shares of Series A preferred stock at $1.25 per share. " * 2))

GOOD = {"share_classes": 1, "what": "Testco makes widgets for hospitals.", "short": "Testco", "proceeds": "It will spend about 40% on a new factory and the rest on general costs.",
        "financials": {"unit": "thousands", "currency": "USD", "years": [{"period_end": "2024-12-31", "revenue": 12345, "operating": -1111, "net": -1500},
                                                                         {"period_end": "2025-12-31", "revenue": 23456, "operating": -2222, "net": -2900}],
                       "part_year": None, "cash": {"value": 8765, "date": "2025-12-31"}},
        "sellers": [{"name": "Jane Roe", "role": "Founder", "before": 5000000, "sold": 600000}, {"name": "Venture Fund I", "role": "Venture investor", "before": 9000000, "sold": 400000}],
        "others": [{"name": "John Doe", "role": "Director", "before": 3000000}],
        "rounds": [{"name": "Series A", "when": "2024", "price": 1.25, "conversion_price": 9.375}, {"name": "Series B", "when": None, "price": 6.0, "conversion_price": 45.0}],
        "preferred_converts": "other",
        "lockup": {"days": 180, "summary": "Directors and officers cannot sell for 180 days.", "early": False},
        "risks": ["It has lost money every year.", "It relies on one supplier.", "A third risk.", "A fourth risk.", "A fifth risk."]}


def model_checks(folder):
    import copy
    import reader_model as rm
    sections = rm.cut(rm.to_lines(LONG))
    check("sections: all found, the real section rather than its contents-page entry",
          (sorted(sections), sections["holders"].split("\n")[1], "Series B 2,000,000 $6.00 $45.00" in sections["preferred"], "issuance price of $125" in sections["preferred"]),
          (sorted(rm.WANTED), "Jane Roe(1) 5,000,000 600,000", True, False))
    check("sections: risk factors keep the headings and drop the long explanations", sections["risks"].split("\n")[:3],
          ["RISK FACTORS", "We have a history of losses and may never be profitable.", "We depend on a single supplier for our main component."])
    text, offer = reader.to_text(LONG), {"low": 18.0, "high": 22.0, "company_shares": 2000000, "holder_shares": 1000000}
    fields, checks, hidden = rm.merge(GOOD, copy.deepcopy(GOOD), text, offer)
    check("two readers agree: everything is kept", (sorted(fields), hidden, len(checks)),
          (["financials", "lockup", "others", "proceeds", "risks", "risks_note", "rounds", "rounds_note", "sellers", "short", "what"], [], 4))
    check("figures are turned into millions, with cash and the net result",
          (fields["financials"]["years"][1], [(f["value"], f["label"]) for f in fields["financials"]["facts"]]),
          ({"label": "2025", "revenue": 23.456, "operating": -2.222}, [(8.765, "cash on Dec 31, 2025"), (-2.9, "net result, 2025")]))

    def changed(edit):
        other = copy.deepcopy(GOOD)
        edit(other)
        return other
    left = lambda first, second: {h["item"]: h["why"] for h in rm.merge(first, second, text, offer)[2]}
    check("readers differ on one holder: the sellers are left out, the rest stays",
          left(GOOD, changed(lambda a: a["sellers"][0].update(before=5100000))), {"Who is selling": "the two AI readers gave different names or figures"})
    both = changed(lambda a: a["sellers"][1].update(sold=300000))
    check("holders that do not add up to the cover are left out", left(both, copy.deepcopy(both))["Who is selling"], "the named holders' shares add up to 900,000, not the 1,000,000 on the cover")
    both = changed(lambda a: a["financials"]["years"][1].update(revenue=99999))
    check("a figure both readers give but the filing does not print is left out", left(both, copy.deepcopy(both)), {"Revenue, results and cash": "a figure the readers gave is not printed in the filing"})
    check("rounds use the conversion price, which is per share of common stock", (rm.merge(GOOD, copy.deepcopy(GOOD), text, offer)[0]["rounds"], rm.merge(GOOD, copy.deepcopy(GOOD), text, offer)[0]["rounds_note"][:42]),
          ([{"name": "Series A", "when": "2024", "price": 9.375}, {"name": "Series B", "when": None, "price": 45.0}], "Prices are each round's conversion price: "))
    both = changed(lambda a: [r.update(conversion_price=None) for r in a["rounds"]])
    check("with no conversion price and no one-for-one statement, rounds are left out", left(both, copy.deepcopy(both))["What earlier rounds paid"],
          "the filing does not say, in a way both AI readers agree on, what each round paid per share of common stock")
    both = changed(lambda a: ([r.update(conversion_price=None) for r in a["rounds"]], a.update(preferred_converts="one_for_one")))
    kept = rm.merge(both, copy.deepcopy(both), text, offer)[0]
    check("issue prices are used when the filing says conversion is one for one", ([r["price"] for r in kept["rounds"]], kept["rounds_note"][:47]), ([1.25, 6.0], "Prices are each round's original issue price. T"))
    both = changed(lambda a: a["rounds"][1].update(conversion_price=125.0))
    check("a round priced far above the IPO is left out", left(both, copy.deepcopy(both))["What earlier rounds paid"][:52], "the highest round price is less than a quarter of th")
    both = changed(lambda a: a.update(share_classes=2))
    check("with two share classes the other holders are left out", ("others" in rm.merge(both, both, text, offer)[0], left(both, both)), (False, {"Other large holders": "the ownership table splits holdings between share classes, and they are not added up here"}))
    both = changed(lambda a: a.update(proceeds="It will raise $84.6 billion."))
    check("a summary with a number the filing does not print is dropped", "proceeds" in rm.merge(both, both, text, offer)[0], False)
    messy = changed(lambda a: (a["rounds"][0].update(price="$1.25"), a["sellers"].append({"name": "John Doe", "role": "Director", "before": "3,000,000", "sold": 0}), a.update(others=[])))
    tidied = rm.tidy(messy)
    check("answers are tidied: text figures become numbers, non-sellers leave the sellers list",
          (tidied["rounds"][0]["price"], [r["name"] for r in tidied["sellers"]], tidied["others"][0]["before"], rm.merge(GOOD, tidied, text, offer)[2]), (1.25, ["Jane Roe", "Venture Fund I"], 3000000, []))
    part = changed(lambda a: a["financials"].update(part_year={"period_end": "2026-09-30", "revenue": 23456, "operating": -1111, "net": -1500, "prior_revenue": 12345, "prior_operating": -2222, "prior_net": -2900}))
    check("a part year is labelled from its dates", (lambda y: (y["label"], y["prior_label"]))(rm.merge(part, copy.deepcopy(part), text, offer)[0]["financials"]["ytd"]), ("Jan to Sep 2026", "Jan to Sep 2025"))
    no_summary = rm.cut(rm.to_lines(LONG.replace(b"Summary Consolidated Financial Data", b"Consolidated Statements of Operations")))
    check("sections: with no summary table, the income statement is used", no_summary["summary_financials"].split("\n")[1], "(in thousands) 2024 2025")
    check("differences between readers are listed", rm.differences(GOOD, changed(lambda a: (a["rounds"][0].update(price=12.5), a["lockup"].update(days=90)))),
          ["rounds series a price: 1.25 / 12.5", "lock-up days: 180 / 90"])

    # a whole run, with the models replaced by canned answers
    url = "https://www.sec.gov/x/long.htm"
    (folder / "fixtures" / tracker.Fetcher.fixture_name(url)).write_bytes(LONG)
    (folder / "list.json").write_text(json.dumps({"ipos": [{"cik": 9200001, "name": "TESTCO WIDGETS INC"}, {"cik": 9100009, "name": "TESTCO HAND INC"}]}))
    bdir = folder / "model_breakdowns"
    bdir.mkdir()
    (bdir / "9200001.json").write_text(json.dumps({"cik": 9200001, "method": "auto", "doc": url, "offer": offer, "checks": [{"item": "Price range", "how": "x"}],
                                                   "hidden": [{"item": "Revenue, results and cash", "why": "not tagged"}]}))
    (bdir / "9100009.json").write_text(json.dumps({"cik": 9100009, "method": "manual", "doc": url, "offer": offer}))
    asked = []
    rm.ask = lambda model, user, key: (asked.append(model), (copy.deepcopy(GOOD), {"input_tokens": 27000, "output_tokens": 2000}, None))[1]
    go = lambda: rm.run(tracker.Fetcher("test", fixtures=folder / "fixtures"), TODAY, folder / "list.json", bdir, folder / "compare.json", "key", log=lambda *_: None)
    report = go()
    b = json.loads((bdir / "9200001.json").read_text())
    check("run: each deal gets the two readers, hand-made breakdowns are left alone", (sorted(asked), json.loads((bdir / "9100009.json").read_text()).get("model")),
          (["claude-haiku-5-5", "claude-sonnet-5-5"], None))
    check("run: the breakdown gains the checked fields and loses the 'not tagged' note", ("sellers" in b, "financials" in b, [h["item"] for h in b["hidden"]], len(b["checks"])), (True, True, [], 5))
    check("run: the log records agreement and cost", (report["later"][0]["readers_differ"], report["spent_usd"]), ([], 0.0777))
    asked.clear()
    go()
    check("run: a deal already read is not read again", asked, [])
    rm.CHECKS_VERSION, rm.MODEL_VERSION = 2, [rm.PROMPT_VERSION, 2]
    go()
    check("run: changed checks are applied from the saved answers, without asking again", (asked, json.loads((bdir / "9200001.json").read_text())["model"]["key"][1]), ([], [rm.PROMPT_VERSION, 2]))
    check("without a key nothing is asked", rm.run(None, TODAY, folder / "list.json", bdir, folder / "compare.json", "", log=lambda *_: None), {})


def main():
    folder = Path(tempfile.mkdtemp())
    try:
        build(folder)
        go = lambda: reader.run(tracker.Fetcher("test", fixtures=folder / "fixtures"), TODAY, folder / "ipos.json", folder / "breakdowns", folder / "reader.json", log=lambda *_: None)
        first = go()
        state = json.loads((folder / "reader.json").read_text())["deals"]
        load = lambda cik: json.loads((folder / "breakdowns" / f"{cik}.json").read_text())
        check("shells and withdrawn deals are not read", (first["queued"], "9100010" in state, "9100011" in state), (16, False, False))

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
        check("a final price with no cover total and no earlier range: no breakdown", state["9100012"]["notes"][0], "the final price is not confirmed by a total on the cover or by an earlier price range")

        small = load(9100002)
        check("small foreign deal: offer terms", small["offer"], {"low": 4.0, "high": 6.0, "holder_shares": 0, "company_shares": 1500000, "shares_after": 12500000})
        check("small foreign deal: untagged financials are left out", ("financials" in small, [h["item"] for h in small["hidden"]]), (False, ["Revenue, results and cash"]))
        check("small foreign deal: says why", small["hidden"][0]["why"], "the financial statements are printed in the filing but not tagged, and the printed ones have not been read yet")

        check("blank price range: no breakdown", (state["9100003"]["result"], state["9100003"]["notes"]), ("not confirmed", ["no price range in this filing yet"]))

        priced = load(9100004)
        check("priced deal: final price, shares, and the range from the filing before", priced["offer"],
              {"price": 21.0, "holder_shares": 1000000, "company_shares": 5000000, "shares_after": 48000000, "low": 19.0, "high": 22.0})
        check("priced deal: price checked against the cover total", [c["how"] for c in priced["checks"] if c["item"] == "Final price"], ["price times shares sold equals the total on the cover"])
        check("priced deal: tagged figures not in the printed statements are left out", ("financials" in priced, priced["hidden"][-1]["why"]),
              (False, "the tagged figures for the latest year could not be found in the printed statements"))

        check("cover and summary disagree: no breakdown", (state["9100005"]["result"], (folder / "breakdowns" / "9100005.json").exists()), ("not confirmed", False))
        check("holders selling an unread number: no breakdown", state["9100006"]["notes"], ["existing holders are selling, but the number of shares could not be read"])
        check("a failed share count keeps a short piece of the filing in the log", state["9100005"]["notes"][1].startswith("near:"), True)
        ads = load(9100007)
        check("depositary shares: no company value, non-dollar accounts left out", ("shares_after" in ads["offer"], ads["offer"]["company_shares"], len(ads["hidden"]),
              ads["hidden"][1]["why"]), (False, 7500000, 2, "the company reports in EUR, not US dollars"))
        check("'offered by us in this offering', with the filing's own total", load(9100013)["offer"],
              {"price": 12.0, "holder_shares": 0, "company_shares": 20000000, "shares_after": 99032452})
        check("'Shares offered:' with a footnote mark", load(9100014)["offer"], {"price": 8.0, "holder_shares": 0, "company_shares": 1875000, "shares_after": 12064525})
        unlike = load(9100015)
        check("share classes that are not alike are not added up", (unlike["offer"], unlike["hidden"][0]["why"]),
              ({"price": 18.0, "holder_shares": 0, "company_shares": 9722223}, "the share classes are not alike, so their counts cannot simply be added up"))
        check("a sale of most of the company gets no company value", ("shares_after" in load(9100017)["offer"], load(9100017)["hidden"][0]["why"][:41]),
              (False, "the shares sold would be more than 60% of"))
        two = load(9100018)
        check("cover and summary disagree, but one pair adds up to the cover total", (two["offer"]["company_shares"], two["offer"]["holder_shares"], two["checks"][-2]["how"]),
              (1500000, 1000000, "the two add up to the total number of shares on the cover"))
        check("a direct listing is recognised, not read", state["9100016"]["result"], "resale")
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
        model_checks(folder)
        print("\nall checks passed")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
