# Belconnen Sold-History — Live (Vercel + Supabase)

A live, always-current viewer for your scraped Belconnen data. The scraper keeps
pushing to Supabase; this site reads Supabase **server-side** through a serverless
API, so the data is live and your secret key is never exposed to the browser.

```
Scraper (your VM)  ->  Supabase (belconnen_sold)  ->  Vercel API (/api/*)  ->  Browser UI
```

## What's in here

```
verceldeploy/
  index.html            # the UI (cards, search, filters, sort, pagination, detail modal)
  api/
    listings.js         # GET /api/listings  — paginated/searchable/filterable list
    listing.js          # GET /api/listing?id=… — one full record (for the modal)
    facets.js           # GET /api/facets    — distinct values for the filter dropdowns
  lib/supabase.js       # shared helper: env, sanitisation, query builder (server-side only)
  vercel.json           # function settings
  package.json          # Node >=18 (uses built-in fetch — no dependencies to install)
  .env.local.example    # template for local env vars
  .gitignore
```

## Security model (read this)

- The **service-role key lives only in `process.env`** on the server. It is never
  sent to the browser and never appears in any API response.
- Because the key bypasses RLS, the API is deliberately locked down: it only ever
  does **GET** selects against the single `belconnen_sold` table, with **whitelisted**
  columns, sort options and filters, and **sanitised** user input — so it can't be
  used to read other tables, inject filters, or write data.
- **You do NOT need an RLS read policy** for this setup (that's only for the anon-key
  approach). You can keep RLS fully enabled with no public policies.
- ⚠️ The service key that was shared earlier should be **rotated** in Supabase
  (Project Settings → API → roll the service-role key) and the new value used below.

## Deploy — Option 1: GitHub (recommended, auto-deploys on push)

1. Put this `verceldeploy` folder in a Git repo and push to GitHub.
2. In Vercel: **Add New… → Project → Import** the repo. Framework preset = **Other**
   (no build step needed). Root directory = the folder containing `index.html`.
3. **Settings → Environment Variables**, add for all environments:
   - `SUPABASE_URL` = `https://YOUR-PROJECT.supabase.co`
   - `SUPABASE_SERVICE_KEY` = your **rotated** service-role key
4. **Deploy.** Your site is live at `https://<project>.vercel.app`. Every push
   redeploys; the data itself is always live (read from Supabase on each request).

## Deploy — Option 2: Vercel CLI

```bash
cd verceldeploy
npm i -g vercel          # once
vercel                   # follow prompts to link/create the project
# add env vars (or do it in the dashboard):
vercel env add SUPABASE_URL
vercel env add SUPABASE_SERVICE_KEY
vercel --prod            # promote to production
```

## Run locally

```bash
cp .env.local.example .env.local     # then fill in real values
vercel dev                           # serves index.html + /api on http://localhost:3000
```

## How it behaves (production-hardened)

- **Live + cached:** list responses carry `Cache-Control: s-maxage=30` (detail 60s,
  facets 300s) so the site is fast and Supabase isn't hammered, while staying current
  within seconds. Lower these if you want even fresher data.
- **Pagination:** server-side `limit`/`offset` (page size capped at 100). Total count
  comes from PostgREST's `Content-Range`.
- **Errors never break the page:** missing config → generic 500; upstream issues → 502;
  the UI shows a friendly message with a Retry button. Facets fail silently (dropdowns
  just show "All"). All secrets are kept out of error messages.
- **XSS-safe:** every value from the database is HTML-escaped before rendering.

## Scaling notes (when the dataset grows large)

- **Price/date sorting:** price is stored as formatted text (`$1,200,000`), so a numeric
  price sort isn't offered (it would mis-order). If you need true numeric/date sorting at
  scale, add `price_numeric` / `sold_at` columns in Supabase and extend the `SORTS`
  whitelist in `lib/supabase.js`.
- **Facets:** `api/facets.js` dedupes a capped (5,000-row) sample per column. For very
  large tables, replace it with a Postgres materialised view or an RPC and point the
  endpoint at that.
- **Exact counts** on huge tables can be slow; switch `Prefer: count=exact` to
  `count=estimated` in `api/listings.js` if needed.
