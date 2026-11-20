# Parxen × Flipkart delivery checker (prototype)

A small local web app that checks whether each Parxen power bank listing on Flipkart
can be delivered to each pincode on your list, and how fast. The result is a grid
of products × pincodes that you can download as Excel.

## Start it

Double-click **`start.bat`**. The first time, it sets everything up (a few minutes,
it downloads a browser). Then it opens **http://localhost:5050**.

Or from a terminal in this folder:

```
.venv\Scripts\python app.py
```

## Use it

| Page | What it's for |
|---|---|
| **Overview** | The latest result for every product × pincode: KPIs, coverage by product, the grid (filter by Metros / State capitals, "gaps only", search), "Run full check", "Export Excel". The ↻ icon on a row re-checks that city for all products; on a column header it re-checks that product at all pincodes. "Retry errors" and "Check unchecked" appear when needed. |
| **Products** | Paste a Flipkart product link to add a new model. Rename inline, pause or remove listings. Every colour or variant is its own listing, so add the exact link you sell. |
| **Pincodes** | Comes preloaded with 8 major metros + all 28 state capitals (30 pincodes; Chandigarh serves Punjab & Haryana). Paste more (`pincode, city, state`) or upload a CSV. |
| **History** | A log of recent jobs (full checks and re-checks). Only the latest result per cell is kept, so there is no archive of old runs. |
| **Settings** | Daily or weekly automatic checks, whether each covers every pincode or a rolling batch, the pace, and how the hosted read-only site is published. |
| **Hosted view** | Opens the read-only page exactly as your team sees it on Vercel. |

The app opens in day (light) mode. The button at the bottom of the sidebar switches to dark mode, and the choice is remembered per browser.
The app uses a neutral pin icon, not the Parxen logo.

What each cell in the grid means:

- **Deliverable**: Flipkart showed "Delivery by …". The exact text is shown, e.g. "Tomorrow, 11 PM". "No COD" is added if cash on delivery isn't offered.
- **Not deliverable**: Flipkart said "Not deliverable at your location".
- **Out of stock**: the listing is sold out or unavailable.
- **Error / Unclear**: the check couldn't read a result. Hover the cell for details. A screenshot is saved in `data/screens/`.

## How it works

`checker.py` drives a real (hidden) Chrome browser through the same steps a person takes:

1. Open the first product, click **Select delivery location**, type the pincode,
   pick the suggestion and click **Confirm**.
2. With that location set, open every product page and read the **Delivery details** block.
3. Start a fresh browser session for the next pincode.

It waits a random 2–5 seconds between pages (change this on the Settings page).
That works out to about **1 minute per pincode for 7 products**. Rough run times:

- 50 pincodes: about 1 hour
- 500 pincodes: about 8 hours

Everything is stored in `data/parxen.db` (SQLite).

## All-India districts and rolling batches

**Pincodes → Add 730 district pincodes** adds one pincode per district, taken from India Post's
All India Pincode Directory (`seed/district_hq_pincodes.csv`):

- the district's head post office whose name matches the district (365 districts)
- else a sub-office named after the district (180)
- else another head office in the district (100)
- else the pincode with the most post offices (92)

The last two are approximate. Edit the CSV or the Pincodes page if a district should use a different town.
Delhi's 11 districts each get their own pincode.

A full check of about 750 pincodes takes roughly 18 hours and loads about 6,800 Flipkart pages, so don't do
that in one go. Instead, set **Settings → Each automatic check covers → A rolling batch**:

- each scheduled run checks the N pincodes that need it most: never checked first, then the oldest results
- with 100 per night (about 2.5 hours), every district is refreshed about every 8 days
- **Next batch** on the Overview runs one batch by hand
- the Overview shows how many pincodes were checked in the last 7 days

If Flipkart blocks a check, automatic checks pause for 24 hours. Batches of more than about 150 a day
from one internet connection raise the chance of captchas, and the block would hit everyone in the office
using Flipkart, so increase the batch size slowly.

## Running it when the app is closed (Windows Task Scheduler)

The in-app schedule only fires while the app is running. To run it at a fixed
time regardless, create a Task Scheduler task:

- **Program:** `D:\Electronics\Parxen_Powerbanks\.venv\Scripts\python.exe`
- **Arguments:** `run_check.py`
- **Start in:** `D:\Electronics\Parxen_Powerbanks`

It publishes the hosted site afterwards if publishing is switched on.

## Hosted read-only site (Vercel)

Two parts:

- **Admin (this PC):** the Flask app. Running checks, re-checks, editing products and pincodes. Never exposed to the internet.
- **Viewer (Vercel):** the `site/` folder, a static page with the report, search, filters and **Download Excel**.
  It has no backend and no forms, so nothing can be changed from it.

After each check the app writes `site/data/latest.json` and `site/data/coverage.xlsx`, then publishes them
(Settings → Hosted read-only site):

- **Git push** (recommended): commits only the `site/` folder and runs `git push`. Vercel redeploys on every push.
- **Vercel CLI:** `npm i -g vercel`, then run `vercel link` once inside `site/`. The app then runs `vercel deploy --prod`.

One-time Vercel setup for Git push: import the GitHub repo in Vercel, set **Root Directory = `site`**,
Framework preset **Other**, no build command. `python publish.py` publishes by hand.

The hosted page is public to anyone with the link. It sends `noindex` headers and `robots.txt` blocks
search engines, but Vercel's password protection is not on the free plan.
`data/` (the database and screenshots) and `.venv/` are in `.gitignore` and never leave this PC.

## Troubleshooting

- **Watch it work:** run `set PARXEN_HEADED=1` then `.venv\Scripts\python app.py`.
  The browser window will be visible.
- **Lots of "Error" cells:** Flipkart may have changed its page layout. The
  selectors are in `checker.py` (`set_location`, `parse_product_page`). Look at the
  screenshots in `data/screens/`.
- **Run says "Blocked":** Flipkart is rate-limiting. Increase the delays on the
  Schedule page and try later.
- **"Flipkart did not suggest pincode":** the pincode doesn't exist, or Flipkart
  doesn't recognise it.

## Caveats

- Flipkart has no official API for this. The tool reads the public product page,
  so a Flipkart redesign can break it until the selectors are updated.
- Flipkart's Terms of Use restrict automated access. Keep the pace slow, and
  ask your Flipkart account manager whether they can provide serviceability
  data officially.
- Results reflect the seller and stock at check time. They can change during the day.
