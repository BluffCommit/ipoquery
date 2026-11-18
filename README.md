# IPO Query tracker

The first slice of IPO Query (ipoquery.com): a script that finds companies filing to go
public in the US and works out where each deal stands. It reads SEC EDGAR only,
uses no AI model, and needs no paid service.

## What it produces

- `docs/index.html`: the site, with the list and every IPO page.
- `docs/data/ipos.json`: the same list as data, for the IPO pages built later.
- `data/state.json`: the script's memory, so each run only fetches what is new.

## The site

`site/report.html` is the report view: the homepage and the page for each IPO,
in one file. The tracker writes the data into it and saves the result as
`docs/index.html`. To rebuild the page after changing the template, without
contacting the SEC:

    python tracker.py --render-only

An IPO page shows the full breakdown when `data/breakdowns/<CIK>.json` exists
for that company, and a "not read yet" page otherwise. `2130357.json`
(Anthropic) is the first example, read by hand from the filing. It is the
format the automated reader has to produce: the price range and share counts,
yearly revenue and operating result, who is selling, what each funding round
paid per share, the lock-up schedule and the first risk factors. The page
works out everything else (amounts raised, company value, returns) from those.

## The reader

`reader.py` runs after the tracker. For each upcoming deal, and each deal that
priced in the last 120 days, it downloads the newest prospectus (at most 40 a
run, and each document only once) and writes `data/breakdowns/<CIK>.json`.
This first part uses no model and costs nothing. It reads:

- the offer terms: price range or final price, shares sold by the company and
  by existing holders, shares outstanding afterwards
- revenue, operating or net result and cash, from the figures tagged (XBRL)
  inside the filing. For a deal that has priced it also reads the filing
  before pricing, which has the price range and the tagged statements.

A figure is kept only if a consistency check passes: the filing states it in
two places, the midpoint used in the filing matches the range on the cover,
price times shares equals the total on the cover, or no figure is tagged
with two different values. Anything that fails is left out, and the
page lists what was left out and why. If the offer terms fail, no breakdown is
written. `data/reader.json` records what happened for every deal; when a
share count fails it keeps a short piece of the filing to show why. Direct
listings and other resales have no set price and are marked as such.

The reader never replaces a breakdown marked `"method": "manual"`.

    python test_reader.py      # offline tests, on made-up filings in real wording

Not built yet: who is selling, what earlier rounds paid, lock-ups, use of
proceeds and risks. Those need a model to read the filing.

## How it decides

1. Once a day it reads EDGAR's quarterly filing index and picks out companies
   that filed an S-1 or F-1 (the forms used to register an IPO) or an amendment.
2. For each new company it reads that company's filing history and keeps it only
   if it had not already been filing annual or quarterly reports. That removes
   public companies registering more shares.
3. The stage comes from the types of filing made since:

   | Filing seen | Stage shown |
   |---|---|
   | S-1 or F-1 | Filed |
   | S-1/A or F-1/A | Amended |
   | EFFECT | Cleared by the SEC |
   | 424B4 | Priced |
   | RW | Withdrawn |
   | nothing for 270 days | Stalled |
   | EFFECT, then no 424B4 for 30 days | Cleared, never priced |

4. Every run re-checks deals still in motion, so an amendment, pricing or
   withdrawal shows up at the next run (every 6 hours as set; hourly is
   possible on a public repository). A brand-new filer appears the
   morning after it files, because the index is rebuilt overnight.

Blank-check shells (SPACs) and commodity or crypto trusts also file S-1s. They
are kept in the data, labelled, and hidden on the page unless you tick the box.

Companies it decides are not IPOs are listed with the reason in
`data/state.json` under `skip`, so a missing name can be traced.

## What it cannot tell yet

- **Price range, deal size, exchange.** These are inside the documents. They
  arrive with the next slice, which reads the cover page.
- **Tiny self-run offerings.** Some S-1 filers are very small companies with no
  underwriter. They look the same as real IPOs until the cover page is read.
  The "Has a ticker symbol" box on the page is a rough filter in the meantime:
  it hides most of them, but also hides real deals that have not named a
  symbol yet.

## Set it up on GitHub (about ten minutes)

1. Create a new **public** repository and upload these files, keeping the
   folders as they are. Hourly runs are free on public repositories; on a
   private one they use up the monthly allowance.
2. The SEC requires automated requests to name a contact and blocks ones that
   do not. The workflow sends `IPO Query info@ipoquery.com`. To change it, go
   to Settings, then Secrets and variables, then Actions, then the Variables
   tab, and add a variable named `SEC_USER_AGENT`.
3. Open the Actions tab, choose "Update IPO tracker" and press "Run workflow".
   The first run reads a year of filings and takes several minutes.
4. Go to Settings, then Pages. Under "Build and deployment" choose "Deploy from
   a branch", branch `main`, folder `/docs`. The list appears at the address
   GitHub shows there.

After that it updates itself on the schedule in
`.github/workflows/update.yml`. If updates ever stop, check the
Actions tab: GitHub can pause scheduled jobs on repositories with no activity.

## Run it on your own computer

    SEC_USER_AGENT="IPO Query info@ipoquery.com" python tracker.py

Then open `docs/index.html`.

## Tests

    python test_tracker.py

Runs offline against saved EDGAR-shaped responses: a new filing, an amended
one, a priced one, a withdrawal, a refiling, a foreign issuer, a blank-check
shell, a crypto trust, a stalled deal and an already-public company. It also
checks a real snapshot of Anthropic's filing history.

## Status

Running live since Nov 17, 2026. The first run read a year of EDGAR filings in
about four minutes.
