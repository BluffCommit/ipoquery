# IPO Query tracker

The first slice of IPO Query (ipoquery.com): a script that finds companies filing to go
public in the US and works out where each deal stands. It reads SEC EDGAR only,
uses no AI model, and needs no paid service.

## What it produces

- `docs/index.html`: the site, with the list and every IPO page.
- `docs/data/ipos.json`: the same list as data, for the IPO pages built later.
- `data/state.json`: the script's memory, so each run only fetches what is new.

## Hosting

The site is served by GitHub Pages from the `docs/` folder of `main`, at
ipoquery.com. `docs/CNAME` (written by GitHub when the custom domain is set)
holds the domain; `docs/.nojekyll` tells Pages to serve the files as they are.

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
priced in the last 120 days, it downloads the newest prospectus (at most 80 a
run, and each document only once) and writes `data/breakdowns/<CIK>.json`.
This first part uses no model and costs nothing. It reads:

- the offer terms: price range or final price, shares sold by the company and
  by existing holders, shares outstanding afterwards
- for a deal that has priced, the price range from the filing before pricing
- revenue, operating or net result and cash, but only for a company that
  already files tagged (XBRL) reports. IPO filings themselves carry no tagged
  financial statements, so for most deals this waits for the model step.

A figure is kept only if a consistency check passes: the filing states it in
two places, the midpoint used in the filing matches the range on the cover,
price times shares equals the total on the cover, or a tagged figure also
appears in the printed statements. Anything that fails is left out, and the
page lists what was left out and why. If the offer terms fail, no breakdown is
written. `data/reader.json` records what happened for every deal; when a
share count fails it keeps a short piece of the filing to show why. Direct
listings and other resales have no set price and are marked as such.

The reader does not replace a breakdown marked `"method": "manual"` until the
deal prices; after that the hand-made page is out of date and the automatic
one takes over.

    python test_reader.py      # offline tests, on made-up filings in real wording

## The model step

`reader_model.py` runs next, if an `ANTHROPIC_API_KEY` secret is set. For each
breakdown the reader produced, it cuts the relevant sections out of the
prospectus (about 28,000 tokens) and has two models read them separately. It
adds the printed financial figures, who is selling, what earlier rounds paid,
the lock-up, and plain-English summaries of the business, the use of proceeds
and the first risk factors. A figure is added only when:

- both readers give the same number, and it is printed in the filing
- the named sellers' shares add up to the number on the cover
- round prices are per share of common stock: the conversion price of each
  round, or its issue price where the filing says conversion is one for one
- the highest round price is between a quarter of the IPO price and three
  times it (otherwise a share split has usually been missed)
- other large holders are shown only when the ownership table has a single
  share class

A third, stronger model also read 32 deals as a comparison; every
disagreement is in `data/model_compare.json`. The readers' answers are saved
in `data/model_answers/`, so a change to the checks can be applied again
without paying to ask the models again. Cost is about 7 cents a filing.

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
