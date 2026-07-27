# CLAUDE.md

Guidance for Claude Code (or any future contributor) working in this repository.

## What this project is

`webscrapper` is a **general-purpose e-commerce scraper** and data-management
dashboard for WordPress/WooCommerce storefronts. Point it at any URL on a store
and it discovers that store's whole catalogue from its sitemaps, extracts
structured product data (title, price, brand, categories, images,
descriptions), downloads images, and writes everything to disk. A Flask backend
exposes the data and scraper controls over a REST API; a React (Vite)
single-page app consumes that API as a dashboard.

It is **not tied to any one store** — nothing about a specific site should be
hardcoded. Multiple stores can be scraped and kept side by side.

There is no database. Each store owns a folder under `data/<domain>/`, and
`products.json` / `categories.json` inside it are the source of truth, loaded
into memory and cached by the backend. The dashboard displays one store at a
time — the "active" site, recorded in `data/.active_site`.

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
  fragile part of the project** — it keys off standard WooCommerce class names
  (`product_title`, `woocommerce-product-details__short-description`,
  `tab-description`, `.woocommerce-product-gallery__wrapper`), so it works
  across most WooCommerce stores but breaks on heavily customised themes. If a
  scrape returns products with empty titles, this is the first place to look.
- `scraper/downloader.py` — `ImageDownloader`: async image downloads with a
  semaphore for concurrency control, skips files that already exist on disk
  (resumable).
- `main.py` — orchestrates: discover product URLs → scrape concurrently with
  `asyncio` + a `Semaphore(workers)` → write
  `data/<site>/structured/<category>/<product>/` with `data.json`,
  `description.md`, `short_description.txt`, `images/`. Flushes
  `products.json` / `categories.json` / `failed_urls.json` every 10
  completions plus a final write. Progress goes to `data/progress.json`
  (`{current, total, eta}`), which the frontend polls for the live progress
  bar. Logs to stdout and `scraper.log`. CLI:
  `python main.py --target_url URL [--limit N] [--workers 8] [--no-resume] [--single-product] [--new-version]`.

  **A run must always reach the end, or be stopped by the user — nothing else.**
  Several things enforce that, and removing any one of them reintroduces
  silent partial scrapes:
    - `scrape_product()` is a thin wrapper that catches everything and records
      the URL as failed; the real work is in `_scrape_product_inner()`.
      Product pages are wildly inconsistent and *will* throw.
    - `asyncio.gather(..., return_exceptions=True)` — without this one
      unhandled task exception abandons every remaining product.
    - `write_json_atomic()` (temp file + `os.replace`) — these files are
      rewritten every few seconds while the dashboard polls them; writing in
      place lets a reader parse a half-written file.
    - `safe_path_segment()` and `downloader.safe_filename()` — titles and
      image URLs go straight into paths. Windows caps paths at 260 chars and
      rejects a set of characters outright; unsanitised names silently lose
      images and whole products.
    - A manual stop is logged to `scraper.log` by `app.py`'s stop endpoint, so
      a stopped run is distinguishable from a crash after the fact.

- `scraper/discovery.py` — platform fingerprinting and product-URL discovery,
  shared by the scraper and the dashboard's pre-scrape analysis. Two entry
  points: `analyze_site()` (fast, ~4 requests, used while a dialog is open)
  and `discover_products()` (exhaustive, used at scrape time).

  **Discovery is layered and additive**, because no single strategy works
  everywhere and stores are often inconsistent with themselves:
  sitemaps → platform API (Shopify `/products.json`, WooCommerce Store API,
  WP REST) → listing-page crawl as a last resort. Results are **merged, not
  first-wins** — on the reference store the sitemaps yield 3,353 URLs and the
  WooCommerce API 3,400, and the union is what gets scraped. If you make this
  "first strategy that returns something wins", you will silently lose products.

  Traps already handled here, easy to reintroduce:
    - **Never put `'item'` in `PRODUCT_SITEMAP_HINTS`** — the word "sitemap"
      contains "item", so it matches every sitemap on the site.
    - `product_cat-`, `product_tag-`, `pa_*-` sitemaps list category / tag /
      attribute *archive* pages, not products (`TAXONOMY_SITEMAP_HINTS`).
    - `/shop/` and `/store/` are deliberately absent from `PRODUCT_URL_HINTS`
      — they match catalogue indexes far more often than products.
    - WordPress theme/plugin names are parsed from asset URLs with a strict
      slug charset; a loose pattern picks up `*` and template placeholders.

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

## Multi-site data layout

```
data/
├── .active_site                  # which site the dashboard shows (plain text)
├── .exports/                     # scratch space for generated xlsx/xml/zip
├── progress.json                 # live run state, global to whichever scrape is running
├── example-store.com/            # one folder per store, named after its domain
│   ├── products.json
│   ├── categories.json
│   ├── failed_urls.json
│   └── structured/<Category>/<Product>/{data.json,description.md,short_description.txt,images/}
└── example-store.com_v2_20260728-014500/   # re-scrape kept as a separate version
```

Folder naming lives in `main.py`: `site_folder_name()` lowercases the host and
strips a leading `www.`, so `https://www.foo.com/x` and `https://foo.com` map
to the same folder. `resolve_site_dir(url, new_version=True)` allocates
`<site>_v2_<timestamp>`, `_v3_`, … instead of touching the existing folder.

On the backend everything reads through `active_site_dir()` /
`site_file(name)` — **never** `os.path.join(DATA_DIR, 'products.json')`
directly, or you'll break multi-site. `site_file()` returns `''` (not `None`)
when nothing has been scraped, so `os.path.exists()` treats it as missing.
`_CACHE` is keyed by full path so switching sites can't serve stale data.

Dotfiles are filtered out of `/api/files`, keeping `.active_site` and
`.exports/` out of the File Explorer.

**Exports run as background jobs.** A full image archive is thousands of files
and hundreds of megabytes — far too slow to hold a request open for, and the
UI would look hung. `/api/export/start` spawns a thread, the client polls
`/api/export/status/<id>` for a step counter and a live message (archive
packing reports every 50 files), then fetches `/api/export/download/<id>`.
The browser is sent to the download URL directly rather than buffering a
blob, so a 700MB file streams to disk instead of into memory. Finished jobs
and their files are purged after an hour, and `.exports/` is scratch space —
safe to delete at any time.

## API surface (all under `app.py`)

| Route | Method | Purpose |
|---|---|---|
| `/` | GET | Serves the built React `index.html` |
| `/api/scrape` | POST | Launches `main.py` as a subprocess (`{url, limit, workers}`) |
| `/api/scrape/stop` | POST | Terminates the running scraper subprocess |
| `/api/status` | GET | `{running, pid}` — is a scrape currently active |
| `/api/system/status` | GET | Full health report, see below |
| `/api/system/wipe` | POST | Deletes every site folder, cache and the log. Refuses (409) while a scrape is running |
| `/api/sites` | GET | All scraped sites with product/failure counts and which is active |
| `/api/sites/active` | POST | Switch which site the dashboard shows |
| `/api/site/check` | GET | `?url=` → whether that store already has data; drives the update-vs-new-version prompt |
| `/api/site/analyze` | GET | `?url=` → platform, theme, plugins, sitemaps, APIs, estimated product count. Advisory only — never blocks a scrape |
| `/api/export/bundle` | POST | Synchronous export. Kept for scripting; the dashboard uses the job endpoints below |
| `/api/export/start` | POST | `{sites[], formats[], clean}` → `{job_id}`, work runs on a background thread |
| `/api/export/status/<job_id>` | GET | `{state, step, total_steps, message, filename, size}` — polled for the progress dialog |
| `/api/export/download/<job_id>` | GET | Serves the finished file; jobs expire after `EXPORT_JOB_TTL` (1h) |
| `/api/sites/delete` | POST | `{names[]}` or `{all: true}`. Refuses (409) while a scrape runs; repoints the active site if it was deleted |
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
- **Target site** — a genuine outbound reachability check against *the active
  site's* domain (`target_site_domain()` strips any `_v2_<timestamp>` suffix);
  reports "No site scraped yet" when `data/` is empty. Runs on a **daemon
  background thread** on a 60-second interval (`_check_target_site_loop` in
  `app.py`), cached in a lock-guarded module dict. Deliberately **not** done
  inline in the request handler — a live network call in a single-threaded
  Flask dev server would stall every other request while it waited.
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
