import os
import re
import signal
import subprocess
import sys
import json
import csv
import io
import logging
import time
import shutil
import platform
import threading
import zipfile
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse
from flask import Flask, jsonify, request, send_from_directory, Response
from flask_cors import CORS

app = Flask(__name__, static_folder='frontend/dist', static_url_path='')
CORS(app)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
LOG_FILE = os.path.join(BASE_DIR, 'scraper.log')
VENV_PYTHON = sys.executable  # the interpreter running this Flask process (the venv's python)
MAIN_SCRIPT = os.path.join(BASE_DIR, 'main.py')

# Track running scraper process
scraper_process = None

APP_START_TIME = time.time()

def human_size(num_bytes):
    size = float(num_bytes)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if abs(size) < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"

def format_duration(seconds):
    seconds = max(0, int(seconds))
    d, seconds = divmod(seconds, 86400)
    h, seconds = divmod(seconds, 3600)
    m, s = divmod(seconds, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if not parts: parts.append(f"{s}s")
    return ' '.join(parts)

# ── Scraped Sites ───────────────────────────────────────────
# Each store scraped lives in its own folder under data/, named after its
# domain (plus _v2_<timestamp> etc. for re-scrapes kept side by side). The
# dashboard shows one at a time: the "active" site.
ACTIVE_SITE_FILE = os.path.join(DATA_DIR, '.active_site')

def list_site_dirs():
    if not os.path.isdir(DATA_DIR):
        return []
    names = [d for d in os.listdir(DATA_DIR)
             if not d.startswith('.') and os.path.isdir(os.path.join(DATA_DIR, d))]
    return sorted(names, key=lambda n: os.path.getmtime(os.path.join(DATA_DIR, n)), reverse=True)

def active_site_name():
    """Which site the dashboard is currently showing."""
    try:
        with open(ACTIVE_SITE_FILE, 'r', encoding='utf-8') as f:
            name = f.read().strip()
        if name and os.path.isdir(os.path.join(DATA_DIR, name)):
            return name
    except Exception:
        pass
    sites = list_site_dirs()
    return sites[0] if sites else None

def active_site_dir():
    name = active_site_name()
    return os.path.join(DATA_DIR, name) if name else None

def site_file(filename):
    """Path to a file inside the active site's folder.

    Returns '' when nothing has been scraped yet, which os.path.exists()
    reports as missing - so callers can treat it like any absent file.
    """
    site_dir = active_site_dir()
    return os.path.join(site_dir, filename) if site_dir else ''

def export_prefix():
    return (active_site_name() or 'scraped').replace('.', '_')

# Generated export artifacts live out of the way so data/ shows only site folders.
EXPORT_DIR = os.path.join(DATA_DIR, '.exports')

def export_workspace():
    os.makedirs(EXPORT_DIR, exist_ok=True)
    return EXPORT_DIR

# ── Memory Cache ────────────────────────────────────────────
# Keyed by path so switching between sites doesn't serve stale data.
_CACHE = {}

def load_json_cached(path, default):
    if not path or not os.path.exists(path):
        return default
    try:
        mtime = os.path.getmtime(path)
        entry = _CACHE.get(path)
        if entry is None or mtime > entry['mtime']:
            with open(path, 'r', encoding='utf-8') as f:
                _CACHE[path] = {'data': json.load(f), 'mtime': mtime}
        return _CACHE[path]['data']
    except Exception as e:
        logger.error(f"Error reading {path}: {e}")
        return default

def load_products_from_cache():
    return load_json_cached(site_file('products.json'), [])

def load_categories_from_cache():
    return load_json_cached(site_file('categories.json'), [])

# ── Static React Build ──────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory(app.static_folder, 'index.html')

# ── Scraper Control ─────────────────────────────────────────
@app.route('/api/scrape', methods=['POST'])
def trigger_scrape():
    global scraper_process
    if scraper_process and scraper_process.poll() is None:
        return jsonify({'status': 'error', 'message': 'Scraper already running'}), 400

    req_data = request.json or {}
    url = req_data.get('url')
    limit = req_data.get('limit')
    workers = req_data.get('workers', 8)
    new_version = bool(req_data.get('new_version'))

    if not url:
        return jsonify({'status': 'error',
                        'message': 'A store URL is required.'}), 400

    cmd = [VENV_PYTHON, MAIN_SCRIPT, '--target_url', url]
    if limit:
        cmd.extend(['--limit', str(limit)])
    if new_version:
        cmd.append('--new-version')

    cmd.extend(['--workers', str(workers)])

    try:
        scraper_process = subprocess.Popen(cmd, cwd=BASE_DIR)
        return jsonify({'status': 'success', 'message': 'Scraping started', 'pid': scraper_process.pid})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

def append_to_scraper_log(message):
    """Write a line into scraper.log in the scraper's own format.

    The scraper subprocess owns that file, so events that happen here in the
    web layer (a manual stop, most importantly) would otherwise leave no trace
    and be indistinguishable from a crash when reading the log later.
    """
    try:
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{stamp} - INFO - {message}\n")
    except Exception as e:
        logger.error(f"Could not append to scraper log: {e}")

@app.route('/api/scrape/stop', methods=['POST'])
def stop_scrape():
    global scraper_process
    if scraper_process and scraper_process.poll() is None:
        progress = load_json_cached(os.path.join(DATA_DIR, 'progress.json'), {})
        done, total = progress.get('current', 0), progress.get('total', 0)
        try:
            scraper_process.terminate()
            scraper_process.wait(timeout=5)
        except Exception:
            scraper_process.kill()
        scraper_process = None
        append_to_scraper_log(
            f"STOPPED BY USER at {done}/{total} products. Partial data was saved; "
            f"re-run the same site to resume from here.")
        logger.info(f"Scrape stopped by user at {done}/{total}")
        return jsonify({'status': 'success', 'message': 'Scraper stopped'})
    return jsonify({'status': 'error', 'message': 'No scraper running'}), 400

@app.route('/api/status', methods=['GET'])
def get_status():
    global scraper_process
    running = scraper_process is not None and scraper_process.poll() is None
    return jsonify({'running': running, 'pid': scraper_process.pid if running else None})

# ── Scraped Sites ────────────────────────────────────────────
def site_summary(name):
    site_dir = os.path.join(DATA_DIR, name)
    products_file = os.path.join(site_dir, 'products.json')
    failed_file = os.path.join(site_dir, 'failed_urls.json')
    return {
        'name': name,
        'products': len(load_json_cached(products_file, [])),
        'failed': len(load_json_cached(failed_file, [])),
        'modified': datetime.fromtimestamp(os.path.getmtime(site_dir), timezone.utc).isoformat(),
        'active': name == active_site_name(),
    }

@app.route('/api/sites', methods=['GET'])
def get_sites():
    return jsonify({'active': active_site_name(),
                    'sites': [site_summary(n) for n in list_site_dirs()]})

@app.route('/api/sites/active', methods=['POST'])
def set_active_site_route():
    name = (request.json or {}).get('name')
    if not name or not os.path.isdir(os.path.join(DATA_DIR, name)):
        return jsonify({'status': 'error', 'message': 'Unknown site'}), 404
    with open(ACTIVE_SITE_FILE, 'w', encoding='utf-8') as f:
        f.write(name)
    return jsonify({'status': 'success', 'active': name})

@app.route('/api/site/check', methods=['GET'])
def check_site():
    """Does this store already have scraped data? Drives the update-vs-new-version prompt."""
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify({'valid': False, 'message': 'A store URL is required.'})

    parsed = urlparse(url if '://' in url else f'https://{url}')
    host = (parsed.netloc or parsed.path).lower().split('/')[0].split('@')[-1]
    if host.startswith('www.'):
        host = host[4:]
    if '.' not in host:
        return jsonify({'valid': False, 'message': 'That does not look like a valid URL.'})
    name = re.sub(r'[^a-z0-9.\-]', '_', host)

    exists = os.path.isdir(os.path.join(DATA_DIR, name))
    versions = [d for d in list_site_dirs() if d.startswith(f'{name}_v')]
    return jsonify({
        'valid': True,
        'site': name,
        'exists': exists,
        'products': len(load_json_cached(os.path.join(DATA_DIR, name, 'products.json'), [])) if exists else 0,
        'versions': len(versions),
    })

@app.route('/api/site/analyze', methods=['GET'])
def analyze_site_route():
    """Reconnaissance on a store before scraping: platform, theme, plugins, sitemaps, APIs."""
    url = request.args.get('url', '').strip()
    if not url:
        return jsonify({'valid': False, 'message': 'A store URL is required.'}), 400

    parsed = urlparse(url if '://' in url else f'https://{url}')
    host = (parsed.netloc or parsed.path).lower().split('/')[0]
    if '.' not in host:
        return jsonify({'valid': False, 'message': 'That does not look like a valid URL.'}), 400

    try:
        from scraper.client import ScraperClient
        from scraper.parser import Parser
        from scraper import discovery
        report = discovery.analyze_site(ScraperClient(), Parser(), url)
        report['valid'] = True
        return jsonify(report)
    except Exception as e:
        logger.error(f"Site analysis failed for {url}: {e}")
        # Analysis is advisory - never let it block the user from scraping.
        return jsonify({'valid': True, 'reachable': False, 'url': url,
                        'platform': {'id': 'unknown', 'name': 'Unknown', 'supported': False,
                                     'confidence': 'none', 'evidence': []},
                        'warnings': [f'Could not analyze this site: {e}'],
                        'notes': [], 'sitemaps': [], 'product_sitemaps': [], 'apis': []})

@app.route('/api/sites/delete', methods=['POST'])
def delete_sites():
    """Delete one, several, or all scraped sites."""
    global scraper_process
    if scraper_process and scraper_process.poll() is None:
        return jsonify({'status': 'error',
                        'message': 'A scrape is running. Stop it before deleting sites.'}), 409

    body = request.json or {}
    names = body.get('names') or []
    if body.get('all'):
        names = list_site_dirs()
    # Only ever touch real site folders directly under data/.
    names = [n for n in names
             if not n.startswith('.') and os.path.isdir(os.path.join(DATA_DIR, n))
             and os.path.dirname(os.path.abspath(os.path.join(DATA_DIR, n))) == os.path.abspath(DATA_DIR)]
    if not names:
        return jsonify({'status': 'error', 'message': 'No matching sites to delete.'}), 400

    was_active = active_site_name()
    deleted, failed = [], []
    for name in names:
        try:
            shutil.rmtree(os.path.join(DATA_DIR, name))
            deleted.append(name)
        except Exception as e:
            logger.error(f"Could not delete site {name}: {e}")
            failed.append({'name': name, 'error': str(e)})

    # Drop cache entries pointing into the removed folders.
    for path in [p for p in list(_CACHE)
                 if any(os.path.join(DATA_DIR, n) in p for n in deleted)]:
        _CACHE.pop(path, None)

    # If the site on screen just went away, point at whatever remains.
    if was_active in deleted:
        remaining = list_site_dirs()
        try:
            if remaining:
                with open(ACTIVE_SITE_FILE, 'w', encoding='utf-8') as f:
                    f.write(remaining[0])
            elif os.path.exists(ACTIVE_SITE_FILE):
                os.remove(ACTIVE_SITE_FILE)
        except Exception as e:
            logger.error(f"Could not update active site after deletion: {e}")

    logger.info(f"Deleted {len(deleted)} site(s): {', '.join(deleted)}")
    return jsonify({'status': 'success', 'deleted': deleted, 'failed': failed,
                    'active': active_site_name()})

# ── Wipe ─────────────────────────────────────────────────────
@app.route('/api/system/wipe', methods=['POST'])
def wipe_everything():
    """Delete all scraped data, caches and logs. Irreversible."""
    global scraper_process
    if scraper_process and scraper_process.poll() is None:
        return jsonify({'status': 'error',
                        'message': 'A scrape is running. Stop it before wiping.'}), 409

    removed = []
    try:
        if os.path.isdir(DATA_DIR):
            for entry in os.listdir(DATA_DIR):
                path = os.path.join(DATA_DIR, entry)
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                removed.append(entry)
        os.makedirs(DATA_DIR, exist_ok=True)

        _CACHE.clear()

        if os.path.exists(LOG_FILE):
            open(LOG_FILE, 'w', encoding='utf-8').close()
            removed.append('scraper.log')

        logger.info(f"Wiped {len(removed)} item(s) from data directory")
        return jsonify({'status': 'success', 'removed': removed})
    except Exception as e:
        logger.error(f"Wipe failed: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ── System Status (health of every moving part in this app) ───
@app.route('/api/system/status', methods=['GET'])
def system_status():
    global scraper_process
    services = []

    # 1. Backend API — if this handler runs at all, Flask is up.
    services.append({
        'id': 'backend_api',
        'name': 'Backend API',
        'category': 'Core',
        'icon': 'server',
        'status': 'operational',
        'detail': f'Flask server up for {format_duration(time.time() - APP_START_TIME)}'
    })

    # 2. Scraper engine
    running = scraper_process is not None and scraper_process.poll() is None
    services.append({
        'id': 'scraper_engine',
        'name': 'Scraper Engine',
        'category': 'Core',
        'icon': 'cpu',
        'status': 'active' if running else 'operational',
        'detail': f'Running (PID {scraper_process.pid})' if running else 'Idle — ready to scrape'
    })

    # 3. Data storage
    try:
        exists = os.path.exists(DATA_DIR)
        writable = exists and os.access(DATA_DIR, os.W_OK)
        if not exists:
            services.append({'id': 'data_storage', 'name': 'Data Storage', 'category': 'Storage', 'icon': 'database',
                              'status': 'down', 'detail': 'Data directory missing'})
        elif not writable:
            services.append({'id': 'data_storage', 'name': 'Data Storage', 'category': 'Storage', 'icon': 'database',
                              'status': 'warning', 'detail': 'Data directory is not writable'})
        else:
            products = load_products_from_cache()
            services.append({'id': 'data_storage', 'name': 'Data Storage', 'category': 'Storage', 'icon': 'database',
                              'status': 'operational', 'detail': f'{len(products):,} products indexed'})
    except Exception as e:
        services.append({'id': 'data_storage', 'name': 'Data Storage', 'category': 'Storage', 'icon': 'database',
                          'status': 'down', 'detail': str(e)})

    # 4. Frontend build
    frontend_index = os.path.join(BASE_DIR, 'frontend', 'dist', 'index.html')
    if os.path.exists(frontend_index):
        built_at = datetime.fromtimestamp(os.path.getmtime(frontend_index)).strftime('%Y-%m-%d %H:%M')
        services.append({'id': 'frontend_build', 'name': 'Frontend Build', 'category': 'Core', 'icon': 'layers',
                          'status': 'operational', 'detail': f'Built {built_at}'})
    else:
        services.append({'id': 'frontend_build', 'name': 'Frontend Build', 'category': 'Core', 'icon': 'layers',
                          'status': 'down', 'detail': 'No production build found — run npm run build'})

    # 5. Logging
    if os.path.exists(LOG_FILE):
        size = os.path.getsize(LOG_FILE)
        age = time.time() - os.path.getmtime(LOG_FILE)
        services.append({'id': 'logging', 'name': 'Logging', 'category': 'Core', 'icon': 'file-text',
                          'status': 'operational', 'detail': f'{human_size(size)} — last write {format_duration(age)} ago'})
    else:
        services.append({'id': 'logging', 'name': 'Logging', 'category': 'Core', 'icon': 'file-text',
                          'status': 'warning', 'detail': 'No log file yet — run a scrape to generate one'})

    # 7. Disk space
    try:
        total, used, free = shutil.disk_usage(BASE_DIR)
        pct_free = (free / total * 100) if total else 0
        disk_status = 'operational' if pct_free > 10 else ('warning' if pct_free > 3 else 'down')
        services.append({'id': 'disk_space', 'name': 'Disk Space', 'category': 'System', 'icon': 'hard-drive',
                          'status': disk_status, 'detail': f'{human_size(free)} free of {human_size(total)} ({pct_free:.1f}% free)'})
    except Exception as e:
        services.append({'id': 'disk_space', 'name': 'Disk Space', 'category': 'System', 'icon': 'hard-drive',
                          'status': 'down', 'detail': str(e)})

    # 8. Python runtime
    services.append({'id': 'runtime', 'name': 'Python Runtime', 'category': 'System', 'icon': 'activity',
                      'status': 'operational', 'detail': f'Python {platform.python_version()} ({platform.system()})'})

    rank = {'operational': 0, 'active': 0, 'checking': 0, 'warning': 1, 'down': 2}
    overall_rank = max((rank.get(s['status'], 1) for s in services), default=0)
    overall = ['operational', 'degraded', 'down'][overall_rank]

    return jsonify({
        'overall': overall,
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'services': services
    })

# ── Logs ─────────────────────────────────────────────────────
@app.route('/api/logs', methods=['GET'])
def get_logs():
    lines = 100
    try:
        lines = int(request.args.get('lines', 100))
    except:
        pass
    if not os.path.exists(LOG_FILE):
        return jsonify({'logs': 'Log file not found. Start a scrape first.'})
    try:
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            all_lines = f.readlines()
            return jsonify({'logs': "".join(all_lines[-lines:])})
    except Exception as e:
        return jsonify({'logs': f"Error reading logs: {e}"})

# ── Progress ─────────────────────────────────────────────────
@app.route('/api/progress', methods=['GET'])
def get_progress():
    progress_file = os.path.join(DATA_DIR, 'progress.json')
    if not os.path.exists(progress_file):
        return jsonify({'current': 0, 'total': 0})
    try:
        with open(progress_file, 'r') as f:
            return jsonify(json.load(f))
    except:
        return jsonify({'current': 0, 'total': 0})

# ── Products (Pagination & Search) ────────────────────────────
@app.route('/api/products', methods=['GET'])
def get_products():
    page = int(request.args.get('page', 1))
    limit = int(request.args.get('limit', 20))
    search = request.args.get('search', '').lower()
    category = request.args.get('category', '')
    
    products_file = site_file('products.json')
    if not os.path.exists(products_file):
        return jsonify({
            'data': [],
            'total': 0,
            'page': page,
            'limit': limit,
            'totalPages': 0,
            'hasMore': False,
            'categories': []
        })
        
    try:
        all_products = load_products_from_cache()
            
        categories_file = site_file('categories.json')
        all_categories = []
        if os.path.exists(categories_file):
            all_categories = load_categories_from_cache()
                
        # Filter
        filtered = []
        for p in all_products:
            if not p.get('title'):
                continue
            if search and search not in p.get('title', '').lower() and search not in (p.get('short_description') or '').lower():
                continue
            if category and category not in p.get('categories', []):
                continue
            filtered.append(p)
            
        # Sort (optional, e.g. newest first, for now we just keep as scraped)
        
        # Paginate
        total = len(filtered)
        total_pages = (total + limit - 1) // limit
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated = filtered[start_idx:end_idx]
        
        return jsonify({
            'data': paginated,
            'total': total,
            'page': page,
            'limit': limit,
            'totalPages': total_pages,
            'hasMore': page < total_pages,
            'categories': sorted(all_categories)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/stats', methods=['GET'])
def get_stats():
    products_file = site_file('products.json')
    if not os.path.exists(products_file):
        return jsonify({'total_products': 0, 'total_categories': 0, 'total_brands': 0, 'total_images': 0})
        
    try:
        products = load_products_from_cache()
        
        unique_categories = set()
        unique_brands = set()
        total_images = 0
        
        for p in products:
            if not p.get('title'): continue
            if p.get('categories'):
                unique_categories.update(p['categories'])
            if p.get('brand') and p['brand'] != 'Unknown':
                unique_brands.add(p['brand'])
            if p.get('images'):
                total_images += len(p['images'])
                
        return jsonify({
            'total_products': len(products),
            'total_categories': len(unique_categories),
            'total_brands': len(unique_brands),
            'total_images': total_images
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── File Explorer ─────────────────────────────────────────────
@app.route('/api/files', methods=['GET'])
def list_files():
    if not os.path.exists(DATA_DIR):
        return jsonify([])

    def get_tree(path):
        tree = []
        try:
            items = sorted(os.listdir(path))
        except:
            return tree
        for item in items:
            if item.startswith('.'):
                continue  # internal state (.active_site) and export scratch space
            item_path = os.path.join(path, item)
            rel_path = os.path.relpath(item_path, DATA_DIR)
            is_dir = os.path.isdir(item_path)
            tree.append({
                'name': item,
                'path': rel_path,
                'type': 'directory' if is_dir else 'file'
            })
        return tree

    req_path = request.args.get('path', '')
    target_dir = os.path.join(DATA_DIR, req_path)
    
    # Security check to prevent traversing outside DATA_DIR
    if not os.path.abspath(target_dir).startswith(os.path.abspath(DATA_DIR)):
        return jsonify([])
        
    return jsonify(get_tree(target_dir))

# ── Dynamic Image Serving ─────────────────────────────────────
@app.route('/api/image', methods=['GET'])
def get_image():
    title = request.args.get('title', '')
    filename = request.args.get('filename', '')
    if not title or not filename:
        return jsonify({'error': 'Missing title or filename'}), 400
        
    import re
    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', title)
    safe_name = re.sub(r'_+', '_', safe_name).strip('_')
    
    # 1. Check flat path within the active site
    old_path = os.path.join(site_file('images'), safe_name, filename) if site_file('images') else ''
    if old_path and os.path.exists(old_path):
        return send_from_directory(os.path.dirname(old_path), filename)

    # 2. Check structured path by scanning
    structured_dir = site_file('structured')
    if os.path.exists(structured_dir):
        for root, dirs, files in os.walk(structured_dir):
            if os.path.basename(root) == safe_name:
                img_path = os.path.join(root, 'images', filename)
                if os.path.exists(img_path):
                    return send_from_directory(os.path.dirname(img_path), filename)
                    
    return jsonify({'error': 'Image not found'}), 404

# ── Serve Data Files ──────────────────────────────────────────
@app.route('/data/<path:filename>')
def serve_data(filename):
    return send_from_directory(DATA_DIR, filename)

# ── Export ────────────────────────────────────────────────────
@app.route('/api/export/json', methods=['GET'])
def export_json():
    products_file = site_file('products.json')
    if not os.path.exists(products_file):
        return jsonify({'error': 'No data to export'}), 404
        
    is_clean = request.args.get('clean', 'true').lower() in ('true', '1')
    
    if not is_clean:
        return send_from_directory(active_site_dir(), 'products.json', as_attachment=True, download_name=f'{export_prefix()}_products.json')
        
    try:
        data = load_products_from_cache()
        for item in data:
            if 'short_description' in item:
                item['short_description'] = clean_html(item['short_description'])
            if 'long_description' in item:
                item['long_description'] = clean_html(item['long_description'])
        
        output = io.BytesIO()
        output.write(json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8'))
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='application/json',
            headers={'Content-Disposition': f'attachment; filename={export_prefix()}_products.json'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def clean_html(raw_html):
    if not raw_html: return ""
    text = re.sub(r'<(br|/p|/li|div[^>]*)>', '\n', raw_html, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    import html
    text = html.unescape(text)
    text = re.sub(r'\n\s*\n', '\n', text).strip()
    return text

@app.route('/api/export/csv', methods=['GET'])
def export_csv():
    products_file = site_file('products.json')
    if not os.path.exists(products_file):
        return jsonify({'error': 'No data to export'}), 404
        
    is_clean = request.args.get('clean', 'true').lower() in ('true', '1')
    
    try:
        products = load_products_from_cache()
        output = io.StringIO()
        fieldnames = ['title', 'brand', 'price', 'url', 'categories', 'images', 'short_description', 'long_description']
        writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for p in products:
            if not p.get('title'):
                continue
                
            short_desc = p.get('short_description', '') or ''
            long_desc = p.get('long_description', '') or ''
            
            if is_clean:
                short_desc = clean_html(short_desc)
                long_desc = clean_html(long_desc)
            else:
                short_desc = short_desc.replace('<br>', '\n')
                long_desc = long_desc.replace('<br>', '\n')
                
            row = {
                'title': p.get('title', ''),
                'brand': p.get('brand', ''),
                'price': p.get('price', ''),
                'url': p.get('url', ''),
                'categories': ' > '.join(p.get('categories', [])),
                'images': ' | '.join(p.get('images', [])),
                'short_description': short_desc,
                'long_description': long_desc
            }
            writer.writerow(row)
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={export_prefix()}_products.csv'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/excel', methods=['GET'])
def export_excel():
    products_file = site_file('products.json')
    if not os.path.exists(products_file):
        return jsonify({'error': 'No data to export'}), 404
        
    is_clean = request.args.get('clean', 'true').lower() in ('true', '1')
    
    try:
        import pandas as pd
        products = load_products_from_cache()
            
        data = []
        for p in products:
            if not p.get('title'): continue
            
            short_desc = p.get('short_description', '') or ''
            long_desc = p.get('long_description', '') or ''
            
            if is_clean:
                short_desc = clean_html(short_desc)
                long_desc = clean_html(long_desc)
            else:
                short_desc = short_desc.replace('<br>', '\n')
                long_desc = long_desc.replace('<br>', '\n')
                
            data.append({
                'Title': re.sub(r'[\000-\010]|[\013-\014]|[\016-\037]', '', p.get('title', '')),
                'Brand': re.sub(r'[\000-\010]|[\013-\014]|[\016-\037]', '', p.get('brand', '')),
                'Price': re.sub(r'[\000-\010]|[\013-\014]|[\016-\037]', '', p.get('price', '')),
                'URL': re.sub(r'[\000-\010]|[\013-\014]|[\016-\037]', '', p.get('url', '')),
                'Categories': re.sub(r'[\000-\010]|[\013-\014]|[\016-\037]', '', ' > '.join(p.get('categories', []))),
                'Images': re.sub(r'[\000-\010]|[\013-\014]|[\016-\037]', '', ' | '.join(p.get('images', []))),
                'Short Description': re.sub(r'[\000-\010]|[\013-\014]|[\016-\037]', '', short_desc),
                'Long Description': re.sub(r'[\000-\010]|[\013-\014]|[\016-\037]', '', long_desc)
            })
            
        df = pd.DataFrame(data)
        excel_path = os.path.join(export_workspace(), 'products.xlsx')
        df.to_excel(excel_path, index=False)
        return send_from_directory(export_workspace(), 'products.xlsx', as_attachment=True, download_name=f'{export_prefix()}_products.xlsx')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/xml', methods=['GET'])
def export_xml():
    products_file = site_file('products.json')
    if not os.path.exists(products_file):
        return jsonify({'error': 'No data to export'}), 404
        
    is_clean = request.args.get('clean', 'true').lower() in ('true', '1')
    
    try:
        data = load_products_from_cache()
        xml_path = os.path.join(export_workspace(), 'products.xml')
        with open(xml_path, 'w', encoding='utf-8') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n<products>\n')
            for item in data:
                if not item.get('title'): continue
                f.write('  <product>\n')
                
                if is_clean:
                    if 'short_description' in item:
                        item['short_description'] = clean_html(item['short_description'])
                    if 'long_description' in item:
                        item['long_description'] = clean_html(item['long_description'])
                        
                for k, v in item.items():
                    if isinstance(v, list):
                        v = ', '.join([str(i).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;') for i in v])
                    else:
                        v = str(v).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    f.write(f'    <{k}>{v}</{k}>\n')
                f.write('  </product>\n')
            f.write('</products>')
        return send_from_directory(export_workspace(), 'products.xml', as_attachment=True, download_name=f'{export_prefix()}_products.xml')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/images', methods=['GET'])
def export_images_all():
    # Legacy export, redirects to structured export
    return export_structured()

@app.route('/api/export/structured', methods=['GET'])
def export_structured():
    images_dir = site_file('structured')
    if not os.path.exists(images_dir):
        images_dir = site_file('images')
    if not os.path.exists(images_dir):
        return jsonify({'error': 'No data to export'}), 404
    try:
        import shutil
        zip_path = os.path.join(export_workspace(), 'structured_export')
        shutil.make_archive(zip_path, 'zip', images_dir)
        return send_from_directory(export_workspace(), 'structured_export.zip', as_attachment=True, download_name=f'{export_prefix()}_structured.zip')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/structured/data', methods=['GET'])
def export_structured_data():
    structured_dir = site_file('structured')
    if not os.path.exists(structured_dir):
        return jsonify({'error': 'No structured data to export'}), 404
    try:
        import zipfile
        zip_path = os.path.join(export_workspace(), 'structured_data.zip')
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(structured_dir):
                # Exclude images directories
                if 'images' in dirs:
                    dirs.remove('images')
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, structured_dir)
                    zipf.write(file_path, arcname)
        return send_from_directory(export_workspace(), 'structured_data.zip', as_attachment=True, download_name=f'{export_prefix()}_data_only.zip')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/structured/images', methods=['GET'])
def export_structured_images():
    structured_dir = site_file('structured')
    if not os.path.exists(structured_dir):
        return jsonify({'error': 'No structured data to export'}), 404
    try:
        import zipfile
        zip_path = os.path.join(export_workspace(), 'structured_images.zip')
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(structured_dir):
                if os.path.basename(root) == 'images':
                    for file in files:
                        file_path = os.path.join(root, file)
                        # Keep the product folder name before /images
                        arcname = os.path.join(os.path.basename(os.path.dirname(root)), file)
                        zipf.write(file_path, arcname)
        return send_from_directory(export_workspace(), 'structured_images.zip', as_attachment=True, download_name=f'{export_prefix()}_images_only.zip')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ── Composable exports ────────────────────────────────────────
# The builders below take a product list rather than reading the active site,
# so one request can export any combination of sites and formats.

PRODUCT_FIELDS = ['title', 'brand', 'price', 'url', 'categories', 'images',
                  'short_description', 'long_description']

def _descriptions(product, clean):
    short = product.get('short_description', '') or ''
    long_ = product.get('long_description', '') or ''
    if clean:
        return clean_html(short), clean_html(long_)
    return short.replace('<br>', '\n'), long_.replace('<br>', '\n')

def _rows(products, clean):
    for p in products:
        if not p.get('title'):
            continue
        short, long_ = _descriptions(p, clean)
        yield {
            'title': p.get('title', ''), 'brand': p.get('brand', ''),
            'price': p.get('price', ''), 'url': p.get('url', ''),
            'categories': ' > '.join(p.get('categories', [])),
            'images': ' | '.join(p.get('images', [])),
            'short_description': short, 'long_description': long_,
        }

def build_json(products, clean):
    data = []
    for p in products:
        item = dict(p)
        item['short_description'], item['long_description'] = _descriptions(p, clean)
        data.append(item)
    return json.dumps(data, indent=2, ensure_ascii=False).encode('utf-8')

def build_csv(products, clean):
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=PRODUCT_FIELDS, extrasaction='ignore')
    writer.writeheader()
    for row in _rows(products, clean):
        writer.writerow(row)
    return out.getvalue().encode('utf-8-sig')

def build_excel(products, clean):
    import pandas as pd
    strip_ctrl = lambda s: re.sub(r'[\000-\010]|[\013-\014]|[\016-\037]', '', str(s))
    frame = pd.DataFrame([{k.replace('_', ' ').title(): strip_ctrl(v) for k, v in row.items()}
                          for row in _rows(products, clean)])
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
        frame.to_excel(writer, index=False, sheet_name='Products')
    return buffer.getvalue()

def build_xml(products, clean):
    esc = lambda v: str(v).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    parts = ['<?xml version="1.0" encoding="UTF-8"?>\n<products>\n']
    for p in products:
        if not p.get('title'):
            continue
        item = dict(p)
        item['short_description'], item['long_description'] = _descriptions(p, clean)
        parts.append('  <product>\n')
        for key, value in item.items():
            value = ', '.join(esc(v) for v in value) if isinstance(value, list) else esc(value)
            parts.append(f'    <{key}>{value}</{key}>\n')
        parts.append('  </product>\n')
    parts.append('</products>')
    return ''.join(parts).encode('utf-8')

def build_list_csv(products, key, header):
    values = set()
    for p in products:
        field = p.get(key)
        for item in (field if isinstance(field, list) else [field]):
            if item and str(item).strip() and str(item) != 'Unknown':
                values.add(str(item).strip())
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([header])
    for value in sorted(values):
        writer.writerow([value])
    return out.getvalue().encode('utf-8-sig')

EXPORT_BUILDERS = {
    'json':       ('products.json', lambda p, c: build_json(p, c)),
    'csv':        ('products.csv',  lambda p, c: build_csv(p, c)),
    'excel':      ('products.xlsx', lambda p, c: build_excel(p, c)),
    'xml':        ('products.xml',  lambda p, c: build_xml(p, c)),
    'categories': ('categories.csv', lambda p, c: build_list_csv(p, 'categories', 'Category Name')),
    'brands':     ('brands.csv',     lambda p, c: build_list_csv(p, 'brand', 'Brand Name')),
}

# Archive formats copy files off disk rather than serialising products.
ARCHIVE_FORMATS = {
    'archive_all': ('everything', lambda name: True),
    'archive_data': ('data-only', lambda name: name != 'images'),
    'archive_images': ('images-only', lambda name: name == 'images'),
}

class ExportCancelled(Exception):
    """Raised inside a running export job once the user asks to cancel."""


def add_structured_to_zip(zipf, site, fmt, prefix, on_progress=None, should_cancel=None):
    structured = os.path.join(DATA_DIR, site, 'structured')
    if not os.path.isdir(structured):
        return 0
    written = 0
    for root, dirs, files in os.walk(structured):
        in_images = os.path.basename(root) == 'images'
        if fmt == 'archive_data' and in_images:
            dirs[:] = []
            continue
        if fmt == 'archive_images' and not in_images:
            continue
        for filename in files:
            full = os.path.join(root, filename)
            arc = os.path.join(prefix, os.path.relpath(full, structured))
            try:
                zipf.write(full, arc)
                written += 1
                # Archives can run to thousands of files; report progress and
                # check for cancellation periodically so the UI keeps moving
                # and a cancel doesn't wait for the whole archive to finish.
                if written % 50 == 0:
                    if should_cancel and should_cancel():
                        raise ExportCancelled()
                    if on_progress:
                        on_progress(written)
            except ExportCancelled:
                raise
            except Exception as e:
                logger.error(f"Skipping {full} in archive: {e}")
    if on_progress:
        on_progress(written)
    return written


# ── Export jobs ───────────────────────────────────────────────
# Exports can take minutes (a full image archive is thousands of files), which
# is far too long to hold a request open. Work happens on a background thread
# and the client polls for progress.
_export_jobs = {}
_export_jobs_lock = threading.Lock()
EXPORT_JOB_TTL = 3600  # seconds to keep a finished export around for download

FORMAT_LABELS = {
    'json': 'JSON', 'csv': 'CSV', 'excel': 'Excel', 'xml': 'XML',
    'categories': 'category list', 'brands': 'brand list',
    'archive_all': 'full archive', 'archive_data': 'data archive',
    'archive_images': 'image archive',
}

def _job_update(job_id, **fields):
    with _export_jobs_lock:
        if job_id in _export_jobs:
            _export_jobs[job_id].update(fields)

def _purge_old_export_jobs():
    now = time.time()
    with _export_jobs_lock:
        stale = [jid for jid, job in _export_jobs.items()
                 if now - job.get('created', now) > EXPORT_JOB_TTL]
        for jid in stale:
            path = _export_jobs[jid].get('path')
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
            del _export_jobs[jid]

def _job_cancelled(job_id):
    with _export_jobs_lock:
        return bool(_export_jobs.get(job_id, {}).get('cancel_requested'))


def _run_export_job(job_id, sites, formats, clean):
    suffix = '' if clean else 'raw_'
    steps = [(site, fmt) for site in sites for fmt in formats]
    _job_update(job_id, state='running', total_steps=len(steps), step=0,
                message='Preparing…')

    path = None
    try:
        def check_cancelled():
            if _job_cancelled(job_id):
                raise ExportCancelled()

        products_cache = {}

        def products_for(site):
            if site not in products_cache:
                _job_update(job_id, message=f'Loading products for {site}…')
                products_cache[site] = load_json_cached(
                    os.path.join(DATA_DIR, site, 'products.json'), [])
            return products_cache[site]

        # A single plain file needs no ZIP wrapper.
        if len(sites) == 1 and len(formats) == 1 and formats[0] in EXPORT_BUILDERS:
            site, fmt = sites[0], formats[0]
            filename, builder = EXPORT_BUILDERS[fmt]
            _job_update(job_id, step=0, message=f'Building {FORMAT_LABELS.get(fmt, fmt)} for {site}…')
            check_cancelled()
            payload = builder(products_for(site), clean)
            check_cancelled()
            path = os.path.join(export_workspace(), f'{job_id}_{filename}')
            with open(path, 'wb') as f:
                f.write(payload)
            _job_update(job_id, state='ready', step=1, path=path,
                        filename=f"{site.replace('.', '_')}_{suffix}{filename}",
                        size=os.path.getsize(path), message='Ready to download')
            return

        path = os.path.join(export_workspace(), f'{job_id}.zip')
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for index, (site, fmt) in enumerate(steps, start=1):
                check_cancelled()
                label = FORMAT_LABELS.get(fmt, fmt)
                _job_update(job_id, step=index - 1,
                            message=f'Building {label} for {site}…')
                folder = site if len(sites) > 1 else ''
                try:
                    if fmt in EXPORT_BUILDERS:
                        filename, builder = EXPORT_BUILDERS[fmt]
                        zipf.writestr(os.path.join(folder, f'{suffix}{filename}'),
                                      builder(products_for(site), clean))
                    else:
                        archive_label = ARCHIVE_FORMATS[fmt][0]

                        def progress(count, _site=site, _label=label):
                            _job_update(job_id,
                                        message=f'Adding {_label} for {_site}… {count:,} files')

                        add_structured_to_zip(zipf, site, fmt,
                                              os.path.join(folder, archive_label),
                                              on_progress=progress,
                                              should_cancel=lambda: _job_cancelled(job_id))
                except ExportCancelled:
                    raise
                except Exception as e:
                    # One bad format must not cost the rest of the bundle.
                    logger.error(f"Export {fmt} for {site} failed: {e}")
                    zipf.writestr(os.path.join(folder, f'{fmt}_FAILED.txt'), str(e))
                _job_update(job_id, step=index)

        _job_update(job_id, message='Compressing…')
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        name = (f"export_{sites[0].replace('.', '_')}_{stamp}.zip" if len(sites) == 1
                else f"export_{len(sites)}_sites_{stamp}.zip")
        _job_update(job_id, state='ready', path=path, filename=name,
                    size=os.path.getsize(path), message='Ready to download')
    except ExportCancelled:
        # Drop the half-built archive rather than leaving hundreds of MB behind.
        _discard_export_file(path)
        logger.info(f"Export job {job_id} cancelled by user")
        _job_update(job_id, state='cancelled', path=None, message='Export cancelled')
    except Exception as e:
        _discard_export_file(path)
        logger.error(f"Export job {job_id} failed: {e}")
        _job_update(job_id, state='error', path=None, error=str(e), message='Export failed')


def _discard_export_file(path):
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except Exception as e:
            logger.error(f"Could not remove partial export {path}: {e}")

@app.route('/api/export/start', methods=['POST'])
def start_export():
    body = request.json or {}
    sites = [s for s in (body.get('sites') or []) if os.path.isdir(os.path.join(DATA_DIR, s))]
    formats = [f for f in (body.get('formats') or [])
               if f in EXPORT_BUILDERS or f in ARCHIVE_FORMATS]
    clean = bool(body.get('clean', True))

    if not sites:
        return jsonify({'error': 'Select at least one site to export.'}), 400
    if not formats:
        return jsonify({'error': 'Select at least one export format.'}), 400

    _purge_old_export_jobs()
    job_id = uuid.uuid4().hex[:12]
    with _export_jobs_lock:
        _export_jobs[job_id] = {
            'id': job_id, 'state': 'queued', 'step': 0,
            'total_steps': len(sites) * len(formats),
            'message': 'Queued…', 'created': time.time(),
            'sites': sites, 'formats': formats,
        }
    threading.Thread(target=_run_export_job, args=(job_id, sites, formats, clean),
                     daemon=True).start()
    return jsonify({'job_id': job_id})

@app.route('/api/export/status/<job_id>', methods=['GET'])
def export_status(job_id):
    with _export_jobs_lock:
        job = _export_jobs.get(job_id)
        if not job:
            return jsonify({'error': 'Unknown or expired export job'}), 404
        return jsonify({k: v for k, v in job.items() if k != 'path'})

@app.route('/api/export/cancel/<job_id>', methods=['POST'])
def cancel_export(job_id):
    """Ask a running export to stop.

    The worker checks this flag between steps and every 50 files while packing
    an archive, so a cancel takes effect quickly rather than waiting for a
    multi-thousand-file archive to finish.
    """
    with _export_jobs_lock:
        job = _export_jobs.get(job_id)
        if not job:
            return jsonify({'status': 'error', 'message': 'Unknown or expired export job'}), 404
        if job['state'] in ('ready', 'error', 'cancelled'):
            return jsonify({'status': 'success', 'state': job['state'],
                            'message': 'Export already finished'})
        job['cancel_requested'] = True
        job['message'] = 'Cancelling…'
    return jsonify({'status': 'success', 'state': 'cancelling'})

@app.route('/api/export/download/<job_id>', methods=['GET'])
def export_download(job_id):
    with _export_jobs_lock:
        job = _export_jobs.get(job_id)
    if not job or job.get('state') != 'ready':
        return jsonify({'error': 'Export is not ready'}), 404
    path = job.get('path')
    if not path or not os.path.exists(path):
        return jsonify({'error': 'Export file is no longer available'}), 404
    return send_from_directory(os.path.dirname(path), os.path.basename(path),
                               as_attachment=True, download_name=job['filename'])

@app.route('/api/export/bundle', methods=['POST'])
def export_bundle():
    """Export any combination of sites and formats.

    One site and one file format downloads that file directly; anything more
    is bundled into a ZIP with a folder per site.
    """
    body = request.json or {}
    sites = [s for s in (body.get('sites') or []) if os.path.isdir(os.path.join(DATA_DIR, s))]
    formats = [f for f in (body.get('formats') or [])
               if f in EXPORT_BUILDERS or f in ARCHIVE_FORMATS]
    clean = bool(body.get('clean', True))

    if not sites:
        return jsonify({'error': 'Select at least one site to export.'}), 400
    if not formats:
        return jsonify({'error': 'Select at least one export format.'}), 400

    suffix = '' if clean else '_raw'

    # Fast path: a single plain file, downloaded as-is.
    if len(sites) == 1 and len(formats) == 1 and formats[0] in EXPORT_BUILDERS:
        site, fmt = sites[0], formats[0]
        filename, builder = EXPORT_BUILDERS[fmt]
        try:
            payload = builder(load_json_cached(os.path.join(DATA_DIR, site, 'products.json'), []), clean)
        except Exception as e:
            logger.error(f"Export {fmt} for {site} failed: {e}")
            return jsonify({'error': str(e)}), 500
        download = f"{site.replace('.', '_')}{suffix}_{filename}"
        return Response(payload, mimetype='application/octet-stream',
                        headers={'Content-Disposition': f'attachment; filename={download}'})

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for site in sites:
            products = load_json_cached(os.path.join(DATA_DIR, site, 'products.json'), [])
            folder = site if len(sites) > 1 else ''
            for fmt in formats:
                try:
                    if fmt in EXPORT_BUILDERS:
                        filename, builder = EXPORT_BUILDERS[fmt]
                        zipf.writestr(os.path.join(folder, f"{suffix.lstrip('_') or 'clean'}_{filename}"
                                                   if suffix else filename),
                                      builder(products, clean))
                    else:
                        label = ARCHIVE_FORMATS[fmt][0]
                        add_structured_to_zip(zipf, site, fmt, os.path.join(folder, label))
                except Exception as e:
                    # One bad format shouldn't lose the rest of the bundle.
                    logger.error(f"Export {fmt} for {site} failed: {e}")
                    zipf.writestr(os.path.join(folder, f'{fmt}_FAILED.txt'), str(e))

    buffer.seek(0)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    name = f"export_{sites[0].replace('.', '_')}_{stamp}.zip" if len(sites) == 1 \
        else f"export_{len(sites)}_sites_{stamp}.zip"
    return Response(buffer.getvalue(), mimetype='application/zip',
                    headers={'Content-Disposition': f'attachment; filename={name}'})

@app.route('/api/export/categories', methods=['GET'])
def export_categories():
    cat_file = site_file('categories.json')
    if not os.path.exists(cat_file):
        return jsonify({'error': 'No categories to export'}), 404
    return send_from_directory(active_site_dir(), 'categories.json', as_attachment=True, download_name='categories.json')

@app.route('/api/export/categories_csv', methods=['GET'])
def export_categories_csv():
    try:
        all_products = load_products_from_cache()
        categories = set()
        for p in all_products:
            for cat in p.get('categories', []):
                if cat: categories.add(cat.strip())
                
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Category Name'])
        for cat in sorted(list(categories)):
            writer.writerow([cat])
            
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={export_prefix()}_categories.csv'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/brands_csv', methods=['GET'])
def export_brands_csv():
    products_file = site_file('products.json')
    if not os.path.exists(products_file):
        return jsonify({'error': 'No data to export'}), 404
    try:
        products = load_products_from_cache()
            
        brands = set()
        for p in products:
            b = p.get('brand')
            if b: brands.add(b.strip())
                
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['Brand Name'])
        for b in sorted(list(brands)):
            writer.writerow([b])
            
        output.seek(0)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename={export_prefix()}_brands.csv'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    app.run(host='0.0.0.0', port=5000, debug=False)
