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
  `curl_cffi`. Never raises — the sync `fetch_page` returns `None`, the async
  `fetch_page_async` returns `(None, reason)` so callers can record *why* a URL
  was missed.

  **Browser-fingerprint escalation is the important part here.** A single
  hardcoded `impersonate="chrome"` silently loses whole stores: several hosts
  (anything behind the `hcdn` edge) answer Chrome's TLS/JA3 fingerprint with a
  403 "Checking your browser" interstitial and serve the identical request
  happily under Safari. So a fetch walks `IMPERSONATE_PROFILES`
  (chrome → safari → firefox), and `PROFILE_MEMO` remembers the first profile
  that worked **per host**, so the cost is paid once per host rather than once
  per URL. Consequences worth knowing:
    - **403 is deliberately NOT in `PERMANENT_STATUSES`.** It signals a
      fingerprint block far more often than a genuinely forbidden resource.
      Putting it back makes every `hcdn`-fronted store fail instantly again.
    - `looks_like_challenge()` catches bot walls that answer **200** with an
      interstitial; without it those get parsed as if they were products.
    - After `_EXHAUSTED_AFTER` (3) URLs on a host fail *every* profile, the
      ladder collapses to one profile for that host. Without this, a genuinely
      blocked site turns a 3,000-URL run into 27,000 requests.
    - Only a *fingerprint* rejection counts towards that — a timeout says
      nothing about which browser we look like. Hence `record_failure(blocked=)`.
    - `normalize_url()` percent-encodes non-ASCII paths; Shopify handles
      routinely contain `®`/`™` and curl rejects them raw.

  **Fulfilment-context probing** (`CONTEXT_MEMO`, `PRICE_CONTEXT_PARAMS`) is
  the same per-host-adaptation idea applied to a different failure. Some
  storefronts render the entire product page — title, images, description —
  but omit the price until the request states how the goods would be
  delivered, and report the item as out of stock meanwhile. Scraped naively,
  the whole catalogue comes back looking sold out. `main.recover_missing_price`
  re-fetches a title-but-no-price product with each candidate parameter
  (`?sid=SLOTTED` and friends), and the one that works is remembered for the
  host, so the cost is one extra request per *host*, not per product. Probing
  stops after `_CONTEXT_PROBE_LIMIT` products so a genuinely sold-out store
  does not double its request count. The parameter is never written into the
  stored `url` — it is how we asked, not where the product lives.
- `scraper/extractors.py` — platform-neutral readers for schema.org JSON-LD,
  Open Graph meta tags and microdata, plus price/entity normalisation. No I/O,
  never raises. `jsonld_nodes()` flattens `@graph` and `mainEntity` nesting and
  tolerates the ways real sites break JSON-LD (CDATA wrappers, HTML comments,
  trailing commas).
- `scraper/schema.py` — **the single definition of a product record.**
  `CORE_FIELDS` (the original eight) + `DETAIL_FIELDS` (`price_value`,
  `currency`, `sku`, `availability`, `in_stock`), with `extracted_by` marked
  diagnostic and never exported. `normalize()` guarantees types;
  `export_row()` flattens to strings. **`app.py` imports this** — adding a
  field is a one-line change here, and it is no longer possible to forget an
  exporter. (The XML exporter used to serialise whatever keys a record
  happened to carry, so a new dict-valued field landed in the feed as a Python
  repr inside a tag.)
- `scraper/parser.py` — `Parser`: pure static methods, no I/O. Parses sitemap
  XML for `<loc>` URLs, and parses a product page into a record.

  **Extraction is layered, ordered by how universal each layer is:**
  schema.org structured data (JSON-LD, then microdata) → Open Graph meta tags
  → theme CSS selectors. Each field independently takes the first layer that
  yields a value, so a store publishing half its data as JSON-LD and the rest
  only in markup still comes out complete.

  This ordering is the whole point: the parser used to key *only* off default
  WooCommerce class names, which returned an empty record on Shopify,
  Django-Oscar, Magento, Next.js storefronts and any customised Woo theme —
  every one of which publishes schema.org data because Google rich results
  depend on it. Selector tables (`TITLE_SELECTORS`, `PRICE_SELECTORS`, …) are
  now the *fallback*, which is why it is safe for them to be broad.

  `extracted_by` on each record names the layer that won per field — the
  fastest way to tell "this store needs new selectors" from "this store
  blocked us". Check it first when a scrape looks wrong.

  Traps already handled, easy to reintroduce:
    - The last breadcrumb is the product itself; left in, it becomes a
      category, and then a directory per product in `structured/`.
    - JSON-LD payloads are full of HTML entities (`Home &amp; Living`) — hence
      `extractors.unescape`, applied twice for `&amp;amp;`.
    - Stores that put their **own name** in the JSON-LD `brand` field collapse
      the whole catalogue to one brand, so a brand matching the site's domain
      is rejected and the next layer gets a turn.
    - Galleries contain placeholders, spacers and trust badges (`_JUNK_IMAGE_TOKENS`),
      and lazy-loading themes hide the real URL in one of ~10 `data-*`
      attributes or the largest `srcset` candidate.
- `scraper/downloader.py` — `ImageDownloader`: async image downloads with a
  semaphore for concurrency control, skips files that already exist on disk
  (resumable). **It takes the shared `ScraperClient`** and fetches through
  `fetch_bytes_async`, so images use whichever browser fingerprint works for
  that host. Downloading them over a separate hardcoded-Chrome session meant a
  fingerprint-blocked store scraped perfectly and yielded *none* of its
  pictures — the product pages escalated to Safari, the images did not.
- `scraper/paths.py` — **all filesystem-name safety, in one place**, because
  the scraper and the downloader have to agree: how long an image filename may
  be depends on the directory the scraper chose for the product.
  - Sanitising each segment is not enough; the limit is on the **total** path.
    `build_product_dir()` budgets the whole thing, dropping the deepest
    category first (losing "Switches" hurts less than losing the product's
    identity) and only then squeezing the product folder. `image_path()`
    fits the filename into whatever room is left, returning `''` when even a
    minimal name will not fit so the caller skips rather than fails on open().
  - **Truncation always appends a short hash.** Two long titles sharing a
    prefix ("… 4GB 128GB Black" / "… 8GB 256GB Blue") would otherwise collapse
    onto one directory and overwrite each other.
  - Windows reserved names (`CON`, `PRN`, `LPT1`, …) are escaped.
  - `MAX_PATH` is enforced on every platform, not just Windows: `data/` gets
    copied to and served from Windows machines, so a tree that only works on
    Linux is a trap.
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
    - `scraper/paths.py` — titles and image URLs go straight into paths.
      Windows caps paths at 260 chars and rejects a set of characters
      outright; unsanitised names silently lose images and whole products.
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
    - `_is_scrapable_page()` drops what sitemaps list alongside products:
      images on a media host, PDFs, blog and account pages, off-host URLs —
      and **the site root**, which appears in product sitemaps more often than
      you would hope. Scraped, the homepage becomes a record titled after the
      store, which then counts as "already done" on every later run.
    - `_infer_product_urls()` handles stores publishing one undifferentiated
      `sitemap.xml` with no `product-sitemap.xml` to key off: it groups URLs by
      first path segment and takes the segment that both looks like a product
      path and dominates the file (≥66%). It returns `[]` rather than guessing
      when nothing dominates, so a flat-URL site falls through to the other
      strategies instead of scraping its About page.
    - OpenCart stores are usually run with SEO URLs on, so products carry no
      `product_id=` anywhere — `discover_via_opencart` harvests the stock
      listing markup (`.product-thumb a`, `.product-layout a`, …) via the
      `product/search` route, and strips the paging params off each link so the
      same product isn't scraped once per originating page.
    - `discover_via_crawl` is a bounded BFS, not a pagination-only follow:
      storefronts that render their homepage in JavaScript expose no product
      link until you reach a category page, and their category URLs are bare
      slugs with nothing to pattern-match on. Listing-looking pages are
      visited first; if hint-matching finds nothing, the dominant shape of
      everything collected is inferred instead.

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

## The browser fallback (`scraper/browser_client.py`)

The last rung of `IMPERSONATE_PROFILES` is a real Chrome, driven by
`undetected-chromedriver`, for hosts behind an interactive JavaScript
challenge that no TLS fingerprint can satisfy. It is reached only after every
curl_cffi profile has been refused.

**The dependency is optional and not in requirements.txt.** Without it,
`HAS_BROWSER` is False and the rung is absent from the ladder — everything else
is unaffected. Install it only when you actually need it.

Points that are easy to get wrong, all of which were:

- **`probe()` must never start a browser.** It backs the dashboard's
  pre-scrape analysis and runs inside a Flask request handler, so launching
  Chrome there hangs the single-threaded dev server for the length of a
  challenge. The rung is skipped there; the scrape itself still uses it.
- **One driver for the process, not one per thread.** A thread-local driver
  meant eight workers spawned eight Chromes, several GB of memory and eight
  challenge solves for the same host.
- **`close_driver()` is called from `ScraperClient.close()`/`aclose()`.**
  A browser started for the fallback outlives the HTTP sessions otherwise.
- **No `version_main` pin.** undetected-chromedriver matches the installed
  Chrome by itself; a hardcoded major version breaks on every other machine.
- The challenge wait is `CHALLENGE_TIMEOUT` (15s), not the 60s it was: that
  figure was written for a human solving a CAPTCHA by hand, and on an
  unattended run it stalled a worker for a minute per URL on a host that was
  never going to let us in.

The module was previously called `playwright_client` with a `HAS_PLAYWRIGHT`
flag; it has never used Playwright. The old names are re-exported so nothing
that imported them breaks.

## Product storage

Each store folder holds both:

- **`products.jsonl`** — the source of truth. One JSON object per line,
  appended the moment a product is scraped, so a run killed at any point keeps
  everything already completed. A re-scrape appends rather than rewriting, so a
  URL can appear more than once and **the last line for a URL wins**.
- **`products.json`** — a snapshot of the same data, refreshed every
  `SNAPSHOT_INTERVAL` seconds and once at the end. Kept because exports and
  older tooling read it, and because a site scraped by an earlier version only
  has this.

`main.py` reads through `_read_all_products()` / `_load_scraped_urls()`, which
prefer the JSONL and fall back to the legacy JSON. `app.py` reads through
`load_products_from_cache()`, which does the same.

Things worth knowing before touching this:

- **A torn final line is normal, not corruption.** The file is appended to
  live, so a reader can catch a half-written last line. Both readers skip it
  and keep the rest — losing one product is recoverable, losing the catalogue
  is not. Never make a parse failure fatal here.
- **`app.py`'s cache is incremental.** It remembers the byte offset it reached
  and parses only the tail on each poll; re-reading the whole file twice a
  second would make the dashboard cost O(catalogue). A file that has *shrunk*
  means it was rewritten (a fresh scrape, `--no-resume`), so the offset is
  reset rather than trusted.
- **`ProgressReporter` throttles** `data/progress.json` to one write a second.
  The final update passes `force=True`; without it a finished run shows one
  short forever.
- **`cache/html/`** holds gzipped page bodies keyed by a hash of the URL,
  written through a temp file. A corrupt entry counts as a miss rather than
  raising — a run killed mid-write used to leave a truncated `.gz` that every
  later run would try, and fail, to read.

## Stores that will not quote a price

Some storefronts render the whole product page — title, images, description —
but omit the price and report every item out of stock until the request says
how the goods would be delivered. Scraped naively, such a catalogue comes back
100% sold out with no prices at all.

`main.py`'s `recover_missing_price()` re-fetches a priceless product with each
of `client.PRICE_CONTEXT_PARAMS` until one yields a price, and
`client.CONTEXT_MEMO` remembers the answer per host so the discovery is paid
once rather than per product. The probe URL is *not* kept on the record: the
parameter is how we asked, not where the product lives, and it must not reach
the exported feed.

Traps here, all of which cost real prices when got wrong:

- **The remembered answer must not be the *only* one tried.** One storefront
  can stock the same catalogue under several fulfilment modes; a product absent
  from the remembered mode may still be priced under another. `candidates()`
  returns the known set *first* and the alternates behind it, retiring the
  alternates only after they have failed to pay off across several products
  (`credit()` resets that counter whenever one does).
- **First writer wins in `remember()`.** Workers run concurrently and finish
  out of order; letting the last one overwrite made the recorded answer a
  matter of timing, and the log claim two different answers for one host.
- Probing stops after `_CONTEXT_PROBE_LIMIT` fruitless products, so a store
  whose stock genuinely *is* exhausted does not double its request count.
- A product with no price is a perfectly ordinary outcome. Anything that fails
  leaves the original record untouched.

## Known issues / gotchas (found while working in this repo, not yet fixed)

1. ~~The periodic flush is O(n²).~~ **Fixed.** Products are appended to
   `products.jsonl` one line at a time as they are scraped; `products.json` is
   now only a periodic snapshot (`SNAPSHOT_INTERVAL`, 20s) plus a final write.
   See "Product storage" below.
2. `image.png` in the repo root (added by a recent upstream commit) is just a
   README screenshot, not app data.
3. `check_fields.py`, `test_brand.py`, `test_client.py`, `test_product.py` at
   the repo root are ad hoc manual scripts (no test runner, no assertions
   framework) — run directly with the venv's Python for spot-checking parser
   output, not part of any CI.
4. No CI exists. There is a `pytest` suite under `tests/` (run `pytest`), plus
   two self-contained, offline, no-dependency scripts — run all three after any
   change to the scraper or the exporters:
   ```bash
   ./venv/Scripts/python.exe -m pytest -q
   ./venv/Scripts/python.exe test_robustness.py   # parsing, paths, discovery, context probing
   ./venv/Scripts/python.exe test_exports.py      # schema + all four export builders
   ```
   **`tests/test_api.py` and `tests/test_main_storage.py` are largely skipped.**
   They were written against a single-site storage layer — one global
   `products.jsonl` with an incremental offset cache and an on-disk HTML cache —
   which the upstream merge did not carry over; this tree stores one folder per
   store and resolves paths through `active_site_dir()`. `tests/conftest.py`
   skips rather than errors when those globals are absent, and asserts on the
   attribute rather than a version flag, so the tests start running again by
   themselves if that layer is ever ported onto the per-store layout. Do not
   "fix" them by reintroducing a global `products.jsonl` — that would break
   multi-site, which the dashboard depends on.
   `test_robustness.py` covers the hostile inputs real stores produce
   (truncated JSON-LD, entity-encoded names, price ranges, galleries of
   tracking pixels) and asserts the parser never raises. `test_exports.py`
   feeds every builder a current record, a **legacy** record with none of the
   new fields, and a junk record with wrong types — the legacy case is what
   guarantees `products.json` files scraped by an older version still export.
   Neither makes a network request, so both run in about a second.
5. `pyrightconfig.json` points Pyright at `./venv` — if you recreate the venv
   elsewhere this needs to match.

## Branching / workflow notes for this repo

- Default branch: `main`.
- `joe` — working branch for dashboard feature work (created for the System
  Status page). Push here rather than to `main` unless told otherwise.
- Commit messages in this repo do not currently follow a strict convention
  (mix of `feat:`/`fix:`/`docs:` prefixes and plain descriptions) — either is
  fine, prefer a `type: summary` first line when the change fits a clear category.
