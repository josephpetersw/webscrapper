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
from datetime import datetime, timezone
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

# ── Target Site Reachability (checked in the background so status
# requests never block on a live network call) ──────────────────
TARGET_SITE_URL = 'https://www.phoneplacekenya.com'
TARGET_SITE_CHECK_INTERVAL = 60  # seconds
_target_site_lock = threading.Lock()
_target_site_state = {'status': 'checking', 'detail': 'Awaiting first check...', 'checked_at': None}

def _check_target_site_loop():
    from curl_cffi import requests as curl_requests
    while True:
        try:
            start = time.time()
            session = curl_requests.Session(impersonate="chrome")
            resp = session.get(TARGET_SITE_URL, timeout=10)
            elapsed_ms = round((time.time() - start) * 1000)
            with _target_site_lock:
                if resp.status_code == 200:
                    _target_site_state['status'] = 'operational'
                    _target_site_state['detail'] = f'Responded 200 OK in {elapsed_ms}ms'
                else:
                    _target_site_state['status'] = 'warning'
                    _target_site_state['detail'] = f'Responded HTTP {resp.status_code} in {elapsed_ms}ms'
                _target_site_state['checked_at'] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            with _target_site_lock:
                _target_site_state['status'] = 'down'
                _target_site_state['detail'] = f'Unreachable: {e}'
                _target_site_state['checked_at'] = datetime.now(timezone.utc).isoformat()
        time.sleep(TARGET_SITE_CHECK_INTERVAL)

threading.Thread(target=_check_target_site_loop, daemon=True).start()

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

# ── Memory Cache ────────────────────────────────────────────
_CACHE = {
    'products': None,
    'products_mtime': 0,
    'categories': None,
    'categories_mtime': 0
}

def load_products_from_cache():
    products_file = os.path.join(DATA_DIR, 'products.json')
    if not os.path.exists(products_file):
        return []
    
    try:
        mtime = os.path.getmtime(products_file)
        if _CACHE['products'] is None or mtime > _CACHE['products_mtime']:
            with open(products_file, 'r', encoding='utf-8') as f:
                _CACHE['products'] = json.load(f)
            _CACHE['products_mtime'] = mtime
        return _CACHE['products']
    except Exception as e:
        logger.error(f"Error reading products.json: {e}")
        return []

def load_categories_from_cache():
    cat_file = os.path.join(DATA_DIR, 'categories.json')
    if not os.path.exists(cat_file):
        return []
        
    try:
        mtime = os.path.getmtime(cat_file)
        if _CACHE['categories'] is None or mtime > _CACHE['categories_mtime']:
            with open(cat_file, 'r', encoding='utf-8') as f:
                _CACHE['categories'] = json.load(f)
            _CACHE['categories_mtime'] = mtime
        return _CACHE['categories']
    except Exception as e:
        logger.error(f"Error reading categories.json: {e}")
        return []

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
    workers = req_data.get('workers', 20)

    cmd = [VENV_PYTHON, MAIN_SCRIPT]
    if url:
        cmd.extend(['--target_url', url])
    if limit:
        cmd.extend(['--limit', str(limit)])
    
    cmd.extend(['--workers', str(workers)])

    try:
        scraper_process = subprocess.Popen(cmd, cwd=BASE_DIR)
        return jsonify({'status': 'success', 'message': 'Scraping started', 'pid': scraper_process.pid})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/scrape/stop', methods=['POST'])
def stop_scrape():
    global scraper_process
    if scraper_process and scraper_process.poll() is None:
        try:
            scraper_process.terminate()
            scraper_process.wait(timeout=5)
        except:
            scraper_process.kill()
        scraper_process = None
        return jsonify({'status': 'success', 'message': 'Scraper stopped'})
    return jsonify({'status': 'error', 'message': 'No scraper running'}), 400

@app.route('/api/status', methods=['GET'])
def get_status():
    global scraper_process
    running = scraper_process is not None and scraper_process.poll() is None
    return jsonify({'running': running, 'pid': scraper_process.pid if running else None})

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

    # 6. Target site (external dependency, checked in the background)
    with _target_site_lock:
        ts = dict(_target_site_state)
    services.append({'id': 'target_site', 'name': 'XphoneKenya.com', 'category': 'External', 'icon': 'globe',
                      'status': ts['status'], 'detail': ts['detail'], 'checked_at': ts['checked_at']})

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
    
    products_file = os.path.join(DATA_DIR, 'products.json')
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
            
        categories_file = os.path.join(DATA_DIR, 'categories.json')
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
    products_file = os.path.join(DATA_DIR, 'products.json')
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
    
    # 1. Check old flat path
    old_path = os.path.join(DATA_DIR, 'images', safe_name, filename)
    if os.path.exists(old_path):
        return send_from_directory(os.path.dirname(old_path), filename)
        
    # 2. Check structured path by scanning
    structured_dir = os.path.join(DATA_DIR, 'structured')
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
    products_file = os.path.join(DATA_DIR, 'products.json')
    if not os.path.exists(products_file):
        return jsonify({'error': 'No data to export'}), 404
        
    is_clean = request.args.get('clean', 'true').lower() in ('true', '1')
    
    if not is_clean:
        return send_from_directory(DATA_DIR, 'products.json', as_attachment=True, download_name='phoneplacekenya_products.json')
        
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
            headers={'Content-Disposition': 'attachment; filename=phoneplacekenya_products.json'}
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
    products_file = os.path.join(DATA_DIR, 'products.json')
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
            headers={'Content-Disposition': 'attachment; filename=phoneplacekenya_products.csv'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/excel', methods=['GET'])
def export_excel():
    products_file = os.path.join(DATA_DIR, 'products.json')
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
        excel_path = os.path.join(DATA_DIR, 'products.xlsx')
        df.to_excel(excel_path, index=False)
        return send_from_directory(DATA_DIR, 'products.xlsx', as_attachment=True, download_name='phoneplacekenya_products.xlsx')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/xml', methods=['GET'])
def export_xml():
    products_file = os.path.join(DATA_DIR, 'products.json')
    if not os.path.exists(products_file):
        return jsonify({'error': 'No data to export'}), 404
        
    is_clean = request.args.get('clean', 'true').lower() in ('true', '1')
    
    try:
        data = load_products_from_cache()
        xml_path = os.path.join(DATA_DIR, 'products.xml')
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
        return send_from_directory(DATA_DIR, 'products.xml', as_attachment=True, download_name='phoneplacekenya_products.xml')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/images', methods=['GET'])
def export_images_all():
    # Legacy export, redirects to structured export
    return export_structured()

@app.route('/api/export/structured', methods=['GET'])
def export_structured():
    images_dir = os.path.join(DATA_DIR, 'structured')
    if not os.path.exists(images_dir):
        images_dir = os.path.join(DATA_DIR, 'images')
    if not os.path.exists(images_dir):
        return jsonify({'error': 'No data to export'}), 404
    try:
        import shutil
        zip_path = os.path.join(DATA_DIR, 'structured_export')
        shutil.make_archive(zip_path, 'zip', images_dir)
        return send_from_directory(DATA_DIR, 'structured_export.zip', as_attachment=True, download_name='phoneplacekenya_structured.zip')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/structured/data', methods=['GET'])
def export_structured_data():
    structured_dir = os.path.join(DATA_DIR, 'structured')
    if not os.path.exists(structured_dir):
        return jsonify({'error': 'No structured data to export'}), 404
    try:
        import zipfile
        zip_path = os.path.join(DATA_DIR, 'structured_data.zip')
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(structured_dir):
                # Exclude images directories
                if 'images' in dirs:
                    dirs.remove('images')
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, structured_dir)
                    zipf.write(file_path, arcname)
        return send_from_directory(DATA_DIR, 'structured_data.zip', as_attachment=True, download_name='phoneplacekenya_data_only.zip')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/structured/images', methods=['GET'])
def export_structured_images():
    structured_dir = os.path.join(DATA_DIR, 'structured')
    if not os.path.exists(structured_dir):
        return jsonify({'error': 'No structured data to export'}), 404
    try:
        import zipfile
        zip_path = os.path.join(DATA_DIR, 'structured_images.zip')
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(structured_dir):
                if os.path.basename(root) == 'images':
                    for file in files:
                        file_path = os.path.join(root, file)
                        # Keep the product folder name before /images
                        arcname = os.path.join(os.path.basename(os.path.dirname(root)), file)
                        zipf.write(file_path, arcname)
        return send_from_directory(DATA_DIR, 'structured_images.zip', as_attachment=True, download_name='phoneplacekenya_images_only.zip')
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/categories', methods=['GET'])
def export_categories():
    cat_file = os.path.join(DATA_DIR, 'categories.json')
    if not os.path.exists(cat_file):
        return jsonify({'error': 'No categories to export'}), 404
    return send_from_directory(DATA_DIR, 'categories.json', as_attachment=True, download_name='categories.json')

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
            headers={'Content-Disposition': 'attachment; filename=phoneplacekenya_categories.csv'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/brands_csv', methods=['GET'])
def export_brands_csv():
    products_file = os.path.join(DATA_DIR, 'products.json')
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
            headers={'Content-Disposition': 'attachment; filename=phoneplacekenya_brands.csv'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    os.makedirs(DATA_DIR, exist_ok=True)
    app.run(host='0.0.0.0', port=5000, debug=False)
