import os
import re
import html
import signal
import subprocess
import json
import csv
import io
import time
import logging
import threading
from flask import Flask, jsonify, request, send_from_directory, Response
from flask_cors import CORS

from scraper.client import logger

app = Flask(__name__, static_folder='frontend/dist', static_url_path='')
CORS(app)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
LOG_FILE = os.path.join(BASE_DIR, 'scraper.log')
VENV_PYTHON = os.path.join(BASE_DIR, 'venv', 'bin', 'python')
MAIN_SCRIPT = os.path.join(BASE_DIR, 'main.py')
PID_FILE = os.path.join(DATA_DIR, 'scraper.pid')
PRODUCTS_JSONL = os.path.join(DATA_DIR, 'products.jsonl')
PRODUCTS_JSON = os.path.join(DATA_DIR, 'products.json')

def _safe_component(text):
    # Must stay identical to main.py's _safe_component (folder names must match)
    safe = re.sub(r'[^a-zA-Z0-9]', '_', text)
    return re.sub(r'_+', '_', safe).strip('_')

# ── Scraper process tracking (PID file) ──────────────────────
# The PID lives on disk, not in a module global, so all gunicorn worker
# processes agree on whether a scraper is running.

def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True

def _pid_is_scraper(pid):
    if not _pid_alive(pid):
        return False
    try:
        with open(f'/proc/{pid}/cmdline', 'rb') as f:
            return b'main.py' in f.read()
    except OSError:
        return True  # no /proc (non-Linux): liveness check is the best we can do

def _scraper_pid():
    try:
        with open(PID_FILE, 'r') as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    return pid if _pid_is_scraper(pid) else None

# ── Memory Cache ────────────────────────────────────────────
_CACHE = {
    'products_by_url': {},
    'products_source': None,
    'products_offset': 0,
    'products_mtime': 0,
    'categories': None,
    'categories_mtime': 0,
    'image_index': {},
    'image_index_key': -1,
    'walk_index': {},
    'walk_index_time': 0.0,
}
_cache_lock = threading.Lock()

def _load_jsonl_locked():
    """Incremental read: only bytes appended since the last call are parsed,
    so polling the dashboard during a live scrape stays cheap."""
    try:
        size = os.path.getsize(PRODUCTS_JSONL)
    except OSError:
        return list(_CACHE['products_by_url'].values())

    if _CACHE['products_source'] != PRODUCTS_JSONL or size < _CACHE['products_offset']:
        _CACHE.update(products_by_url={}, products_source=PRODUCTS_JSONL, products_offset=0)

    if size > _CACHE['products_offset']:
        with open(PRODUCTS_JSONL, 'rb') as f:
            f.seek(_CACHE['products_offset'])
            chunk = f.read()
        # Only consume complete lines; a torn tail is re-read next time
        end = chunk.rfind(b'\n') + 1
        for i, line in enumerate(chunk[:end].splitlines()):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = rec.get('url') or f"#{_CACHE['products_offset']}+{i}"
            _CACHE['products_by_url'][key] = rec
        _CACHE['products_offset'] += end

    return list(_CACHE['products_by_url'].values())

def _load_legacy_json_locked():
    if not os.path.exists(PRODUCTS_JSON):
        return []
    try:
        mtime = os.path.getmtime(PRODUCTS_JSON)
        if _CACHE['products_source'] != PRODUCTS_JSON or mtime > _CACHE['products_mtime']:
            with open(PRODUCTS_JSON, 'r', encoding='utf-8') as f:
                records = json.load(f)
            _CACHE.update(
                products_by_url={(rec.get('url') or f'#{i}'): rec for i, rec in enumerate(records)},
                products_source=PRODUCTS_JSON,
                products_mtime=mtime,
            )
        return list(_CACHE['products_by_url'].values())
    except Exception as e:
        logger.error(f"Error reading products.json: {e}")
        return []

def load_products_from_cache():
    with _cache_lock:
        if os.path.exists(PRODUCTS_JSONL):
            return _load_jsonl_locked()
        return _load_legacy_json_locked()

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
    if _scraper_pid():
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
    if req_data.get('force'):
        cmd.append('--force')

    cmd.extend(['--workers', str(workers)])

    try:
        proc = subprocess.Popen(cmd, cwd=BASE_DIR)
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PID_FILE, 'w') as f:
            f.write(str(proc.pid))
        # Reap the child when it exits so it doesn't linger as a zombie
        threading.Thread(target=proc.wait, daemon=True).start()
        return jsonify({'status': 'success', 'message': 'Scraping started', 'pid': proc.pid})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/scrape/stop', methods=['POST'])
def stop_scrape():
    pid = _scraper_pid()
    if not pid:
        return jsonify({'status': 'error', 'message': 'No scraper running'}), 400
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if not _pid_alive(pid):
                break
            time.sleep(0.1)
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.remove(PID_FILE)
    except OSError:
        pass
    return jsonify({'status': 'success', 'message': 'Scraper stopped'})

@app.route('/api/status', methods=['GET'])
def get_status():
    pid = _scraper_pid()
    return jsonify({'running': pid is not None, 'pid': pid})

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
        # Tail by seeking near the end instead of reading the whole file —
        # this endpoint is polled every 2s and the log grows large.
        with open(LOG_FILE, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            read_size = min(size, max(lines * 300, 16384))
            f.seek(size - read_size)
            tail = f.read().decode('utf-8', errors='replace')
        log_lines = tail.splitlines()
        if read_size < size and log_lines:
            log_lines = log_lines[1:]  # drop the partial first line
        return jsonify({'logs': "\n".join(log_lines[-lines:])})
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

    try:
        all_products = load_products_from_cache()
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
def _image_dir_for(safe_name):
    """Resolve a product's images directory without walking the tree per request."""
    products = load_products_from_cache()

    with _cache_lock:
        # Index from scraped records (they carry 'image_dir' since the JSONL rework)
        if _CACHE['image_index_key'] != len(products):
            index = {}
            for p in products:
                title = p.get('title') or ''
                img_dir = p.get('image_dir')
                if title and img_dir:
                    index[_safe_component(title)] = os.path.join(DATA_DIR, img_dir)
            _CACHE['image_index'] = index
            _CACHE['image_index_key'] = len(products)

        hit = _CACHE['image_index'].get(safe_name)
        if hit:
            return hit

        # Legacy records without image_dir: one filesystem walk, cached,
        # rebuilt at most every 30s while new products keep appearing
        now = time.time()
        if safe_name not in _CACHE['walk_index'] and now - _CACHE['walk_index_time'] > 30:
            structured_dir = os.path.join(DATA_DIR, 'structured')
            walk_index = {}
            if os.path.exists(structured_dir):
                for root, dirs, files in os.walk(structured_dir):
                    if 'images' in dirs:
                        walk_index[os.path.basename(root)] = os.path.join(root, 'images')
            _CACHE['walk_index'] = walk_index
            _CACHE['walk_index_time'] = now

        return _CACHE['walk_index'].get(safe_name)

@app.route('/api/image', methods=['GET'])
def get_image():
    title = request.args.get('title', '')
    filename = request.args.get('filename', '')
    if not title or not filename:
        return jsonify({'error': 'Missing title or filename'}), 400

    safe_name = _safe_component(title)

    # 1. Check old flat path
    old_path = os.path.join(DATA_DIR, 'images', safe_name, filename)
    if os.path.exists(old_path):
        return send_from_directory(os.path.dirname(old_path), filename, max_age=86400)

    # 2. Structured path via cached index
    img_dir = _image_dir_for(safe_name)
    if img_dir and os.path.exists(os.path.join(img_dir, filename)):
        return send_from_directory(img_dir, filename, max_age=86400)

    return jsonify({'error': 'Image not found'}), 404

# ── Serve Data Files ──────────────────────────────────────────
@app.route('/data/<path:filename>')
def serve_data(filename):
    return send_from_directory(DATA_DIR, filename)

# ── Export ────────────────────────────────────────────────────
_HTML_BREAK_RE = re.compile(r'<(br|/p|/li|div[^>]*)>', re.IGNORECASE)
_HTML_TAG_RE = re.compile(r'<[^>]+>')
_BLANK_LINE_RE = re.compile(r'\n\s*\n')
_CTRL_CHAR_RE = re.compile(r'[\000-\010\013-\014\016-\037]')

def clean_html(raw_html):
    if not raw_html: return ""
    text = _HTML_BREAK_RE.sub('\n', raw_html)
    text = _HTML_TAG_RE.sub('', text)
    text = html.unescape(text)
    return _BLANK_LINE_RE.sub('\n', text).strip()

@app.route('/api/export/json', methods=['GET'])
def export_json():
    data = load_products_from_cache()
    if not data:
        return jsonify({'error': 'No data to export'}), 404

    is_clean = request.args.get('clean', 'true').lower() in ('true', '1')

    try:
        if is_clean:
            # Copy records — mutating them in place would corrupt the cache
            data = [
                {**item,
                 'short_description': clean_html(item.get('short_description', '')),
                 'long_description': clean_html(item.get('long_description', ''))}
                for item in data
            ]
        return Response(
            json.dumps(data, indent=2, ensure_ascii=False),
            mimetype='application/json',
            headers={'Content-Disposition': 'attachment; filename=phoneplacekenya_products.json'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/export/csv', methods=['GET'])
def export_csv():
    products = load_products_from_cache()
    if not products:
        return jsonify({'error': 'No data to export'}), 404

    is_clean = request.args.get('clean', 'true').lower() in ('true', '1')

    try:
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
    products = load_products_from_cache()
    if not products:
        return jsonify({'error': 'No data to export'}), 404

    is_clean = request.args.get('clean', 'true').lower() in ('true', '1')

    try:
        import pandas as pd

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
                'Title': _CTRL_CHAR_RE.sub('', p.get('title', '')),
                'Brand': _CTRL_CHAR_RE.sub('', p.get('brand', '')),
                'Price': _CTRL_CHAR_RE.sub('', p.get('price', '')),
                'URL': _CTRL_CHAR_RE.sub('', p.get('url', '')),
                'Categories': _CTRL_CHAR_RE.sub('', ' > '.join(p.get('categories', []))),
                'Images': _CTRL_CHAR_RE.sub('', ' | '.join(p.get('images', []))),
                'Short Description': _CTRL_CHAR_RE.sub('', short_desc),
                'Long Description': _CTRL_CHAR_RE.sub('', long_desc)
            })

        df = pd.DataFrame(data)
        # Build in memory: writing a fixed path in DATA_DIR let concurrent
        # export requests clobber each other's files
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        return Response(
            buf.getvalue(),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers={'Content-Disposition': 'attachment; filename=phoneplacekenya_products.xlsx'}
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def _xml_escape(value):
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

@app.route('/api/export/xml', methods=['GET'])
def export_xml():
    data = load_products_from_cache()
    if not data:
        return jsonify({'error': 'No data to export'}), 404

    is_clean = request.args.get('clean', 'true').lower() in ('true', '1')

    # Streamed straight to the client: no temp file in DATA_DIR, no cache mutation
    def generate():
        yield '<?xml version="1.0" encoding="UTF-8"?>\n<products>\n'
        for item in data:
            if not item.get('title'): continue
            yield '  <product>\n'
            for k, v in item.items():
                if is_clean and k in ('short_description', 'long_description'):
                    v = clean_html(v)
                if isinstance(v, list):
                    v = ', '.join(_xml_escape(i) for i in v)
                else:
                    v = _xml_escape(v)
                yield f'    <{k}>{v}</{k}>\n'
            yield '  </product>\n'
        yield '</products>'

    return Response(
        generate(),
        mimetype='application/xml',
        headers={'Content-Disposition': 'attachment; filename=phoneplacekenya_products.xml'}
    )

@app.route('/api/export/images', methods=['GET'])
def export_images_all():
    # Legacy export, redirects to structured export
    return export_structured()

_STORED_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.gif')

def _zip_compression_for(filename):
    """Deflating already-compressed images burns CPU for ~0% size gain."""
    import zipfile
    if filename.lower().endswith(_STORED_EXTENSIONS):
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED

@app.route('/api/export/structured', methods=['GET'])
def export_structured():
    source_dir = os.path.join(DATA_DIR, 'structured')
    if not os.path.exists(source_dir):
        source_dir = os.path.join(DATA_DIR, 'images')
    if not os.path.exists(source_dir):
        return jsonify({'error': 'No data to export'}), 404
    try:
        import zipfile
        zip_path = os.path.join(DATA_DIR, 'structured_export.zip')
        with zipfile.ZipFile(zip_path, 'w') as zipf:
            for root, dirs, files in os.walk(source_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, source_dir)
                    zipf.write(file_path, arcname, compress_type=_zip_compression_for(file))
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
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zipf:
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
    products = load_products_from_cache()
    if not products:
        return jsonify({'error': 'No data to export'}), 404
    try:
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
