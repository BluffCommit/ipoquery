#!/usr/bin/env python3
"""Offline tests for tracker.py. Run:  python test_tracker.py

Builds a folder of saved EDGAR-shaped responses and checks that each kind of
company ends up in the right stage. Company names starting with TESTCO are made
up. The Anthropic record is a real snapshot of its EDGAR filing history taken
on Nov 16, 2026.
"""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import tracker

TODAY = dt.date(2026, 11, 16)
HEADER = ("Description:           Master Index of EDGAR Dissemination Feed\n"
          "Last Data Received:    November 13, 2026\n"
          "Comments:              webmaster@sec.gov\n"
          "Anonymous FTP:         ftp://ftp.sec.gov/edgar/\n"
          "Cloud HTTP:            https://www.sec.gov/Archives/\n\n \n \n \n"
          "CIK|Company Name|Form Type|Date Filed|Filename\n" + "-" * 80 + "\n")

# cik: (name, sic, extra submission fields, [(form, date), ...] oldest first)
COMPANIES = {
    9000001: ("TESTCO NEW INC", "7372", {}, [("S-1", "2026-11-03")]),
    9000002: ("TESTCO AMENDED INC", "2834", {}, [("DRS", "2026-05-01"), ("DRS/A", "2026-06-20"), ("S-1", "2026-09-10"), ("S-1/A", "2026-10-01"), ("S-1/A", "2026-10-20")]),
    9000003: ("TESTCO PRICED INC", "3674", {"tickers": ["TPRC"], "exchanges": ["Nasdaq"]},
              [("S-1", "2026-07-10"), ("S-1/A", "2026-08-05"), ("FWP", "2026-09-10"), ("EFFECT", "2026-09-16"), ("8-A12B", "2026-09-16"),
               ("424B4", "2026-09-18"), ("10-Q", "2026-11-10"), ("S-1", "2026-11-13")]),
    9000004: ("TESTCO WITHDRAWN INC", "5812", {}, [("S-1", "2026-02-10"), ("S-1/A", "2026-03-15"), ("RW", "2026-06-01")]),
    9000005: ("TESTCO REFILED INC", "4911", {}, [("S-1", "2024-03-01"), ("RW", "2024-09-01"), ("S-1", "2026-10-01")]),
    9000006: ("TESTCO ALREADY PUBLIC INC", "7372", {}, [("10-K", "2025-03-01"), ("10-Q", "2025-08-10"), ("S-1", "2026-10-15")]),
    9000007: ("TESTCO ACQUISITION CORP", "6770", {}, [("S-1", "2026-09-02")]),
    9000008: ("TESTCO FOREIGN LTD", "7374", {"addresses": {"business": {"stateOrCountry": "K3", "stateOrCountryDescription": "Hong Kong", "isForeignLocation": 1}}},
              [("F-1", "2026-08-20"), ("F-1/A", "2026-10-28")]),
    9000009: ("TESTCO BIG FILER INC", "6021", {"files": [{"name": "CIK0009000009-submissions-001.json"}]}, [("S-1", "2026-10-05")]),
    9000010: ("TESTCO BITCOIN TRUST", "6221", {}, [("S-1", "2026-05-05"), ("S-1/A", "2026-07-01")]),
    9000011: ("TESTCO STALLED INC", "3841", {}, [("S-1", "2025-12-01")]),
    9000012: ("TESTCO EFFECTIVE INC", "6798", {}, [("S-1", "2026-10-01"), ("S-1/A", "2026-11-02"), ("EFFECT", "2026-11-12"), ("8-A12B", "2026-11-12")]),
}

# Real snapshot: data.sec.gov/submissions/CIK0002130357.json as of Nov 16, 2026 (newest first, as EDGAR returns it).
ANTHROPIC = {
    "cik": "0002130357", "entityType": "other", "sic": "7372", "sicDescription": "Services-Prepackaged Software", "name": "Anthropic, PBC",
    "tickers": ["ANT"], "exchanges": [None], "category": "", "stateOfIncorporation": "DE",
    "addresses": {"business": {"street1": "548 MARKET STREET", "street2": "PMB 90375", "city": "SAN FRANCISCO", "stateOrCountry": "CA",
                               "zipCode": "94104", "stateOrCountryDescription": "CA", "isForeignLocation": None}},
    "filings": {"recent": {
        "form": ["FWP", "FWP", "FWP", "FWP", "FWP", "S-1/A", "S-1/A", "S-1", "DRS/A", "DRS/A", "DRS/A", "DRS/A", "DRS/A", "DRS"],
        "filingDate": ["2026-11-12"] * 5 + ["2026-11-10", "2026-11-02", "2026-10-26", "2026-10-09", "2026-10-01", "2026-09-04", "2026-08-10", "2026-07-13", "2026-06-01"],
        "accessionNumber": ["0001628280-26-073045", "0001628280-26-073040", "0001628280-26-072953", "0001628280-26-072952", "0001628280-26-072949",
                            "0001628280-26-072484", "0001628280-26-068676", "0001628280-26-067168", "0002130357-26-000014", "0002130357-26-000009",
                            "0001628279-26-001111", "0001628279-26-000993", "0001628279-26-000861", "0002130357-26-000005"],
        "primaryDocument": ["anthropicpbc-fwpjapanamend.htm", "anthropicpbc-fwpxeeaprospea.htm", "anthropicpbc-fwpauswrapcap.htm", "anthropicpbc-fwpukmaterials.htm",
                            "anthropicpbc-fwpipowebsi.htm", "anthropicpbc-sx1ax2.htm", "anthropicpbc-sx1ax1.htm", "anthropicpbc-sx1.htm"] + ["filename1.htm"] * 6,
    }, "files": []},
}


def submission(cik, name, sic, extra, filings):
    newest_first = list(reversed(filings))
    sub = {"cik": f"{cik:010d}", "name": name, "sic": sic, "sicDescription": "Test industry", "tickers": extra.get("tickers", []), "exchanges": extra.get("exchanges", []),
           "addresses": extra.get("addresses", {"business": {"stateOrCountry": "DE", "stateOrCountryDescription": "DE", "isForeignLocation": None}}),
           "filings": {"recent": {"form": [f for f, _ in newest_first], "filingDate": [d for _, d in newest_first],
                                  "accessionNumber": [f"{cik:010d}-26-{i:06d}" for i in range(len(newest_first), 0, -1)],
                                  "primaryDocument": ["doc.htm"] * len(newest_first),
                                  "acceptanceDateTime": [d + "T12:00:00.000Z" for _, d in newest_first]},
                       "files": extra.get("files", [])}}
    return sub


def build_fixtures(folder, companies):
    folder.mkdir(parents=True, exist_ok=True)
    rows = {}
    everyone = {cik: (name, filings) for cik, (name, _, _, filings) in companies.items()}
    recent = ANTHROPIC["filings"]["recent"]
    everyone[2130357] = ("Anthropic, PBC", list(zip(recent["form"], recent["filingDate"])))
    for cik, (name, filings) in everyone.items():
        for form, date in filings:
            quarter = tracker.quarter_of(dt.date.fromisoformat(date))
            shown = date.replace("-", "") if (cik, form) == (9000001, "S-1") else date     # one row in the daily-index date style
            rows.setdefault(quarter, []).append(f"{cik}|{name}|{form}|{shown}|edgar/data/{cik}/x.txt")
    rows.setdefault((2026, 4), []).append("1000045|SOME OTHER CO|10-K|2026-10-30|edgar/data/1000045/y.txt")
    for year, quarter in tracker.quarters_between(TODAY - dt.timedelta(days=365), TODAY):
        text = HEADER + "\n".join(rows.get((year, quarter), [])) + "\n"
        (folder / tracker.Fetcher.fixture_name(tracker.index_url(year, quarter))).write_bytes(text.encode("latin-1"))
    for cik, (name, sic, extra, filings) in companies.items():
        (folder / tracker.Fetcher.fixture_name(tracker.submissions_url(cik))).write_text(json.dumps(submission(cik, name, sic, extra, filings)))
    (folder / tracker.Fetcher.fixture_name(tracker.submissions_url(2130357))).write_text(json.dumps(ANTHROPIC))


def check(label, got, want):
    if got != want:
        raise AssertionError(f"{label}: got {got!r}, wanted {want!r}")
    print(f"  ok  {label}")


def main():
    work = Path(tempfile.mkdtemp(prefix="tracker-test-"))
    try:
        fixtures, state, out = work / "fixtures", work / "data" / "state.json", work / "docs"
        build_fixtures(fixtures, COMPANIES)
        quiet = lambda *_: None

        print("first run")
        fetcher = tracker.Fetcher("test", fixtures)
        result = tracker.run(fetcher, TODAY, 365, state, out, log=quiet)
        saved = json.loads(state.read_text())
        c, skip = saved["companies"], saved["skip"]
        check("index files read", len(saved["quarters_done"]), 4)
        check("companies tracked", result["tracked"], 11)
        check("new filing", (c["9000001"]["stage"], c["9000001"]["first_filed"], c["9000001"]["sector"]), ("filed", "2026-11-03", "Software and internet"))
        check("amended, with drafts", (c["9000002"]["stage"], c["9000002"]["amendments"], c["9000002"]["draft_date"], c["9000002"]["sector"]), ("amended", 2, "2026-05-01", "Biotech and pharma"))
        check("priced; later follow-on ignored", (c["9000003"]["stage"], c["9000003"]["priced_date"], c["9000003"]["first_filed"], c["9000003"]["tickers"]), ("priced", "2026-09-18", "2026-07-10", ["TPRC"]))
        check("priced: latest document is the final prospectus", c["9000003"]["latest"]["form"], "424B4")
        check("withdrawn", (c["9000004"]["stage"], c["9000004"]["withdrawn_date"]), ("withdrawn", "2026-06-01"))
        check("refiled after withdrawal", (c["9000005"]["stage"], c["9000005"]["first_filed"], c["9000005"]["prior_attempts"]), ("filed", "2026-10-01", 1))
        check("already public is skipped", skip.get("9000006"), "already_public")
        check("blank-check shell flagged", (c["9000007"]["kind"], c["9000007"]["stage"]), ("spac", "filed"))
        check("foreign issuer", (c["9000008"]["stage"], c["9000008"]["form"], c["9000008"]["foreign"], c["9000008"]["location"]), ("amended", "F-1", True, "Hong Kong"))
        check("established filer is skipped", skip.get("9000009"), "established_filer")
        check("crypto trust flagged as fund", c["9000010"]["kind"], "fund")
        check("stalled", (c["9000011"]["stage"], c["9000011"]["stalled"]), ("filed", True))
        check("declared effective", (c["9000012"]["stage"], c["9000012"]["effective_date"], c["9000012"]["listing_date"], c["9000012"]["sector"]), ("effective", "2026-11-12", "2026-11-12", "Real estate"))
        a = c["2130357"]
        check("Anthropic (real snapshot)", (a["stage"], a["amendments"], a["first_filed"], a["draft_date"], a["tickers"], a["exchanges"], a["marketing"], a["kind"], a["sector"], a["location"]),
              ("amended", 2, "2026-10-26", "2026-06-01", ["ANT"], [], True, "company", "Software and internet", "CA"))
        check("Anthropic latest document link", a["latest"]["url"], "https://www.sec.gov/Archives/edgar/data/2130357/000162828026072484/anthropicpbc-sx1ax2.htm")
        page = (out / "index.html").read_text()
        check("page has the data built in", ("/*DATA*/null" not in page, "Anthropic, PBC" in page), (True, True))
        payload = json.loads((out / "data" / "ipos.json").read_text())
        check("json sorted newest activity first", payload["ipos"][0]["name"], "TESTCO EFFECTIVE INC")
        check("stage counts", payload["counts"], {"priced": 1, "amended": 4, "effective": 1, "filed": 4, "withdrawn": 1})

        print("second run, same day")
        fetcher = tracker.Fetcher("test", fixtures)
        result = tracker.run(fetcher, TODAY, 365, state, out, log=quiet)
        check("no index re-read, only active deals re-checked", (fetcher.requests, result["new"], result["changed"]), (result["refreshed"], 0, False))
        check("active deals re-checked", result["refreshed"], 9)

        print("third run, next day, after one company amends and one prices")
        updated = dict(COMPANIES)
        name, sic, extra, filings = updated[9000001]
        updated[9000001] = (name, sic, extra, filings + [("S-1/A", "2026-11-17")])
        name, sic, extra, filings = updated[9000012]
        updated[9000012] = (name, sic, extra, filings + [("424B4", "2026-11-17")])
        build_fixtures(fixtures, updated)
        result = tracker.run(tracker.Fetcher("test", fixtures), TODAY + dt.timedelta(days=1), 365, state, out, log=quiet)
        c = json.loads(state.read_text())["companies"]
        check("amendment picked up", (c["9000001"]["stage"], c["9000001"]["amendments"]), ("amended", 1))
        check("pricing picked up", (c["9000012"]["stage"], c["9000012"]["priced_date"]), ("priced", "2026-11-17"))
        check("output rewritten", result["changed"], True)

        print("guards")
        env = {k: v for k, v in os.environ.items() if k != "SEC_USER_AGENT"}
        refused = subprocess.run([sys.executable, str(Path(tracker.__file__)), "--state", str(work / "s.json"), "--out", str(work / "o")], env=env, capture_output=True, text=True)
        check("refuses to call the SEC without a contact", (refused.returncode != 0, "SEC_USER_AGENT" in refused.stderr), (True, True))
        check("index dates in both styles", list(tracker.parse_index(b"junk\n1|A|S-1|20261103|f\n2|B|F-1|2026-11-04|g\nnot|a|row\n")), [(1, "S-1", "2026-11-03"), (2, "F-1", "2026-11-04")])
        print("\nall checks passed")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
