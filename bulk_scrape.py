import os
import sys
import json
import time
import argparse
import subprocess
import logging

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
MAIN_SCRIPT = os.path.join(BASE_DIR, 'main.py')
LOG_FILE = os.path.join(BASE_DIR, 'scraper.log')

def append_to_scraper_log(message):
    try:
        stamp = time.strftime('%Y-%m-%d %H:%M:%S,000')
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{stamp} - INFO - {message}\n")
    except Exception:
        pass

def update_bulk_progress(current, total, current_url, queue_urls):
    os.makedirs(DATA_DIR, exist_ok=True)
    progress_file = os.path.join(DATA_DIR, 'bulk_progress.json')
    try:
        with open(f"{progress_file}.tmp", 'w') as f:
            json.dump({'current': current, 'total': total, 'current_url': current_url, 'queue': queue_urls}, f)
        os.replace(f"{progress_file}.tmp", progress_file)
    except Exception as e:
        print(f"Failed to write bulk progress: {e}")

def run_bulk(queue_file, workers=8, new_version=False):
    if not os.path.exists(queue_file):
        print(f"Queue file not found: {queue_file}")
        return

    with open(queue_file, 'r', encoding='utf-8') as f:
        urls = [line.strip() for line in f if line.strip()]

    total = len(urls)
    append_to_scraper_log(f"Starting bulk scrape of {total} sites.")
    
    for i, url in enumerate(urls, 1):
        update_bulk_progress(i, total, url, urls)
        append_to_scraper_log(f"[Bulk Scrape] Starting site {i}/{total}: {url}")
        
        cmd = [sys.executable, MAIN_SCRIPT, '--target_url', url, '--workers', str(workers)]
        if new_version:
            cmd.append('--new-version')
            
        try:
            # We use Popen and wait so we can be interrupted (SIGTERM from app.py)
            proc = subprocess.Popen(cmd, cwd=BASE_DIR)
            proc.wait()
            if proc.returncode != 0:
                append_to_scraper_log(f"[Bulk Scrape] Site {url} exited with code {proc.returncode}")
        except KeyboardInterrupt:
            append_to_scraper_log("[Bulk Scrape] Interrupted by user.")
            proc.terminate()
            break
        except Exception as e:
            append_to_scraper_log(f"[Bulk Scrape] Error running scraper for {url}: {e}")

    append_to_scraper_log(f"Finished bulk scrape of {total} sites.")
    # Clear bulk progress when done
    try:
        progress_file = os.path.join(DATA_DIR, 'bulk_progress.json')
        if os.path.exists(progress_file):
            os.remove(progress_file)
    except:
        pass

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('queue_file', help="Path to text file containing URLs")
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--new-version', action='store_true')
    args = parser.parse_args()

    run_bulk(args.queue_file, args.workers, args.new_version)
