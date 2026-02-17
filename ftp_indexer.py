#!/usr/bin/env python3
"""
FTP Indexer - Standalone background service for DubList Automation
Scans the FTP server every 15 minutes and writes the index to disk as JSON.
The main Flask app reads this file instead of scanning FTP on every startup.

Usage:
    python3 ftp_indexer.py              # Run continuously (15-min loop)
    python3 ftp_indexer.py --once       # Run once and exit
    python3 ftp_indexer.py --interval 5 # Custom interval in minutes
"""

import os
import sys
import json
import time
import signal
import argparse
import logging
from datetime import datetime
from ftplib import FTP

# ============================================
# Configuration (matches DubListAutomation.py)
# ============================================
FTP_HOST = '192.168.0.198'
FTP_PORT = 21
FTP_USER = 'creative'
FTP_PASS = 'MMxhZtqv6!'
FTP_SEARCH_DIRS = ['/MDSDublist-Commercial_Content_Upload', '/Lilly_Archive']

DATA_FOLDER = '/mnt/ingest/Dubs/Data'
INDEX_FILE = os.path.join(DATA_FOLDER, 'ftp_index.json')
INDEX_LOCK_FILE = os.path.join(DATA_FOLDER, 'ftp_index.lock')

DEFAULT_INTERVAL_MINUTES = 15
MAX_DEPTH = 15
MAX_RETRIES = 3

# ============================================
# Logging
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
log = logging.getLogger('ftp_indexer')

# Graceful shutdown
shutdown_requested = False

def handle_signal(signum, frame):
    global shutdown_requested
    log.info(f"Received signal {signum}, shutting down after current cycle...")
    shutdown_requested = True

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


class FTPIndexer:
    """Scans an FTP server and builds a searchable file index."""

    def __init__(self):
        self.index = []
        self.directories = set()
        self.ftp = None
        self.stats = {
            'files_found': 0,
            'dirs_scanned': 0,
            'errors': 0,
            'reconnects': 0,
        }

    def connect(self):
        """Establish FTP connection."""
        self.ftp = FTP()
        self.ftp.connect(FTP_HOST, FTP_PORT, timeout=30)
        self.ftp.login(FTP_USER, FTP_PASS)
        log.info(f"Connected to FTP {FTP_HOST}:{FTP_PORT}")

    def ensure_connection(self):
        """Reconnect if the connection has dropped."""
        if self.ftp is None:
            self.connect()
            return

        try:
            self.ftp.pwd()
        except Exception:
            log.warning("Connection lost, reconnecting...")
            try:
                self.ftp.quit()
            except Exception:
                pass
            self.ftp = None
            self.connect()
            self.stats['reconnects'] += 1
            time.sleep(0.5)

    def disconnect(self):
        """Close the FTP connection."""
        if self.ftp:
            try:
                self.ftp.quit()
            except Exception:
                pass
            self.ftp = None

    def scan_directory(self, directory, depth=0, retry_count=0):
        """Recursively scan a directory on the FTP server."""
        if depth > MAX_DEPTH:
            return

        self.directories.add(directory)
        self.stats['dirs_scanned'] = len(self.directories)

        try:
            self.ensure_connection()
            self.ftp.cwd(directory)
            items = []

            # Try MLSD first (modern, gives structured data), fall back to DIR
            use_mlsd = True
            try:
                mlsd_items = list(self.ftp.mlsd())
                for name, facts in mlsd_items:
                    if name in ['.', '..']:
                        continue
                    entry_type = facts.get('type', '').lower()
                    is_dir = entry_type in ('dir', 'cdir', 'pdir') or entry_type == ''
                    # If type is missing/empty, try to cwd into it to detect directories
                    if entry_type == '':
                        try:
                            self.ftp.cwd(f"{directory}/{name}")
                            self.ftp.cwd(directory)
                            is_dir = True
                        except Exception:
                            is_dir = False
                    items.append({'name': name, 'is_dir': is_dir})
            except Exception:
                use_mlsd = False
                try:
                    raw_lines = []
                    self.ftp.dir(raw_lines.append)
                    for item in raw_lines:
                        parts = item.split()
                        if len(parts) < 9:
                            continue
                        item_name = ' '.join(parts[8:])
                        is_dir = item.startswith('d')
                        items.append({'name': item_name, 'is_dir': is_dir})
                except Exception as e:
                    if self._is_connection_error(e) and retry_count < MAX_RETRIES:
                        log.warning(f"Connection error in {directory}, retry {retry_count + 1}/{MAX_RETRIES}")
                        self.ftp = None
                        time.sleep(1)
                        return self.scan_directory(directory, depth, retry_count + 1)
                    if depth < 3:
                        log.error(f"Cannot list {directory}: {e}")
                    self.stats['errors'] += 1
                    return

            for entry in items:
                item_name = entry['name']
                is_dir = entry['is_dir']

                if is_dir:
                    if any(c in item_name for c in ['?', '*', '"', '<', '>', '|']):
                        continue
                    subdir = f"{directory}/{item_name}".replace('//', '/')
                    try:
                        self.scan_directory(subdir, depth + 1)
                    except Exception as e:
                        if self._is_connection_error(e):
                            log.warning("Connection error in subdirectory, will retry...")
                            self.ftp = None
                            time.sleep(0.5)
                        elif depth < 3:
                            log.error(f"Error scanning {subdir}: {e}")
                        self.stats['errors'] += 1
                else:
                    full_path = f"{directory}/{item_name}".replace('//', '/')
                    base_name = os.path.splitext(item_name)[0]
                    self.index.append({
                        'filename': item_name,
                        'basename': base_name,
                        'path': full_path,
                    })
                    self.stats['files_found'] = len(self.index)

        except Exception as e:
            if self._is_connection_error(e) and retry_count < MAX_RETRIES:
                log.warning(f"Connection error in {directory}, retry {retry_count + 1}/{MAX_RETRIES}")
                self.ftp = None
                time.sleep(1)
                return self.scan_directory(directory, depth, retry_count + 1)
            if depth < 3:
                log.error(f"Error in {directory}: {e}")
            self.stats['errors'] += 1

    @staticmethod
    def _is_connection_error(e):
        msg = str(e)
        return 'Broken pipe' in msg or 'Connection reset' in msg or 'EOF' in msg

    def build_index(self):
        """Full index build: connect, scan all dirs, disconnect."""
        self.index = []
        self.directories = set()
        self.stats = {'files_found': 0, 'dirs_scanned': 0, 'errors': 0, 'reconnects': 0}

        start_time = time.time()
        log.info("=" * 60)
        log.info("Starting FTP index build...")
        log.info("=" * 60)

        try:
            self.connect()

            for search_dir in FTP_SEARCH_DIRS:
                log.info(f"Scanning: {search_dir}")
                try:
                    self.ensure_connection()
                    self.ftp.cwd(search_dir)
                    self.scan_directory(search_dir)
                except Exception as e:
                    log.error(f"Error with {search_dir}: {e}")
                    self.ftp = None  # Force reconnect for next directory

        except Exception as e:
            log.error(f"Critical error during index build: {e}")
        finally:
            self.disconnect()

        elapsed = time.time() - start_time
        log.info("=" * 60)
        log.info(f"INDEX COMPLETE: {len(self.index)} files from {len(self.directories)} dirs in {elapsed:.1f}s")
        log.info(f"Stats: {self.stats}")
        log.info("=" * 60)

        return self.index, sorted(list(self.directories))

    def save_to_disk(self):
        """Write the index to disk as JSON with an atomic write."""
        os.makedirs(DATA_FOLDER, exist_ok=True)

        payload = {
            'timestamp': datetime.now().isoformat(),
            'file_count': len(self.index),
            'directory_count': len(self.directories),
            'directories': sorted(list(self.directories)),
            'stats': self.stats,
            'index': self.index,
        }

        # Atomic write: write to temp file then rename
        tmp_file = INDEX_FILE + '.tmp'
        try:
            with open(tmp_file, 'w', encoding='utf-8') as f:
                json.dump(payload, f, separators=(',', ':'))

            os.replace(tmp_file, INDEX_FILE)
            log.info(f"Index saved to {INDEX_FILE} ({len(self.index)} files, {os.path.getsize(INDEX_FILE) / 1024:.0f} KB)")
            return True
        except Exception as e:
            log.error(f"Failed to save index: {e}")
            # Clean up temp file
            try:
                os.unlink(tmp_file)
            except OSError:
                pass
            return False


def run_once():
    """Build the index once and save to disk."""
    indexer = FTPIndexer()
    indexer.build_index()
    return indexer.save_to_disk()


def run_loop(interval_minutes):
    """Run the indexer in a continuous loop."""
    log.info(f"FTP Indexer starting - will refresh every {interval_minutes} minutes")
    log.info(f"Index file: {INDEX_FILE}")
    log.info(f"FTP server: {FTP_HOST}:{FTP_PORT}")
    log.info(f"Search dirs: {FTP_SEARCH_DIRS}")

    while not shutdown_requested:
        run_once()

        # Sleep in small increments so we can respond to shutdown signals
        sleep_seconds = interval_minutes * 60
        log.info(f"Next index build in {interval_minutes} minutes...")
        for _ in range(sleep_seconds):
            if shutdown_requested:
                break
            time.sleep(1)

    log.info("FTP Indexer stopped.")


def main():
    parser = argparse.ArgumentParser(description='FTP Indexer for DubList Automation')
    parser.add_argument('--once', action='store_true',
                        help='Build index once and exit')
    parser.add_argument('--interval', type=int, default=DEFAULT_INTERVAL_MINUTES,
                        help=f'Refresh interval in minutes (default: {DEFAULT_INTERVAL_MINUTES})')
    args = parser.parse_args()

    os.makedirs(DATA_FOLDER, exist_ok=True)

    if args.once:
        success = run_once()
        sys.exit(0 if success else 1)
    else:
        run_loop(args.interval)


if __name__ == '__main__':
    main()
