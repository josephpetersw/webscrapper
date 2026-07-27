# CLAUDE.md

Guidance for Claude Code (or any future contributor) working in this repository.

## What this project is

**XphoneKenyaScraper** (repo name: `webscrapper`) is a concurrent web scraper and
data-management dashboard purpose-built for **phoneplacekenya.com** (a
Cloudflare-protected WooCommerce storefront). It crawls the site's product
sitemap, extracts structured product data (title, price, brand, categories,
images, descriptions), downloads images, and stores everything on disk under
`data/`. A Flask backend exposes this data and scraper controls over a REST
API; a React (Vite) single-page app consumes that API as a dashboard.

There is no database — `data/products.json` and `data/categories.json` are
the source of truth, loaded into memory and cached by the backend.

GitHub: `josephpetersw/webscrapper` (owner account: `josephpetersw`).

## Tech stack

**Backend** — `app.py` (Flask, single file, ~800 lines)
- Flask + Flask-CORS, serves both the REST API and the compiled React build
  (`frontend/dist`) as static files, single process.
- `curl_cffi` (`impersonate="chrome"`) for all outbound HTTP — this is what
  bypasses Cloudflare's bot detection. Do not swap in plain `requests`; it
  will get blocked.
- `BeautifulSoup4` + `lxml` for HTML parsing, `markdownify` for converting
  long descriptions to Markdown, `pandas`/`openpyxl` for Excel export.
- Dev server via `python app.py` (Windows-friendly). README also documents
  `gunicorn` as a Linux/macOS "production" option — **gunicorn does not run
  natively on Windows**, so on this machine always use `python app.py`.

**Scraper** — `main.py` + `scraper/` package
- `scraper/client.py` — `ScraperClient`: sync and async page fetchers via
  `curl_cffi`, Chrome impersonation, 30s timeout. Retries transient failures
  3× with linear backoff; statuses in `PERMANENT_STATUSES` (404, 403, 410, …)
  short-circuit immediately since retrying can't help. Never raises — the
  sync `fetch_page` returns `None`, the async `fetch_page_async` returns
  `(None, reason)` so callers can record *why* a URL was missed.
- `scraper/parser.py` — `Parser`: pure static methods, no I/O. Parses sitemap
  XML for `<loc>` URLs, and parses a WooCommerce product page's HTML into a
  dict (title, short/long description, categories via breadcrumbs, brand
  inferred from the last breadcrumb category or first word of the title,
  images from the gallery wrapper, price). **This is the single most
  fragile part of the project** — it depends entirely on phoneplacekenya.com's
  current WooCommerce theme markup. If scrapes start coming back empty, this
  is the first place to check (the site's theme may have changed).
- `scraper/downloader.py` — `ImageDownloader`: async image downloads with a
  semaphore for concurrency control, skips files that already exist on disk
  (resumable).
- `main.py` — orchestrates: discover product URLs → scrape concurrently with
  `asyncio` + a `Semaphore(workers)` → write
  `data/structured/<category>/<product>/` with `data.json`, `description.md`,
  `short_description.txt`, `images/`. Flushes `products.json` /
  `categories.json` / `failed_urls.json` every 10 completions plus a final
  write. Progress goes to `data/progress.json` (`{current, total, eta}`),
  which the frontend polls for the live progress bar. Logs to stdout and
  `scraper.log`. CLI:
  `python main.py --target_url URL --limit N --workers 8 [--no-resume] [--single-product]`.

  **Site discovery (`discover_product_urls`)** — works against any
  WordPress/WooCommerce store, not just the default one. Reads `robots.txt`
  for `Sitemap:` directives, falls back to the conventional locations
  (`/sitemap_index.xml`, `/sitemap.xml`, `/wp-sitemap.xml`,
  `/product-sitemap.xml`), then walks sitemap *indexes* down to leaf
  sitemaps. If any leaf sitemap is name-identified as a product sitemap, only
  those are trusted; URL-pattern matching is a fallback for stores whose
  sitemaps aren't helpfully named. Two traps worth knowing, both already
  handled and easy to reintroduce:
    - **Never put `'item'` in `PRODUCT_SITEMAP_HINTS`** — the word "sitemap"
      itself contains "item", so it matches every sitemap on the site.
    - `product_cat-`, `product_tag-`, `pa_*-` sitemaps list category / tag /
      attribute *archive* pages, not products; `TAXONOMY_SITEMAP_HINTS`
      excludes them. Likewise `/shop/` and `/store/` are deliberately absent
      from `PRODUCT_URL_HINTS` (they match catalogue indexes far more often
      than products).

  **Resume** is on by default: `load_existing_products()` reads the previous
  `products.json` and skips URLs already scraped, so a re-run fills in only
  what's missing and a crash is recoverable. Records with no `title` are
  treated as not-done, which self-heals junk rows. Pass `--no-resume` to
  force a full re-scrape.

  **Failures are recorded, never silent.** `fetch_page_async` returns
  `(html, reason)`; anything that fails all retries is appended to
  `state['failed']` and written to `data/failed_urls.json`, with a warning
  logged at the end. Re-running retries them automatically (they aren't in
  `products.json`, so resume doesn't skip them).

**Frontend** — `frontend/` (React 19, Vite, Tailwind CSS v4, lucide-react icons)
- Single-file app: `frontend/src/App.jsx` (~800 lines, no router, no state
  library — plain `useState`/`useEffect`, polling via `setInterval`).
- Tailwind v4 is configured via CSS (`@import "tailwindcss"` +  `@theme` block
  in `frontend/src/index.css`), not the classic JS-only config. A
  `tailwind.config.js` also exists (legacy/belt-and-braces) with the same
  color palette duplicated — **if you add a new theme color, update both
  places** or the CSS `@theme` block alone (that one is authoritative).
- Custom design system lives in `index.css` under `@layer components`:
  `.panel`, `.form-input`, `.btn`/`.btn-primary`/`.btn-danger`/
  `.btn-outline-primary`/`.btn-outline-secondary`, `.badge` +
  `.badge-success`/`.badge-danger`/`.badge-warning`/`.badge-secondary`,
  `.terminal`. **Important Tailwind v4 gotcha**: class names must appear as
  full literal strings somewhere in the source for the compiler to pick them
  up — never build a class name via string concatenation/interpolation
  (e.g. `` `bg-${color}/10` ``). Always keep a static lookup object whose
  *values* are complete class strings (see `SERVICE_STATUS_META` /
  `STATUS_ICONS` in `App.jsx` for the pattern used throughout this codebase).
- Dark mode: manual, via a `dark` class toggled on `<html>`, persisted to
  `localStorage['theme']`, defaulting to the OS preference.
- `frontend/dist` is the production build Flask serves at `/`; it is
  git-ignored and must be rebuilt after any frontend change:
  ```bash
  cd frontend && npm run build
  ```

## Local setup (Windows, this machine)

```bash
python -m venv venv
./venv/Scripts/python.exe -m pip install flask flask-cors beautifulsoup4 lxml curl_cffi aiofiles markdownify pandas openpyxl
cd frontend && npm install && npm run build && cd ..
./venv/Scripts/python.exe app.py
# → http://127.0.0.1:5000
```

There is no `requirements.txt` — dependencies are only documented in the
README's `pip install` line. If you add a new Python dependency, update the
README's install command (there's nowhere else it's tracked).

## API surface (all under `app.py`)

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Serves the built React `index.html` |
| `/api/scrape` | POST | Launches `main.py` as a subprocess (`{url, limit, workers}`) |
| `/api/scrape/stop` | POST | Terminates the running scraper subprocess |
| `/api/status` | GET | `{running, pid}` — is a scrape currently active |
| `/api/system/status` | GET | **New.** Full health report, see below |
| `/api/logs` | GET | Tail of `scraper.log` (`?lines=N`) |
| `/api/progress` | GET | Contents of `data/progress.json` |
| `/api/products` | GET | Paginated/filterable product list (`page`, `limit`, `search`, `category`) |
| `/api/stats` | GET | Totals: products/categories/brands/images |
| `/api/files` | GET | File-tree listing under `data/` (path-traversal guarded) |
| `/api/image` | GET | Resolves a product image by title+filename across old/new storage layouts |
| `/data/<path>` | GET | Raw static file serving from `data/` |
| `/api/export/json`, `/csv`, `/excel`, `/xml` | GET | Full product export, `?clean=true\|false` strips/keeps raw HTML in descriptions |
| `/api/export/categories`, `/categories_csv`, `/brands_csv` | GET | Metadata list exports |
| `/api/export/structured`, `/structured/data`, `/structured/images` | GET | ZIP archives of `data/structured/` (everything / data-only / images-only) |

A module-level `_CACHE` dict memoizes `products.json`/`categories.json` in
memory, invalidated by file mtime (`load_products_from_cache()` /
`load_categories_from_cache()`) — every endpoint that reads product data goes
through these, not raw file reads. If you add a new endpoint that reads
products, use these helpers rather than reopening the JSON file.

## System Status feature (added on the `joe` branch)

A new dashboard page (sidebar → "System Status", `Activity` icon with a
colored health dot that's always visible regardless of which view is open)
that reports the health of every moving part of the app:

- **Backend API** — trivially "operational" (if the handler ran, Flask is up); reports process uptime.
- **Scraper Engine** — "active" while a scrape subprocess is running, "operational" (idle) otherwise.
- **Data Storage** — checks `data/` exists and is writable, reports indexed product count.
- **Frontend Build** — checks `frontend/dist/index.html` exists, reports its build timestamp.
- **Logging** — checks `scraper.log` exists, reports size and time since last write.
- **XphoneKenya.com (target site)** — a genuine outbound reachability check
  against `https://www.phoneplacekenya.com`, run on a **daemon background
  thread** on a 60-second interval (`_check_target_site_loop` in `app.py`),
  cached in a lock-guarded module dict. Deliberately **not** done inline in
  the request handler — a live network call in a single-threaded Flask dev
  server would stall every other request while it waited on the network.
- **Disk Space** — `shutil.disk_usage()` on the app's drive; "warning" below
  10% free, "down" below 3% free.
- **Python Runtime** — version and OS, always "operational".

Backend: `GET /api/system/status` → `{overall, checked_at, services: [...]}`
where each service is `{id, name, category, icon, status, detail,
checked_at?}`. `status` is one of `operational | active | checking | warning
| down`; `overall` is the worst of all services, collapsed to
`operational | degraded | down`.

Frontend: polled on the same 2-second interval as the rest of the dashboard
(`fetchSystemStatus`, alongside `fetchLogs`/`fetchProgress`/`fetchStatus`).
Icon-per-service and color-per-status are static lookup tables
(`STATUS_ICONS`, `SERVICE_STATUS_META`) in `App.jsx` — extend those, not a
computed class name, when adding a new service or status.

## Known issues / gotchas (found while working in this repo, not yet fixed)

1. **The periodic flush is O(n²).** `save_results()` rewrites the whole of
   `products.json` every 10 completions. Over a full 3,353-product run that's
   ~335 rewrites of a file growing toward ~50MB — several GB of cumulative
   disk writes. Works fine, just wasteful; fix by appending incrementally or
   flushing on a time interval if it becomes a problem.
2. `image.png` in the repo root (added by a recent upstream commit) is just a
   README screenshot, not app data.
3. `check_fields.py`, `test_brand.py`, `test_client.py`, `test_product.py` at
   the repo root are ad hoc manual scripts (no test runner, no assertions
   framework) — run directly with the venv's Python for spot-checking parser
   output, not part of any CI.
4. No automated test suite / CI exists in this repo at all.
5. `pyrightconfig.json` points Pyright at `./venv` — if you recreate the venv
   elsewhere this needs to match.

## Branching / workflow notes for this repo

- Default branch: `main`.
- `joe` — working branch for dashboard feature work (created for the System
  Status page). Push here rather than to `main` unless told otherwise.
- Commit messages in this repo do not currently follow a strict convention
  (mix of `feat:`/`fix:`/`docs:` prefixes and plain descriptions) — either is
  fine, prefer a `type: summary` first line when the change fits a clear category.
